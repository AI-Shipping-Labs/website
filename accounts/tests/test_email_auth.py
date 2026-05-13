"""Tests for email+password authentication (issue #94).

Tests cover:
- POST /api/register: email+password user creation
- POST /api/login: email+password login
- GET /api/verify-email?token={jwt}: email verification
- POST /api/password-reset-request: password reset email request
- GET/POST /api/password-reset: password reset flow
- POST /account/api/change-password: change password
- Registration page template
- Email verification banner on account page
"""

import datetime
import json
import time
from unittest.mock import patch

import jwt
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

JWT_ALGORITHM = "HS256"


def _make_verification_token(user_id, expired=False):
    """Create a verification JWT token for testing."""
    if expired:
        exp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    else:
        exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    payload = {
        "user_id": user_id,
        "action": "verify_email",
        "exp": exp,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _make_password_reset_token(user_id, expired=False):
    """Create a password reset JWT token for testing."""
    if expired:
        exp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    else:
        exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    payload = {
        "user_id": user_id,
        "action": "password_reset",
        "exp": exp,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


# ── Registration API ──────────────────────────────────────────────────


@tag('core')
class RegisterAPITest(TestCase):
    """Tests for POST /api/register."""

    url = "/api/register"

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_register_creates_user(self):
        """Valid registration creates a new user."""
        resp = self._post({"email": "new@example.com", "password": "secure1234"})
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(User.objects.filter(email="new@example.com").exists())

    @patch("accounts.views.auth._send_verification_email")
    def test_register_sends_verification_email(self, mock_send):
        """Registration triggers a verification email to the new user."""
        resp = self._post(
            {"email": "verify-send@example.com", "password": "secure1234"}
        )
        self.assertEqual(resp.status_code, 201)
        user = User.objects.get(email="verify-send@example.com")
        mock_send.assert_called_once_with(user)

    def test_register_user_has_password(self):
        """Registered user has a usable password."""
        self._post({"email": "pwd@example.com", "password": "secure1234"})
        user = User.objects.get(email="pwd@example.com")
        self.assertTrue(user.check_password("secure1234"))

    def test_register_email_verified_false(self):
        """New user has email_verified=False."""
        self._post({"email": "unverified@example.com", "password": "secure1234"})
        user = User.objects.get(email="unverified@example.com")
        self.assertFalse(user.email_verified)

    def test_register_tier_is_free(self):
        """New user gets free tier."""
        self._post({"email": "free@example.com", "password": "secure1234"})
        user = User.objects.get(email="free@example.com")
        self.assertIsNotNone(user.tier)
        self.assertEqual(user.tier.slug, "free")

    def test_register_duplicate_email_returns_400(self):
        """Cannot register with an email that already exists."""
        User.objects.create_user(email="dupe@example.com", password="existing1234")
        resp = self._post({"email": "dupe@example.com", "password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already exists", resp.json()["error"])

    def test_register_missing_email_returns_400(self):
        resp = self._post({"password": "secure1234"})
        self.assertEqual(resp.status_code, 400)

    def test_register_missing_password_returns_400(self):
        resp = self._post({"email": "no-pwd@example.com"})
        self.assertEqual(resp.status_code, 400)

    def test_register_short_password_returns_400(self):
        """Password must be at least 8 characters."""
        resp = self._post({"email": "short@example.com", "password": "short"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("8 characters", resp.json()["error"])

    def test_register_empty_email_returns_400(self):
        resp = self._post({"email": "", "password": "secure1234"})
        self.assertEqual(resp.status_code, 400)

    def test_register_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_register_get_not_allowed(self):
        """GET method is not allowed on register endpoint."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_register_normalizes_email(self):
        """Email is normalized (lowercase domain)."""
        self._post({"email": "User@EXAMPLE.COM", "password": "secure1234"})
        self.assertTrue(User.objects.filter(email="user@example.com").exists())

    def test_register_url_name(self):
        url = reverse("api_register")
        self.assertEqual(url, "/api/register")

    def test_register_returns_success_message(self):
        resp = self._post({"email": "msg@example.com", "password": "secure1234"})
        data = resp.json()
        self.assertIn("message", data)
        self.assertIn("verify", data["message"].lower())
        self.assertEqual(data["return_url"], "")

    def test_register_returns_safe_return_url(self):
        resp = self._post({
            "email": "next@example.com",
            "password": "secure1234",
            "next": "/courses/free-course/m/l",
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["return_url"], "/courses/free-course/m/l")

    def test_register_ignores_unsafe_return_url(self):
        resp = self._post({
            "email": "unsafe-next@example.com",
            "password": "secure1234",
            "next": "https://evil.example",
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["return_url"], "")


# ── Email Verification API ────────────────────────────────────────────


@tag('core')
class VerifyEmailAPITest(TestCase):
    """Tests for GET /api/verify-email?token={jwt}."""

    url = "/api/verify-email"

    def assert_html_result(self, resp, *, heading, message_text=None):
        self.assertTemplateUsed(resp, "email_app/verify_result.html")
        self.assertIn("text/html", resp.headers["Content-Type"])
        self.assertContains(resp, heading, status_code=resp.status_code)
        if message_text:
            self.assertContains(resp, message_text, status_code=resp.status_code)
        self.assertNotContains(
            resp,
            '{"status"',
            status_code=resp.status_code,
            html=False,
        )
        self.assertNotContains(
            resp,
            '{"error"',
            status_code=resp.status_code,
            html=False,
        )

    def test_verify_sets_email_verified_true(self):
        """Valid token sets email_verified to True."""
        user = User.objects.create_user(
            email="verify@example.com",
            password="test1234",
            verification_expires_at=timezone.now() + datetime.timedelta(days=1),
        )
        self.assertFalse(user.email_verified)

        token = _make_verification_token(user.pk)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assert_html_result(resp, heading="Email Verified")
        self.assertContains(resp, 'href="/accounts/login/"')
        self.assertContains(resp, "Sign In")

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertIsNone(user.verification_expires_at)

    def test_verify_already_verified_user_succeeds(self):
        """Verifying an already-verified user still returns success."""
        user = User.objects.create_user(
            email="already@example.com",
            password="test1234",
            first_name="Already",
        )
        user.email_verified = True
        user.save(update_fields=["email_verified"])

        token = _make_verification_token(user.pk)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assert_html_result(resp, heading="Email Verified")

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertEqual(user.first_name, "Already")

    def test_verify_authenticated_success_links_to_account(self):
        """Authenticated users get an account-oriented next action."""
        user = User.objects.create_user(
            email="signed-in@example.com",
            password="test1234",
        )
        self.client.force_login(user)

        token = _make_verification_token(user.pk)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assert_html_result(resp, heading="Email Verified")
        self.assertContains(resp, 'href="/account/"')
        self.assertContains(resp, "Continue to Account")

    def test_verify_expired_token_returns_400(self):
        """Expired token returns 400."""
        user = User.objects.create_user(email="expired@example.com", password="test1234")
        token = _make_verification_token(user.pk, expired=True)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 400)
        self.assert_html_result(
            resp,
            heading="Verification Failed",
            message_text="expired",
        )

    def test_verify_invalid_token_returns_400(self):
        """Garbage token returns 400."""
        resp = self.client.get(f"{self.url}?token=invalid_garbage")
        self.assertEqual(resp.status_code, 400)
        self.assert_html_result(
            resp,
            heading="Verification Failed",
            message_text="invalid",
        )

    def test_verify_missing_token_returns_400(self):
        """No token parameter returns 400."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)
        self.assert_html_result(
            resp,
            heading="Verification Failed",
            message_text="incomplete",
        )

    def test_verify_wrong_action_returns_400(self):
        """Token with wrong action type returns 400."""
        user = User.objects.create_user(email="wrong-action@example.com", password="test1234")
        payload = {
            "user_id": user.pk,
            "action": "password_reset",  # wrong action
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=24),
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 400)
        self.assert_html_result(
            resp,
            heading="Verification Failed",
            message_text="invalid",
        )

    def test_verify_nonexistent_user_returns_404(self):
        """Token for non-existent user returns 404."""
        token = _make_verification_token(99999)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 404)
        self.assert_html_result(
            resp,
            heading="Verification Failed",
            message_text="could not find an account",
        )

    def test_verify_url_name(self):
        url = reverse("api_verify_email")
        self.assertEqual(url, "/api/verify-email")


# ── Login API ─────────────────────────────────────────────────────────


@tag('core')
class LoginAPITest(TestCase):
    """Tests for POST /api/login."""

    url = "/api/login"

    def setUp(self):
        self.user = User.objects.create_user(
            email="login@example.com", password="correct1234"
        )

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_login_with_valid_credentials(self):
        """Valid email+password returns 200 and logs user in."""
        resp = self._post({"email": "login@example.com", "password": "correct1234"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        self.assertEqual(resp.json()["redirect_url"], "/")

    def test_login_returns_safe_next_redirect_url(self):
        resp = self._post({
            "email": "login@example.com",
            "password": "correct1234",
            "next": "/events/demo?register=1",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["redirect_url"], "/events/demo?register=1")

    def test_login_ignores_unsafe_next_redirect_url(self):
        unsafe_values = [
            "https://example.com/phish",
            "//example.com/phish",
            "javascript:alert(1)",
            "/\\example.com",
        ]
        for value in unsafe_values:
            with self.subTest(value=value):
                self.client.logout()
                resp = self._post({
                    "email": "login@example.com",
                    "password": "correct1234",
                    "next": value,
                })
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["redirect_url"], "/")

    def test_login_authenticates_session(self):
        """After login, user is authenticated in the session."""
        login_resp = self._post(
            {"email": "login@example.com", "password": "correct1234"}
        )
        self.assertEqual(login_resp.status_code, 200)
        resp = self.client.get("/account/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertTemplateUsed(resp, "accounts/account.html")

    def test_login_wrong_password_returns_401(self):
        """Wrong password returns 401."""
        resp = self._post({"email": "login@example.com", "password": "wrongpass"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "Invalid email or password")

    def test_login_nonexistent_user_returns_401(self):
        """Non-existent email returns 401."""
        resp = self._post({"email": "nobody@example.com", "password": "whatever"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "Invalid email or password")

    def test_login_missing_email_returns_400(self):
        resp = self._post({"password": "something"})
        self.assertEqual(resp.status_code, 400)

    def test_login_missing_password_returns_400(self):
        resp = self._post({"email": "login@example.com"})
        self.assertEqual(resp.status_code, 400)

    def test_login_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_login_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_login_case_insensitive_email(self):
        """Login works with different email casing."""
        resp = self._post(
            {"email": "LOGIN@EXAMPLE.COM", "password": "correct1234"}
        )
        # Django's ModelBackend is case-sensitive by default; our authenticate
        # lowercases the email, so this should work if the stored email matches.
        # The stored email is "login@example.com" (normalized domain).
        # We send "login@example.com" after lowercasing.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_login_url_name(self):
        url = reverse("api_login")
        self.assertEqual(url, "/api/login")

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_login_query_guard_valid_wrong_password_and_unknown_email(self):
        """Guard avoidable DB work while keeping password verification intact.

        The timing investigation for issue #371 showed the safe reduction was
        avoiding allauth fallback work for this JSON endpoint; password hashing
        remains the dominant expected cost in production.
        """
        self.user.set_password("correct1234")
        self.user.save(update_fields=["password"])
        scenarios = [
            ({"email": "login@example.com", "password": "correct1234"}, 200, 9),
            ({"email": "login@example.com", "password": "wrongpass"}, 401, 1),
            ({"email": "nobody@example.com", "password": "whatever"}, 401, 1),
        ]

        for payload, expected_status, max_queries in scenarios:
            with self.subTest(email=payload["email"], status=expected_status):
                with CaptureQueriesContext(connection) as captured:
                    resp = self._post(payload)
                self.assertEqual(resp.status_code, expected_status)
                self.assertLessEqual(
                    len(captured),
                    max_queries,
                    [query["sql"] for query in captured.captured_queries],
                )

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_login_timing_helper_covers_valid_wrong_password_and_unknown_email(self):
        """Bound local overhead without making CI depend on production hash cost.

        The threshold below intentionally has slack: it must catch a real regression
        (e.g. an O(N) auth fallback or extra DB round-trips) while tolerating CPU
        contention on shared CI runners where ``--parallel 4`` pushes per-request
        wall time above a tighter bound. See issue #626 for the previous flake.
        """
        self.user.set_password("correct1234")
        self.user.save(update_fields=["password"])
        scenarios = [
            ({"email": "login@example.com", "password": "correct1234"}, 200),
            ({"email": "login@example.com", "password": "wrongpass"}, 401),
            ({"email": "nobody@example.com", "password": "whatever"}, 401),
        ]

        for payload, expected_status in scenarios:
            with self.subTest(email=payload["email"], status=expected_status):
                started_at = time.perf_counter()
                resp = self._post(payload)
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                self.assertEqual(resp.status_code, expected_status)
                self.assertLess(elapsed_ms, 750)

    def test_login_uses_model_backend_without_allauth_fallback(self):
        """Email/password API avoids duplicate allauth backend verification."""
        with patch(
            "allauth.account.auth_backends.AuthenticationBackend.authenticate",
            side_effect=AssertionError("allauth fallback should not run"),
        ):
            resp = self._post({"email": "login@example.com", "password": "wrongpass"})
        self.assertEqual(resp.status_code, 401)

    @override_settings(LOGIN_API_SLOW_MS=0)
    def test_login_slow_diagnostic_logs_outcome_without_credentials(self):
        with self.assertLogs("accounts.views.auth", level="WARNING") as captured:
            resp = self._post({"email": "login@example.com", "password": "wrongpass"})

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(captured.records[0].login_outcome, "invalid_credentials")
        self.assertGreaterEqual(captured.records[0].elapsed_ms, 0)
        log_message = captured.records[0].getMessage()
        self.assertNotIn("login@example.com", log_message)
        self.assertNotIn("wrongpass", log_message)


# ── Password Reset Request API ────────────────────────────────────────


@tag('core')
class PasswordResetRequestAPITest(TestCase):
    """Tests for POST /api/password-reset-request."""

    url = "/api/password-reset-request"

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_reset_request_for_existing_user_returns_200(self):
        """Request with existing email returns 200."""
        User.objects.create_user(email="reset@example.com", password="test1234")
        resp = self._post({"email": "reset@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    @patch("accounts.views.auth._send_password_reset_email")
    def test_reset_request_sends_email_to_existing_user(self, mock_send):
        """Password reset request sends a reset email to existing user."""
        user = User.objects.create_user(
            email="reset-email@example.com", password="test1234"
        )
        resp = self._post({"email": "reset-email@example.com"})
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once_with(user)

    @patch("accounts.views.auth._send_password_reset_email")
    def test_reset_request_does_not_send_email_for_nonexistent_user(self, mock_send):
        """Password reset request does NOT send email for non-existent user."""
        self._post({"email": "nobody@example.com"})
        mock_send.assert_not_called()

    def test_reset_request_for_nonexistent_email_returns_200(self):
        """Request with non-existent email still returns 200 (no reveal)."""
        resp = self._post({"email": "nobody@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_reset_request_missing_email_returns_400(self):
        resp = self._post({})
        self.assertEqual(resp.status_code, 400)

    def test_reset_request_empty_email_returns_400(self):
        resp = self._post({"email": ""})
        self.assertEqual(resp.status_code, 400)

    def test_reset_request_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_reset_request_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_reset_request_url_name(self):
        url = reverse("api_password_reset_request")
        self.assertEqual(url, "/api/password-reset-request")


# ── Password Reset API ───────────────────────────────────────────────


@tag('core')
class PasswordResetAPITest(TestCase):
    """Tests for GET/POST /api/password-reset."""

    url = "/api/password-reset"

    def setUp(self):
        self.user = User.objects.create_user(
            email="resetpw@example.com", password="oldpass1234"
        )

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_get_renders_form_with_valid_token(self):
        """GET with valid token renders password reset template."""
        token = _make_password_reset_token(self.user.pk)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "accounts/password_reset.html")
        self.assertEqual(resp.context["token"], token)
        self.assertEqual(resp.context["reset_email"], self.user.email)

    def test_get_form_has_password_manager_hints(self):
        token = _make_password_reset_token(self.user.pk)
        resp = self.client.get(f"{self.url}?token={token}")

        self.assertContains(resp, 'method="post"')
        self.assertContains(resp, 'action="/api/password-reset"')
        self.assertContains(resp, 'id="reset-username"')
        self.assertContains(resp, 'autocomplete="username"')
        self.assertContains(resp, 'value="resetpw@example.com"')
        self.assertContains(resp, 'id="new-password"')
        self.assertContains(resp, 'name="new_password"')
        self.assertContains(resp, 'autocomplete="new-password"', count=2)

    def test_get_shows_error_for_expired_token(self):
        """GET with expired token shows error message."""
        token = _make_password_reset_token(self.user.pk, expired=True)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("error", resp.context)
        self.assertIn("expired", resp.context["error"].lower())

    def test_get_shows_error_for_invalid_token(self):
        """GET with invalid token shows error message."""
        resp = self.client.get(f"{self.url}?token=garbage")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("error", resp.context)

    def test_get_shows_error_for_wrong_action_token(self):
        token = _make_verification_token(self.user.pk)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("error", resp.context)
        self.assertContains(resp, "Invalid password reset link.")

    def test_get_missing_token_returns_400(self):
        """GET without token returns 400."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_post_resets_password(self):
        """POST with valid token and new_password resets the password."""
        token = _make_password_reset_token(self.user.pk)
        resp = self._post({"token": token, "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass1234"))
        self.assertFalse(self.user.check_password("oldpass1234"))

    def test_post_expired_token_returns_400(self):
        token = _make_password_reset_token(self.user.pk, expired=True)
        resp = self._post({"token": token, "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_post_invalid_token_returns_400(self):
        resp = self._post({"token": "garbage", "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_post_missing_token_returns_400(self):
        resp = self._post({"new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_post_missing_password_returns_400(self):
        token = _make_password_reset_token(self.user.pk)
        resp = self._post({"token": token})
        self.assertEqual(resp.status_code, 400)

    def test_post_short_password_returns_400(self):
        token = _make_password_reset_token(self.user.pk)
        resp = self._post({"token": token, "new_password": "short"})
        self.assertEqual(resp.status_code, 400)

    def test_post_wrong_action_token_returns_400(self):
        """Token with verify_email action cannot be used for password reset."""
        token = _make_verification_token(self.user.pk)
        resp = self._post({"token": token, "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_post_nonexistent_user_returns_404(self):
        token = _make_password_reset_token(99999)
        resp = self._post({"token": token, "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 404)

    def test_post_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)



# ── Change Password API ──────────────────────────────────────────────


@tag('core')
class ChangePasswordAPITest(TestCase):
    """Tests for POST /account/api/change-password."""

    url = "/account/api/change-password"

    def setUp(self):
        self.user = User.objects.create_user(
            email="changepw@example.com", password="oldpass1234"
        )
        self.client.force_login(self.user)

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_change_password_succeeds(self):
        """Valid current + new password changes the password."""
        resp = self._post(
            {"current_password": "oldpass1234", "new_password": "newpass5678"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass5678"))

    def test_wrong_current_password_returns_400(self):
        resp = self._post(
            {"current_password": "wrongpass", "new_password": "newpass5678"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("incorrect", resp.json()["error"].lower())

    def test_missing_current_password_returns_400(self):
        resp = self._post({"new_password": "newpass5678"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_new_password_returns_400(self):
        resp = self._post({"current_password": "oldpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_short_new_password_returns_400(self):
        resp = self._post(
            {"current_password": "oldpass1234", "new_password": "short"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_session_remains_valid_after_change(self):
        """User stays logged in after password change."""
        self._post(
            {"current_password": "oldpass1234", "new_password": "newpass5678"}
        )
        resp = self.client.get("/account/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_logged_out_redirects(self):
        """Logged-out user gets redirect."""
        self.client.logout()
        resp = self._post(
            {"current_password": "oldpass1234", "new_password": "newpass5678"}
        )
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

# ── Registration Page ─────────────────────────────────────────────────


class RegisterPageTest(TestCase):
    """Tests for the registration page template."""

    def test_register_page_uses_correct_template(self):
        resp = self.client.get("/accounts/register/")
        self.assertTemplateUsed(resp, "accounts/register.html")

    def test_register_page_contains_form(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("register-form", content)

    def test_register_page_contains_email_input(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("register-email", content)

    def test_register_page_contains_password_input(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("register-password", content)

    def test_register_page_contains_oauth_links(self):
        site = Site.objects.get_current()
        for provider, name in (('google', 'Google'), ('github', 'GitHub')):
            app = SocialApp.objects.create(
                provider=provider,
                name=name,
                client_id=f'{provider}-cid',
                secret=f'{provider}-secret',
            )
            app.sites.add(site)
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("Sign up with Google", content)
        self.assertIn("Sign up with GitHub", content)

    def test_register_page_contains_slack_button(self):
        app = SocialApp.objects.create(
            provider='slack',
            name='Slack',
            client_id='slack-cid',
            secret='slack-secret',
        )
        app.sites.add(Site.objects.get_current())
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("Sign up with Slack", content)

    def test_register_page_contains_slack_oauth_link(self):
        app = SocialApp.objects.create(
            provider='slack',
            name='Slack',
            client_id='slack-cid',
            secret='slack-secret',
        )
        app.sites.add(Site.objects.get_current())
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("/accounts/slack/login/", content)

    def test_register_page_links_to_login(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("/accounts/login/", content)

    def test_register_page_redirects_authenticated_user(self):
        user = User.objects.create_user(email="auth@example.com")
        self.client.force_login(user)
        resp = self.client.get("/accounts/register/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")

    def test_register_url_name(self):
        url = reverse("account_register")
        self.assertEqual(url, "/accounts/register/")

    def test_register_shortcut_redirects(self):
        """GET /register redirects to /accounts/register/."""
        resp = self.client.get("/register")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/accounts/register/")

    def test_register_shortcut_preserves_safe_next(self):
        resp = self.client.get("/register?next=/events/demo")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/accounts/register/?next=%2Fevents%2Fdemo")

    def test_signup_shortcut_preserves_safe_next(self):
        resp = self.client.get("/accounts/signup/?next=/courses/demo")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/accounts/register/?next=%2Fcourses%2Fdemo")

    def test_signup_shortcut_drops_unsafe_next(self):
        resp = self.client.get("/accounts/signup/?next=https://evil.example")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/accounts/register/")


# ── Login Page Updates ────────────────────────────────────────────────


class LoginPageEmailPasswordTest(TestCase):
    """Tests that login page now includes email+password form."""

    @classmethod
    def setUpTestData(cls):
        # OAuth buttons + divider only render when at least one
        # ``SocialApp`` row is configured (issue #322 gating). Seed two
        # so the existing assertions for Google + GitHub buttons hold.
        site = Site.objects.get_current()
        for provider, name in (('google', 'Google'), ('github', 'GitHub')):
            app = SocialApp.objects.create(
                provider=provider, name=name,
                client_id=f'{provider}-cid', secret=f'{provider}-sec',
            )
            app.sites.add(site)

    def test_login_page_contains_email_input(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("login-email", content)

    def test_login_page_contains_password_input(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("login-password", content)

    def test_login_page_contains_login_form(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("login-form", content)
        self.assertIn('aria-busy="false"', content)

    def test_login_page_contains_register_link(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("Create account", content)

    def test_login_page_still_has_oauth_buttons(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("Sign in with Google", content)
        self.assertIn("Sign in with GitHub", content)

    def test_login_page_contains_divider(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("or continue with", content)

    def test_login_page_contains_submit_feedback_hooks(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn('id="login-submit"', content)
        self.assertIn('data-idle-text="Sign in"', content)
        self.assertIn('data-loading-text="Signing in..."', content)
        self.assertIn("loginPending", content)
        self.assertIn("setLoginPending(true)", content)


# ── Email Verification Banner ─────────────────────────────────────────


class EmailVerificationBannerTest(TestCase):
    """Tests for the email verification banner on the account page."""

    def test_unverified_user_sees_banner(self):
        """Unverified user sees the verification banner."""
        user = User.objects.create_user(
            email="unverified@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        content = resp.content.decode()
        self.assertIn("email-verification-banner", content)
        self.assertIn("Verify your email", content)

    def test_verified_user_does_not_see_banner(self):
        """Verified user does not see the verification banner."""
        user = User.objects.create_user(
            email="verified@example.com", password="test1234"
        )
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        self.client.force_login(user)
        resp = self.client.get("/account/")
        content = resp.content.decode()
        self.assertNotIn("email-verification-banner", content)


# ── Account Page Change Password Section ──────────────────────────────


class AccountPageChangePasswordSectionTest(TestCase):
    """Tests for the change password section on the account page."""

    def test_change_password_section_exists(self):
        user = User.objects.create_user(
            email="cpw@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        content = resp.content.decode()
        self.assertIn("change-password-section", content)
        self.assertIn("Change Password", content)

    def test_change_password_form_exists(self):
        user = User.objects.create_user(
            email="cpw2@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        content = resp.content.decode()
        self.assertIn("change-password-form", content)
        self.assertIn("current-password", content)
        self.assertIn("new-password", content)


# ── Works Alongside OAuth ─────────────────────────────────────────────


@tag('core')
class OAuthCoexistenceTest(TestCase):
    """Tests that email+password auth works alongside existing OAuth."""

    def test_email_registered_user_can_login(self):
        """User registered via email can authenticate with password."""
        User.objects.create_user(
            email="emailuser@example.com", password="mypass1234"
        )
        resp = self.client.post(
            "/api/login",
            data=json.dumps(
                {"email": "emailuser@example.com", "password": "mypass1234"}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        user = User.objects.get(email="emailuser@example.com")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_oauth_user_without_password_cannot_login_with_password(self):
        """User without usable password (OAuth only) gets 401 on login API."""
        User.objects.create_user(email="oauthuser@example.com")
        resp = self.client.post(
            "/api/login",
            data=json.dumps(
                {"email": "oauthuser@example.com", "password": "anything"}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_same_user_model_used(self):
        """Both email+password and OAuth users share the same User model."""
        from django.conf import settings
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")


# ── Unverified Users Access ───────────────────────────────────────────


@tag('core')
class UnverifiedUserAccessTest(TestCase):
    """Tests that unverified users have same access as free tier."""

    def test_unverified_user_can_access_public_pages(self):
        """Unverified user can access the homepage."""
        user = User.objects.create_user(
            email="unv@example.com", password="test1234"
        )
        self.assertFalse(user.email_verified)
        self.client.force_login(user)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "AI Shipping Labs")

    def test_unverified_user_can_access_account_page(self):
        """Unverified user can access their account page."""
        user = User.objects.create_user(
            email="unv2@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "accounts/account.html")
        self.assertEqual(resp.context["user"].email, "unv2@example.com")


# ── CSRF Cookie on Login/Register Pages ─────────────────────────────


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
)
class CsrfCookieOnAuthPagesTest(TestCase):
    """Regression tests: login and register pages must set the csrftoken cookie.

    Without @ensure_csrf_cookie on login_view and register_view, the
    csrftoken cookie is not sent in the response. Client-side JS that reads
    the cookie to set the X-CSRFToken header on API requests (e.g. POST
    /api/login) gets an empty string, causing Django's CSRF middleware to
    reject the request with 403 Forbidden.
    """

    def test_login_page_sets_csrftoken_cookie(self):
        """GET /accounts/login/ must include a csrftoken cookie."""
        resp = self.client.get("/accounts/login/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            settings.CSRF_COOKIE_NAME,
            resp.cookies,
            "Login page did not set the csrftoken cookie",
        )
        cookie_value = resp.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertTrue(
            len(cookie_value) > 0,
            "csrftoken cookie is empty on the login page",
        )

    @override_settings(
        CSRF_COOKIE_SECURE=True,
        SECURE_HSTS_SECONDS=3600,
    )
    def test_login_page_sets_secure_csrftoken_and_hsts_on_https(self):
        resp = self.client.get(
            "/accounts/login/",
            HTTP_X_FORWARDED_PROTO="https",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.cookies[settings.CSRF_COOKIE_NAME]["secure"])
        self.assertEqual(
            resp.headers["Strict-Transport-Security"],
            "max-age=3600",
        )

    @override_settings(
        CSRF_COOKIE_SECURE=False,
        SESSION_COOKIE_SECURE=False,
        SECURE_HSTS_SECONDS=0,
    )
    def test_login_page_keeps_local_http_cookie_behavior(self):
        resp = self.client.get("/accounts/login/")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.cookies[settings.CSRF_COOKIE_NAME]["secure"])
        self.assertNotIn("Strict-Transport-Security", resp.headers)

    @override_settings(
        CSRF_COOKIE_SECURE=True,
        SESSION_COOKIE_SECURE=True,
        SECURE_HSTS_SECONDS=3600,
    )
    def test_login_api_sets_secure_session_cookie_on_https(self):
        User.objects.create_user(
            email="secure-session@example.com",
            password="secure1234",
        )
        page_resp = self.client.get(
            "/accounts/login/",
            HTTP_X_FORWARDED_PROTO="https",
        )
        csrf_token = page_resp.cookies[settings.CSRF_COOKIE_NAME].value

        login_resp = self.client.post(
            "/api/login",
            data=json.dumps({
                "email": "secure-session@example.com",
                "password": "secure1234",
            }),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
            HTTP_X_FORWARDED_PROTO="https",
        )

        self.assertEqual(login_resp.status_code, 200)
        self.assertTrue(login_resp.cookies[settings.SESSION_COOKIE_NAME]["secure"])

    def test_register_page_sets_csrftoken_cookie(self):
        """GET /accounts/register/ must include a csrftoken cookie."""
        resp = self.client.get("/accounts/register/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            settings.CSRF_COOKIE_NAME,
            resp.cookies,
            "Register page did not set the csrftoken cookie",
        )
        cookie_value = resp.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertTrue(
            len(cookie_value) > 0,
            "csrftoken cookie is empty on the register page",
        )

    def test_login_api_works_with_csrf_from_login_page(self):
        """Full flow: visit login page, extract CSRF token, POST /api/login.

        This reproduces the bug where getCsrfToken() returned an empty
        string because the csrftoken cookie was missing, causing 403.
        """
        User.objects.create_user(
            email="csrftest@example.com", password="secure1234"
        )

        # Step 1: Visit the login page to get the CSRF cookie
        page_resp = self.client.get("/accounts/login/")
        self.assertEqual(page_resp.status_code, 200)
        csrf_token = page_resp.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertTrue(len(csrf_token) > 0)

        # Step 2: POST to /api/login with the CSRF token in the header
        # (mimicking the JS fetch() call with X-CSRFToken header)
        login_resp = self.client.post(
            "/api/login",
            data=json.dumps(
                {"email": "csrftest@example.com", "password": "secure1234"}
            ),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
        )
        self.assertEqual(
            login_resp.status_code,
            200,
            f"Expected 200 OK but got {login_resp.status_code}. "
            "CSRF token from login page cookie may not be working.",
        )
        self.assertEqual(login_resp.json()["status"], "ok")

    def test_register_api_works_with_csrf_from_register_page(self):
        """Full flow: visit register page, extract CSRF token, POST /api/register.

        Same bug pattern as login -- the register page JS also needs
        the csrftoken cookie to POST to /api/register.
        """
        # Step 1: Visit the register page to get the CSRF cookie
        page_resp = self.client.get("/accounts/register/")
        self.assertEqual(page_resp.status_code, 200)
        csrf_token = page_resp.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertTrue(len(csrf_token) > 0)

        # Step 2: POST to /api/register with the CSRF token in the header
        register_resp = self.client.post(
            "/api/register",
            data=json.dumps(
                {"email": "csrfnew@example.com", "password": "secure1234"}
            ),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
        )
        self.assertEqual(
            register_resp.status_code,
            201,
            f"Expected 201 Created but got {register_resp.status_code}. "
            "CSRF token from register page cookie may not be working.",
        )
        self.assertEqual(register_resp.json()["status"], "ok")


class ResendVerificationApiTest(TestCase):
    """Tests for POST /account/api/resend-verification."""

    URL = "/account/api/resend-verification"

    def setUp(self):
        from django.core.cache import cache as _cache
        _cache.clear()

    def _make_unverified_user(self, email="resend-unverified@example.com"):
        return User.objects.create_user(email=email, password="test1234")

    def _make_verified_user(self, email="resend-verified@example.com"):
        user = User.objects.create_user(email=email, password="test1234")
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        return user

    def test_unverified_user_post_sends_one_email(self):
        with patch("accounts.views.auth._send_verification_email") as patched_send:
            user = self._make_unverified_user()
            self.client.force_login(user)

            response = self.client.post(self.URL, follow=True)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.redirect_chain, [("/account/", 302)])
            patched_send.assert_called_once_with(user)
            self.assertContains(response, "Verification email sent")
            self.assertContains(response, 'data-message-tag="success"')

    def test_second_post_within_window_does_not_resend(self):
        with patch("accounts.views.auth._send_verification_email") as patched_send:
            user = self._make_unverified_user("throttle@example.com")
            self.client.force_login(user)

            self.client.post(self.URL)
            self.assertEqual(patched_send.call_count, 1)

            response = self.client.post(self.URL, follow=True)

            self.assertEqual(patched_send.call_count, 1)
            self.assertEqual(response.redirect_chain, [("/account/", 302)])
            self.assertContains(response, "minute")
            self.assertContains(response, 'data-message-tag="warning"')

    def test_failed_send_shows_error_and_does_not_throttle(self):
        from django.core.cache import cache as _cache

        with patch(
            "accounts.views.auth._send_verification_email",
            return_value=None,
        ) as patched_send:
            user = self._make_unverified_user("send-fails@example.com")
            self.client.force_login(user)

            response = self.client.post(self.URL, follow=True)

            self.assertEqual(response.redirect_chain, [("/account/", 302)])
            patched_send.assert_called_once_with(user)
            self.assertContains(response, "verification email")
            self.assertContains(response, 'data-message-tag="error"')
            self.assertIsNone(_cache.get(f"verify-email-resend:{user.id}"))

    def test_raised_send_exception_shows_error_logs_and_does_not_throttle(self):
        from django.core.cache import cache as _cache

        with patch(
            "accounts.views.auth._send_verification_email",
            side_effect=RuntimeError("mail backend unavailable"),
        ) as patched_send:
            user = self._make_unverified_user("send-raises@example.com")
            self.client.force_login(user)

            with self.assertLogs("accounts.views.account", level="ERROR") as logs:
                response = self.client.post(self.URL, follow=True)

            self.assertEqual(response.redirect_chain, [("/account/", 302)])
            patched_send.assert_called_once_with(user)
            self.assertContains(response, "verification email")
            self.assertContains(response, 'data-message-tag="error"')
            self.assertIsNone(_cache.get(f"verify-email-resend:{user.id}"))
            self.assertIn(
                f"Verification email resend failed for user_id={user.id}",
                "\n".join(logs.output),
            )

    def test_clearing_throttle_key_allows_resend(self):
        from django.core.cache import cache as _cache

        with patch("accounts.views.auth._send_verification_email") as patched_send:
            user = self._make_unverified_user("reset@example.com")
            self.client.force_login(user)

            self.client.post(self.URL)
            self.client.post(self.URL)
            self.assertEqual(patched_send.call_count, 1)

            _cache.delete(f"verify-email-resend:{user.id}")

            self.client.post(self.URL)
            self.assertEqual(patched_send.call_count, 2)

    def test_verified_user_post_does_not_send(self):
        with patch("accounts.views.auth._send_verification_email") as patched_send:
            user = self._make_verified_user()
            self.client.force_login(user)

            response = self.client.post(self.URL, follow=True)

            self.assertEqual(response.redirect_chain, [("/account/", 302)])
            patched_send.assert_not_called()
            self.assertContains(response, "already verified")
            self.assertContains(response, 'data-message-tag="info"')

    def test_anonymous_post_redirects_to_login_and_does_not_send(self):
        from django.core.cache import cache as _cache

        with patch("accounts.views.auth._send_verification_email") as patched_send:
            response = self.client.post(self.URL)

            self.assertEqual(response.status_code, 302)
            self.assertIn("/accounts/login/", response.url)
            patched_send.assert_not_called()
            self.assertIsNone(_cache.get("verify-email-resend:1"))

    def test_get_returns_405(self):
        user = self._make_unverified_user("get405@example.com")
        self.client.force_login(user)

        response = self.client.get(self.URL)

        self.assertEqual(response.status_code, 405)

    def test_per_user_throttle_does_not_block_other_users(self):
        with patch("accounts.views.auth._send_verification_email") as patched_send:
            user_a = self._make_unverified_user("a-throttle@example.com")
            user_b = self._make_unverified_user("b-throttle@example.com")

            self.client.force_login(user_a)
            self.client.post(self.URL)

            self.client.logout()
            self.client.force_login(user_b)
            self.client.post(self.URL)

            self.assertEqual(patched_send.call_count, 2)
            send_emails = sorted(
                call.args[0].email for call in patched_send.call_args_list
            )
            self.assertEqual(
                send_emails,
                ["a-throttle@example.com", "b-throttle@example.com"],
            )
