"""Tests for shared first-party user action JWT generation."""

import datetime

import jwt
from django.conf import settings
from django.test import TestCase, override_settings

from accounts.models import User
from accounts.utils.tokens import JWT_ALGORITHM, generate_user_action_token

FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _decode(token):
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={"verify_exp": False},
    )


def _exp_datetime(payload):
    return datetime.datetime.fromtimestamp(
        payload["exp"],
        tz=datetime.timezone.utc,
    )


class UserActionTokenHelperTest(TestCase):
    def test_generates_expiring_action_token(self):
        started_at = datetime.datetime.now(datetime.timezone.utc)

        token = generate_user_action_token(
            42,
            "verify_email",
            expiry_hours=24,
        )
        payload = _decode(token)

        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["action"], "verify_email")
        self.assertGreater(
            _exp_datetime(payload),
            started_at + datetime.timedelta(hours=23, minutes=59),
        )

    def test_generates_no_expiry_action_token(self):
        token = generate_user_action_token(42, "unsubscribe")
        payload = _decode(token)

        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["action"], "unsubscribe")
        self.assertNotIn("exp", payload)

    def test_merges_allowed_extra_payload_fields(self):
        token = generate_user_action_token(
            42,
            "verify_email",
            expiry_hours=24,
            redirect_to="/downloads/test/file",
        )
        payload = _decode(token)

        self.assertEqual(payload["redirect_to"], "/downloads/test/file")

    def test_rejects_unsupported_extra_payload_fields(self):
        with self.assertRaises(ValueError):
            generate_user_action_token(
                42,
                "verify_email",
                expiry_hours=24,
                role="admin",
            )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class UserActionTokenFlowCompatibilityTest(TestCase):
    def test_account_verification_wrapper_token_is_accepted(self):
        from accounts.views.auth import _generate_verification_token

        user = User.objects.create_user(
            email="verify-wrapper@example.com",
            password="oldpass1234",
            email_verified=False,
        )
        token = _generate_verification_token(user.pk)

        response = self.client.get(f"/api/verify-email?token={token}")

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_password_reset_wrapper_token_is_accepted_by_get_and_post(self):
        from accounts.views.auth import _generate_password_reset_token

        user = User.objects.create_user(
            email="reset-wrapper@example.com",
            password="oldpass1234",
        )
        token = _generate_password_reset_token(user.pk)

        get_response = self.client.get(f"/api/password-reset?token={token}")
        self.assertEqual(get_response.status_code, 200)
        self.assertTemplateUsed(get_response, "accounts/password_reset.html")

        post_response = self.client.post(
            "/api/password-reset",
            data='{"token": "%s", "new_password": "newpass1234"}' % token,
            content_type="application/json",
        )

        self.assertEqual(post_response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("newpass1234"))

    def test_newsletter_verification_wrapper_preserves_redirect_to(self):
        from email_app.views.newsletter import _generate_verification_token

        user = User.objects.create_user(
            email="newsletter-wrapper@example.com",
            password="oldpass1234",
            email_verified=False,
        )
        token = _generate_verification_token(
            user.pk,
            redirect_to="/downloads/test/file",
        )

        response = self.client.get(f"/api/verify-email?token={token}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/downloads/test/file")
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
