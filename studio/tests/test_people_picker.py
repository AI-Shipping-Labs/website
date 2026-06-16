"""Rich Studio people picker (issue #720).

Covers the extended ``studio_user_search`` JSON endpoint (name + email
matching, relevance ordering, sprint-context badges, sprint slug 404) and
the new shared template include ``studio/includes/_people_picker.html``
that the tier-overrides page (and future surfaces like #718) consumes.
"""

import datetime
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


@tag('core')
class StudioUserSearchNameAndEmailTest(TestCase):
    """Endpoint matches across first_name, last_name, and email."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _user(self, email, **kwargs):
        return User.objects.create_user(email=email, password='pw', **kwargs)

    def test_matches_first_name(self):
        self._user('a@test.com', first_name='Alice', last_name='Brown')
        self._user('b@test.com', first_name='Bob', last_name='Smith')

        response = self.client.get(reverse('studio_user_search'), {'q': 'alic'})

        emails = [r['email'] for r in response.json()['results']]
        self.assertEqual(emails, ['a@test.com'])

    def test_matches_last_name(self):
        self._user('a@test.com', first_name='Carol', last_name='Davis')
        self._user('b@test.com', first_name='Bob', last_name='Smith')

        response = self.client.get(reverse('studio_user_search'), {'q': 'davis'})

        emails = [r['email'] for r in response.json()['results']]
        self.assertEqual(emails, ['a@test.com'])

    def test_matches_email_when_name_does_not(self):
        self._user('partner@example.com', first_name='Zed', last_name='Zelda')

        response = self.client.get(reverse('studio_user_search'), {'q': 'partner'})

        emails = [r['email'] for r in response.json()['results']]
        self.assertEqual(emails, ['partner@example.com'])

    def test_match_is_case_insensitive(self):
        self._user('a@test.com', first_name='Alice', last_name='Brown')

        response = self.client.get(reverse('studio_user_search'), {'q': 'ALICE'})

        emails = [r['email'] for r in response.json()['results']]
        self.assertEqual(emails, ['a@test.com'])

    def test_short_query_returns_empty_results(self):
        self._user('alex@test.com', first_name='Alex', last_name='Grigorev')

        response = self.client.get(reverse('studio_user_search'), {'q': 'a'})

        self.assertEqual(response.json(), {'results': []})

    def test_results_include_identity_and_tier_fields(self):
        free = Tier.objects.get(slug='free')
        main = Tier.objects.get(slug='main')
        free_user = self._user(
            'free@test.com', first_name='Free', last_name='User',
        )
        free_user.tier = free
        free_user.save(update_fields=['tier'])
        main_user = self._user(
            'main@test.com', first_name='Main', last_name='Person',
        )
        main_user.tier = main
        main_user.save(update_fields=['tier'])

        response = self.client.get(reverse('studio_user_search'), {'q': 'test'})

        results = {r['email']: r for r in response.json()['results']}
        free_row = results['free@test.com']
        self.assertEqual(free_row['first_name'], 'Free')
        self.assertEqual(free_row['last_name'], 'User')
        self.assertEqual(free_row['display_name'], 'Free User')
        self.assertEqual(free_row['tier_level'], free.level)
        self.assertFalse(free_row['has_community_access'])
        main_row = results['main@test.com']
        self.assertEqual(main_row['tier_level'], main.level)
        self.assertTrue(main_row['has_community_access'])

    def test_tier_fields_use_effective_active_override_level(self):
        free = Tier.objects.get(slug='free')
        main = Tier.objects.get(slug='main')
        premium = Tier.objects.get(slug='premium')
        active_main = self._user(
            'active-main-override@test.com',
            first_name='Active',
            last_name='Override',
        )
        active_main.tier = free
        active_main.save(update_fields=['tier'])
        TierOverride.objects.create(
            user=active_main,
            original_tier=free,
            override_tier=main,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )
        active_premium = self._user(
            'active-premium-override@test.com',
            first_name='Premium',
            last_name='Override',
        )
        active_premium.tier = free
        active_premium.save(update_fields=['tier'])
        TierOverride.objects.create(
            user=active_premium,
            original_tier=free,
            override_tier=premium,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )

        response = self.client.get(
            reverse('studio_user_search'), {'q': 'override@test.com'},
        )

        results = {r['email']: r for r in response.json()['results']}
        self.assertEqual(results['active-main-override@test.com']['tier_level'], 20)
        self.assertTrue(
            results['active-main-override@test.com']['has_community_access'],
        )
        self.assertEqual(
            results['active-premium-override@test.com']['tier_level'], 30,
        )
        self.assertTrue(
            results['active-premium-override@test.com']['has_community_access'],
        )

    def test_tier_fields_ignore_missing_lapsed_and_basic_overrides(self):
        free = Tier.objects.get(slug='free')
        basic = Tier.objects.get(slug='basic')
        main = Tier.objects.get(slug='main')
        no_override = self._user('no-override@test.com')
        no_override.tier = free
        no_override.save(update_fields=['tier'])
        expired = self._user('expired-override@test.com')
        expired.tier = free
        expired.save(update_fields=['tier'])
        TierOverride.objects.create(
            user=expired,
            original_tier=free,
            override_tier=main,
            expires_at=timezone.now() - timedelta(days=1),
            is_active=True,
        )
        inactive = self._user('inactive-override@test.com')
        inactive.tier = free
        inactive.save(update_fields=['tier'])
        TierOverride.objects.create(
            user=inactive,
            original_tier=free,
            override_tier=main,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=False,
        )
        basic_only = self._user('basic-only-override@test.com')
        basic_only.tier = free
        basic_only.save(update_fields=['tier'])
        TierOverride.objects.create(
            user=basic_only,
            original_tier=free,
            override_tier=basic,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )

        response = self.client.get(reverse('studio_user_search'), {'q': 'override'})

        results = {r['email']: r for r in response.json()['results']}
        for email in (
            'no-override@test.com',
            'expired-override@test.com',
            'inactive-override@test.com',
        ):
            self.assertEqual(results[email]['tier_level'], 0)
            self.assertFalse(results[email]['has_community_access'])
        self.assertEqual(results['basic-only-override@test.com']['tier_level'], 10)
        self.assertFalse(
            results['basic-only-override@test.com']['has_community_access'],
        )

    def test_display_name_falls_back_to_email_local_part_when_no_name(self):
        self._user('noname@test.com')

        response = self.client.get(reverse('studio_user_search'), {'q': 'noname'})

        row = response.json()['results'][0]
        self.assertEqual(row['email'], 'noname@test.com')
        self.assertEqual(row['display_name'], 'noname')
        self.assertEqual(row['first_name'], '')
        self.assertEqual(row['last_name'], '')


@tag('core')
class StudioUserSearchOrderingTest(TestCase):
    """Exact match > startswith > substring; alphabetical within each band."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_exact_email_beats_startswith_beats_substring(self):
        User.objects.create_user(
            email='nealexis@test.com', password='pw',
            first_name='Nea', last_name='Lexis',
        )
        User.objects.create_user(
            email='alexander@test.com', password='pw',
            first_name='Alexander', last_name='Smith',
        )
        User.objects.create_user(
            email='alex@test.com', password='pw',
            first_name='Alex', last_name='Grigorev',
        )

        response = self.client.get(reverse('studio_user_search'), {'q': 'alex'})

        emails = [r['email'] for r in response.json()['results']]
        self.assertEqual(
            emails,
            ['alex@test.com', 'alexander@test.com', 'nealexis@test.com'],
        )

    def test_exact_full_name_beats_substring(self):
        User.objects.create_user(
            email='other@test.com', password='pw',
            first_name='Sam', last_name='Otherson',
        )
        User.objects.create_user(
            email='samone@test.com', password='pw',
            first_name='Sam', last_name='One',
        )

        response = self.client.get(reverse('studio_user_search'), {'q': 'Sam One'})

        emails = [r['email'] for r in response.json()['results']]
        self.assertEqual(emails[0], 'samone@test.com')

    def test_results_capped_at_ten(self):
        for i in range(15):
            User.objects.create_user(email=f'bulk{i:02d}@cap.test', password='pw')

        response = self.client.get(reverse('studio_user_search'), {'q': 'bulk'})

        self.assertEqual(len(response.json()['results']), 10)


