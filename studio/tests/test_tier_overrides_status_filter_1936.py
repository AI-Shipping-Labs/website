"""Studio tier overrides list: "Not raising access" filter (issue #1936).

A grant raises access only when its tier is above the member's current
stored tier. Grants at or below it are leftovers: the list counts them,
filters to them, and lets the operator revoke them without losing the
filter.
"""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from community.models import CommunityAuditLog
from payments.models import Tier, TierOverride
from tests.fixtures import set_membership

User = get_user_model()

URL = '/studio/tier_overrides/'


def _make_override(user, tier, *, original_tier, days, granted_by=None):
    return TierOverride.objects.create(
        user=user,
        original_tier=original_tier,
        override_tier=tier,
        expires_at=timezone.now() + timedelta(days=days),
        granted_by=granted_by,
        is_active=True,
    )


class TierOverrideStatusFixture(TestCase):
    """The four-member fixture from the issue.

    A: stored Main, Basic grant (not raising).
    B: stored Free, Premium and Basic grants (both raising).
    C: stored Basic, Basic grant (equal, not raising).
    D: granted Main while Free, stored tier now Premium (not raising).
    """

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')
        cls.staff = User.objects.create_user(
            email='staff-1936@test.com', password='pw', is_staff=True,
        )
        cls.a = User.objects.create_user(email='a-1936@test.com')
        set_membership(cls.a, tier=cls.main)
        cls.b = User.objects.create_user(email='b-1936@test.com')
        set_membership(cls.b, tier=cls.free)
        cls.c = User.objects.create_user(email='c-1936@test.com')
        set_membership(cls.c, tier=cls.basic)
        cls.d = User.objects.create_user(email='d-1936@test.com')
        set_membership(cls.d, tier=cls.premium)

        cls.a_basic = _make_override(
            cls.a, cls.basic, original_tier=cls.main, days=5,
            granted_by=cls.staff,
        )
        cls.b_premium = _make_override(
            cls.b, cls.premium, original_tier=cls.free, days=10,
        )
        cls.b_basic = _make_override(
            cls.b, cls.basic, original_tier=cls.free, days=300,
        )
        cls.c_basic = _make_override(
            cls.c, cls.basic, original_tier=cls.basic, days=20,
        )
        cls.d_main = _make_override(
            cls.d, cls.main, original_tier=cls.free, days=30,
        )

    def setUp(self):
        self.client.login(email='staff-1936@test.com', password='pw')

    def _rows(self, response):
        return [
            (o.user.email, o.override_tier.slug)
            for o in response.context['active_overrides']
        ]

    def _html(self, response):
        return response.content.decode()


class StatusChipsAndCountsTest(TierOverrideStatusFixture):
    def test_all_view_shows_unfiltered_chip_counts_and_suffix(self):
        response = self.client.get(URL)
        html = self._html(response)

        self.assertRegex(
            html,
            r'data-testid="active-overrides-count">\s*5 active · 3 not raising access\s*<',
        )
        self.assertRegex(
            html, r'data-testid="active-overrides-filter-all"[^>]*>All \(5\)</a>',
        )
        self.assertRegex(
            html,
            r'data-testid="active-overrides-filter-effective"[^>]*>Raising access \(2\)</a>',
        )
        self.assertRegex(
            html,
            r'data-testid="active-overrides-filter-ineffective"[^>]*>Not raising access \(3\)</a>',
        )
        self.assertRegex(
            html, r'data-testid="active-overrides-filter-all"\s+aria-current="page"',
        )
        self.assertEqual(len(self._rows(response)), 5)

    def test_ineffective_filter_lists_only_leftover_grants(self):
        response = self.client.get(URL, {'status': 'ineffective'})

        self.assertEqual(
            self._rows(response),
            [
                ('a-1936@test.com', 'basic'),
                ('c-1936@test.com', 'basic'),
                ('d-1936@test.com', 'main'),
            ],
        )
        html = self._html(response)
        self.assertEqual(html.count('data-raises-access="false"'), 3)
        self.assertNotIn('data-raises-access="true"', html)
        self.assertEqual(html.count('data-testid="active-override-ineffective"'), 3)
        self.assertRegex(
            html,
            r'data-testid="active-overrides-filter-ineffective"\s+aria-current="page"',
        )
        self.assertNotRegex(
            html, r'data-testid="active-overrides-filter-all"\s+aria-current',
        )
        # Chip counts stay unfiltered on a filtered view.
        self.assertIn('All (5)', html)

    def test_effective_filter_lists_only_raising_grants(self):
        response = self.client.get(URL, {'status': 'effective'})

        self.assertEqual(
            self._rows(response),
            [('b-1936@test.com', 'premium'), ('b-1936@test.com', 'basic')],
        )
        html = self._html(response)
        self.assertNotIn('data-testid="active-override-ineffective"', html)
        self.assertEqual(html.count('data-raises-access="true"'), 2)

    def test_unknown_status_behaves_like_all(self):
        response = self.client.get(URL, {'status': 'bogus'})

        self.assertEqual(len(self._rows(response)), 5)
        self.assertEqual(response.context['status_filter'], '')
        self.assertRegex(
            self._html(response),
            r'data-testid="active-overrides-filter-all"\s+aria-current="page"',
        )


