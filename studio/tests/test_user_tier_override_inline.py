"""Inline tier-override controls on the Studio user detail page (issue #562).

The standalone /studio/users/tier-override/ page is not changed; these
tests cover the new inline block on /studio/users/<id>/ plus the new
``POST /studio/users/<id>/tier-override/{create,revoke}`` endpoints.

The business rules (upward-only override, deactivate previous active
row, five duration choices) are reused from ``studio.views.tier_overrides``
so we only test the parts that are new here:

- view context: ``available_override_tiers``, ``is_highest_tier``,
  ``override_duration_labels``, ``tier_source``, ``active_override``.
- template: badge in the Tier row, inline form, revoke button.
- new endpoints: success paths, validation, access control, redirect back
  to the detail page (never the standalone page).
- standalone page still redirects to itself (regression lock).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier

User = get_user_model()


class _InlineOverrideTestBase(TestCase):
    """Shared fixtures: tiers, staff, members."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def _make_member(self, email, tier=None, stripe_customer_id=''):
        user = User.objects.create_user(email=email, password='pw')
        if tier is not None:
            user.tier = tier
        user.stripe_customer_id = stripe_customer_id
        user.save()
        return user

    def _make_override(
        self, user, override_tier=None, expires_at=None, is_active=True,
        granted_by=None,
    ):
        return TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=override_tier or self.main,
            expires_at=expires_at or (timezone.now() + timedelta(days=14)),
            granted_by=granted_by or self.staff,
            is_active=is_active,
        )


class UserDetailTierOverrideContextTest(_InlineOverrideTestBase):
    """View context fields added in issue #562."""

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_free_user_has_three_available_override_tiers(self):
        member = self._make_member('free@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.status_code, 200)
        slugs = [
            t.slug for t in response.context['available_override_tiers']
        ]
        self.assertEqual(slugs, ['basic', 'main', 'premium'])
        self.assertFalse(response.context['is_highest_tier'])

    def test_premium_user_has_no_available_override_tiers(self):
        member = self._make_member('premium@test.com', tier=self.premium)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['available_override_tiers']), [],
        )
        self.assertTrue(response.context['is_highest_tier'])

    def test_duration_labels_match_studio_choices(self):
        member = self._make_member('dur@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(
            response.context['override_duration_labels'],
            ['14 days', '1 month', '3 months', '6 months', '12 months'],
        )

    def test_tier_source_is_default_for_free_user_without_stripe(self):
        member = self._make_member('default@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.context['tier_source'], 'default')

    def test_tier_source_is_stripe_when_customer_id_is_set(self):
        member = self._make_member(
            'paid@test.com', tier=self.basic, stripe_customer_id='cus_X',
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.context['tier_source'], 'stripe')

    def test_tier_source_is_override_when_active_override_exists(self):
        member = self._make_member(
            'override@test.com', tier=self.free, stripe_customer_id='cus_Y',
        )
        self._make_override(member, override_tier=self.main)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.context['tier_source'], 'override')
        self.assertTrue(response.context['has_override'])
        self.assertIsNotNone(response.context['active_override'])


