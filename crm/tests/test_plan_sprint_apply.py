"""Tests for `#plan-sprints` auto-apply + reversal (issue #890, Phase 2).

The LLM is stubbed at the parse boundary (``parse_plan_sprint_thread``); no
live provider is ever called. Covers: apply flips + change rows, already-done
skip, hallucinated-id drop, skip rules (unmatched / non-active sprint), daily-
rerun idempotency, new-reply idempotency, and the reversal helpers.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from crm.models import (
    AppliedProgressChange,
    IngestedProgressEvent,
    SlackMessage,
    SlackThread,
)
from crm.services.plan_sprint_parse import (
    ParsedCompletion,
    PlanSprintParseResult,
)
from crm.tasks.apply_plan_sprint_progress import (
    apply_thread_progress,
    reverse_change,
    reverse_event,
)
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Sprint,
    Week,
)

User = get_user_model()

CHANNEL = 'C_TEST_PLANSPRINTS'


def _parse_result(completions, summary='Did stuff.', blockers=None):
    """Build a stubbed PlanSprintParseResult."""
    return PlanSprintParseResult(
        completed_items=[
            ParsedCompletion(item_kind=k, item_id=i, confidence=1.0)
            for (k, i) in completions
        ],
        summary=summary,
        blockers=blockers or [],
    )


@override_settings(LLM_API_KEY='sk-test', LLM_PROVIDER='anthropic')
class ApplyProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@example.com', password='x', slack_user_id='U_MEMBER',
        )
        cls.sprint = Sprint.objects.create(
            name='Active Sprint', slug='active-sprint',
            start_date=datetime.date(2026, 5, 1), status='active',
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)
        cls.cp = Checkpoint.objects.create(week=cls.week, description='Pipeline')
        cls.deliv = Deliverable.objects.create(plan=cls.plan, description='Demo')
        cls.nstep = NextStep.objects.create(plan=cls.plan, description='Deploy')

    def _thread(self, ts='1700000000.000100', text='Done', user='U_MEMBER',
                member=None, plan=None):
        thread = SlackThread.objects.create(
            channel_id=CHANNEL, thread_ts=ts, slack_user_id=user,
            member=member if member is not None else self.member,
            plan=plan if plan is not None else self.plan,
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=thread, ts=ts, slack_user_id=user, text=text,
            posted_at=timezone.now(), is_root=True,
        )
        return thread

    def _apply(self, thread, result, ingest=None):
        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            return_value=result,
        ) as mock_parse:
            event = apply_thread_progress(thread, ingest=ingest)
        return event, mock_parse

    def test_apply_flips_items_and_records_change_rows(self):
        thread = self._thread()
        result = _parse_result(
            [('checkpoint', self.cp.id), ('deliverable', self.deliv.id)],
            blockers=['blocked on X'],
        )
        event, _ = self._apply(thread, result)

        self.cp.refresh_from_db()
        self.deliv.refresh_from_db()
        self.assertIsNotNone(self.cp.done_at)
        self.assertIsNotNone(self.deliv.done_at)
        self.assertEqual(event.changes.count(), 2)
        for change in event.changes.all():
            self.assertIsNone(change.previous_done_at)
        self.assertEqual(event.blockers, ['blocked on X'])

    def test_already_done_item_records_no_change(self):
        # next-step already done before apply.
        self.nstep.done_at = timezone.now()
        self.nstep.save(update_fields=['done_at'])
        prior = self.nstep.done_at

        thread = self._thread()
        result = _parse_result(
            [('checkpoint', self.cp.id), ('next_step', self.nstep.id)],
        )
        event, _ = self._apply(thread, result)

        self.nstep.refresh_from_db()
        self.assertEqual(self.nstep.done_at, prior)
        # only the checkpoint flip recorded; the already-done next-step is not.
        kinds = list(event.changes.values_list('item_kind', flat=True))
        self.assertEqual(kinds, ['checkpoint'])

    def test_hallucinated_id_is_dropped(self):
        thread = self._thread()
        result = _parse_result(
            [('checkpoint', self.cp.id), ('deliverable', 999999)],
        )
        event, _ = self._apply(thread, result)

        self.assertEqual(event.changes.count(), 1)
        self.assertEqual(event.changes.first().item_kind, 'checkpoint')

    def test_unmatched_thread_produces_no_event(self):
        thread = self._thread(member=None, plan=None, user='U_STRANGER')
        # set member/plan null explicitly
        thread.member = None
        thread.plan = None
        thread.save(update_fields=['member', 'plan'])
        result = _parse_result([('checkpoint', self.cp.id)])

        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            return_value=result,
        ) as mock_parse:
            event = apply_thread_progress(thread)
        self.assertIsNone(event)
        mock_parse.assert_not_called()
        self.assertEqual(IngestedProgressEvent.objects.count(), 0)

    def test_non_active_sprint_plan_produces_no_event(self):
        self.sprint.status = 'completed'
        self.sprint.save(update_fields=['status'])
        thread = self._thread()
        result = _parse_result([('checkpoint', self.cp.id)])

        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            return_value=result,
        ) as mock_parse:
            event = apply_thread_progress(thread)
        self.assertIsNone(event)
        mock_parse.assert_not_called()

    def test_daily_rerun_no_new_replies_is_idempotent(self):
        thread = self._thread()
        result = _parse_result([('checkpoint', self.cp.id)])
        event1, _ = self._apply(thread, result)
        self.assertEqual(AppliedProgressChange.objects.count(), 1)

        # Second run, no new messages: must NOT parse again or add rows.
        event2, mock_parse = self._apply(thread, result)
        mock_parse.assert_not_called()
        self.assertEqual(event1.pk, event2.pk)
        self.assertEqual(AppliedProgressChange.objects.count(), 1)
        self.assertEqual(IngestedProgressEvent.objects.count(), 1)

    def test_new_reply_applies_only_still_null_items(self):
        thread = self._thread()
        result1 = _parse_result([('checkpoint', self.cp.id)])
        event1, _ = self._apply(thread, result1)
        self.assertEqual(event1.source_message_ts, '1700000000.000100')
        self.cp.refresh_from_db()
        cp_done_at = self.cp.done_at

        # New reply arrives with a later ts; stub now reports cp AND next-step.
        SlackMessage.objects.create(
            thread=thread, ts='1700000050.000000', slack_user_id='U_MEMBER',
            text='Also finished deploy', posted_at=timezone.now(),
        )
        result2 = _parse_result(
            [('checkpoint', self.cp.id), ('next_step', self.nstep.id)],
        )
        event2, mock_parse = self._apply(thread, result2)

        mock_parse.assert_called_once()
        self.assertEqual(event1.pk, event2.pk)
        # cp was already done -> not re-flipped, no duplicate change row.
        self.cp.refresh_from_db()
        self.nstep.refresh_from_db()
        self.assertEqual(self.cp.done_at, cp_done_at)
        self.assertIsNotNone(self.nstep.done_at)
        self.assertEqual(AppliedProgressChange.objects.count(), 2)
        self.assertEqual(event2.source_message_ts, '1700000050.000000')


@override_settings(LLM_API_KEY='sk-test', LLM_PROVIDER='anthropic')
class ReversalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='r@example.com', password='x', slack_user_id='U_R',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)

    def _thread(self, ts='1700000000.000900'):
        thread = SlackThread.objects.create(
            channel_id=CHANNEL, thread_ts=ts, slack_user_id='U_R',
            member=self.member, plan=self.plan, posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=thread, ts=ts, slack_user_id='U_R', text='done',
            posted_at=timezone.now(), is_root=True,
        )
        return thread

    def _apply(self, thread, completions):
        result = _parse_result(completions)
        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            return_value=result,
        ):
            return apply_thread_progress(thread)

    def test_event_undo_restores_changes_but_not_manual_completion(self):
        cp = Checkpoint.objects.create(week=self.week, description='cp')
        deliv = Deliverable.objects.create(plan=self.plan, description='d')
        # A third item completed MANUALLY before ingest.
        manual = NextStep.objects.create(plan=self.plan, description='manual')
        manual.done_at = timezone.now()
        manual.save(update_fields=['done_at'])
        manual_done = manual.done_at

        thread = self._thread()
        event = self._apply(
            thread, [('checkpoint', cp.id), ('deliverable', deliv.id)],
        )
        self.assertEqual(event.changes.count(), 2)

        reverse_event(event)

        cp.refresh_from_db()
        deliv.refresh_from_db()
        manual.refresh_from_db()
        self.assertIsNone(cp.done_at)
        self.assertIsNone(deliv.done_at)
        # The manual completion is untouched.
        self.assertEqual(manual.done_at, manual_done)
        self.assertFalse(
            IngestedProgressEvent.objects.filter(pk=event.pk).exists()
        )
        self.assertEqual(AppliedProgressChange.objects.count(), 0)

    def test_single_change_undo_leaves_other_change_applied(self):
        cp = Checkpoint.objects.create(week=self.week, description='cp')
        deliv = Deliverable.objects.create(plan=self.plan, description='d')
        thread = self._thread()
        event = self._apply(
            thread, [('checkpoint', cp.id), ('deliverable', deliv.id)],
        )
        cp_change = event.changes.get(checkpoint=cp)

        reverse_change(cp_change)

        cp.refresh_from_db()
        deliv.refresh_from_db()
        self.assertIsNone(cp.done_at)
        self.assertIsNotNone(deliv.done_at)
        # Event remains (still carries summary/blockers), one change left.
        self.assertTrue(
            IngestedProgressEvent.objects.filter(pk=event.pk).exists()
        )
        self.assertEqual(event.changes.count(), 1)
