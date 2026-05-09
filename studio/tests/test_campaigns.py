"""Tests for studio campaign management views."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from email_app.models import EmailCampaign
from email_app.services.email_service import EmailService
from email_app.tests.test_email_service import assert_no_internal_footer_text
from payments.models import Tier

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

    def test_create_campaign_default_slack_filter_is_any(self):
        # Issue #358: omitting the field defaults to "any".
        self.client.post(
            "/studio/campaigns/new",
            {
                "subject": "Default Slack",
                "body": "x",
                "target_min_level": "0",
            },
        )
        campaign = EmailCampaign.objects.get(subject="Default Slack")
        self.assertEqual(campaign.slack_filter, "any")

    def test_create_campaign_with_slack_filter_yes(self):
        self.client.post(
            "/studio/campaigns/new",
            {
                "subject": "Members Only",
                "body": "x",
                "target_min_level": "20",
                "slack_filter": "yes",
            },
        )
        campaign = EmailCampaign.objects.get(subject="Members Only")
        self.assertEqual(campaign.slack_filter, "yes")

    def test_create_campaign_with_invalid_slack_filter_falls_back_to_any(self):
        self.client.post(
            "/studio/campaigns/new",
            {
                "subject": "Bad Slack",
                "body": "x",
                "target_min_level": "0",
                "slack_filter": "garbage",
            },
        )
        campaign = EmailCampaign.objects.get(subject="Bad Slack")
        self.assertEqual(campaign.slack_filter, "any")


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
        self.assertContains(response, 'data-testid="send-campaign-btn"')
        self.assertContains(response, "Send Test")
        self.assertContains(response, "Duplicate Campaign")
        self.assertContains(response, 'name="test_recipients"')

    def test_detail_hides_send_button_for_non_draft_campaign(self):
        self.campaign.status = "sent"
        self.campaign.save(update_fields=["status"])

        response = self.client.get(f"/studio/campaigns/{self.campaign.pk}/")

        self.assertNotContains(response, 'data-testid="send-campaign-btn"')
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
            email_kind="promotional",
            unsubscribe_url=None,
        )

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "draft")
        self.assertEqual(self.campaign.sent_count, 0)

    @patch.object(EmailService, "_send_ses", return_value="test-ses-clean")
    def test_test_send_uses_clean_footer_for_real_render(self, mock_ses):
        recipient = User.objects.create_user(
            email="recipient@example.com",
            email_verified=True,
            unsubscribed=False,
        )

        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": recipient.email},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        mock_ses.assert_called_once()
        html = mock_ses.call_args[0][2]
        self.assertIn("/api/unsubscribe?token=", html)
        self.assertNotIn('<p class="verify-email-cta">', html)
        self.assertEqual(mock_ses.call_args.kwargs["email_kind"], "promotional")
        self.assertIn(
            "/api/unsubscribe?token=",
            mock_ses.call_args.kwargs["unsubscribe_url"],
        )
        assert_no_internal_footer_text(self, html)

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


class StudioCampaignCreateFormTest(TestCase):
    """Test create form UX (issue #292)."""

    @classmethod
    def setUpTestData(cls):
        cls.free, _ = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )
        cls.main, _ = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20},
        )

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com", password="testpass", is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_create_form_submit_button_reads_save_as_draft(self):
        """The create-form submit button reads 'Save as Draft' (not
        'Create Campaign') and uses the neutral/secondary button class,
        not 'bg-accent'."""
        response = self.client.get("/studio/campaigns/new")
        self.assertContains(response, "Save as Draft")
        self.assertNotContains(response, ">Create Campaign<")

    def test_create_form_shows_recipient_count_for_default_audience(self):
        """GET renders the recipient-count helper using level=0."""
        # 2 eligible at level 0 (free tier, verified, subscribed)
        User.objects.create_user(
            email="free1@t.com", password="p", tier=self.free,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email="main1@t.com", password="p", tier=self.main,
            email_verified=True, unsubscribed=False,
        )
        # Not counted: unsubscribed + unverified
        User.objects.create_user(
            email="free2@t.com", password="p", tier=self.free,
            email_verified=True, unsubscribed=True,
        )
        User.objects.create_user(
            email="free3@t.com", password="p", tier=self.free,
            email_verified=False, unsubscribed=False,
        )

        response = self.client.get("/studio/campaigns/new")
        # Staff user from setUp has no tier assigned so they are NOT counted.
        self.assertEqual(response.context["recipient_count"], 2)
        self.assertContains(response, "Will reach 2 eligible recipient")


