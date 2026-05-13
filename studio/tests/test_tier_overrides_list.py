"""Cross-cutting "Active overrides" list on the standalone tier-override page.

Issue #567 extends the existing ``tier_override_page`` view so the page
always renders a list of every currently-active TierOverride at the top,
independent of whether the operator has searched for a user.

These tests cover the new behaviour only — the per-user search, creation
and revoke flows are already exercised by
``studio/tests/test_user_tier_override_inline.py`` and
``accounts/tests/test_tier_override.py``.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier

User = get_user_model()


class ActiveOverridesListTestBase(TestCase):
    """Shared fixtures: tiers + staff user + helpers."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.url = reverse('studio_tier_override')

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _make_user(self, email, tier=None):
        user = User.objects.create_user(email=email, password='pw')
        if tier is not None:
            user.tier = tier
            user.save(update_fields=['tier'])
        return user

    def _make_override(
        self, user, override_tier=None, *, expires_in_days=30,
        is_active=True, granted_by=None, original_tier_sentinel=False,
    ):
        original_tier = user.tier
        if original_tier_sentinel:
            original_tier = None
        return TierOverride.objects.create(
            user=user,
            original_tier=original_tier,
            override_tier=override_tier or self.main,
            expires_at=timezone.now() + timedelta(days=expires_in_days),
            granted_by=granted_by or self.staff,
            is_active=is_active,
        )


