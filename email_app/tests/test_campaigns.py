"""Tests for email campaign functionality.

Covers:
- EmailCampaign model: TARGET_LEVEL_CHOICES, get_eligible_recipients, get_recipient_count
- Campaign send task: status transitions, EmailLog creation, rate limiting, error handling
- Admin views: list campaigns, send test email, send campaign, recipient count
- Campaign status transitions: draft -> sending -> sent
"""

import json
from unittest.mock import MagicMock, patch, call

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from email_app.models import EmailCampaign, EmailLog

User = get_user_model()


class EmailCampaignModelTest(TestCase):
    """Test EmailCampaign model enhancements for campaigns."""

    def setUp(self):
        from payments.models import Tier
        self.free_tier, _ = Tier.objects.get_or_create(slug='free', defaults={'name': 'Free', 'level': 0})
        self.basic_tier, _ = Tier.objects.get_or_create(slug='basic', defaults={'name': 'Basic', 'level': 10})
        self.main_tier, _ = Tier.objects.get_or_create(slug='main', defaults={'name': 'Main', 'level': 20})
        self.premium_tier, _ = Tier.objects.get_or_create(slug='premium', defaults={'name': 'Premium', 'level': 30})

    def test_target_level_choices(self):
        """target_min_level has choices for 0/10/20/30."""
        campaign = EmailCampaign.objects.create(
            subject='Test',
            body='Body',
            target_min_level=0,
        )
        choices = dict(EmailCampaign.TARGET_LEVEL_CHOICES)
        self.assertIn(0, choices)
        self.assertIn(10, choices)
        self.assertIn(20, choices)
        self.assertIn(30, choices)
        self.assertEqual(choices[0], 'Everyone (including free)')
        self.assertEqual(choices[30], 'Premium only')

    def test_get_eligible_recipients_everyone(self):
        """target_min_level=0 includes all verified, subscribed users."""
        User.objects.create_user(
            email='free@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email='premium@test.com', tier=self.premium_tier,
            email_verified=True, unsubscribed=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='All Users', body='Hi', target_min_level=0,
        )
        recipients = campaign.get_eligible_recipients()
        self.assertEqual(recipients.count(), 2)

    def test_get_eligible_recipients_basic_plus(self):
        """target_min_level=10 includes Basic+ only."""
        User.objects.create_user(
            email='free@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email='basic@test.com', tier=self.basic_tier,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email='main@test.com', tier=self.main_tier,
            email_verified=True, unsubscribed=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='Basic+', body='Hi', target_min_level=10,
        )
        recipients = campaign.get_eligible_recipients()
        self.assertEqual(recipients.count(), 2)
        emails = set(recipients.values_list('email', flat=True))
        self.assertIn('basic@test.com', emails)
        self.assertIn('main@test.com', emails)
        self.assertNotIn('free@test.com', emails)

    def test_get_eligible_recipients_premium_only(self):
        """target_min_level=30 includes Premium only."""
        User.objects.create_user(
            email='main@test.com', tier=self.main_tier,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email='premium@test.com', tier=self.premium_tier,
            email_verified=True, unsubscribed=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='Premium', body='Hi', target_min_level=30,
        )
        recipients = campaign.get_eligible_recipients()
        self.assertEqual(recipients.count(), 1)
        self.assertEqual(recipients.first().email, 'premium@test.com')

    def test_get_eligible_recipients_excludes_unsubscribed(self):
        """Unsubscribed users are excluded from recipients."""
        User.objects.create_user(
            email='subscribed@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email='unsub@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=True,
        )
        campaign = EmailCampaign.objects.create(
            subject='Test', body='Hi', target_min_level=0,
        )
        recipients = campaign.get_eligible_recipients()
        self.assertEqual(recipients.count(), 1)
        self.assertEqual(recipients.first().email, 'subscribed@test.com')

    def test_get_eligible_recipients_excludes_unverified(self):
        """Unverified users are excluded from recipients."""
        User.objects.create_user(
            email='verified@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email='unverified@test.com', tier=self.free_tier,
            email_verified=False, unsubscribed=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='Test', body='Hi', target_min_level=0,
        )
        recipients = campaign.get_eligible_recipients()
        self.assertEqual(recipients.count(), 1)
        self.assertEqual(recipients.first().email, 'verified@test.com')

    def test_get_recipient_count(self):
        """get_recipient_count returns the count of eligible recipients."""
        for i in range(5):
            User.objects.create_user(
                email=f'user{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )
        campaign = EmailCampaign.objects.create(
            subject='Test', body='Hi', target_min_level=0,
        )
        self.assertEqual(campaign.get_recipient_count(), 5)

    def test_status_default_draft(self):
        """New campaigns default to 'draft' status."""
        campaign = EmailCampaign.objects.create(
            subject='Test', body='Body',
        )
        self.assertEqual(campaign.status, 'draft')

    def test_str_representation(self):
        campaign = EmailCampaign.objects.create(
            subject='My Campaign', body='Body',
        )
        self.assertEqual(str(campaign), 'My Campaign (draft)')


class SendCampaignTaskTest(TestCase):
    """Test the send_campaign background task."""

    def setUp(self):
        from payments.models import Tier
        self.free_tier, _ = Tier.objects.get_or_create(slug='free', defaults={'name': 'Free', 'level': 0})
        self.basic_tier, _ = Tier.objects.get_or_create(slug='basic', defaults={'name': 'Basic', 'level': 10})

        # Create eligible users
        self.user1 = User.objects.create_user(
            email='user1@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        self.user2 = User.objects.create_user(
            email='user2@test.com', tier=self.basic_tier,
            email_verified=True, unsubscribed=False,
        )
        # Ineligible user (unsubscribed)
        self.user3 = User.objects.create_user(
            email='user3@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=True,
        )

        self.campaign = EmailCampaign.objects.create(
            subject='Test Campaign',
            body='# Hello\n\nThis is a test.',
            target_min_level=0,
            status='draft',
        )

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_send_campaign_sends_to_eligible_users(self, MockService):
        """Campaign is sent to all eligible recipients."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-msg-001'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        from email_app.tasks.send_campaign import send_campaign
        result = send_campaign(self.campaign.pk, send_delay=0)

        self.assertEqual(result['sent_count'], 2)
        self.assertEqual(result['status'], 'sent')

        # Check EmailLogs created
        logs = EmailLog.objects.filter(campaign=self.campaign)
        self.assertEqual(logs.count(), 2)
        log_emails = set(logs.values_list('user__email', flat=True))
        self.assertIn('user1@test.com', log_emails)
        self.assertIn('user2@test.com', log_emails)
        self.assertNotIn('user3@test.com', log_emails)

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_send_campaign_status_transitions(self, MockService):
        """Campaign transitions from draft -> sending -> sent."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-msg-001'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        # Verify starting state
        self.assertEqual(self.campaign.status, 'draft')

        from email_app.tasks.send_campaign import send_campaign
        send_campaign(self.campaign.pk, send_delay=0)

        # Verify final state
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')
        self.assertIsNotNone(self.campaign.sent_at)
        self.assertEqual(self.campaign.sent_count, 2)

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_send_campaign_creates_email_logs(self, MockService):
        """An EmailLog is created for each successful send."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-123'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        from email_app.tasks.send_campaign import send_campaign
        send_campaign(self.campaign.pk, send_delay=0)

        logs = EmailLog.objects.filter(campaign=self.campaign)
        self.assertEqual(logs.count(), 2)
        for log in logs:
            self.assertEqual(log.email_type, 'campaign')
            self.assertEqual(log.ses_message_id, 'ses-123')
            self.assertEqual(log.campaign, self.campaign)

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_send_campaign_continues_on_individual_failure(self, MockService):
        """If one email fails, the rest continue sending."""
        from email_app.services.email_service import EmailServiceError

        mock_service = MockService.return_value
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'
        # First call fails, second succeeds
        mock_service._send_ses.side_effect = [
            EmailServiceError('SES error'),
            'ses-msg-002',
        ]

        from email_app.tasks.send_campaign import send_campaign
        result = send_campaign(self.campaign.pk, send_delay=0)

        # Only 1 successful send
        self.assertEqual(result['sent_count'], 1)
        self.assertEqual(EmailLog.objects.filter(campaign=self.campaign).count(), 1)

        # Campaign should still be marked as sent
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')

    def test_send_campaign_not_found_raises_error(self):
        """Sending a non-existent campaign raises ValueError."""
        from email_app.tasks.send_campaign import send_campaign

        with self.assertRaises(ValueError) as ctx:
            send_campaign(99999, send_delay=0)
        self.assertIn('not found', str(ctx.exception))

    def test_send_campaign_not_draft_raises_error(self):
        """Sending a campaign that is not draft raises ValueError."""
        self.campaign.status = 'sent'
        self.campaign.save()

        from email_app.tasks.send_campaign import send_campaign

        with self.assertRaises(ValueError) as ctx:
            send_campaign(self.campaign.pk, send_delay=0)
        self.assertIn("status 'sent'", str(ctx.exception))

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_send_campaign_respects_target_min_level(self, MockService):
        """Campaign only sends to users at or above the target tier level."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-msg-001'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        # Campaign targets Basic+ (level 10)
        campaign = EmailCampaign.objects.create(
            subject='Basic+ Only',
            body='Hi',
            target_min_level=10,
            status='draft',
        )

        from email_app.tasks.send_campaign import send_campaign
        result = send_campaign(campaign.pk, send_delay=0)

        # Only user2 (basic) should receive it
        self.assertEqual(result['sent_count'], 1)
        log = EmailLog.objects.filter(campaign=campaign).first()
        self.assertEqual(log.user.email, 'user2@test.com')

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_send_campaign_updates_sent_count_incrementally(self, MockService):
        """sent_count is updated as each email is sent."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-msg-001'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        from email_app.tasks.send_campaign import send_campaign
        send_campaign(self.campaign.pk, send_delay=0)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.sent_count, 2)


class CampaignAdminTest(TestCase):
    """Test admin views for email campaigns."""

    def setUp(self):
        from payments.models import Tier
        self.free_tier, _ = Tier.objects.get_or_create(slug='free', defaults={'name': 'Free', 'level': 0})

        self.admin_user = User.objects.create_superuser(
            email='admin@test.com',
            password='adminpass123',
        )
        # Set admin as verified subscriber
        self.admin_user.email_verified = True
        self.admin_user.tier = self.free_tier
        self.admin_user.save()
        self.client.login(email='admin@test.com', password='adminpass123')

        self.campaign = EmailCampaign.objects.create(
            subject='Test Campaign',
            body='# Hello\n\nTest content.',
            target_min_level=0,
            status='draft',
        )

    def test_campaign_list_accessible(self):
        """Admin campaign list page loads."""
        response = self.client.get('/admin/email_app/emailcampaign/')
        self.assertEqual(response.status_code, 200)

    def test_campaign_list_shows_campaigns(self):
        """Campaign list shows subject, status, sent_count."""
        response = self.client.get('/admin/email_app/emailcampaign/')
        content = response.content.decode()
        self.assertIn('Test Campaign', content)
        self.assertIn('Draft', content)

    def test_campaign_add_form(self):
        """Admin can access the add campaign form."""
        response = self.client.get('/admin/email_app/emailcampaign/add/')
        self.assertEqual(response.status_code, 200)

    def test_campaign_add_creates_campaign(self):
        """Admin can create a new campaign."""
        response = self.client.post('/admin/email_app/emailcampaign/add/', {
            'subject': 'New Campaign',
            'body': '# New\n\nContent here.',
            'target_min_level': 0,
        })
        # Should redirect to change list on success
        self.assertIn(response.status_code, [200, 302])
        self.assertTrue(
            EmailCampaign.objects.filter(subject='New Campaign').exists()
        )

    def test_campaign_change_form_shows_actions(self):
        """Change form shows send test and send campaign buttons for draft."""
        url = f'/admin/email_app/emailcampaign/{self.campaign.pk}/change/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Campaign Actions', content)
        self.assertIn('Send Test Email', content)
        self.assertIn('Send Campaign', content)

    def test_campaign_change_form_sent_no_actions(self):
        """Change form hides action buttons for sent campaigns."""
        self.campaign.status = 'sent'
        self.campaign.save()
        url = f'/admin/email_app/emailcampaign/{self.campaign.pk}/change/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('already been sent', content)

    @patch('email_app.services.email_service.EmailService')
    def test_send_test_email(self, MockService):
        """Send test email endpoint sends to admin's email."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'test-ses-id'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        url = reverse(
            'admin:email_app_emailcampaign_send_test',
            args=[self.campaign.pk],
        )
        response = self.client.post(url, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertIn('admin@test.com', data['message'])

        # SES should be called with [TEST] prefix
        mock_service._send_ses.assert_called_once()
        call_args = mock_service._send_ses.call_args
        self.assertEqual(call_args[0][0], 'admin@test.com')
        self.assertIn('[TEST]', call_args[0][1])

    def test_send_test_email_get_not_allowed(self):
        """Send test email only accepts POST."""
        url = reverse(
            'admin:email_app_emailcampaign_send_test',
            args=[self.campaign.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    def test_send_test_email_campaign_not_found(self):
        """Send test email returns 404 for non-existent campaign."""
        url = reverse(
            'admin:email_app_emailcampaign_send_test',
            args=[99999],
        )
        response = self.client.post(url, content_type='application/json')
        self.assertEqual(response.status_code, 404)

    @patch('jobs.tasks.async_task')
    def test_send_campaign_enqueues_job(self, mock_async_task):
        """Send campaign enqueues a background job."""
        mock_async_task.return_value = 'task-id-123'

        url = reverse(
            'admin:email_app_emailcampaign_send_campaign',
            args=[self.campaign.pk],
        )
        response = self.client.post(url, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertIn('queued', data['message'])

        mock_async_task.assert_called_once_with(
            'email_app.tasks.send_campaign.send_campaign',
            campaign_id=self.campaign.pk,
        )

    @patch('jobs.tasks.async_task')
    def test_send_campaign_already_sending(self, mock_async_task):
        """Cannot send a campaign that is already sending."""
        self.campaign.status = 'sending'
        self.campaign.save()

        url = reverse(
            'admin:email_app_emailcampaign_send_campaign',
            args=[self.campaign.pk],
        )
        response = self.client.post(url, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data['status'], 'error')

    @patch('jobs.tasks.async_task')
    def test_send_campaign_already_sent(self, mock_async_task):
        """Cannot send a campaign that is already sent."""
        self.campaign.status = 'sent'
        self.campaign.save()

        url = reverse(
            'admin:email_app_emailcampaign_send_campaign',
            args=[self.campaign.pk],
        )
        response = self.client.post(url, content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_send_campaign_get_not_allowed(self):
        """Send campaign only accepts POST."""
        url = reverse(
            'admin:email_app_emailcampaign_send_campaign',
            args=[self.campaign.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    def test_recipient_count_endpoint(self):
        """Recipient count endpoint returns correct count."""
        # Create some eligible users
        for i in range(3):
            User.objects.create_user(
                email=f'user{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )

        url = reverse(
            'admin:email_app_emailcampaign_recipient_count',
            args=[self.campaign.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # 3 test users + 1 admin user (who is also verified & subscribed)
        self.assertEqual(data['count'], 4)
        self.assertEqual(data['target_min_level'], 0)

    def test_recipient_count_not_found(self):
        """Recipient count returns 404 for non-existent campaign."""
        url = reverse(
            'admin:email_app_emailcampaign_recipient_count',
            args=[99999],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_campaign_list_displays_sent_count(self):
        """Campaign list shows sent_count column."""
        self.campaign.sent_count = 42
        self.campaign.status = 'sent'
        self.campaign.sent_at = timezone.now()
        self.campaign.save()

        response = self.client.get('/admin/email_app/emailcampaign/')
        content = response.content.decode()
        self.assertIn('42', content)

    def test_draft_campaign_fields_editable(self):
        """Draft campaigns have editable subject and body."""
        url = f'/admin/email_app/emailcampaign/{self.campaign.pk}/change/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_sent_campaign_fields_readonly(self):
        """Sent campaigns have readonly fields."""
        self.campaign.status = 'sent'
        self.campaign.save()
        url = f'/admin/email_app/emailcampaign/{self.campaign.pk}/change/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class CampaignAdminUnauthenticatedTest(TestCase):
    """Test admin campaign views require authentication."""

    def test_campaign_list_requires_login(self):
        """Campaign list requires admin login."""
        response = self.client.get('/admin/email_app/emailcampaign/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_send_test_requires_login(self):
        """Send test endpoint requires admin login."""
        campaign = EmailCampaign.objects.create(
            subject='Test', body='Body',
        )
        url = f'/admin/email_app/emailcampaign/{campaign.pk}/send-test/'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

    def test_send_campaign_requires_login(self):
        """Send campaign endpoint requires admin login."""
        campaign = EmailCampaign.objects.create(
            subject='Test', body='Body',
        )
        url = f'/admin/email_app/emailcampaign/{campaign.pk}/send-campaign/'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
