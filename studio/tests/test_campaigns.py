"""Tests for studio campaign management views."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from email_app.models import EmailCampaign

User = get_user_model()


class StudioCampaignListTest(TestCase):
    """Test campaign list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/campaigns/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/campaigns/')
        self.assertTemplateUsed(response, 'studio/campaigns/list.html')

    def test_list_shows_campaigns(self):
        EmailCampaign.objects.create(
            subject='Test Campaign', body='Hello',
        )
        response = self.client.get('/studio/campaigns/')
        self.assertContains(response, 'Test Campaign')

    def test_list_filter_by_status(self):
        EmailCampaign.objects.create(
            subject='Draft Campaign', body='Hello', status='draft',
        )
        EmailCampaign.objects.create(
            subject='Sent Campaign', body='Hello', status='sent',
        )
        response = self.client.get('/studio/campaigns/?status=draft')
        self.assertContains(response, 'Draft Campaign')
        self.assertNotContains(response, 'Sent Campaign')

    def test_list_search(self):
        EmailCampaign.objects.create(
            subject='Welcome Email', body='Hello',
        )
        EmailCampaign.objects.create(
            subject='Update Email', body='News',
        )
        response = self.client.get('/studio/campaigns/?q=Welcome')
        self.assertContains(response, 'Welcome Email')
        self.assertNotContains(response, 'Update Email')

    def test_empty_state_message_and_create_link(self):
        """When no campaigns exist, show empty-state message and 'New Campaign' link.

        Covers Playwright Scenario 11 (test_empty_campaign_list_shows_message_and_create_link)
        from the deleted playwright_tests/test_email_campaigns.py.
        """
        # No campaigns exist for this test.
        self.assertEqual(EmailCampaign.objects.count(), 0)

        response = self.client.get('/studio/campaigns/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No campaigns found')
        # 'New Campaign' link is still available even with no campaigns.
        self.assertContains(response, 'href="/studio/campaigns/new"')
        self.assertContains(response, 'New Campaign')


class StudioCampaignCreateTest(TestCase):
    """Test campaign creation."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_returns_200(self):
        response = self.client.get('/studio/campaigns/new')
        self.assertEqual(response.status_code, 200)

    def test_create_campaign_post(self):
        response = self.client.post('/studio/campaigns/new', {
            'subject': 'New Campaign',
            'body': '# Hello World',
            'target_min_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        campaign = EmailCampaign.objects.get(subject='New Campaign')
        self.assertEqual(campaign.status, 'draft')
        self.assertEqual(campaign.target_min_level, 0)

    def test_create_campaign_with_target(self):
        self.client.post('/studio/campaigns/new', {
            'subject': 'Premium Campaign',
            'body': 'Premium content',
            'target_min_level': '30',
        })
        campaign = EmailCampaign.objects.get(subject='Premium Campaign')
        self.assertEqual(campaign.target_min_level, 30)


class StudioCampaignDetailTest(TestCase):
    """Test campaign detail/preview view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.campaign = EmailCampaign.objects.create(
            subject='Detail Campaign', body='Test body',
        )

    def test_detail_returns_200(self):
        response = self.client.get(f'/studio/campaigns/{self.campaign.pk}/')
        self.assertEqual(response.status_code, 200)

    def test_detail_shows_campaign_info(self):
        response = self.client.get(f'/studio/campaigns/{self.campaign.pk}/')
        self.assertContains(response, 'Detail Campaign')
        self.assertContains(response, 'Test body')

    def test_detail_shows_recipient_count(self):
        response = self.client.get(f'/studio/campaigns/{self.campaign.pk}/')
        self.assertIn('recipient_count', response.context)

    def test_detail_nonexistent_returns_404(self):
        response = self.client.get('/studio/campaigns/99999/')
        self.assertEqual(response.status_code, 404)
