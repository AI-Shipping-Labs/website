"""Authoritative campaign delivery-state coverage."""

import threading
import uuid
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import ANY, patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, tag
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.campaign_dispatch import (
    CampaignDeliveryConflict,
    assume_delivery_sent,
    claim_and_enqueue_campaign,
    retry_delivery,
)
from email_app.services.email_service import EmailServiceError
from email_app.tasks.send_campaign import (
    INACTIVE_AT_SEND,
    _finalize_delivery_sent,
    refresh_campaign_status,
    send_campaign,
    send_campaign_batch,
)
from jobs.tasks import build_task_name
from payments.models import Tier
from tests.fixtures import TierSetupMixin

User = get_user_model()


class CampaignDeliveryBase(TierSetupMixin):
    def make_campaign_delivery(self, *, state=CampaignDelivery.State.PENDING):
        user = User.objects.create_user(
            email=f"recipient-{uuid.uuid4().hex}@test.com",
            tier=self.free_tier,
            email_verified=True,
            unsubscribed=False,
        )
        campaign = EmailCampaign.objects.create(
            subject="Durable campaign",
            body="Hello",
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
            state=state,
        )
        return campaign, user, delivery


@tag("core")
class CampaignClaimAndFanOutTest(CampaignDeliveryBase, TestCase):
    @patch("jobs.tasks.async_task", return_value="parent-task")
    def test_claim_is_single_use_and_non_draft_never_enqueues(self, enqueue):
        campaign = EmailCampaign.objects.create(subject="Claim once", body="Hi")

        winner = claim_and_enqueue_campaign(campaign.pk, source="test")
        loser = claim_and_enqueue_campaign(campaign.pk, source="test")

        self.assertTrue(winner.claimed)
        self.assertFalse(loser.claimed)
        enqueue.assert_called_once_with(
            "email_app.tasks.send_campaign.send_campaign",
            campaign_id=campaign.pk,
            task_name=build_task_name(
                "Send campaign",
                f"#{campaign.pk} {campaign.subject}",
                "test",
            ),
        )
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "sending")

    @patch("jobs.tasks.async_task", side_effect=RuntimeError("broker unavailable"))
    def test_enqueue_failure_rolls_claim_back_to_draft(self, enqueue):
        campaign = EmailCampaign.objects.create(subject="Retryable", body="Hi")

        with self.assertRaises(RuntimeError):
            claim_and_enqueue_campaign(campaign.pk, source="test")

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "draft")
        enqueue.assert_called_once_with(
            "email_app.tasks.send_campaign.send_campaign",
            campaign_id=campaign.pk,
            task_name=build_task_name(
                "Send campaign",
                f"#{campaign.pk} {campaign.subject}",
                "test",
            ),
        )

    def test_snapshot_is_frozen_and_parent_retry_is_a_noop(self):
        first = User.objects.create_user(
            email="frozen-first@test.com",
            tier=self.free_tier,
            email_verified=True,
        )
        campaign = EmailCampaign.objects.create(subject="Frozen", body="Hi")

        initial = send_campaign(campaign.pk, batch_size=1)
        User.objects.create_user(
            email="late-joiner@test.com",
            tier=self.free_tier,
            email_verified=True,
        )
        retried = send_campaign(campaign.pk, batch_size=1)

        self.assertEqual(initial["total"], 1)
        self.assertTrue(retried["already_snapshotted"])
        self.assertEqual(campaign.deliveries.count(), 1)
        self.assertEqual(campaign.deliveries.get().recipient_user_pk, first.pk)
        from django_q.models import Schedule

        self.assertEqual(
            Schedule.objects.filter(
                func="email_app.tasks.send_campaign.send_campaign_batch",
            ).count(),
            1,
        )

    def test_snapshot_and_schedules_roll_back_together(self):
        User.objects.create_user(
            email="rollback-recipient@test.com",
            tier=self.free_tier,
            email_verified=True,
        )
        campaign = EmailCampaign.objects.create(subject="Atomic fanout", body="Hi")

        with patch(
            "django_q.models.Schedule.objects.create",
            side_effect=RuntimeError("schedule insert failed"),
        ):
            with self.assertRaises(RuntimeError):
                send_campaign(campaign.pk, batch_size=1)

        campaign.refresh_from_db()
        self.assertIsNone(campaign.audience_snapshotted_at)
        self.assertEqual(campaign.status, "draft")
        self.assertFalse(campaign.deliveries.exists())


