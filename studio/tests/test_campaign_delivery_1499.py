"""Studio reconciliation and durable delivery visibility for issue #1499."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag("core")
class CampaignDeliveryStudioTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email="campaign-resolver@test.com",
            password="pw",
            tier=cls.free_tier,
            is_staff=True,
        )
        cls.non_staff = User.objects.create_user(
            email="campaign-member@test.com",
            password="pw",
            tier=cls.free_tier,
        )
        cls.recipient = User.objects.create_user(
            email="ambiguous-recipient@test.com",
            tier=cls.free_tier,
            email_verified=True,
        )

    def setUp(self):
        self.campaign = EmailCampaign.objects.create(
            subject="Attention campaign",
            body="Hi",
            status="needs_attention",
            audience_snapshotted_at=timezone.now(),
        )
        self.delivery = CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=self.recipient,
            recipient_user_pk=self.recipient.pk,
            recipient_email=self.recipient.email,
            state=CampaignDelivery.State.AMBIGUOUS,
            attempt_count=1,
            last_error="Transport outcome indeterminate.",
            completed_at=timezone.now(),
        )
        self.client.force_login(self.staff)

    def test_detail_and_attention_list_show_durable_breakdown_and_controls(self):
        detail = self.client.get(
            reverse("studio_campaign_detail", args=[self.campaign.pk]),
        )
        recipients = self.client.get(
            reverse("studio_campaign_recipients", args=[self.campaign.pk]),
            {"attention": "1"},
        )

        self.assertContains(detail, "Needs attention")
        self.assertContains(detail, "Ambiguous")
        self.assertContains(detail, "Review 1 recipient needing attention")
        self.assertContains(recipients, self.recipient.email)
        self.assertContains(recipients, "Transport outcome indeterminate.")
        self.assertContains(recipients, "Assume delivered")

    @patch("jobs.tasks.async_task", return_value="targeted")
    def test_retry_post_is_attributed_and_stale_post_does_not_queue_twice(self, enqueue):
        url = reverse(
            "studio_campaign_delivery_resolve",
            args=[self.campaign.pk, self.delivery.pk],
        )

        first = self.client.post(url, {"action": "retry"})
        second = self.client.post(url, {"action": "retry"})

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.PENDING)
        self.assertEqual(self.delivery.resolved_by, self.staff)
        enqueue.assert_called_once_with(
            "email_app.tasks.send_campaign.send_campaign_batch",
            campaign_id=self.campaign.pk,
            delivery_ids=[self.delivery.pk],
            task_name=(
                f"Retry campaign delivery: #{self.campaign.pk} delivery "
                f"{self.delivery.pk} from Studio campaign reconciliation"
            ),
        )

    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    def test_assume_delivered_has_no_send_or_log_side_effect(self, transport):
        url = reverse(
            "studio_campaign_delivery_resolve",
            args=[self.campaign.pk, self.delivery.pk],
        )

        response = self.client.post(url, {"action": "assume_sent"})

        self.assertEqual(response.status_code, 302)
        self.delivery.refresh_from_db()
        self.campaign.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.ASSUMED_SENT)
        self.assertEqual(self.campaign.sent_count, 0)
        self.assertEqual(self.campaign.status, "sent")
        self.assertFalse(EmailLog.objects.filter(campaign=self.campaign).exists())
        transport.assert_not_called()

    def test_resolution_requires_staff_post_and_csrf(self):
        url = reverse(
            "studio_campaign_delivery_resolve",
            args=[self.campaign.pk, self.delivery.pk],
        )
        self.assertEqual(self.client.get(url).status_code, 405)

        self.client.force_login(self.non_staff)
        self.assertEqual(
            self.client.post(url, {"action": "assume_sent"}).status_code,
            403,
        )

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(
            csrf_client.post(url, {"action": "assume_sent"}).status_code,
            403,
        )
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.AMBIGUOUS)
