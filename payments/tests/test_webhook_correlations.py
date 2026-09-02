"""Correlation persistence and historical backfill for retained webhooks."""

import copy
import importlib
from unittest.mock import patch

from django.apps import apps as django_apps
from django.test import TestCase, tag

from accounts.models import User
from integrations.models import WebhookLog
from payments.models import StripeWebhookDeliveryAttempt, WebhookEvent
from payments.services.webhook_dispatch import process_event


@tag("core")
class WebhookCorrelationIngestionTest(TestCase):
    def test_stripe_terminal_events_store_safe_correlations_and_keep_idempotency(self):
        cases = (
            (
                "checkout.session.completed",
                {
                    "id": "cs_corr",
                    "customer": "cus_corr",
                    "subscription": {"id": "sub_corr"},
                    "metadata": {"user_id": "101"},
                    "client_reference_id": "101",
                },
                101,
                "cus_corr",
                "sub_corr",
            ),
            (
                "customer.updated",
                {
                    "id": "cus_customer_corr",
                    "metadata": {"user_id": 102},
                },
                102,
                "cus_customer_corr",
                "",
            ),
            (
                "customer.subscription.updated",
                {
                    "id": "sub_subscription_corr",
                    "customer": {"id": "cus_subscription_corr"},
                    "metadata": {"user_id": "103"},
                },
                103,
                "cus_subscription_corr",
                "sub_subscription_corr",
            ),
            (
                "invoice.paid",
                {
                    "id": "in_corr",
                    "customer": {"id": "cus_invoice_corr"},
                    "subscription": "sub_invoice_corr",
                    "client_reference_id": "104",
                },
                104,
                "cus_invoice_corr",
                "sub_invoice_corr",
            ),
        )

        with patch(
            "payments.services.webhook_dispatch.run_handler",
            return_value=StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED,
        ) as run_handler:
            for index, (event_type, obj, subject_id, customer_id, subscription_id) in enumerate(cases):
                event_id = f"evt_corr_{index}"
                outcome, status = process_event(
                    event_id=event_id,
                    event_type=event_type,
                    obj=obj,
                    livemode=False,
                )
                self.assertEqual(outcome, "processed")
                self.assertEqual(status, 200)
                terminal = WebhookEvent.objects.get(stripe_event_id=event_id)
                self.assertEqual(terminal.subject_user_id, subject_id)
                self.assertEqual(terminal.stripe_customer_id, customer_id)
                self.assertEqual(terminal.stripe_subscription_id, subscription_id)
                self.assertEqual(terminal.payload, {})

            duplicate_obj = cases[0][1]
            process_event(
                event_id="evt_corr_0",
                event_type=cases[0][0],
                obj=duplicate_obj,
                livemode=False,
            )

        self.assertEqual(run_handler.call_count, len(cases))
        attempts = StripeWebhookDeliveryAttempt.objects.filter(
            stripe_event_id="evt_corr_0",
        ).order_by("attempt_number")
        self.assertEqual(attempts.count(), 2)
        self.assertEqual(
            list(attempts.values_list("outcome", flat=True)),
            ["processed", "already_processed"],
        )
        terminal = WebhookEvent.objects.get(stripe_event_id="evt_corr_0")
        self.assertEqual(terminal.subject_user_id, 101)
        self.assertEqual(terminal.stripe_customer_id, "cus_corr")
        self.assertEqual(terminal.stripe_subscription_id, "sub_corr")

    def test_ambiguous_or_malformed_subject_values_are_not_persisted(self):
        with patch(
            "payments.services.webhook_dispatch.run_handler",
            return_value=StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED,
        ):
            process_event(
                event_id="evt_corr_ambiguous",
                event_type="checkout.session.completed",
                obj={
                    "id": "cs_ambiguous",
                    "customer": "cus_ambiguous",
                    "metadata": {"user_id": "201"},
                    "client_reference_id": "202",
                },
                livemode=False,
            )
            process_event(
                event_id="evt_corr_malformed",
                event_type="invoice.paid",
                obj={
                    "id": "in_malformed",
                    "customer": 123,
                    "subscription": {"id": 456},
                    "metadata": {"user_id": True},
                    "client_reference_id": " 203",
                },
                livemode=False,
            )

        ambiguous = WebhookEvent.objects.get(stripe_event_id="evt_corr_ambiguous")
        self.assertIsNone(ambiguous.subject_user_id)
        self.assertEqual(ambiguous.stripe_customer_id, "cus_ambiguous")
        malformed = WebhookEvent.objects.get(stripe_event_id="evt_corr_malformed")
        self.assertIsNone(malformed.subject_user_id)
        self.assertEqual(malformed.stripe_customer_id, "")
        self.assertEqual(malformed.stripe_subscription_id, "")

    def test_oversized_numeric_subject_does_not_fail_terminal_delivery(self):
        with patch(
            "payments.services.webhook_dispatch.run_handler",
            return_value=StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED,
        ):
            outcome, status = process_event(
                event_id="evt_corr_oversized_subject",
                event_type="checkout.session.completed",
                obj={
                    "id": "cs_oversized_subject",
                    "customer": "cus_oversized_subject",
                    "metadata": {"user_id": "9" * 4301},
                },
                livemode=False,
            )

        self.assertEqual(outcome, StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED)
        self.assertEqual(status, 200)
        terminal = WebhookEvent.objects.get(
            stripe_event_id="evt_corr_oversized_subject",
        )
        self.assertIsNone(terminal.subject_user_id)
        self.assertEqual(terminal.stripe_customer_id, "cus_oversized_subject")
        self.assertEqual(terminal.payload, {})