@tag("core")
class CampaignDeliveryFailureSemanticsTest(CampaignDeliveryBase, TestCase):
    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    def test_missing_user_is_terminally_skipped(self, transport):
        missing_campaign, missing_user, missing_delivery = (
            self.make_campaign_delivery()
        )
        missing_user.delete()

        send_campaign_batch(
            missing_campaign.pk,
            [missing_delivery.pk],
            send_delay=0,
        )

        missing_delivery.refresh_from_db()
        self.assertEqual(missing_delivery.state, CampaignDelivery.State.SKIPPED)
        self.assertEqual(missing_delivery.skip_reason, "user_missing_at_send")
        transport.assert_not_called()

    def test_merged_snapshotted_secondary_is_skipped_before_render_or_ses(self):
        from accounts.services.account_merge import merge_accounts

        campaign, secondary, delivery = self.make_campaign_delivery()
        canonical = User.objects.create_user(
            email="durable-canonical@test.com",
            tier=self.free_tier,
            email_verified=True,
        )
        merge_accounts(
            canonical,
            secondary,
            actor_label="campaign-race-test",
        )
        delivery.refresh_from_db()
        secondary.refresh_from_db()
        self.assertEqual(delivery.user_id, canonical.pk)
        self.assertEqual(delivery.recipient_user_pk, secondary.pk)
        self.assertFalse(secondary.is_active)

        with (
            patch(
                "email_app.tasks.send_campaign.render_email_markdown",
            ) as render_body,
            patch(
                "email_app.tasks.send_campaign.EmailService.prepare_rendered",
            ) as prepare,
            patch(
                "email_app.tasks.send_campaign.EmailService._build_unsubscribe_url",
            ) as build_unsubscribe_url,
            patch(
                "email_app.tasks.send_campaign.EmailService._build_verify_email_url",
            ) as build_verify_url,
            patch(
                "email_app.tasks.send_campaign.EmailService.send_prepared",
            ) as transport,
        ):
            result = send_campaign_batch(
                campaign.pk,
                delivery_ids=[delivery.pk],
                send_delay=0,
            )

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.SKIPPED)
        self.assertEqual(delivery.skip_reason, INACTIVE_AT_SEND)
        self.assertEqual(delivery.attempt_count, 0)
        self.assertEqual(result["sent_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["unsubscribed_at_send_count"], 0)
        self.assertEqual(campaign.status, "sent")
        self.assertEqual(campaign.sent_count, 0)
        self.assertIsNotNone(campaign.sent_at)
        self.assertFalse(EmailLog.objects.filter(campaign=campaign).exists())
        render_body.assert_not_called()
        prepare.assert_not_called()
        build_unsubscribe_url.assert_not_called()
        build_verify_url.assert_not_called()
        transport.assert_not_called()

    @patch(
        "email_app.tasks.send_campaign.EmailService.send_prepared",
        return_value="changed-address-message",
    )
    def test_changed_destination_is_persisted_before_transport(self, transport):
        campaign, user, delivery = self.make_campaign_delivery()
        User.objects.filter(pk=user.pk).update(email="current-destination@test.com")

        send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.SENT)
        self.assertEqual(delivery.recipient_email, "current-destination@test.com")
        self.assertEqual(
            transport.call_args.args[0].to_email,
            "current-destination@test.com",
        )

    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    @patch(
        "email_app.tasks.send_campaign.EmailService.prepare_rendered",
        side_effect=ValueError("template exploded"),
    )
    def test_pre_transport_failure_is_failed_without_ses(self, prepare, transport):
        campaign, _user, delivery = self.make_campaign_delivery()

        send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.FAILED)
        self.assertEqual(delivery.attempt_count, 0)
        self.assertFalse(EmailLog.objects.filter(campaign=campaign).exists())
        transport.assert_not_called()

    def test_definitive_ses_rejection_is_failed(self):
        campaign, _user, delivery = self.make_campaign_delivery()

        def reject(_prepared):
            try:
                raise ClientError(
                    {"Error": {"Code": "MessageRejected", "Message": "rejected"}},
                    "SendEmail",
                )
            except ClientError as exc:
                raise EmailServiceError("send failed") from exc

        with patch(
            "email_app.tasks.send_campaign.EmailService.send_prepared",
            side_effect=reject,
        ) as transport:
            send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.FAILED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertFalse(EmailLog.objects.filter(campaign=campaign).exists())
        transport.assert_called_once_with(ANY)

    def test_timeout_and_missing_message_id_are_ambiguous(self):
        outcomes = (TimeoutError("timed out"), "")
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__):
                campaign, _user, delivery = self.make_campaign_delivery()

                if isinstance(outcome, Exception):
                    def indeterminate(_prepared, cause=outcome):
                        try:
                            raise cause
                        except TimeoutError as exc:
                            raise EmailServiceError("send failed") from exc

                    side_effect = indeterminate
                    return_value = None
                else:
                    side_effect = None
                    return_value = outcome
                with patch(
                    "email_app.tasks.send_campaign.EmailService.send_prepared",
                    side_effect=side_effect,
                    return_value=return_value,
                ):
                    send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

                delivery.refresh_from_db()
                self.assertEqual(delivery.state, CampaignDelivery.State.AMBIGUOUS)
                self.assertFalse(EmailLog.objects.filter(campaign=campaign).exists())

    def test_success_then_local_crash_expires_ambiguous_without_resend(self):
        campaign, _user, delivery = self.make_campaign_delivery()

        with patch(
            "email_app.tasks.send_campaign.EmailService.send_prepared",
            return_value="accepted-by-ses",
        ) as transport, patch(
            "email_app.tasks.send_campaign._finalize_delivery_sent",
            side_effect=RuntimeError("worker died"),
        ):
            with self.assertRaises(RuntimeError):
                send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

            delivery.refresh_from_db()
            self.assertEqual(delivery.state, CampaignDelivery.State.DISPATCHING)
            CampaignDelivery.objects.filter(pk=delivery.pk).update(
                claim_expires_at=timezone.now() - timedelta(seconds=1),
            )
            send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.AMBIGUOUS)
        self.assertFalse(EmailLog.objects.filter(campaign=campaign).exists())
        transport.assert_called_once_with(ANY)

    def test_wrong_claim_token_cannot_finalize_or_log(self):
        campaign, _user, delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.DISPATCHING,
        )
        owner_token = uuid.uuid4()
        CampaignDelivery.objects.filter(pk=delivery.pk).update(claim_token=owner_token)

        finalized = _finalize_delivery_sent(
            delivery.pk,
            uuid.uuid4(),
            "must-not-be-logged",
        )

        self.assertFalse(finalized)
        self.assertFalse(EmailLog.objects.filter(campaign=campaign).exists())
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.DISPATCHING)

    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    def test_terminal_states_are_never_automatically_resent(self, transport):
        for state in (
            CampaignDelivery.State.DISPATCHING,
            CampaignDelivery.State.SENT,
            CampaignDelivery.State.SKIPPED,
            CampaignDelivery.State.FAILED,
            CampaignDelivery.State.AMBIGUOUS,
            CampaignDelivery.State.ASSUMED_SENT,
        ):
            with self.subTest(state=state):
                campaign, _user, delivery = self.make_campaign_delivery(state=state)
                if state == CampaignDelivery.State.DISPATCHING:
                    CampaignDelivery.objects.filter(pk=delivery.pk).update(
                        claim_expires_at=timezone.now() + timedelta(minutes=5),
                    )
                send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)
        transport.assert_not_called()


