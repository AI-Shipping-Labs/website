"""Tests for Studio campaign schedule visibility and unschedule action.

The detail page shows the scheduled wall-clock time in the campaign's
timezone with a cancel button (drafts only); the unschedule POST clears the
fields and refuses for non-draft campaigns.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from email_app.models import EmailCampaign

User = get_user_model()


def _future_utc():
    return timezone.now() + timedelta(days=2)


class StudioCampaignScheduleDisplayTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="schedule-staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="schedule-staff@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Scheduled Campaign",
            body="Test body",
            status="draft",
        )

    def test_detail_shows_schedule_and_cancel_button(self):
        self.campaign.scheduled_at = datetime(
            2026, 11, 5, 9, 0, tzinfo=dt_timezone.utc,
        )
        self.campaign.scheduled_timezone = "Europe/Berlin"
        self.campaign.save(update_fields=["scheduled_at", "scheduled_timezone"])

        response = self.client.get(
            reverse(
                "studio_campaign_detail", kwargs={"campaign_id": self.campaign.pk},
            ),
        )

        self.assertContains(response, 'data-testid="campaign-schedule"')
        self.assertContains(response, "2026-11-05 10:00 (Europe/Berlin)")
        self.assertContains(response, 'data-testid="unschedule-campaign-btn"')
        self.assertContains(response, "/unschedule")

    def test_detail_hides_schedule_for_unscheduled_campaign(self):
        response = self.client.get(
            reverse(
                "studio_campaign_detail", kwargs={"campaign_id": self.campaign.pk},
            ),
        )
        self.assertNotContains(response, 'data-testid="campaign-schedule"')

    def test_detail_hides_cancel_button_for_sent_campaign(self):
        self.campaign.status = "sent"
        self.campaign.scheduled_at = _future_utc()
        self.campaign.scheduled_timezone = "UTC"
        self.campaign.save(update_fields=[
            "status", "scheduled_at", "scheduled_timezone",
        ])
        response = self.client.get(
            reverse(
                "studio_campaign_detail", kwargs={"campaign_id": self.campaign.pk},
            ),
        )
        self.assertContains(response, 'data-testid="campaign-schedule"')
        self.assertNotContains(response, 'data-testid="unschedule-campaign-btn"')

    def test_send_confirm_mentions_cancelled_schedule(self):
        self.campaign.scheduled_at = _future_utc()
        self.campaign.scheduled_timezone = "UTC"
        self.campaign.save(update_fields=["scheduled_at", "scheduled_timezone"])
        response = self.client.get(
            reverse(
                "studio_campaign_detail", kwargs={"campaign_id": self.campaign.pk},
            ),
        )
        self.assertContains(response, "This also cancels the scheduled send.")


class StudioCampaignUnscheduleTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="unschedule-staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="unschedule-staff@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Unschedule Me",
            body="Test body",
            status="draft",
            scheduled_at=_future_utc(),
            scheduled_timezone="Europe/Berlin",
        )
        self.url = reverse(
            "studio_campaign_unschedule", kwargs={"campaign_id": self.campaign.pk},
        )

    def test_unschedule_clears_fields_and_redirects(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.scheduled_at)
        self.assertIsNone(self.campaign.scheduled_timezone)
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("cancelled" in str(m) for m in messages))

    def test_unschedule_without_schedule_shows_error(self):
        self.campaign.scheduled_at = None
        self.campaign.scheduled_timezone = None
        self.campaign.save(update_fields=["scheduled_at", "scheduled_timezone"])
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "draft")
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Could not unschedule" in str(m) for m in messages))

    def test_unschedule_refused_for_non_draft(self):
        self.campaign.status = "sending"
        self.campaign.save(update_fields=["status"])
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.scheduled_at)

    def test_unschedule_unknown_campaign_404(self):
        response = self.client.post(
            reverse("studio_campaign_unschedule", kwargs={"campaign_id": 99999}),
        )
        self.assertEqual(response.status_code, 404)
