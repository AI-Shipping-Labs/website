"""Tests for community.services.staff_notifications (issue #703).

Covers the helper that fires three independent best-effort sends on
every paid checkout (Basic+):

- (A) Personalised co-founder welcome to the new user, addressed To the
  member, CC'ing the configured staff mailbox (issue #977; reverts the
  #950 BCC decision, which had reverted the original #703 CC).
- (B1) Structured internal heads-up email to staff.
- (B2) Plain mrkdwn Slack post to the staff channel.

The helper is invoked from ``handle_checkout_completed``; the tests
exercise both the helper directly (unit-style) and the end-to-end path
through the webhook handler.
"""

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

        # Issue #977: welcome called with cc=staff email (not bcc).
        mock_welcome.assert_called_once()
        welcome_kwargs = mock_welcome.call_args.kwargs
        self.assertEqual(welcome_kwargs.get("cc"), self.STAFF_EMAIL)
        self.assertNotIn("bcc", welcome_kwargs)

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

        # Issue #847: a Basic (level 10) checkout routes to the
        # basic_welcome template, not the Main cofounder_welcome.
        welcome_logs = EmailLog.objects.filter(
            email_type="basic_welcome",
        )
        self.assertEqual(welcome_logs.count(), 1)
        self.assertEqual(welcome_logs.first().user, user)
        self.assertEqual(
            EmailLog.objects.filter(email_type="cofounder_welcome").count(),
            0,
        )

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

    def test_webhook_threads_real_amount_and_ids_into_notification(self):
        """Issue #952: the handler passes the Checkout Session's
        ``amount_total`` / ``currency`` / ``payment_intent`` (already
        loaded — no live Stripe call) straight into ``notify_paid_signup``.
        """
        user = User.objects.create_user(email="thread@test.com")

        session = self._basic_session(user)
        session.update({
            "amount_total": 2000,
            "currency": "eur",
            "payment_intent": "pi_THREAD",
        })

        cfg = self._cfg_full()
        with patch(
            "payments.services.get_config",
            side_effect=cfg,
        ), patch(
            "community.services.staff_notifications.notify_paid_signup",
        ) as mock_notify, patch(
            "payments.services._get_stripe_client",
            side_effect=AssertionError("live Stripe call in webnook path"),
        ):
            handle_checkout_completed(session)

        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        self.assertEqual(kwargs["amount_total_minor"], 2000)
        self.assertEqual(kwargs["currency"], "eur")
        self.assertEqual(kwargs["payment_intent_id"], "pi_THREAD")

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
        # Issue #847: a Basic checkout routes to basic_welcome, and the
        # replay must not produce a second welcome of ANY tier.
        from email_app.models import EmailLog
        self.assertEqual(
            EmailLog.objects.filter(email_type="basic_welcome").count(),
            1,
        )
        self.assertEqual(
            EmailLog.objects.filter(
                email_type__in=[
                    "cofounder_welcome", "premium_welcome",
                ],
            ).count(),
            0,
        )