@tag("core")
class CampaignAggregationAndReconciliationTest(CampaignDeliveryBase, TestCase):
    def test_attention_blocks_completion_and_confirmed_count_excludes_assumed(self):
        campaign, user, sent_delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.SENT,
        )
        log = EmailLog.objects.create(
            campaign=campaign,
            user=user,
            recipient_email=user.email,
            email_type="campaign",
            ses_message_id="confirmed",
        )
        CampaignDelivery.objects.filter(pk=sent_delivery.pk).update(
            email_log=log,
            ses_message_id="confirmed",
        )
        for state in (
            CampaignDelivery.State.ASSUMED_SENT,
            CampaignDelivery.State.FAILED,
        ):
            extra = User.objects.create_user(
                email=f"aggregate-{state}@test.com",
                tier=self.free_tier,
                email_verified=True,
            )
            CampaignDelivery.objects.create(
                campaign=campaign,
                user=extra,
                recipient_user_pk=extra.pk,
                recipient_email=extra.email,
                state=state,
            )

        refresh_campaign_status(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "needs_attention")
        self.assertEqual(campaign.sent_count, 1)
        self.assertIsNone(campaign.sent_at)

        campaign.deliveries.filter(state=CampaignDelivery.State.FAILED).update(
            state=CampaignDelivery.State.SKIPPED,
        )
        refresh_campaign_status(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "sent")
        self.assertEqual(campaign.sent_count, 1)
        self.assertIsNotNone(campaign.sent_at)

    @patch("jobs.tasks.async_task", return_value="targeted-retry")
    def test_failed_retry_is_attributed_and_stale_retry_cannot_queue_twice(self, enqueue):
        _campaign, _user, delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.FAILED,
        )
        actor = User.objects.create_user(
            email="resolver@test.com",
            tier=self.free_tier,
            is_staff=True,
        )

        retry_delivery(delivery.pk, actor=actor)
        with self.assertRaises(CampaignDeliveryConflict):
            retry_delivery(delivery.pk, actor=actor)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.PENDING)
        self.assertEqual(delivery.resolution, CampaignDelivery.Resolution.RETRY)
        self.assertEqual(delivery.resolved_by, actor)
        self.assertIsNotNone(delivery.resolved_at)
        enqueue.assert_called_once_with(
            "email_app.tasks.send_campaign.send_campaign_batch",
            campaign_id=delivery.campaign_id,
            delivery_ids=[delivery.pk],
            task_name=build_task_name(
                "Retry campaign delivery",
                f"#{delivery.campaign_id} delivery {delivery.pk}",
                "Studio campaign reconciliation",
            ),
        )

    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    def test_assume_sent_never_sends_or_fabricates_log(self, transport):
        campaign, _user, delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.AMBIGUOUS,
        )
        actor = User.objects.create_user(
            email="assumer@test.com",
            tier=self.free_tier,
            is_staff=True,
        )

        assume_delivery_sent(delivery.pk, actor=actor)

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.ASSUMED_SENT)
        self.assertEqual(delivery.resolved_by, actor)
        self.assertEqual(campaign.status, "sent")
        self.assertEqual(campaign.sent_count, 0)
        self.assertFalse(EmailLog.objects.filter(campaign=campaign).exists())
        transport.assert_not_called()

    @patch(
        "email_app.tasks.send_campaign.refresh_campaign_status",
        side_effect=RuntimeError("aggregate refresh failed"),
    )
    def test_assume_sent_rolls_back_if_aggregate_refresh_fails(self, _refresh):
        campaign, _user, delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.AMBIGUOUS,
        )
        actor = User.objects.create_user(
            email="rollback-assumer@test.com",
            tier=self.free_tier,
            is_staff=True,
        )

        with self.assertRaises(RuntimeError):
            assume_delivery_sent(delivery.pk, actor=actor)

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.AMBIGUOUS)
        self.assertEqual(delivery.resolution, "")
        self.assertEqual(campaign.status, "sending")