class ActiveOverridesContextTest(ActiveOverridesListTestBase):
    """View context exposes ``active_overrides`` regardless of search state."""

    def test_context_includes_active_overrides_without_search(self):
        user = self._make_user('a@test.com', tier=self.free)
        self._make_override(user, self.main, expires_in_days=10)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('active_overrides', response.context)
        rows = list(response.context['active_overrides'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, user.pk)

    def test_context_includes_active_overrides_with_search(self):
        a = self._make_user('a@test.com', tier=self.free)
        self._make_user('searched@test.com', tier=self.free)
        self._make_override(a, self.main, expires_in_days=10)
        response = self.client.get(self.url, {'email': 'searched@test.com'})
        self.assertEqual(response.status_code, 200)
        rows = list(response.context['active_overrides'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, a.pk)

    def test_active_overrides_ordered_soonest_first(self):
        u1 = self._make_user('soon@test.com', tier=self.free)
        u2 = self._make_user('later@test.com', tier=self.free)
        u3 = self._make_user('latest@test.com', tier=self.free)
        # Create in deliberately wrong order to prove the queryset sorts.
        self._make_override(u3, self.main, expires_in_days=90)
        self._make_override(u1, self.basic, expires_in_days=2)
        self._make_override(u2, self.main, expires_in_days=30)

        response = self.client.get(self.url)
        rows = list(response.context['active_overrides'])
        emails = [r.user.email for r in rows]
        self.assertEqual(
            emails, ['soon@test.com', 'later@test.com', 'latest@test.com'],
        )

    def test_excludes_revoked_and_expired_overrides(self):
        kept = self._make_user('kept@test.com', tier=self.free)
        revoked = self._make_user('revoked@test.com', tier=self.free)
        expired = self._make_user('expired@test.com', tier=self.free)

        self._make_override(kept, self.main, expires_in_days=10)
        self._make_override(
            revoked, self.main, expires_in_days=10, is_active=False,
        )
        # Manually expired: ``is_active=True`` but expires_at in the past.
        TierOverride.objects.create(
            user=expired,
            original_tier=expired.tier,
            override_tier=self.main,
            expires_at=timezone.now() - timedelta(minutes=5),
            granted_by=self.staff,
            is_active=True,
        )

        response = self.client.get(self.url)
        emails = [r.user.email for r in response.context['active_overrides']]
        self.assertEqual(emails, ['kept@test.com'])

    def test_empty_state_when_no_active_overrides(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['active_overrides']), [])


class ActiveOverridesRenderTest(ActiveOverridesListTestBase):
    """Template renders the list section, count, columns, and revoke control."""

    def test_section_visible_with_count_when_overrides_exist(self):
        u = self._make_user('one@test.com', tier=self.free)
        self._make_override(u, self.main, expires_in_days=10)
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="active-overrides-section"')
        self.assertContains(
            response, 'data-testid="active-overrides-count"',
        )
        self.assertContains(response, '1 active')
        self.assertContains(response, 'data-testid="active-overrides-table"')
        self.assertNotContains(
            response, 'data-testid="active-overrides-empty"',
        )

    def test_section_shows_empty_state_when_no_overrides(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="active-overrides-section"')
        self.assertContains(response, 'data-testid="active-overrides-empty"')
        self.assertContains(
            response,
            'No active overrides right now. Use the search above to grant one.',
        )
        self.assertContains(response, 'No active overrides')
        # The empty section must not render the table or any rows.
        self.assertNotContains(
            response, 'data-testid="active-overrides-table"',
        )
        self.assertNotContains(
            response, 'data-testid="active-override-row"',
        )

    def test_row_renders_columns_and_user_detail_link(self):
        user = self._make_user('rowtest@test.com', tier=self.free)
        override = self._make_override(user, self.main, expires_in_days=5)

        response = self.client.get(self.url)

        # User column links to the studio user detail page.
        detail_url = reverse('studio_user_detail', args=[user.pk])
        self.assertContains(
            response,
            f'href="{detail_url}"',
        )
        # Effective and base tier names appear.
        self.assertContains(
            response, 'data-testid="active-override-effective-tier"',
        )
        self.assertContains(
            response, 'data-testid="active-override-base-tier"',
        )
        # Set-by column shows the staff email.
        self.assertContains(response, self.staff.email)
        # Confirm copy on the revoke button matches the existing prompt.
        self.assertContains(
            response,
            "Revoke this override? The user will immediately lose upgraded access.",
        )
        # Revoke form posts to the per-user endpoint with the override id.
        self.assertContains(
            response,
            f'value="{override.pk}"',
        )
        revoke_url = reverse('studio_user_tier_override_revoke', args=[user.pk])
        self.assertContains(response, f'action="{revoke_url}"')

    def test_legacy_row_without_original_tier_renders_dash(self):
        user = self._make_user('legacy@test.com', tier=self.free)
        self._make_override(
            user, self.main, expires_in_days=10,
            original_tier_sentinel=True,
        )
        response = self.client.get(self.url)
        # The base-tier column for this row is rendered as '-'.
        self.assertContains(
            response,
            (
                '<td class="px-6 py-4 text-sm text-muted-foreground"\n'
                '            data-testid="active-override-base-tier">-</td>'
            ),
            html=False,
        )

    def test_granted_by_dash_when_unset(self):
        user = self._make_user('orphan@test.com', tier=self.free)
        # Granted-by is explicitly None.
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=14),
            granted_by=None,
            is_active=True,
        )
        response = self.client.get(self.url)
        self.assertContains(
            response,
            (
                '<td class="px-6 py-4 text-sm text-muted-foreground"\n'
                '            data-testid="active-override-set-by">-</td>'
            ),
            html=False,
        )

    def test_search_form_still_renders_on_same_page(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="tier-override-user-search"')
        self.assertContains(response, 'data-search-url="/studio/api/users/search/"')


class ActiveOverridesRevokeIntegrationTest(ActiveOverridesListTestBase):
    """Revoking a row uses the existing endpoint and is absent on next render."""

    def test_revoke_removes_row_from_active_list(self):
        user = self._make_user('revokeme@test.com', tier=self.free)
        override = self._make_override(user, self.main, expires_in_days=14)

        # Sanity: the row is in the list.
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['active_overrides']), 1)

        revoke_url = reverse('studio_user_tier_override_revoke', args=[user.pk])
        post = self.client.post(
            revoke_url,
            data={'override_id': override.pk, 'next': 'tier_overrides_list'},
        )
        self.assertEqual(post.status_code, 302)
        self.assertEqual(post['Location'], '/studio/tier_overrides/')

        override.refresh_from_db()
        self.assertFalse(override.is_active)

        response = self.client.get(self.url)
        self.assertEqual(list(response.context['active_overrides']), [])

    def test_revoke_without_email_still_redirects_to_standalone_page(self):
        user = self._make_user('noemail@test.com', tier=self.free)
        override = self._make_override(user, self.main, expires_in_days=14)
        revoke_url = reverse('studio_user_tier_override_revoke', args=[user.pk])

        post = self.client.post(
            revoke_url,
            data={'override_id': override.pk, 'next': 'tier_overrides_list'},
        )
        self.assertEqual(post.status_code, 302)
        self.assertEqual(post['Location'], '/studio/tier_overrides/')
