"""Tests for the campaign 25 false-sent delivery restoration migration."""

from importlib import import_module

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog

_MIGRATION = import_module("email_app.migrations.0036_restore_campaign_25_false_sent_deliveries")


class RestoreCampaign25MigrationTest(TestCase):
    def test_restores_only_campaign_25_empty_ses_sent_deliveries_and_logs(self):
        now = timezone.now()
        campaign_25 = EmailCampaign.objects.create(
            pk=25,
            subject="Campaign 25",
            body="Body",
            status="sent",
        )
        other_campaign = EmailCampaign.objects.create(
            pk=24,
            subject="Campaign 24",
            body="Body",
            status="needs_attention",
        )

        real_log = EmailLog.objects.create(
            campaign=campaign_25,
            recipient_email="real@example.com",
            email_type="campaign",
            subject="Campaign 25",
            ses_message_id="ses-real-1",
        )
        real_sent = CampaignDelivery.objects.create(
            campaign=campaign_25,
            recipient_user_pk=101,
            recipient_email="real@example.com",
            state=CampaignDelivery.State.SENT,
            ses_message_id="ses-real-1",
            email_log=real_log,
            completed_at=now,
        )
        skipped = CampaignDelivery.objects.create(
            campaign=campaign_25,
            recipient_user_pk=102,
            recipient_email="skipped@example.com",
            state=CampaignDelivery.State.SKIPPED,
            skip_reason="skipped_at_relay",
            completed_at=now,
        )
        false_log = EmailLog.objects.create(
            campaign=campaign_25,
            recipient_email="false1@example.com",
            email_type="campaign",
            subject="Campaign 25",
            ses_message_id="",
        )
        false_sent_with_log = CampaignDelivery.objects.create(
            campaign=campaign_25,
            recipient_user_pk=103,
            recipient_email="false1@example.com",
            state=CampaignDelivery.State.SENT,
            ses_message_id="",
            email_log=false_log,
            completed_at=now,
        )
        false_sent_no_log = CampaignDelivery.objects.create(
            campaign=campaign_25,
            recipient_user_pk=104,
            recipient_email="false2@example.com",
            state=CampaignDelivery.State.SENT,
            ses_message_id="",
            email_log=None,
            completed_at=now,
        )
        other_log = EmailLog.objects.create(
            campaign=other_campaign,
            recipient_email="other@example.com",
            email_type="campaign",
            subject="Campaign 24",
            ses_message_id="",
        )
        other_delivery = CampaignDelivery.objects.create(
            campaign=other_campaign,
            recipient_user_pk=105,
            recipient_email="other@example.com",
            state=CampaignDelivery.State.SENT,
            ses_message_id="",
            email_log=other_log,
            completed_at=now,
        )

        _MIGRATION.restore_campaign_25_false_sent_deliveries(apps, None)
        # Re-run to verify idempotency.
        _MIGRATION.restore_campaign_25_false_sent_deliveries(apps, None)

        real_sent.refresh_from_db()
        skipped.refresh_from_db()
        false_sent_with_log.refresh_from_db()
        false_sent_no_log.refresh_from_db()
        other_delivery.refresh_from_db()
        campaign_25.refresh_from_db()

        self.assertEqual(real_sent.state, CampaignDelivery.State.SENT)
        self.assertEqual(real_sent.email_log_id, real_log.pk)
        self.assertEqual(skipped.state, CampaignDelivery.State.SKIPPED)

        self.assertEqual(false_sent_with_log.state, CampaignDelivery.State.PENDING)
        self.assertIsNone(false_sent_with_log.email_log_id)
        self.assertIsNone(false_sent_with_log.completed_at)
        self.assertEqual(false_sent_no_log.state, CampaignDelivery.State.PENDING)
        self.assertIsNone(false_sent_no_log.completed_at)

        self.assertFalse(EmailLog.objects.filter(pk=false_log.pk).exists())
        self.assertTrue(EmailLog.objects.filter(pk=real_log.pk).exists())

        self.assertEqual(other_delivery.state, CampaignDelivery.State.SENT)
        self.assertEqual(other_delivery.email_log_id, other_log.pk)
        self.assertEqual(campaign_25.status, "needs_attention")