@tag('core')
class CofounderWelcomeTemplateContextTest(TestCase):
    """The welcome template renders user_first_name + sprint paragraph."""

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.basic_tier.price_eur_month = 20
        cls.basic_tier.save(update_fields=["price_eur_month"])

    def _render_welcome(self, user, sprint_paragraph):
        """Render the cofounder_welcome body for ``user`` with the given
        sprint paragraph, returning the HTML body.
        """
        from email_app.services import EmailService

        service = EmailService()
        _, body_html, _ = service._render_template_with_footer(
            "cofounder_welcome",
            user,
            {
                "user_first_name": user.first_name,
                "current_sprint_status_paragraph": sprint_paragraph,
            },
        )
        return body_html

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

    def test_welcome_renders_first_name_in_greeting(self):
        """Scenario: greeting reads ``Hey Sam,`` when first_name present."""
        user = User.objects.create_user(email="sam@test.com", first_name="Sam")
        body = self._render_welcome(user, "")
        self.assertIn("Hey Sam,", body)

    def test_welcome_falls_back_to_there_when_first_name_empty(self):
        """When first_name is empty the markdown template's ``|default:"there"``
        substitutes the placeholder. We render the template to confirm
        the placeholder lands in the output body, and both founders are
        still named.
        """
        user = User.objects.create_user(email="noname@test.com", first_name="")
        body = self._render_welcome(user, "")

        self.assertIn("Hey there,", body)
        # Both founders named even without a first name.
        self.assertIn("Alexey", body)
        self.assertIn("Valeriia", body)

    def test_welcome_opening_and_signoff_name_both_founders(self):
        """The opening and sign-off name both founders, spelled exactly,
        and the solo-founder phrasing is gone.
        """
        user = User.objects.create_user(email="both@test.com", first_name="Sam")
        body = self._render_welcome(user, "")

        self.assertIn("Alexey", body)
        self.assertIn("Valeriia", body)
        # Solo opening / cc phrasing removed.
        self.assertNotIn("I'm Valeriia, one of the co-founders", body)
        self.assertNotIn("cc'd Alexey", body)
        # Solo signature removed.
        self.assertNotIn("Valeriia Kuka", body)

    def test_welcome_links_onboarding_form_as_primary_cta(self):
        """The onboarding-form CTA links ``/onboarding/`` and precedes the
        short-call CTA; the body does not lead with the call.
        """
        user = User.objects.create_user(email="cta@test.com", first_name="Sam")
        body = self._render_welcome(user, "")

        self.assertIn("/onboarding/", body)
        # The absolute onboarding URL ends in /onboarding/.
        self.assertRegex(body, r"https?://[^\s\"'<]+/onboarding/")

        form_idx = body.find("/onboarding/")
        call_idx = body.find("short call")
        self.assertNotEqual(form_idx, -1)
        self.assertNotEqual(call_idx, -1)
        # Form CTA appears before the call CTA.
        self.assertLess(form_idx, call_idx)
        # No longer leads with the call.
        self.assertNotIn("Would you be open to a short call", body)

    def test_welcome_retains_personalized_plan_framing(self):
        """The personalized-plan framing survives the rework."""
        user = User.objects.create_user(email="plan@test.com", first_name="Sam")
        body = self._render_welcome(user, "")

        self.assertIn("personalized plan", body)

    def test_sprint_paragraph_is_always_empty_and_never_dated(self):
        """Issue #950: the injected sprint paragraph is now always empty —
        no dated, specific-sprint sentence is ever produced, even when an
        active sprint exists, so the welcome copy cannot go stale.
        """
        from datetime import timedelta

        from django.utils import timezone

        from community.services.staff_notifications import (
            _current_sprint_paragraph,
        )

        # No sprints -> empty.
        self.assertEqual(_current_sprint_paragraph(), "")

        # A currently-running sprint must STILL produce no injected
        # sentence — the evergreen copy lives in the template, not here.
        start = timezone.localdate() - timedelta(days=3)
        Sprint.objects.create(
            name="Spring 2026",
            slug="spring-2026",
            start_date=start,
            duration_weeks=4,
            status="active",
        )
        self.assertEqual(_current_sprint_paragraph(), "")

    def test_welcome_is_evergreen_links_sprints_and_has_no_month_literal(self):
        """The rendered welcome links the public ``/sprints`` page, says we
        regularly run community sprints, and contains NO month name or
        ISO date literal so it can never name a stale/finished sprint.
        """

        user = User.objects.create_user(email="evergreen@test.com", first_name="Sam")
        # Render with the production paragraph value (always "" now).
        from community.services.staff_notifications import (
            _current_sprint_paragraph,
        )

        body = self._render_welcome(user, _current_sprint_paragraph())

        # Evergreen "we run sprints" framing + public sprints link.
        self.assertIn("community sprints", body.lower())
        self.assertRegex(body, r"https?://[^\s\"'<]+/sprints")

        # No month-name literal anywhere (case-insensitive, word-bounded).
        months = (
            "January February March April May June July August "
            "September October November December"
        ).split()
        for month in months:
            self.assertNotRegex(
                body,
                rf"\b{month}\b",
                f"welcome leaked a month literal: {month}",
            )
        # No ISO date literal (YYYY-MM-DD) either.
        self.assertNotRegex(
            body,
            r"\b\d{4}-\d{2}-\d{2}\b",
            "welcome leaked an ISO date literal",
        )
        # No leftover running-sprint artefact.
        self.assertNotIn("currently running", body)

    def test_welcome_finish_onboarding_cta_promises_a_call(self):
        """The CTA tells the member to finish onboarding and frames the
        post-onboarding call (the actual booking links land in #951).
        """
        user = User.objects.create_user(email="cta951@test.com", first_name="Sam")
        body = self._render_welcome(user, "")

        # Finish-onboarding framing + the post-onboarding call promise.
        self.assertRegex(body, r"https?://[^\s\"'<]+/onboarding/")
        onboarding_idx = body.lower().find("onboarding")
        call_idx = body.lower().find("call")
        self.assertNotEqual(onboarding_idx, -1)
        self.assertNotEqual(call_idx, -1)
        self.assertIn("finish", body.lower())


