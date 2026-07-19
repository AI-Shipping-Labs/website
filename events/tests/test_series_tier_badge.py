"""Tests for the per-session tier badge on the series page (issue #956).

The series page (`templates/events/event_series.html`) renders a tier badge
on every session row so visitors — including anonymous ones — can see the
Free-vs-paid split across sibling sessions of the same program. The badge
text comes from ``LEVEL_TO_PUBLIC_LABEL`` via the ``required_tier_label``
filter; no hardcoded tier strings live in the template.

Covers:
- One ``series-event-tier`` badge per visible session, each carrying a
  ``data-required-level`` equal to the event's ``required_level``.
- Free (level 0) badge: neutral styling, "Free" copy, no lock icon.
- Gated badges (10/20/30): accent styling, lock icon, public label.
- Visible to anonymous visitors and to authenticated members regardless
  of whether they can access the session.
- Regression guards: the aggregate ``series-tier-note`` and the
  per-session state pills are unchanged.
"""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
)
from events.models import Event, EventSeries
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_series(**kwargs):
    defaults = {
        'name': 'LLM Zoomcamp Office Hours',
        'slug': 'llm-zoomcamp-office-hours',
        'start_time': timezone.now().time(),
        'timezone': 'Europe/Berlin',
    }
    defaults.update(kwargs)
    return EventSeries.objects.create(**defaults)


def _make_occurrence(series, *, offset_days, position, required_level,
                     status='upcoming', slug=None):
    start = timezone.now() + timedelta(days=offset_days)
    return Event.objects.create(
        title=f'{series.name} — Session {position}',
        slug=slug or f'{series.slug}-session-{position}',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        required_level=required_level,
        event_series=series,
        series_position=position,
    )


def _badge_html_for_level(html, level):
    """Return the ``series-event-tier`` <span> markup for a given level.

    Matches the span carrying ``data-required-level="<level>"`` so the
    test can assert on that specific badge's icon/copy in isolation.
    """
    pattern = re.compile(
        r'<span[^>]*data-testid="series-event-tier"[^>]*'
        r'data-required-level="%d"[^>]*>(.*?)</span>' % level,
        re.DOTALL,
    )
    match = pattern.search(html)
    return match.group(0) if match else None


@tag('core')
class SeriesTierBadgeAnonymousTest(TestCase):
    """Anonymous visitors see a per-session tier badge for every session."""

    @classmethod
    def setUpTestData(cls):
        cls.series = _make_series()
        cls.free = _make_occurrence(
            cls.series, offset_days=7, position=1,
            required_level=LEVEL_OPEN, slug='oh-free',
        )
        cls.basic = _make_occurrence(
            cls.series, offset_days=14, position=2,
            required_level=LEVEL_BASIC, slug='oh-basic',
        )
        cls.main = _make_occurrence(
            cls.series, offset_days=21, position=3,
            required_level=LEVEL_MAIN, slug='oh-main',
        )
        cls.premium = _make_occurrence(
            cls.series, offset_days=28, position=4,
            required_level=LEVEL_PREMIUM, slug='oh-premium',
        )
        cls.url = cls.series.get_absolute_url()

    def setUp(self):
        self.response = self.client.get(self.url)
        self.html = self.response.content.decode()

    def test_one_badge_per_visible_session(self):
        self.assertEqual(self.response.status_code, 200)
        # Four published sessions -> four tier badges.
        self.assertEqual(self.html.count('data-testid="series-event-tier"'), 4)
        self.assertEqual(self.html.count('data-component="member-badge"'), 4)

    def test_each_badge_carries_matching_required_level(self):
        for level in (LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN, LEVEL_PREMIUM):
            with self.subTest(level=level):
                self.assertIsNotNone(
                    _badge_html_for_level(self.html, level),
                    f'no tier badge with data-required-level="{level}"',
                )

    def test_free_badge_reads_free_with_no_lock_icon(self):
        badge = _badge_html_for_level(self.html, LEVEL_OPEN)
        self.assertIn('Free', badge)
        self.assertNotIn('data-lucide="lock"', badge)
        # Free access uses the shared success treatment, not the paid accent.
        self.assertIn('bg-green-500/15', badge)
        self.assertIn('data-lucide="badge-check"', badge)
        self.assertNotIn('text-accent', badge)

    def test_basic_badge_reads_public_label_with_lock(self):
        badge = _badge_html_for_level(self.html, LEVEL_BASIC)
        self.assertIn('Basic or above', badge)
        self.assertIn('data-lucide="lock"', badge)
        self.assertIn('text-accent', badge)

    def test_main_badge_reads_public_label_with_lock(self):
        badge = _badge_html_for_level(self.html, LEVEL_MAIN)
        self.assertIn('Main or above', badge)
        self.assertIn('data-lucide="lock"', badge)
        self.assertIn('text-accent', badge)

    def test_premium_badge_reads_public_label_with_lock(self):
        badge = _badge_html_for_level(self.html, LEVEL_PREMIUM)
        self.assertIn('Premium', badge)
        self.assertIn('data-lucide="lock"', badge)
        self.assertIn('text-accent', badge)

    def test_free_and_gated_rows_differ_so_split_is_visible(self):
        free_badge = _badge_html_for_level(self.html, LEVEL_OPEN)
        main_badge = _badge_html_for_level(self.html, LEVEL_MAIN)
        self.assertNotEqual(free_badge, main_badge)
        self.assertIn('Free', free_badge)
        self.assertIn('Main or above', main_badge)


