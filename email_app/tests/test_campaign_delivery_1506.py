"""Campaign delivery recovery, freeze, and attempt-cap coverage (#1506)."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from django_q.models import Schedule

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.email_service import UNSUBSCRIBED_AT_SEND, EmailServiceError
from email_app.tasks.campaign_delivery_recovery import (
    BATCH_FUNC,
    recover_campaign_deliveries,
)
from email_app.tasks.send_campaign import (
    DEFAULT_MAX_DELIVERY_ATTEMPTS,
    get_max_delivery_attempts,
    refresh_campaign_status,
    send_campaign,
    send_campaign_batch,
)
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag("core")
class CampaignDelivery1506Base(TierSetupMixin, TestCase):
    def make_campaign_delivery(self, *, state=CampaignDelivery.State.PENDING, **fields):
        user = User.objects.create_user(
            email=f"recipient-{uuid.uuid4().hex}@test.com",
            tier=self.free_tier,
            email_verified=True,
            unsubscribed=False,
        )
        campaign = EmailCampaign.objects.create(
            subject="Recovery campaign",
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
            **fields,
        )
        return campaign, user, delivery

    def _batch_schedules(self):
        return list(Schedule.objects.filter(func=BATCH_FUNC).order_by("pk"))


class CampaignStatusDerivationTest(CampaignDelivery1506Base):
    def test_pending_keeps_sending_even_when_another_row_failed(self):
        campaign, _user, failed = self.make_campaign_delivery(
            state=CampaignDelivery.State.FAILED,
        )
        extra = User.objects.create_user(
            email="still-pending@test.com",
            tier=self.free_tier,
            email_verified=True,
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=extra,
            recipient_user_pk=extra.pk,
            recipient_email=extra.email,
            state=CampaignDelivery.State.PENDING,
        )

        refresh_campaign_status(campaign.pk)

        campaign.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(failed.state, CampaignDelivery.State.FAILED)
        self.assertEqual(campaign.status, "sending")
        self.assertIsNone(campaign.sent_at)

    def test_failed_and_ambiguous_leave_sending_and_clear_sent_at(self):
        campaign, _user, failed = self.make_campaign_delivery(
            state=CampaignDelivery.State.FAILED,
        )
        extra = User.objects.create_user(
            email="ambiguous@test.com",
            tier=self.free_tier,
            email_verified=True,
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=extra,
            recipient_user_pk=extra.pk,
            recipient_email=extra.email,
            state=CampaignDelivery.State.AMBIGUOUS,
        )
        EmailCampaign.objects.filter(pk=campaign.pk).update(
            sent_at=timezone.now(),
            sent_count=4,
        )

        refresh_campaign_status(campaign.pk)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "needs_attention")
        self.assertIsNone(campaign.sent_at)
        self.assertEqual(campaign.sent_count, 0)

    def test_sent_requires_every_row_sent_skipped_or_assumed(self):
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
        CampaignDelivery.objects.filter(pk=sent_delivery.pk).update(email_log=log)
        extra = User.objects.create_user(
            email="assumed@test.com",
            tier=self.free_tier,
            email_verified=True,
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=extra,
            recipient_user_pk=extra.pk,
            recipient_email=extra.email,
            state=CampaignDelivery.State.ASSUMED_SENT,
        )

        refresh_campaign_status(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "sent")
        self.assertEqual(campaign.sent_count, 1)
        self.assertIsNotNone(campaign.sent_at)

    def test_sent_count_excludes_assumed_failed_and_skipped(self):
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
        CampaignDelivery.objects.filter(pk=sent_delivery.pk).update(email_log=log)
        for state in (
            CampaignDelivery.State.ASSUMED_SENT,
            CampaignDelivery.State.FAILED,
            CampaignDelivery.State.SKIPPED,
        ):
            extra = User.objects.create_user(
                email=f"{state}@test.com",
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
        self.assertEqual(campaign.sent_count, 1)
        self.assertEqual(campaign.status, "needs_attention")

    def test_refresh_does_not_query_live_audience(self):
        campaign, _user, _delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.FAILED,
        )
        with patch.object(
            EmailCampaign,
            "get_eligible_recipients",
        ) as eligible:
            refresh_campaign_status(campaign.pk)
        eligible.assert_not_called()


class CampaignAudienceFreezeTest(CampaignDelivery1506Base):
    def test_late_joiner_is_not_added_on_parent_retry_or_recovery(self):
        first = User.objects.create_user(
            email="first-frozen@test.com",
            tier=self.free_tier,
            email_verified=True,
        )
        campaign = EmailCampaign.objects.create(subject="Frozen", body="Hi")
        send_campaign(campaign.pk, batch_size=10)
        User.objects.create_user(
            email="late-joiner@test.com",
            tier=self.free_tier,
            email_verified=True,
        )

        retried = send_campaign(campaign.pk, batch_size=10)
        recover_campaign_deliveries()

        self.assertTrue(retried["already_snapshotted"])
        self.assertEqual(campaign.deliveries.count(), 1)
        self.assertEqual(campaign.deliveries.get().recipient_user_pk, first.pk)
        self.assertFalse(
            campaign.deliveries.filter(
                recipient_email="late-joiner@test.com",
            ).exists()
        )

    def test_unsubscribed_snapshot_is_skipped_on_the_same_row(self):
        campaign, user, delivery = self.make_campaign_delivery()
        original_pk = delivery.pk
        User.objects.filter(pk=user.pk).update(unsubscribed=True)

        send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(campaign.deliveries.count(), 1)
        self.assertEqual(delivery.pk, original_pk)
        self.assertEqual(delivery.state, CampaignDelivery.State.SKIPPED)
        self.assertEqual(delivery.skip_reason, UNSUBSCRIBED_AT_SEND)
        self.assertEqual(campaign.status, "sent")
        self.assertEqual(campaign.sent_count, 0)


class CampaignAttemptCapAndRecoveryTest(CampaignDelivery1506Base):
    def tearDown(self):
        IntegrationSetting.objects.filter(
            key="CAMPAIGN_DELIVERY_MAX_ATTEMPTS",
        ).delete()
        clear_config_cache()
        super().tearDown()

    def test_invalid_max_attempts_fall_back_to_three(self):
        for raw in ("", "nope", "0", "-2"):
            with self.subTest(raw=raw):
                IntegrationSetting.objects.update_or_create(
                    key="CAMPAIGN_DELIVERY_MAX_ATTEMPTS",
                    defaults={"value": raw, "group": "ses"},
                )
                clear_config_cache()
                self.assertEqual(
                    get_max_delivery_attempts(),
                    DEFAULT_MAX_DELIVERY_ATTEMPTS,
                )
        IntegrationSetting.objects.filter(
            key="CAMPAIGN_DELIVERY_MAX_ATTEMPTS",
        ).delete()
        clear_config_cache()

    def test_automatic_retry_queues_failed_below_cap_without_operator_fields(self):
        campaign, _user, delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.FAILED,
            attempt_count=1,
            last_error="SES definitively rejected the request.",
        )

        result = recover_campaign_deliveries()

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(result["automatic_retries"], 1)
        self.assertEqual(delivery.state, CampaignDelivery.State.PENDING)
        self.assertIsNone(delivery.resolved_by_id)
        self.assertEqual(delivery.resolution, "")
        self.assertIn("automatic retry 2/3", delivery.last_error)
        self.assertEqual(len(self._batch_schedules()), 1)
        self.assertEqual(campaign.status, "sending")

    def test_automatic_retry_stops_at_cap_and_leaves_needs_attention(self):
        campaign, _user, delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.FAILED,
            attempt_count=3,
            last_error="SES definitively rejected the request.",
        )

        result = recover_campaign_deliveries()

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(result["automatic_retries"], 0)
        self.assertEqual(delivery.state, CampaignDelivery.State.FAILED)
        self.assertEqual(len(self._batch_schedules()), 0)
        self.assertEqual(campaign.status, "needs_attention")

    def test_ambiguous_is_never_auto_retried(self):
        campaign, _user, delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.AMBIGUOUS,
            attempt_count=1,
        )

        with patch(
            "email_app.tasks.send_campaign.EmailService.send_prepared",
        ) as transport:
            recover_campaign_deliveries()

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.AMBIGUOUS)
        self.assertEqual(len(self._batch_schedules()), 0)
        self.assertEqual(campaign.status, "needs_attention")
        transport.assert_not_called()

    def test_expired_dispatching_becomes_ambiguous_without_ses(self):
        campaign, _user, delivery = self.make_campaign_delivery(
            state=CampaignDelivery.State.DISPATCHING,
            attempt_count=1,
        )
        CampaignDelivery.objects.filter(pk=delivery.pk).update(
            claim_expires_at=timezone.now() - timedelta(seconds=1),
            claim_token=uuid.uuid4(),
        )

        with patch(
            "email_app.tasks.send_campaign.EmailService.send_prepared",
        ) as transport:
            recover_campaign_deliveries()

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.AMBIGUOUS)
        self.assertEqual(campaign.status, "needs_attention")
        transport.assert_not_called()
        self.assertEqual(len(self._batch_schedules()), 0)

    def test_orphan_pending_reenqueue_is_idempotent(self):
        campaign, _user, delivery = self.make_campaign_delivery()

        first = recover_campaign_deliveries()
        second = recover_campaign_deliveries()

        delivery.refresh_from_db()
        self.assertEqual(first["requeued_batches"], 1)
        self.assertEqual(second["requeued_batches"], 0)
        self.assertEqual(len(self._batch_schedules()), 1)
        self.assertEqual(delivery.state, CampaignDelivery.State.PENDING)
        self.assertEqual(campaign.deliveries.count(), 1)

    def test_live_schedule_is_not_double_enqueued(self):
        campaign, _user, delivery = self.make_campaign_delivery()
        Schedule.objects.create(
            name="existing-batch",
            func=BATCH_FUNC,
            schedule_type=Schedule.ONCE,
            repeats=1,
            next_run=timezone.now(),
            kwargs={
                "campaign_id": campaign.pk,
                "delivery_ids": [delivery.pk],
            },
        )

        recover_campaign_deliveries()

        self.assertEqual(len(self._batch_schedules()), 1)

    def test_definitive_rejection_is_failed_and_timeout_is_ambiguous(self):
        campaign, _user, failed_delivery = self.make_campaign_delivery()

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
        ):
            send_campaign_batch(campaign.pk, [failed_delivery.pk], send_delay=0)

        failed_delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(failed_delivery.state, CampaignDelivery.State.FAILED)
        self.assertEqual(campaign.status, "needs_attention")

        timeout_campaign, _timeout_user, timeout_delivery = self.make_campaign_delivery()

        def timeout(_prepared):
            try:
                raise TimeoutError("timed out")
            except TimeoutError as exc:
                raise EmailServiceError("send failed") from exc

        with patch(
            "email_app.tasks.send_campaign.EmailService.send_prepared",
            side_effect=timeout,
        ):
            send_campaign_batch(
                timeout_campaign.pk, [timeout_delivery.pk], send_delay=0,
            )

        timeout_delivery.refresh_from_db()
        timeout_campaign.refresh_from_db()
        self.assertEqual(timeout_delivery.state, CampaignDelivery.State.AMBIGUOUS)
        self.assertEqual(timeout_campaign.status, "needs_attention")
        recover_campaign_deliveries()
        timeout_delivery.refresh_from_db()
        self.assertEqual(timeout_delivery.state, CampaignDelivery.State.AMBIGUOUS)
