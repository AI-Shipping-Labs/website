"""Tests for the payments services module.

Tests cover:
- _tier_for_price_id helper
- verify_webhook_signature
- webhook fulfillment helpers
- hard-deprecated local checkout/subscription mutation helpers
"""

from datetime import datetime, timezone
from decimal import Decimal
from smtplib import SMTPException
from unittest.mock import MagicMock, patch

import stripe
from django.core import mail
from django.test import TestCase, tag

from accounts.models import User
from content.models import Course, CourseAccess
from payments.models import ConversionAttribution, Tier, WebhookEvent
from payments.services import (
    _get_subscription_period_end,
    _get_subscription_price_id,
    _tier_for_price_id,
    _tier_from_subscription,
    cancel_subscription,
    create_checkout_session,
    downgrade_subscription,
    handle_checkout_completed,
    upgrade_subscription,
)


class StripeMappingObject:
    """Minimal StripeObject-like mapping whose .items is the dict method."""

    def __init__(self, **values):
        self._values = values

    def __getitem__(self, key):
        return self._values[key]

    def items(self):
        return self._values.items()


@tag('core')
class TierForPriceIdTest(TestCase):
    """Tests for the _tier_for_price_id helper function."""

    def setUp(self):
        self.basic = Tier.objects.get(slug="basic")
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.stripe_price_id_yearly = "price_basic_yearly"
        self.basic.save()

        self.main = Tier.objects.get(slug="main")
        self.main.stripe_price_id_monthly = "price_main_monthly"
        self.main.save()

    def test_finds_tier_by_monthly_price_id(self):
        tier = _tier_for_price_id("price_basic_monthly")
        self.assertEqual(tier, self.basic)

    def test_finds_tier_by_yearly_price_id(self):
        tier = _tier_for_price_id("price_basic_yearly")
        self.assertEqual(tier, self.basic)

    def test_returns_none_for_unknown_price_id(self):
        tier = _tier_for_price_id("price_unknown")
        self.assertIsNone(tier)

    def test_returns_none_for_empty_price_id(self):
        tier = _tier_for_price_id("")
        self.assertIsNone(tier)


