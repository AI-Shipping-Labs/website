"""Tests for newsletter subscribe, verify-email (with lead magnet), and unsubscribe flows.

Covers:
- POST /api/subscribe: new user creation, idempotent for existing, no info leak
- POST /api/subscribe with redirect_to: lead magnet flow
- GET /api/verify-email?token=: email verification, redirect_to support
- GET /api/unsubscribe?token=: unsubscribe via JWT
- GET /subscribe: subscribe page renders
- Subscribe form appears in footer, subscribe page, article CTAs
- Admin subscriber list, filter, CSV export
- JWT token generation and validation
- Edge cases: invalid email, missing fields, expired/invalid tokens
"""

import json
from unittest.mock import patch

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()

JWT_ALGORITHM = "HS256"


class SubscribeAPITest(TestCase):
    """Test POST /api/subscribe endpoint."""

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_new_email_creates_user(self, mock_send):
        response = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "new@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("Check your email", data["message"])

        # User should be created
        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.email_verified)
        self.assertFalse(user.unsubscribed)

        # Verification email should be sent
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        self.assertEqual(call_args[0][0].email, "new@example.com")

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_existing_email_returns_200(self, mock_send):
        """Existing email returns same message (no information leak)."""
        User.objects.create_user(email="existing@example.com")

        response = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "existing@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("Check your email", data["message"])

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_existing_verified_email_no_resend(self, mock_send):
        """If existing user is already verified, don't re-send verification."""
        User.objects.create_user(
            email="verified@example.com",
            email_verified=True,
        )

        response = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "verified@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        # Should NOT send verification email (already verified)
        mock_send.assert_not_called()

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_existing_unverified_resends_email(self, mock_send):
        """If existing user is unverified, re-send verification email."""
        User.objects.create_user(
            email="unverified@example.com",
            email_verified=False,
        )

        response = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "unverified@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()

    def test_subscribe_missing_email_returns_400(self):
        response = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Email is required", response.json()["error"])

    def test_subscribe_invalid_email_returns_400(self):
        response = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "not-an-email"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid email", response.json()["error"])

    def test_subscribe_invalid_json_returns_400(self):
        response = self.client.post(
            "/api/subscribe",
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_subscribe_get_method_not_allowed(self):
        response = self.client.get("/api/subscribe")
        self.assertEqual(response.status_code, 405)

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_case_insensitive_email(self, mock_send):
        """Email matching should be case-insensitive."""
        User.objects.create_user(email="user@example.com")

        response = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "USER@EXAMPLE.COM"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        # Should not create a duplicate user
        self.assertEqual(User.objects.filter(email__iexact="user@example.com").count(), 1)


class SubscribeLeadMagnetTest(TestCase):
    """Test subscribe API with redirect_to for lead magnet flow."""

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_with_redirect_to(self, mock_send):
        response = self.client.post(
            "/api/subscribe",
            data=json.dumps({
                "email": "lead@example.com",
                "redirect_to": "/downloads/ai-cheat-sheet/file",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        # Verify the redirect_to was passed to the email sending function
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        self.assertEqual(
            call_args[1].get("redirect_to") or call_args[0][1],
            "/downloads/ai-cheat-sheet/file",
        )


class VerifyEmailAPITest(TestCase):
    """Test GET /api/verify-email?token= endpoint."""

    def _make_token(self, user_id, redirect_to=None, expired=False):
        """Helper to generate a test JWT token."""
        import datetime

        payload = {
            "user_id": user_id,
            "action": "verify_email",
        }
        if expired:
            payload["exp"] = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        else:
            payload["exp"] = (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(hours=24)
            )
        if redirect_to:
            payload["redirect_to"] = redirect_to
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)

    def test_verify_email_sets_verified(self):
        user = User.objects.create_user(email="verify@example.com")
        self.assertFalse(user.email_verified)

        token = self._make_token(user.pk)
        response = self.client.get(f"/api/verify-email?token={token}")
        self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_verify_email_already_verified(self):
        user = User.objects.create_user(
            email="already@example.com",
            email_verified=True,
        )
        token = self._make_token(user.pk)
        response = self.client.get(f"/api/verify-email?token={token}")
        self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_verify_email_expired_token(self):
        user = User.objects.create_user(email="expired@example.com")
        token = self._make_token(user.pk, expired=True)
        response = self.client.get(f"/api/verify-email?token={token}")
        self.assertEqual(response.status_code, 400)

    def test_verify_email_invalid_token(self):
        response = self.client.get("/api/verify-email?token=invalid-token")
        self.assertEqual(response.status_code, 400)

    def test_verify_email_missing_token(self):
        response = self.client.get("/api/verify-email")
        self.assertEqual(response.status_code, 400)

    def test_verify_email_wrong_action(self):
        import datetime

        payload = {
            "user_id": 1,
            "action": "password_reset",
            "exp": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(hours=24)
            ),
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)
        response = self.client.get(f"/api/verify-email?token={token}")
        self.assertEqual(response.status_code, 400)

    def test_verify_email_user_not_found(self):
        token = self._make_token(99999)
        response = self.client.get(f"/api/verify-email?token={token}")
        self.assertEqual(response.status_code, 404)

    def test_verify_email_with_redirect_to(self):
        """Lead magnet flow: verify email then redirect to download."""
        user = User.objects.create_user(email="magnet@example.com")
        token = self._make_token(
            user.pk,
            redirect_to="/downloads/ai-cheat-sheet/file",
        )
        response = self.client.get(f"/api/verify-email?token={token}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            "/downloads/ai-cheat-sheet/file",
        )

        user.refresh_from_db()
        self.assertTrue(user.email_verified)


class UnsubscribeAPITest(TestCase):
    """Test GET /api/unsubscribe?token= endpoint."""

    def _make_unsubscribe_token(self, user_id):
        """Helper to generate an unsubscribe JWT token (no expiry)."""
        payload = {
            "user_id": user_id,
            "action": "unsubscribe",
        }
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)

    def test_unsubscribe_sets_unsubscribed(self):
        user = User.objects.create_user(email="unsub@example.com")
        self.assertFalse(user.unsubscribed)

        token = self._make_unsubscribe_token(user.pk)
        response = self.client.get(f"/api/unsubscribe?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unsubscribed")

        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)

    def test_unsubscribe_already_unsubscribed(self):
        user = User.objects.create_user(
            email="already-unsub@example.com",
            unsubscribed=True,
        )
        token = self._make_unsubscribe_token(user.pk)
        response = self.client.get(f"/api/unsubscribe?token={token}")
        self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)

    def test_unsubscribe_invalid_token(self):
        response = self.client.get("/api/unsubscribe?token=garbage")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid")

    def test_unsubscribe_missing_token(self):
        response = self.client.get("/api/unsubscribe")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Token is required")

    def test_unsubscribe_wrong_action(self):
        payload = {
            "user_id": 1,
            "action": "verify_email",
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)
        response = self.client.get(f"/api/unsubscribe?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid")

    def test_unsubscribe_user_not_found(self):
        token = self._make_unsubscribe_token(99999)
        response = self.client.get(f"/api/unsubscribe?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not found")

    def test_unsubscribe_token_no_expiry(self):
        """Unsubscribe tokens should work without an expiry."""
        user = User.objects.create_user(email="noexp@example.com")
        token = self._make_unsubscribe_token(user.pk)

        # Decode the token - should have no 'exp' claim
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        self.assertNotIn("exp", payload)

        # Token should still work
        response = self.client.get(f"/api/unsubscribe?token={token}")
        self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)


