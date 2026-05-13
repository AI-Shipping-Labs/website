"""Studio tier/status pill cleanup for issue #589."""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from crm.models import CRMRecord
from payments.models import Tier

User = get_user_model()


def _row_html(html, testid):
    pattern = r'<tr[^>]*data-testid="' + re.escape(testid) + r'"[^>]*>.*?</tr>'
    match = re.search(pattern, html, re.DOTALL)
    if match is None:
        raise AssertionError(f'Could not locate row {testid}.')
    return match.group(0)


class TierLabelCleanupTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')
        cls.staff = User.objects.create_user(
            email='staff-589@test.com',
            password='pw',
            is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff-589@test.com', password='pw')

    def _user(self, email, *, tier=None, **kwargs):
        user = User.objects.create_user(email=email, password='pw', **kwargs)
        if tier is not None:
            user.tier = tier
            user.save(update_fields=['tier'])
        return user

    def _override(self, user, tier=None):
        return TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=tier or self.premium,
            expires_at=timezone.now() + timedelta(days=30),
            granted_by=self.staff,
            is_active=True,
        )


class UserTierAndStatusPillTest(TierLabelCleanupTestBase):
    def test_user_detail_uses_tier_and_status_pills_without_suffix(self):
        member = self._user('detail-override@test.com', tier=self.free)
        self._override(member, self.premium)

        response = self.client.get(reverse('studio_user_detail', args=[member.pk]))

        self.assertEqual(response.context['tier_name'], 'Premium')
        self.assertEqual(response.context['tier_slug'], 'premium')
        self.assertEqual(response.context['tier_source'], 'override')
        self.assertContains(response, 'data-testid="user-detail-tier-pill"')
        self.assertContains(response, 'data-tier="premium"')
        self.assertContains(response, 'data-testid="user-detail-tier-badge"')
        self.assertContains(response, 'data-tier-source="override"')
        self.assertContains(response, 'data-testid="user-detail-status-pill"')
        self.assertContains(response, 'data-status="active"')
        self.assertNotContains(response, '(override)')

    def test_user_list_exposes_tier_source_and_renders_override_pill_only_for_override(self):
        boosted = self._user('boosted-589@test.com', tier=self.free)
        plain = self._user('plain-589@test.com', tier=self.main)
        self._override(boosted, self.premium)

        response = self.client.get('/studio/users/?filter=all')
        rows = {row['email']: row for row in response.context['user_rows']}
        self.assertEqual(rows[boosted.email]['tier_name'], 'Premium')
        self.assertEqual(rows[boosted.email]['tier_slug'], 'premium')
        self.assertEqual(rows[boosted.email]['tier_source'], 'override')

        body = response.content.decode()
        boosted_html = _row_html(body, f'user-row-{boosted.pk}')
        plain_html = _row_html(body, f'user-row-{plain.pk}')
        self.assertIn('data-testid="user-list-tier-pill"', boosted_html)
        self.assertIn('data-tier="premium"', boosted_html)
        self.assertIn('data-testid="user-list-tier-override-pill"', boosted_html)
        self.assertNotIn('(override)', boosted_html)
        self.assertIn('data-testid="user-list-tier-pill"', plain_html)
        self.assertNotIn('data-testid="user-list-tier-override-pill"', plain_html)

    def test_user_detail_status_pill_uses_canonical_status_colours(self):
        active = self._user('active-589@test.com')
        staff = self._user('staff-member-589@test.com', is_staff=True)
        inactive = self._user('inactive-589@test.com', is_active=False)

        cases = [
            (active, 'active', 'bg-green-500/15 text-green-300'),
            (staff, 'staff', 'bg-blue-500/15 text-blue-300'),
            (inactive, 'inactive', 'bg-red-500/15 text-red-300'),
        ]
        for user, status, classes in cases:
            response = self.client.get(reverse('studio_user_detail', args=[user.pk]))
            self.assertContains(response, 'data-testid="user-detail-status-pill"')
            self.assertContains(response, f'data-status="{status}"')
            self.assertContains(response, classes)


class CrmTierPillTest(TierLabelCleanupTestBase):
    def test_crm_list_and_detail_use_tier_pills_and_override_source(self):
        member = self._user('crm-override-589@test.com', tier=self.main)
        self._override(member, self.premium)
        record = CRMRecord.objects.create(
            user=member,
            created_by=self.staff,
            status='active',
        )

        list_response = self.client.get('/studio/crm/?filter=all')
        row = list_response.context['rows'][0]
        self.assertEqual(row['tier_name'], 'Premium')
        self.assertEqual(row['tier_slug'], 'premium')
        self.assertEqual(row['tier_source'], 'override')
        list_html = _row_html(list_response.content.decode(), f'crm-row-{record.pk}')
        self.assertIn('data-testid="crm-list-tier-pill"', list_html)
        self.assertIn('data-testid="crm-list-tier-override-pill"', list_html)
        self.assertNotIn('(override)', list_html)

        detail_response = self.client.get(
            reverse('studio_crm_detail', args=[record.pk])
        )
        self.assertEqual(detail_response.context['tier_name'], 'Premium')
        self.assertEqual(detail_response.context['tier_slug'], 'premium')
        self.assertEqual(detail_response.context['tier_source'], 'override')
        self.assertContains(detail_response, 'data-testid="crm-detail-tier-pill"')
        self.assertContains(
            detail_response, 'data-testid="crm-detail-tier-override-pill"',
        )
        self.assertNotContains(detail_response, 'Tier: Premium (override)')


class TierOverrideSurfacePillTest(TierLabelCleanupTestBase):
    def test_global_active_overrides_table_uses_tier_pills_without_override_pills(self):
        member = self._user('global-override-589@test.com', tier=self.basic)
        self._override(member, self.premium)

        response = self.client.get(reverse('studio_tier_overrides_list'))

        self.assertContains(
            response, 'data-testid="active-override-effective-tier-pill"',
        )
        self.assertContains(
            response, 'data-testid="active-override-base-tier-pill"',
        )
        self.assertNotContains(
            response, 'data-testid="tier-override-active-override-pill"',
        )
        self.assertNotContains(response, '(override)')

    def test_per_user_tier_override_page_uses_base_active_and_history_pills(self):
        member = self._user('per-user-override-589@test.com', tier=self.basic)
        inactive = TierOverride.objects.create(
            user=member,
            original_tier=member.tier,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
            granted_by=self.staff,
            is_active=False,
        )
        self._override(member, self.premium)

        response = self.client.get(
            reverse('studio_user_tier_override_page', args=[member.pk])
        )

        self.assertContains(
            response, 'data-testid="tier-override-user-base-tier-pill"',
        )
        self.assertContains(response, 'data-testid="tier-override-active-tier-pill"')
        self.assertContains(
            response, 'data-testid="tier-override-active-override-pill"',
        )
        self.assertContains(
            response, 'data-testid="tier-override-history-override-tier-pill"',
        )
        self.assertContains(
            response, 'data-testid="tier-override-history-original-tier-pill"',
        )
        self.assertContains(response, inactive.override_tier.name)
        self.assertNotContains(response, '(override)')
