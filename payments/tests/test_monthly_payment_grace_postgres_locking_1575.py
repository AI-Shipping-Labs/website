"""PostgreSQL regressions for monthly-payment-grace row locks (#1575).

The service loads nullable ``User.tier`` relations while locking the user,
grace, or delivery row that owns each state transition. PostgreSQL rejects a
bare ``FOR UPDATE`` across those outer joins. SQLite omits the clause, so this
module runs only in the serial PostgreSQL verification lane.
"""

from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import TierOverride, User
from email_app.models import EmailLog
from payments.models import MonthlyPaymentGrace as Grace
from payments.models import MonthlyPaymentGraceDelivery as Delivery
from payments.services import monthly_payment_grace as service
from tests.fixtures import TierSetupMixin

USER_TABLE = User._meta.db_table
GRACE_TABLE = Grace._meta.db_table
DELIVERY_TABLE = Delivery._meta.db_table


def subscription():
    return {
        "id": "sub_postgres_grace",
        "customer": "cus_postgres_grace",
        "status": "past_due",
        "items": {
            "data": [
                {
                    "price": {
                        "id": "price_postgres_main_monthly",
                        "recurring": {"interval": "month", "interval_count": 1},
                    },
                },
            ],
        },
    }


def invoice():
    return {
        "id": "in_postgres_grace",
        "customer": "cus_postgres_grace",
        "subscription": "sub_postgres_grace",
        "paid": False,
        "status": "open",
        "collection_method": "charge_automatically",
        "created": 1_786_532_400,
    }