@tag('core')
class TierWelcomeRoutingTest(TestCase):
    """Issue #847: the welcome template is chosen by the purchased tier.

    Each paid tier gets exactly its own template, routed on
    ``tier.level``: Basic (10) -> ``basic_welcome``, Main (20) ->
    ``cofounder_welcome``, Premium (30) -> ``premium_welcome``. No
    double-send: exactly one welcome of the matching slug, zero of the
    other two.
    """

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.main_tier = Tier.objects.get(slug="main")
        cls.premium_tier = Tier.objects.get(slug="premium")

    def _send_for(self, tier, email):
        """Drive ``notify_paid_signup`` for ``tier`` with all staff/Slack
        paths off, capturing the slug passed to ``EmailService.send`` for
        the user-facing welcome (the first send call).
        """
        from community.services import staff_notifications

        user = User.objects.create_user(email=email, first_name="Dana")
        with patch(
            "community.services.staff_notifications.get_config",
            return_value="",
        ), patch(
            "email_app.services.email_service.EmailService.send"
        ) as mock_send:
            staff_notifications.notify_paid_signup(
                user=user,
                tier=tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_route",
                session_id="cs_route",
                billing_period="monthly",
            )
        # First send is always the user-facing welcome.
        welcome_call = mock_send.call_args_list[0]
        return welcome_call.args[1], welcome_call.args[2]

    def test_basic_routes_to_basic_welcome(self):
        slug, _ = self._send_for(self.basic_tier, "basicroute@test.com")
        self.assertEqual(slug, "basic_welcome")

    def test_main_routes_to_cofounder_welcome(self):
        slug, _ = self._send_for(self.main_tier, "mainroute@test.com")
        self.assertEqual(slug, "cofounder_welcome")

    def test_premium_routes_to_premium_welcome(self):
        slug, _ = self._send_for(self.premium_tier, "premiumroute@test.com")
        self.assertEqual(slug, "premium_welcome")

    def test_unexpected_paid_level_falls_back_to_main_and_warns(self):
        """A paid tier whose level is not 10/20/30 falls back to the Main
        ``cofounder_welcome`` template and logs a warning — the welcome is
        never silently dropped.
        """
        from types import SimpleNamespace

        from community.services import staff_notifications

        odd_tier = SimpleNamespace(slug="legacy-paid", name="Legacy", level=15)
        user = User.objects.create_user(email="odd@test.com", first_name="Odd")

        with patch(
            "community.services.staff_notifications.get_config",
            return_value="",
        ), patch(
            "email_app.services.email_service.EmailService.send"
        ) as mock_send, patch(
            "community.services.staff_notifications.logger"
        ) as mock_logger:
            staff_notifications.notify_paid_signup(
                user=user,
                tier=odd_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_odd",
                session_id="cs_odd",
                billing_period="monthly",
            )

        welcome_call = mock_send.call_args_list[0]
        self.assertEqual(welcome_call.args[1], "cofounder_welcome")
        # Exactly one welcome was sent — never dropped.
        self.assertEqual(mock_send.call_count, 1)
        mock_logger.warning.assert_called()


