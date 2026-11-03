"""Campaign pacing stays in force during failures and queue recovery."""

from datetime import timedelta
from unittest.mock import call, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from email_app.models import EmailCampaign
from email_app.services.relay_campaigns import RelayCampaignDeliveryRejected
from email_app.tasks.campaign_delivery_recovery import _enqueue_delivery_chunks
from email_app.tasks.send_campaign import (
    _get_batch_interval_seconds,
    send_campaign_batch,
)
from integrations.config import clear_config_cache
from tests.fixtures import TierSetupMixin, create_user_with_membership


class CampaignBatchIntervalTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    @override_settings(CAMPAIGN_BATCH_INTERVAL_SECONDS=0)
    def test_zero_interval_is_clamped_to_five_minutes(self):
        clear_config_cache()

        self.assertEqual(_get_batch_interval_seconds(batch_size=200), 300)

    @override_settings(CAMPAIGN_BATCH_INTERVAL_SECONDS=60)
    def test_large_batches_extend_the_interval_to_cover_their_send_time(self):
        clear_config_cache()

        self.assertEqual(_get_batch_interval_seconds(batch_size=500), 750)


class CampaignRecoveryPacingTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_recovery_batches_are_staggered(self):
        fixed_now = timezone.now()
        campaign = EmailCampaign.objects.create(
            subject="Recovery pacing",
            body="Hello",
            status="sending",
            audience_snapshotted_at=fixed_now,
        )
        with (
            patch(
                "email_app.tasks.campaign_delivery_recovery._get_batch_size",
                return_value=1,
            ),
            patch(
                "email_app.tasks.campaign_delivery_recovery.timezone.now",
                return_value=fixed_now,
            ),
            override_settings(CAMPAIGN_BATCH_INTERVAL_SECONDS=0),
        ):
            clear_config_cache()
            created = _enqueue_delivery_chunks(campaign, [101, 102, 103], set())

        from django_q.models import Schedule

        schedules = list(Schedule.objects.order_by("next_run"))
        self.assertEqual(created, 3)
        self.assertEqual(
            [schedule.next_run for schedule in schedules],
            [fixed_now + timedelta(seconds=seconds) for seconds in (0, 300, 600)],
        )


class CampaignTransportPacingTest(TierSetupMixin, TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_rejected_transport_attempts_still_wait_before_the_next_call(self):
        campaign, deliveries = self._campaign_with_deliveries()
        result, transport_count, sleeps = self._send_rejected(campaign, deliveries)

        self.assertEqual(transport_count, 3)
        self.assertEqual(result["failed_count"], 3)
        self.assertEqual(sleeps, [call(1.5)] * 3)

    def _campaign_with_deliveries(self):
        campaign = EmailCampaign.objects.create(
            subject="Pacing failures",
            body="Hello",
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        deliveries = []
        for index in range(3):
            user = create_user_with_membership(
                email=f"pacing-{index}@test.com",
                tier=self.free_tier,
                email_verified=True,
                unsubscribed=False,
            )
            deliveries.append(
                campaign.deliveries.create(
                    user=user,
                    recipient_user_pk=user.pk,
                    recipient_email=user.email,
                )
            )
        return campaign, deliveries

    def _send_rejected(self, campaign, deliveries):
        with (
            patch(
                "email_app.tasks.send_campaign._send_campaign_transport",
                side_effect=RelayCampaignDeliveryRejected(
                    "invalid recipient",
                    status_code=422,
                ),
            ) as transport,
            patch("email_app.services.campaign_pacing.time.sleep") as sleep,
        ):
            result = send_campaign_batch(
                campaign.pk,
                [delivery.pk for delivery in deliveries],
                send_delay=1.5,
            )
        return result, transport.call_count, sleep.call_args_list