@tag("core", "postgresql")
class MonthlyPaymentGracePostgresLockingTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.main_tier.stripe_price_id_monthly = "price_postgres_main_monthly"
        cls.main_tier.save(update_fields=["stripe_price_id_monthly"])

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest(
                "FOR UPDATE over nullable outer joins is PostgreSQL-only; SQLite drops the locking clause entirely"
            )

    def make_user(self, **kwargs):
        defaults = {
            "email": "postgres-grace-member@example.com",
            "tier": self.main_tier,
            "stripe_customer_id": "cus_postgres_grace",
            "subscription_id": "sub_postgres_grace",
        }
        defaults.update(kwargs)
        return User.objects.create_user(**defaults)

    def create_grace(self, *, user=None, **kwargs):
        user = user or self.make_user()
        started_at = kwargs.pop("grace_started_at", timezone.now())
        values = {
            "user": user,
            "base_tier_at_start": self.main_tier,
            "stripe_customer_id": "cus_postgres_grace",
            "stripe_subscription_id": "sub_postgres_grace",
            "stripe_invoice_id": "in_postgres_grace",
            "livemode": False,
            "source": Grace.SOURCE_WEBHOOK,
            "interval": "month",
            "interval_count": 1,
            "grace_started_at": started_at,
            "grace_expires_at": started_at + timedelta(hours=service.GRACE_HOURS),
        }
        values.update(kwargs)
        return Grace.objects.create(**values)

    def assert_scoped_outer_join_lock(self, queries, base_table):
        matching = [
            query["sql"]
            for query in queries
            if f'FROM "{base_table}"' in query["sql"]
            and "LEFT OUTER JOIN" in query["sql"]
            and "FOR UPDATE" in query["sql"]
        ]
        self.assertTrue(
            matching,
            f"No locking outer-join query observed for {base_table}: {queries}",
        )
        for sql in matching:
            self.assertIn(f'FOR UPDATE OF "{base_table}"', sql)

    @staticmethod
    def record_delivery(delivery):
        return EmailLog.objects.create(
            user=delivery.grace.user,
            recipient_email=delivery.recipient,
            email_type=delivery.kind,
            subject=f"PostgreSQL lock test: {delivery.kind}",
        )

    @override_settings(PAYMENT_FAILURE_TEAM_EMAIL="team@aishippinglabs.com")
    def test_failure_starts_grace_and_initial_deliveries_can_be_fenced(self):
        user = self.make_user()
        with (
            patch.object(service, "process_due_deliveries"),
            patch.object(service, "_audit"),
            CaptureQueriesContext(connection) as queries,
        ):
            grace, qualification = service.start_grace_from_failure(
                invoice=invoice(),
                subscription=subscription(),
                event_id="evt_postgres_grace",
                event_created=1_786_532_400,
                livemode=False,
            )

        self.assert_scoped_outer_join_lock(queries.captured_queries, USER_TABLE)
        self.assertTrue(qualification.eligible)
        self.assertEqual(grace.status, Grace.STATUS_ACTIVE)
        self.assertEqual(grace.user_id, user.pk)
        user.refresh_from_db()
        self.assertEqual(user.tier, self.main_tier)
        deliveries = list(grace.deliveries.order_by("kind"))
        self.assertEqual(
            {delivery.kind for delivery in deliveries},
            {Delivery.KIND_FAILURE_MEMBER, Delivery.KIND_FAILURE_TEAM},
        )

        for delivery in deliveries:
            now = timezone.now()
            with CaptureQueriesContext(connection) as claim_queries:
                claimed, token = service._claim_delivery(delivery.pk, now)
            self.assert_scoped_outer_join_lock(
                claim_queries.captured_queries,
                DELIVERY_TABLE,
            )
            self.assertIn("grace", claimed._state.fields_cache)
            self.assertIn("user", claimed.grace._state.fields_cache)
            self.assertEqual(claimed.grace.user.tier, self.main_tier)

            with CaptureQueriesContext(connection) as transport_queries:
                fenced = service._begin_delivery_transport(delivery.pk, token, now)
            self.assert_scoped_outer_join_lock(
                transport_queries.captured_queries,
                DELIVERY_TABLE,
            )
            self.assertEqual(fenced.pk, delivery.pk)

    def test_paid_invoice_recovers_once_and_removes_unsent_work(self):
        user = self.make_user(tags=["stripe:lapsed", "stripe:churned"])
        grace = self.create_grace(user=user)
        for kind in (Delivery.KIND_REMINDER_MEMBER, Delivery.KIND_EXPIRED_MEMBER):
            Delivery.objects.create(grace=grace, kind=kind, recipient=user.email)

        with (
            patch.object(service, "_audit"),
            CaptureQueriesContext(connection) as queries,
        ):
            recovered = service.recover_grace(
                subscription_id="sub_postgres_grace",
                invoice_id="in_postgres_grace",
                event_id="evt_postgres_paid",
                livemode=False,
            )

        self.assert_scoped_outer_join_lock(queries.captured_queries, USER_TABLE)
        self.assertEqual(recovered.pk, grace.pk)
        recovered.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(recovered.status, Grace.STATUS_RECOVERED)
        self.assertEqual(user.tier, self.main_tier)
        self.assertIn("stripe:active", user.tags)
        self.assertIn("stripe:plan-main", user.tags)
        self.assertFalse(recovered.deliveries.exists())
        self.assertIsNone(
            service.recover_grace(
                subscription_id="sub_postgres_grace",
                invoice_id="in_postgres_grace",
                event_id="evt_postgres_paid_repeat",
                livemode=False,
            )
        )

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce")
    def test_reminder_sweep_locks_grace_and_sends_once(self):
        now = timezone.now()
        grace = self.create_grace(
            grace_started_at=now - timedelta(days=5),
            grace_expires_at=now + timedelta(hours=service.REMINDER_HOURS),
            policy_enforced_at=now - timedelta(days=6),
        )
        with (
            patch.object(
                service,
                "_revalidate",
                return_value=(subscription(), invoice(), "ok", ""),
            ),
            patch.object(service, "_send_delivery", side_effect=self.record_delivery) as send,
            patch.object(service, "_audit"),
            CaptureQueriesContext(connection) as queries,
        ):
            self.assertEqual(service.sweep_payment_graces(now=now), 1)

        self.assert_scoped_outer_join_lock(queries.captured_queries, GRACE_TABLE)
        self.assert_scoped_outer_join_lock(queries.captured_queries, DELIVERY_TABLE)
        delivery = grace.deliveries.get(kind=Delivery.KIND_REMINDER_MEMBER)
        self.assertEqual(delivery.status, Delivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(send.call_count, 1)

        with (
            patch.object(
                service,
                "_revalidate",
                return_value=(subscription(), invoice(), "ok", ""),
            ),
            patch.object(service, "_send_delivery", side_effect=self.record_delivery) as repeat,
        ):
            service.sweep_payment_graces(now=now + timedelta(minutes=15))
        repeat.assert_not_called()
        self.assertEqual(
            grace.deliveries.filter(kind=Delivery.KIND_REMINDER_MEMBER).count(),
            1,
        )

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce")
    def test_expiry_sweep_changes_base_only_and_preserves_courtesy_access(self):
        user = self.make_user(tags=["stripe:active", "stripe:plan-main"])
        override = TierOverride.objects.create(
            user=user,
            original_tier=self.main_tier,
            override_tier=self.premium_tier,
            expires_at=timezone.now() + timedelta(days=30),
            source="staff:postgres-lock-test",
        )
        now = timezone.now()
        grace = self.create_grace(
            user=user,
            grace_started_at=now - timedelta(days=7),
            grace_expires_at=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        with (
            patch.object(
                service,
                "_revalidate",
                return_value=(subscription(), invoice(), "ok", ""),
            ),
            patch.object(service, "_send_delivery", side_effect=self.record_delivery),
            patch.object(service, "_audit"),
            CaptureQueriesContext(connection) as queries,
        ):
            self.assertEqual(service.sweep_payment_graces(now=now), 1)

        self.assert_scoped_outer_join_lock(queries.captured_queries, GRACE_TABLE)
        self.assert_scoped_outer_join_lock(queries.captured_queries, USER_TABLE)
        self.assert_scoped_outer_join_lock(queries.captured_queries, DELIVERY_TABLE)
        grace.refresh_from_db()
        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(grace.status, Grace.STATUS_EXPIRED)
        self.assertEqual(user.tier, self.free_tier)
        self.assertEqual(user.subscription_id, "sub_postgres_grace")
        self.assertTrue(override.is_active)
        self.assertEqual(service._effective_tier(user), self.premium_tier)
        delivery = grace.deliveries.get(kind=Delivery.KIND_EXPIRED_MEMBER)
        self.assertEqual(delivery.status, Delivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 1)

    def test_reclaimed_delivery_rejects_stale_transport_token(self):
        grace = self.create_grace()
        delivery = Delivery.objects.create(
            grace=grace,
            kind=Delivery.KIND_FAILURE_MEMBER,
            recipient=grace.user.email,
        )
        first_now = timezone.now()
        with CaptureQueriesContext(connection) as first_claim_queries:
            _, stale_token = service._claim_delivery(delivery.pk, first_now)
        self.assert_scoped_outer_join_lock(
            first_claim_queries.captured_queries,
            DELIVERY_TABLE,
        )

        reclaimed_at = first_now + service.CLAIM_TIMEOUT + timedelta(seconds=1)
        with CaptureQueriesContext(connection) as reclaim_queries:
            _, current_token = service._claim_delivery(delivery.pk, reclaimed_at)
        self.assert_scoped_outer_join_lock(
            reclaim_queries.captured_queries,
            DELIVERY_TABLE,
        )
        self.assertNotEqual(stale_token, current_token)

        with CaptureQueriesContext(connection) as stale_queries:
            self.assertIsNone(
                service._begin_delivery_transport(
                    delivery.pk,
                    stale_token,
                    reclaimed_at,
                )
            )
        self.assert_scoped_outer_join_lock(
            stale_queries.captured_queries,
            DELIVERY_TABLE,
        )

        with CaptureQueriesContext(connection) as current_queries:
            fenced = service._begin_delivery_transport(
                delivery.pk,
                current_token,
                reclaimed_at,
            )
        self.assert_scoped_outer_join_lock(
            current_queries.captured_queries,
            DELIVERY_TABLE,
        )
        self.assertEqual(fenced.pk, delivery.pk)
        delivery.refresh_from_db()
        self.assertEqual(delivery.claim_token, current_token)
        self.assertEqual(delivery.transport_started_at, reclaimed_at)
        self.assertEqual(delivery.attempt_count, 2)
