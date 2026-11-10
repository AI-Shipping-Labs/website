"""Tests for campaign detail email engagement metrics."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from email_app.models import EmailCampaign, EmailLog

User = get_user_model()


class CampaignDetailEngagementTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff-engagement@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff-engagement@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Engagement Campaign",
            body="Hello",
            status="sent",
        )

    def test_campaign_detail_shows_sent_open_click_counts(self):
        now = timezone.now()
        logs = []
        for index in range(100):
            user = User.objects.create_user(email=f"recipient-{index}@test.com")
            logs.append(
                EmailLog(
                    campaign=self.campaign,
                    user=user,
                    email_type="campaign",
                    ses_message_id=f"ses-{index}",
                    opened_at=now if index < 60 else None,
                    clicked_at=now if index < 25 else None,
                ),
            )
        EmailLog.objects.bulk_create(logs)

        stats = {
            "sent_count": 83,
            "delivered_count": 82,
            "open_count": 50,
            "unique_open_count": 40,
            "click_count": 18,
            "unique_click_count": 15,
            "unsubscribe_count": 2,
            "bounce_count": 1,
            "complaint_count": 0,
        }
        self.campaign.relay_campaign_key = "aishl-campaign-test"
        self.campaign.relay_campaign_keys = ["aishl-campaign-test"]
        self.campaign.relay_campaign_stats = stats
        self.campaign.save(update_fields=[
            "relay_campaign_key", "relay_campaign_keys", "relay_campaign_stats",
        ])

        with patch(
            "studio.views.campaigns.refresh_campaign_relay_stats_loud",
        ) as refresh:
            response = self.client.get(
                reverse("studio_campaign_detail", args=[self.campaign.pk]),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(refresh.call_count, 1)
        self.assertContains(response, "Relay sent count")
        self.assertContains(response, "Sent: 83")
        self.assertContains(response, "Delivered: 82")
        self.assertContains(response, "Opens: 40 unique, 50 total")
        self.assertContains(response, "Clicks: 15 unique, 18 total")
        self.assertContains(response, "Unsubscribes: 2")
        self.assertContains(response, "Bounces: 1")
        self.assertContains(response, "Complaints: 0")
        self.assertNotContains(response, "Sent: 100")

    def test_campaign_detail_handles_zero_sends(self):
        self.campaign.relay_campaign_key = "aishl-campaign-zero"
        self.campaign.relay_campaign_keys = ["aishl-campaign-zero"]
        self.campaign.relay_campaign_stats = {
            "sent_count": 0,
            "delivered_count": 0,
            "open_count": 0,
            "unique_open_count": 0,
            "click_count": 0,
            "unique_click_count": 0,
            "unsubscribe_count": 0,
            "bounce_count": 0,
            "complaint_count": 0,
        }
        self.campaign.save(update_fields=[
            "relay_campaign_key", "relay_campaign_keys", "relay_campaign_stats",
        ])
        with patch("studio.views.campaigns.refresh_campaign_relay_stats_loud"):
            response = self.client.get(
                reverse("studio_campaign_detail", args=[self.campaign.pk]),
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sent: 0")
        self.assertContains(response, "Delivered: 0")
        self.assertContains(response, "Opens: 0 unique, 0 total")
        self.assertContains(response, "Clicks: 0 unique, 0 total")
        self.assertContains(response, "Unsubscribes: 0")
        self.assertContains(response, "Bounces: 0")
        self.assertContains(response, "Complaints: 0")
