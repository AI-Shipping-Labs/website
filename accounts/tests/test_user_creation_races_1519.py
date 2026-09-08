"""Race contracts for register and newsletter user creation (issue #1519)."""

import datetime
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings, tag
from django.utils import timezone

from accounts.models import User
from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER, SIGNUP_SOURCE_SIGNUP
from accounts.services.user_creation import create_user_conflict_safe

FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
REGISTER_URL = "/api/register"
SUBSCRIBE_URL = "/api/subscribe"
SUBSCRIBE_SUCCESS = (
    "Thanks! We've created a free account and emailed a verification link. "
    "Click it to confirm your subscription and activate your account."
)


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class UserCreationCollisionTest(TestCase):
    """Injected conflicts prove both endpoints recover without a broken tx."""

    def _register(self, email, password="new-secret-1234"):
        return self.client.post(
            REGISTER_URL,
            data=json.dumps({"email": email, "password": password}),
            content_type="application/json",
        )

    def _subscribe(self, email):
        return self.client.post(
            SUBSCRIBE_URL,
            data=json.dumps({"email": email}),
            content_type="application/json",
        )

    def test_helper_recovers_from_email_integrity_error_without_writing_winner(self):
        original_expiry = timezone.now() + datetime.timedelta(days=2)
        winner = User.objects.create_user(
            email="winner@example.com",
            password="winner-secret-1234",
            signup_source=SIGNUP_SOURCE_SIGNUP,
            verification_expires_at=original_expiry,
        )

        with patch.object(
            User.objects,
            "create_user",
            side_effect=IntegrityError("duplicate email"),
        ):
            user, created = create_user_conflict_safe(
                email=winner.email,
                password="loser-secret-1234",
                signup_source=SIGNUP_SOURCE_NEWSLETTER,
                verification_expires_at=timezone.now() + datetime.timedelta(days=7),
            )

        self.assertFalse(created)
        self.assertEqual(user.pk, winner.pk)
        winner.refresh_from_db()
        self.assertTrue(winner.check_password("winner-secret-1234"))
        self.assertFalse(winner.check_password("loser-secret-1234"))
        self.assertEqual(winner.signup_source, SIGNUP_SOURCE_SIGNUP)
        self.assertEqual(winner.verification_expires_at, original_expiry)

    def test_helper_reraises_integrity_error_without_email_winner(self):
        with (
            patch.object(
                User.objects,
                "create_user",
                side_effect=IntegrityError("different constraint"),
            ),
            self.assertRaises(IntegrityError),
        ):
            create_user_conflict_safe(email="missing-winner@example.com")

    @patch("accounts.views.auth._send_verification_email")
    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    def test_register_collision_returns_duplicate_without_side_effects(self, probe, send):
        winner = User.objects.create_user(
            email="register-race@example.com",
            password="winner-secret-1234",
            signup_source=SIGNUP_SOURCE_SIGNUP,
            verification_expires_at=timezone.now() + datetime.timedelta(days=2),
        )
        original_expiry = winner.verification_expires_at

        with (
            patch.object(User.objects, "filter") as initial_lookup,
            patch.object(
                User.objects,
                "create_user",
                side_effect=IntegrityError("duplicate email"),
            ),
        ):
            initial_lookup.return_value.exists.return_value = False
            response = self._register(winner.email)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "A user with this email already exists"})
        self.assertEqual(User.objects.filter(email__iexact=winner.email).count(), 1)
        winner.refresh_from_db()
        self.assertTrue(winner.check_password("winner-secret-1234"))
        self.assertFalse(winner.check_password("new-secret-1234"))
        self.assertEqual(winner.signup_source, SIGNUP_SOURCE_SIGNUP)
        self.assertEqual(winner.verification_expires_at, original_expiry)
        self.assertNotIn("_auth_user_id", self.client.session)
        probe.assert_not_called()
        send.assert_not_called()

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_collision_resends_for_unverified_winner(self, send):
        original_expiry = timezone.now() + datetime.timedelta(days=2)
        winner = User.objects.create_user(
            email="subscribe-race@example.com",
            password="winner-secret-1234",
            signup_source=SIGNUP_SOURCE_SIGNUP,
            verification_expires_at=original_expiry,
        )

        with (
            patch.object(
                User.objects,
                "get",
                side_effect=[User.DoesNotExist, winner],
            ),
            patch.object(
                User.objects,
                "create_user",
                side_effect=IntegrityError("duplicate email"),
            ),
        ):
            response = self._subscribe(winner.email)

        self.assertContains(response, SUBSCRIBE_SUCCESS, status_code=200)
        self.assertEqual(User.objects.filter(email__iexact=winner.email).count(), 1)
        send.assert_called_once_with(winner, redirect_to=None)
        winner.refresh_from_db()
        self.assertTrue(winner.check_password("winner-secret-1234"))
        self.assertEqual(winner.signup_source, SIGNUP_SOURCE_SIGNUP)
        self.assertEqual(winner.verification_expires_at, original_expiry)

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_collision_does_not_resend_for_verified_winner(self, send):
        winner = User.objects.create_user(
            email="verified-race@example.com",
            email_verified=True,
            signup_source=SIGNUP_SOURCE_SIGNUP,
        )

        with (
            patch.object(
                User.objects,
                "get",
                side_effect=[User.DoesNotExist, winner],
            ),
            patch.object(
                User.objects,
                "create_user",
                side_effect=IntegrityError("duplicate email"),
            ),
        ):
            response = self._subscribe(winner.email)

        self.assertContains(response, SUBSCRIBE_SUCCESS, status_code=200)
        self.assertEqual(User.objects.filter(email__iexact=winner.email).count(), 1)
        send.assert_not_called()

    @patch("accounts.views.auth._send_verification_email")
    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    def test_register_collision_keeps_newsletter_winner_unusable(self, probe, send):
        original_expiry = timezone.now() + datetime.timedelta(days=2)
        winner = User.objects.create_user(
            email="newsletter-first@example.com",
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            verification_expires_at=original_expiry,
        )

        with (
            patch.object(User.objects, "filter") as initial_lookup,
            patch.object(
                User.objects,
                "create_user",
                side_effect=IntegrityError("duplicate email"),
            ),
        ):
            initial_lookup.return_value.exists.return_value = False
            response = self._register(winner.email)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.filter(email__iexact=winner.email).count(), 1)
        winner.refresh_from_db()
        self.assertFalse(winner.has_usable_password())
        self.assertEqual(winner.signup_source, SIGNUP_SOURCE_NEWSLETTER)
        self.assertEqual(winner.verification_expires_at, original_expiry)
        self.assertNotIn("_auth_user_id", self.client.session)
        probe.assert_not_called()
        send.assert_not_called()


