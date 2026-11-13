"""Tests for unalerted needs_attention and stuck sending campaign alerts."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.tasks.relay_campaign_watchdog import (
    DEFAULT_STALL_TIMEOUT_MINUTES,
    run_relay_campaign_watchdog,
)
from tests.fixtures import TierSetupMixin, create_user_with_membership


class CampaignAttentionAlertsTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_user_with_membership(
            email="c17-alert-test@example.com",
            email_verified=False,
        )

    def _make_campaign(self, **overrides):
        defaults = {
            "subject": "Attention Test",
            "body": "Body.",
            "status": "needs_attention",
            "relay_campaign_state": "not_queued",
            "audience_snapshotted_at": timezone.now(),
        }
        defaults.update(overrides)
        return EmailCampaign.objects.create(**defaults)

    def test_unalerted_needs_attention_campaign_alerts_once_with_summary(self):
        campaign = self._make_campaign()
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.user,
            recipient_user_pk=self.user.pk,
            recipient_email=self.user.email,
            state=CampaignDelivery.State.AMBIGUOUS,
        )

        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
                return_value=1,
            ) as mock_mail,
        ):
            first = run_relay_campaign_watchdog()
            second = run_relay_campaign_watchdog()

        self.assertEqual(first, {"checked": 1, "failed": 0})
        self.assertEqual(second, {"checked": 0, "failed": 0})
        self.assertEqual(mock_mail.call_count, 1)
        body = mock_mail.call_args.kwargs["message"]
        self.assertIn("ambiguous=1", body)
        campaign.refresh_from_db()
        self.assertIsNotNone(campaign.relay_campaign_attention_alerted_at)

    def test_failed_without_pending_deliveries_also_alerts_once(self):
        campaign = self._make_campaign(
            relay_campaign_state="failed",
            relay_campaign_error="Relay returned HTTP 403",
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.user,
            recipient_user_pk=self.user.pk,
            recipient_email=self.user.email,
            state=CampaignDelivery.State.AMBIGUOUS,
        )

        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
                return_value=1,
            ) as mock_mail,
        ):
            result = run_relay_campaign_watchdog()

        self.assertEqual(result, {"checked": 1, "failed": 0})
        self.assertEqual(mock_mail.call_count, 1)
        self.assertIn("Relay returned HTTP 403", mock_mail.call_args.kwargs["message"])

    def test_failed_alert_delivery_leaves_timestamp_unset_to_retry(self):
        campaign = self._make_campaign()

        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
                return_value=0,
            ),
        ):
            run_relay_campaign_watchdog()

        campaign.refresh_from_db()
        self.assertIsNone(campaign.relay_campaign_attention_alerted_at)

    def test_archived_needs_attention_campaign_is_ignored(self):
        self._make_campaign(is_archived=True)

        with patch(
            "email_app.services.relay_campaign_alerts.send_mail",
        ) as mock_mail:
            result = run_relay_campaign_watchdog()

        self.assertEqual(result, {"checked": 0, "failed": 0})
        mock_mail.assert_not_called()

    def test_stuck_sending_campaign_alerts_after_stall_timeout(self):
        stale = timezone.now() - timedelta(minutes=DEFAULT_STALL_TIMEOUT_MINUTES + 10)
        campaign = self._make_campaign(
            status="sending",
            relay_campaign_state="not_queued",
            relay_campaign_progress_at=stale,
        )
        delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.user,
            recipient_user_pk=self.user.pk,
            recipient_email=self.user.email,
            state=CampaignDelivery.State.PENDING,
        )
        CampaignDelivery.objects.filter(pk=delivery.pk).update(updated_at=stale)

        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
                return_value=1,
            ) as mock_mail,
        ):
            result = run_relay_campaign_watchdog()

        self.assertEqual(result, {"checked": 1, "failed": 0})
        self.assertEqual(mock_mail.call_count, 1)
        campaign.refresh_from_db()
        self.assertIsNotNone(campaign.relay_campaign_attention_alerted_at)

    def test_observation_window_sending_campaign_does_not_false_alarm(self):
        stale = timezone.now() - timedelta(minutes=DEFAULT_STALL_TIMEOUT_MINUTES + 10)
        campaign = self._make_campaign(
            status="sending",
            relay_campaign_state="not_queued",
            relay_campaign_progress_at=stale,
        )
        delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.user,
            recipient_user_pk=self.user.pk,
            recipient_email=self.user.email,
            state=CampaignDelivery.State.SENT,
        )
        CampaignDelivery.objects.filter(pk=delivery.pk).update(updated_at=stale)

        with patch(
            "email_app.services.relay_campaign_alerts.send_mail",
        ) as mock_mail:
            result = run_relay_campaign_watchdog()

        self.assertEqual(result, {"checked": 0, "failed": 0})
        mock_mail.assert_not_called()
