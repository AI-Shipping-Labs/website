"""Tests for the public sprint detail page (issue #443).

The detail page renders one of four CTAs based on viewer state and is
the entry point for self-join. These tests cover the four CTA branches,
the draft-status hiding rule, and the tier-name rendering.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from payments.models import Tier
from plans.models import Sprint, SprintEnrollment

User = get_user_model()


def _premium_user(email):
    """Create a Premium-tier user so eligibility tests pass."""
    user = User.objects.create_user(email=email, password='pw')
    user.tier = Tier.objects.get(slug='premium')
    user.save(update_fields=['tier'])
    return user


def _free_user(email):
    user = User.objects.create_user(email=email, password='pw')
    # New users default to ``free`` already; explicit assignment for clarity.
    user.tier = Tier.objects.get(slug='free')
    user.save(update_fields=['tier'])
    return user


class SprintDetailAnonymousTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            status='active', min_tier_level=30,
        )

    def test_anonymous_sees_login_cta(self):
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-cta-login"')
        self.assertContains(response, '/accounts/login/?next=/sprints/may-2026')
        self.assertNotContains(response, 'data-testid="sprint-cta-join"')


class SprintDetailDraftHidingTest(TestCase):
    """Draft sprints are hidden from anonymous and non-staff users."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Draft', slug='draft',
            start_date=datetime.date(2026, 5, 1),
            status='draft',
        )

    def test_draft_returns_404_for_anonymous(self):
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_draft_returns_404_for_member(self):
        member = User.objects.create_user(email='m@test.com', password='pw')
        self.client.force_login(member)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_draft_renders_for_staff(self):
        staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class SprintDetailUnderTierTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Premium-only', slug='premium-only',
            start_date=datetime.date(2026, 5, 1),
            status='active', min_tier_level=30,
        )
        cls.free_user = _free_user('free@test.com')

    def test_under_tier_user_sees_upgrade_cta(self):
        self.client.force_login(self.free_user)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-cta-upgrade"')
        self.assertContains(response, 'Upgrade to Premium to join')
        self.assertContains(response, 'href="/pricing"')
        self.assertNotContains(response, 'data-testid="sprint-cta-join"')


class SprintDetailEligibleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Premium-only', slug='premium-only',
            start_date=datetime.date(2026, 5, 1),
            status='active', min_tier_level=30,
        )
        cls.premium_user = _premium_user('p@test.com')

    def test_eligible_not_enrolled_sees_join_button(self):
        self.client.force_login(self.premium_user)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-cta-join"')
        self.assertNotContains(response, 'data-testid="sprint-cta-upgrade"')
        self.assertNotContains(response, 'data-testid="sprint-cta-enrolled"')

    def test_enrolled_sees_leave_button_and_board_link(self):
        SprintEnrollment.objects.create(
            sprint=self.sprint, user=self.premium_user,
        )
        self.client.force_login(self.premium_user)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-cta-enrolled"')
        self.assertContains(response, 'data-testid="sprint-cta-leave"')
        self.assertContains(response, 'data-testid="sprint-cta-board"')


class SprintDetailTierBadgeTest(TestCase):
    """The tier-name badge mirrors LEVEL_TO_TIER_NAME."""

    def test_main_tier_badge_when_min_is_20(self):
        sprint = Sprint.objects.create(
            name='Main+', slug='main-only',
            start_date=datetime.date(2026, 5, 1),
            status='active', min_tier_level=20,
        )
        url = reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug})
        response = self.client.get(url)
        self.assertContains(response, 'Main tier required')
