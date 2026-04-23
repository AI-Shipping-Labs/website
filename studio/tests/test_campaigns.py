"""Tests for studio campaign management views."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from email_app.models import EmailCampaign

User = get_user_model()


class StudioCampaignListTest(TestCase):
    """Test campaign list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_list_returns_200(self):
        response = self.client.get("/studio/campaigns/")
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get("/studio/campaigns/")
        self.assertTemplateUsed(response, "studio/campaigns/list.html")

    def test_list_shows_campaigns(self):
        EmailCampaign.objects.create(
            subject="Test Campaign",
            body="Hello",
        )
        response = self.client.get("/studio/campaigns/")
        self.assertContains(response, "Test Campaign")

    def test_list_filter_by_status(self):
        EmailCampaign.objects.create(
            subject="Draft Campaign",
            body="Hello",
            status="draft",
        )
        EmailCampaign.objects.create(
            subject="Sent Campaign",
            body="Hello",
            status="sent",
        )
        response = self.client.get("/studio/campaigns/?status=draft")
        self.assertContains(response, "Draft Campaign")
        self.assertNotContains(response, "Sent Campaign")

    def test_list_search(self):
        EmailCampaign.objects.create(
            subject="Welcome Email",
            body="Hello",
        )
        EmailCampaign.objects.create(
            subject="Update Email",
            body="News",
        )
        response = self.client.get("/studio/campaigns/?q=Welcome")
        self.assertContains(response, "Welcome Email")
        self.assertNotContains(response, "Update Email")

    def test_empty_state_message_and_create_link(self):
        """When no campaigns exist, show empty-state message and 'New Campaign' link.

        Covers Playwright Scenario 11 (test_empty_campaign_list_shows_message_and_create_link)
        from the deleted playwright_tests/test_email_campaigns.py.
        """
        # No campaigns exist for this test.
        self.assertEqual(EmailCampaign.objects.count(), 0)

        response = self.client.get("/studio/campaigns/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No campaigns found")
        # 'New Campaign' link is still available even with no campaigns.
        self.assertContains(response, 'href="/studio/campaigns/new"')
        self.assertContains(response, "New Campaign")


