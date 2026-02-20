"""Tests for the EmailLog and EmailCampaign models.

Covers:
- EmailLog creation with all fields
- EmailLog FK relationships (user, campaign)
- EmailLog ordering
- EmailCampaign creation and status choices
- String representations
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.models import EmailCampaign, EmailLog

User = get_user_model()


class EmailLogModelTest(TestCase):
    """Test EmailLog model."""

    def setUp(self):
        self.user = User.objects.create_user(email='test@example.com')

    def test_create_email_log(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type='welcome',
            ses_message_id='abc-123',
        )
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.email_type, 'welcome')
        self.assertEqual(log.ses_message_id, 'abc-123')
        self.assertIsNotNone(log.sent_at)
        self.assertIsNone(log.campaign)

    def test_email_log_str(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type='payment_failed',
        )
        self.assertIn('payment_failed', str(log))
        self.assertIn('test@example.com', str(log))

    def test_email_log_with_campaign(self):
        campaign = EmailCampaign.objects.create(
            subject='Test Campaign',
            body='Test body',
        )
        log = EmailLog.objects.create(
            user=self.user,
            email_type='campaign',
            campaign=campaign,
        )
        self.assertEqual(log.campaign, campaign)
        self.assertIn(log, campaign.email_logs.all())

    def test_email_log_campaign_nullable(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type='welcome',
        )
        self.assertIsNone(log.campaign)

    def test_email_log_ordering(self):
        log1 = EmailLog.objects.create(
            user=self.user,
            email_type='welcome',
        )
        log2 = EmailLog.objects.create(
            user=self.user,
            email_type='cancellation',
        )
        logs = list(EmailLog.objects.all())
        self.assertEqual(logs[0].pk, log2.pk)
        self.assertEqual(logs[1].pk, log1.pk)

    def test_email_log_user_related_name(self):
        EmailLog.objects.create(
            user=self.user,
            email_type='welcome',
        )
        EmailLog.objects.create(
            user=self.user,
            email_type='event_reminder',
        )
        self.assertEqual(self.user.email_logs.count(), 2)

    def test_ses_message_id_default_empty(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type='welcome',
        )
        self.assertEqual(log.ses_message_id, '')

    def test_campaign_deletion_sets_null(self):
        campaign = EmailCampaign.objects.create(
            subject='Delete Me',
            body='Body',
        )
        log = EmailLog.objects.create(
            user=self.user,
            email_type='campaign',
            campaign=campaign,
        )
        campaign.delete()
        log.refresh_from_db()
        self.assertIsNone(log.campaign)


class EmailCampaignModelTest(TestCase):
    """Test EmailCampaign model."""

    def test_create_campaign(self):
        campaign = EmailCampaign.objects.create(
            subject='Weekly Update',
            body='# Hello\n\nThis is a test.',
            target_min_level=0,
        )
        self.assertEqual(campaign.subject, 'Weekly Update')
        self.assertEqual(campaign.status, 'draft')
        self.assertEqual(campaign.sent_count, 0)
        self.assertIsNone(campaign.sent_at)
        self.assertIsNotNone(campaign.created_at)

    def test_campaign_str(self):
        campaign = EmailCampaign.objects.create(
            subject='Test Subject',
            body='Body',
        )
        self.assertEqual(str(campaign), 'Test Subject (draft)')

    def test_campaign_status_choices(self):
        campaign = EmailCampaign.objects.create(
            subject='Sending Test',
            body='Body',
            status='sending',
        )
        self.assertEqual(campaign.status, 'sending')

        campaign.status = 'sent'
        campaign.save()
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')

    def test_campaign_ordering(self):
        c1 = EmailCampaign.objects.create(subject='First', body='Body')
        c2 = EmailCampaign.objects.create(subject='Second', body='Body')
        campaigns = list(EmailCampaign.objects.all())
        self.assertEqual(campaigns[0].pk, c2.pk)
        self.assertEqual(campaigns[1].pk, c1.pk)