@tag('core')
class StudioUserSearchSprintContextTest(TestCase):
    """``?sprint=<slug>`` adds in_sprint and has_plan_in_sprint flags."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.enrolled = User.objects.create_user(
            email='enrolled@test.com', password='pw',
            first_name='En', last_name='Rolled',
        )
        cls.requested = User.objects.create_user(
            email='requested@test.com', password='pw',
            first_name='Re', last_name='Quested',
        )
        cls.outside = User.objects.create_user(
            email='outside@test.com', password='pw',
            first_name='Out', last_name='Side',
        )
        # `enrolled` is enrolled AND has a plan in the sprint.
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.enrolled)
        Plan.objects.create(
            sprint=cls.sprint, member=cls.enrolled, goal='ship it',
        )
        # `requested` is enrolled but has no plan.
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.requested)
        # `outside` is neither enrolled nor has a plan.

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_sprint_context_flags_per_user(self):
        response = self.client.get(
            reverse('studio_user_search'),
            {'q': 'test', 'sprint': 'may-2026'},
        )

        rows = {r['email']: r for r in response.json()['results']}
        self.assertTrue(rows['enrolled@test.com']['in_sprint'])
        self.assertTrue(rows['enrolled@test.com']['has_plan_in_sprint'])
        self.assertTrue(rows['requested@test.com']['in_sprint'])
        self.assertFalse(rows['requested@test.com']['has_plan_in_sprint'])
        self.assertFalse(rows['outside@test.com']['in_sprint'])
        self.assertFalse(rows['outside@test.com']['has_plan_in_sprint'])

    def test_without_sprint_query_no_sprint_flags(self):
        response = self.client.get(reverse('studio_user_search'), {'q': 'test'})

        for row in response.json()['results']:
            self.assertNotIn('in_sprint', row)
            self.assertNotIn('has_plan_in_sprint', row)

    def test_unknown_sprint_slug_returns_404(self):
        response = self.client.get(
            reverse('studio_user_search'),
            {'q': 'test', 'sprint': 'no-such-sprint'},
        )

        self.assertEqual(response.status_code, 404)


@tag('core')
class StudioPeoplePickerIncludeRenderTest(TestCase):
    """Direct rendering of the new ``_people_picker.html`` include."""

    def _render(self, **kwargs):
        template = Template(
            '{% include "studio/includes/_people_picker.html" '
            'with name=name id_prefix=id_prefix search_url=search_url '
            'extra_query=extra_query on_select_action=on_select_action '
            'navigate_url_template=navigate_url_template %}'
        )
        ctx = {
            'name': kwargs.get('name', 'member'),
            'id_prefix': kwargs.get('id_prefix', 'picker-demo'),
            'search_url': kwargs.get('search_url', '/studio/api/users/search/'),
            'extra_query': kwargs.get('extra_query', ''),
            'on_select_action': kwargs.get('on_select_action', 'set_value'),
            'navigate_url_template': kwargs.get('navigate_url_template', ''),
        }
        return template.render(Context(ctx))

    def test_renders_input_with_prefixed_id_and_search_url(self):
        html = self._render(
            id_prefix='custom-prefix',
            search_url='/studio/api/users/search/',
        )

        self.assertIn('id="custom-prefix-search"', html)
        self.assertIn('data-search-url="/studio/api/users/search/"', html)
        self.assertIn('id="custom-prefix-suggestions"', html)

    def test_renders_hidden_field_with_passed_name(self):
        html = self._render(name='member', id_prefix='picker-demo')

        self.assertIn('name="member"', html)
        self.assertIn('id="picker-demo-id"', html)

    def test_extra_query_attribute_propagates(self):
        html = self._render(
            id_prefix='picker-demo', extra_query='sprint=may-2026',
        )

        self.assertIn('data-extra-query="sprint=may-2026"', html)

    def test_navigate_template_attribute_propagates(self):
        html = self._render(
            id_prefix='picker-demo',
            on_select_action='navigate',
            navigate_url_template='/studio/users/{id}/tier_override/',
        )

        self.assertIn('data-on-select="navigate"', html)
        self.assertIn(
            'data-navigate-template="/studio/users/{id}/tier_override/"',
            html,
        )


@tag('core')
class StudioTierOverridesPageUsesIncludeTest(TestCase):
    """Migrating tier_overrides.html must keep the existing picker contract."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_page_uses_shared_include(self):
        response = self.client.get(reverse('studio_tier_overrides_list'))

        self.assertTemplateUsed(response, 'studio/includes/_people_picker.html')
        # The shared markup must keep the legacy testids and search URL the
        # existing tier-overrides Playwright tests look for.
        self.assertContains(response, 'data-testid="tier-override-user-search"')
        self.assertContains(
            response, 'data-testid="tier-override-user-suggestions"',
        )
        self.assertContains(
            response, 'data-search-url="/studio/api/users/search/"',
        )
        self.assertContains(
            response,
            'data-navigate-template="/studio/users/{id}/tier_override/"',
        )