class StudioCampaignCreateTest(TestCase):
    """Test campaign creation."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_create_form_returns_200(self):
        response = self.client.get("/studio/campaigns/new")
        self.assertEqual(response.status_code, 200)

    def test_create_campaign_post(self):
        response = self.client.post(
            "/studio/campaigns/new",
            {
                "subject": "New Campaign",
                "body": "# Hello World",
                "target_min_level": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        campaign = EmailCampaign.objects.get(subject="New Campaign")
        self.assertRedirects(
            response,
            reverse("studio_campaign_detail", args=[campaign.pk]),
        )
        self.assertEqual(campaign.status, "draft")
        self.assertEqual(campaign.target_min_level, 0)

    def test_create_campaign_with_target(self):
        self.client.post(
            "/studio/campaigns/new",
            {
                "subject": "Premium Campaign",
                "body": "Premium content",
                "target_min_level": "30",
            },
        )
        campaign = EmailCampaign.objects.get(subject="Premium Campaign")
        self.assertEqual(campaign.target_min_level, 30)


class StudioCampaignDetailTest(TestCase):
    """Test campaign detail/preview view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Detail Campaign",
            body="Test body",
        )

    def test_detail_returns_200(self):
        response = self.client.get(f"/studio/campaigns/{self.campaign.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_detail_shows_campaign_info(self):
        response = self.client.get(f"/studio/campaigns/{self.campaign.pk}/")
        self.assertContains(response, "Detail Campaign")
        self.assertContains(response, "Test body")

    def test_detail_shows_recipient_count(self):
        response = self.client.get(f"/studio/campaigns/{self.campaign.pk}/")
        self.assertIn("recipient_count", response.context)

    def test_detail_nonexistent_returns_404(self):
        response = self.client.get("/studio/campaigns/99999/")
        self.assertEqual(response.status_code, 404)

    def test_detail_shows_test_send_form(self):
        response = self.client.get(f"/studio/campaigns/{self.campaign.pk}/")
        self.assertContains(response, "Send Campaign")
        self.assertContains(response, "Send Test")
        self.assertContains(response, "Duplicate Campaign")
        self.assertContains(response, 'name="test_recipients"')

    def test_detail_hides_send_button_for_non_draft_campaign(self):
        self.campaign.status = "sent"
        self.campaign.save(update_fields=["status"])

        response = self.client.get(f"/studio/campaigns/{self.campaign.pk}/")

        self.assertNotContains(response, "Send Campaign")
        self.assertContains(response, "Duplicate Campaign")

    def test_duplicate_campaign_creates_draft_copy_and_redirects(self):
        self.campaign.status = "sent"
        self.campaign.sent_count = 42
        self.campaign.save(update_fields=["status", "sent_count"])

        response = self.client.post(
            reverse("studio_campaign_duplicate", args=[self.campaign.pk]),
            follow=True,
        )

        duplicate = EmailCampaign.objects.exclude(pk=self.campaign.pk).get()
        self.assertRedirects(
            response,
            reverse("studio_campaign_detail", args=[duplicate.pk]),
        )
        self.assertEqual(duplicate.subject, "Detail Campaign (Copy)")
        self.assertEqual(duplicate.body, self.campaign.body)
        self.assertEqual(
            duplicate.target_min_level,
            self.campaign.target_min_level,
        )
        self.assertEqual(duplicate.status, "draft")
        self.assertEqual(duplicate.sent_count, 0)
        self.assertIsNone(duplicate.sent_at)
        self.assertContains(response, "Created draft copy")
        self.assertContains(response, "Detail Campaign (Copy)")

    @patch("studio.views.campaigns.EmailService")
    def test_test_send_single_address(self, MockService):
        mock_service = MockService.return_value
        mock_service.render_markdown_email.return_value = "<html>test</html>"
        mock_service._send_ses.return_value = "test-ses-id"

        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "preview@example.com"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test email sent to 1 address(es): preview@example.com.")
        mock_service._send_ses.assert_called_once_with(
            "preview@example.com",
            "[TEST] Detail Campaign",
            "<html>test</html>",
        )

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "draft")
        self.assertEqual(self.campaign.sent_count, 0)

    @patch("studio.views.campaigns.EmailService")
    def test_test_send_multiple_addresses_deduplicates(self, MockService):
        mock_service = MockService.return_value
        mock_service.render_markdown_email.return_value = "<html>test</html>"
        mock_service._send_ses.return_value = "test-ses-id"

        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "one@example.com,\ntwo@example.com; one@example.com"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test email sent to 2 address(es): one@example.com, two@example.com.")
        self.assertEqual(mock_service._send_ses.call_count, 2)

    @patch("studio.views.campaigns.EmailService")
    def test_test_send_invalid_address_shows_error(self, MockService):
        mock_service = MockService.return_value

        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "good@example.com, not-an-email"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid email address(es): not-an-email.")
        self.assertContains(response, "good@example.com, not-an-email")
        mock_service._send_ses.assert_not_called()

    def test_test_send_requires_staff(self):
        self.client.logout()
        User.objects.create_user(
            email="member@test.com",
            password="testpass",
            is_staff=False,
        )
        self.client.login(email="member@test.com", password="testpass")

        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "preview@example.com"},
        )

        self.assertEqual(response.status_code, 403)

    @patch("jobs.tasks.async_task", return_value="task-id-123")
    def test_send_campaign_queues_job_and_redirects_to_worker(self, mock_async_task):
        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/send",
            follow=True,
        )

        self.assertRedirects(response, "/studio/worker/")
        self.assertContains(response, "queued for sending")
        mock_async_task.assert_called_once_with(
            "email_app.tasks.send_campaign.send_campaign",
            campaign_id=self.campaign.pk,
        )

    @patch("jobs.tasks.async_task")
    def test_send_campaign_non_draft_shows_error(self, mock_async_task):
        self.campaign.status = "sent"
        self.campaign.save(update_fields=["status"])

        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/send",
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse("studio_campaign_detail", args=[self.campaign.pk]),
        )
        self.assertContains(response, "is already sent")
        mock_async_task.assert_not_called()