@tag("core")
class WebhookCorrelationMigrationTest(TestCase):
    def test_backfill_reads_documented_paths_in_batches_without_changing_payloads(self):
        target = User.objects.create_user(email="correlation-target@test.com")
        provider = WebhookEvent.objects.create(
            stripe_event_id="evt_migration_provider",
            event_type="checkout.session.completed",
            payload={
                "data": {
                    "object": {
                        "customer": "cus_migration",
                        "subscription": {"id": "sub_migration"},
                        "metadata": {"user_id": str(target.pk)},
                        "client_reference_id": str(target.pk),
                    },
                },
                "root_customer": "cus_ignore_me",
            },
        )
        internal = WebhookEvent.objects.create(
            stripe_event_id="audit_migration_provider",
            event_type="backfill_stripe_tiers",
            payload={
                "user_id": str(target.pk),
                "stripe_customer_id": "cus_audit",
                "stripe_subscription_id": "sub_audit",
                "subscription_id": "sub_audit",
                "old_subscription_id": "sub_audit",
                "data": {
                    "object": {
                        "customer": "cus_should_be_ignored",
                    },
                },
            },
        )
        ambiguous_subject = WebhookEvent.objects.create(
            stripe_event_id="evt_migration_ambiguous_subject",
            event_type="invoice.paid",
            payload={
                "data": {
                    "object": {
                        "customer": "cus_ambiguous_subject",
                        "subscription": "sub_ambiguous_subject",
                        "metadata": {"user_id": "301"},
                        "client_reference_id": "302",
                    },
                },
            },
        )
        ambiguous_internal = WebhookEvent.objects.create(
            stripe_event_id="audit_migration_ambiguous_ids",
            event_type="subscription_reconciliation_apply",
            payload={
                "user_id": target.pk,
                "stripe_customer_id": "cus_ambiguous_audit",
                "stripe_subscription_id": "sub_new",
                "subscription_id": "sub_old",
            },
        )
        malformed = WebhookEvent.objects.create(
            stripe_event_id="evt_migration_malformed",
            event_type="customer.updated",
            payload={
                "data": {
                    "object": {
                        "id": 123,
                        "customer": 123,
                        "metadata": {"user_id": "9" * 4301},
                    },
                },
            },
        )
        unknown = WebhookEvent.objects.create(
            stripe_event_id="evt_migration_unknown",
            event_type="invoice.created",
            payload={
                "data": {
                    "object": {
                        "customer": "cus_unknown",
                        "subscription": "sub_unknown",
                        "metadata": {"user_id": str(target.pk)},
                    },
                },
            },
        )

        calendly = WebhookLog.objects.create(
            service="calendly",
            event_type="invitee.created",
            payload={
                "payload": {
                    "uri": "https://api.calendly.com/invitees/migration",
                    "scheduled_event": {
                        "uri": "https://api.calendly.com/events/migration",
                    },
                },
            },
        )
        malformed_calendly = WebhookLog.objects.create(
            service="calendly",
            event_type="invitee.created",
            payload={
                "payload": {
                    "uri": ["not-a-uri"],
                    "scheduled_event": {"uri": "x" * 501},
                },
            },
        )
        other_service = WebhookLog.objects.create(
            service="zoom",
            event_type="invitee.created",
            payload={
                "payload": {
                    "uri": "https://api.calendly.com/invitees/ignored",
                    "scheduled_event": {
                        "uri": "https://api.calendly.com/events/ignored",
                    },
                },
            },
        )

        payloads = {
            ("event", row.pk): copy.deepcopy(row.payload)
            for row in (provider, internal, ambiguous_subject, ambiguous_internal, malformed, unknown)
        }
        payloads.update(
            {("log", row.pk): copy.deepcopy(row.payload) for row in (calendly, malformed_calendly, other_service)}
        )

        migration = importlib.import_module(
            "payments.migrations.0015_webhookevent_stripe_customer_id_and_more",
        )
        migration.backfill_webhook_correlations(django_apps, None)

        provider.refresh_from_db()
        self.assertEqual(provider.subject_user_id, target.pk)
        self.assertEqual(provider.stripe_customer_id, "cus_migration")
        self.assertEqual(provider.stripe_subscription_id, "sub_migration")
        internal.refresh_from_db()
        self.assertEqual(internal.subject_user_id, target.pk)
        self.assertEqual(internal.stripe_customer_id, "cus_audit")
        self.assertEqual(internal.stripe_subscription_id, "sub_audit")
        ambiguous_subject.refresh_from_db()
        self.assertIsNone(ambiguous_subject.subject_user_id)
        self.assertEqual(ambiguous_subject.stripe_customer_id, "cus_ambiguous_subject")
        self.assertEqual(ambiguous_subject.stripe_subscription_id, "sub_ambiguous_subject")
        ambiguous_internal.refresh_from_db()
        self.assertEqual(ambiguous_internal.subject_user_id, target.pk)
        self.assertEqual(ambiguous_internal.stripe_customer_id, "cus_ambiguous_audit")
        self.assertEqual(ambiguous_internal.stripe_subscription_id, "")
        malformed.refresh_from_db()
        self.assertIsNone(malformed.subject_user_id)
        self.assertEqual(malformed.stripe_customer_id, "")
        self.assertEqual(malformed.stripe_subscription_id, "")
        unknown.refresh_from_db()
        self.assertIsNone(unknown.subject_user_id)
        self.assertEqual(unknown.stripe_customer_id, "")
        self.assertEqual(unknown.stripe_subscription_id, "")

        calendly.refresh_from_db()
        self.assertEqual(
            calendly.calendly_event_uri,
            "https://api.calendly.com/events/migration",
        )
        self.assertEqual(
            calendly.calendly_invitee_uri,
            "https://api.calendly.com/invitees/migration",
        )
        malformed_calendly.refresh_from_db()
        self.assertEqual(malformed_calendly.calendly_event_uri, "")
        self.assertEqual(malformed_calendly.calendly_invitee_uri, "")
        other_service.refresh_from_db()
        self.assertEqual(other_service.calendly_event_uri, "")
        self.assertEqual(other_service.calendly_invitee_uri, "")

        for model, key in (
            (WebhookEvent, "event"),
            (WebhookLog, "log"),
        ):
            for row in model.objects.all():
                self.assertEqual(row.payload, payloads[(key, row.pk)])

        migration.backfill_webhook_correlations(django_apps, None)
        for row in (provider, internal, ambiguous_subject, ambiguous_internal, malformed, unknown):
            row.refresh_from_db()
        for row in (calendly, malformed_calendly, other_service):
            row.refresh_from_db()
        self.assertEqual(provider.subject_user_id, target.pk)
        self.assertEqual(provider.stripe_customer_id, "cus_migration")
        self.assertEqual(provider.stripe_subscription_id, "sub_migration")
        self.assertEqual(
            calendly.calendly_event_uri,
            "https://api.calendly.com/events/migration",
        )
        self.assertEqual(
            calendly.calendly_invitee_uri,
            "https://api.calendly.com/invitees/migration",
        )
