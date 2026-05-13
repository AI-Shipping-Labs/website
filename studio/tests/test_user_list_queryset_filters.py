"""QuerySet-backed Studio user list filters (issue #606)."""

import csv
import io
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier
from studio.views import users as users_view

User = get_user_model()
FAST_PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListQuerySetFiltersTest(TestCase):
    """List/export behavior stays stable while filters move into QuerySets."""

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
            tier=cls.free,
        )
        cls.basic_user = User.objects.create_user(
            email='basic@test.com',
            password='pw',
            tier=cls.basic,
            tags=['early-adopter-2026'],
        )
        cls.main_user = User.objects.create_user(
            email='main@test.com',
            password='pw',
            tier=cls.main,
            first_name='Ada',
            stripe_customer_id='cus_MAIN123',
            slack_user_id='U01MAIN123',
        )
        cls.premium_user = User.objects.create_user(
            email='premium@test.com',
            password='pw',
            tier=cls.premium,
        )
        cls.override_user = User.objects.create_user(
            email='trial@test.com',
            password='pw',
            tier=cls.free,
            tags=['cohort-a', 'early-adopter'],
            slack_member=True,
            slack_checked_at=timezone.now(),
        )
        cls.expired_override_user = User.objects.create_user(
            email='expired@test.com',
            password='pw',
            tier=cls.free,
        )

        TierOverride.objects.create(
            user=cls.override_user,
            original_tier=cls.free,
            override_tier=cls.main,
            expires_at=timezone.now() + timedelta(days=7),
            granted_by=cls.staff,
        )
        TierOverride.objects.create(
            user=cls.expired_override_user,
            original_tier=cls.free,
            override_tier=cls.premium,
            expires_at=timezone.now() - timedelta(days=1),
            granted_by=cls.staff,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _emails(self, response):
        return {row['email'] for row in response.context['page'].object_list}

    def test_tier_filter_and_counts_use_active_overrides(self):
        response = self.client.get('/studio/users/', {'filter': 'main_plus'})

        emails = self._emails(response)
        self.assertIn('trial@test.com', emails)
        self.assertIn('main@test.com', emails)
        self.assertIn('premium@test.com', emails)
        self.assertNotIn('basic@test.com', emails)
        self.assertNotIn('expired@test.com', emails)

        trial_row = next(
            row for row in response.context['page'].object_list
            if row['email'] == 'trial@test.com'
        )
        self.assertEqual(trial_row['tier_name'], 'Main')
        self.assertEqual(trial_row['tier_source'], 'override')

        self.assertEqual(response.context['paid_count'], 4)
        self.assertEqual(response.context['main_plus_count'], 3)
        self.assertEqual(response.context['premium_count'], 1)

    def test_slack_filter_combines_with_tier_queryset_filter(self):
        response = self.client.get(
            '/studio/users/',
            {'filter': 'main_plus', 'slack': 'yes'},
        )

        self.assertEqual(self._emails(response), {'trial@test.com'})
        row = response.context['page'].object_list[0]
        self.assertEqual(row['slack_status'], 'Member')

    def test_search_matches_scalar_fields_and_tag_substrings(self):
        scalar_response = self.client.get('/studio/users/', {'q': 'cus_MAIN'})
        self.assertEqual(self._emails(scalar_response), {'main@test.com'})

        tag_response = self.client.get('/studio/users/', {'q': 'cohort'})
        self.assertEqual(self._emails(tag_response), {'trial@test.com'})

    def test_tag_filter_stays_exact_and_combines_with_search(self):
        response = self.client.get(
            '/studio/users/',
            {'tag': 'Early Adopter', 'q': 'trial'},
        )

        self.assertEqual(self._emails(response), {'trial@test.com'})
        self.assertEqual(response.context['active_tag'], 'early-adopter')

        exact_response = self.client.get(
            '/studio/users/',
            {'tag': 'early-adopter'},
        )
        self.assertEqual(self._emails(exact_response), {'trial@test.com'})

    def test_list_builds_rows_only_for_the_current_page(self):
        for index in range(6):
            User.objects.create_user(
                email=f'bulk-{index}@test.com',
                password='pw',
                tier=self.free,
            )

        with (
            mock.patch('studio.views.users.USER_LIST_PAGE_SIZE', 3),
            mock.patch(
                'studio.views.users._active_override_map',
                wraps=users_view._active_override_map,
            ) as override_map_spy,
        ):
            response = self.client.get('/studio/users/', {'page': 2})

        self.assertEqual(len(response.context['page'].object_list), 3)
        listed_users = override_map_spy.call_args.args[0]
        self.assertEqual(len(listed_users), 3)

    def test_csv_export_preserves_filtered_override_and_slack_behavior(self):
        response = self.client.get(
            '/studio/users/export',
            {
                'filter': 'main_plus',
                'slack': 'yes',
                'tag': 'cohort-a',
                'page': '99',
            },
        )

        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['email'], 'trial@test.com')
        self.assertEqual(rows[0]['tier'], 'Main (override)')
        self.assertEqual(rows[0]['tags'], 'cohort-a,early-adopter')
        self.assertEqual(rows[0]['slack'], 'Member')
