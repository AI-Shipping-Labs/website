"""Tests for the StudioNoStoreMiddleware (issues #347 and #1517).

The middleware sets ``Cache-Control: private, no-store`` and
``Vary: Cookie`` on every response under ``/studio/*``, ``/accounts/*``,
and ``/account/*`` so authenticated HTML can never be cached by a
browser back-forward cache, service worker, or future intermediary
CDN. Public pages must NOT inherit this header.

It also exercises the messages-drain behaviour (acceptance criterion
"login flash is drained on next page") and the no-double-render
guarantee on Studio sub-pages.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import MemberAPIKey
from accounts.services.email_change import request_email_change
from accounts.utils.tokens import generate_password_reset_token

User = get_user_model()


class PrivateNoStoreAssertions:
    def assert_private_no_store(self, response, *, vary=True):
        cache_control = response.get("Cache-Control", "").lower()
        self.assertIn(
            "no-store",
            cache_control,
            f"missing no-store in Cache-Control: {cache_control!r}",
        )
        self.assertIn(
            "private",
            cache_control,
            f"missing private in Cache-Control: {cache_control!r}",
        )
        if vary:
            self.assertIn(
                "cookie",
                response.get("Vary", "").lower(),
                f"missing Cookie in Vary: {response.get('Vary', '')!r}",
            )

    def assert_token_response_is_private(self, response, *, vary):
        self.assert_private_no_store(response, vary=vary)
        self.assertEqual(response.get("Pragma"), "no-cache")
        self.assertEqual(response.get("Referrer-Policy"), "no-referrer")


class StudioCacheHeadersTest(TestCase):
    """Authenticated Studio responses must be uncacheable."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def _assert_no_store(self, response):
        cache_control = response.get('Cache-Control', '').lower()
        self.assertIn('no-store', cache_control,
                      f"missing no-store in Cache-Control: {cache_control!r}")
        self.assertIn('private', cache_control,
                      f"missing private in Cache-Control: {cache_control!r}")
        vary = response.get('Vary', '').lower()
        # patch_vary_headers adds "Cookie" (case-insensitive) — the value
        # itself can include other directives appended by other middleware.
        self.assertIn('cookie', vary,
                      f"missing Cookie in Vary: {vary!r}")

    def test_studio_dashboard_has_no_store(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self._assert_no_store(response)

    def test_studio_settings_has_no_store(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        self._assert_no_store(response)

    def test_studio_worker_has_no_store(self):
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 200)
        self._assert_no_store(response)

    def test_studio_redirect_for_anonymous_still_has_no_store(self):
        # Anonymous studio request -> 302 to login. The middleware should
        # still mark the redirect as no-store; otherwise an intermediary
        # could cache a redirect to login keyed by URL alone.
        anon = Client()
        response = anon.get('/studio/')
        self.assertEqual(response.status_code, 302)
        self._assert_no_store(response)


class AccountsCacheHeadersTest(PrivateNoStoreAssertions, TestCase):
    """``/accounts/*`` (allauth) responses must also be uncacheable."""

    def test_login_page_has_no_store(self):
        response = self.client.get('/accounts/login/')
        # 200 (login form) — and even on a 302 redirect this should hold.
        self.assertIn(response.status_code, (200, 302))
        cache_control = response.get('Cache-Control', '').lower()
        self.assertIn('no-store', cache_control)
        self.assertIn('private', cache_control)
        self.assertIn('cookie', response.get('Vary', '').lower())

    def test_logout_page_has_no_store(self):
        response = self.client.get('/accounts/logout/')
        self.assertIn(response.status_code, (200, 302, 405))
        cache_control = response.get('Cache-Control', '').lower()
        self.assertIn('no-store', cache_control)
        self.assertIn('private', cache_control)

    def test_password_reset_request_page_has_no_store(self):
        response = self.client.get("/accounts/password-reset-request")
        self.assertTemplateUsed(response, "accounts/password_reset_request.html")
        self.assert_token_response_is_private(response, vary=True)


@override_settings(SES_ENABLED=False)
class MemberAccountCacheHeadersTest(PrivateNoStoreAssertions, TestCase):
    """Every sensitive member-account response is private and uncacheable."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="cache-member@test.com",
            password="OldPass123!",
            account_activated=True,
            email_verified=True,
        )
        self.client.force_login(self.user)

    def test_account_page_has_no_store(self):
        response = self.client.get("/account/")

        self.assertContains(response, self.user.email)
        self.assert_private_no_store(response)

    def test_anonymous_account_redirect_has_no_store(self):
        response = Client().get("/account/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assert_private_no_store(response)

    def test_account_404_has_no_store(self):
        response = self.client.get("/account/not-a-real-route")

        self.assertEqual(response.status_code, 404)
        self.assert_private_no_store(response)

    def test_profile_redirect_has_no_store(self):
        response = self.client.post(
            "/account/profile",
            {"first_name": "Cache", "last_name": "Member"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/account/#profile")
        self.assert_private_no_store(response)

    def test_data_export_attachment_has_no_store(self):
        response = self.client.get("/account/api/data-export")

        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response.json()["manifest"]["primary_email"], self.user.email)
        self.assert_private_no_store(response)

    @patch.object(
        MemberAPIKey,
        "generate_plaintext_key",
        return_value="asl_member_cache_header_plaintext_key",
    )
    def test_created_api_key_plaintext_response_has_no_store(self, _generate):
        response = self.client.post(
            "/account/api/member-api-keys",
            {"name": "local agent"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertContains(
            response,
            "asl_member_cache_header_plaintext_key",
            status_code=201,
        )
        self.assert_private_no_store(response)

    def test_api_key_revoke_redirect_has_no_store(self):
        member_key, _plaintext = MemberAPIKey.create_for_user(
            user=self.user,
            name="revoke me",
        )

        response = self.client.post(
            f"/account/api/member-api-keys/{member_key.pk}/revoke",
        )

        self.assertEqual(response.status_code, 302)
        self.assert_private_no_store(response)

    def test_api_key_delete_redirect_has_no_store(self):
        member_key, _plaintext = MemberAPIKey.create_for_user(
            user=self.user,
            name="delete me",
        )
        member_key.revoke()

        response = self.client.post(
            f"/account/api/member-api-keys/{member_key.pk}/delete",
        )

        self.assertEqual(response.status_code, 302)
        self.assert_private_no_store(response)

    @patch("accounts.views.account.request_account_deletion")
    def test_deletion_request_redirect_and_error_have_no_store(self, request_deletion):
        request_deletion.return_value = SimpleNamespace(success=True)
        redirect_response = self.client.post("/account/api/request-deletion")

        self.assertEqual(redirect_response.status_code, 302)
        self.assert_private_no_store(redirect_response)

        request_deletion.return_value = SimpleNamespace(success=False)
        error_response = self.client.post("/account/api/request-deletion")

        self.assertEqual(error_response.status_code, 503)
        self.assertContains(
            error_response,
            "We could not deliver your deletion request.",
            status_code=503,
        )
        self.assert_private_no_store(error_response)

    def test_email_change_valid_and_invalid_results_have_token_headers(self):
        _request, token = request_email_change(
            self.user,
            "cache-member-new@test.com",
            current_password="OldPass123!",
            send=False,
        )

        valid_response = Client().get(
            "/account/change-email/confirm",
            {"token": token},
        )
        invalid_response = Client().get(
            "/account/change-email/confirm",
            {"token": "not-a-token"},
        )

        self.assertContains(valid_response, "Email changed")
        self.assertEqual(invalid_response.status_code, 400)
        self.assert_token_response_is_private(valid_response, vary=True)
        self.assert_token_response_is_private(invalid_response, vary=True)

    def test_account_json_preferences_have_no_store(self):
        cases = [
            (
                "/account/api/email-preferences",
                {"newsletter": True},
            ),
            (
                "/account/api/timezone-preference",
                {"timezone": "Europe/Berlin"},
            ),
            (
                "/account/api/dismiss-card",
                {"card": "slack_join"},
            ),
        ]

        for path, payload in cases:
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    data=json.dumps(payload),
                    content_type="application/json",
                )

                self.assertEqual(response.json()["status"], "ok")
                self.assert_private_no_store(response)

    def test_change_password_json_has_no_store(self):
        response = self.client.post(
            "/account/api/change-password",
            data=json.dumps(
                {
                    "current_password": "OldPass123!",
                    "new_password": "NewPass456!",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.json()["status"], "ok")
        self.assert_private_no_store(response)

    def test_resend_verification_redirect_has_no_store(self):
        response = self.client.post("/account/api/resend-verification")

        self.assertEqual(response.status_code, 302)
        self.assert_private_no_store(response)


class PasswordResetCacheHeadersTest(PrivateNoStoreAssertions, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="cache-reset@test.com",
            password="OldPass123!",
        )

    def test_valid_and_invalid_get_responses_have_token_headers(self):
        token = generate_password_reset_token(self.user)

        valid_response = self.client.get(
            "/api/password-reset",
            {"token": token},
        )
        invalid_response = self.client.get(
            "/api/password-reset",
            {"token": "not-a-token"},
        )

        self.assertContains(valid_response, 'id="reset-form"')
        self.assertContains(invalid_response, "Invalid password reset link.")
        self.assert_token_response_is_private(valid_response, vary=False)
        self.assert_token_response_is_private(invalid_response, vary=False)

    def test_success_and_validation_error_posts_have_token_headers(self):
        validation_error = self.client.post(
            "/api/password-reset",
            data="not json",
            content_type="application/json",
        )
        token = generate_password_reset_token(self.user)
        success = self.client.post(
            "/api/password-reset",
            data=json.dumps(
                {
                    "token": token,
                    "new_password": "NewPass456!",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(validation_error.status_code, 400)
        self.assertEqual(success.json()["status"], "ok")
        self.assert_token_response_is_private(validation_error, vary=False)
        self.assert_token_response_is_private(success, vary=False)


class PublicPagesNotAffectedTest(TestCase):
    """Public pages must NOT get ``private, no-store`` from the middleware.

    Other middleware or framework defaults may set their own headers,
    but the StudioNoStoreMiddleware specifically MUST NOT touch URLs
    outside ``/studio/`` and ``/accounts/``.
    """

    def test_homepage_does_not_have_private_no_store_from_middleware(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        cache_control = response.get('Cache-Control', '').lower()
        # Homepage may or may not have a Cache-Control (Django's default
        # is to leave it unset). What we assert is that it is not the
        # exact "private, no-store" combo that our middleware imposes —
        # because if it were, public-page caching would be broken.
        self.assertNotEqual(cache_control.strip(), 'private, no-store')

    def test_blog_does_not_have_private_no_store_from_middleware(self):
        response = self.client.get("/blog")
        self.assertTemplateUsed(response, "content/blog_list.html")
        cache_control = response.get("Cache-Control", "").lower()
        self.assertNotEqual(cache_control.strip(), "private, no-store")


class StudioMessagesRenderedOnceTest(TestCase):
    """Studio responses must render messages exactly once per page.

    Regression guard against double-rendering: before issue #347 every
    leaf Studio template iterated ``messages`` itself in addition to
    (or instead of) the base template. Now ``studio/base.html`` is
    the single render site.

    We seed a real flash message by POSTing to an existing Studio
    action endpoint (``studio_worker_drain_queue``) which always emits
    a ``messages.info`` flash and 302-redirects to ``/studio/worker/``.
    Following the redirect with the test client picks up the message
    naturally — no fragile session/cookie hacks.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def _seed_flash_and_get(self, target_url):
        """POST to a real Studio endpoint that emits a flash, then GET
        the target page (which should drain the message). Returns the
        target response and the flash text we expect to find on it.

        We use ``studio_worker_drain_queue`` because it always emits
        ``messages.info('Queue is already empty.')`` when there are no
        queued tasks (which is the test default).
        """
        post = self.client.post('/studio/worker/queue/drain/')
        # Endpoint redirects to /studio/worker/ on success.
        self.assertEqual(post.status_code, 302, post.content[:200])
        flash = 'Queue is already empty.'
        # Now GET the target URL — message is still queued and will
        # render on this page.
        response = self.client.get(target_url)
        return response, flash

    def test_message_rendered_once_on_studio_page(self):
        response, flash = self._seed_flash_and_get('/studio/worker/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        # Exactly one occurrence of the literal — guards against the
        # "rendered in studio/base.html and again in worker.html" bug.
        self.assertEqual(body.count(flash), 1,
                         f"message rendered {body.count(flash)} times, expected 1")

    def test_message_rendered_once_on_settings_page(self):
        response, flash = self._seed_flash_and_get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertEqual(body.count(flash), 1)

    def test_message_drained_after_first_render(self):
        first, flash = self._seed_flash_and_get('/studio/worker/')
        self.assertEqual(first.status_code, 200)
        self.assertIn(flash, first.content.decode('utf-8'))
        # The very next request must not contain the message anymore —
        # the previous render drained the messages queue.
        second = self.client.get('/studio/worker/')
        self.assertEqual(second.status_code, 200)
        self.assertNotIn(flash, second.content.decode('utf-8'))


class HomepageDrainsMessagesTest(TestCase):
    """Public homepage must drain the messages queue too.

    Allauth redirects to ``LOGIN_REDIRECT_URL='/'`` after login. If the
    homepage doesn't render messages, allauth's "Successfully signed
    in as ..." flash sits in the cookie store and later leaks into a
    Studio sub-page (issue #347 root cause). This test simulates the
    "flash queued, next page drains it" sequence by depositing a
    message via a real Studio action endpoint (which redirects back
    out of /studio/), then visiting the homepage.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_message_renders_once_on_homepage_then_drains(self):
        # Seed a flash (drain-queue emits 'Queue is already empty.').
        post = self.client.post('/studio/worker/queue/drain/')
        self.assertEqual(post.status_code, 302)
        flash = 'Queue is already empty.'

        # First homepage render must show the flash exactly once.
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertEqual(body.count(flash), 1,
                         "homepage must render the flash exactly once")

        # And on the next render it must be gone — drained.
        second = self.client.get('/')
        self.assertNotIn(flash, second.content.decode('utf-8'))
