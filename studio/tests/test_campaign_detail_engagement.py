"""Tests for campaign detail email engagement metrics."""

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

        response = self.client.get(
            reverse("studio_campaign_detail", args=[self.campaign.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sent: 100")
        self.assertContains(response, "Opened: 60 (60.0%)")
        self.assertContains(response, "Clicked: 25 (25.0%)")

    def test_campaign_detail_handles_zero_sends(self):
        response = self.client.get(
            reverse("studio_campaign_detail", args=[self.campaign.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sent: 0")
        self.assertContains(response, "Opened: 0 (0.0%)")
        self.assertContains(response, "Clicked: 0 (0.0%)")
