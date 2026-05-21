"""Dispatch tests for the per-flow verification email split (issue #767).

The signup view must dispatch ``email_verification_signup`` and the
newsletter-subscribe view must dispatch ``email_verification_subscribe``.
Verified by capturing the ``EmailService.send`` call (template_name is
the second positional arg).
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
    @patch("email_app.services.email_service.EmailService.send")
    def test_register_api_dispatches_email_verification_signup(
        self, mock_send, _probe,
    ):
        # Sentinel that mirrors a successful send (EmailLog row).
        from email_app.models import EmailLog

        def _fake_send(user, template_name, context=None):
            return EmailLog.objects.create(
                user=user,
                email_type=template_name,
                ses_message_id="ses-test-signup",
            )

        mock_send.side_effect = _fake_send

        resp = self.client.post(
            "/api/register",
            data=json.dumps(
                {"email": "signup-dispatch@example.com", "password": "secure1234"},
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)

        slugs = _captured_template_names(mock_send)
        self.assertIn("email_verification_signup", slugs)
        # The legacy slug must not be used anywhere on the signup path.
        self.assertNotIn("email_verification", slugs)
        # And the signup path must not accidentally pick the subscribe slug.
        self.assertNotIn("email_verification_subscribe", slugs)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class SubscribePathDispatchesSubscribeVerificationTemplate(TestCase):
    """``POST /api/subscribe`` must dispatch the subscribe-flow template."""

    @patch("email_app.services.email_service.EmailService.send")
    def test_subscribe_api_dispatches_email_verification_subscribe(
        self, mock_send,
    ):
        from email_app.models import EmailLog

        def _fake_send(user, template_name, context=None):
            return EmailLog.objects.create(
                user=user,
                email_type=template_name,
                ses_message_id="ses-test-subscribe",
            )

        mock_send.side_effect = _fake_send

        resp = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "subscribe-dispatch@example.com"}),
            content_type="application/json",
        )
        # The subscribe endpoint returns 200 on success.
        self.assertEqual(resp.status_code, 200)

        slugs = _captured_template_names(mock_send)
        self.assertIn("email_verification_subscribe", slugs)
        # The legacy slug must not be used on the subscribe path.
        self.assertNotIn("email_verification", slugs)
        # And the subscribe path must not accidentally pick the signup slug.
        self.assertNotIn("email_verification_signup", slugs)

    @patch("email_app.services.email_service.EmailService.send")
    def test_subscribe_creates_emaillog_row_with_subscribe_slug(
        self, mock_send,
    ):
        # Pair test that asserts at the persistence layer too — the
        # EmailLog row is the source of truth the reminder cron later
        # reads to pick the per-flow reminder template.
        from email_app.models import EmailLog

        def _fake_send(user, template_name, context=None):
            return EmailLog.objects.create(
                user=user,
                email_type=template_name,
                ses_message_id="ses-test-subscribe-log",
            )

        mock_send.side_effect = _fake_send

        resp = self.client.post(
            "/api/subscribe",
            data=json.dumps({"email": "subscribe-log@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        user = User.objects.get(email="subscribe-log@example.com")
        self.assertTrue(
            EmailLog.objects.filter(
                user=user,
                email_type="email_verification_subscribe",
            ).exists()
        )
        self.assertFalse(
            EmailLog.objects.filter(
                user=user,
                email_type__in=[
                    "email_verification",
                    "email_verification_signup",
                ],
            ).exists()
        )