class UserDetailTierOverrideTemplateTest(_InlineOverrideTestBase):
    """Template renders the inline block, badge, and history link."""

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_override_section_sits_between_membership_and_tags(self):
        # Issue #586 promoted the override block out of the Membership
        # card into its own top-level section. The override section must
        # appear AFTER the membership section opens and BEFORE the tags
        # section opens. The inner ``-block`` testid is preserved so
        # other tests / selectors keep working.
        member = self._make_member('inline@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        membership_open = body.index(
            'data-testid="user-detail-membership-section"',
        )
        override_section = body.index(
            'data-testid="user-detail-tier-override-section"',
        )
        override_block = body.index(
            'data-testid="user-detail-tier-override-block"',
        )
        tags_section = body.index('data-testid="user-tags-section"')
        self.assertLess(membership_open, override_section)
        self.assertLess(override_section, override_block)
        self.assertLess(override_block, tags_section)

    def test_default_badge_for_free_user_without_stripe(self):
        member = self._make_member('default-badge@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-tier-source="default"')
        self.assertContains(response, 'Default')
        self.assertNotContains(response, 'data-tier-source="override"')
        self.assertNotContains(response, 'data-tier-source="stripe"')

    def test_from_stripe_badge_for_paid_user_without_override(self):
        member = self._make_member(
            'stripe-badge@test.com',
            tier=self.basic,
            stripe_customer_id='cus_ABC',
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-tier-source="stripe"')
        self.assertContains(response, 'From Stripe')
        self.assertNotContains(response, 'data-tier-source="override"')

    def test_override_badge_for_user_with_active_override(self):
        member = self._make_member('ov-badge@test.com', tier=self.free)
        self._make_override(member, override_tier=self.main)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-tier-source="override"')
        self.assertContains(response, 'Override')
        # Base line shows the stored subscription tier underneath.
        self.assertContains(response, 'data-testid="user-detail-tier-base"')
        self.assertContains(response, 'Base: Free')

    def test_form_lists_available_tiers_and_durations(self):
        member = self._make_member('form@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        # Three tier radios (basic, main, premium) with stable testids.
        self.assertEqual(
            body.count('data-testid="user-detail-tier-override-tier-option"'),
            3,
        )
        self.assertIn('data-tier-slug="basic"', body)
        self.assertIn('data-tier-slug="main"', body)
        self.assertIn('data-tier-slug="premium"', body)
        # Five duration submit buttons with stable testids.
        self.assertEqual(
            body.count('data-testid="user-detail-tier-override-duration"'),
            5,
        )
        for label in ['14 days', '1 month', '3 months', '6 months', '12 months']:
            self.assertIn(f'data-duration="{label}"', body)

    def test_highest_tier_user_sees_no_form(self):
        member = self._make_member('peak@test.com', tier=self.premium)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(
            response, 'data-testid="user-detail-tier-override-highest"',
        )
        self.assertContains(
            response,
            'User is already at the highest tier',
        )
        self.assertNotContains(
            response, 'data-testid="user-detail-tier-override-form"',
        )
        self.assertNotContains(
            response, 'data-testid="user-detail-tier-override-duration"',
        )

    def test_active_override_replaces_form_with_revoke(self):
        member = self._make_member('revokable@test.com', tier=self.free)
        self._make_override(member, override_tier=self.main)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(
            response,
            'data-testid="user-detail-tier-override-revoke-form"',
        )
        self.assertContains(
            response, 'data-testid="user-detail-tier-override-revoke"',
        )
        # Create form must not be present at the same time.
        self.assertNotContains(
            response, 'data-testid="user-detail-tier-override-form"',
        )

    def test_history_link_points_to_standalone_page_with_email(self):
        member = self._make_member('history@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(
            response,
            f'href="/studio/users/{member.pk}/tier_override/"',
        )

    def test_first_tier_option_is_preselected(self):
        # The lowest available tier (Main, level 20) should be checked
        # for a Basic-tier user. We slice the radio input markup and
        # confirm `checked` lands on the Main radio specifically (rather
        # than relying on the test client to leak loop state).
        import re
        member = self._make_member('first@test.com', tier=self.basic)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        # Pull every <input type="radio" name="tier_id" ...> tag and
        # assert exactly one has `checked` AND that one is the Main pk.
        radios = re.findall(
            r'<input type="radio" name="tier_id"[^>]*>', body,
        )
        checked = [r for r in radios if ' checked' in r or 'checked>' in r]
        self.assertEqual(
            len(checked), 1,
            f'Expected exactly one checked tier radio, got {len(checked)}: {checked}',
        )
        self.assertIn(f'value="{self.main.pk}"', checked[0])


class UserTierOverrideCreateEndpointTest(_InlineOverrideTestBase):
    """POST /studio/users/<id>/tier-override/create."""

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_create_makes_override_and_redirects_to_detail(self):
        member = self._make_member('create@test.com', tier=self.free)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.main.pk, 'duration': '1 month'},
        )
        self.assertRedirects(response, f'/studio/users/{member.pk}/')
        override = TierOverride.objects.get(user=member, is_active=True)
        self.assertEqual(override.override_tier_id, self.main.pk)
        self.assertEqual(override.original_tier_id, self.free.pk)
        self.assertEqual(override.granted_by_id, self.staff.pk)

    def test_create_deactivates_existing_active_override(self):
        member = self._make_member('replace@test.com', tier=self.free)
        old = self._make_override(member, override_tier=self.basic)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.premium.pk, 'duration': '3 months'},
        )
        self.assertEqual(response.status_code, 302)
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        # New row is active; old row is inactive — exactly one active.
        self.assertEqual(
            TierOverride.objects.filter(user=member, is_active=True).count(),
            1,
        )

    def test_downgrade_attempt_is_rejected(self):
        member = self._make_member('down@test.com', tier=self.main)
        before = TierOverride.objects.count()
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.basic.pk, 'duration': '1 month'},
        )
        self.assertRedirects(response, f'/studio/users/{member.pk}/')
        self.assertEqual(TierOverride.objects.count(), before)

    def test_same_tier_attempt_is_rejected(self):
        member = self._make_member('same@test.com', tier=self.main)
        before = TierOverride.objects.count()
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.main.pk, 'duration': '1 month'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TierOverride.objects.count(), before)

    def test_invalid_duration_is_rejected(self):
        member = self._make_member('bad-dur@test.com', tier=self.free)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.main.pk, 'duration': '99 years'},
        )
        self.assertRedirects(response, f'/studio/users/{member.pk}/')
        self.assertFalse(
            TierOverride.objects.filter(user=member).exists(),
        )

    def test_missing_tier_id_is_rejected(self):
        member = self._make_member('no-tier@test.com', tier=self.free)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'duration': '1 month'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            TierOverride.objects.filter(user=member).exists(),
        )

    def test_invalid_tier_id_is_rejected(self):
        member = self._make_member('bad-tier@test.com', tier=self.free)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': '999999', 'duration': '1 month'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            TierOverride.objects.filter(user=member).exists(),
        )

    def test_get_returns_405(self):
        member = self._make_member('get@test.com', tier=self.free)
        response = self.client.get(
            f'/studio/users/{member.pk}/tier_override/create',
        )
        self.assertEqual(response.status_code, 405)

    def test_404_for_unknown_user(self):
        response = self.client.post(
            '/studio/users/9999999/tier_override/create',
            {'tier_id': self.main.pk, 'duration': '1 month'},
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        member = self._make_member('anon@test.com', tier=self.free)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.main.pk, 'duration': '1 month'},
        )
        # @staff_required redirects unauthenticated users to login.
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertFalse(
            TierOverride.objects.filter(user=member).exists(),
        )

    def test_non_staff_user_gets_403(self):
        self.client.logout()
        member = self._make_member('victim@test.com', tier=self.free)
        regular = User.objects.create_user(
            email='regular@test.com', password='pw',
        )
        regular.tier = self.free
        regular.save()
        self.client.login(email='regular@test.com', password='pw')
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.main.pk, 'duration': '1 month'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            TierOverride.objects.filter(user=member).exists(),
        )