class StudioCampaignListActionsTest(TestCase):
    """Test list page actions (issue #292)."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com", password="testpass", is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_list_empty_state_shows_create_cta(self):
        """With zero campaigns the page renders the empty-state block
        with a visible 'Create your first campaign' CTA link."""
        self.assertEqual(EmailCampaign.objects.count(), 0)

        response = self.client.get("/studio/campaigns/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="campaigns-empty-state"')
        self.assertContains(response, "Create your first campaign")
        self.assertContains(response, "New Campaign")
        self.assertContains(response, 'href="/studio/campaigns/new"')

    def test_list_draft_rows_show_edit_link(self):
        """Draft rows link to the new edit URL."""
        draft = EmailCampaign.objects.create(
            subject="Draft Row", body="Hello", status="draft",
        )
        response = self.client.get("/studio/campaigns/")
        self.assertContains(
            response,
            f'href="/studio/campaigns/{draft.pk}/edit"',
        )

    def test_list_sent_rows_hide_edit_link(self):
        """Non-draft rows do NOT link to the edit URL."""
        sent = EmailCampaign.objects.create(
            subject="Sent Row", body="Hello", status="sent",
        )
        response = self.client.get("/studio/campaigns/")
        self.assertNotContains(
            response,
            f'href="/studio/campaigns/{sent.pk}/edit"',
        )
        # View link is still present for non-drafts.
        self.assertContains(
            response,
            f'href="/studio/campaigns/{sent.pk}/"',
        )

    def test_list_status_select_does_not_auto_submit(self):
        """The status <select> no longer has onchange=this.form.submit().

        Explicit Search click is now the only way to filter — prevents
        accidental re-renders from keyboard navigation in a staff tool.
        """
        response = self.client.get("/studio/campaigns/")
        self.assertNotContains(response, 'onchange="this.form.submit()"')


class StudioCampaignEditTest(TestCase):
    """Test campaign edit view (issue #292)."""

    @classmethod
    def setUpTestData(cls):
        cls.free, _ = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )
        cls.main, _ = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20},
        )

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com", password="testpass", is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Draft Edit Me",
            body="Original body",
            target_min_level=0,
            status="draft",
        )

    def test_edit_form_prefills_draft_fields(self):
        """GET renders form.html with subject, body, target_min_level."""
        response = self.client.get(
            f"/studio/campaigns/{self.campaign.pk}/edit",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "studio/campaigns/form.html")
        self.assertContains(response, 'value="Draft Edit Me"')
        # The body textarea contains the current body.
        self.assertContains(response, "Original body")
        # Edit form: button label is 'Save Changes'.
        self.assertContains(response, "Save Changes")

    def test_edit_form_shows_recipient_count_for_campaign_audience(self):
        """The recipient-count helper reflects the campaign's current
        target_min_level (not the default level=0 count)."""
        # Setup: one Main-tier user, one Free-tier user. Only Main counts
        # at level=20.
        User.objects.create_user(
            email="free-u@t.com", password="p", tier=self.free,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email="main-u@t.com", password="p", tier=self.main,
            email_verified=True, unsubscribed=False,
        )
        self.campaign.target_min_level = 20
        self.campaign.save(update_fields=["target_min_level"])

        response = self.client.get(
            f"/studio/campaigns/{self.campaign.pk}/edit",
        )
        self.assertEqual(response.context["recipient_count"], 1)
        self.assertContains(response, "Will reach 1 eligible recipient")

    def test_edit_post_updates_draft_and_redirects(self):
        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/edit",
            {
                "subject": "New Subject",
                "body": "New body",
                "target_min_level": "10",
            },
        )
        self.assertRedirects(
            response,
            f"/studio/campaigns/{self.campaign.pk}/",
            fetch_redirect_response=False,
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.subject, "New Subject")
        self.assertEqual(self.campaign.body, "New body")
        self.assertEqual(self.campaign.target_min_level, 10)
        self.assertEqual(self.campaign.status, "draft")
        flashed = list(get_messages(response.wsgi_request))
        self.assertEqual(len(flashed), 1)
        self.assertEqual(flashed[0].tags, "success")
        self.assertIn("updated", flashed[0].message.lower())

    def test_edit_post_rejects_non_draft_and_does_not_change_fields(self):
        """POST on sent campaign: fields unchanged, error message, redirect
        to detail."""
        self.campaign.status = "sent"
        self.campaign.save(update_fields=["status"])
        original_subject = self.campaign.subject
        original_body = self.campaign.body
        original_level = self.campaign.target_min_level

        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/edit",
            {
                "subject": "Hijacked Subject",
                "body": "Hijacked body",
                "target_min_level": "30",
            },
        )
        self.assertRedirects(
            response,
            f"/studio/campaigns/{self.campaign.pk}/",
            fetch_redirect_response=False,
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.subject, original_subject)
        self.assertEqual(self.campaign.body, original_body)
        self.assertEqual(self.campaign.target_min_level, original_level)
        self.assertEqual(self.campaign.status, "sent")

        flashed = list(get_messages(response.wsgi_request))
        self.assertEqual(len(flashed), 1)
        self.assertEqual(flashed[0].tags, "error")
        self.assertIn("cannot be edited", flashed[0].message.lower())

    def test_edit_get_on_non_draft_redirects_with_error(self):
        """GET on sent campaign redirects to detail with an error."""
        self.campaign.status = "sent"
        self.campaign.save(update_fields=["status"])
        response = self.client.get(
            f"/studio/campaigns/{self.campaign.pk}/edit",
        )
        self.assertRedirects(
            response,
            f"/studio/campaigns/{self.campaign.pk}/",
            fetch_redirect_response=False,
        )
        flashed = list(get_messages(response.wsgi_request))
        self.assertEqual(len(flashed), 1)
        self.assertEqual(flashed[0].tags, "error")

    def test_edit_requires_staff_get_returns_403(self):
        """Non-staff GET is forbidden and the campaign is unchanged."""
        self.client.logout()
        non_staff = User.objects.create_user(
            email="member@test.com", password="p", is_staff=False,
        )
        self.assertFalse(non_staff.is_staff)
        self.client.login(email="member@test.com", password="p")

        response = self.client.get(
            f"/studio/campaigns/{self.campaign.pk}/edit",
        )
        self.assertEqual(response.status_code, 403)

    def test_edit_requires_staff_post_returns_403_no_side_effect(self):
        self.client.logout()
        User.objects.create_user(
            email="member@test.com", password="p", is_staff=False,
        )
        self.client.login(email="member@test.com", password="p")

        original_subject = self.campaign.subject
        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/edit",
            {
                "subject": "Hijack",
                "body": "Hijack",
                "target_min_level": "0",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.subject, original_subject)

    def test_edit_nonexistent_returns_404(self):
        response = self.client.get("/studio/campaigns/99999/edit")
        self.assertEqual(response.status_code, 404)