@tag('core')
class TierWelcomeRenderTest(TestCase):
    """Issue #847: the rendered Basic/Premium bodies carry the right copy."""

    def _render(self, template_slug, user):
        from email_app.services import EmailService

        service = EmailService()
        _, body_html, _ = service._render_template_with_footer(
            template_slug,
            user,
            {
                "user_first_name": user.first_name,
                "current_sprint_status_paragraph": "",
            },
        )
        return body_html

    def test_basic_body_mentions_writeups_newsletter_and_onboarding(self):
        user = User.objects.create_user(email="b1@test.com", first_name="Sam")
        body = self._render("basic_welcome", user)

        self.assertIn("Hey Sam,", body)
        self.assertIn("write-ups", body)
        self.assertIn("newsletter", body)
        # Onboarding CTA links the live form.
        self.assertRegex(body, r"https?://[^\s\"'<]+/onboarding/")
        # Call offer present.
        self.assertIn("short call", body)
        self.assertIn("Welcome aboard!", body)

    def test_basic_body_states_slack_is_not_included_and_no_sprint(self):
        """Basic must not promise Slack/community as a benefit and must
        not carry sprint / personalized-plan language. It may say Slack is
        NOT included.
        """
        user = User.objects.create_user(email="b2@test.com", first_name="Sam")
        body = self._render("basic_welcome", user)

        # It explicitly tells the member Slack is NOT part of Basic.
        self.assertIn("does not include access to our community", body)
        # No sprint / personalized-plan-in-sprint language leaked in.
        self.assertNotIn("sprint", body.lower())
        self.assertNotIn("personalized plan", body)

    def test_basic_body_falls_back_to_there_without_first_name(self):
        user = User.objects.create_user(email="b3@test.com", first_name="")
        body = self._render("basic_welcome", user)
        self.assertIn("Hey there,", body)

    def test_premium_body_mentions_courses_honesty_and_onboarding(self):
        user = User.objects.create_user(email="p1@test.com", first_name="Sam")
        body = self._render("premium_welcome", user)

        self.assertIn("Hey Sam,", body)
        # Courses are part of Premium and honestly flagged as not-yet-live.
        self.assertIn("courses", body.lower())
        self.assertIn("aren't any on the platform yet", body)
        # Asks what course the member wants.
        self.assertIn("what would you most want a course on", body.lower())
        # Onboarding CTA + call offer.
        self.assertRegex(body, r"https?://[^\s\"'<]+/onboarding/")
        self.assertIn("short call", body)
        self.assertIn("Welcome aboard!", body)


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
                "STRIPE_DASHBOARD_ACCOUNT_ID": "acct_FIELDS",
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

        # Slack body carries the same fields. Interval renders as the
        # human label (issue #952), not the raw billing_period token.
        slack_text = mock_slack.call_args.kwargs["json"]["text"]
        self.assertIn("fields@test.com", slack_text)
        self.assertIn("basic", slack_text.lower())
        self.assertIn("Monthly", slack_text)
        self.assertIn("free", slack_text)  # previous tier slug
        self.assertIn("google", slack_text)
        self.assertIn("ai_eng_jan", slack_text)
        self.assertIn("cus_FIELDS", slack_text)  # in the account-scoped link
        self.assertIn(f"/studio/users/{user.pk}/", slack_text)

    def test_rendered_email_body_has_links_amount_and_activity(self):
        """Issue #952: render the staff template and assert the real
        amount, account-scoped deep-links, inline activity, and Studio
        timeline link all land in the HTML body.
        """
        from datetime import timedelta

        from django.utils import timezone

        from analytics.models import UserActivity
        from email_app.services import EmailService

        user = User.objects.create_user(email="body@test.com")
        UserActivity.objects.create(
            user=user,
            event_type=UserActivity.EVENT_LESSON_OPEN,
            occurred_at=timezone.now() - timedelta(minutes=1),
            label="Opened lesson Intro",
        )

        from community.services import staff_notifications

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=lambda key, default="": {
                "SITE_BASE_URL": "https://example.test",
                "STRIPE_DASHBOARD_ACCOUNT_ID": "acct_BODY",
            }.get(key, default),
        ):
            ctx = staff_notifications._build_signup_context(
                user=user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_BODY",
                session_id="cs_BODY",
                billing_period="monthly",
                amount_total_minor=2000,
                currency="eur",
                payment_intent_id="pi_BODY",
                subscription_id="sub_BODY",
            )

        service = EmailService()
        recipient = User.objects.create_user(email="staff-body@test.com")
        _, body_html, _ = service._render_template_with_footer(
            "staff_signup_notification", recipient, ctx,
        )

        self.assertIn("€20.00", body_html)
        self.assertIn(
            "https://dashboard.stripe.com/acct_BODY/customers/cus_BODY", body_html,
        )
        self.assertIn(
            "https://dashboard.stripe.com/acct_BODY/payments/pi_BODY", body_html,
        )
        self.assertIn(
            "https://dashboard.stripe.com/acct_BODY/subscriptions/sub_BODY",
            body_html,
        )
        self.assertIn("Opened lesson Intro", body_html)
        self.assertIn(f"/studio/users/{user.pk}/", body_html)

    def test_rendered_email_body_plain_ids_when_account_blank(self):
        """Blank ``STRIPE_DASHBOARD_ACCOUNT_ID`` -> ids render as plain
        text (no ``dashboard.stripe.com`` link) and the empty activity
        line shows.
        """
        from analytics.models import UserActivity
        from community.services import staff_notifications
        from email_app.services import EmailService

        user = User.objects.create_user(email="plain@test.com")
        # Force the empty-activity state for the inline summary assertion.
        UserActivity.objects.filter(user=user).delete()

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=lambda key, default="": {
                "SITE_BASE_URL": "https://example.test",
                "STRIPE_DASHBOARD_ACCOUNT_ID": "",
            }.get(key, default),
        ):
            ctx = staff_notifications._build_signup_context(
                user=user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_PLAIN",
                session_id="cs_PLAIN",
                billing_period="monthly",
                amount_total_minor=2000,
                currency="eur",
                payment_intent_id="pi_PLAIN",
                subscription_id="sub_PLAIN",
            )

        service = EmailService()
        recipient = User.objects.create_user(email="staff-plain@test.com")
        _, body_html, _ = service._render_template_with_footer(
            "staff_signup_notification", recipient, ctx,
        )

        self.assertNotIn("dashboard.stripe.com", body_html)
        self.assertIn("cus_PLAIN", body_html)
        self.assertIn("pi_PLAIN", body_html)
        self.assertIn("sub_PLAIN", body_html)
        self.assertIn("No recorded activity yet", body_html)

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

    def test_send_bcc_string_lands_in_ses_payload(self):
        """Issue #950: a bcc kwarg threads through send() to _send_ses,
        and cc is left untouched so the two paths stay independent.
        """
        from email_app.services import EmailService

        user = User.objects.create_user(email="bcc-recipient@test.com")
        service = EmailService()
        with patch.object(service, "_send_ses", return_value="ses-bcc-1") as mock_ses:
            service.send(
                user,
                "cofounder_welcome",
                {
                    "user_first_name": "",
                    "current_sprint_status_paragraph": "",
                },
                bcc="staff@test.com",
            )
        mock_ses.assert_called_once()
        self.assertEqual(mock_ses.call_args.kwargs.get("bcc"), "staff@test.com")
        self.assertIsNone(mock_ses.call_args.kwargs.get("cc"))