class UserTierOverrideRevokeEndpointTest(_InlineOverrideTestBase):
    """POST /studio/users/<id>/tier-override/revoke."""

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_revoke_deactivates_override_and_redirects_to_detail(self):
        member = self._make_member('rev@test.com', tier=self.free)
        override = self._make_override(member, override_tier=self.main)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/revoke',
            {'override_id': override.pk},
        )
        self.assertRedirects(response, f'/studio/users/{member.pk}/')
        override.refresh_from_db()
        self.assertFalse(override.is_active)

    def test_revoke_does_not_touch_other_users_override(self):
        # Defensive: an attacker who knows another user's override id
        # cannot revoke it via /studio/users/<my-id>/tier-override/revoke.
        owner = self._make_member('owner@test.com', tier=self.free)
        bystander = self._make_member('bystander@test.com', tier=self.free)
        override = self._make_override(owner, override_tier=self.main)
        response = self.client.post(
            f'/studio/users/{bystander.pk}/tier_override/revoke',
            {'override_id': override.pk},
        )
        # Redirects to the bystander's detail page with an error flash;
        # the owner's override remains active.
        self.assertEqual(response.status_code, 302)
        override.refresh_from_db()
        self.assertTrue(override.is_active)

    def test_revoke_with_missing_override_id_is_rejected(self):
        member = self._make_member('miss@test.com', tier=self.free)
        override = self._make_override(member, override_tier=self.main)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/revoke',
            {},
        )
        self.assertEqual(response.status_code, 302)
        override.refresh_from_db()
        self.assertTrue(override.is_active)

    def test_get_returns_405(self):
        member = self._make_member('rg@test.com', tier=self.free)
        response = self.client.get(
            f'/studio/users/{member.pk}/tier_override/revoke',
        )
        self.assertEqual(response.status_code, 405)

    def test_non_staff_user_gets_403(self):
        self.client.logout()
        member = self._make_member('rv-victim@test.com', tier=self.free)
        override = self._make_override(member, override_tier=self.main)
        User.objects.create_user(email='reg2@test.com', password='pw')
        self.client.login(email='reg2@test.com', password='pw')
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/revoke',
            {'override_id': override.pk},
        )
        self.assertEqual(response.status_code, 403)
        override.refresh_from_db()
        self.assertTrue(override.is_active)


class LegacyStandaloneTierOverrideRedirectTest(_InlineOverrideTestBase):
    """Old standalone POST URLs preserve methods while moving to user URLs."""

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_standalone_create_redirects_to_user_create_preserving_method(self):
        member = self._make_member('legacy@test.com', tier=self.free)
        response = self.client.post(
            '/studio/users/tier-override/create',
            {
                'email': 'legacy@test.com',
                'tier_id': self.basic.pk,
                'duration': '1 month',
            },
        )
        self.assertEqual(response.status_code, 308)
        self.assertEqual(
            response.url,
            f'/studio/users/{member.pk}/tier_override/create',
        )
        self.assertFalse(
            TierOverride.objects.filter(user=member, is_active=True).exists(),
        )

    def test_standalone_revoke_redirects_to_user_revoke_preserving_method(self):
        member = self._make_member('legrev@test.com', tier=self.free)
        override = self._make_override(member, override_tier=self.main)
        response = self.client.post(
            '/studio/users/tier-override/revoke',
            {'override_id': override.pk, 'email': member.email},
        )
        self.assertEqual(response.status_code, 308)
        self.assertEqual(
            response.url,
            f'/studio/users/{member.pk}/tier_override/revoke',
        )
        override.refresh_from_db()
        self.assertTrue(override.is_active)
