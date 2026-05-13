"""Tier override URL and autocomplete behaviour for issue #587."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier

User = get_user_model()


class TierOverrideIssue587Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
            first_name='Staff',
            last_name='Member',
        )
        cls.non_staff = User.objects.create_user(
            email='free@test.com',
            password='pw',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _user(self, email, tier=None, **kwargs):
        user = User.objects.create_user(email=email, password='pw', **kwargs)
        if tier is not None:
            user.tier = tier
            user.save(update_fields=['tier'])
        return user

    def _override(self, user, tier=None, *, active=True):
        return TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=tier or self.main,
            expires_at=timezone.now() + timedelta(days=30),
            granted_by=self.staff,
            is_active=active,
        )

    def test_user_search_returns_limited_identity_results(self):
        self._user('learner1@test.com', first_name='Learner', last_name='One')
        self._user('learner2@test.com')
        self._user('partner@example.com')

        response = self.client.get(reverse('studio_user_search'), {'q': 'learn'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                'results': [
                    {'id': User.objects.get(email='learner1@test.com').pk, 'email': 'learner1@test.com', 'name': 'Learner One'},
                    {'id': User.objects.get(email='learner2@test.com').pk, 'email': 'learner2@test.com', 'name': ''},
                ],
            },
        )
        self.assertEqual(
            set(response.json()['results'][0].keys()),
            {'id', 'email', 'name'},
        )

    def test_user_search_returns_empty_for_short_empty_and_no_match(self):
        self._user('alpha@test.com')
        for query in ['', 'a', 'missing']:
            response = self.client.get(reverse('studio_user_search'), {'q': query})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'results': []})

    def test_user_search_digit_query_matches_exact_pk_or_email_substring(self):
        user = self._user('numeric@test.com')
        while user.pk < 10:
            user = self._user(f'numeric-{user.pk}@test.com')
        other = self._user(f'user{user.pk}@test.com')

        response = self.client.get(reverse('studio_user_search'), {'q': str(user.pk)})

        emails = [row['email'] for row in response.json()['results']]
        self.assertIn(user.email, emails)
        self.assertIn(other.email, emails)

    def test_user_search_non_staff_does_not_return_json_user_list(self):
        self._user('secret@test.com')
        self.client.login(email='free@test.com', password='pw')

        response = self.client.get(reverse('studio_user_search'), {'q': 'secret'})

        self.assertEqual(response.status_code, 403)
        self.assertNotIn('secret@test.com', response.content.decode())

    def test_per_user_page_renders_details_create_active_and_history(self):
        user = self._user('target@test.com', tier=self.free)
        self._override(user, self.premium)
        inactive = self._override(user, self.basic, active=False)

        response = self.client.get(
            reverse('studio_user_tier_override_page', args=[user.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/users/tier_override.html')
        self.assertContains(response, 'target@test.com')
        self.assertContains(response, 'Active Override')
        self.assertContains(response, 'Create Override')
        self.assertContains(response, 'Override History')
        self.assertContains(response, 'Premium')
        self.assertContains(response, inactive.override_tier.name)
        self.assertContains(
            response,
            reverse('studio_user_tier_override_revoke', args=[user.pk]),
        )
        self.assertContains(
            response,
            reverse('studio_user_tier_override_create', args=[user.pk]),
        )

    def test_per_user_page_404s_for_missing_user(self):
        response = self.client.get('/studio/users/999999/tier_override/')
        self.assertEqual(response.status_code, 404)

    def test_global_page_is_search_shell_with_active_table(self):
        user = self._user('active@test.com', tier=self.free)
        self._override(user, self.main)

        response = self.client.get(reverse('studio_tier_overrides_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="tier-override-user-search"')
        self.assertContains(response, reverse('studio_user_search'))
        self.assertContains(response, 'data-testid="active-overrides-table"')
        self.assertContains(response, 'active@test.com')
        self.assertNotContains(response, 'name="email"')
        self.assertNotContains(response, 'User Email (exact match)')

    def test_legacy_global_urls_redirect_to_new_locations(self):
        user = self._user('bookmark@test.com')

        response = self.client.get('/studio/users/tier-override/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/studio/tier_overrides/')

        response = self.client.get(
            '/studio/users/tier-override/',
            {'email': user.email},
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'/studio/users/{user.pk}/tier_override/',
        )

        response = self.client.get(
            '/studio/users/tier-override/',
            {'email': 'ghost@nowhere.com'},
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/studio/tier_overrides/')

    def test_legacy_user_action_urls_redirect_preserving_post_methods(self):
        user = self._user('legacy@test.com')

        for action in ('create', 'revoke'):
            get_response = self.client.get(
                f'/studio/users/{user.pk}/tier-override/{action}'
            )
            self.assertEqual(get_response.status_code, 301)
            self.assertEqual(
                get_response['Location'],
                f'/studio/users/{user.pk}/tier_override/{action}',
            )

            post_response = self.client.post(
                f'/studio/users/{user.pk}/tier-override/{action}'
            )
            self.assertEqual(post_response.status_code, 308)
            self.assertEqual(
                post_response['Location'],
                f'/studio/users/{user.pk}/tier_override/{action}',
            )

    def test_user_detail_links_active_override_block_to_per_user_page(self):
        user = self._user('existing@test.com', tier=self.free)
        self._override(user, self.premium)

        response = self.client.get(reverse('studio_user_detail', args=[user.pk]))

        url = reverse('studio_user_tier_override_page', args=[user.pk])
        self.assertContains(response, f'href="{url}"')
        self.assertContains(
            response,
            'data-testid="user-detail-tier-override-page-link"',
        )
        self.assertContains(
            response,
            'data-testid="user-detail-tier-override-history-link"',
        )

    def test_sidebar_points_to_new_global_url(self):
        response = self.client.get(reverse('studio_dashboard'))

        self.assertContains(response, 'href="/studio/tier_overrides/"')
        self.assertNotContains(response, 'href="/studio/users/tier-override/"')