@tag('core')
class SeriesTierBadgeAuthenticatedTest(TierSetupMixin, TestCase):
    """Authenticated members see the badge regardless of access; the
    existing aggregate note and state pills are unchanged."""

    def setUp(self):
        self.series = _make_series()
        self.free = _make_occurrence(
            self.series, offset_days=7, position=1,
            required_level=LEVEL_OPEN, slug='oh-free',
        )
        self.main = _make_occurrence(
            self.series, offset_days=14, position=2,
            required_level=LEVEL_MAIN, slug='oh-main',
        )
        self.url = self.series.get_absolute_url()

    def _login(self, tier):
        user = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )
        user.tier = tier
        user.save()
        self.client.force_login(user)
        return user

    def test_free_member_sees_both_badges_and_aggregate_note(self):
        self._login(self.free_tier)
        response = self.client.get(self.url)
        html = response.content.decode()

        # Both badges render even though the Main session is out of reach.
        self.assertIn('Free', _badge_html_for_level(html, LEVEL_OPEN))
        self.assertIn('Main or above', _badge_html_for_level(html, LEVEL_MAIN))

        # The aggregate authenticated-only note is unchanged.
        self.assertContains(response, 'series-tier-note')
        self.assertContains(response, 'require a higher tier')

        # The gated session shows the unchanged "Upgrade to register" pill.
        self.assertContains(response, 'series-event-state-no-access')

    def test_free_member_sees_gated_badge_and_upgrade_pill_together(self):
        self._login(self.free_tier)
        response = self.client.get(self.url)
        html = response.content.decode()
        # Informational badge (left) AND action pill (right) both present.
        self.assertIsNotNone(_badge_html_for_level(html, LEVEL_MAIN))
        self.assertIn('series-event-state-no-access', html)

    def test_main_member_sees_badges_and_no_aggregate_note(self):
        self._login(self.main_tier)
        response = self.client.get(self.url)
        html = response.content.decode()

        # Tier badges still render for every row.
        self.assertIsNotNone(_badge_html_for_level(html, LEVEL_OPEN))
        self.assertIsNotNone(_badge_html_for_level(html, LEVEL_MAIN))

        # No "require a higher tier" note for a member who can access all.
        self.assertNotContains(response, 'series-tier-note')
        # The previously gated session is now registrable, not gated.
        self.assertNotContains(response, 'series-event-state-no-access')
