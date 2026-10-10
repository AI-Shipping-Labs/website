"""Studio's "this user is comped" signals count only effective overrides
(issue #1933).

An override is effective only when the strongest active override is above
the user's stored base tier (``EffectiveTier.is_override``). An override at
or below the base tier raises nothing, so it drives no Override pill, badge,
CSV suffix, or ``Override (comped)`` count -- but it stays visible and
revocable in the user detail override block.
"""

import csv
import datetime
import io
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMRecord
from events.models import Event, EventRegistration
from payments.models import TierOverride
from tests.fixtures import StaffUserMixin, TierSetupMixin, set_membership

User = get_user_model()


def _make_user(email, tier, **membership):
    user = User.objects.create_user(email=email, password='pw')
    set_membership(user, tier=tier, **membership)
    return user


def _grant(user, tier, days=30, **extra):
    return TierOverride.objects.create(
        user=user, override_tier=tier,
        expires_at=timezone.now() + datetime.timedelta(days=days), **extra,
    )


class OverrideFixtureMixin(TierSetupMixin, StaffUserMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Stored Main, no Stripe, leftover Basic comp: not effective.
        cls.leftover = _make_user('leftover@test.com', cls.main_tier)
        cls.leftover_override = _grant(cls.leftover, cls.basic_tier)
        # Free base, staff Premium plus Maven Basic: effective Premium.
        cls.stacked = _make_user('stacked@test.com', cls.free_tier)
        cls.stacked_premium = _grant(cls.stacked, cls.premium_tier, days=20)
        cls.stacked_basic = _grant(
            cls.stacked, cls.basic_tier, days=300, source='maven:cohort-3',
        )
        # Free base with a Main comp: effective Main (regression guard).
        cls.comped_main = _make_user('comped-main@test.com', cls.free_tier)
        _grant(cls.comped_main, cls.main_tier)
        # Paying Main subscriber with a higher Premium override: Paid only.
        cls.paid_plus = _make_user(
            'paid-plus@test.com', cls.main_tier,
            subscription_id='sub_paid_plus', stripe_customer_id='cus_paid',
        )
        _grant(cls.paid_plus, cls.premium_tier)

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def _row(self, email):
        response = self.client.get('/studio/users/', {'q': email})
        rows = [r for r in response.context['user_rows'] if r['email'] == email]
        self.assertEqual(len(rows), 1)
        return rows[0], response

    def _detail(self, user):
        return self.client.get(
            reverse('studio_user_detail', kwargs={'user_id': user.pk}),
        )


class IneffectiveOverrideSignalTest(OverrideFixtureMixin, TestCase):
    def test_users_list_row_shows_base_tier_without_override_pill(self):
        row, response = self._row('leftover@test.com')

        self.assertEqual((row['tier_name'], row['tier_source']), ('Main', 'default'))
        self.assertNotContains(response, 'data-testid="user-list-tier-override-pill"')

    def test_user_detail_has_no_override_badge_or_base_line(self):
        response = self._detail(self.leftover)

        self.assertEqual(response.context['tier_source'], 'default')
        self.assertContains(response, 'data-tier-source="default"')
        self.assertNotContains(response, 'data-testid="user-detail-tier-base"')

    def test_user_detail_override_block_explains_and_keeps_revoke(self):
        response = self._detail(self.leftover)
        html = response.content.decode()

        note = re.search(
            r'data-testid="user-detail-tier-override-ineffective">(.*?)</p>',
            html, re.DOTALL,
        )
        self.assertIsNotNone(note)
        self.assertEqual(
            ' '.join(note.group(1).split()),
            'Not raising access: the stored tier (Main) is already at or above Basic.',
        )
        self.assertContains(
            response,
            f'name="override_id" value="{self.leftover_override.pk}"',
        )
        self.assertContains(response, 'data-testid="user-detail-tier-override-revoke"')

    def test_revoking_ineffective_override_keeps_main(self):
        self.client.post(
            reverse('studio_user_tier_override_revoke', kwargs={'user_id': self.leftover.pk}),
            {'override_id': self.leftover_override.pk},
        )

        response = self._detail(self.leftover)
        self.assertIsNone(response.context['active_override'])
        self.assertEqual(response.context['tier_name'], 'Main')
        self.assertNotContains(
            response, 'data-testid="user-detail-tier-override-revoke"',
        )

    def test_csv_export_suffixes_only_effective_overrides(self):
        response = self.client.get(reverse('studio_user_export'))
        rows = {
            row['email']: row['tier']
            for row in csv.DictReader(io.StringIO(response.content.decode()))
        }

        self.assertEqual(rows['leftover@test.com'], 'Main')
        self.assertEqual(rows['stacked@test.com'], 'Premium (override)')
        self.assertEqual(rows['comped-main@test.com'], 'Main (override)')


class EffectiveOverrideSignalTest(OverrideFixtureMixin, TestCase):
    def test_effective_override_keeps_pill_and_badge(self):
        row, response = self._row('comped-main@test.com')
        self.assertEqual((row['tier_name'], row['tier_source']), ('Main', 'override'))
        self.assertContains(response, 'data-testid="user-list-tier-override-pill"')

        detail = self._detail(self.comped_main)
        self.assertContains(detail, 'data-tier-source="override"')
        self.assertContains(detail, 'data-testid="user-detail-tier-base"')
        self.assertNotContains(
            detail, 'data-testid="user-detail-tier-override-ineffective"',
        )

    def test_revoking_staff_premium_falls_back_to_maven_basic(self):
        self.client.post(
            reverse('studio_user_tier_override_revoke', kwargs={'user_id': self.stacked.pk}),
            {'override_id': self.stacked_premium.pk},
        )

        response = self._detail(self.stacked)
        self.assertEqual(response.context['tier_name'], 'Basic')
        self.assertEqual(response.context['tier_source'], 'override')
        self.assertEqual(response.context['active_override'].pk, self.stacked_basic.pk)


class CompedCountsTest(OverrideFixtureMixin, TestCase):
    def _counts(self):
        return self.client.get('/studio/users/').context

    def test_comped_cells_count_only_effective_overrides(self):
        ctx = self._counts()

        # comped-main -> Main; stacked -> Premium (its strongest override);
        # leftover (Basic below stored Main) and paid-plus (subscriber) are
        # in no comped cell.
        self.assertEqual(
            (ctx['override_basic'], ctx['override_main'], ctx['override_premium']),
            (0, 1, 1),
        )
        self.assertEqual(ctx['total_comped'], 2)

    def test_subscriber_with_higher_override_counts_only_as_paid(self):
        ctx = self._counts()

        self.assertEqual(ctx['paid_main'], 1)
        self.assertEqual(ctx['total_paying'], 1)


class CrossSurfaceAgreementTest(OverrideFixtureMixin, TestCase):
    """Users list, detail, CRM, global search and the event roster agree."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Agreement Event', slug='agreement-event',
            start_datetime=timezone.now() + datetime.timedelta(days=5),
        )
        for user in (cls.stacked, cls.leftover):
            EventRegistration.objects.create(event=cls.event, user=user)
            CRMRecord.objects.create(user=user, created_by=cls.staff)

    def _surfaces(self, user):
        row, _ = self._row(user.email)
        detail = self._detail(user).context
        crm = self.client.get(
            reverse('studio_crm_detail', kwargs={'crm_id': user.crm_record.pk}),
        ).context
        search = self.client.get(
            reverse('studio_global_search'), {'q': user.email},
        ).json()['results']['users'][0]['metadata']
        roster = {
            reg.user_id: reg
            for reg in self.client.get(
                f'/studio/events/{self.event.pk}/edit',
            ).context['registrations']
        }[user.pk]
        return row, detail, crm, search, roster

    def test_stacked_user_is_premium_override_everywhere(self):
        row, detail, crm, search, roster = self._surfaces(self.stacked)

        self.assertEqual(
            [row['tier_name'], detail['tier_name'], crm['tier_name'], roster.effective_tier.name],
            ['Premium'] * 4,
        )
        self.assertEqual(
            [row['tier_source'], detail['tier_source'], crm['tier_source']],
            ['override'] * 3,
        )
        self.assertTrue(search.startswith('Premium (override until '))
        self.assertTrue(roster.tier_override_note)

    def test_leftover_user_is_plain_main_everywhere(self):
        row, detail, crm, search, roster = self._surfaces(self.leftover)

        self.assertEqual(
            [row['tier_name'], detail['tier_name'], crm['tier_name'], roster.effective_tier.name],
            ['Main'] * 4,
        )
        self.assertNotIn('override', [row['tier_source'], detail['tier_source'], crm['tier_source']])
        self.assertTrue(search.startswith('Main · '))
        self.assertFalse(roster.tier_override_note)


def _cell_texts(html, testid):
    pattern = re.compile(
        rf'<(td|th)\b[^>]*data-testid="{testid}"[^>]*>(.*?)</\1>', re.DOTALL,
    )
    return [
        ' '.join(re.sub(r'<[^>]+>', ' ', match.group(2)).split())
        for match in pattern.finditer(html)
    ]


class RosterTimestampMarkupTest(TierSetupMixin, StaffUserMixin, TestCase):
    """Roster timestamps split into date and time parts that can wrap.

    The no-overlap layout itself is asserted in Playwright
    (``playwright_tests/test_tier_override_consistency_1933.py``); this pins the
    server-rendered text so a Free row reads ``2026-10-09 13:09`` and
    ``Free`` in separate cells.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Roster Event', slug='roster-event-1933',
            start_datetime=timezone.now() + datetime.timedelta(days=5),
        )
        member = _make_user('free-member@test.com', cls.free_tier)
        registration = EventRegistration.objects.create(event=cls.event, user=member)
        moment = datetime.datetime(2026, 10, 9, 13, 9, tzinfo=datetime.timezone.utc)
        EventRegistration.objects.filter(pk=registration.pk).update(
            registered_at=moment, joined_at=moment,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_registered_and_joined_render_date_and_time_parts(self):
        html = self.client.get(f'/studio/events/{self.event.pk}/edit').content.decode()

        self.assertEqual(_cell_texts(html, 'registration-registered'), ['2026-10-09 13:09'])
        self.assertEqual(_cell_texts(html, 'registration-tier'), ['Free'])
        self.assertEqual(_cell_texts(html, 'registration-joined'), ['Joined 2026-10-09 13:09'])