@tag('core')
class SubscriptionExtractionTest(TestCase):
    """Tests for resilient Stripe subscription shape parsing."""

    def setUp(self):
        self.basic = Tier.objects.get(slug="basic")
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.stripe_price_id_yearly = "price_basic_yearly"
        self.basic.save(update_fields=[
            "stripe_price_id_monthly", "stripe_price_id_yearly",
        ])

    @patch("payments.services._get_stripe_client")
    def test_period_end_from_top_level_subscription(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "current_period_end": 1774396800,
            "items": {"data": []},
        }
        mock_get_client.return_value = mock_client

        period_end = _get_subscription_period_end("sub_top_period")

        self.assertEqual(
            period_end,
            datetime.fromtimestamp(1774396800, tz=timezone.utc),
        )

    @patch("payments.services._get_stripe_client")
    def test_period_end_from_first_subscription_item(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "items": {
                "data": [
                    {
                        "current_period_end": 1774396800,
                        "price": {"id": "price_basic_monthly"},
                    },
                ],
            },
        }
        mock_get_client.return_value = mock_client

        period_end = _get_subscription_period_end("sub_item_period")

        self.assertEqual(
            period_end,
            datetime.fromtimestamp(1774396800, tz=timezone.utc),
        )

    @patch("payments.services._get_stripe_client")
    def test_price_id_from_dict_like_subscription(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "items": {
                "data": [
                    {"price": {"id": "price_basic_yearly"}},
                ],
            },
        }
        mock_get_client.return_value = mock_client

        self.assertEqual(
            _get_subscription_price_id("sub_dict_price"),
            "price_basic_yearly",
        )

    @patch("payments.services._get_stripe_client")
    def test_price_id_from_mapping_object_with_items_method_collision(
        self, mock_get_client,
    ):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = StripeMappingObject(
            items={
                "data": [
                    StripeMappingObject(
                        current_period_end=1774396800,
                        price=StripeMappingObject(id="price_basic_monthly"),
                    ),
                ],
            },
        )
        mock_get_client.return_value = mock_client

        with patch("payments.services.logger") as mock_logger:
            price_id = _get_subscription_price_id("sub_collision_price")

        self.assertEqual(price_id, "price_basic_monthly")
        mock_logger.exception.assert_not_called()

    @patch("payments.services._get_stripe_client")
    def test_tier_from_subscription_uses_extracted_price_id(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "items": {
                "data": [
                    {"price": {"id": "price_basic_yearly"}},
                ],
            },
        }
        mock_get_client.return_value = mock_client

        self.assertEqual(_tier_from_subscription("sub_tier_price"), self.basic)

    @patch("payments.services._get_stripe_client")
    def test_subscription_lookup_stripe_error_fails_soft(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.side_effect = stripe.APIConnectionError(
            message="network unavailable",
        )
        mock_get_client.return_value = mock_client

        with patch("payments.services.logger") as mock_logger:
            price_id = _get_subscription_price_id("sub_stripe_down")
            period_end = _get_subscription_period_end("sub_stripe_down")
            tier = _tier_from_subscription("sub_stripe_down")

        self.assertEqual(price_id, "")
        self.assertIsNone(period_end)
        self.assertIsNone(tier)
        self.assertEqual(mock_logger.exception.call_count, 3)

    @patch("payments.services._get_stripe_client")
    def test_subscription_lookup_unexpected_error_propagates(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.side_effect = RuntimeError("parser bug")
        mock_get_client.return_value = mock_client

        with self.assertRaisesMessage(RuntimeError, "parser bug"):
            _get_subscription_price_id("sub_bug")

    @patch("payments.services._get_stripe_client")
    def test_incomplete_subscription_shape_fails_soft(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {"items": {"data": []}}
        mock_get_client.return_value = mock_client

        with patch("payments.services.logger") as mock_logger:
            price_id = _get_subscription_price_id("sub_incomplete")
            period_end = _get_subscription_period_end("sub_incomplete")

        self.assertEqual(price_id, "")
        self.assertIsNone(period_end)
        mock_logger.exception.assert_not_called()

    @patch("payments.services._get_stripe_client")
    def test_checkout_resolves_tier_and_yearly_attribution_from_subscription_price(
        self, mock_get_client,
    ):
        user = User.objects.create_user(email="subprice@test.com")
        subscription = {
            "items": {
                "data": [
                    {
                        "current_period_end": 1774396800,
                        "price": {"id": "price_basic_yearly"},
                    },
                ],
            },
        }
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = subscription
        mock_get_client.return_value = mock_client
        session_data = {
            "id": "cs_subprice",
            "customer": "cus_subprice",
            "customer_details": {"email": "subprice@test.com"},
            "subscription": "sub_subprice",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "missing", "user_id": str(user.pk)},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, self.basic)
        self.assertEqual(
            user.billing_period_end,
            datetime.fromtimestamp(1774396800, tz=timezone.utc),
        )
        attribution = ConversionAttribution.objects.get(
            stripe_session_id="cs_subprice",
        )
        self.assertEqual(attribution.billing_period, "yearly")
        self.assertEqual(attribution.amount_eur, self.basic.price_eur_year)
        self.assertEqual(attribution.mrr_eur, self.basic.price_eur_year // 12)


@tag('core')
class DeprecatedLocalStripeMutationServicesTest(TestCase):
    """Obsolete local Stripe mutation helpers fail before calling Stripe."""

    def setUp(self):
        self.user = User.objects.create_user(email="deprecated-services@test.com")

    @patch("payments.services._get_stripe_client")
    def test_create_checkout_session_is_hard_deprecated(self, mock_get_client):
        with self.assertRaisesMessage(RuntimeError, "Payment Links"):
            create_checkout_session(
                self.user,
                "basic",
                "monthly",
                "https://example.test/success",
                "https://example.test/cancel",
            )
        mock_get_client.assert_not_called()

    @patch("payments.services._get_stripe_client")
    def test_direct_subscription_mutations_are_hard_deprecated(
        self, mock_get_client,
    ):
        deprecated_calls = [
            lambda: upgrade_subscription(self.user, "main", "monthly"),
            lambda: downgrade_subscription(self.user, "basic", "monthly"),
            lambda: cancel_subscription(self.user),
        ]

        for call in deprecated_calls:
            with self.subTest(call=call):
                with self.assertRaisesMessage(RuntimeError, "Customer Portal"):
                    call()

        mock_get_client.assert_not_called()


@tag('core')
class PaymentNotificationEmailTest(TestCase):
    """Operator notification email on checkout completion (issue #645).

    Covers the 9 named scenarios in the issue spec. The operator's
    recipient address is configured via the ``PAYMENT_NOTIFICATION_EMAIL``
    setting; when unset / empty no email is sent. Send failures are
    swallowed (logged at WARNING) so the webhook still records success.
    """

    NOTIFY_TO = "ops@aishippinglabs.test"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _basic_session_data(self, user, *, session_id="cs_notify_basic"):
        return {
            "id": session_id,
            "customer": "cus_notify_basic",
            "customer_details": {"email": user.email},
            "subscription": "",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

    def _course_session_data(self, course, email, *, session_id="cs_notify_course"):
        return {
            "id": session_id,
            "customer": "cus_notify_course",
            "customer_details": {"email": email},
            "subscription": "",
            "client_reference_id": None,
            "metadata": {"course_id": str(course.pk)},
        }

    # ------------------------------------------------------------------
    # AC: no email when setting unset / empty
    # ------------------------------------------------------------------
    @patch("payments.services.get_config")
    def test_payment_notification_not_sent_when_setting_unset(self, mock_get_config):
        # Default to "" for ANY get_config call (Stripe keys etc all empty).
        mock_get_config.return_value = ""
        user = User.objects.create_user(email="unset@test.com")

        handle_checkout_completed(self._basic_session_data(user))

        # The recipient was looked up.
        recipient_calls = [
            c for c in mock_get_config.call_args_list
            if c.args and c.args[0] == "PAYMENT_NOTIFICATION_EMAIL"
        ]
        self.assertEqual(
            len(recipient_calls), 1,
            "Helper must check PAYMENT_NOTIFICATION_EMAIL exactly once.",
        )
        self.assertEqual(
            len(mail.outbox), 0,
            "No email may be sent when PAYMENT_NOTIFICATION_EMAIL is unset.",
        )

    def test_payment_notification_not_sent_when_setting_empty_string(self):
        # Patch only the PAYMENT_NOTIFICATION_EMAIL key, leave the rest
        # of get_config alone so the handler's other lookups still work.
        original_get_config = __import__(
            "integrations.config", fromlist=["get_config"]
        ).get_config

        def fake_get_config(key, default=None):
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return ""  # explicit empty string
            return original_get_config(key, default)

        user = User.objects.create_user(email="emptystr@test.com")

        with patch("payments.services.get_config", side_effect=fake_get_config):
            handle_checkout_completed(self._basic_session_data(user))

        self.assertEqual(
            len(mail.outbox), 0,
            "Empty-string setting must not trigger an email.",
        )

    # ------------------------------------------------------------------
    # AC: email sent for each variant
    # ------------------------------------------------------------------
    @patch("payments.services.get_config")
    def test_payment_notification_sent_when_setting_set_for_new_user(
        self, mock_get_config,
    ):
        # Only PAYMENT_NOTIFICATION_EMAIL has a meaningful value; the
        # rest of get_config returns "" so we avoid live Stripe calls.
        def _cfg(key, default=""):
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return self.NOTIFY_TO
            return default if default is not None else ""
        mock_get_config.side_effect = _cfg

        session_data = {
            "id": "cs_new_user_notify",
            "customer": "cus_new_user_notify",
            "customer_details": {"email": "newpaid@test.com"},
            "subscription": "",
            "client_reference_id": None,
            "metadata": {"tier_slug": "basic"},
        }

        handle_checkout_completed(session_data)

        # Exactly one email, to the configured recipient, with the
        # "new paid signup" subject.
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.NOTIFY_TO])
        self.assertEqual(
            sent.subject, "[AISL] New paid signup: newpaid@test.com",
        )
        # User was actually created by the handler.
        self.assertTrue(
            User.objects.filter(email="newpaid@test.com").exists(),
        )

    @patch("payments.services.get_config")
    def test_payment_notification_sent_when_setting_set_for_existing_user_upgrade(
        self, mock_get_config,
    ):
        def _cfg(key, default=""):
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return self.NOTIFY_TO
            return default if default is not None else ""
        mock_get_config.side_effect = _cfg

        # Pre-existing user already on free tier — checkout upgrades them.
        free_tier = Tier.objects.get(slug="free")
        user = User.objects.create_user(email="upgrade@test.com")
        user.tier = free_tier
        user.save(update_fields=["tier"])

        handle_checkout_completed(self._basic_session_data(user))

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.NOTIFY_TO])
        self.assertEqual(
            sent.subject, "[AISL] Tier upgrade: upgrade@test.com -> basic",
        )

    @patch("payments.services.get_config")
    def test_payment_notification_sent_when_setting_set_for_course_purchase(
        self, mock_get_config,
    ):
        def _cfg(key, default=""):
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return self.NOTIFY_TO
            return default if default is not None else ""
        mock_get_config.side_effect = _cfg

        user = User.objects.create_user(email="coursebuy@test.com")
        course = Course.objects.create(
            title="Resilient LLM Apps",
            slug="resilient-llm-apps",
            status="published",
            individual_price_eur=Decimal("99.00"),
        )

        handle_checkout_completed(self._course_session_data(course, user.email))

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.NOTIFY_TO])
        self.assertEqual(
            sent.subject, "[AISL] Course purchase: coursebuy@test.com",
        )
        # And the underlying CourseAccess row was still created — the
        # email is fired AFTER fulfillment, not in place of it.
        self.assertTrue(
            CourseAccess.objects.filter(user=user, course=course).exists(),
        )

    # ------------------------------------------------------------------
    # AC: subject prefixes distinguish the three variants
    # ------------------------------------------------------------------
    @patch("payments.services.get_config")
    def test_payment_notification_email_subject_distinguishes_new_vs_upgrade_vs_course(
        self, mock_get_config,
    ):
        def _cfg(key, default=""):
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return self.NOTIFY_TO
            return default if default is not None else ""
        mock_get_config.side_effect = _cfg

        # --- 1. New paid signup ---
        new_session = {
            "id": "cs_subject_new",
            "customer": "cus_subject_new",
            "customer_details": {"email": "subj-new@test.com"},
            "subscription": "",
            "client_reference_id": None,
            "metadata": {"tier_slug": "basic"},
        }
        handle_checkout_completed(new_session)

        # --- 2. Existing user upgrade ---
        existing = User.objects.create_user(email="subj-upgrade@test.com")
        free_tier = Tier.objects.get(slug="free")
        existing.tier = free_tier
        existing.save(update_fields=["tier"])
        handle_checkout_completed(self._basic_session_data(
            existing, session_id="cs_subject_upgrade",
        ))

        # --- 3. Course purchase ---
        buyer = User.objects.create_user(email="subj-course@test.com")
        course = Course.objects.create(
            title="MLOps Bootcamp",
            slug="mlops-bootcamp-645",
            status="published",
            individual_price_eur=Decimal("199.00"),
        )
        handle_checkout_completed(self._course_session_data(
            course, buyer.email, session_id="cs_subject_course",
        ))

        # Exactly three emails were sent; each prefix appears once.
        self.assertEqual(len(mail.outbox), 3)
        subjects = [m.subject for m in mail.outbox]
        self.assertIn(
            "[AISL] New paid signup: subj-new@test.com", subjects,
        )
        self.assertIn(
            "[AISL] Tier upgrade: subj-upgrade@test.com -> basic", subjects,
        )
        self.assertIn(
            "[AISL] Course purchase: subj-course@test.com", subjects,
        )

    # ------------------------------------------------------------------
    # AC: body contains required fields
    # ------------------------------------------------------------------
    @patch("payments.services.get_config")
    def test_payment_notification_email_body_contains_user_email_and_stripe_customer_id(
        self, mock_get_config,
    ):
        def _cfg(key, default=""):
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return self.NOTIFY_TO
            return default if default is not None else ""
        mock_get_config.side_effect = _cfg

        user = User.objects.create_user(email="body@test.com")
        free_tier = Tier.objects.get(slug="free")
        user.tier = free_tier
        user.save(update_fields=["tier"])

        session_data = {
            "id": "cs_body_test",
            "customer": "cus_body_test_xyz",
            "customer_details": {"email": user.email},
            "subscription": "",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        handle_checkout_completed(session_data)

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        # The required fields from the issue spec, line by line.
        self.assertIn("body@test.com", body)
        self.assertIn("cus_body_test_xyz", body)
        self.assertIn("cs_body_test", body)  # session id / traceability
        self.assertIn("basic", body)  # tier slug
        # The "new user" flag is rendered as yes/no — for an existing
        # user upgrade we expect "no".
        self.assertIn("New user: no", body)

    # ------------------------------------------------------------------
    # AC: send_mail failure is swallowed
    # ------------------------------------------------------------------
    @patch("payments.services.get_config")
    @patch("payments.services.send_mail")
    def test_payment_notification_send_mail_exception_does_not_break_handler(
        self, mock_send_mail, mock_get_config,
    ):
        def _cfg(key, default=""):
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return self.NOTIFY_TO
            return default if default is not None else ""
        mock_get_config.side_effect = _cfg

        # send_mail blows up with a realistic transport error.
        mock_send_mail.side_effect = SMTPException("relay refused")

        user = User.objects.create_user(email="smtp@test.com")
        free_tier = Tier.objects.get(slug="free")
        user.tier = free_tier
        user.save(update_fields=["tier"])

        with patch("payments.services.logger") as mock_logger:
            # Critically, this MUST NOT raise.
            handle_checkout_completed(self._basic_session_data(user))

        # The handler still committed the tier change before sending.
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "basic")
        # The failure was logged at WARNING level (not error/exception),
        # because a missing operator notification is not a payment
        # failure — the user has been served.
        mock_logger.warning.assert_called()
        # And send_mail really was attempted.
        mock_send_mail.assert_called_once()

    # ------------------------------------------------------------------
    # AC: idempotency rides on WebhookEvent; the helper doesn't add a
    # second guard. Two webhook deliveries of the same event id must
    # only fire one email.
    # ------------------------------------------------------------------
    def test_payment_notification_not_resent_on_duplicate_webhook_delivery(self):
        # This test goes through the webhook view to exercise the
        # real idempotency short-circuit. We use override_settings to
        # set the Stripe webhook secret and patch get_config so the
        # notification recipient is "set" without touching the DB.
        import hashlib
        import hmac
        import json
        import time

        from django.test import override_settings

        WEBHOOK_URL = "/api/webhooks/payments"
        WEBHOOK_SECRET = "whsec_dupe_645"

        original_get_config = __import__(
            "integrations.config", fromlist=["get_config"]
        ).get_config

        def _cfg(key, default=""):
            if key == "PAYMENT_NOTIFICATION_EMAIL":
                return self.NOTIFY_TO
            if key == "STRIPE_WEBHOOK_SECRET":
                return WEBHOOK_SECRET
            return original_get_config(key, default)

        def _sign(payload_bytes):
            ts = str(int(time.time()))
            signed = f"{ts}.{payload_bytes.decode('utf-8')}"
            sig = hmac.new(
                WEBHOOK_SECRET.encode(),
                signed.encode(),
                hashlib.sha256,
            ).hexdigest()
            return f"t={ts},v1={sig}"

        user = User.objects.create_user(email="dupe-notify@test.com")
        free_tier = Tier.objects.get(slug="free")
        user.tier = free_tier
        user.save(update_fields=["tier"])

        event_data = {
            "id": "evt_dupe_notify_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_dupe_notify_1",
                    "customer": "cus_dupe_notify",
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
        sig = _sign(payload)

        with override_settings(STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET), \
                patch("payments.services.get_config", side_effect=_cfg):
            # First delivery: handler runs, exactly one email is sent.
            r1 = self.client.post(
                WEBHOOK_URL, data=payload,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sig,
            )
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r1.json()["status"], "ok")
            self.assertEqual(
                len(mail.outbox), 1,
                "First delivery must send exactly one notification email.",
            )

            # Second delivery (same event id) — short-circuited as
            # already_processed; the helper must NOT run a second time.
            r2 = self.client.post(
                WEBHOOK_URL, data=payload,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sig,
            )
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r2.json()["status"], "already_processed")
            self.assertEqual(
                len(mail.outbox), 1,
                "Duplicate delivery must NOT send a second email.",
            )
            # And exactly one WebhookEvent row exists.
            self.assertEqual(
                WebhookEvent.objects.filter(
                    stripe_event_id="evt_dupe_notify_1",
                ).count(),
                1,
            )
