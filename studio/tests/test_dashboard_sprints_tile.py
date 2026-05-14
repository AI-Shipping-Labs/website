"""Tests for the Studio dashboard "Active Sprints" tile (issue #442).

The tile shows:

- ``stats.active_sprints`` -- count of sprints with ``status='active'``.
- ``stats.total_plans`` -- count of all plans across all sprints.
- An anchor whose ``href`` resolves to the Studio sprint list view.

The tile costs exactly two extra SQL queries on top of the pre-#442
baseline -- one ``count()`` per stat. No annotation, no N+1.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import Plan, Sprint

User = get_user_model()


class StudioDashboardSprintsTileCountsTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='pw')

    def test_zero_state_renders_zero_counts(self):
        response = self.client.get('/studio/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['active_sprints'], 0)
        self.assertEqual(response.context['stats']['total_plans'], 0)
        self.assertContains(
            response, 'data-testid="studio-dashboard-active-sprints"',
        )
        self.assertContains(
            response, 'data-testid="studio-dashboard-plans-subline"',
        )
        self.assertContains(response, '0 plans')

    def test_active_sprint_count_excludes_draft_and_completed(self):
        # 1 active, 1 draft, 1 completed.
        Sprint.objects.create(
            name='Active', slug='active-1',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        Sprint.objects.create(
            name='Draft', slug='draft-1',
            start_date=datetime.date(2026, 6, 1),
            status='draft',
        )
        Sprint.objects.create(
            name='Done', slug='done-1',
            start_date=datetime.date(2026, 4, 1),
            status='completed',
        )

        response = self.client.get('/studio/')

        self.assertEqual(response.context['stats']['active_sprints'], 1)

    def test_total_plans_counts_across_all_sprints(self):
        sprint_active = Sprint.objects.create(
            name='Active', slug='active-2',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        sprint_draft = Sprint.objects.create(
            name='Draft', slug='draft-2',
            start_date=datetime.date(2026, 6, 1),
            status='draft',
        )
        # 3 plans on active sprint, 2 plans on draft sprint = 5 total.
        for i in range(3):
            user = User.objects.create_user(
                email=f'a{i}@test.com', password='pw',
            )
            Plan.objects.create(member=user, sprint=sprint_active)
        for i in range(2):
            user = User.objects.create_user(
                email=f'd{i}@test.com', password='pw',
            )
            Plan.objects.create(member=user, sprint=sprint_draft)

        response = self.client.get('/studio/')

        self.assertEqual(response.context['stats']['active_sprints'], 1)
        self.assertEqual(response.context['stats']['total_plans'], 5)
        # The subline pluralizes correctly.
        self.assertContains(response, '5 plans')

    def test_singular_plan_uses_correct_pluralization(self):
        sprint = Sprint.objects.create(
            name='Solo', slug='solo-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        user = User.objects.create_user(
            email='solo@test.com', password='pw',
        )
        Plan.objects.create(member=user, sprint=sprint)

        response = self.client.get('/studio/')

        self.assertEqual(response.context['stats']['total_plans'], 1)
        self.assertContains(response, '1 plan')
        # Make sure we did not accidentally produce "1 plans".
        self.assertNotContains(response, '1 plans')

    def test_tile_anchor_resolves_to_sprint_list(self):
        response = self.client.get('/studio/')

        expected_href = reverse('studio_sprint_list')
        self.assertContains(response, f'href="{expected_href}"')
        self.assertContains(
            response, 'data-testid="studio-dashboard-sprints-link"',
        )


class StudioDashboardSprintsTileQueryCountTest(TestCase):
    """The new tile must add exactly two count() queries -- no N+1.

    We measure the dashboard's query count with one sprint+plan row,
    then again with a much larger sprint+plan dataset, and assert the
    two counts are equal. If anyone slips in a per-sprint annotation
    or a per-plan select, the second measurement would be larger and
    this test fails.

    Determinism note: the dashboard route triggers a handful of
    one-time-per-process cache-miss queries that fire only on the FIRST
    request after a Python interpreter starts:

    - ``integrations.middleware.RedirectMiddleware`` reads
      ``Redirect`` via ``cache.get('active_redirects')`` and caches the
      result in the default ``LocMemCache`` for 5 minutes.
    - ``integrations.config.get_config`` (called from the site_context
      and env-mismatch context processors) reads ``IntegrationSetting``
      once and stashes the rows in a module-level dict.

    Both caches survive ``TestCase`` rollback because they live in
    process memory, not in the test DB. Without explicit pre-warming
    the test would observe two extra queries on whichever measurement
    happened first in the process -- which depends on whether other
    tests in the same parallel worker ran before us. CI's ``--parallel``
    scheduler can dispatch us to either a cold or warm worker, so
    ``baseline_count`` floated between 28 and 30 and the test failed
    intermittently with both directions of inequality (28 != 30 and
    28 != 29). We pre-warm via one throwaway ``GET /studio/`` in
    ``setUp`` so both measurements see the warm-cache state and the
    assertion observes only view-level query work.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='pw')
        # Pre-warm process-level caches that the dashboard touches via
        # middleware + context processors so the measured GETs both
        # start from the same warm state. See class docstring.
        self._warm_process_caches()

    def _warm_process_caches(self):
        """Discard one dashboard response purely to populate caches."""
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)

    def _measure_dashboard_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries), [q['sql'] for q in ctx.captured_queries]

    def test_dashboard_query_count_does_not_scale_with_sprint_or_plan_count(self):
        # Small dataset baseline.
        small_sprint = Sprint.objects.create(
            name='Small', slug='small',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        small_user = User.objects.create_user(
            email='s@test.com', password='pw',
        )
        Plan.objects.create(member=small_user, sprint=small_sprint)

        baseline_count, _ = self._measure_dashboard_queries()

        # Add many more sprints + plans.
        for i in range(5):
            sprint = Sprint.objects.create(
                name=f'Sprint {i}', slug=f'sprint-extra-{i}',
                start_date=datetime.date(2026, 5, 1),
                status='active' if i % 2 == 0 else 'draft',
            )
            for p_idx in range(4):
                user = User.objects.create_user(
                    email=f'm{i}-{p_idx}@test.com', password='pw',
                )
                Plan.objects.create(member=user, sprint=sprint)

        scaled_count, scaled_sqls = self._measure_dashboard_queries()

        self.assertEqual(
            baseline_count, scaled_count,
            f'Studio dashboard query count grew with data volume '
            f'({baseline_count} -> {scaled_count}). '
            f'A per-sprint or per-plan query slipped in. SQLs: {scaled_sqls}',
        )

    def test_dashboard_emits_one_count_per_new_stat(self):
        """Two extra count queries -- one for sprints, one for plans.

        Hits the SQL log directly to count occurrences of the relevant
        table-name + COUNT pattern. This is brittle to refactors but
        cheap and direct: it documents the cost shape claimed in the
        groomed spec.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        sqls = [q['sql'].lower() for q in ctx.captured_queries]
        sprint_counts = [
            s for s in sqls
            if 'count(' in s and '"plans_sprint"' in s
        ]
        plan_counts = [
            s for s in sqls
            if 'count(' in s and '"plans_plan"' in s
        ]
        self.assertEqual(
            len(sprint_counts), 1,
            f'Expected exactly one COUNT against plans_sprint; '
            f'got {sprint_counts}',
        )
        self.assertEqual(
            len(plan_counts), 1,
            f'Expected exactly one COUNT against plans_plan; '
            f'got {plan_counts}',
        )
