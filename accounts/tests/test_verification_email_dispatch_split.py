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


def _captured_template_names(mock_send):
    """Return every ``template_name`` value passed to ``EmailService.send``.

    Both the auth helper and the newsletter helper call ``send`` as
    ``service.send(user, template_name, context)``, so ``call_args.args[1]``
    is the slug. Returning a list (in order) lets the test assert the
    exact dispatch without false positives if the helper later starts
    sending multiple emails.
    """
    return [call.args[1] for call in mock_send.call_args_list]


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

    @patch("email_app.services.email_service.EmailService.send")
    def test_subscribe_api_dispatches_relay_verification(self, mock_send):
        from community_base.jobs.models import JobIntent

        resp = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "subscribe-dispatch@example.com"}),
            content_type="application/json",
        )
        # The subscribe endpoint answers with the generic ok payload.
        self.assertEqual(resp.json()["status"], "ok")

        # A6.2 step 4: the verification message moves to Relay; the site
        # records the handoff as a durable job intent.
        self.assertTrue(
            JobIntent.objects.filter(
                handler="email_app.relay_sync.request_contact_verification"
            ).exists()
        )
        # No site template is dispatched on this path anymore.
        self.assertEqual(_captured_template_names(mock_send), [])

    @patch("email_app.services.email_service.EmailService.send")
    def test_subscribe_creates_durable_verification_intent(self, mock_send):
        # Pair test that asserts at the persistence layer too — the
        # durable JobIntent is the source of truth the jobs runner later
        # drains into Relay's request-verification endpoint.
        from community_base.jobs.models import JobIntent

        resp = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "subscribe-log@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.json()["status"], "ok")

        from email_app.models import EmailLog

        user = User.objects.get(email="subscribe-log@example.com")
        intent = JobIntent.objects.get(
            handler="email_app.relay_sync.request_contact_verification"
        )
        self.assertEqual(intent.payload["user_id"], user.pk)
        # No EmailLog row: the site no longer renders or sends this mail.
        self.assertFalse(EmailLog.objects.filter(user=user).exists())
