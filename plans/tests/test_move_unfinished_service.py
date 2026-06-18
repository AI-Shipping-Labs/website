"""Service tests for moving unfinished plan items to another sprint (#1042)."""

import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone

from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Resource,
    Sprint,
    SprintEnrollment,
    Week,
    WeekNote,
)
from plans.services import (
    MoveUnfinishedItemsError,
    eligible_move_target_sprints,
    move_unfinished_items_to_sprint,
    unfinished_plan_item_counts,
)

User = get_user_model()


def _make_plan(member, sprint, weeks=None):
    plan = Plan.objects.create(member=member, sprint=sprint)
    week_count = weeks if weeks is not None else sprint.duration_weeks
    for n in range(1, week_count + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


class MoveUnfinishedItemsServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='member@test.com', password='pw')
        cls.source_sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1), duration_weeks=6,
        )
        cls.next_sprint = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 1), duration_weeks=6,
        )
        cls.later_sprint = Sprint.objects.create(
            name='July 2026', slug='july-2026',
            start_date=datetime.date(2026, 7, 1), duration_weeks=4,
        )
        cls.cancelled_sprint = Sprint.objects.create(
            name='Cancelled', slug='cancelled',
            start_date=datetime.date(2026, 8, 1), duration_weeks=4,
            status='cancelled',
        )

    def setUp(self):
        self.source = _make_plan(self.member, self.source_sprint, 6)
        self.source_weeks = {
            week.week_number: week for week in self.source.weeks.all()
        }

    def test_eligible_targets_are_later_non_cancelled_in_start_order(self):
        targets = list(eligible_move_target_sprints(source_plan=self.source))
        self.assertEqual([s.slug for s in targets], ['june-2026', 'july-2026'])

    def test_counts_unfinished_by_type(self):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='unfinished cp', position=0,
        )
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='done cp', position=1,
            done_at=timezone.now(),
        )
        Deliverable.objects.create(plan=self.source, description='ship', position=0)
        NextStep.objects.create(plan=self.source, description='follow', position=0)

        self.assertEqual(
            unfinished_plan_item_counts(source_plan=self.source),
            {
                'checkpoints': 1,
                'deliverables': 1,
                'next_steps': 1,
                'total': 3,
            },
        )

    def test_moves_only_unfinished_items_and_leaves_unrelated_content(self):
        unfinished = Checkpoint.objects.create(
            week=self.source_weeks[1], description='move cp', position=0,
        )
        completed = Checkpoint.objects.create(
            week=self.source_weeks[2], description='keep done cp', position=0,
            done_at=timezone.now(),
        )
        deliverable = Deliverable.objects.create(
            plan=self.source, description='move deliverable', position=0,
        )
        done_deliverable = Deliverable.objects.create(
            plan=self.source, description='done deliverable', position=1,
            done_at=timezone.now(),
        )
        step = NextStep.objects.create(
            plan=self.source, description='move step', position=0,
        )
        Resource.objects.create(plan=self.source, title='stay resource')
        WeekNote.objects.create(
            week=self.source_weeks[1], body='stay note', author=self.member,
        )

        summary = move_unfinished_items_to_sprint(
            source_plan=self.source,
            target_sprint=self.next_sprint,
            actor=self.staff,
        )

        target = Plan.objects.get(member=self.member, sprint=self.next_sprint)
        self.assertTrue(summary['created_target_plan'])
        self.assertEqual(summary['target_plan_id'], target.pk)
        self.assertEqual(summary['moved']['total'], 3)

        unfinished.refresh_from_db()
        deliverable.refresh_from_db()
        step.refresh_from_db()
        completed.refresh_from_db()
        done_deliverable.refresh_from_db()

        self.assertEqual(unfinished.week.plan_id, target.pk)
        self.assertEqual(unfinished.week.week_number, 1)
        self.assertEqual(deliverable.plan_id, target.pk)
        self.assertEqual(step.plan_id, target.pk)
        self.assertEqual(completed.week.plan_id, self.source.pk)
        self.assertEqual(done_deliverable.plan_id, self.source.pk)
        self.assertEqual(self.source.resources.count(), 1)
        self.assertEqual(WeekNote.objects.filter(week__plan=self.source).count(), 1)
        self.assertEqual(SprintEnrollment.objects.get(
            sprint=self.next_sprint,
            user=self.member,
        ).enrolled_by_id, self.staff.pk)

    def test_reuses_existing_target_plan_and_appends_after_existing_content(self):
        target = _make_plan(self.member, self.next_sprint, 6)
        target.goal = 'Existing goal'
        target.summary_goal = 'Existing summary'
        target.save()
        target_week = target.weeks.get(week_number=1)
        Checkpoint.objects.create(
            week=target_week, description='existing cp', position=0,
        )
        Deliverable.objects.create(
            plan=target, description='existing deliverable', position=0,
        )
        NextStep.objects.create(
            plan=target, description='existing step', position=0,
        )
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='source cp a', position=0,
        )
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='source cp b', position=1,
        )
        Deliverable.objects.create(
            plan=self.source, description='source deliverable', position=0,
        )
        NextStep.objects.create(
            plan=self.source, description='source step', position=0,
        )

        summary = move_unfinished_items_to_sprint(
            source_plan=self.source,
            target_sprint=self.next_sprint,
            actor=self.staff,
        )

        target.refresh_from_db()
        self.assertFalse(summary['created_target_plan'])
        self.assertEqual(target.goal, 'Existing goal')
        self.assertEqual(target.summary_goal, 'Existing summary')
        self.assertEqual(
            [cp.description for cp in target_week.checkpoints.all()],
            ['existing cp', 'source cp a', 'source cp b'],
        )
        self.assertEqual(
            [d.description for d in target.deliverables.all()],
            ['existing deliverable', 'source deliverable'],
        )
        self.assertEqual(
            [s.description for s in target.next_steps.all()],
            ['existing step', 'source step'],
        )

    def test_shorter_target_sprint_receives_overflow_checkpoints_in_last_week(self):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='week 1', position=0,
        )
        Checkpoint.objects.create(
            week=self.source_weeks[4], description='week 4', position=0,
        )
        Checkpoint.objects.create(
            week=self.source_weeks[6], description='week 6', position=0,
        )

        move_unfinished_items_to_sprint(
            source_plan=self.source,
            target_sprint=self.later_sprint,
            actor=self.staff,
        )

        target = Plan.objects.get(member=self.member, sprint=self.later_sprint)
        self.assertEqual(
            [cp.description for cp in target.weeks.get(week_number=1).checkpoints.all()],
            ['week 1'],
        )
        self.assertEqual(
            [cp.description for cp in target.weeks.get(week_number=4).checkpoints.all()],
            ['week 4', 'week 6'],
        )

    def test_rerun_after_success_is_no_unfinished_no_op(self):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='move once', position=0,
        )
        move_unfinished_items_to_sprint(
            source_plan=self.source,
            target_sprint=self.next_sprint,
            actor=self.staff,
        )

        with self.assertRaises(MoveUnfinishedItemsError) as ctx:
            move_unfinished_items_to_sprint(
                source_plan=self.source,
                target_sprint=self.next_sprint,
                actor=self.staff,
            )

        self.assertEqual(ctx.exception.code, 'no_unfinished_items')
        target = Plan.objects.get(member=self.member, sprint=self.next_sprint)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=target).count(), 1,
        )

    def test_cancelled_and_earlier_targets_are_rejected_without_mutation(self):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='stay put', position=0,
        )
        earlier = Sprint.objects.create(
            name='April', slug='april',
            start_date=datetime.date(2026, 4, 1),
        )

        for sprint, code in (
            (self.cancelled_sprint, 'cancelled_target_sprint'),
            (self.source_sprint, 'target_sprint_not_later'),
            (earlier, 'target_sprint_not_later'),
        ):
            with self.subTest(sprint=sprint.slug):
                with self.assertRaises(MoveUnfinishedItemsError) as ctx:
                    move_unfinished_items_to_sprint(
                        source_plan=self.source,
                        target_sprint=sprint,
                        actor=self.staff,
                    )
                self.assertEqual(ctx.exception.code, code)

        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.source).count(), 1,
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=earlier).exists(),
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.cancelled_sprint).exists(),
        )

    def test_atomic_rollback_on_failure(self):
        checkpoint = Checkpoint.objects.create(
            week=self.source_weeks[1], description='cp', position=0,
        )
        deliverable = Deliverable.objects.create(
            plan=self.source, description='deliverable', position=0,
        )

        original_save = Deliverable.save

        def fail_deliverable_save(instance, *args, **kwargs):
            if instance.pk == deliverable.pk and instance.plan_id != self.source.pk:
                raise DatabaseError('boom')
            return original_save(instance, *args, **kwargs)

        with mock.patch.object(Deliverable, 'save', fail_deliverable_save):
            with self.assertRaises(DatabaseError):
                move_unfinished_items_to_sprint(
                    source_plan=self.source,
                    target_sprint=self.next_sprint,
                    actor=self.staff,
                )

        checkpoint.refresh_from_db()
        deliverable.refresh_from_db()
        self.assertEqual(checkpoint.week.plan_id, self.source.pk)
        self.assertEqual(deliverable.plan_id, self.source.pk)
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.next_sprint).exists(),
        )
