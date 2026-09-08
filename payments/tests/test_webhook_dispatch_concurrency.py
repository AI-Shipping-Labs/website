"""Concurrency contracts for Stripe webhook attempt allocation and dispatch."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from unittest.mock import patch

from django.db import (
    OperationalError,
    close_old_connections,
    connection,
    transaction,
)
from django.test import TransactionTestCase, tag

from accounts.models import User
from payments.exceptions import WebhookUnmatchedUserError
from payments.models import StripeWebhookDeliveryAttempt, Tier, WebhookEvent
from payments.services import webhook_dispatch
from payments.services.webhook_dispatch import process_event


@tag("core")
class WebhookDispatchConcurrencyTest(TransactionTestCase):
    """Two workers share one durable attempt sequence and processing claim."""

    event_type = "customer.subscription.deleted"
    event_obj = {"id": "sub_concurrent", "customer": "cus_concurrent"}

    def setUp(self):
        super().setUp()
        Tier.objects.get_or_create(
            slug="free",
            defaults={"name": "Free", "level": 0},
        )
        Tier.objects.get_or_create(
            slug="main",
            defaults={"name": "Main", "level": 20},
        )

    def _deliver_concurrently(self, event_id):
        barrier = Barrier(2)

        def deliver():
            barrier.wait(timeout=10)
            for retry in range(4):
                close_old_connections()
                try:
                    kwargs = {
                        "event_id": event_id,
                        "event_type": self.event_type,
                        "obj": self.event_obj,
                        "livemode": False,
                    }
                    if connection.vendor == "sqlite":
                        # Roll back the whole simulated delivery when SQLite's
                        # table lock raises. Retrying must not turn two worker
                        # deliveries into a third committed attempt. PostgreSQL
                        # exercises the production transaction boundaries.
                        with transaction.atomic():
                            return process_event(**kwargs)
                    return process_event(**kwargs)
                except OperationalError:
                    if retry == 3:
                        raise
                    # SQLite may surface its database-wide writer lock instead
                    # of waiting as PostgreSQL does for select_for_update.
                    time.sleep(0.05 * (retry + 1))
                finally:
                    close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(deliver) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_terminal_winner_runs_handler_once_and_loser_short_circuits(self):
        member = User.objects.create_user(
            email="concurrent-webhook@test.com",
            stripe_customer_id=self.event_obj["customer"],
            subscription_id=self.event_obj["id"],
            tier=Tier.objects.get(slug="main"),
        )
        with patch(
            "payments.services.webhook_dispatch.run_handler",
            wraps=webhook_dispatch.run_handler,
        ) as run_handler, patch(
            "payments.services._community_remove",
        ) as community_remove:
            results = self._deliver_concurrently("evt_concurrent_terminal")

        attempts = list(
            StripeWebhookDeliveryAttempt.objects.filter(
                stripe_event_id="evt_concurrent_terminal",
            ).order_by("attempt_number")
        )
        self.assertEqual([attempt.attempt_number for attempt in attempts], [1, 2])
        self.assertEqual(
            [attempt.outcome for attempt in attempts],
            [
                StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED,
                StripeWebhookDeliveryAttempt.OUTCOME_ALREADY_PROCESSED,
            ],
        )
        self.assertCountEqual(
            results,
            [
                (StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED, 200),
                (StripeWebhookDeliveryAttempt.OUTCOME_ALREADY_PROCESSED, 200),
            ],
        )
        processed_attempt = next(
            attempt
            for attempt in attempts
            if attempt.outcome == StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED
        )
        run_handler.assert_called_once_with(
            self.event_type,
            self.event_obj,
            event_context={
                "event_id": "evt_concurrent_terminal",
                "created": None,
                "livemode": False,
            },
            attempt=processed_attempt,
        )
        self.assertEqual(
            WebhookEvent.objects.filter(
                stripe_event_id="evt_concurrent_terminal",
            ).count(),
            1,
        )
        member.refresh_from_db()
        self.assertEqual(member.tier.slug, "free")
        self.assertEqual(member.subscription_id, "")
        community_remove.assert_called_once_with(member)

    def test_retryable_winner_releases_claim_for_waiting_delivery(self):
        calls = 0
        calls_lock = Lock()

        def retryable_then_terminal(*args, **kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
                invocation = calls
            if invocation == 1:
                raise WebhookUnmatchedUserError("user not available yet")
            return StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED

        with patch(
            "payments.services.webhook_dispatch.run_handler",
            side_effect=retryable_then_terminal,
        ):
            results = self._deliver_concurrently("evt_concurrent_retryable")

        attempts = list(
            StripeWebhookDeliveryAttempt.objects.filter(
                stripe_event_id="evt_concurrent_retryable",
            ).order_by("attempt_number")
        )
        self.assertEqual([attempt.attempt_number for attempt in attempts], [1, 2])
        self.assertCountEqual(
            [attempt.outcome for attempt in attempts],
            [
                StripeWebhookDeliveryAttempt.OUTCOME_UNMATCHED_USER,
                StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED,
            ],
        )
        self.assertCountEqual(
            results,
            [
                (StripeWebhookDeliveryAttempt.OUTCOME_UNMATCHED_USER, 500),
                (StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED, 200),
            ],
        )
        self.assertEqual(calls, 2)
        self.assertEqual(
            WebhookEvent.objects.filter(
                stripe_event_id="evt_concurrent_retryable",
            ).count(),
            1,
        )

    def test_operator_replay_uses_same_sequence_and_terminal_claim(self):
        operator = User.objects.create_user(
            email="webhook-replay@test.com",
            is_staff=True,
        )
        with patch(
            "payments.services.webhook_dispatch.run_handler",
            return_value=StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED,
        ) as run_handler:
            first = process_event(
                event_id="evt_operator_sequence",
                event_type=self.event_type,
                obj=self.event_obj,
                livemode=False,
            )
            replay = process_event(
                event_id="evt_operator_sequence",
                event_type=self.event_type,
                obj=self.event_obj,
                livemode=False,
                source=StripeWebhookDeliveryAttempt.SOURCE_OPERATOR_REPLAY,
                requested_by=operator,
            )

        attempts = list(
            StripeWebhookDeliveryAttempt.objects.filter(
                stripe_event_id="evt_operator_sequence",
            ).order_by("attempt_number")
        )
        self.assertEqual(first, (StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED, 200))
        self.assertEqual(
            replay,
            (StripeWebhookDeliveryAttempt.OUTCOME_ALREADY_PROCESSED, 200),
        )
        self.assertEqual([attempt.attempt_number for attempt in attempts], [1, 2])
        self.assertEqual(
            [attempt.source for attempt in attempts],
            [
                StripeWebhookDeliveryAttempt.SOURCE_STRIPE_DELIVERY,
                StripeWebhookDeliveryAttempt.SOURCE_OPERATOR_REPLAY,
            ],
        )
        self.assertEqual(attempts[1].requested_by, operator)
        run_handler.assert_called_once_with(
            self.event_type,
            self.event_obj,
            event_context={
                "event_id": "evt_operator_sequence",
                "created": None,
                "livemode": False,
            },
            attempt=attempts[0],
        )
