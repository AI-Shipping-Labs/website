"""Focused H-10 regressions for exact privacy webhook ownership."""

import copy
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import PrivacyRequestLog, User
from accounts.services.privacy import REDACTED, delete_account_for_privacy
from community.models import BookedCall, CallHost, UnmatchedBookedCall
from integrations.models import WebhookLog
from payments.models import (
    CheckoutFulfillment,
    ConversionAttribution,
    MonthlyPaymentGrace,
    PaymentAccountMismatch,
    SubscriptionReconciliationFinding,
    SubscriptionReconciliationRun,
    Tier,
    WebhookEvent,
)


@tag("core")
class PrivacyWebhookCorrelationTest(TestCase):
    def test_only_exactly_correlated_rows_are_allowlist_scrubbed(self):
        target = User.objects.create_user(
            email="alex@example.test",
            first_name="Alex",
            last_name="Member",
        )
        target_id = target.pk
        target.stripe_customer_id = "cus_target"
        target.save(update_fields=["stripe_customer_id"])

        host = CallHost.objects.create(
            name="Alexey",
            slug="privacy-correlation-host",
            booking_url="https://calendly.com/privacy-host",
            is_active=True,
            capacity=2,
            current_load=1,
        )
        BookedCall.objects.create(
            host=host,
            member=target,
            invitee_email=target.email,
            invitee_name="Alex",
            calendly_event_uri="https://api.calendly.com/events/target",
            calendly_invitee_uri="https://api.calendly.com/invitees/target",
        )
        UnmatchedBookedCall.objects.create(
            member=target,
            invitee_email=target.email,
            invitee_name="Alex",
            calendly_event_uri="https://api.calendly.com/events/staged",
            calendly_invitee_uri="https://api.calendly.com/invitees/staged",
        )

        stripe_payload = {
            "id": "evt-target",
            "data": {
                "object": {
                    "id": "cs_target",
                    "customer": "cus_target",
                    "subscription": "sub_target",
                    "customer_email": target.email,
                    "receipt_email": target.email,
                    "email": target.email,
                    "name": "Alex",
                    "phone": "+49 123",
                    "address": {"line1": "Target street"},
                    "shipping": {"name": "Alex"},
                    "billing_details": {"email": target.email},
                    "customer_details": {
                        "email": target.email,
                        "name": "Alex",
                        "phone": "+49 123",
                        "address": {"city": "Berlin"},
                    },
                    "metadata": {
                        "user_id": str(target.pk),
                        "unlisted_text": "Alex cus_target",
                    },
                    "client_reference_id": str(target.pk),
                    "unlisted_text": "prefix-cus_target and Alex",
                    "unlisted_list": ["Alex", {"note": target.email}],
                },
            },
            "unlisted_root": "Alex cus_target",
        }
        correlated_stripe = WebhookEvent.objects.create(
            stripe_event_id="evt_privacy_correlated",
            event_type="checkout.session.completed",
            stripe_customer_id="cus_target",
            stripe_subscription_id="sub_target",
            payload=stripe_payload,
            error_message="provider diagnostic detail",
        )
        internal_payload = {
            "email": target.email,
            "stripe_customer_id": "cus_target",
            "stripe_subscription_id": "sub_target",
            "subscription_id": "sub_target",
            "old_subscription_id": "sub_old_target",
            "unlisted": {
                "text": "Alex cus_target",
                "list": [target.email, "keep this"],
            },
        }
        correlated_internal = WebhookEvent.objects.create(
            stripe_event_id="audit_privacy_correlated",
            event_type="backfill_stripe_tiers",
            subject_user_id=target.pk,
            payload=internal_payload,
            error_message="internal diagnostic detail",
        )
        calendly_payload = {
            "event": "invitee.created",
            "payload": {
                "email": target.email,
                "name": "Alex",
                "text_reminder_number": "+49 123",
                "reschedule_url": "https://calendly.test/reschedule/target",
                "cancel_url": "https://calendly.test/cancel/target",
                "uri": "https://api.calendly.com/invitees/target",
                "scheduled_event": {
                    "uri": "https://api.calendly.com/events/target",
                    "start_time": "2099-01-01T12:00:00Z",
                },
                "questions_and_answers": [
                    {"question": "What will you build?", "answer": "Alex's project"},
                ],
                "description": "Alex cus_target",
            },
        }
        correlated_calendly = WebhookLog.objects.create(
            service="calendly",
            event_type="invitee.created",
            calendly_event_uri="https://api.calendly.com/events/target",
            calendly_invitee_uri="https://api.calendly.com/invitees/target",
            payload=calendly_payload,
            error_message="calendly diagnostic detail",
        )

        unrelated_stripe_payload = {
            "data": {
                "object": {
                    "customer_email": target.email,
                    "name": "Alex",
                    "description": "prefix-cus_target",
                    "metadata": {"note": "Alex"},
                    "list": [target.email, "Alex"],
                },
            },
        }
        unrelated_stripe = WebhookEvent.objects.create(
            stripe_event_id="evt_privacy_unrelated",
            event_type="checkout.session.completed",
            payload=unrelated_stripe_payload,
            error_message="keep this diagnostic detail",
        )
        unrelated_calendly_payload = {
            "event": "invitee.created",
            "payload": {
                "email": target.email,
                "name": "Alex",
                "questions_and_answers": [{"question": "Keep", "answer": "Alex"}],
                "description": "prefix-cus_target",
            },
        }
        unrelated_calendly = WebhookLog.objects.create(
            service="calendly",
            event_type="invitee.created",
            payload=unrelated_calendly_payload,
            error_message="keep this Calendly diagnostic detail",
        )
        original_unrelated_stripe = copy.deepcopy(unrelated_stripe_payload)
        original_unrelated_calendly = copy.deepcopy(unrelated_calendly_payload)

        with patch("accounts.services.privacy.notify_privacy_staff") as notify:
            result = delete_account_for_privacy(target)

        self.assertTrue(result.success)
        notify.assert_called_once_with(
            event="completed_delete",
            email="alex@example.test",
            old_user_id=target_id,
            row_count_summary=result.row_count_summary,
        )

        correlated_stripe.refresh_from_db()
        stripe_object = correlated_stripe.payload["data"]["object"]
        self.assertEqual(stripe_object["customer"], REDACTED)
        self.assertEqual(stripe_object["subscription"], REDACTED)
        self.assertEqual(stripe_object["customer_email"], REDACTED)
        self.assertEqual(stripe_object["receipt_email"], REDACTED)
        self.assertEqual(stripe_object["email"], REDACTED)
        self.assertEqual(stripe_object["name"], REDACTED)
        self.assertEqual(stripe_object["phone"], REDACTED)
        self.assertEqual(stripe_object["address"], REDACTED)
        self.assertEqual(stripe_object["shipping"], REDACTED)
        self.assertEqual(stripe_object["billing_details"], REDACTED)
        self.assertEqual(stripe_object["customer_details"]["email"], REDACTED)
        self.assertEqual(stripe_object["customer_details"]["name"], REDACTED)
        self.assertEqual(stripe_object["customer_details"]["phone"], REDACTED)
        self.assertEqual(stripe_object["customer_details"]["address"], REDACTED)
        self.assertEqual(stripe_object["metadata"]["user_id"], REDACTED)
        self.assertEqual(stripe_object["client_reference_id"], REDACTED)
        self.assertEqual(stripe_object["id"], "cs_target")
        self.assertEqual(stripe_object["metadata"]["unlisted_text"], "Alex cus_target")
        self.assertEqual(stripe_object["unlisted_text"], "prefix-cus_target and Alex")
        self.assertEqual(stripe_object["unlisted_list"], ["Alex", {"note": target.email}])
        self.assertEqual(correlated_stripe.error_message, REDACTED)

        correlated_internal.refresh_from_db()
        for key in (
            "email",
            "stripe_customer_id",
            "stripe_subscription_id",
            "subscription_id",
            "old_subscription_id",
        ):
            self.assertEqual(correlated_internal.payload[key], REDACTED)
        self.assertEqual(
            correlated_internal.payload["unlisted"],
            {"text": "Alex cus_target", "list": [target.email, "keep this"]},
        )
        self.assertEqual(correlated_internal.error_message, REDACTED)

        correlated_calendly.refresh_from_db()
        calendly_inner = correlated_calendly.payload["payload"]
        for key in (
            "email",
            "name",
            "text_reminder_number",
            "reschedule_url",
            "cancel_url",
        ):
            self.assertEqual(calendly_inner[key], REDACTED)
        self.assertEqual(calendly_inner["uri"], "https://api.calendly.com/invitees/target")
        self.assertEqual(
            calendly_inner["scheduled_event"]["uri"],
            "https://api.calendly.com/events/target",
        )
        self.assertEqual(
            calendly_inner["scheduled_event"]["start_time"],
            "2099-01-01T12:00:00Z",
        )
        self.assertEqual(
            calendly_inner["questions_and_answers"],
            [{"question": "What will you build?", "answer": REDACTED}],
        )
        self.assertEqual(calendly_inner["description"], "Alex cus_target")
        self.assertEqual(correlated_calendly.error_message, REDACTED)

        unrelated_stripe.refresh_from_db()
        unrelated_calendly.refresh_from_db()
        self.assertEqual(unrelated_stripe.payload, original_unrelated_stripe)
        self.assertEqual(unrelated_stripe.error_message, "keep this diagnostic detail")
        self.assertEqual(unrelated_calendly.payload, original_unrelated_calendly)
        self.assertEqual(
            unrelated_calendly.error_message,
            "keep this Calendly diagnostic detail",
        )
        self.assertEqual(
            result.row_count_summary["retained"]["scrubbed_webhook_events"],
            2,
        )
        self.assertEqual(
            result.row_count_summary["retained"]["scrubbed_calendly_webhook_logs"],
            1,
        )

    def test_payment_correlations_include_only_subject_owned_rows(self):
        target = User.objects.create_user(email="payment-target@test.example")
        target.stripe_customer_id = "cus_user_owned"
        target.save(update_fields=["stripe_customer_id"])
        main = Tier.objects.get(slug="main")
        now = timezone.now()

        ConversionAttribution.objects.create(
            user=target,
            stripe_session_id="cs_conversion_owned",
            stripe_subscription_id="sub_conversion_owned",
            tier=main,
            billing_period="monthly",
            amount_eur=50,
            mrr_eur=50,
        )
        CheckoutFulfillment.objects.create(
            user=target,
            stripe_session_id="cs_fulfillment_owned",
            stripe_customer_id="cus_fulfillment_owned",
            stripe_subscription_id="sub_fulfillment_owned",
            status=CheckoutFulfillment.STATUS_FULFILLED,
        )
        MonthlyPaymentGrace.objects.create(
            user=target,
            base_tier_at_start=main,
            stripe_customer_id="cus_grace_owned",
            stripe_subscription_id="sub_grace_owned",
            stripe_invoice_id="in_grace_owned",
            source=MonthlyPaymentGrace.SOURCE_WEBHOOK,
            status=MonthlyPaymentGrace.STATUS_ACTIVE,
            interval="month",
            interval_count=1,
            grace_started_at=now,
            grace_expires_at=now + timedelta(days=7),
        )
        run = SubscriptionReconciliationRun.objects.create()
        SubscriptionReconciliationFinding.objects.create(
            run=run,
            user=target,
            email=target.email,
            classification="owned_test",
            current_subscription_id="sub_finding_current_owned",
            stripe_customer_id="cus_finding_owned",
            stripe_subscription_id="sub_finding_owned",
        )
        PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_paid_owned",
            stripe_customer_id="cus_paid_owned",
            stripe_subscription_id="sub_paid_owned",
            stripe_email=target.email,
            paid_user=target,
            reason=PaymentAccountMismatch.REASON_UNKNOWN_REFERENCE,
        )

        correlation_cases = (
            ("user", "cus_user_owned", "sub_user_owned"),
            ("conversion", "", "sub_conversion_owned"),
            ("fulfillment", "cus_fulfillment_owned", "sub_fulfillment_owned"),
            ("grace", "cus_grace_owned", "sub_grace_owned"),
            ("finding", "cus_finding_owned", "sub_finding_owned"),
            ("paid", "cus_paid_owned", "sub_paid_owned"),
        )
        events = []
        for suffix, customer_id, subscription_id in correlation_cases:
            events.append(
                WebhookEvent.objects.create(
                    stripe_event_id=f"evt_owned_{suffix}",
                    event_type="checkout.session.completed",
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    payload={
                        "data": {
                            "object": {
                                "customer": customer_id,
                                "subscription": subscription_id,
                                "customer_email": target.email,
                            },
                        },
                    },
                )
            )

        with patch("accounts.services.privacy.notify_privacy_staff"):
            result = delete_account_for_privacy(target)

        self.assertTrue(result.success)
        self.assertEqual(
            result.row_count_summary["retained"]["scrubbed_webhook_events"],
            len(events),
        )
        for event, (_, customer_id, subscription_id) in zip(
            events,
            correlation_cases,
            strict=True,
        ):
            event.refresh_from_db()
            obj = event.payload["data"]["object"]
            self.assertEqual(
                obj["customer"],
                REDACTED if customer_id else "",
            )
            self.assertEqual(
                obj["subscription"],
                REDACTED if subscription_id else "",
            )
            self.assertEqual(obj["customer_email"], REDACTED)

    def test_customer_and_subscription_object_ids_are_redacted(self):
        target = User.objects.create_user(email="object-id-target@test.example")
        target.stripe_customer_id = "cus_object_target"
        target.save(update_fields=["stripe_customer_id"])
        customer_event = WebhookEvent.objects.create(
            stripe_event_id="evt_customer_object_id",
            event_type="customer.updated",
            stripe_customer_id="cus_object_target",
            payload={"data": {"object": {"id": "cus_object_target"}}},
        )
        subscription_event = WebhookEvent.objects.create(
            stripe_event_id="evt_subscription_object_id",
            event_type="customer.subscription.updated",
            subject_user_id=target.pk,
            stripe_subscription_id="sub_object_target",
            payload={"data": {"object": {"id": "sub_object_target"}}},
        )

        with patch("accounts.services.privacy.notify_privacy_staff"):
            result = delete_account_for_privacy(target)

        self.assertTrue(result.success)
        customer_event.refresh_from_db()
        subscription_event.refresh_from_db()
        self.assertEqual(customer_event.payload["data"]["object"]["id"], REDACTED)
        self.assertEqual(
            subscription_event.payload["data"]["object"]["id"],
            REDACTED,
        )

    def test_oversized_numeric_subject_values_do_not_break_privacy_scrubbing(self):
        target = User.objects.create_user(email="oversized-subject@test.example")
        target.stripe_customer_id = "cus_oversized_subject"
        target.save(update_fields=["stripe_customer_id"])
        oversized_numeric_value = "9" * 4301
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_oversized_subject",
            event_type="checkout.session.completed",
            stripe_customer_id="cus_oversized_subject",
            payload={
                "data": {
                    "object": {
                        "customer_email": target.email,
                        "metadata": {"user_id": oversized_numeric_value},
                        "client_reference_id": oversized_numeric_value,
                    },
                },
            },
        )

        with patch("accounts.services.privacy.notify_privacy_staff"):
            result = delete_account_for_privacy(target)

        self.assertTrue(result.success)
        event.refresh_from_db()
        obj = event.payload["data"]["object"]
        self.assertEqual(obj["customer_email"], REDACTED)
        self.assertEqual(obj["metadata"]["user_id"], oversized_numeric_value)
        self.assertEqual(obj["client_reference_id"], oversized_numeric_value)

    def test_candidate_and_resolver_links_do_not_borrow_or_scrub_other_billing_data(self):
        target = User.objects.create_user(email="operator-target@test.example")
        paid_user = User.objects.create_user(email="paid-owner@test.example")
        candidate = PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_candidate_only",
            stripe_customer_id="cus_candidate_only",
            stripe_subscription_id="sub_candidate_only",
            stripe_email="other-billing@example.test",
            paid_user=paid_user,
            candidate_user=target,
            reason=PaymentAccountMismatch.REASON_PRIMARY_EMAIL_COLLISION,
            details={
                "paid_user_email": "other-billing@example.test",
                "stripe_customer_id": "cus_candidate_only",
                "keep": "candidate context",
            },
        )
        resolved = PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_resolved_only",
            stripe_customer_id="cus_resolved_only",
            stripe_subscription_id="sub_resolved_only",
            stripe_email="resolved-owner@example.test",
            paid_user=paid_user,
            resolved_by=target,
            reason=PaymentAccountMismatch.REASON_UNKNOWN_REFERENCE,
            details={
                "email": "resolved-owner@example.test",
                "stripe_customer_id": "cus_resolved_only",
                "keep": "operator history",
            },
        )
        candidate_event = WebhookEvent.objects.create(
            stripe_event_id="evt_candidate_only",
            event_type="checkout.session.completed",
            stripe_customer_id="cus_candidate_only",
            stripe_subscription_id="sub_candidate_only",
            payload={"data": {"object": {"customer_email": candidate.stripe_email}}},
        )
        resolved_event = WebhookEvent.objects.create(
            stripe_event_id="evt_resolved_only",
            event_type="checkout.session.completed",
            stripe_customer_id="cus_resolved_only",
            stripe_subscription_id="sub_resolved_only",
            payload={"data": {"object": {"customer_email": resolved.stripe_email}}},
        )

        target_id = target.pk
        with patch("accounts.services.privacy.notify_privacy_staff"):
            result = delete_account_for_privacy(target)

        self.assertTrue(result.success)
        candidate.refresh_from_db()
        self.assertIsNone(candidate.candidate_user_id)
        self.assertEqual(candidate.paid_user_id, paid_user.pk)
        self.assertEqual(
            candidate.stripe_email,
            f"deleted-user-{target_id}@privacy.invalid",
        )
        self.assertEqual(candidate.details["paid_user_email"], REDACTED)
        self.assertEqual(candidate.details["stripe_customer_id"], REDACTED)
        self.assertEqual(candidate.details["keep"], "candidate context")

        resolved.refresh_from_db()
        self.assertIsNone(resolved.resolved_by_id)
        self.assertEqual(resolved.paid_user_id, paid_user.pk)
        self.assertEqual(resolved.stripe_email, "resolved-owner@example.test")
        self.assertEqual(
            resolved.details,
            {
                "email": "resolved-owner@example.test",
                "stripe_customer_id": "cus_resolved_only",
                "keep": "operator history",
            },
        )
        candidate_event.refresh_from_db()
        resolved_event.refresh_from_db()
        self.assertEqual(
            candidate_event.payload,
            {"data": {"object": {"customer_email": "other-billing@example.test"}}},
        )
        self.assertEqual(
            resolved_event.payload,
            {"data": {"object": {"customer_email": "resolved-owner@example.test"}}},
        )
        self.assertEqual(
            result.row_count_summary["retained"]["payment_account_mismatches"],
            2,
        )

    def test_scrub_failure_rolls_back_account_retained_rows_and_audit(self):
        target = User.objects.create_user(
            email="rollback-target@test.example",
            first_name="Rollback",
        )
        target.stripe_customer_id = "cus_rollback"
        target.save(update_fields=["stripe_customer_id"])
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_privacy_rollback",
            event_type="checkout.session.completed",
            stripe_customer_id="cus_rollback",
            payload={
                "data": {
                    "object": {
                        "customer": "cus_rollback",
                        "customer_email": target.email,
                    },
                },
            },
        )
        original_payload = copy.deepcopy(event.payload)

        def fail_when_scrubbing(*args, **kwargs):
            if kwargs.get("update_fields") == ["payload", "error_message"]:
                raise RuntimeError("controlled scrub failure")
            raise AssertionError("unexpected WebhookEvent save")

        with patch.object(WebhookEvent, "save", side_effect=fail_when_scrubbing):
            with patch("accounts.services.privacy.notify_privacy_staff") as notify:
                with self.assertRaisesRegex(RuntimeError, "controlled scrub failure"):
                    delete_account_for_privacy(target)

        self.assertTrue(User.objects.filter(pk=target.pk).exists())
        event.refresh_from_db()
        self.assertEqual(event.payload, original_payload)
        self.assertEqual(event.stripe_customer_id, "cus_rollback")
        self.assertFalse(
            PrivacyRequestLog.objects.filter(
                request_type=PrivacyRequestLog.REQUEST_DELETE,
                status=PrivacyRequestLog.STATUS_COMPLETED,
                old_user_id=target.pk,
            ).exists()
        )
        notify.assert_not_called()
