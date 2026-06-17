"""Tests for shared sprint-plan progress annotations."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from plans.dashboard import build_sprint_plan_card_context
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
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint, visibility='cohort',
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
                context['cohort_has_other_members'],
            )

        self.assertEqual(plan.pk, self.plan.pk)
        self.assertEqual(progress, (2, 3, 2, 3, True))

    def test_dashboard_card_context_defaults_to_zero_without_plan(self):
        user = User.objects.create_user(email='noplan@test.com', password='pw')

        with self.assertNumQueries(1):
            context = build_sprint_plan_card_context(user)

        self.assertEqual(
            context,
            {
                'plan': None,
                'plan_progress_total': 0,
                'plan_progress_done': 0,
                'cohort_has_other_members': False,
            },
        )