class SubscribePageTest(TestCase):
    """Test the /subscribe dedicated page."""

    def test_subscribe_page_returns_200(self):
        response = self.client.get("/subscribe")
        self.assertEqual(response.status_code, 200)

    def test_subscribe_page_uses_correct_template(self):
        response = self.client.get("/subscribe")
        self.assertTemplateUsed(response, "email_app/subscribe.html")

    def test_subscribe_page_contains_form(self):
        response = self.client.get("/subscribe")
        self.assertContains(response, "subscribe-form")
        self.assertContains(response, 'type="email"')
        self.assertContains(response, "Subscribe")


class FooterSubscribeFormTest(TestCase):
    """Test that the subscribe form appears in the site footer."""

    def test_homepage_footer_has_subscribe_form(self):
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("subscribe-form", content)
        self.assertIn('type="email"', content)


class ArticleCTASubscribeFormTest(TestCase):
    """Test that the subscribe CTA appears on article detail pages."""

    def test_article_page_has_subscribe_cta(self):
        from content.models import Article
        from django.utils import timezone
        import datetime

        article = Article.objects.create(
            title="Test Article",
            slug="test-article",
            content_markdown="# Hello World",
            status="published",
            date=datetime.date.today(),
            published_at=timezone.now(),
        )

        response = self.client.get(f"/blog/{article.slug}")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("subscribe-form", content)
        self.assertIn("Enjoyed this article?", content)