@tag("core", "postgresql")
@skipUnless(connection.vendor == "postgresql", "requires PostgreSQL row locking")
class CampaignPostgresConcurrencyTest(CampaignDeliveryBase, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        # TransactionTestCase deliberately does not run setUpTestData, so the
        # shared TierSetupMixin cannot initialize this fixture for the real
        # multi-connection tests.
        self.free_tier = Tier.objects.get_or_create(
            slug="free",
            defaults={"name": "Free", "level": 0},
        )[0]

    @patch("jobs.tasks.async_task", return_value="parent")
    def test_simultaneous_campaign_claims_enqueue_once(self, enqueue):
        campaign = EmailCampaign.objects.create(subject="Concurrent", body="Hi")
        start = threading.Barrier(3)
        results = []
        errors = []

        def contender():
            close_old_connections()
            try:
                start.wait(timeout=5)
                results.append(
                    claim_and_enqueue_campaign(campaign.pk, source="concurrency test")
                )
            except Exception as exc:  # pragma: no cover - assertion captures it
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=contender) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(
            any(thread.is_alive() for thread in threads),
            "campaign claim worker threads did not finish",
        )
        self.assertEqual(errors, [])
        self.assertEqual(sum(result.claimed for result in results), 1)
        enqueue.assert_called_once_with(
            "email_app.tasks.send_campaign.send_campaign",
            campaign_id=campaign.pk,
            task_name=build_task_name(
                "Send campaign",
                f"#{campaign.pk} {campaign.subject}",
                "concurrency test",
            ),
        )

    def test_simultaneous_delivery_workers_call_transport_once(self):
        campaign, _user, delivery = self.make_campaign_delivery()
        start = threading.Barrier(3)
        transport_gate = threading.Barrier(2)
        errors = []

        def transport(_prepared):
            transport_gate.wait(timeout=5)
            return "one-message"

        def worker():
            close_old_connections()
            try:
                start.wait(timeout=5)
                send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)
            except Exception as exc:  # pragma: no cover - assertion captures it
                errors.append(exc)
            finally:
                connection.close()

        with patch(
            "email_app.tasks.send_campaign.EmailService.send_prepared",
            side_effect=transport,
        ) as send:
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait(timeout=5)
            transport_gate.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=10)

        self.assertFalse(
            any(thread.is_alive() for thread in threads),
            "campaign delivery worker threads did not finish",
        )
        self.assertEqual(errors, [])
        send.assert_called_once_with(ANY)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.SENT)
        self.assertEqual(EmailLog.objects.filter(campaign=campaign).count(), 1)
