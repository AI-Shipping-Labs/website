"""Dispatch tests for the per-flow verification email split (issue #767).

The signup view must dispatch ``email_verification_signup`` through the
package mail app. Since A6.2 the newsletter-subscribe view hands its
verification message to Relay's double opt-in flow instead of sending
the ``email_verification_subscribe`` template itself; the lead-magnet
variant is the only subscribe path that still sends site mail.
"""

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import User
from email_app.models import EmailLog


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class SignupPathDispatchesSignupVerificationTemplate(TestCase):
    """``POST /api/register`` must dispatch the signup-flow template."""

    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    def test_register_api_dispatches_email_verification_signup(self, _probe):
        # A1.2: the signup path dispatches through the package mail app,
        # so the dispatch is asserted on the durable EmailDelivery rows.
        from community_base.mail.models import EmailDelivery

        resp = self.client.post(
            "/api/register",
            data=json.dumps(
                {"email": "signup-dispatch@example.com", "password": "secure1234"},
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)

        slugs = list(EmailDelivery.objects.values_list("purpose", flat=True))
        self.assertIn("email_verification_signup", slugs)
        # The legacy slug must not be used anywhere on the signup path.
        self.assertNotIn("email_verification", slugs)
        # And the signup path must not accidentally pick the subscribe slug.
        self.assertNotIn("email_verification_subscribe", slugs)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class SubscribePathDispatchesRelayVerification(TestCase):
    """``POST /api/subscribe`` must dispatch Relay's double opt-in (A6.2)."""

    def test_subscribe_api_dispatches_relay_verification(self):
        from community_base.jobs.models import JobIntent

        resp = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "subscribe-dispatch@example.com"}),
            content_type="application/json",
        )

        self.assertEqual(resp.json()["status"], "ok")
        self.assertTrue(
            JobIntent.objects.filter(
                handler="email_app.relay_sync.request_contact_verification",
            ).exists(),
        )
        self.assertFalse(EmailLog.objects.exists())

    def test_subscribe_creates_durable_verification_intent_for_new_user(self):
        from community_base.jobs.models import JobIntent

        resp = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "subscribe-log@example.com"}),
            content_type="application/json",
        )

        self.assertEqual(resp.json()["status"], "ok")
        user = User.objects.get(email="subscribe-log@example.com")
        intent = JobIntent.objects.get(
            handler="email_app.relay_sync.request_contact_verification",
        )
        self.assertEqual(intent.payload, {"user_id": user.pk})
        self.assertFalse(EmailLog.objects.filter(user=user).exists())
