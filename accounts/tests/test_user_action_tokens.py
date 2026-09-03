"""Tests for shared first-party user action JWT generation."""

import datetime

import jwt
from django.conf import settings
from django.test import TestCase, override_settings

from accounts.models import User
from accounts.utils.tokens import (
    JWT_ALGORITHM,
    PASSWORD_RESET_PROOF_CLAIM,
    generate_password_reset_token,
    generate_user_action_token,
    resolve_password_reset_token,
)

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

    def test_password_reset_token_does_not_expose_password_material(self):
        user = User.objects.create_user(
            email="reset-secret@example.com",
            password="oldpass1234",
        )
        token = generate_password_reset_token(user)
        payload = _decode(token)

        self.assertNotIn(user.password, token)
        self.assertNotIn("oldpass1234", token)
        self.assertNotIn(user.password, payload[PASSWORD_RESET_PROOF_CLAIM])
        self.assertNotIn("oldpass1234", payload[PASSWORD_RESET_PROOF_CLAIM])

    def test_password_reset_wrapper_token_is_accepted_by_validator(self):
        from accounts.views.auth import _generate_password_reset_token

        user = User.objects.create_user(
            email="reset-validator@example.com",
            password="oldpass1234",
        )
        token = _generate_password_reset_token(user.pk)
        resolved_user, payload = resolve_password_reset_token(token)
        self.assertEqual(resolved_user.pk, user.pk)
        self.assertEqual(payload["action"], "password_reset")

    def test_legacy_password_reset_jwt_without_proof_is_rejected(self):
        user = User.objects.create_user(
            email="legacy-reset@example.com",
            password="oldpass1234",
        )
        token = generate_user_action_token(
            user.pk,
            "password_reset",
            expiry_hours=1,
        )

        get_response = self.client.get(f"/api/password-reset?token={token}")
        self.assertEqual(get_response.context["error"], "Invalid password reset link.")
        self.assertNotIn("token", get_response.context)

        post_response = self.client.post(
            "/api/password-reset",
            data='{"token": "%s", "new_password": "newpass1234"}' % token,
            content_type="application/json",
        )
        self.assertEqual(post_response.status_code, 400)
        self.assertEqual(post_response.json(), {"error": "Invalid token"})
        user.refresh_from_db()
        self.assertTrue(user.check_password("oldpass1234"))

    def test_secure_generator_token_is_rejected_by_verify_email(self):
        user = User.objects.create_user(
            email="reset-not-verify@example.com",
            password="oldpass1234",
            email_verified=False,
        )
        token = generate_password_reset_token(user)

        response = self.client.get(f"/api/verify-email?token={token}")
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)

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
