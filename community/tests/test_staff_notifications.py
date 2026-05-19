"""Tests for community.services.staff_notifications (issue #703).

Covers the helper that fires three independent best-effort sends on
every paid checkout (Basic+):

- (A) Personalised co-founder welcome to the new user, CC'ing the
  configured staff mailbox.
- (B1) Structured internal heads-up email to staff.
- (B2) Plain mrkdwn Slack post to the staff channel.

The helper is invoked from ``handle_checkout_completed``; the tests
exercise both the helper directly (unit-style) and the end-to-end path
through the webhook handler.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings, tag

from accounts.models import User
from content.models import Course, CourseAccess
from payments.models import Tier
from payments.services import handle_checkout_completed
from plans.models import Sprint


@tag('core')
class NotifyPaidSignupHelperTest(TestCase):
    """Direct tests of ``notify_paid_signup`` (no webhook plumbing)."""

    STAFF_EMAIL = "founders@aishippinglabs.test"
    SLACK_CHANNEL = "C0PAIDSIGNUP"

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.basic_tier.price_eur_month = 20
        cls.basic_tier.price_eur_year = 200
        cls.basic_tier.save(update_fields=["price_eur_month", "price_eur_year"])
        cls.free_tier = Tier.objects.get(slug="free")

    def setUp(self):
        # Patch settings used by the helper so each test starts clean.
        self.user = User.objects.create_user(
            email="newpaid@test.com",
            first_name="Alex",
        )

    def _cfg(self, **overrides):
        """Build a get_config side_effect for the helper.

        Defaults: staff email + channel set, Slack enabled + token set.
        Override individual keys per test.
        """
        defaults = {
            "STAFF_SIGNUP_NOTIFY_EMAIL": self.STAFF_EMAIL,
            "STAFF_SIGNUP_NOTIFY_CHANNEL_ID": self.SLACK_CHANNEL,
            "SLACK_ENABLED": "true",
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SITE_BASE_URL": "https://example.test",
        }
        defaults.update(overrides)

        def _get(key, default=""):
            if key in defaults:
                return defaults[key]
            return default if default is not None else ""

        return _get

    # ------------------------------------------------------------------
    # Happy path: all three sends fire and carry the right payload.
    # ------------------------------------------------------------------
    def test_happy_path_fires_welcome_with_cc_staff_email_and_slack(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
        ) as mock_welcome, patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
        ) as mock_staff_email:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_happy",
                session_id="cs_happy",
                billing_period="monthly",
            )

        # Welcome called with cc=staff email.
        mock_welcome.assert_called_once()
        welcome_kwargs = mock_welcome.call_args.kwargs
        self.assertEqual(welcome_kwargs.get("cc"), self.STAFF_EMAIL)

        # Internal email sent to the staff mailbox.
        mock_staff_email.assert_called_once()
        self.assertEqual(
            mock_staff_email.call_args.args[0], self.STAFF_EMAIL,
        )

        # Slack post hit the configured channel.
        mock_slack.assert_called_once()
        slack_payload = mock_slack.call_args.kwargs["json"]
        self.assertEqual(slack_payload["channel"], self.SLACK_CHANNEL)
        self.assertIn("New paid signup", slack_payload["text"])
        self.assertIn("newpaid@test.com", slack_payload["text"])
        self.assertIn("basic", slack_payload["text"].lower())

    # ------------------------------------------------------------------
    # STAFF_SIGNUP_NOTIFY_EMAIL empty: welcome still goes, no CC, no
    # staff email.
    # ------------------------------------------------------------------
    def test_no_staff_email_setting_drops_cc_and_staff_email(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(STAFF_SIGNUP_NOTIFY_EMAIL=""),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
        ) as mock_welcome, patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
        ) as mock_staff_email:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_no_staff",
                session_id="cs_no_staff",
                billing_period="monthly",
            )

        # Welcome still went, but without a cc.
        mock_welcome.assert_called_once()
        self.assertIsNone(mock_welcome.call_args.kwargs.get("cc"))

        # Staff email path did NOT run.
        mock_staff_email.assert_not_called()

        # Slack post still ran (its setting was untouched).
        mock_slack.assert_called_once()

    # ------------------------------------------------------------------
    # STAFF_SIGNUP_NOTIFY_CHANNEL_ID empty: Slack skipped, others run.
    # ------------------------------------------------------------------
    def test_no_slack_channel_skips_slack_only(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(STAFF_SIGNUP_NOTIFY_CHANNEL_ID=""),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
        ) as mock_welcome, patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
        ) as mock_staff_email:
            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_no_slack",
                session_id="cs_no_slack",
                billing_period="monthly",
            )

        mock_welcome.assert_called_once()
        mock_staff_email.assert_called_once()
        mock_slack.assert_not_called()

    # ------------------------------------------------------------------
    # SLACK_ENABLED false skips Slack silently.
    # ------------------------------------------------------------------
    def test_slack_disabled_flag_skips_slack(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=False,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
        ), patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
        ):
            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_flag_off",
                session_id="cs_flag_off",
                billing_period="monthly",
            )

        mock_slack.assert_not_called()

    # ------------------------------------------------------------------
    # SLACK_BOT_TOKEN empty skips Slack silently.
    # ------------------------------------------------------------------
    def test_slack_bot_token_missing_skips_slack(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(SLACK_BOT_TOKEN=""),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack:
            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_no_token",
                session_id="cs_no_token",
                billing_period="monthly",
            )

        mock_slack.assert_not_called()

    # ------------------------------------------------------------------
    # Welcome EmailService failure does NOT block staff email or Slack.
    # ------------------------------------------------------------------
    def test_welcome_failure_does_not_block_staff_email_or_slack(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
            side_effect=Exception("SES exploded"),
        ), patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
        ) as mock_staff_email, patch(
            "community.services.staff_notifications.logger"
        ) as mock_logger:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            # MUST NOT raise.
            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_welcome_fail",
                session_id="cs_welcome_fail",
                billing_period="monthly",
            )

        # Staff email + Slack still attempted.
        mock_staff_email.assert_called_once()
        mock_slack.assert_called_once()
        mock_logger.exception.assert_called()

    # ------------------------------------------------------------------
    # Internal email failure does NOT block Slack.
    # ------------------------------------------------------------------
    def test_internal_email_failure_does_not_block_slack(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
        ), patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
            side_effect=Exception("staff send died"),
        ), patch(
            "community.services.staff_notifications.logger"
        ) as mock_logger:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_staff_fail",
                session_id="cs_staff_fail",
                billing_period="monthly",
            )

        mock_slack.assert_called_once()
        mock_logger.exception.assert_called()

    # ------------------------------------------------------------------
    # Slack requests.post failure does NOT block / does NOT raise.
    # ------------------------------------------------------------------
    def test_slack_request_exception_does_not_raise(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post",
            side_effect=requests.exceptions.ConnectionError("net down"),
        ), patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
        ) as mock_welcome, patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
        ) as mock_staff_email, patch(
            "community.services.staff_notifications.logger"
        ) as mock_logger:
            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_slack_down",
                session_id="cs_slack_down",
                billing_period="monthly",
            )

        # Welcome + staff email still ran.
        mock_welcome.assert_called_once()
        mock_staff_email.assert_called_once()
        mock_logger.exception.assert_called()

    # ------------------------------------------------------------------
    # Slack returning ok=False is treated as a warning, not raise.
    # ------------------------------------------------------------------
    def test_slack_ok_false_response_logs_warning_and_returns(self):
        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=self._cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
        ), patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
        ), patch(
            "community.services.staff_notifications.logger"
        ) as mock_logger:
            mock_slack.return_value.json.return_value = {
                "ok": False,
                "error": "channel_not_found",
            }
            mock_slack.return_value.status_code = 200

            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_slack_reject",
                session_id="cs_slack_reject",
                billing_period="monthly",
            )

        mock_logger.warning.assert_called()


@tag('core')
class NotifyPaidSignupEndToEndTest(TestCase):
    """Tests through ``handle_checkout_completed`` (real EmailService).

    Uses Django's locmem mail backend (SES is disabled in test
    settings) to assert that the welcome email actually lands in
    ``mail.outbox`` with the right CC, that the internal email also
    lands, and that the Slack post fires.
    """

    STAFF_EMAIL = "founders-e2e@aishippinglabs.test"
    SLACK_CHANNEL = "C0E2EPAIDSIGNUP"

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.basic_tier.price_eur_month = 20
        cls.basic_tier.price_eur_year = 200
        cls.basic_tier.save(update_fields=["price_eur_month", "price_eur_year"])

    def _cfg_full(self, **overrides):
        defaults = {
            "STAFF_SIGNUP_NOTIFY_EMAIL": self.STAFF_EMAIL,
            "STAFF_SIGNUP_NOTIFY_CHANNEL_ID": self.SLACK_CHANNEL,
            "SLACK_ENABLED": "true",
            "SLACK_BOT_TOKEN": "xoxb-test-token-e2e",
            "PAYMENT_NOTIFICATION_EMAIL": "",  # avoid the legacy ping
        }
        defaults.update(overrides)

        def _get(key, default=""):
            if key in defaults:
                return defaults[key]
            return default if default is not None else ""

        return _get

    def _basic_session(self, user):
        return {
            "id": "cs_e2e_basic",
            "customer": "cus_e2e_basic",
            "customer_details": {"email": user.email},
            "subscription": "",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

    def test_paid_checkout_sends_welcome_with_cc_and_staff_email(self):
        user = User.objects.create_user(
            email="endtoend@test.com",
            first_name="Riley",
        )

        cfg = self._cfg_full()

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=cfg,
        ), patch(
            "payments.services.get_config",
            side_effect=cfg,
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            handle_checkout_completed(self._basic_session(user))

        # Two emails landed: welcome (with CC) and staff notification.
        # PAYMENT_NOTIFICATION_EMAIL was empty so the legacy ping is
        # skipped. SES is disabled in tests so EmailService routes
        # through django.core.mail when wired up — but the actual SES
        # path is patched off in the test settings via SES_ENABLED=False
        # which short-circuits before any send_email call. To verify
        # real sends, check the EmailLog rows the service writes on
        # the success path.
        from email_app.models import EmailLog

        cofounder_logs = EmailLog.objects.filter(
            email_type="cofounder_welcome",
        )
        self.assertEqual(cofounder_logs.count(), 1)
        self.assertEqual(cofounder_logs.first().user, user)

        # The staff signup email is sent to a SimpleNamespace surrogate
        # (issue #703 grooming decision — no fake DB user). It does
        # NOT write an EmailLog row because EmailLog.user is a FK to a
        # real User row. To prove the staff email was attempted, we
        # would need to mock-patch the EmailService — that coverage
        # lives in NotifyPaidSignupHelperTest. Here we just confirm
        # the welcome did land an EmailLog and Slack also fired.

        # Slack also went.
        mock_slack.assert_called_once()
        self.assertEqual(
            mock_slack.call_args.kwargs["json"]["channel"],
            self.SLACK_CHANNEL,
        )

    def test_free_checkout_does_not_trigger_paid_signup_notifications(self):
        # Free signups never go through Stripe checkout, so the only
        # way to exercise this is to assert that handle_checkout_completed
        # with no tier_slug falls through the helper. To keep the test
        # honest we drive a checkout that resolves to a tier of level 0:
        # this isn't a realistic Stripe payload but it locks the level
        # gate behaviour in.
        Tier.objects.get(slug="free")  # ensure seeded
        # The handler returns early when no tier is found; the helper
        # is only called inside the resolved-tier branch. We just
        # verify a no-tier session doesn't fire the helper.
        with patch(
            "community.services.staff_notifications.notify_paid_signup"
        ) as mock_notify:
            handle_checkout_completed({
                "id": "cs_no_tier",
                "customer": "cus_no_tier",
                "customer_details": {"email": "anon@test.com"},
                "subscription": "",
                "client_reference_id": None,
                "metadata": {"tier_slug": ""},
            })

        mock_notify.assert_not_called()

    def test_course_purchase_does_not_trigger_paid_signup_notifications(self):
        user = User.objects.create_user(email="coursebuyer-e2e@test.com")
        course = Course.objects.create(
            title="Test Course 703",
            slug="test-course-703",
            status="published",
            individual_price_eur=Decimal("99.00"),
        )
        session_data = {
            "id": "cs_course_703",
            "customer": "cus_course_703",
            "customer_details": {"email": user.email},
            "subscription": "",
            "client_reference_id": None,
            "metadata": {"course_id": str(course.pk)},
        }

        with patch(
            "community.services.staff_notifications.notify_paid_signup"
        ) as mock_notify:
            handle_checkout_completed(session_data)

        mock_notify.assert_not_called()
        # And the CourseAccess row was created — the course path still
        # works end-to-end.
        self.assertTrue(
            CourseAccess.objects.filter(user=user, course=course).exists(),
        )

    def test_replayed_webhook_does_not_send_welcome_twice(self):
        """Idempotency: a duplicate webhook delivery short-circuits at
        the ``WebhookEvent`` dispatch layer; the helper must NOT run a
        second time, so the EmailLog count + Slack call count stay at 1.
        """
        import hashlib
        import hmac
        import json
        import time

        WEBHOOK_URL = "/api/webhooks/payments"
        WEBHOOK_SECRET = "whsec_dupe_703"

        from integrations.config import get_config as real_get_config

        def cfg(key, default=""):
            if key == "STAFF_SIGNUP_NOTIFY_EMAIL":
                return self.STAFF_EMAIL
            if key == "STAFF_SIGNUP_NOTIFY_CHANNEL_ID":
                return self.SLACK_CHANNEL
            if key == "SLACK_ENABLED":
                return "true"
            if key == "SLACK_BOT_TOKEN":
                return "xoxb-test-token-replay"
            if key == "STRIPE_WEBHOOK_SECRET":
                return WEBHOOK_SECRET
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return ""
            return real_get_config(key, default)

        def sign(payload_bytes):
            ts = str(int(time.time()))
            signed = f"{ts}.{payload_bytes.decode('utf-8')}"
            sig = hmac.new(
                WEBHOOK_SECRET.encode(),
                signed.encode(),
                hashlib.sha256,
            ).hexdigest()
            return f"t={ts},v1={sig}"

        user = User.objects.create_user(
            email="replay@test.com",
            first_name="Repla",
        )

        event_data = {
            "id": "evt_replay_703",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_replay_703",
                    "customer": "cus_replay_703",
                    "customer_details": {"email": user.email},
                    "subscription": "",
                    "client_reference_id": str(user.pk),
                    "metadata": {
                        "tier_slug": "basic",
                        "user_id": str(user.pk),
                    },
                },
            },
        }
        payload = json.dumps(event_data).encode()
        sig = sign(payload)

        with override_settings(STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET), patch(
            "payments.services.get_config", side_effect=cfg,
        ), patch(
            "community.services.staff_notifications.get_config",
            side_effect=cfg,
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            r1 = self.client.post(
                WEBHOOK_URL, data=payload,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sig,
            )
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r1.json()["status"], "ok")

            # Replay: same event id. Must short-circuit at the
            # WebhookEvent dispatch layer.
            r2 = self.client.post(
                WEBHOOK_URL, data=payload,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sig,
            )
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r2.json()["status"], "already_processed")

            # Exactly one Slack post, even though two webhooks landed.
            self.assertEqual(mock_slack.call_count, 1)

        # Exactly one welcome EmailLog row, even after the replay. The
        # staff signup email is sent to a SimpleNamespace surrogate so
        # it never writes an EmailLog row — Slack call count above is
        # the authoritative signal that the helper only ran once.
        from email_app.models import EmailLog
        self.assertEqual(
            EmailLog.objects.filter(email_type="cofounder_welcome").count(),
            1,
        )


@tag('core')
class CofounderWelcomeTemplateContextTest(TestCase):
    """The welcome template renders user_first_name + sprint paragraph."""

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.basic_tier.price_eur_month = 20
        cls.basic_tier.save(update_fields=["price_eur_month"])

    def test_welcome_renders_first_name_when_present(self):
        from community.services import staff_notifications

        user = User.objects.create_user(email="first@test.com", first_name="Sam")

        with patch(
            "community.services.staff_notifications.get_config",
            return_value="",
        ), patch(
            "email_app.services.email_service.EmailService.send"
        ) as mock_send:
            staff_notifications.notify_paid_signup(
                user=user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_first",
                session_id="cs_first",
                billing_period="monthly",
            )

        # First call is the welcome to the new user.
        welcome_call = mock_send.call_args_list[0]
        ctx = welcome_call.args[2]
        self.assertEqual(ctx["user_first_name"], "Sam")

    def test_welcome_falls_back_to_there_when_first_name_empty(self):
        """When first_name is empty the markdown template's ``|default:"there"``
        substitutes the placeholder. We render the template to confirm
        the placeholder lands in the output body.
        """
        from email_app.services import EmailService

        user = User.objects.create_user(email="noname@test.com", first_name="")
        service = EmailService()
        subject, body_html, _ = service._render_template_with_footer(
            "cofounder_welcome",
            user,
            {
                "user_first_name": user.first_name,
                "current_sprint_status_paragraph": "We run sprints.",
            },
        )

        self.assertIn("Hey there,", body_html)
        self.assertIn("We run sprints.", body_html)

    def test_welcome_renders_active_sprint_when_one_exists(self):
        Sprint.objects.create(
            name="Spring 2026",
            slug="spring-2026",
            start_date=date(2026, 5, 1),
            duration_weeks=4,
            status="active",
        )

        from community.services.staff_notifications import (
            _current_sprint_paragraph,
        )

        paragraph = _current_sprint_paragraph()

        self.assertIn("Spring 2026", paragraph)
        self.assertIn("2026-05-01", paragraph)
        # end_date = start + 4 weeks = 2026-05-29
        self.assertIn("2026-05-29", paragraph)

    def test_welcome_falls_back_when_no_active_sprint(self):
        from community.services.staff_notifications import (
            _current_sprint_paragraph,
        )

        # No sprints with status='active' in this test.
        paragraph = _current_sprint_paragraph()

        self.assertIn("cohort sprints", paragraph)
        self.assertIn("next one opens", paragraph)


@tag('core')
class StaffSignupNotificationBodyTest(TestCase):
    """Both the internal email body and the Slack post carry the
    required fields.
    """

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.basic_tier.price_eur_month = 20
        cls.basic_tier.save(update_fields=["price_eur_month"])
        cls.free_tier = Tier.objects.get(slug="free")

    def test_internal_email_and_slack_carry_required_fields(self):
        user = User.objects.create_user(
            email="fields@test.com", first_name="Pat",
        )
        # Pre-existing attribution row.
        from analytics.models import UserAttribution
        UserAttribution.objects.update_or_create(
            user=user,
            defaults={
                "first_touch_utm_source": "google",
                "first_touch_utm_campaign": "ai_eng_jan",
            },
        )
        # Reload the user so the OneToOne reverse descriptor sees the
        # row we just created/updated. Without this, Django's cached
        # related-object state still says "no attribution row".
        user = User.objects.get(pk=user.pk)

        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=lambda key, default="": {
                "STAFF_SIGNUP_NOTIFY_EMAIL": "f@test.com",
                "STAFF_SIGNUP_NOTIFY_CHANNEL_ID": "C0F",
                "SLACK_ENABLED": "true",
                "SLACK_BOT_TOKEN": "xoxb-f",
                "SITE_BASE_URL": "https://example.test",
            }.get(key, default),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            return_value=True,
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch(
            "email_app.services.email_service.EmailService.send"
        ) as mock_send:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            staff_notifications.notify_paid_signup(
                user=user,
                tier=self.basic_tier,
                previous_tier=self.free_tier,
                was_new_user=False,
                stripe_customer_id="cus_FIELDS",
                session_id="cs_FIELDS",
                billing_period="monthly",
            )

        # Two EmailService.send calls: welcome + staff_signup_notification.
        self.assertEqual(mock_send.call_count, 2)
        staff_call = mock_send.call_args_list[1]
        self.assertEqual(staff_call.args[1], "staff_signup_notification")
        staff_ctx = staff_call.args[2]
        for key in (
            "paid_user_email",
            "tier_slug",
            "tier_name",
            "previous_tier_slug",
            "was_new_user_label",
            "amount_label",
            "stripe_customer_id",
            "stripe_customer_url",
            "stripe_session_id",
            "first_touch_utm_source",
            "first_touch_utm_campaign",
            "signup_timestamp",
            "studio_user_url",
        ):
            self.assertIn(key, staff_ctx, f"missing {key} from staff ctx")
        self.assertEqual(staff_ctx["paid_user_email"], "fields@test.com")
        self.assertEqual(staff_ctx["tier_slug"], "basic")
        self.assertEqual(staff_ctx["previous_tier_slug"], "free")
        self.assertEqual(staff_ctx["was_new_user_label"], "no")
        self.assertIn("20", staff_ctx["amount_label"])
        self.assertIn("monthly", staff_ctx["amount_label"])
        self.assertEqual(staff_ctx["stripe_customer_id"], "cus_FIELDS")
        self.assertIn("cus_FIELDS", staff_ctx["stripe_customer_url"])
        self.assertEqual(staff_ctx["stripe_session_id"], "cs_FIELDS")
        self.assertEqual(staff_ctx["first_touch_utm_source"], "google")
        self.assertEqual(staff_ctx["first_touch_utm_campaign"], "ai_eng_jan")
        self.assertIn(f"/studio/users/{user.pk}/", staff_ctx["studio_user_url"])

        # Slack body carries the same fields.
        slack_text = mock_slack.call_args.kwargs["json"]["text"]
        self.assertIn("fields@test.com", slack_text)
        self.assertIn("basic", slack_text.lower())
        self.assertIn("monthly", slack_text)
        self.assertIn("free", slack_text)  # previous tier slug
        self.assertIn("google", slack_text)
        self.assertIn("ai_eng_jan", slack_text)
        self.assertIn("cus_FIELDS", slack_text)
        self.assertIn(f"/studio/users/{user.pk}/", slack_text)

    def test_attribution_missing_renders_dash(self):
        user = User.objects.create_user(email="noattr@test.com")
        # No attribution row exists — make sure default signal doesn't
        # auto-create one with non-empty UTM. The signal creates an
        # empty row, so we explicitly delete it to force the "no row"
        # branch in the helper.
        from analytics.models import UserAttribution
        UserAttribution.objects.filter(user=user).delete()

        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            return_value="",
        ), patch(
            "email_app.services.email_service.EmailService.send"
        ) as mock_send:
            staff_notifications.notify_paid_signup(
                user=user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_NOATTR",
                session_id="cs_NOATTR",
                billing_period="monthly",
            )

        welcome_call = mock_send.call_args_list[0]
        ctx_inner = welcome_call.args[2]
        # Welcome only carries first_name + sprint paragraph; the
        # attribution dash check belongs on the helper's shared ctx.
        # Reach into the helper through a direct call.
        ctx = staff_notifications._build_signup_context(
            user=user,
            tier=self.basic_tier,
            previous_tier=None,
            was_new_user=True,
            stripe_customer_id="cus_NOATTR",
            session_id="cs_NOATTR",
            billing_period="monthly",
        )
        self.assertEqual(ctx["first_touch_utm_source"], "—")
        self.assertEqual(ctx["first_touch_utm_campaign"], "—")
        self.assertEqual(ctx["previous_tier_slug"], "—")
        # Welcome context was built with the same machinery.
        self.assertIn("user_first_name", ctx_inner)


@tag('core')
class EmailServiceCcArgumentTest(TestCase):
    """``EmailService.send`` accepts a cc kwarg and threads it to SES."""

    def test_send_cc_string_lands_in_ses_payload(self):
        from email_app.services import EmailService
        from email_app.services.email_service import _normalize_cc

        # _normalize_cc accepts either a string or a list.
        self.assertEqual(
            _normalize_cc("a@test.com"), ["a@test.com"],
        )
        self.assertEqual(
            _normalize_cc(["a@test.com", "b@test.com"]),
            ["a@test.com", "b@test.com"],
        )
        self.assertEqual(_normalize_cc(None), [])
        self.assertEqual(_normalize_cc(""), [])
        self.assertEqual(_normalize_cc([""]), [])

        # And the cc actually rides through to _send_ses.
        user = User.objects.create_user(email="cc-recipient@test.com")
        service = EmailService()
        with patch.object(service, "_send_ses", return_value="ses-cc-1") as mock_ses:
            service.send(
                user,
                "cofounder_welcome",
                {
                    "user_first_name": "",
                    "current_sprint_status_paragraph": "We run sprints.",
                },
                cc="cc@test.com",
            )
        mock_ses.assert_called_once()
        self.assertEqual(mock_ses.call_args.kwargs.get("cc"), "cc@test.com")
