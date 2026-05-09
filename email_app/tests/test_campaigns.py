"""Tests for email campaign functionality.

Covers:
- EmailCampaign model: TARGET_LEVEL_CHOICES, get_eligible_recipients, get_recipient_count
- Campaign send task: status transitions, EmailLog creation, rate limiting, error handling
- Admin views: list campaigns, send test email, send campaign, recipient count
- Campaign status transitions: draft -> sending -> sent
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from email_app.models import EmailCampaign, EmailLog
from email_app.tests.test_email_service import assert_no_internal_footer_text
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag('core')
class EmailCampaignModelTest(TierSetupMixin, TestCase):
    """Test EmailCampaign model enhancements for campaigns."""

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

    def test_get_eligible_recipients_includes_active_override(self):
        """Active tier overrides count toward campaign eligibility."""
        override_user = User.objects.create_user(
            email='override@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        TierOverride.objects.create(
            user=override_user,
            original_tier=self.free_tier,
            override_tier=self.premium_tier,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )
        campaign = EmailCampaign.objects.create(
            subject='Premium', body='Hi', target_min_level=30,
        )

        recipients = campaign.get_eligible_recipients()

        self.assertEqual(recipients.count(), 1)
        self.assertEqual(recipients.first().email, 'override@test.com')

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

    def test_slack_filter_yes_returns_only_members(self):
        """Issue #358: slack_filter='yes' restricts to slack_member=True."""
        User.objects.create_user(
            email='in@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=True,
        )
        User.objects.create_user(
            email='out@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='Members', body='Hi', target_min_level=0,
            slack_filter='yes',
        )
        emails = list(campaign.get_eligible_recipients().values_list('email', flat=True))
        self.assertEqual(emails, ['in@test.com'])

    def test_slack_filter_no_returns_only_non_members(self):
        """Issue #358: slack_filter='no' restricts to slack_member=False."""
        User.objects.create_user(
            email='in@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=True,
        )
        User.objects.create_user(
            email='out@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='Non-members', body='Hi', target_min_level=0,
            slack_filter='no',
        )
        emails = list(campaign.get_eligible_recipients().values_list('email', flat=True))
        self.assertEqual(emails, ['out@test.com'])

    def test_slack_filter_any_does_not_restrict(self):
        """Issue #358: default slack_filter='any' applies no Slack filter."""
        User.objects.create_user(
            email='in@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=True,
        )
        User.objects.create_user(
            email='out@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='Anyone', body='Hi', target_min_level=0,
        )
        # Default value is "any".
        self.assertEqual(campaign.slack_filter, 'any')
        self.assertEqual(campaign.get_eligible_recipients().count(), 2)