class StudioCampaignDeleteTest(TestCase):
    """Test campaign delete view (issue #292)."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com", password="testpass", is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Delete Me", body="Bye", status="draft",
        )

    def test_delete_removes_draft_and_redirects_to_list(self):
        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/delete",
        )
        self.assertRedirects(
            response,
            "/studio/campaigns/",
            fetch_redirect_response=False,
        )
        self.assertFalse(
            EmailCampaign.objects.filter(pk=self.campaign.pk).exists(),
        )
        flashed = list(get_messages(response.wsgi_request))
        self.assertEqual(len(flashed), 1)
        self.assertEqual(flashed[0].tags, "success")
        self.assertIn("Deleted draft campaign", flashed[0].message)
        self.assertIn("Delete Me", flashed[0].message)

    def test_delete_rejects_non_draft_and_keeps_record(self):
        self.campaign.status = "sent"
        self.campaign.save(update_fields=["status"])
        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/delete",
        )
        self.assertRedirects(
            response,
            f"/studio/campaigns/{self.campaign.pk}/",
            fetch_redirect_response=False,
        )
        # Record is still there.
        self.assertTrue(
            EmailCampaign.objects.filter(pk=self.campaign.pk).exists(),
        )
        flashed = list(get_messages(response.wsgi_request))
        self.assertEqual(len(flashed), 1)
        self.assertEqual(flashed[0].tags, "error")
        self.assertIn("Only draft campaigns", flashed[0].message)

    def test_delete_get_returns_405(self):
        response = self.client.get(
            f"/studio/campaigns/{self.campaign.pk}/delete",
        )
        self.assertEqual(response.status_code, 405)

    def test_delete_requires_staff_no_side_effect(self):
        """Non-staff POST gets 403 AND the campaign still exists."""
        self.client.logout()
        User.objects.create_user(
            email="member@test.com", password="p", is_staff=False,
        )
        self.client.login(email="member@test.com", password="p")

        before = EmailCampaign.objects.count()
        response = self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/delete",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(EmailCampaign.objects.count(), before)
        self.assertTrue(
            EmailCampaign.objects.filter(pk=self.campaign.pk).exists(),
        )

    def test_delete_nonexistent_returns_404(self):
        response = self.client.post("/studio/campaigns/99999/delete")
        self.assertEqual(response.status_code, 404)


class StudioCampaignDetailPreviewTest(TestCase):
    """Preview rendering + layout tests (issue #292)."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com", password="testpass", is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_preview_renders_markdown_html(self):
        """Preview HTML in the view context includes <h1> and <strong>
        tags produced by the markdown library, not the raw `#` / `**`
        characters."""
        campaign = EmailCampaign.objects.create(
            subject="Markdown test", body="# Hello\n\n**bold**",
            status="draft",
        )
        response = self.client.get(f"/studio/campaigns/{campaign.pk}/")
        self.assertEqual(response.status_code, 200)
        preview_html = response.context["preview_html"]
        self.assertIn("<h1>Hello</h1>", preview_html)
        self.assertIn("<strong>bold</strong>", preview_html)
        assert_no_internal_footer_text(self, preview_html)

    def test_preview_uses_email_service_pipeline(self):
        """campaign_detail delegates preview rendering to
        EmailService.render_markdown_email."""
        campaign = EmailCampaign.objects.create(
            subject="Pipeline subject", body="pipeline body",
            status="draft",
        )
        sentinel_html = '<html data-sentinel="yes"><body>OK</body></html>'
        with patch(
            "studio.views.campaigns.EmailService.render_markdown_email",
            return_value=sentinel_html,
        ) as mock_render:
            response = self.client.get(
                f"/studio/campaigns/{campaign.pk}/",
            )
        self.assertEqual(response.status_code, 200)
        mock_render.assert_called_once()
        args, kwargs = mock_render.call_args
        passed_subject = kwargs.get("subject", args[0] if args else None)
        passed_body = kwargs.get("body", args[1] if len(args) > 1 else None)
        self.assertEqual(passed_subject, "Pipeline subject")
        self.assertEqual(passed_body, "pipeline body")

    def test_preview_is_scoped_to_iframe_via_srcdoc(self):
        """The response contains an <iframe ... srcdoc="..."> element
        carrying the rendered preview HTML."""
        campaign = EmailCampaign.objects.create(
            subject="Iframe check", body="# Iframe content",
            status="draft",
        )
        response = self.client.get(f"/studio/campaigns/{campaign.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="campaign-preview-iframe"')
        self.assertContains(response, "srcdoc=")

    def test_preview_srcdoc_attribute_contains_full_email_document(self):
        """Regression: the srcdoc attribute must carry the entire rendered
        email document, not be truncated at the first double-quote inside
        the SafeString HTML (e.g. `<html lang="en">`). The template must
        HTML-escape the rendered HTML so attribute quoting stays valid."""
        import re
        from html import unescape

        campaign = EmailCampaign.objects.create(
            subject="Reg test",
            body="# Heading\n\n**bold** text",
            status="draft",
        )
        response = self.client.get(f"/studio/campaigns/{campaign.pk}/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        match = re.search(r'srcdoc="([^"]*)"', html)
        self.assertIsNotNone(match, "srcdoc attribute missing")
        unescaped = unescape(match.group(1))
        # Full email document must reach the iframe, including rendered markdown.
        self.assertIn("<h1>Heading</h1>", unescaped)
        self.assertIn("<strong>bold</strong>", unescaped)
        self.assertIn("</html>", unescaped)
        assert_no_internal_footer_text(self, unescaped)

    def test_detail_shows_edit_and_delete_for_draft(self):
        """Draft campaigns render the Edit link and Delete form."""
        campaign = EmailCampaign.objects.create(
            subject="Draft Actions", body="x", status="draft",
        )
        response = self.client.get(f"/studio/campaigns/{campaign.pk}/")
        self.assertContains(
            response, f'href="/studio/campaigns/{campaign.pk}/edit"',
        )
        self.assertContains(
            response,
            f'action="/studio/campaigns/{campaign.pk}/delete"',
        )

    def test_detail_hides_edit_and_delete_for_sent(self):
        """Sent campaigns do NOT render the Edit link or Delete form."""
        campaign = EmailCampaign.objects.create(
            subject="Sent Actions", body="x", status="sent",
        )
        response = self.client.get(f"/studio/campaigns/{campaign.pk}/")
        self.assertNotContains(
            response, f'href="/studio/campaigns/{campaign.pk}/edit"',
        )
        self.assertNotContains(
            response,
            f'action="/studio/campaigns/{campaign.pk}/delete"',
        )

    def test_detail_send_button_shows_recipient_count(self):
        """The Send button label includes the eligible recipient count."""
        free, _ = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )
        for i in range(3):
            User.objects.create_user(
                email=f"u{i}@t.com", password="p", tier=free,
                email_verified=True, unsubscribed=False,
            )
        # The staff user from setUp has no tier; not counted.
        campaign = EmailCampaign.objects.create(
            subject="Send btn", body="x", status="draft",
            target_min_level=0,
        )
        response = self.client.get(f"/studio/campaigns/{campaign.pk}/")
        self.assertEqual(response.context["recipient_count"], 3)
        self.assertContains(response, "Send to 3 recipient")

    def test_detail_send_button_has_destructive_class(self):
        """Send uses a clearly destructive background (red-600), distinct
        from the neutral/secondary style used by Edit, Duplicate, Test
        Send."""
        campaign = EmailCampaign.objects.create(
            subject="Send style", body="x", status="draft",
        )
        response = self.client.get(f"/studio/campaigns/{campaign.pk}/")
        html = response.content.decode()
        self.assertIn("bg-red-600", html)
        self.assertContains(response, 'data-testid="send-campaign-btn"')

    def test_detail_send_form_has_confirm_onsubmit(self):
        """The Send form has an onsubmit confirm() guard — the JS
        behaviour is verified in Playwright."""
        campaign = EmailCampaign.objects.create(
            subject="Send confirm", body="x", status="draft",
        )
        response = self.client.get(f"/studio/campaigns/{campaign.pk}/")
        self.assertContains(response, 'onsubmit="return confirm(')
