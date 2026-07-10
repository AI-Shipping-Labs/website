"""Tests for the "Your sprint plan" card on the home dashboard (issue #442).

The authenticated home view (rendered from
``templates/content/dashboard.html``) carries the same card with the
same context keys as the Account page. These tests verify the
member-dashboard surface mirrors the Account-page surface and that
non-participant members see no sprint copy.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_PREMIUM
from plans.models import (
    Checkpoint,
    Plan,
    Sprint,
    SprintEnrollment,
    SprintFeedbackRequest,
    Week,
)
from questionnaires.models import Questionnaire, Response
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _active_sprint_start():
    return datetime.date.today() - datetime.timedelta(days=14)


def _expected_sprint_range(start_date, duration_weeks):
    end_date = start_date + datetime.timedelta(weeks=duration_weeks)
    if start_date.year == end_date.year:
        return (
            f'{start_date:%B} {start_date.day} – '
            f'{end_date:%B} {end_date.day}, {end_date.year} '
            f'({duration_weeks} weeks)'
        )
    return (
        f'{start_date:%B} {start_date.day}, {start_date.year} – '
        f'{end_date:%B} {end_date.day}, {end_date.year} '
        f'({duration_weeks} weeks)'
    )


class DashboardSprintPlanCardTest(TierSetupMixin, TestCase):
    def test_dashboard_card_shown_when_user_has_plan(self):
        user = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        start_date = _active_sprint_start()
        sprint = Sprint.objects.create(
            name='August 2026', slug='august-2026',
            start_date=start_date,
            duration_weeks=8,
            status='active',
        )
        plan = Plan.objects.create(
            member=user, sprint=sprint, shared_at=timezone.now(),
        )

        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['plan'].pk, plan.pk)
        self.assertContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
        # The Open my plan CTA points at the read-only owner view.
        expected_href = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
        )
        self.assertContains(response, f'href="{expected_href}"')
        # Sprint metadata is rendered.
        self.assertContains(response, 'August 2026')
        self.assertContains(response, _expected_sprint_range(start_date, 8))

    def test_ended_shared_plan_card_shows_recap_feedback_and_next_action(self):
        user = User.objects.create_user(
            email='ended-card@test.com',
            password='pw',
            tier=self.main_tier,
        )
        ended = Sprint.objects.create(
            name='Ended Sprint',
            slug='ended-sprint',
            start_date=datetime.date.today() - datetime.timedelta(weeks=8),
            duration_weeks=4,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        next_sprint = Sprint.objects.create(
            name='Next Sprint',
            slug='next-sprint',
            start_date=ended.end_date,
            duration_weeks=4,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        plan = Plan.objects.create(
            member=user,
            sprint=ended,
            shared_at=timezone.now(),
        )
        week = Week.objects.create(plan=plan, week_number=1, position=0)
        Checkpoint.objects.create(
            week=week,
            description='Done',
            position=0,
            done_at=timezone.now(),
        )
        Checkpoint.objects.create(
            week=week,
            description='Open',
            position=1,
        )
        questionnaire = Questionnaire.objects.create(
            title='Ended Feedback',
            slug='ended-feedback',
            purpose='feedback',
        )
        SprintFeedbackRequest.objects.create(
            sprint=ended,
            questionnaire=questionnaire,
            distributed_at=timezone.now(),
        )
        response = Response.objects.create(
            questionnaire=questionnaire,
            respondent=user,
        )

        self.client.login(email='ended-card@test.com', password='pw')
        page = self.client.get('/')

        self.assertContains(page, 'data-testid="account-sprint-plan-recap"')
        self.assertContains(page, '1 of 2 checkpoints done')
        self.assertContains(page, 'Share feedback')
        self.assertContains(
            page,
            reverse(
                'sprint_feedback_fill',
                kwargs={
                    'sprint_slug': ended.slug,
                    'response_id': response.pk,
                },
            ),
        )
        self.assertContains(page, 'Join the next sprint')
        self.assertContains(
            page,
            reverse(
                'sprint_detail',
                kwargs={'sprint_slug': next_sprint.slug},
            ),
        )

    def test_dashboard_card_hidden_when_user_has_no_plan(self):
        User.objects.create_user(
            email='nopl@test.com', password='pw',
        )

        self.client.login(email='nopl@test.com', password='pw')
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['plan'])
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"',
        )

    def test_anonymous_homepage_has_no_sprint_card(self):
        response = self.client.get('/')

        # Anonymous users see the public marketing homepage; the sprint
        # card markup must not be rendered there.
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"',
        )

    def test_eligible_user_without_plan_sees_active_sprint_opportunity(self):
        User.objects.create_user(
            email='main-sprint@test.com', password='pw', tier=self.main_tier,
        )
        sprint = Sprint.objects.create(
            name='Main Sprint', slug='main-sprint',
            start_date=_active_sprint_start(),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )

        self.client.login(email='main-sprint@test.com', password='pw')
        response = self.client.get('/')

        self.assertContains(response, 'Sprints & Cohorts')
        self.assertContains(response, 'Main Sprint')
        self.assertContains(
            response,
            reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug}),
        )
        self.assertContains(response, 'View sprint')

    def test_ineligible_user_does_not_see_locked_active_sprint(self):
        User.objects.create_user(
            email='basic-sprint@test.com', password='pw', tier=self.basic_tier,
        )
        Sprint.objects.create(
            name='Premium Sprint', slug='premium-sprint',
            start_date=_active_sprint_start(),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_PREMIUM,
        )

        self.client.login(email='basic-sprint@test.com', password='pw')
        response = self.client.get('/')

        self.assertNotContains(response, 'Premium Sprint')
        self.assertContains(response, 'No active sprint openings for your tier')
        self.assertContains(response, 'href="/activities"')

    def test_enrolled_user_without_plan_links_active_sprint_to_cohort(self):
        user = User.objects.create_user(
            email='enrolled-sprint@test.com', password='pw', tier=self.main_tier,
        )
        sprint = Sprint.objects.create(
            name='Enrolled Sprint', slug='enrolled-sprint',
            start_date=_active_sprint_start(),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        SprintEnrollment.objects.create(user=user, sprint=sprint)

        self.client.login(email='enrolled-sprint@test.com', password='pw')
        response = self.client.get('/')

        self.assertContains(response, 'Enrolled Sprint')
        self.assertContains(
            response,
            reverse('cohort_board', kwargs={'sprint_slug': sprint.slug}),
        )
        self.assertContains(response, 'View cohort')

    def test_user_with_plan_keeps_plan_card_and_can_see_other_sprint(self):
        user = User.objects.create_user(
            email='planned-sprint@test.com', password='pw', tier=self.main_tier,
        )
        current_start = _active_sprint_start()
        current = Sprint.objects.create(
            name='Current Sprint', slug='current-sprint',
            start_date=current_start,
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        other = Sprint.objects.create(
            name='Other Sprint', slug='other-sprint',
            start_date=current_start + datetime.timedelta(weeks=8),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        plan = Plan.objects.create(
            member=user, sprint=current, shared_at=timezone.now(),
        )

        self.client.login(email='planned-sprint@test.com', password='pw')
        response = self.client.get('/')

        self.assertContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
        self.assertContains(
            response,
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': current.slug, 'plan_id': plan.pk},
            ),
        )
        self.assertContains(response, 'Other Sprint')
        self.assertContains(
            response,
            reverse('sprint_detail', kwargs={'sprint_slug': other.slug}),
        )