class EmailServiceUnsubscribeLinkTest(TestCase):
    """Test that every outgoing email includes an unsubscribe link."""

    @patch("email_app.services.email_service.EmailService._send_ses", return_value="msg-id")
    def test_email_includes_unsubscribe_link(self, mock_ses):
        from email_app.services.email_service import EmailService

        user = User.objects.create_user(email="test@example.com")
        service = EmailService()
        service.send(user, "welcome", {"tier_name": "Free"})

        mock_ses.assert_called_once()
        html_body = mock_ses.call_args[0][2]
        self.assertIn("Unsubscribe", html_body)
        self.assertIn("/api/unsubscribe?token=", html_body)


class SubscriberAdminTest(TestCase):
    """Test admin subscriber list, filter, and CSV export."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="adminpass123",
        )
        self.client.login(email="admin@example.com", password="adminpass123")

    def test_subscriber_admin_list_accessible(self):
        response = self.client.get("/admin/email_app/subscriber/")
        self.assertEqual(response.status_code, 200)

    def test_subscriber_admin_shows_users(self):
        User.objects.create_user(email="sub1@example.com")
        User.objects.create_user(email="sub2@example.com")

        response = self.client.get("/admin/email_app/subscriber/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("sub1@example.com", content)
        self.assertIn("sub2@example.com", content)

    def test_subscriber_admin_filter_verified(self):
        User.objects.create_user(
            email="verified@example.com", email_verified=True
        )
        User.objects.create_user(
            email="unverified@example.com", email_verified=False
        )

        response = self.client.get(
            "/admin/email_app/subscriber/?sub_status=verified"
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("verified@example.com", content)

    def test_subscriber_admin_filter_unsubscribed(self):
        User.objects.create_user(
            email="active@example.com", unsubscribed=False
        )
        User.objects.create_user(
            email="unsub@example.com", unsubscribed=True
        )

        response = self.client.get(
            "/admin/email_app/subscriber/?sub_status=unsubscribed"
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("unsub@example.com", content)

    def test_subscriber_admin_csv_export(self):
        user1 = User.objects.create_user(
            email="export1@example.com", email_verified=True
        )
        user2 = User.objects.create_user(
            email="export2@example.com", email_verified=False
        )

        # Select both users and trigger CSV export
        response = self.client.post(
            "/admin/email_app/subscriber/",
            {
                "action": "export_csv",
                "_selected_action": [user1.pk, user2.pk, self.admin_user.pk],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("subscribers.csv", response["Content-Disposition"])

        # Parse CSV content
        content = response.content.decode("utf-8")
        lines = content.strip().split("\n")
        self.assertGreaterEqual(len(lines), 3)  # header + 2 data rows (at least)
        self.assertIn("Email", lines[0])
        self.assertIn("export1@example.com", content)
        self.assertIn("export2@example.com", content)


class AccountResubscribeTest(TestCase):
    """Test that the /account page has a toggle to re-subscribe."""

    def test_account_page_shows_newsletter_toggle(self):
        user = User.objects.create_user(
            email="toggle@example.com",
            password="testpass123",
            unsubscribed=True,
        )
        self.client.login(email="toggle@example.com", password="testpass123")

        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("newsletter-toggle", content)
        self.assertIn("Newsletter", content)

    def test_account_api_resubscribe(self):
        user = User.objects.create_user(
            email="resub@example.com",
            password="testpass123",
            unsubscribed=True,
        )
        self.client.login(email="resub@example.com", password="testpass123")

        response = self.client.post(
            "/account/api/email-preferences",
            data=json.dumps({"newsletter": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["newsletter"])

        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)


class TokenGenerationTest(TestCase):
    """Test JWT token generation for verification and unsubscribe."""

    def test_verification_token_contains_user_id(self):
        from email_app.views.newsletter import _generate_verification_token

        token = _generate_verification_token(42)
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["action"], "verify_email")

    def test_verification_token_with_redirect_to(self):
        from email_app.views.newsletter import _generate_verification_token

        token = _generate_verification_token(
            42, redirect_to="/downloads/test/file"
        )
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
        self.assertEqual(payload["redirect_to"], "/downloads/test/file")

    def test_verification_token_without_redirect_to(self):
        from email_app.views.newsletter import _generate_verification_token

        token = _generate_verification_token(42)
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
        self.assertNotIn("redirect_to", payload)

    def test_verification_token_has_expiry(self):
        from email_app.views.newsletter import _generate_verification_token

        token = _generate_verification_token(42)
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
        self.assertIn("exp", payload)

    def test_unsubscribe_token_has_no_expiry(self):
        from email_app.views.newsletter import _generate_unsubscribe_token

        token = _generate_unsubscribe_token(42)
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        self.assertNotIn("exp", payload)
        self.assertEqual(payload["action"], "unsubscribe")