@tag('core')
class PaidSignupRealAmountAndLinksTest(TestCase):
    """Issue #952: real charged amount + interval, account-scoped Stripe
    dashboard deep-links, and inline pre-upgrade activity in the staff
    heads-up email — all without a live Stripe round-trip.
    """

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.basic_tier.price_eur_month = 20
        cls.basic_tier.price_eur_year = 200
        cls.basic_tier.save(update_fields=["price_eur_month", "price_eur_year"])

    def setUp(self):
        self.user = User.objects.create_user(email="real@test.com")

    def _build_ctx(self, **kwargs):
        """Call ``_build_signup_context`` with a patched get_config.

        ``account_id`` controls ``STRIPE_DASHBOARD_ACCOUNT_ID``;
        everything else is forwarded to the context builder. The Stripe
        client is patched to a sentinel that raises if touched, so any
        test that accidentally triggers a live call fails loudly.
        """
        from community.services import staff_notifications

        account_id = kwargs.pop("account_id", "")
        defaults = dict(
            user=self.user,
            tier=self.basic_tier,
            previous_tier=None,
            was_new_user=True,
            stripe_customer_id="cus_X",
            session_id="cs_X",
            billing_period="monthly",
        )
        defaults.update(kwargs)

        def _cfg(key, default=""):
            if key == "STRIPE_DASHBOARD_ACCOUNT_ID":
                return account_id
            if key == "SITE_BASE_URL":
                return "https://example.test"
            return default

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg,
        ), patch(
            "payments.services._get_stripe_client",
            side_effect=AssertionError("live Stripe call in notification path"),
        ):
            return staff_notifications._build_signup_context(**defaults)

    # -- Real charged amount ------------------------------------------
    def test_real_amount_from_session_overrides_tier_price(self):
        ctx = self._build_ctx(
            amount_total_minor=2000, currency="eur", billing_period="monthly",
        )
        self.assertEqual(ctx["amount_label"], "€20.00")

    def test_real_amount_yearly_renders_interval_yearly(self):
        ctx = self._build_ctx(
            amount_total_minor=20000, currency="eur", billing_period="yearly",
        )
        self.assertEqual(ctx["amount_label"], "€200.00")
        self.assertEqual(ctx["interval_label"], "Yearly")

    def test_real_amount_non_eur_currency_renders_code(self):
        ctx = self._build_ctx(amount_total_minor=2500, currency="usd")
        self.assertEqual(ctx["amount_label"], "$25.00")

    def test_missing_amount_falls_back_to_tier_price(self):
        # No amount_total -> fall back to the tier price for the period.
        ctx = self._build_ctx(amount_total_minor=None, billing_period="monthly")
        self.assertEqual(ctx["amount_label"], "€20 (monthly)")

    def test_missing_amount_and_tier_price_renders_pending_not_unknown(self):
        # Strip both prices off a tier and pass no real amount: the final
        # fallback is the literal pending text, NEVER "unknown".
        self.basic_tier.price_eur_month = None
        self.basic_tier.price_eur_year = None
        try:
            ctx = self._build_ctx(amount_total_minor=None, billing_period="monthly")
        finally:
            self.basic_tier.price_eur_month = 20
            self.basic_tier.price_eur_year = 200
        self.assertEqual(ctx["amount_label"], "Amount pending — see Stripe")
        self.assertNotIn("unknown", ctx["amount_label"].lower())

    # -- Interval ------------------------------------------------------
    def test_one_time_blank_billing_period_omits_interval(self):
        ctx = self._build_ctx(
            amount_total_minor=4900, currency="eur", billing_period="",
        )
        # One-time: interval is empty so the template omits the line.
        self.assertEqual(ctx["interval_label"], "")

    def test_interval_fallback_lookup_used_when_billing_period_empty(self):
        """When billing_period is empty but a subscription exists, the
        OPTIONAL Stripe interval lookup fills in the interval. The lookup
        itself is mocked — no live Stripe call leaks through.
        """
        from community.services import staff_notifications

        def _cfg(key, default=""):
            return default

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg,
        ), patch(
            "payments.services._get_subscription_interval",
            return_value="year",
        ) as mock_interval:
            ctx = staff_notifications._build_signup_context(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_X",
                session_id="cs_X",
                billing_period="",
                subscription_id="sub_FALLBACK",
            )
        mock_interval.assert_called_once_with("sub_FALLBACK")
        self.assertEqual(ctx["interval_label"], "Yearly")

    def test_interval_fallback_raises_omits_interval_and_still_builds(self):
        """If the optional interval lookup raises, the interval is omitted
        and the context still builds (the email still sends).
        """
        import stripe

        from community.services import staff_notifications

        def _cfg(key, default=""):
            return default

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg,
        ), patch(
            "payments.services._get_subscription_interval",
            side_effect=stripe.StripeError("boom"),
        ):
            ctx = staff_notifications._build_signup_context(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_X",
                session_id="cs_X",
                billing_period="",
                subscription_id="sub_RAISES",
            )
        self.assertEqual(ctx["interval_label"], "")

    def test_common_path_with_billing_period_skips_interval_lookup(self):
        """When billing_period is set, the optional Stripe lookup is NEVER
        called — proving no added round-trip on the common webhook path.
        """
        from community.services import staff_notifications

        def _cfg(key, default=""):
            return default

        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg,
        ), patch(
            "payments.services._get_subscription_interval",
            side_effect=AssertionError("interval lookup on common path"),
        ):
            ctx = staff_notifications._build_signup_context(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_X",
                session_id="cs_X",
                billing_period="monthly",
                subscription_id="sub_COMMON",
            )
        self.assertEqual(ctx["interval_label"], "Monthly")

    # -- Account-scoped dashboard links --------------------------------
    def test_dashboard_links_account_scoped_when_account_id_set(self):
        ctx = self._build_ctx(
            account_id="acct_LIVE",
            stripe_customer_id="cus_ABC",
            payment_intent_id="pi_ABC",
            subscription_id="sub_ABC",
        )
        self.assertEqual(
            ctx["stripe_customer_url"],
            "https://dashboard.stripe.com/acct_LIVE/customers/cus_ABC",
        )
        self.assertEqual(
            ctx["stripe_payment_url"],
            "https://dashboard.stripe.com/acct_LIVE/payments/pi_ABC",
        )
        self.assertEqual(
            ctx["stripe_subscription_url"],
            "https://dashboard.stripe.com/acct_LIVE/subscriptions/sub_ABC",
        )

    def test_dashboard_links_blank_when_account_id_missing(self):
        ctx = self._build_ctx(
            account_id="",
            stripe_customer_id="cus_ABC",
            payment_intent_id="pi_ABC",
            subscription_id="sub_ABC",
        )
        # Blank account id -> no URL; the id stays available as plain text.
        self.assertEqual(ctx["stripe_customer_url"], "")
        self.assertEqual(ctx["stripe_payment_url"], "")
        self.assertEqual(ctx["stripe_subscription_url"], "")
        self.assertEqual(ctx["stripe_customer_id"], "cus_ABC")
        self.assertEqual(ctx["stripe_payment_intent_id"], "pi_ABC")
        self.assertEqual(ctx["stripe_subscription_id"], "sub_ABC")

    # -- Inline pre-upgrade activity -----------------------------------
    def test_recent_activity_lists_last_five_newest_first(self):
        from datetime import timedelta

        from django.utils import timezone

        from analytics.models import UserActivity

        # Drop any auto-created rows (e.g. a signup activity from
        # create_user) so the window is exactly the rows we control.
        UserActivity.objects.filter(user=self.user).delete()

        base = timezone.now()
        # Create 6 rows; only the newest 5 should appear, newest-first.
        for i in range(6):
            UserActivity.objects.create(
                user=self.user,
                event_type=UserActivity.EVENT_LESSON_OPEN,
                occurred_at=base - timedelta(minutes=i),
                label=f"Lesson {i}",
            )

        ctx = self._build_ctx()
        lines = ctx["recent_activity_lines"]
        self.assertEqual(len(lines), 5)
        # Newest first: Lesson 0 before Lesson 4; Lesson 5 excluded.
        self.assertIn("Lesson 0", lines[0])
        self.assertIn("Lesson 4", lines[4])
        self.assertFalse(any("Lesson 5" in line for line in lines))

    def test_recent_activity_empty_state_line(self):
        from analytics.models import UserActivity
        # Remove the auto-created signup activity row so the user genuinely
        # has no recorded activity.
        UserActivity.objects.filter(user=self.user).delete()
        ctx = self._build_ctx()
        self.assertEqual(ctx["recent_activity_lines"], ["No recorded activity yet"])

    # -- No live Stripe call -------------------------------------------
    def test_build_context_never_calls_stripe(self):
        # _build_ctx already patches _get_stripe_client to raise; a clean
        # build proves the common path makes no live Stripe call.
        ctx = self._build_ctx(
            amount_total_minor=2000, currency="eur",
            payment_intent_id="pi_NOCALL", subscription_id="sub_NOCALL",
            account_id="acct_NOCALL",
        )
        self.assertEqual(ctx["amount_label"], "€20.00")
        self.assertNotIn("unknown", ctx["amount_label"].lower())


