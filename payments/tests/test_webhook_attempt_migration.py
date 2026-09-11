"""Migration coverage for payments schema/data changes."""

from datetime import timedelta
from importlib import import_module

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class WebhookAttemptNumberMigrationTest(TransactionTestCase):
    migrate_from = [("payments", "0015_webhookevent_stripe_customer_id_and_more")]
    migrate_to = [("payments", "0016_unique_webhook_attempt_number")]
    latest_target = [("payments", "0018_backfill_memberships_from_users")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.latest_target)
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


class MembershipBackfillMigrationTest(TransactionTestCase):
    accounts_target = ("accounts", "0029_privacycompletiondelivery_and_more")
    migrate_from = [
        ("payments", "0016_unique_webhook_attempt_number"),
        accounts_target,
    ]
    schema_target = [("payments", "0017_membership"), accounts_target]
    migrate_to = [
        ("payments", "0018_backfill_memberships_from_users"),
        accounts_target,
    ]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def test_backfill_copies_every_user_and_reapplies_after_reverse(self):
        OldTier = self.old_apps.get_model("payments", "Tier")
        OldUser = self.old_apps.get_model("accounts", "User")
        free = OldTier.objects.get(slug="free")
        main = OldTier.objects.get(slug="main")
        period_end = timezone.now() + timedelta(days=30)
        paid = OldUser.objects.create(
            email="legacy-paid@example.com",
            tier=main,
            pending_tier=free,
            billing_period_end=period_end,
            stripe_customer_id="cus_legacy",
            subscription_id="sub_legacy",
        )
        bare = OldUser.objects.create(email="legacy-bare@example.com", tier=None)

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.schema_target)
        schema_apps = self.executor.loader.project_state(self.schema_target).apps
        SchemaMembership = schema_apps.get_model("payments", "Membership")
        SchemaMembership.objects.create(
            user_id=paid.pk,
            tier_id=free.pk,
            stripe_customer_id="stale-customer",
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        migrated_apps = self.executor.loader.project_state(self.migrate_to).apps
        Membership = migrated_apps.get_model("payments", "Membership")

        self.assertEqual(Membership.objects.count(), OldUser.objects.count())
        paid_membership = Membership.objects.get(user_id=paid.pk)
        self.assertEqual(paid_membership.tier_id, main.pk)
        self.assertEqual(paid_membership.pending_tier_id, free.pk)
        self.assertEqual(paid_membership.billing_period_end, period_end)
        self.assertEqual(paid_membership.stripe_customer_id, "cus_legacy")
        self.assertEqual(paid_membership.subscription_id, "sub_legacy")
        self.assertIsNone(Membership.objects.get(user_id=bare.pk).tier_id)

        MigrationExecutor(connection).migrate(self.schema_target)
        schema_apps = MigrationExecutor(connection).loader.project_state(
            self.schema_target
        ).apps
        self.assertEqual(
            schema_apps.get_model("payments", "Membership").objects.count(),
            0,
        )

        MigrationExecutor(connection).migrate(self.migrate_to)
        reapplied_apps = MigrationExecutor(connection).loader.project_state(
            self.migrate_to
        ).apps
        self.assertEqual(
            reapplied_apps.get_model("payments", "Membership").objects.count(),
            OldUser.objects.count(),
        )
