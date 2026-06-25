"""Tests for the event-registrant campaign audience (issue #1076).

Covers ``EmailCampaign.get_eligible_recipients`` when ``target_event`` is set:
the base set becomes the event's registrants, with the existing
tier/unsubscribe/verification filters ANDing on top.
"""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from email_app.models import EmailCampaign
from events.models import Event, EventRegistration
from tests.fixtures import TierSetupMixin

User = get_user_model()
UTC = dt_timezone.utc


@tag('core')
class CampaignEventAudienceTest(TierSetupMixin, TestCase):
    """``target_event`` scopes the audience to that event's registrants."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Shipping Agents Workshop',
            slug='shipping-agents-workshop',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=agents',
        )
        cls.other_event = Event.objects.create(
            title='Unrelated Event',
            slug='unrelated-event',
            start_datetime=datetime(2026, 5, 1, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 5, 1, 17, 0, tzinfo=UTC),
            status='completed',
        )

    def _register(self, email, *, tier=None, unsubscribed=False,
                  email_verified=True, event=None):
        user = User.objects.create_user(
            email=email,
            tier=tier or self.free_tier,
            email_verified=email_verified,
            unsubscribed=unsubscribed,
        )
        EventRegistration.objects.create(event=event or self.event, user=user)
        return user

    def test_target_event_returns_only_registrants(self):
        reg_a = self._register('a@test.com')
        reg_b = self._register('b@test.com')
        # A non-registrant verified user must NOT be in the audience.
        User.objects.create_user(email='outsider@test.com', tier=self.free_tier,
                                 email_verified=True)
        # A registrant of a different event is excluded.
        self._register('other@test.com', event=self.other_event)

        campaign = EmailCampaign.objects.create(
            subject='Recording', body='Hi', target_event=self.event,
        )
        recipient_ids = set(
            campaign.get_eligible_recipients().values_list('pk', flat=True)
        )
        self.assertEqual(recipient_ids, {reg_a.pk, reg_b.pk})
        self.assertEqual(campaign.get_recipient_count(), 2)

    def test_null_target_event_uses_tier_audience(self):
        """With target_event NULL, behavior is the historical tier audience."""
        self._register('reg@test.com')
        User.objects.create_user(email='nonreg@test.com', tier=self.free_tier,
                                 email_verified=True)
        campaign = EmailCampaign.objects.create(
            subject='Tier', body='Hi', target_min_level=0, target_event=None,
        )
        # Both verified subscribed users qualify — registration is irrelevant.
        self.assertEqual(campaign.get_recipient_count(), 2)

    def test_tier_filter_ands_with_registrant_set(self):
        """target_min_level=20 narrows registrants to Main+ only."""
        self._register('free@test.com', tier=self.free_tier)
        main_a = self._register('main-a@test.com', tier=self.main_tier)
        main_b = self._register('main-b@test.com', tier=self.main_tier)
        # A Main user who did NOT register must not appear (proves the
        # tier filter ANDs with the registrant set, not unions).
        User.objects.create_user(email='main-outsider@test.com',
                                 tier=self.main_tier, email_verified=True)

        campaign = EmailCampaign.objects.create(
            subject='Paid recording', body='Hi',
            target_event=self.event, target_min_level=20,
        )
        recipient_ids = set(
            campaign.get_eligible_recipients().values_list('pk', flat=True)
        )
        self.assertEqual(recipient_ids, {main_a.pk, main_b.pk})

        # Clearing the tier filter back to everyone restores all registrants.
        campaign.target_min_level = 0
        campaign.save(update_fields=['target_min_level'])
        self.assertEqual(campaign.get_recipient_count(), 3)

    def test_unsubscribed_registrant_excluded(self):
        self._register('sub-a@test.com')
        self._register('sub-b@test.com')
        self._register('gone@test.com', unsubscribed=True)
        campaign = EmailCampaign.objects.create(
            subject='Recording', body='Hi', target_event=self.event,
        )
        self.assertEqual(campaign.get_recipient_count(), 2)

    def test_unverified_registrant_excluded_by_default(self):
        self._register('verified@test.com')
        self._register('unverified@test.com', email_verified=False)
        campaign = EmailCampaign.objects.create(
            subject='Recording', body='Hi', target_event=self.event,
        )
        self.assertEqual(campaign.get_recipient_count(), 1)

        # ``everyone`` audience_verification drops the verified-only filter.
        campaign.audience_verification = (
            EmailCampaign.AUDIENCE_VERIFICATION_EVERYONE
        )
        campaign.save(update_fields=['audience_verification'])
        self.assertEqual(campaign.get_recipient_count(), 2)

    def test_event_with_no_registrants_is_empty_audience(self):
        empty_event = Event.objects.create(
            title='Empty', slug='empty-event',
            start_datetime=datetime(2026, 7, 1, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 7, 1, 17, 0, tzinfo=UTC),
            status='completed',
        )
        campaign = EmailCampaign.objects.create(
            subject='Recording', body='Hi', target_event=empty_event,
        )
        self.assertEqual(campaign.get_recipient_count(), 0)