@tag('core')
@override_settings(SES_ENABLED=True)
class PaidWelcomeSESDestinationTest(TestCase):
    """Issue #977: the paid-signup welcome is addressed To the member with
    the staff mailbox on CC (a visible copy), never To team@ and never on
    BCC; From and Reply-To resolve to ``welcome@`` for the welcome types.

    Drives the real ``notify_paid_signup`` / ``_send_cofounder_welcome`` ->
    ``EmailService`` path with a mocked SES (``boto3``) client so we can
    assert on the SES ``Destination`` / ``FromEmailAddress`` /
    ``ReplyToAddresses`` the service actually builds. Config is set through
    real ``IntegrationSetting`` rows so the helper AND the email
    classification layer resolve from one consistent source.
    """

    STAFF_EMAIL = "team@aishippinglabs.com"
    WELCOME_FROM = "welcome@aishippinglabs.com"

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")

    def setUp(self):
        from integrations.config import clear_config_cache

        clear_config_cache()
        self.user = User.objects.create_user(
            email="member977@test.com",
            first_name="Mira",
        )
        self.addCleanup(clear_config_cache)

    def _set(self, key, value, group="site"):
        from integrations.models import IntegrationSetting

        IntegrationSetting.objects.update_or_create(
            key=key, defaults={"value": value, "group": group},
        )

    def _run_welcome(self):
        """Run notify_paid_signup with a mocked SES client; return the
        ``send_email`` call kwargs of the (A) member welcome send.

        Slack + the staff heads-up email also fire; we patch Slack off and
        let the staff email run through the same mocked SES client. The
        welcome send is the one whose ``Destination['ToAddresses']`` is the
        member, which is how we pick it out of the captured calls.
        """
        from unittest.mock import MagicMock

        from community.services import staff_notifications
        from integrations.config import clear_config_cache

        clear_config_cache()

        mock_client = MagicMock()
        mock_client.send_email.return_value = {"MessageId": "ses-977"}

        with patch(
            "email_app.services.email_service.boto3"
        ) as mock_boto3, patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack:
            mock_boto3.client.return_value = mock_client
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_977",
                session_id="cs_977",
                billing_period="monthly",
            )

        return [c.kwargs for c in mock_client.send_email.call_args_list]

    def _welcome_call(self, calls):
        """Pick the member welcome send (To == the member's email)."""
        for kwargs in calls:
            to = kwargs["Destination"].get("ToAddresses", [])
            if to == [self.user.email]:
                return kwargs
        self.fail(
            "No SES send was addressed To the member; calls were: "
            f"{[c['Destination'].get('ToAddresses') for c in calls]}"
        )

    # -- Scenario 1: To member, CC team@, no BCC -----------------------
    def test_welcome_to_member_with_team_on_cc_and_no_bcc(self):
        self._set("STAFF_SIGNUP_NOTIFY_EMAIL", self.STAFF_EMAIL)

        welcome = self._welcome_call(self._run_welcome())

        destination = welcome["Destination"]
        self.assertEqual(destination["ToAddresses"], [self.user.email])
        self.assertEqual(destination["CcAddresses"], [self.STAFF_EMAIL])
        self.assertNotIn("BccAddresses", destination)

    # -- Scenario 2: never addressed to team@ --------------------------
    def test_welcome_is_never_addressed_to_team(self):
        self._set("STAFF_SIGNUP_NOTIFY_EMAIL", self.STAFF_EMAIL)

        welcome = self._welcome_call(self._run_welcome())

        self.assertNotIn(self.STAFF_EMAIL, welcome["Destination"]["ToAddresses"])

    # -- Scenario 3: blank staff mailbox => no CC, no BCC --------------
    def test_no_staff_mailbox_member_gets_welcome_with_no_cc_or_bcc(self):
        self._set("STAFF_SIGNUP_NOTIFY_EMAIL", "")

        welcome = self._welcome_call(self._run_welcome())

        destination = welcome["Destination"]
        self.assertEqual(destination["ToAddresses"], [self.user.email])
        self.assertNotIn("CcAddresses", destination)
        self.assertNotIn("BccAddresses", destination)

    # -- Scenario 4: From + Reply-To resolve to welcome@ ---------------
    def test_welcome_from_and_reply_to_resolve_to_welcome(self):
        self._set("STAFF_SIGNUP_NOTIFY_EMAIL", self.STAFF_EMAIL)

        welcome = self._welcome_call(self._run_welcome())

        self.assertEqual(welcome["FromEmailAddress"], self.WELCOME_FROM)
        self.assertEqual(welcome["ReplyToAddresses"], [self.WELCOME_FROM])

    def test_welcome_from_stays_welcome_even_with_stray_legacy_from(self):
        # A stray legacy SES_FROM_EMAIL=noreply@ must NOT pull the welcome
        # From down when SES_WELCOME_FROM_EMAIL=welcome@ is pinned.
        self._set("STAFF_SIGNUP_NOTIFY_EMAIL", self.STAFF_EMAIL)
        self._set("SES_FROM_EMAIL", "noreply@aishippinglabs.com", group="ses")
        self._set("SES_WELCOME_FROM_EMAIL", self.WELCOME_FROM, group="ses")

        welcome = self._welcome_call(self._run_welcome())

        self.assertEqual(welcome["FromEmailAddress"], self.WELCOME_FROM)
        self.assertEqual(welcome["ReplyToAddresses"], [self.WELCOME_FROM])

    # -- Scenario 5: staff heads-up (B1) is its own To send ------------
    def test_staff_headsup_is_addressed_to_team_not_a_cc_of_welcome(self):
        self._set("STAFF_SIGNUP_NOTIFY_EMAIL", self.STAFF_EMAIL)

        calls = self._run_welcome()

        # The (B1) heads-up is a separate SES send addressed To team@.
        staff_sends = [
            c for c in calls
            if c["Destination"].get("ToAddresses") == [self.STAFF_EMAIL]
        ]
        self.assertEqual(
            len(staff_sends), 1,
            "Expected exactly one SES send addressed To the staff mailbox "
            "(the B1 heads-up), distinct from the member welcome's CC.",
        )
        # And that heads-up does NOT carry the welcome on CC/BCC.
        self.assertNotIn("CcAddresses", staff_sends[0]["Destination"])
        self.assertNotIn("BccAddresses", staff_sends[0]["Destination"])

    # -- Scenario 6: welcome failure does not break heads-up ----------
    def test_welcome_failure_does_not_block_staff_email_or_slack(self):
        from community.services import staff_notifications

        self._set("STAFF_SIGNUP_NOTIFY_EMAIL", self.STAFF_EMAIL)
        self._set("STAFF_SIGNUP_NOTIFY_CHANNEL_ID", "C0FAIL977", group="slack")
        self._set("SLACK_ENABLED", "true", group="slack")
        self._set("SLACK_BOT_TOKEN", "xoxb-977", group="slack")

        from integrations.config import clear_config_cache

        clear_config_cache()

        with patch.object(
            staff_notifications,
            "_send_cofounder_welcome",
            side_effect=RuntimeError("welcome SES blew up"),
        ), patch.object(
            staff_notifications,
            "_send_staff_signup_notification",
        ) as mock_staff_email, patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            # Must NOT raise — best-effort isolation (#703).
            staff_notifications.notify_paid_signup(
                user=self.user,
                tier=self.basic_tier,
                previous_tier=None,
                was_new_user=True,
                stripe_customer_id="cus_fail",
                session_id="cs_fail",
                billing_period="monthly",
            )

        mock_staff_email.assert_called_once()
        self.assertEqual(mock_staff_email.call_args.args[0], self.STAFF_EMAIL)
        mock_slack.assert_called_once()
