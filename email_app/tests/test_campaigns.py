"""Tests for email campaign functionality.

Covers:
- EmailCampaign model: TARGET_LEVEL_CHOICES, get_eligible_recipients, get_recipient_count
- Campaign send task: status transitions, EmailLog creation, rate limiting, error handling
- Django admin inspection and retired delivery-action routes
- Campaign status transitions: draft -> sending -> sent
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from payments.models import TierOverride
from tests.fixtures import TierSetupMixin, create_user_with_membership, set_membership

User = get_user_model()


@tag('core')
class EmailCampaignModelTest(TierSetupMixin, TestCase):
    """Test EmailCampaign model enhancements for campaigns."""

    def test_get_eligible_recipients_everyone(self):
        """target_min_level=0 includes all verified, subscribed users."""
        create_user_with_membership(
            email='free@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        create_user_with_membership(
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
        create_user_with_membership(
            email='free@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        create_user_with_membership(
            email='basic@test.com', tier=self.basic_tier,
            email_verified=True, unsubscribed=False,
        )
        create_user_with_membership(
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
        create_user_with_membership(
            email='main@test.com', tier=self.main_tier,
            email_verified=True, unsubscribed=False,
        )
        create_user_with_membership(
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
        override_user = create_user_with_membership(
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
        create_user_with_membership(
            email='subscribed@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        create_user_with_membership(
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
        create_user_with_membership(
            email='verified@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        create_user_with_membership(
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
            create_user_with_membership(
                email=f'user{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )
        campaign = EmailCampaign.objects.create(
            subject='Test', body='Hi', target_min_level=0,
        )
        self.assertEqual(campaign.get_recipient_count(), 5)

    def test_slack_filter_yes_returns_only_members(self):
        """Issue #358: slack_filter='yes' restricts to slack_member=True."""
        create_user_with_membership(
            email='in@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=True,
        )
        create_user_with_membership(
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
        create_user_with_membership(
            email='in@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=True,
        )
        create_user_with_membership(
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
        create_user_with_membership(
            email='in@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
            slack_member=True,
        )
        create_user_with_membership(
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
        self.alice = create_user_with_membership(
            email='alice@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        self.alice.tags = ['early-adopter']
        self.alice.save(update_fields=['tags'])

        self.bob = create_user_with_membership(
            email='bob@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        self.bob.tags = ['early-adopter', 'bounced']
        self.bob.save(update_fields=['tags'])

        self.carol = create_user_with_membership(
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

    def test_include_and_exclude_are_relation_backed_sql(self):
        campaign = EmailCampaign.objects.create(
            subject='Indexed audience',
            body='Hi',
            target_tags_any=['early-adopter'],
            target_tags_none=['bounced'],
        )

        with CaptureQueriesContext(connection) as queries:
            emails = set(
                campaign.get_eligible_recipients().values_list('email', flat=True),
            )

        self.assertEqual(emails, {'alice@test.com'})
        sql = ' '.join(query['sql'] for query in queries).lower()
        self.assertIn('accounts_ext_memberextra_contact_tags', sql)
        self.assertNotIn('"accounts_user"."tags"', sql)

    def test_tag_filter_ands_with_target_min_level(self):
        """Tag filter ANDs with the existing tier-level filter."""
        # Promote Alice to Main, leave Bob on Free with the tag.
        set_membership(self.alice, tier=self.main_tier)

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
        unverified = create_user_with_membership(
            email='unverified@test.com', tier=self.free_tier,
            email_verified=False, unsubscribed=False,
        )
        unverified.tags = ['early-adopter']
        unverified.save(update_fields=['tags'])

        unsub = create_user_with_membership(
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
        set_membership(self.admin, tier=self.free_tier)
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
        set_membership(self.admin, tier=self.free_tier)
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
    'sending', and schedules one send_campaign_batch per chunk as a
    one-off (Schedule.ONCE) future-dated Schedule row staggered by
    CAMPAIGN_BATCH_INTERVAL_SECONDS (issue #922). It does not send any
    emails itself, and the Schedule rows are inert in tests (the
    django-q scheduler daemon is not running), so no email goes out.
    """

    def setUp(self):
        # Create eligible users
        self.user1 = create_user_with_membership(
            email='user1@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        self.user2 = create_user_with_membership(
            email='user2@test.com', tier=self.basic_tier,
            email_verified=True, unsubscribed=False,
        )
        # Ineligible user (unsubscribed)
        self.user3 = create_user_with_membership(
            email='user3@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=True,
        )
        self.inactive_user = create_user_with_membership(
            email='inactive@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False, is_active=False,
        )

        self.campaign = EmailCampaign.objects.create(
            subject='Test Campaign',
            body='# Hello\n\nThis is a test.',
            target_min_level=0,
            status='draft',
        )

    def _batch_schedules(self):
        """Return the send_campaign_batch Schedule rows ordered by next_run."""
        from django_q.models import Schedule
        return list(
            Schedule.objects.filter(
                func='email_app.tasks.send_campaign.send_campaign_batch',
            ).order_by('next_run')
        )

    @staticmethod
    def _kwargs(sched):
        """Decode a Schedule's kwargs (stored as a Python-literal string)."""
        import ast
        return ast.literal_eval(sched.kwargs)

    def test_send_campaign_schedules_one_batch_per_chunk(self):
        """send_campaign chunks recipients and schedules one batch each."""
        # Add more recipients so chunking happens at batch_size=3.
        for i in range(5):
            create_user_with_membership(
                email=f'extra{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )

        from email_app.tasks.send_campaign import send_campaign
        result = send_campaign(self.campaign.pk, batch_size=3)

        # 7 eligible users (2 setUp + 5 extras), batch_size=3 => 3 batches
        self.assertEqual(result['total'], 7)
        self.assertEqual(result['batch_count'], 3)
        self.assertEqual(result['status'], 'sending')

        schedules = self._batch_schedules()
        self.assertEqual(len(schedules), 3)

        # Every schedule is a one-off (ONCE) repeating exactly once.
        from django_q.models import Schedule
        for sched in schedules:
            kwargs = self._kwargs(sched)
            self.assertEqual(sched.schedule_type, Schedule.ONCE)
            self.assertEqual(sched.repeats, 1)
            self.assertEqual(kwargs['campaign_id'], self.campaign.pk)
            self.assertIn('user_ids', kwargs)
            self.assertLessEqual(len(kwargs['user_ids']), 3)

        # Across all chunks, every eligible user appears exactly once.
        all_chunked_ids = []
        for sched in schedules:
            all_chunked_ids.extend(self._kwargs(sched)['user_ids'])
        self.assertEqual(len(all_chunked_ids), 7)
        self.assertEqual(len(set(all_chunked_ids)), 7)

    def test_send_campaign_staggers_batches_by_interval(self):
        """Batch i is scheduled at now + i * interval; batch 0 immediate."""
        for i in range(5):
            create_user_with_membership(
                email=f'extra{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )

        before = timezone.now()
        from email_app.tasks.send_campaign import send_campaign
        # Default interval is 60s; force 4 batches at batch_size=2.
        send_campaign(self.campaign.pk, batch_size=2)
        after = timezone.now()

        schedules = self._batch_schedules()
        # 7 eligible users, batch_size=2 => 4 batches.
        self.assertEqual(len(schedules), 4)

        # Batch 0 (earliest next_run) fires immediately: next_run is at
        # ~now, i.e. within the window the fan-out ran.
        first_run = schedules[0].next_run
        self.assertGreaterEqual(first_run, before)
        self.assertLessEqual(first_run, after)

        # Each subsequent batch is offset by index * 60s from batch 0.
        base = schedules[0].next_run
        for index, sched in enumerate(schedules):
            offset = (sched.next_run - base).total_seconds()
            # Allow a small slack for the per-row creation wall-clock drift.
            self.assertAlmostEqual(offset, index * 60, delta=2)

    def test_send_campaign_interval_read_from_config(self):
        """The stagger reads CAMPAIGN_BATCH_INTERVAL_SECONDS from config."""
        for i in range(5):
            create_user_with_membership(
                email=f'extra{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )

        from email_app.tasks.send_campaign import send_campaign
        # override_settings flows through get_config (it checks Django
        # settings before env/default), so this exercises the config read.
        with override_settings(CAMPAIGN_BATCH_INTERVAL_SECONDS=120):
            send_campaign(self.campaign.pk, batch_size=2)

        schedules = self._batch_schedules()
        self.assertEqual(len(schedules), 4)

        base = schedules[0].next_run
        for index, sched in enumerate(schedules):
            offset = (sched.next_run - base).total_seconds()
            self.assertAlmostEqual(offset, index * 120, delta=2)

    def test_send_campaign_zero_interval_schedules_all_immediately(self):
        """Interval 0 means no stagger: every batch is scheduled at ~now."""
        for i in range(5):
            create_user_with_membership(
                email=f'extra{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )

        before = timezone.now()
        from email_app.tasks.send_campaign import send_campaign
        with override_settings(CAMPAIGN_BATCH_INTERVAL_SECONDS=0):
            send_campaign(self.campaign.pk, batch_size=2)
        after = timezone.now()

        schedules = self._batch_schedules()
        self.assertEqual(len(schedules), 4)
        # All next_run values fall within the fan-out window (no future
        # offset), so nothing is deferred.
        for sched in schedules:
            self.assertGreaterEqual(sched.next_run, before)
            self.assertLessEqual(sched.next_run, after)

    def test_send_campaign_unparseable_interval_falls_back_to_default(self):
        """A non-numeric config value falls back to the 60s default."""
        for i in range(5):
            create_user_with_membership(
                email=f'extra{i}@test.com', tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )

        from email_app.tasks.send_campaign import send_campaign
        with override_settings(CAMPAIGN_BATCH_INTERVAL_SECONDS='not-a-number'):
            send_campaign(self.campaign.pk, batch_size=2)

        schedules = self._batch_schedules()
        self.assertEqual(len(schedules), 4)
        base = schedules[0].next_run
        for index, sched in enumerate(schedules):
            offset = (sched.next_run - base).total_seconds()
            self.assertAlmostEqual(offset, index * 60, delta=2)

    def test_send_campaign_batch_schedule_name_is_descriptive(self):
        """Scheduled batches carry a descriptive name (relates to #920)."""
        from email_app.tasks.send_campaign import send_campaign
        send_campaign(self.campaign.pk, batch_size=1)

        schedules = self._batch_schedules()
        # 2 eligible users, batch_size=1 => 2 batches.
        self.assertEqual(len(schedules), 2)
        for sched in schedules:
            self.assertIn('Send campaign batch', sched.name)
            self.assertIn('campaign fan-out', sched.name)
            # The fired Task carries the same name via q_options.task_name.
            self.assertEqual(
                self._kwargs(sched)['q_options']['task_name'], sched.name,
            )

    def test_send_campaign_transitions_to_sending(self):
        """Fan-out transitions campaign from draft -> sending."""
        self.assertEqual(self.campaign.status, 'draft')

        from email_app.tasks.send_campaign import send_campaign
        send_campaign(self.campaign.pk)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sending')
        # sent_at is not set yet — only set when last batch finishes
        self.assertIsNone(self.campaign.sent_at)

    def test_send_campaign_excludes_ineligible_recipients_from_snapshot(self):
        """Unsubscribed and inactive users never enter the durable snapshot."""
        from email_app.tasks.send_campaign import send_campaign

        expected_count = self.campaign.get_recipient_count()
        result = send_campaign(self.campaign.pk)

        # Collect every user_id scheduled across batches.
        chunked = []
        for sched in self._batch_schedules():
            chunked.extend(self._kwargs(sched)['user_ids'])

        self.assertIn(self.user1.pk, chunked)
        self.assertIn(self.user2.pk, chunked)
        # Unsubscribed user MUST NOT be chunked.
        self.assertNotIn(self.user3.pk, chunked)
        self.assertNotIn(self.inactive_user.pk, chunked)
        self.assertEqual(result['total'], expected_count)
        self.assertEqual(self.campaign.deliveries.count(), expected_count)
        self.assertFalse(
            self.campaign.deliveries.filter(
                recipient_user_pk=self.inactive_user.pk,
            ).exists(),
        )

    def test_send_campaign_no_recipients_marks_sent(self):
        """A campaign with zero eligible recipients goes straight to sent."""
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
        # No batch tasks scheduled.
        self.assertEqual(self._batch_schedules(), [])

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

    def test_send_campaign_uses_settings_batch_size(self):
        """When batch_size is omitted, fan-out uses settings.EMAIL_BATCH_SIZE."""
        with override_settings(EMAIL_BATCH_SIZE=1):
            from email_app.tasks.send_campaign import send_campaign
            result = send_campaign(self.campaign.pk)

        # 2 eligible users, batch_size=1 => 2 batches
        self.assertEqual(result['batch_count'], 2)

    def test_send_campaign_uses_db_batch_size_without_redeploy(self):
        IntegrationSetting.objects.create(
            key='EMAIL_BATCH_SIZE', value='1', group='ses',
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)

        from email_app.tasks.send_campaign import send_campaign
        result = send_campaign(self.campaign.pk)

        self.assertEqual(result['batch_count'], 2)

    def test_invalid_runtime_batch_sizes_fall_back_to_200(self):
        from email_app.tasks.send_campaign import _get_batch_size

        for value in ('0', '-2', 'not-a-number'):
            with self.subTest(value=value):
                IntegrationSetting.objects.update_or_create(
                    key='EMAIL_BATCH_SIZE', defaults={
                        'value': value, 'group': 'ses',
                    },
                )
                clear_config_cache()
                with self.assertLogs(
                    'email_app.tasks.send_campaign', 'WARNING',
                ):
                    self.assertEqual(_get_batch_size(), 200)
        self.addCleanup(clear_config_cache)


@tag('core')
class SendCampaignBatchTest(TierSetupMixin, TestCase):
    """Test the chunked send_campaign_batch task."""

    def setUp(self):
        self.user1 = create_user_with_membership(
            email='user1@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=False,
        )
        self.user2 = create_user_with_membership(
            email='user2@test.com', tier=self.basic_tier,
            email_verified=True, unsubscribed=False,
        )
        self.user3 = create_user_with_membership(
            email='user3@test.com', tier=self.free_tier,
            email_verified=True, unsubscribed=True,
        )
        self.campaign = EmailCampaign.objects.create(
            subject='Test Campaign',
            body='# Hello\n\nThis is a test.',
            target_min_level=0,
            status='sending',  # Already in sending state when batch runs
        )

    pass

    pass

    pass

    pass

    pass

    pass

    pass

    def test_batch_not_found_raises_error(self):
        """Batch on a missing campaign raises ValueError."""
        from email_app.tasks.send_campaign import send_campaign_batch

        with self.assertRaises(ValueError) as ctx:
            send_campaign_batch(99999, user_ids=[1], send_delay=0)
        self.assertIn('not found', str(ctx.exception))

    pass

    pass

    pass

    pass

    pass

    pass

    pass

    def test_stale_batch_instances_converge_from_delivery_ledger(self):
        from email_app.tasks.send_campaign import _refresh_campaign_status

        worker_one = EmailCampaign.objects.get(pk=self.campaign.pk)
        worker_two = EmailCampaign.objects.get(pk=self.campaign.pk)
        log_one = EmailLog.objects.create(
            campaign=self.campaign,
            user=self.user1,
            email_type='campaign',
            ses_message_id='one',
        )
        CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=self.user1,
            recipient_user_pk=self.user1.pk,
            recipient_email=self.user1.email,
            state=CampaignDelivery.State.SENT,
            attempt_count=1,
            email_log=log_one,
            ses_message_id='one',
        )
        _refresh_campaign_status(worker_one)
        log_two = EmailLog.objects.create(
            campaign=self.campaign,
            user=self.user2,
            email_type='campaign',
            ses_message_id='two',
        )
        CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=self.user2,
            recipient_user_pk=self.user2.pk,
            recipient_email=self.user2.email,
            state=CampaignDelivery.State.SENT,
            attempt_count=1,
            email_log=log_two,
            ses_message_id='two',
        )
        _refresh_campaign_status(worker_two)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.sent_count, 2)
        self.assertEqual(self.campaign.status, 'sent')


@tag('core')
class EmailLogUniquenessTest(TierSetupMixin, TestCase):
    """Per-recipient idempotency is enforced at the database level."""

    def test_duplicate_campaign_log_raises_integrity_error(self):
        """Two EmailLogs for the same (campaign, user) violate the constraint."""
        from django.db import IntegrityError, transaction

        user = create_user_with_membership(
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
        user = create_user_with_membership(
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

    pass


class CampaignAdminTest(TierSetupMixin, TestCase):
    """Django admin remains low-level and has no campaign delivery actions."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com',
            password='adminpass123',
        )
        self.admin_user.email_verified = True
        set_membership(self.admin_user, tier=self.free_tier)
        self.admin_user.save()
        self.client.force_login(self.admin_user)
        self.campaign = EmailCampaign.objects.create(
            subject='Private Campaign',
            body='Private campaign body',
            target_min_level=0,
            status='draft',
        )

    def test_change_form_has_no_recipient_or_delivery_controls(self):
        response = self.client.get(
            f'/admin/email_app/emailcampaign/{self.campaign.pk}/change/',
        )
        self.assertNotContains(response, 'Campaign Actions')
        self.assertNotContains(response, 'Estimated Recipients')
        self.assertNotContains(response, 'Send Test Email')
        self.assertNotContains(response, 'Send Campaign')

    pass

    def test_registered_admin_model_routes_remain_available(self):
        list_response = self.client.get('/admin/email_app/emailcampaign/')
        change_response = self.client.get(
            f'/admin/email_app/emailcampaign/{self.campaign.pk}/change/',
        )
        self.assertContains(list_response, self.campaign.subject)
        self.assertContains(change_response, 'name="subject"')

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

    pass

    pass

    pass


@tag('core')
class CampaignEligibilityCriteriaTest(TierSetupMixin, TestCase):
    """Campaign send respects tier, verification, and subscription status.

    Moved from playwright_tests/test_email_campaigns.py Scenario 8.
    """

    pass
