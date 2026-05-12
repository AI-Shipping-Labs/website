"""Tests confirming the Sprint plan card is absent from /account/.

Issue #581 removed the "Your sprint plan" card and its "View cohort"
link from the account page. The card and its data still exist on the
dashboard (`plans.dashboard.build_sprint_plan_card_context`), but
``/account/`` no longer renders it nor receives ``plan``,
``plan_progress_*`` or ``cohort_has_other_members`` in the template
context.

These tests are kept (rather than deleted) so the suite actively
guards against the card sneaking back in via a future refactor.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import Plan, Sprint

User = get_user_model()


class AccountSprintPlanCardAbsentWithoutPlanTest(TestCase):
    """Issue #581: the Sprint plan card never renders on /account/.

    Even when the user has no plan at all, none of the card markup,
    no ``plan`` context key, and no ``View cohort`` link should appear.
    """

    def test_card_markup_absent_when_user_has_no_plan(self):
        user = User.objects.create_user(email='noplan@test.com', password='pw')
        self.client.force_login(user)

        response = self.client.get('/account/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="sprint-plan-section"')
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"'
        )
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-open"'
        )
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-cohort"'
        )

    def test_view_does_not_pass_plan_context_keys(self):
        """The view stops calling ``build_sprint_plan_card_context``."""
        user = User.objects.create_user(email='noplan@test.com', password='pw')
        self.client.force_login(user)

        response = self.client.get('/account/')

        # ``response.context`` is a ContextList; ``in`` checks the keys.
        self.assertNotIn('plan', response.context)
        self.assertNotIn('plan_progress_done', response.context)
        self.assertNotIn('plan_progress_total', response.context)
        self.assertNotIn('cohort_has_other_members', response.context)


class AccountSprintPlanCardAbsentWithPlanTest(TestCase):
    """Issue #581: even when the user HAS a sprint plan, /account/
    must not render the card or the cohort link."""

    def setUp(self):
        self.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6,
            status='active',
        )
        self.user = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        self.plan = Plan.objects.create(
            member=self.user,
            sprint=self.sprint,
            status='active',
            visibility='private',
        )
        self.client.force_login(self.user)

    def test_card_markup_absent_for_user_with_active_plan(self):
        response = self.client.get('/account/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="sprint-plan-section"')
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"'
        )
        self.assertNotContains(response, 'Your sprint plan')
        self.assertNotContains(response, 'Open my plan')
        # The plan's sprint name is no longer surfaced on /account/.
        self.assertNotContains(response, 'May 2026')

    def test_view_drops_plan_context_keys_for_user_with_plan(self):
        """The plan exists, but the account view no longer threads it
        through to the template."""
        response = self.client.get('/account/')

        self.assertNotIn('plan', response.context)
        self.assertNotIn('plan_progress_done', response.context)
        self.assertNotIn('plan_progress_total', response.context)
        self.assertNotIn('cohort_has_other_members', response.context)


class AccountViewCohortLinkAbsentTest(TestCase):
    """Issue #581: the ``View cohort`` link must not appear on /account/
    even when a teammate is enrolled in the same sprint."""

    def test_cohort_link_absent_when_teammate_enrolled(self):
        sprint = Sprint.objects.create(
            name='Sprint Alpha',
            slug='sprint-alpha',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
            status='active',
        )
        viewer = User.objects.create_user(
            email='viewer@test.com', password='pw',
        )
        teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        Plan.objects.create(
            member=viewer, sprint=sprint, visibility='cohort',
        )
        Plan.objects.create(
            member=teammate, sprint=sprint, visibility='cohort',
        )
        self.client.force_login(viewer)

        response = self.client.get('/account/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'View cohort')
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-cohort"'
        )