class OverrideAndBaseTierColumnsTest(TierOverrideStatusFixture):
    def test_header_reads_override_tier(self):
        html = self._html(self.client.get(URL))
        self.assertIn('>Override tier</th>', html)
        self.assertNotIn('Effective tier', html)

    def test_base_tier_shows_current_stored_tier_and_grant_time_tier_for_d(self):
        response = self.client.get(URL)
        html = self._html(response)
        stored = dict(
            re.findall(
                r'data-user-email="([^"]+)"[\s\S]*?'
                r'data-testid="active-override-base-tier-pill"\s+'
                r'data-tier="([a-z]+)"',
                html,
            )
        )
        self.assertEqual(
            stored,
            {
                'a-1936@test.com': 'main',
                'b-1936@test.com': 'free',
                'c-1936@test.com': 'basic',
                'd-1936@test.com': 'premium',
            },
        )
        at_grant = re.findall(
            r'data-testid="active-override-base-tier-at-grant">([^<]+)<', html,
        )
        self.assertEqual(at_grant, ['Free when granted'])
        d_row = html[html.index('data-user-email="d-1936@test.com"'):]
        d_row = d_row[:d_row.index('</tr>')]
        self.assertIn('Free when granted', d_row)

    def test_legacy_grant_without_original_tier_has_no_when_granted_line(self):
        TierOverride.objects.filter(pk=self.d_main.pk).update(original_tier=None)
        html = self._html(self.client.get(URL))
        self.assertNotIn('active-override-base-tier-at-grant', html)


class RevokeKeepsFilterTest(TierOverrideStatusFixture):
    def _revoke(self, override, **extra):
        return self.client.post(
            reverse('studio_user_tier_override_revoke', args=[override.user_id]),
            data={
                'override_id': override.pk,
                'next': 'tier_overrides_list',
                **extra,
            },
        )

    def test_revoke_from_ineffective_filter_returns_to_the_filter(self):
        list_html = self._html(self.client.get(URL, {'status': 'ineffective'}))
        self.assertIn('name="status" value="ineffective"', list_html)

        response = self._revoke(self.a_basic, status='ineffective')

        self.assertRedirects(
            response, f'{URL}?status=ineffective', fetch_redirect_response=False,
        )
        self.a_basic.refresh_from_db()
        self.assertFalse(self.a_basic.is_active)
        self.assertTrue(
            CommunityAuditLog.objects.filter(
                user=self.a, action='tier_override_revoked',
            ).exists()
        )
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Override revoked for a-1936@test.com.')
        self.assertNotIn(
            ('a-1936@test.com', 'basic'), self._rows(followed),
        )
        self.assertContains(followed, 'Not raising access (2)')

    def test_effective_status_is_kept_on_redirect(self):
        response = self._revoke(self.b_basic, status='effective')
        self.assertEqual(response['Location'], f'{URL}?status=effective')

    def test_unknown_status_redirects_to_unfiltered_list(self):
        for value in ('all', 'bogus', '"><script>', ''):
            override = _make_override(
                self.c, self.basic, original_tier=self.basic, days=40,
            )
            response = self._revoke(override, status=value)
            self.assertEqual(response['Location'], URL, value)

    def test_unfiltered_view_does_not_post_a_status(self):
        html = self._html(self.client.get(URL))
        self.assertNotIn('name="status"', html)


class EmptyStatesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.premium = Tier.objects.get(slug='premium')
        User.objects.create_user(
            email='staff-1936@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff-1936@test.com', password='pw')

    def test_filter_with_no_matches_keeps_header_and_shows_clear_link(self):
        member = User.objects.create_user(email='b-only-1936@test.com')
        _make_override(member, self.premium, original_tier=self.free, days=10)
        _make_override(member, self.basic, original_tier=self.free, days=20)

        response = self.client.get(URL, {'status': 'ineffective'})
        html = self._html(response)

        self.assertIn('data-testid="active-overrides-table"', html)
        self.assertIn('>Override tier</th>', html)
        self.assertIn('data-testid="studio-empty-state-filter"', html)
        self.assertIn('No overrides match your filters.', html)
        self.assertRegex(html, r'colspan="7"')
        self.assertRegex(
            html, r'<a href="/studio/tier_overrides/"[^>]*>Clear filters</a>',
        )
        self.assertRegex(
            html, r'data-testid="active-overrides-count">\s*2 active\s*<',
        )

    def test_no_active_overrides_shows_existing_empty_message_without_chips(self):
        response = self.client.get(URL, {'status': 'ineffective'})
        html = self._html(response)

        self.assertIn('data-testid="active-overrides-empty"', html)
        self.assertIn(
            'No active overrides right now. Use the search above to grant one.',
            html,
        )
        self.assertNotIn('data-testid="active-overrides-filter"', html)
        self.assertNotIn('studio-empty-state-filter', html)

    def _html(self, response):
        return response.content.decode()


class StatusFollowsStoredTierTest(TierOverrideStatusFixture):
    def test_grant_raises_access_again_after_stored_tier_drops(self):
        before = self.client.get(URL, {'status': 'ineffective'})
        self.assertIn(('a-1936@test.com', 'basic'), self._rows(before))

        set_membership(self.a, tier=self.free)

        effective = self.client.get(URL, {'status': 'effective'})
        self.assertIn(('a-1936@test.com', 'basic'), self._rows(effective))
        ineffective = self.client.get(URL, {'status': 'ineffective'})
        self.assertNotIn(('a-1936@test.com', 'basic'), self._rows(ineffective))
        self.a_basic.refresh_from_db()
        self.assertTrue(self.a_basic.is_active)


class QueryBudgetTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.staff = User.objects.create_user(
            email='staff-1936@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff-1936@test.com', password='pw')

    def _seed(self, count):
        for index in range(count):
            member = User.objects.create_user(
                email=f'budget-{count}-{index}-1936@test.com',
                first_name='Budget',
            )
            set_membership(member, tier=self.main if index % 2 else self.free)
            _make_override(
                member, self.basic, original_tier=self.free,
                days=index + 1, granted_by=self.staff,
            )

    def _query_count(self, params):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(URL, params)
        self.assertTrue(response.context['active_overrides'])
        return len(ctx.captured_queries)

    def test_query_count_is_constant_in_row_count(self):
        self._seed(2)
        # Warm-up: the first request of a session does one-off work
        # (session/sidebar bookkeeping) unrelated to the row count.
        self.client.get(URL)
        small = {
            status: self._query_count({'status': status} if status else {})
            for status in ('', 'effective', 'ineffective')
        }
        self._seed(6)
        large = {
            status: self._query_count({'status': status} if status else {})
            for status in ('', 'effective', 'ineffective')
        }
        self.assertEqual(small, large)


class AccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user(email='member-1936@test.com', password='pw')

    def test_anonymous_is_redirected_to_login(self):
        for status in ('', 'effective', 'ineffective'):
            response = self.client.get(URL, {'status': status})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response['Location'].startswith('/accounts/login/'))

    def test_non_staff_is_forbidden(self):
        self.client.login(email='member-1936@test.com', password='pw')
        for status in ('', 'effective', 'ineffective'):
            response = self.client.get(URL, {'status': status})
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(b'active-overrides-section', response.content)