@tag('core')
class EmailCampaignTagTargetingTest(TierSetupMixin, TestCase):
    """Issue #357: tag-based recipient targeting on top of tier level.

    Locks in the AND semantics: tier-level filter, include-tags filter
    (any-of), and exclude-tags filter (none-of) all AND together. Empty
    tag lists mean "no filter on that side", so a campaign saved before
    #357 (both lists empty) returns the same queryset as before.
    """

    def setUp(self):
        # Three Free, verified, subscribed users with different tag shapes.
        self.alice = User.objects.create_user(
            email='alice@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        self.alice.tags = ['early-adopter']
        self.alice.save(update_fields=['tags'])

        self.bob = User.objects.create_user(
            email='bob@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        self.bob.tags = ['early-adopter', 'bounced']
        self.bob.save(update_fields=['tags'])

        self.carol = User.objects.create_user(
            email='carol@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        # No tags.

    def test_both_tag_filters_empty_matches_pre_357_behavior(self):
        """Regression: both tag fields empty == pre-#357 queryset."""
        campaign = EmailCampaign.objects.create(
            subject='No tag filter', body='Hi', target_min_level=0,
        )
        emails = set(
            campaign.get_eligible_recipients().values_list('email', flat=True),
        )
        self.assertEqual(
            emails,
            {'alice@test.com', 'bob@test.com', 'carol@test.com'},
        )

    def test_include_tags_any_only(self):
        """target_tags_any narrows to users carrying at least one tag."""
        campaign = EmailCampaign.objects.create(
            subject='Early adopters', body='Hi', target_min_level=0,
            target_tags_any=['early-adopter'],
        )
        emails = set(
            campaign.get_eligible_recipients().values_list('email', flat=True),
        )
        self.assertEqual(emails, {'alice@test.com', 'bob@test.com'})
        self.assertNotIn('carol@test.com', emails)

    def test_exclude_tags_none_only(self):
        """target_tags_none excludes users carrying any of the tags."""
        campaign = EmailCampaign.objects.create(
            subject='Drop bounced', body='Hi', target_min_level=0,
            target_tags_none=['bounced'],
        )
        emails = set(
            campaign.get_eligible_recipients().values_list('email', flat=True),
        )
        # Bob has 'bounced' so he is excluded; Alice and Carol stay.
        self.assertEqual(emails, {'alice@test.com', 'carol@test.com'})
        self.assertNotIn('bob@test.com', emails)

    def test_include_and_exclude_combined(self):
        """Include and exclude AND together: must match include AND avoid exclude."""
        campaign = EmailCampaign.objects.create(
            subject='Early adopters minus bounced',
            body='Hi', target_min_level=0,
            target_tags_any=['early-adopter'],
            target_tags_none=['bounced'],
        )
        emails = set(
            campaign.get_eligible_recipients().values_list('email', flat=True),
        )
        # Alice: early-adopter, no bounced => in. Bob: early-adopter AND
        # bounced => out. Carol: no tags => out (fails include).
        self.assertEqual(emails, {'alice@test.com'})

    def test_tag_filter_ands_with_target_min_level(self):
        """Tag filter ANDs with the existing tier-level filter."""
        # Promote Alice to Main, leave Bob on Free with the tag.
        self.alice.tier = self.main_tier
        self.alice.save(update_fields=['tier'])

        campaign = EmailCampaign.objects.create(
            subject='Main+ early adopters', body='Hi',
            target_min_level=20,
            target_tags_any=['early-adopter'],
        )
        emails = set(
            campaign.get_eligible_recipients().values_list('email', flat=True),
        )
        # Only Alice satisfies Main+ AND has the early-adopter tag.
        self.assertEqual(emails, {'alice@test.com'})

    def test_tag_filter_excludes_unverified_and_unsubscribed(self):
        """Tag filter does not bypass the verification/subscribed gates."""
        unverified = User.objects.create_user(
            email='unverified@test.com', tier=self.free_tier,
            email_verified=False, unsubscribed=False,
        )
        unverified.tags = ['early-adopter']
        unverified.save(update_fields=['tags'])

        unsub = User.objects.create_user(
            email='unsub@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=True,
        )
        unsub.tags = ['early-adopter']
        unsub.save(update_fields=['tags'])

        campaign = EmailCampaign.objects.create(
            subject='Tag filter respects gates', body='Hi',
            target_min_level=0,
            target_tags_any=['early-adopter'],
        )
        emails = set(
            campaign.get_eligible_recipients().values_list('email', flat=True),
        )
        # Alice and Bob carry the tag and pass the gates; the unverified
        # and unsubscribed users with the same tag are still rejected.
        self.assertEqual(emails, {'alice@test.com', 'bob@test.com'})


@tag('core')
class CampaignDuplicateCopiesTagsTest(TierSetupMixin, TestCase):
    """campaign_duplicate must copy both tag fields onto the new draft (#357)."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.com', password='adminpass123',
        )
        self.admin.email_verified = True
        self.admin.tier = self.free_tier
        self.admin.save()
        self.client.login(email='admin@test.com', password='adminpass123')

    def test_duplicate_copies_target_tag_fields(self):
        original = EmailCampaign.objects.create(
            subject='Tagged Campaign',
            body='Hi',
            target_min_level=20,
            target_tags_any=['early-adopter'],
            target_tags_none=['bounced'],
            status='draft',
        )

        url = reverse(
            'studio_campaign_duplicate', args=[original.pk],
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

        duplicate = EmailCampaign.objects.exclude(pk=original.pk).get()
        self.assertEqual(duplicate.target_tags_any, ['early-adopter'])
        self.assertEqual(duplicate.target_tags_none, ['bounced'])
        # And the new copy is a fresh draft (not linked to the original).
        self.assertEqual(duplicate.status, 'draft')
        self.assertEqual(duplicate.target_min_level, 20)


@tag('core')
@override_settings(
    # The base studio template loads hashed static assets via
    # ``CompressedManifestStaticFilesStorage``. Inside the test runner
    # ``collectstatic`` is not run, so the manifest lookup raises. Switch
    # to the simple non-hashing storage for tests that actually render
    # the studio detail HTML. Other (admin/non-render) tests in this
    # file don't need this and stay on the production setting.
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class CampaignFormTagPersistTest(TierSetupMixin, TestCase):
    """The Studio campaign form persists include/exclude tags (#357)."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.com', password='adminpass123',
        )
        self.admin.email_verified = True
        self.admin.tier = self.free_tier
        self.admin.save()
        self.client.login(email='admin@test.com', password='adminpass123')

    def test_create_persists_normalized_tag_fields(self):
        url = reverse('studio_campaign_create')
        response = self.client.post(url, {
            'subject': 'Tagged draft',
            'body': 'Hi',
            'target_min_level': 0,
            # Free-form: mixed case, comma- and space-separated.
            'target_tags_any': 'Early-Adopter, ai-hero-waitlist',
            'target_tags_none': 'bounced',
        })
        self.assertEqual(response.status_code, 302)

        campaign = EmailCampaign.objects.get(subject='Tagged draft')
        self.assertEqual(
            campaign.target_tags_any,
            ['early-adopter', 'ai-hero-waitlist'],
        )
        self.assertEqual(campaign.target_tags_none, ['bounced'])

    def test_edit_overwrites_tag_fields(self):
        campaign = EmailCampaign.objects.create(
            subject='Edit me',
            body='Hi',
            target_min_level=0,
            target_tags_any=['early-adopter'],
            target_tags_none=['bounced'],
            status='draft',
        )
        url = reverse('studio_campaign_edit', args=[campaign.pk])
        response = self.client.post(url, {
            'subject': 'Edited',
            'body': 'Hi',
            'target_min_level': 0,
            # Clear include, replace exclude with a different list.
            'target_tags_any': '',
            'target_tags_none': 'bounced spammer',
        })
        self.assertEqual(response.status_code, 302)

        campaign.refresh_from_db()
        self.assertEqual(campaign.target_tags_any, [])
        self.assertEqual(campaign.target_tags_none, ['bounced', 'spammer'])

    def test_detail_page_renders_active_tag_filters(self):
        campaign = EmailCampaign.objects.create(
            subject='With tags',
            body='Hi',
            target_min_level=0,
            target_tags_any=['early-adopter'],
            target_tags_none=['bounced'],
            status='draft',
        )
        url = reverse('studio_campaign_detail', args=[campaign.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Detail page surfaces both lists in their dedicated cells.
        self.assertContains(
            response,
            'data-testid="campaign-include-tags"',
        )
        self.assertContains(
            response,
            'data-testid="campaign-exclude-tags"',
        )
        self.assertContains(response, 'early-adopter')
        self.assertContains(response, 'bounced')

    def test_detail_page_renders_dash_when_no_tag_filters(self):
        campaign = EmailCampaign.objects.create(
            subject='No tag filter',
            body='Hi',
            target_min_level=0,
            status='draft',
        )
        url = reverse('studio_campaign_detail', args=[campaign.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Both cells render the em-dash placeholder when empty.
        content = response.content.decode()
        # Two —, one for include, one for exclude.
        self.assertGreaterEqual(content.count('—'), 2)


@tag('core')
class SendCampaignFanOutTest(TierSetupMixin, TestCase):
    """Test the top-level send_campaign fan-out task.

    send_campaign queries recipients, transitions the campaign to
    'sending', and enqueues one send_campaign_batch per chunk. It does
    not send any emails itself.
    """

    def setUp(self):
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

    @patch('jobs.tasks.helpers.q_async_task')
    def test_send_campaign_enqueues_one_batch_per_chunk(self, mock_q):
        """send_campaign chunks recipients and enqueues one batch task each."""
        # Add more recipients so chunking happens at batch_size=3.
        for i in range(5):
            User.objects.create_user(
                email=f'extra{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )
        mock_q.return_value = 'task-id'

        from email_app.tasks.send_campaign import send_campaign
        result = send_campaign(self.campaign.pk, batch_size=3)

        # 7 eligible users (2 setUp + 5 extras), batch_size=3 => 3 batches
        self.assertEqual(result['total'], 7)
        self.assertEqual(result['batch_count'], 3)
        self.assertEqual(result['status'], 'sending')
        self.assertEqual(mock_q.call_count, 3)

        # Verify each call was for send_campaign_batch with this campaign
        for call in mock_q.call_args_list:
            args, kwargs = call
            self.assertEqual(
                args[0], 'email_app.tasks.send_campaign.send_campaign_batch',
            )
            self.assertEqual(kwargs['campaign_id'], self.campaign.pk)
            self.assertIn('user_ids', kwargs)
            self.assertLessEqual(len(kwargs['user_ids']), 3)

        # Across all chunks, every eligible user appears exactly once
        all_chunked_ids = []
        for call in mock_q.call_args_list:
            all_chunked_ids.extend(call.kwargs['user_ids'])
        self.assertEqual(len(all_chunked_ids), 7)
        self.assertEqual(len(set(all_chunked_ids)), 7)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_send_campaign_transitions_to_sending(self, mock_q):
        """Fan-out transitions campaign from draft -> sending."""
        mock_q.return_value = 'task-id'
        self.assertEqual(self.campaign.status, 'draft')

        from email_app.tasks.send_campaign import send_campaign
        send_campaign(self.campaign.pk)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sending')
        # sent_at is not set yet — only set when last batch finishes
        self.assertIsNone(self.campaign.sent_at)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_send_campaign_excludes_ineligible_recipients_from_chunks(
        self, mock_q,
    ):
        """Unsubscribed/unverified users are excluded from chunked user_ids."""
        mock_q.return_value = 'task-id'

        from email_app.tasks.send_campaign import send_campaign
        send_campaign(self.campaign.pk)

        # Collect every user_id passed to a batch task.
        chunked = []
        for call in mock_q.call_args_list:
            chunked.extend(call.kwargs['user_ids'])

        self.assertIn(self.user1.pk, chunked)
        self.assertIn(self.user2.pk, chunked)
        # Unsubscribed user MUST NOT be chunked.
        self.assertNotIn(self.user3.pk, chunked)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_send_campaign_no_recipients_marks_sent(self, mock_q):
        """A campaign with zero eligible recipients goes straight to sent."""
        mock_q.return_value = 'task-id'
        # Campaign targets Premium only — no Premium users exist.
        empty_campaign = EmailCampaign.objects.create(
            subject='Premium Only',
            body='Hi',
            target_min_level=30,
            status='draft',
        )

        from email_app.tasks.send_campaign import send_campaign
        result = send_campaign(empty_campaign.pk)

        self.assertEqual(result['total'], 0)
        self.assertEqual(result['batch_count'], 0)
        self.assertEqual(result['status'], 'sent')

        empty_campaign.refresh_from_db()
        self.assertEqual(empty_campaign.status, 'sent')
        self.assertIsNotNone(empty_campaign.sent_at)
        # No batch tasks enqueued
        mock_q.assert_not_called()

    def test_send_campaign_not_found_raises_error(self):
        """Sending a non-existent campaign raises ValueError."""
        from email_app.tasks.send_campaign import send_campaign

        with self.assertRaises(ValueError) as ctx:
            send_campaign(99999)
        self.assertIn('not found', str(ctx.exception))

    def test_send_campaign_not_draft_raises_error(self):
        """Sending a campaign that is not draft raises ValueError."""
        self.campaign.status = 'sent'
        self.campaign.save()

        from email_app.tasks.send_campaign import send_campaign

        with self.assertRaises(ValueError) as ctx:
            send_campaign(self.campaign.pk)
        self.assertIn("status 'sent'", str(ctx.exception))

    @patch('jobs.tasks.helpers.q_async_task')
    def test_send_campaign_uses_settings_batch_size(self, mock_q):
        """When batch_size is omitted, fan-out uses settings.EMAIL_BATCH_SIZE."""
        mock_q.return_value = 'task-id'

        from django.test.utils import override_settings
        with override_settings(EMAIL_BATCH_SIZE=1):
            from email_app.tasks.send_campaign import send_campaign
            result = send_campaign(self.campaign.pk)

        # 2 eligible users, batch_size=1 => 2 batches
        self.assertEqual(result['batch_count'], 2)


@tag('core')
class SendCampaignBatchTest(TierSetupMixin, TestCase):
    """Test the chunked send_campaign_batch task."""

    def setUp(self):
        self.user1 = User.objects.create_user(
            email='user1@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        self.user2 = User.objects.create_user(
            email='user2@test.com', tier=self.basic_tier,
            email_verified=True, unsubscribed=False,
        )
        self.user3 = User.objects.create_user(
            email='user3@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=True,
        )
        self.campaign = EmailCampaign.objects.create(
            subject='Test Campaign',
            body='# Hello\n\nThis is a test.',
            target_min_level=0,
            status='sending',  # Already in sending state when batch runs
        )

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_batch_sends_to_specified_users(self, MockService):
        """send_campaign_batch sends emails only to the given user_ids."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-msg-001'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        from email_app.tasks.send_campaign import send_campaign_batch
        result = send_campaign_batch(
            self.campaign.pk,
            user_ids=[self.user1.pk, self.user2.pk],
            send_delay=0,
        )

        self.assertEqual(result['sent_count'], 2)

        logs = EmailLog.objects.filter(campaign=self.campaign)
        self.assertEqual(logs.count(), 2)
        log_emails = set(logs.values_list('user__email', flat=True))
        self.assertEqual(log_emails, {'user1@test.com', 'user2@test.com'})

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_batch_creates_email_logs_with_correct_fields(self, MockService):
        """Each EmailLog has campaign FK, type=campaign, and SES id set."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-123'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        from email_app.tasks.send_campaign import send_campaign_batch
        send_campaign_batch(
            self.campaign.pk,
            user_ids=[self.user1.pk, self.user2.pk],
            send_delay=0,
        )

        logs = EmailLog.objects.filter(campaign=self.campaign)
        for log in logs:
            self.assertEqual(log.email_type, 'campaign')
            self.assertEqual(log.ses_message_id, 'ses-123')
            self.assertEqual(log.campaign, self.campaign)

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_batch_calls_ses_per_recipient(self, MockService):
        """SES send is called once per user_id."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-123'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        from email_app.tasks.send_campaign import send_campaign_batch
        send_campaign_batch(
            self.campaign.pk,
            user_ids=[self.user1.pk, self.user2.pk],
            send_delay=0,
        )

        self.assertEqual(mock_service._send_ses.call_count, 2)
        sent_emails = {c[0][0] for c in mock_service._send_ses.call_args_list}
        self.assertEqual(sent_emails, {'user1@test.com', 'user2@test.com'})
        for call in mock_service._send_ses.call_args_list:
            self.assertEqual(
                call.kwargs['unsubscribe_url'],
                'http://example.com/unsub',
            )

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_batch_continues_on_individual_failure(self, MockService):
        """If one email fails, the rest of the batch continues."""
        from email_app.services.email_service import EmailServiceError

        mock_service = MockService.return_value
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'
        mock_service._send_ses.side_effect = [
            EmailServiceError('SES error'),
            'ses-msg-002',
        ]

        from email_app.tasks.send_campaign import send_campaign_batch
        with self.assertLogs('email_app.tasks.send_campaign', level='ERROR') as logs:
            result = send_campaign_batch(
                self.campaign.pk,
                user_ids=[self.user1.pk, self.user2.pk],
                send_delay=0,
            )

        self.assertEqual(result['sent_count'], 1)
        self.assertIn(
            f'Failed to send campaign {self.campaign.pk} to user2@test.com',
            logs.output[0],
        )
        self.assertEqual(
            EmailLog.objects.filter(campaign=self.campaign).count(), 1,
        )

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_batch_skips_users_with_existing_log(self, MockService):
        """Idempotency: a retried batch skips users already logged."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-retry'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        # Pretend user1 already received this campaign.
        EmailLog.objects.create(
            campaign=self.campaign,
            user=self.user1,
            email_type='campaign',
            ses_message_id='earlier-attempt',
        )

        from email_app.tasks.send_campaign import send_campaign_batch
        result = send_campaign_batch(
            self.campaign.pk,
            user_ids=[self.user1.pk, self.user2.pk],
            send_delay=0,
        )

        # user1 skipped; user2 sent
        self.assertEqual(result['sent_count'], 1)
        self.assertEqual(result['skipped_count'], 1)

        # SES called only for user2.
        self.assertEqual(mock_service._send_ses.call_count, 1)
        called_emails = {c[0][0] for c in mock_service._send_ses.call_args_list}
        self.assertEqual(called_emails, {'user2@test.com'})

        # No duplicate EmailLog for user1.
        user1_logs = EmailLog.objects.filter(
            campaign=self.campaign, user=self.user1,
        )
        self.assertEqual(user1_logs.count(), 1)
        self.assertEqual(user1_logs.first().ses_message_id, 'earlier-attempt')

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_last_batch_transitions_campaign_to_sent(self, MockService):
        """When the final batch finishes, campaign moves to 'sent'."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-final'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        # Two batches: first one leaves user2 pending; second completes.
        from email_app.tasks.send_campaign import send_campaign_batch
        send_campaign_batch(
            self.campaign.pk, user_ids=[self.user1.pk], send_delay=0,
        )
        self.campaign.refresh_from_db()
        # Still sending — user2 is eligible but not yet logged.
        self.assertEqual(self.campaign.status, 'sending')

        send_campaign_batch(
            self.campaign.pk, user_ids=[self.user2.pk], send_delay=0,
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')
        self.assertIsNotNone(self.campaign.sent_at)
        self.assertEqual(self.campaign.sent_count, 2)

    def test_batch_not_found_raises_error(self):
        """Batch on a missing campaign raises ValueError."""
        from email_app.tasks.send_campaign import send_campaign_batch

        with self.assertRaises(ValueError) as ctx:
            send_campaign_batch(99999, user_ids=[1], send_delay=0)
        self.assertIn('not found', str(ctx.exception))


@tag('core')
class EmailLogUniquenessTest(TierSetupMixin, TestCase):
    """Per-recipient idempotency is enforced at the database level."""

    def test_duplicate_campaign_log_raises_integrity_error(self):
        """Two EmailLogs for the same (campaign, user) violate the constraint."""
        from django.db import IntegrityError, transaction

        user = User.objects.create_user(
            email='dup@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='Dup', body='Hi', target_min_level=0,
        )
        EmailLog.objects.create(
            campaign=campaign, user=user,
            email_type='campaign', ses_message_id='m1',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EmailLog.objects.create(
                    campaign=campaign, user=user,
                    email_type='campaign', ses_message_id='m2',
                )

    def test_multiple_transactional_logs_per_user_allowed(self):
        """Constraint only applies when campaign is set; transactional
        emails (campaign IS NULL) can have multiple rows per user."""
        user = User.objects.create_user(
            email='trans@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        EmailLog.objects.create(
            user=user, email_type='welcome', ses_message_id='w1',
        )
        # Should not raise.
        EmailLog.objects.create(
            user=user, email_type='welcome', ses_message_id='w2',
        )
        self.assertEqual(EmailLog.objects.filter(user=user).count(), 2)


class SendCampaignEndToEndTest(TierSetupMixin, TestCase):
    """End-to-end: fan-out + batch execution with chunking."""

    @patch('email_app.tasks.send_campaign.EmailService')
    @patch('jobs.tasks.helpers.q_async_task')
    def test_full_pipeline_chunks_and_completes(self, mock_q, MockService):
        """7 recipients with batch_size=3 produce 3 chunks; running each
        chunk results in all 7 receiving the campaign and status=sent."""
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-id'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/u'

        users = [
            User.objects.create_user(
                email=f'eu{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )
            for i in range(7)
        ]
        campaign = EmailCampaign.objects.create(
            subject='Pipeline', body='Hi', target_min_level=0,
            status='draft',
        )

        # Capture chunks the fan-out would have queued.
        chunks_to_run = []

        def capture(func, *args, **kwargs):
            if func == 'email_app.tasks.send_campaign.send_campaign_batch':
                chunks_to_run.append(kwargs)
            return 'task-id'

        mock_q.side_effect = capture

        from email_app.tasks.send_campaign import (
            send_campaign,
            send_campaign_batch,
        )
        send_campaign(campaign.pk, batch_size=3)

        self.assertEqual(len(chunks_to_run), 3)

        # Now execute each captured chunk synchronously.
        for chunk_kwargs in chunks_to_run:
            send_campaign_batch(
                chunk_kwargs['campaign_id'],
                user_ids=chunk_kwargs['user_ids'],
                send_delay=0,
            )

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')
        self.assertEqual(campaign.sent_count, 7)
        self.assertIsNotNone(campaign.sent_at)
        # Every user has exactly one EmailLog.
        for user in users:
            self.assertEqual(
                EmailLog.objects.filter(
                    campaign=campaign, user=user,
                ).count(),
                1,
            )


class CampaignAdminTest(TierSetupMixin, TestCase):
    """Test admin views for email campaigns."""

    def setUp(self):
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

    def test_campaign_list_shows_campaigns(self):
        """Campaign list shows subject, status, sent_count."""
        response = self.client.get('/admin/email_app/emailcampaign/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Test Campaign', content)
        self.assertIn('Draft', content)

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
        to_email, subject, html = mock_service._send_ses.call_args[0]
        self.assertEqual(to_email, 'admin@test.com')
        self.assertEqual(subject, '[TEST] Test Campaign')
        self.assertIn('<p>Test content.</p>', html)
        self.assertIn('http://example.com/unsub', html)

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
        content = response.content.decode()
        # Draft subject should be in an editable input field
        self.assertIn('name="subject"', content)
        self.assertIn('name="body"', content)

    def test_sent_campaign_fields_readonly(self):
        """Sent campaigns have readonly fields."""
        self.campaign.status = 'sent'
        self.campaign.save()
        url = f'/admin/email_app/emailcampaign/{self.campaign.pk}/change/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Sent campaign subject and body should NOT be editable input fields
        self.assertNotIn('name="subject"', content)
        self.assertNotIn('name="body"', content)


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


@tag('core')
class CampaignVerifyEmailFooterTest(TierSetupMixin, TestCase):
    """Issue #450: per-recipient verify-email footer CTA on campaigns.

    The footer renders ABOVE the unsubscribe block when the recipient
    is unverified at SEND time. ``send_campaign_batch`` already
    re-fetches each user from the DB inside its loop, so a recipient
    who verifies between enqueue and send sees no CTA — same model as
    the existing ``unsubscribed`` handling.

    Note: ``EmailCampaign.get_eligible_recipients`` excludes unverified
    users, so in normal operator flow no campaign reaches an unverified
    recipient. These tests drive ``send_campaign_batch`` directly with
    explicit ``user_ids`` to exercise the footer hook itself, which
    must hold even when callers bypass the eligibility filter (e.g. a
    future relaxation of the include rule, or an internal preview send).
    """

    def setUp(self):
        self.campaign = EmailCampaign.objects.create(
            subject='Hello there',
            body='# Body\n\nbody copy',
            target_min_level=0,
            status='sending',
        )

    @patch('email_app.tasks.send_campaign.EmailService._send_ses', return_value='ses-450-c1')
    def test_campaign_recipient_unverified_at_send_time_sees_cta(
        self, mock_ses,
    ):
        unverified = User.objects.create_user(
            email='unv@test.com', tier=self.free_tier,
            email_verified=False, unsubscribed=False,
        )

        from email_app.tasks.send_campaign import send_campaign_batch
        send_campaign_batch(
            self.campaign.pk,
            user_ids=[unverified.pk],
            send_delay=0,
        )

        # Captured HTML is the third positional arg to _send_ses.
        html = mock_ses.call_args[0][2]
        self.assertIn('<p class="verify-email-cta">', html)
        self.assertIn('Verify your email', html)
        self.assertIn('/api/verify-email?token=', html)
        self.assertIn('/api/unsubscribe?token=', html)
        assert_no_internal_footer_text(self, html)

    @patch('email_app.tasks.send_campaign.EmailService._send_ses', return_value='ses-450-c2')
    def test_campaign_recipient_verified_at_send_time_omits_cta(
        self, mock_ses,
    ):
        verified = User.objects.create_user(
            email='ver@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )

        from email_app.tasks.send_campaign import send_campaign_batch
        send_campaign_batch(
            self.campaign.pk,
            user_ids=[verified.pk],
            send_delay=0,
        )

        html = mock_ses.call_args[0][2]
        self.assertNotIn('<p class="verify-email-cta">', html)
        self.assertNotIn('/api/verify-email?token=', html)
        self.assertIn('/api/unsubscribe?token=', html)
        assert_no_internal_footer_text(self, html)

    @patch('email_app.tasks.send_campaign.EmailService._send_ses', return_value='ses-450-c3')
    def test_campaign_recipient_verified_after_enqueue_omits_cta(
        self, mock_ses,
    ):
        """The check happens at SEND time, not enqueue time.

        Verifies the spec decision: if a recipient flips
        ``email_verified`` between fan-out and chunk execution,
        ``send_campaign_batch`` must use the fresh DB value (no CTA),
        not the stale in-memory value (would-render CTA).
        """
        recipient = User.objects.create_user(
            email='flip@test.com', tier=self.free_tier,
            email_verified=False, unsubscribed=False,
        )

        # Simulate the user verifying after the fan-out enqueued their
        # PK but before send_campaign_batch runs.
        User.objects.filter(pk=recipient.pk).update(email_verified=True)

        from email_app.tasks.send_campaign import send_campaign_batch
        send_campaign_batch(
            self.campaign.pk,
            user_ids=[recipient.pk],
            send_delay=0,
        )

        html = mock_ses.call_args[0][2]
        self.assertNotIn('<p class="verify-email-cta">', html)
        self.assertNotIn('/api/verify-email?token=', html)
        assert_no_internal_footer_text(self, html)


@tag('core')
class CampaignEligibilityCriteriaTest(TierSetupMixin, TestCase):
    """Campaign send respects tier, verification, and subscription status.

    Moved from playwright_tests/test_email_campaigns.py Scenario 8.
    """

    @patch('email_app.tasks.send_campaign.EmailService')
    def test_campaign_send_respects_tier_verification_and_subscription(
        self, MockService
    ):
        """Main+ campaign sends only to verified, subscribed Main/Premium users.

        Given: 2 verified Main, 1 verified Premium, 1 unsubscribed Main,
        1 unverified Main, 3 Free users.
        Then: sent_count is 3 (2 Main + 1 Premium).
        """
        mock_service = MockService.return_value
        mock_service._send_ses.return_value = 'ses-msg-id'
        mock_service._build_unsubscribe_url.return_value = 'http://example.com/unsub'

        # 2 verified Main members (eligible)
        User.objects.create_user(
            email='main-eligible-1@test.com', tier=self.main_tier,
            email_verified=True, unsubscribed=False,
        )
        User.objects.create_user(
            email='main-eligible-2@test.com', tier=self.main_tier,
            email_verified=True, unsubscribed=False,
        )

        # 1 verified Premium member (eligible)
        User.objects.create_user(
            email='premium-eligible@test.com', tier=self.premium_tier,
            email_verified=True, unsubscribed=False,
        )

        # 1 unsubscribed Main member (NOT eligible)
        User.objects.create_user(
            email='main-unsub@test.com', tier=self.main_tier,
            email_verified=True, unsubscribed=True,
        )

        # 1 unverified Main member (NOT eligible)
        User.objects.create_user(
            email='main-unverified@test.com', tier=self.main_tier,
            email_verified=False, unsubscribed=False,
        )

        # 3 Free members (NOT eligible for level 20)
        for i in range(3):
            User.objects.create_user(
                email=f'free-ineligible-{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )

        campaign = EmailCampaign.objects.create(
            subject='Main+ Campaign',
            body='Content for Main and above',
            target_min_level=20,
            status='draft',
        )

        # Drive the fan-out + batches inline by capturing what
        # send_campaign would have enqueued, then executing each
        # batch synchronously.
        with patch('jobs.tasks.helpers.q_async_task') as mock_q:
            captured = []

            def capture(func, *args, **kwargs):
                if func == 'email_app.tasks.send_campaign.send_campaign_batch':
                    captured.append(kwargs)
                return 'task-id'

            mock_q.side_effect = capture

            from email_app.tasks.send_campaign import (
                send_campaign,
                send_campaign_batch,
            )
            send_campaign(campaign.pk)

            for chunk in captured:
                send_campaign_batch(
                    chunk['campaign_id'],
                    user_ids=chunk['user_ids'],
                    send_delay=0,
                )

        campaign.refresh_from_db()
        self.assertEqual(campaign.sent_count, 3)
        self.assertEqual(campaign.status, 'sent')

        logs = EmailLog.objects.filter(campaign=campaign)
        self.assertEqual(logs.count(), 3)

        recipient_emails = set(logs.values_list('user__email', flat=True))
        self.assertIn('main-eligible-1@test.com', recipient_emails)
        self.assertIn('main-eligible-2@test.com', recipient_emails)
        self.assertIn('premium-eligible@test.com', recipient_emails)

        self.assertNotIn('main-unsub@test.com', recipient_emails)
        self.assertNotIn('main-unverified@test.com', recipient_emails)
        for i in range(3):
            self.assertNotIn(
                f'free-ineligible-{i}@test.com', recipient_emails
            )
