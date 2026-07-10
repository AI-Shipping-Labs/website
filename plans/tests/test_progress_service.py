"""Tests for shared sprint-plan progress annotations."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.access import LEVEL_MAIN
from plans.dashboard import (
    build_active_sprint_opportunities_context,
    build_sprint_plan_card_context,
)
from plans.models import Checkpoint, Plan, Sprint, SprintEnrollment, Week
from plans.services import annotate_plan_progress

User = get_user_model()


class AnnotatePlanProgressTest(TestCase):
    def test_counts_total_and_completed_checkpoints(self):
        sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        plan = Plan.objects.create(member=member, sprint=sprint)
        week_1 = Week.objects.create(plan=plan, week_number=1)
        week_2 = Week.objects.create(plan=plan, week_number=2)

        for index in range(3):
            Checkpoint.objects.create(
                week=week_1,
                description=f'done {index}',
                done_at=timezone.now(),
            )
        for index in range(2):
            Checkpoint.objects.create(
                week=week_2,
                description=f'todo {index}',
            )

        annotated_plan = annotate_plan_progress(
            Plan.objects.filter(pk=plan.pk),
        ).get()

        self.assertEqual(annotated_plan.progress_total, 5)
        self.assertEqual(annotated_plan.progress_done, 3)


class SprintPlanCardProgressContextTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=today - datetime.timedelta(days=7),
            duration_weeks=6,
            status='active',
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.member,
            sprint=cls.sprint,
            visibility='cohort',
            shared_at=timezone.now(),
        )
        week = Week.objects.create(plan=cls.plan, week_number=1)
        for index in range(2):
            Checkpoint.objects.create(
                week=week,
                description=f'done {index}',
                done_at=timezone.now(),
            )
        Checkpoint.objects.create(week=week, description='todo')

        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.teammate)

    def test_dashboard_card_context_uses_shared_progress_annotations(self):
        with self.assertNumQueries(2):
            context = build_sprint_plan_card_context(self.member)
            plan = context['plan']
            progress = (
                context['plan_progress_done'],
                context['plan_progress_total'],
                plan.progress_done,
                plan.progress_total,
                context['has_any_plan'],
                context['cohort_has_other_members'],
            )

        self.assertEqual(plan.pk, self.plan.pk)
        self.assertEqual(progress, (2, 3, 2, 3, True, True))

    def test_dashboard_card_context_defaults_to_zero_without_plan(self):
        user = User.objects.create_user(email='noplan@test.com', password='pw')

        with self.assertNumQueries(1):
            context = build_sprint_plan_card_context(user)

        self.assertEqual(
            context,
            {
                'plan': None,
                'has_any_plan': False,
                'plan_progress_total': 0,
                'plan_progress_done': 0,
                'cohort_has_other_members': False,
                'plan_has_ended': False,
                'sprint_end_feedback_response': None,
                'sprint_end_feedback_url': '',
                'sprint_end_feedback_label': '',
                'sprint_end_next_action': None,
            },
        )

    def test_dashboard_card_context_keeps_current_shared_plan_precedence(self):
        today = timezone.localdate()
        user = User.objects.create_user(
            email='current-shared@test.com', password='pw',
        )
        current = Sprint.objects.create(
            name='Current Sprint',
            slug='current-shared-precedence',
            start_date=today - datetime.timedelta(days=7),
            duration_weeks=6,
            status='active',
        )
        ended = Sprint.objects.create(
            name='Ended Sprint',
            slug='ended-shared-precedence',
            start_date=today - datetime.timedelta(days=70),
            duration_weeks=4,
            status='completed',
        )
        draft = Sprint.objects.create(
            name='Staff Draft Sprint',
            slug='draft-shared-precedence',
            start_date=today - datetime.timedelta(days=1),
            duration_weeks=6,
            status='active',
        )
        current_plan = Plan.objects.create(
            member=user,
            sprint=current,
            shared_at=timezone.now() - datetime.timedelta(days=30),
        )
        ended_plan = Plan.objects.create(
            member=user,
            sprint=ended,
            shared_at=timezone.now(),
        )
        draft_plan = Plan.objects.create(member=user, sprint=draft)
        Plan.objects.filter(pk=current_plan.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=30),
        )
        Plan.objects.filter(pk=ended_plan.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=1),
        )
        Plan.objects.filter(pk=draft_plan.pk).update(created_at=timezone.now())

        context = build_sprint_plan_card_context(user)

        self.assertTrue(context['has_any_plan'])
        self.assertEqual(context['plan'].pk, current_plan.pk)


class ActiveSprintOpportunitiesContextTest(TestCase):
    def test_unshared_member_draft_sprint_is_hidden_from_opportunities(self):
        today = timezone.localdate()
        user = User.objects.create_user(
            email='draft-opportunity@test.com', password='pw',
        )
        draft = Sprint.objects.create(
            name='Draft Sprint',
            slug='draft-opportunity-sprint',
            start_date=today - datetime.timedelta(days=7),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        other = Sprint.objects.create(
            name='Open Sprint',
            slug='open-opportunity-sprint',
            start_date=today + datetime.timedelta(days=7),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        SprintEnrollment.objects.create(sprint=draft, user=user)
        Plan.objects.create(member=user, sprint=draft)

        context = build_active_sprint_opportunities_context(user, LEVEL_MAIN)

        opportunities = context['active_sprint_opportunities']
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0]['sprint'].pk, other.pk)
        self.assertEqual(
            opportunities[0]['url'],
            reverse('sprint_detail', kwargs={'sprint_slug': other.slug}),
        )
        self.assertEqual(opportunities[0]['cta_label'], 'View sprint')