@tag("core", "postgresql")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class UserCreationPostgresRaceTest(TransactionTestCase):
    """Real multi-connection races verify the production uniqueness contract."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("user-creation concurrency requires PostgreSQL")

    def _run_requests(self, requests, patches):
        barrier = threading.Barrier(len(requests))

        def synchronize(original):
            def create(**kwargs):
                barrier.wait(timeout=5)
                return original(**kwargs)

            return create

        original = create_user_conflict_safe

        def post(url, payload):
            close_old_connections()
            try:
                return Client().post(
                    url,
                    data=json.dumps(payload),
                    content_type="application/json",
                )
            finally:
                connection.close()

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "accounts.views.auth.create_user_conflict_safe",
                    side_effect=synchronize(original),
                )
            )
            stack.enter_context(
                patch(
                    "email_app.views.newsletter.create_user_conflict_safe",
                    side_effect=synchronize(original),
                )
            )
            for context_manager in patches:
                stack.enter_context(context_manager)
            with ThreadPoolExecutor(max_workers=len(requests)) as pool:
                futures = [pool.submit(post, url, payload) for url, payload in requests]
                return [future.result(timeout=15) for future in futures]

    def test_overlapping_register_requests_create_one_user(self):
        email = "double-register@example.com"
        responses = self._run_requests(
            [
                (REGISTER_URL, {"email": email, "password": "secure-one-1234"}),
                (REGISTER_URL, {"email": email, "password": "secure-two-1234"}),
            ],
            [
                patch("accounts.views.auth._send_verification_email"),
                patch("accounts.views.auth._probe_slack_membership_on_signup"),
            ],
        )

        self.assertEqual(sorted(response.status_code for response in responses), [201, 400])
        self.assertEqual(User.objects.filter(email__iexact=email).count(), 1)
        user = User.objects.get(email=email)
        self.assertTrue(user.has_usable_password())
        self.assertEqual(user.signup_source, SIGNUP_SOURCE_SIGNUP)

    def test_overlapping_subscribe_requests_create_one_user(self):
        email = "double-subscribe@example.com"
        responses = self._run_requests(
            [
                (SUBSCRIBE_URL, {"email": email}),
                (SUBSCRIBE_URL, {"email": email}),
            ],
            [patch("email_app.views.newsletter._send_subscribe_verification_email")],
        )

        for response in responses:
            self.assertContains(response, SUBSCRIBE_SUCCESS, status_code=200)
        self.assertEqual(User.objects.filter(email__iexact=email).count(), 1)
        user = User.objects.get(email=email)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.signup_source, SIGNUP_SOURCE_NEWSLETTER)

    def test_overlapping_register_and_subscribe_keep_winner_fields(self):
        email = "mixed-race@example.com"
        responses = self._run_requests(
            [
                (REGISTER_URL, {"email": email, "password": "secure-mixed-1234"}),
                (SUBSCRIBE_URL, {"email": email}),
            ],
            [
                patch("accounts.views.auth._send_verification_email"),
                patch("accounts.views.auth._probe_slack_membership_on_signup"),
                patch("email_app.views.newsletter._send_subscribe_verification_email"),
            ],
        )

        register_response, subscribe_response = responses
        self.assertIn(register_response.status_code, [201, 400])
        self.assertContains(subscribe_response, SUBSCRIBE_SUCCESS, status_code=200)
        self.assertEqual(User.objects.filter(email__iexact=email).count(), 1)
        user = User.objects.get(email=email)
        if register_response.status_code == 201:
            self.assertEqual(user.signup_source, SIGNUP_SOURCE_SIGNUP)
            self.assertTrue(user.check_password("secure-mixed-1234"))
        else:
            self.assertEqual(user.signup_source, SIGNUP_SOURCE_NEWSLETTER)
            self.assertFalse(user.has_usable_password())
