"""Unit tests for the carry-over service (issue #808).

Covers ``find_carry_over_source_plan``, ``count_unfinished_carry_over_items``
and ``carry_over_unfinished_tasks`` in ``plans.services``: unfinished-only
copy, ``done_at`` reset, compacted source-week mapping with shorter-sprint
overflow, case-insensitive trimmed dedupe (idempotency), and atomicity.
"""

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
    Week,
    WeekNote,
)
from plans.services import (
    carry_over_unfinished_tasks,
    count_total_unfinished,
    count_unfinished_carry_over_items,
    find_carry_over_source_plan,
)

User = get_user_model()


def _make_plan(member, sprint, weeks):
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, weeks + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


class FindSourcePlanTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.other = User.objects.create_user(email='o@test.com', password='pw')
        cls.s_jan = Sprint.objects.create(
            name='Jan', slug='jan', start_date=datetime.date(2026, 1, 1),
        )
        cls.s_mar = Sprint.objects.create(
            name='Mar', slug='mar', start_date=datetime.date(2026, 3, 1),
        )
        cls.s_may = Sprint.objects.create(
            name='May', slug='may', start_date=datetime.date(2026, 5, 1),
        )

    def test_picks_most_recent_prior_plan(self):
        _make_plan(self.member, self.s_jan, 4)
        mar = _make_plan(self.member, self.s_mar, 4)
        may = _make_plan(self.member, self.s_may, 4)
        source = find_carry_over_source_plan(destination_plan=may)
        self.assertEqual(source.pk, mar.pk)

    def test_no_earlier_plan_returns_none(self):
        jan = _make_plan(self.member, self.s_jan, 4)
        _make_plan(self.member, self.s_may, 4)
        self.assertIsNone(find_carry_over_source_plan(destination_plan=jan))

    def test_never_another_members_plan(self):
        _make_plan(self.other, self.s_jan, 4)
        may = _make_plan(self.member, self.s_may, 4)
        self.assertIsNone(find_carry_over_source_plan(destination_plan=may))

    def test_tie_on_start_date_breaks_by_higher_sprint_id(self):
        tie_low = Sprint.objects.create(
            name='TieLow', slug='tie-low',
            start_date=datetime.date(2026, 3, 1),
        )
        tie_high = Sprint.objects.create(
            name='TieHigh', slug='tie-high',
            start_date=datetime.date(2026, 3, 1),
        )
        _make_plan(self.member, tie_low, 4)
        high = _make_plan(self.member, tie_high, 4)
        # both tie_low and tie_high share start_date 2026-03-01 and are both
        # earlier than May; the higher-id sprint wins. tie_high was created
        # after tie_low so it has the higher id.
        may = _make_plan(self.member, self.s_may, 4)
        source = find_carry_over_source_plan(destination_plan=may)
        self.assertEqual(source.pk, high.pk)


class CarryOverServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.s_prev = Sprint.objects.create(
            name='Prev', slug='prev',
            start_date=datetime.date(2026, 1, 1), duration_weeks=6,
        )
        cls.s_next = Sprint.objects.create(
            name='Next', slug='next',
            start_date=datetime.date(2026, 5, 1), duration_weeks=6,
        )

    def setUp(self):
        self.source = _make_plan(self.member, self.s_prev, 6)
        self.dest = _make_plan(self.member, self.s_next, 6)
        self.src_weeks = {w.week_number: w for w in self.source.weeks.all()}

    def _dest_checkpoints(self, week_number):
        week = self.dest.weeks.get(week_number=week_number)
        return list(week.checkpoints.all())

    def _plan_checkpoints(self, plan, week_number):
        week = plan.weeks.get(week_number=week_number)
        return list(week.checkpoints.all())

    def test_copies_only_unfinished_checkpoints(self):
        Checkpoint.objects.create(
            week=self.src_weeks[1], description='unfinished', position=0,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[1], description='done one', position=1,
            done_at=timezone.now(),
        )
        copied = carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(copied, 1)
        descs = [c.description for c in self._dest_checkpoints(1)]
        self.assertEqual(descs, ['unfinished'])

    def test_done_at_reset_to_null_on_copy(self):
        # A source row with done_at set would not be copied; this guards
        # the reset for items that ARE eligible by asserting the new copy
        # is not done.
        Checkpoint.objects.create(
            week=self.src_weeks[2], description='task', position=0,
        )
        carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        copy = self._dest_checkpoints(1)[0]
        self.assertIsNone(copy.done_at)

    def test_finished_items_remain_on_source(self):
        done = Checkpoint.objects.create(
            week=self.src_weeks[1], description='done', position=0,
            done_at=timezone.now(),
        )
        carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        done.refresh_from_db()
        self.assertIsNotNone(done.done_at)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 0,
        )

    def test_compacts_later_unfinished_source_weeks_to_front(self):
        Checkpoint.objects.create(
            week=self.src_weeks[1], description='done w1', position=0,
            done_at=timezone.now(),
        )
        Checkpoint.objects.create(
            week=self.src_weeks[2], description='done w2', position=0,
            done_at=timezone.now(),
        )
        Checkpoint.objects.create(
            week=self.src_weeks[3], description='w3 task', position=0,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[4], description='w4 task', position=0,
        )
        carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(
            [c.description for c in self._dest_checkpoints(1)], ['w3 task'],
        )
        self.assertEqual(
            [c.description for c in self._dest_checkpoints(2)], ['w4 task'],
        )
        self.assertEqual(self._dest_checkpoints(3), [])

    def test_source_week_one_stays_week_one_when_unfinished(self):
        Checkpoint.objects.create(
            week=self.src_weeks[1], description='w1 task', position=0,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[4], description='w4 task', position=0,
        )
        carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(
            [c.description for c in self._dest_checkpoints(1)], ['w1 task'],
        )
        self.assertEqual(
            [c.description for c in self._dest_checkpoints(2)], ['w4 task'],
        )

    def test_multiple_items_in_source_week_stay_together_in_order(self):
        Checkpoint.objects.create(
            week=self.src_weeks[4], description='w4 second', position=2,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[4], description='w4 first', position=1,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[5], description='w5 only', position=0,
        )
        carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(
            [c.description for c in self._dest_checkpoints(1)],
            ['w4 first', 'w4 second'],
        )
        self.assertEqual(
            [c.description for c in self._dest_checkpoints(2)], ['w5 only'],
        )

    def test_shorter_destination_overflows_into_last_week(self):
        short_sprint = Sprint.objects.create(
            name='Short', slug='short',
            start_date=datetime.date(2026, 6, 1), duration_weeks=2,
        )
        short = _make_plan(self.member, short_sprint, 2)
        Checkpoint.objects.create(
            week=self.src_weeks[3], description='week3 task', position=0,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[4], description='week4 task', position=0,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[6], description='week6 task', position=0,
        )
        carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=short,
        )
        self.assertEqual(
            [c.description for c in self._plan_checkpoints(short, 1)],
            ['week3 task'],
        )
        self.assertEqual(
            [c.description for c in self._plan_checkpoints(short, 2)],
            ['week4 task', 'week6 task'],
        )

    def test_copies_deliverables_and_next_steps_unfinished_only(self):
        Deliverable.objects.create(
            plan=self.source, description='ship', position=0,
        )
        Deliverable.objects.create(
            plan=self.source, description='done deliverable', position=1,
            done_at=timezone.now(),
        )
        NextStep.objects.create(
            plan=self.source, description='follow up', position=0,
        )
        copied = carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(copied, 2)
        self.assertEqual(
            [d.description for d in self.dest.deliverables.all()], ['ship'],
        )
        self.assertEqual(
            [s.description for s in self.dest.next_steps.all()], ['follow up'],
        )
        self.assertEqual(self.dest.next_steps.get().kind, 'pre_sprint')

    def test_does_not_copy_resources_or_week_notes(self):
        Resource.objects.create(
            plan=self.source, title='a resource', position=0,
        )
        WeekNote.objects.create(
            week=self.src_weeks[1], body='reflection', author=self.member,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[1], description='task', position=0,
        )
        carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(self.dest.resources.count(), 0)
        self.assertEqual(
            WeekNote.objects.filter(week__plan=self.dest).count(), 0,
        )

    def test_idempotent_case_insensitive_trimmed_dedupe(self):
        Checkpoint.objects.create(
            week=self.src_weeks[1], description='Build API', position=0,
        )
        Deliverable.objects.create(
            plan=self.source, description='Ship it', position=0,
        )
        first = carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(first, 2)
        # Re-run with no source changes -> no new rows.
        second = carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(second, 0)
        self.assertEqual(len(self._dest_checkpoints(1)), 1)
        self.assertEqual(self.dest.deliverables.count(), 1)

    def test_dedupe_matches_despite_whitespace_and_case(self):
        Checkpoint.objects.create(
            week=self.src_weeks[3], description='  Build the THING  ',
            position=0,
        )
        # Source Week 3 compacts into destination Week 1. Pre-seed that
        # compacted target with a differently-cased / padded variant.
        dest_week = self.dest.weeks.get(week_number=1)
        Checkpoint.objects.create(
            week=dest_week, description='build the thing', position=0,
        )
        copied = carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(copied, 0)
        self.assertEqual(len(self._dest_checkpoints(1)), 1)

    def test_dedupe_scoped_per_destination_week_for_checkpoints(self):
        # Same description in two different source weeks must each land in
        # their own compacted destination week; they are not deduped
        # against each other because the dedupe is week-scoped.
        Checkpoint.objects.create(
            week=self.src_weeks[3], description='same text', position=0,
        )
        Checkpoint.objects.create(
            week=self.src_weeks[5], description='same text', position=0,
        )
        copied = carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(copied, 2)
        self.assertEqual(len(self._dest_checkpoints(1)), 1)
        self.assertEqual(len(self._dest_checkpoints(2)), 1)

    def test_no_op_when_nothing_to_copy(self):
        copied = carry_over_unfinished_tasks(
            source_plan=self.source, destination_plan=self.dest,
        )
        self.assertEqual(copied, 0)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 0,
        )

    def test_atomic_rollback_on_failure(self):
        Checkpoint.objects.create(
            week=self.src_weeks[1], description='one', position=0,
        )
        Deliverable.objects.create(
            plan=self.source, description='two', position=0,
        )
        # Make the Deliverable insert blow up after checkpoints copied so
        # we can prove the whole thing rolled back (no partial copy).
        with mock.patch(
            'plans.services.Deliverable.objects.create',
            side_effect=DatabaseError('boom'),
        ):
            with self.assertRaises(DatabaseError):
                carry_over_unfinished_tasks(
                    source_plan=self.source, destination_plan=self.dest,
                )
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 0,
        )
        self.assertEqual(self.dest.deliverables.count(), 0)


class CountUnfinishedTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='c@test.com', password='pw')
        cls.s_prev = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 1, 1),
            duration_weeks=2,
        )
        cls.s_next = Sprint.objects.create(
            name='S2', slug='s2', start_date=datetime.date(2026, 5, 1),
            duration_weeks=2,
        )

    def test_count_total_unfinished_across_types(self):
        plan = _make_plan(self.member, self.s_prev, 2)
        weeks = {w.week_number: w for w in plan.weeks.all()}
        Checkpoint.objects.create(week=weeks[1], description='a', position=0)
        Checkpoint.objects.create(
            week=weeks[1], description='done', position=1,
            done_at=timezone.now(),
        )
        Deliverable.objects.create(plan=plan, description='d', position=0)
        NextStep.objects.create(plan=plan, description='n', position=0)
        self.assertEqual(count_total_unfinished(source_plan=plan), 3)

    def test_net_count_subtracts_already_copied(self):
        source = _make_plan(self.member, self.s_prev, 2)
        dest = _make_plan(self.member, self.s_next, 2)
        sw = {w.week_number: w for w in source.weeks.all()}
        Checkpoint.objects.create(week=sw[1], description='a', position=0)
        Checkpoint.objects.create(week=sw[1], description='b', position=1)
        # Net count starts at 2.
        self.assertEqual(
            count_unfinished_carry_over_items(
                source_plan=source, destination_plan=dest,
            ),
            2,
        )
        carry_over_unfinished_tasks(source_plan=source, destination_plan=dest)
        # After copying, net count is 0 even though total unfinished is 2.
        self.assertEqual(
            count_unfinished_carry_over_items(
                source_plan=source, destination_plan=dest,
            ),
            0,
        )
        self.assertEqual(count_total_unfinished(source_plan=source), 2)

    def test_net_count_uses_compacted_destination_week(self):
        source = _make_plan(self.member, self.s_prev, 2)
        dest = _make_plan(self.member, self.s_next, 2)
        sw = {w.week_number: w for w in source.weeks.all()}
        dw = {w.week_number: w for w in dest.weeks.all()}
        Checkpoint.objects.create(
            week=sw[2], description=' build eval set ', position=0,
        )
        Checkpoint.objects.create(
            week=sw[2], description='Write rubric', position=1,
        )
        Checkpoint.objects.create(
            week=dw[1], description='Build Eval Set', position=0,
        )

        self.assertEqual(
            count_unfinished_carry_over_items(
                source_plan=source, destination_plan=dest,
            ),
            1,
        )
        copied = carry_over_unfinished_tasks(source_plan=source, destination_plan=dest)
        self.assertEqual(copied, 1)
        self.assertEqual(
            [c.description for c in dw[1].checkpoints.all()],
            ['Build Eval Set', 'Write rubric'],
        )
        self.assertEqual(
            count_unfinished_carry_over_items(
                source_plan=source, destination_plan=dest,
            ),
            0,
        )
