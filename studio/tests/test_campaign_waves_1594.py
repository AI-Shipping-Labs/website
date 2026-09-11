"""Studio controls for monitored re-permission campaigns."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from email_app.models import CampaignDelivery, CampaignWave, EmailCampaign, EmailLog
from email_app.services.campaign_repermission import REPERMISSION_COPY, REPERMISSION_CTA
from payments.models import Tier
from tests.fixtures import create_user_with_membership

User = get_user_model()


class StudioRepermissionBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )[0]
        cls.staff = User.objects.create_user(
            email="wave-staff@example.com",
            password="pw",
            is_staff=True,
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password="pw")

    def make_campaign(self, **fields):
        values = {
            "subject": "Confirm your subscription",
            "body": "A short introduction.",
            "audience_verification": "unverified_only",
        }
        values.update(fields)
        return EmailCampaign.objects.create(**values)

    def make_user(self, suffix, **fields):
        values = {
            "email": f"{suffix}@example.com",
            "tier": self.tier,
            "email_verified": False,
            "unsubscribed": False,
        }
        values.update(fields)
        return create_user_with_membership(**values)


class StudioRepermissionAuthoringTest(StudioRepermissionBase):
    def test_create_persists_unverified_only_and_detail_shows_safety_policy(self):
        response = self.client.post(
            reverse("studio_campaign_create"),
            {
                "subject": "Confirm",
                "body": "Intro",
                "target_min_level": "0",
                "audience_verification": "unverified_only",
            },
        )
        campaign = EmailCampaign.objects.get(subject="Confirm")

        self.assertRedirects(
            response, reverse("studio_campaign_detail", args=[campaign.pk]),
        )
        detail = self.client.get(
            reverse("studio_campaign_detail", args=[campaign.pk]),
        )
        self.assertContains(detail, "Re-permission monitoring")
        self.assertContains(detail, "pilot of at most 100 recipients")
        self.assertContains(detail, "waves contain at most 250")
        self.assertContains(detail, "24 hours")
        self.assertContains(detail, "bounce rate below 2%")
        self.assertContains(detail, "zero complaints")
        self.assertContains(detail, REPERMISSION_COPY)
        self.assertContains(detail, REPERMISSION_CTA)
        self.assertContains(detail, "preview-only")

    @patch("studio.views.campaigns.EmailService.send_prepared", return_value="ses-test")
    def test_test_send_requires_existing_user_and_uses_real_consent_renderer(self, send):
        campaign = self.make_campaign()
        missing = self.client.post(
            reverse("studio_campaign_test_send", args=[campaign.pk]),
            {"test_recipients": "missing@example.com"},
        )
        self.assertContains(
            missing,
            "Re-permission test sends require an existing controlled user.",
        )
        send.assert_not_called()

        controlled = self.make_user("controlled")
        response = self.client.post(
            reverse("studio_campaign_test_send", args=[campaign.pk]),
            {"test_recipients": controlled.email},
        )

        self.assertRedirects(
            response, reverse("studio_campaign_detail", args=[campaign.pk]),
        )
        prepared = send.call_args.args[0]
        self.assertIn(REPERMISSION_COPY, prepared.full_html)
        self.assertEqual(prepared.full_html.count("/api/verify-and-subscribe"), 1)
        self.assertNotIn("/api/verify-email", prepared.full_html)
        self.assertIsNotNone(prepared.unsubscribe_url)

        controlled.email_verified = True
        controlled.save(update_fields=["email_verified"])
        rejected = self.client.post(
            reverse("studio_campaign_test_send", args=[campaign.pk]),
            {"test_recipients": controlled.email},
        )
        self.assertContains(
            rejected,
            "Re-permission test sends require an active subscribed user.",
        )
        self.assertEqual(send.call_count, 1)


class StudioRepermissionMonitoringTest(StudioRepermissionBase):
    def test_detail_shows_metrics_and_release_post_queues_next_wave(self):
        campaign = self.make_campaign(
            status="sending", audience_snapshotted_at=timezone.now(),
        )
        first = CampaignWave.objects.create(
            campaign=campaign,
            number=1,
            state=CampaignWave.State.MONITORING,
            released_at=timezone.now() - timedelta(hours=25),
            monitoring_started_at=timezone.now() - timedelta(hours=24),
        )
        second = CampaignWave.objects.create(campaign=campaign, number=2)
        sent_user = self.make_user("studio-sent")
        sent_log = EmailLog.objects.create(
            campaign=campaign,
            user=sent_user,
            recipient_email=sent_user.email,
            email_type="campaign",
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            wave=first,
            user=sent_user,
            recipient_user_pk=sent_user.pk,
            recipient_email=sent_user.email,
            state=CampaignDelivery.State.SENT,
            email_log=sent_log,
        )
        future_user = self.make_user("studio-future")
        CampaignDelivery.objects.create(
            campaign=campaign,
            wave=second,
            user=future_user,
            recipient_user_pk=future_user.pk,
            recipient_email=future_user.email,
        )

        detail = self.client.get(
            reverse("studio_campaign_detail", args=[campaign.pk]),
        )
        self.assertContains(detail, 'data-testid="campaign-waves-table"')
        self.assertContains(detail, 'data-testid="release-next-wave"')
        self.assertContains(detail, "1 confirmed sent")

        response = self.client.post(
            reverse("studio_campaign_wave_release", args=[campaign.pk]),
        )

        self.assertRedirects(
            response, reverse("studio_campaign_detail", args=[campaign.pk]),
        )
        second.refresh_from_db()
        self.assertEqual(second.state, CampaignWave.State.SENDING)
        self.assertEqual(second.released_by, self.staff)

    def test_wave_release_is_post_only_and_staff_only(self):
        campaign = self.make_campaign()
        url = reverse("studio_campaign_wave_release", args=[campaign.pk])

        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(url).status_code, 403)
