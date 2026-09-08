"""Migration coverage for duplicate Stripe webhook attempt numbers."""

from datetime import timedelta
from importlib import import_module

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class WebhookAttemptNumberMigrationTest(TransactionTestCase):
    migrate_from = [("payments", "0015_webhookevent_stripe_customer_id_and_more")]
    migrate_to = [("payments", "0016_unique_webhook_attempt_number")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def test_duplicate_rows_become_dense_before_unique_constraint(self):
        OldAttempt = self.old_apps.get_model(
            "payments",
            "StripeWebhookDeliveryAttempt",
        )
        received_at = timezone.now()
        for offset, attempt_number in enumerate((7, 1, 1)):
            OldAttempt.objects.create(
                stripe_event_id="evt_legacy_duplicate",
                event_type="customer.subscription.deleted",
                received_at=received_at + timedelta(seconds=offset),
                attempt_number=attempt_number,
            )
        OldAttempt.objects.create(
            stripe_event_id="evt_other",
            event_type="customer.subscription.deleted",
            attempt_number=4,
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        current_apps = self.executor.loader.project_state(self.migrate_to).apps
        Attempt = current_apps.get_model(
            "payments",
            "StripeWebhookDeliveryAttempt",
        )

        self.assertEqual(
            list(
                Attempt.objects.filter(
                    stripe_event_id="evt_legacy_duplicate",
                ).order_by("received_at", "pk").values_list(
                    "attempt_number",
                    flat=True,
                )
            ),
            [1, 2, 3],
        )
        self.assertEqual(
            Attempt.objects.get(stripe_event_id="evt_other").attempt_number,
            1,
        )

        migration = import_module(
            "payments.migrations.0016_unique_webhook_attempt_number",
        )
        migration.renumber_delivery_attempts(current_apps, None)
        self.assertEqual(
            list(
                Attempt.objects.filter(
                    stripe_event_id="evt_legacy_duplicate",
                ).order_by("received_at", "pk").values_list(
                    "attempt_number",
                    flat=True,
                )
            ),
            [1, 2, 3],
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Attempt.objects.create(
                    stripe_event_id="evt_legacy_duplicate",
                    event_type="customer.subscription.deleted",
                    attempt_number=1,
                )
