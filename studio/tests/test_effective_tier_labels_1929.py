"""Studio surfaces label the effective tier, overrides included (issue #1929).

Global search, the user list/detail, the CRM list/detail and the event
registrations roster/CSV all resolve active tier overrides through the
shared ``content.access`` resolver, so they agree with access checks.
"""

import csv
import datetime
import io
import re

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from freezegun import freeze_time

from crm.models import CRMRecord
from events.models import Event, EventRegistration
from payments.models import TierOverride
from studio.views.global_search import _user_results
from tests.fixtures import StaffUserMixin, TierSetupMixin, set_membership

User = get_user_model()

FROZEN_NOW = '2026-10-09 12:00:00'
UTC = datetime.timezone.utc


def _at(month, day, year=2026):
    return datetime.datetime(year, month, day, 12, 0, tzinfo=UTC)


def _make_user(email, tier, **extra):
    user = User.objects.create_user(email=email, password='pw', **extra)
    set_membership(user, tier=tier)
    return user


def _grant(user, tier, expires_at, **extra):
    return TierOverride.objects.create(
        user=user, override_tier=tier, expires_at=expires_at, **extra,
    )


@freeze_time(FROZEN_NOW)
class GlobalSearchTierLabelTest(TierSetupMixin, StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        verified = {'email_verified': True}
        cls.this_year = _make_user('oct-override@test.com', cls.free_tier, **verified)
        _grant(cls.this_year, cls.main_tier, _at(10, 30))

        cls.next_year = _make_user('jan-override@test.com', cls.free_tier, **verified)
        _grant(cls.next_year, cls.main_tier, _at(1, 15, year=2027))

        cls.lapsed = _make_user('lapsed-override@test.com', cls.free_tier, **verified)
        _grant(cls.lapsed, cls.main_tier, _at(10, 8), is_active=True)

        cls.revoked = _make_user('revoked-override@test.com', cls.free_tier, **verified)
        _grant(cls.revoked, cls.main_tier, _at(10, 30), is_active=False)

        cls.premium_sub = _make_user('premium-sub@test.com', cls.premium_tier, **verified)
        _grant(cls.premium_sub, cls.main_tier, _at(10, 30))

        cls.two_grants = _make_user('two-grants@test.com', cls.free_tier, **verified)
        _grant(cls.two_grants, cls.premium_tier, _at(11, 5))
        _grant(cls.two_grants, cls.basic_tier, _at(12, 20), source='maven:cohort-1')

        cls.no_tier = _make_user('no-tier@test.com', None, **verified)

    def _metadata_for(self, email):
        self.client.login(**self.staff_credentials)
        response = self.client.get(reverse('studio_global_search'), {'q': email})
        users = response.json()['results']['users']
        self.assertEqual([item['summary'] for item in users], [email])
        return users[0]['metadata']

    def test_override_expiring_this_year_shows_month_and_day(self):
        self.assertEqual(
            self._metadata_for('oct-override@test.com'),
            'Main (override until Oct 30) · verified · No bounce',
        )

    def test_override_expiring_next_year_includes_the_year(self):
        self.assertEqual(
            self._metadata_for('jan-override@test.com'),
            'Main (override until Jan 15, 2027) · verified · No bounce',
        )

    def test_expired_revoked_and_not_above_base_overrides_show_base_tier(self):
        cases = (
            ('lapsed-override@test.com', 'Free · verified · No bounce'),
            ('revoked-override@test.com', 'Free · verified · No bounce'),
            ('premium-sub@test.com', 'Premium · verified · No bounce'),
        )
        for email, expected in cases:
            with self.subTest(email=email):
                self.assertEqual(self._metadata_for(email), expected)

    def test_strongest_of_several_overrides_wins_with_its_expiry(self):
        self.assertEqual(
            self._metadata_for('two-grants@test.com'),
            'Premium (override until Nov 5) · verified · No bounce',
        )

    def test_user_without_tier_keeps_existing_free_format(self):
        self.assertEqual(
            self._metadata_for('no-tier@test.com'),
            'Free · verified · No bounce',
        )


@freeze_time(FROZEN_NOW)
class GlobalSearchUserQueryCountTest(TierSetupMixin, TestCase):
    """Override resolution is one batched query, whatever the row count."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for index in range(8):
            user = _make_user(f'batch-{index}@crowd.test', cls.free_tier)
            _grant(user, cls.main_tier, _at(10, 30))
        lone = _make_user('lone@solo.test', cls.free_tier)
        _grant(lone, cls.main_tier, _at(10, 30))

    def test_query_count_is_constant_for_one_and_eight_overridden_users(self):
        with CaptureQueriesContext(connection) as one_user:
            one = _user_results('solo.test')
        with CaptureQueriesContext(connection) as eight_users:
            eight = _user_results('crowd.test')

        self.assertEqual(len(one), 1)
        self.assertEqual(len(eight), 8)
        self.assertTrue(all(
            item['metadata'].startswith('Main (override until Oct 30)')
            for item in one + eight
        ))
        self.assertEqual(len(one_user.captured_queries), 2)
        self.assertEqual(
            len(eight_users.captured_queries), len(one_user.captured_queries),
        )


class CoexistingGrantsAcrossStudioTest(TierSetupMixin, StaffUserMixin, TestCase):
    """A later Maven Basic grant never hides an earlier staff Premium grant."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = _make_user('maven-and-staff@test.com', cls.free_tier)
        with freeze_time('2026-09-20 12:00:00'):
            _grant(cls.member, cls.premium_tier, _at(12, 1, year=2099))
        with freeze_time('2026-10-01 12:00:00'):
            _grant(
                cls.member, cls.basic_tier, _at(12, 31, year=2099),
                source='maven:cohort-1',
            )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_user_list_row_shows_strongest_override(self):
        response = self.client.get(
            '/studio/users/', {'q': 'maven-and-staff@test.com'},
        )

        rows = response.context['user_rows']
        self.assertEqual(
            [(row['email'], row['tier_name'], row['tier_slug']) for row in rows],
            [('maven-and-staff@test.com', 'Premium', 'premium')],
        )

    def test_user_list_premium_filter_uses_strongest_override(self):
        response = self.client.get('/studio/users/', {'filter': 'premium'})

        emails = [row['email'] for row in response.context['user_rows']]
        self.assertIn('maven-and-staff@test.com', emails)

    def test_user_detail_pill_shows_strongest_override(self):
        response = self.client.get(
            reverse('studio_user_detail', kwargs={'user_id': self.member.pk}),
        )

        self.assertEqual(response.context['tier_name'], 'Premium')
        self.assertEqual(response.context['tier_slug'], 'premium')

    def test_crm_detail_shows_strongest_override(self):
        record = CRMRecord.objects.create(user=self.member, created_by=self.staff)

        response = self.client.get(
            reverse('studio_crm_detail', kwargs={'crm_id': record.pk}),
        )

        self.assertEqual(response.context['tier_name'], 'Premium')
        self.assertEqual(response.context['tier_source'], 'override')


class CRMListOverrideQueryCountTest(TierSetupMixin, StaffUserMixin, TestCase):
    """The CRM list resolves overrides for the whole page in one query."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        expires = _at(12, 31, year=2099)
        cls.members = []
        for index in range(6):
            member = _make_user(f'crm-{index}@test.com', cls.free_tier)
            _grant(member, cls.main_tier, expires)
            cls.members.append(member)
        cls.stripe_member = _make_user('crm-stripe@test.com', cls.basic_tier)
        set_membership(cls.stripe_member, stripe_customer_id='cus_CRM1929')

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def _list_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/studio/crm/', {'filter': 'all'})
        return response, len(ctx.captured_queries)

    def test_query_count_does_not_grow_with_overridden_rows(self):
        CRMRecord.objects.create(user=self.members[0], created_by=self.staff)
        # Warm-up request: the first staff request of a session runs one-off
        # queries that would skew the comparison.
        self._list_queries()
        _, one_row_queries = self._list_queries()

        for member in self.members[1:]:
            CRMRecord.objects.create(user=member, created_by=self.staff)
        response, many_row_queries = self._list_queries()

        self.assertEqual(len(response.context['rows']), 6)
        self.assertEqual(many_row_queries, one_row_queries)

    def test_rows_show_override_and_base_sources(self):
        CRMRecord.objects.create(user=self.members[0], created_by=self.staff)
        CRMRecord.objects.create(user=self.stripe_member, created_by=self.staff)

        response = self.client.get('/studio/crm/', {'filter': 'all'})

        rows = {row['email']: row for row in response.context['rows']}
        self.assertEqual(
            (rows['crm-0@test.com']['tier_name'], rows['crm-0@test.com']['tier_source']),
            ('Main', 'override'),
        )
        self.assertEqual(
            (rows['crm-stripe@test.com']['tier_name'], rows['crm-stripe@test.com']['tier_source']),
            ('Basic', 'stripe'),
        )


def _testid_cell_texts(html, testid):
    """Return the whitespace-normalised visible text of each ``td``/``dd``."""
    pattern = re.compile(
        rf'<(td|dd)\b[^>]*data-testid="{testid}"[^>]*>(.*?)</\1>', re.DOTALL,
    )
    return [
        ' '.join(re.sub(r'<[^>]+>', ' ', match.group(2)).split())
        for match in pattern.finditer(html)
    ]


@freeze_time(FROZEN_NOW)
class EventRegistrationsEffectiveTierTest(TierSetupMixin, StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Tier Roster Event',
            slug='tier-roster-event',
            start_datetime=_at(10, 20),
        )
        cls.overridden = _make_user('roster-override@test.com', cls.free_tier)
        _grant(cls.overridden, cls.main_tier, _at(10, 30))
        cls.basic = _make_user('roster-basic@test.com', cls.basic_tier)
        newer = EventRegistration.objects.create(event=cls.event, user=cls.overridden)
        older = EventRegistration.objects.create(event=cls.event, user=cls.basic)
        EventRegistration.objects.filter(pk=newer.pk).update(registered_at=_at(10, 5))
        EventRegistration.objects.filter(pk=older.pk).update(registered_at=_at(10, 4))

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_roster_table_and_cards_show_effective_tier_with_override_suffix(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        html = response.content.decode()

        expected = ['Main (override until Oct 30)', 'Basic']
        self.assertEqual(_testid_cell_texts(html, 'registration-tier'), expected)
        self.assertEqual(
            _testid_cell_texts(html, 'registration-card-tier'), expected,
        )

    def test_roster_resolves_overrides_in_one_query_for_all_registrants(self):
        # Warm-up request: one-off first-request queries would skew the count.
        self.client.get(f'/studio/events/{self.event.pk}/edit')
        with CaptureQueriesContext(connection) as two_registrants:
            self.client.get(f'/studio/events/{self.event.pk}/edit')
        for index in range(3):
            member = _make_user(f'roster-extra-{index}@test.com', self.free_tier)
            _grant(member, self.premium_tier, _at(11, 1))
            EventRegistration.objects.create(event=self.event, user=member)
        with CaptureQueriesContext(connection) as five_registrants:
            self.client.get(f'/studio/events/{self.event.pk}/edit')

        override_queries = [
            query['sql'] for query in five_registrants.captured_queries
            if 'accounts_tieroverride' in query['sql']
        ]
        self.assertEqual(len(override_queries), 1)
        self.assertEqual(
            len(five_registrants.captured_queries),
            len(two_registrants.captured_queries),
        )

    def test_csv_tier_column_is_effective_name_without_suffix(self):
        response = self.client.get(
            f'/studio/events/{self.event.pk}/registrations.csv',
        )
        rows = list(csv.reader(io.StringIO(response.content.decode())))

        self.assertEqual(
            rows[0], ['email', 'name', 'registered_at', 'tier', 'joined_at'],
        )
        self.assertEqual(
            [(row[0], row[3]) for row in rows[1:]],
            [
                ('roster-override@test.com', 'Main'),
                ('roster-basic@test.com', 'Basic'),
            ],
        )
