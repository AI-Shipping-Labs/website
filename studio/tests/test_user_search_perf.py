"""Query-budget tests for Studio user autocomplete search."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from payments.models import Tier, TierOverride
from plans.models import Plan, Sprint, SprintEnrollment
from tests.fixtures import set_membership

User = get_user_model()


@tag('core')
class StudioUserSearchQueryBudgetTest(TestCase):
    """Verify studio_user_search query count stays bounded as candidate rows grow."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-perf@test.com',
            password='pw',
            is_staff=True,
        )
        cls.free = Tier.objects.get(slug='free')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')

        cls.candidates = []
        for i in range(10):
            user = User.objects.create_user(
                email=f'picker-candidate-{i:02d}@test.com',
                first_name='Candidate',
                last_name=f'User{i:02d}',
                password='pw',
            )
            set_membership(user, tier=cls.free)
            cls.candidates.append(user)

        TierOverride.objects.create(
            user=cls.candidates[0],
            original_tier=cls.free,
            override_tier=cls.main,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )
        TierOverride.objects.create(
            user=cls.candidates[1],
            original_tier=cls.free,
            override_tier=cls.premium,
            expires_at=timezone.now() + timedelta(days=14),
            is_active=True,
        )

        cls.sprint = Sprint.objects.create(
            name='Perf Sprint',
            slug='perf-sprint',
            start_date=timezone.now().date(),
        )
        SprintEnrollment.objects.create(
            sprint=cls.sprint,
            user=cls.candidates[0],
        )
        Plan.objects.create(
            sprint=cls.sprint,
            member=cls.candidates[0],
            goal='ship it',
        )

    def setUp(self):
        self.client.login(email='staff-perf@test.com', password='pw')
        self.client.get(reverse('studio_user_search'), {'q': 'warmup'})

    def test_search_batches_active_overrides_into_constant_queries(self):
        # 2 queries for auth session + 1 candidate search + 1 batched override query
        with self.assertNumQueries(4):
            response = self.client.get(
                reverse('studio_user_search'),
                {'q': 'picker-candidate'},
            )
        results = response.json()['results']
        self.assertEqual(len(results), 10)
        tier_levels = {r['email']: r['tier_level'] for r in results}
        self.assertEqual(
            tier_levels['picker-candidate-00@test.com'],
            self.main.level,
        )
        self.assertEqual(
            tier_levels['picker-candidate-01@test.com'],
            self.premium.level,
        )
        self.assertEqual(
            tier_levels['picker-candidate-02@test.com'],
            self.free.level,
        )

    def test_search_with_sprint_context_stays_flat_at_seven_queries(self):
        # 2 auth + 1 sprint + 1 search + 1 enrollment + 1 plan + 1 batched override
        with self.assertNumQueries(7):
            response = self.client.get(
                reverse('studio_user_search'),
                {'q': 'picker-candidate', 'sprint': 'perf-sprint'},
            )
        results = response.json()['results']
        self.assertEqual(len(results), 10)
        row_0 = next(
            r for r in results if r['email'] == 'picker-candidate-00@test.com'
        )
        self.assertTrue(row_0['in_sprint'])
        self.assertTrue(row_0['has_plan_in_sprint'])
        row_1 = next(
            r for r in results if r['email'] == 'picker-candidate-01@test.com'
        )
        self.assertFalse(row_1['in_sprint'])
        self.assertFalse(row_1['has_plan_in_sprint'])

    def test_query_count_does_not_grow_with_candidate_count(self):
        # Searching for 1 user takes the exact same number of queries as 10 users
        with self.assertNumQueries(4):
            resp_1 = self.client.get(
                reverse('studio_user_search'),
                {'q': 'picker-candidate-00'},
            )
        self.assertEqual(len(resp_1.json()['results']), 1)

        with self.assertNumQueries(4):
            resp_10 = self.client.get(
                reverse('studio_user_search'),
                {'q': 'picker-candidate'},
            )
        self.assertEqual(len(resp_10.json()['results']), 10)
