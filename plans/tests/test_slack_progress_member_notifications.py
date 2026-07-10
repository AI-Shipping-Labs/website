"""Member-facing Slack progress notification and undo tests (#1200)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
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
from crm.tasks.apply_plan_sprint_progress import apply_thread_progress
from notifications.models import Notification
from plans.models import (
    SPRINT_CADENCE_KIND_SLACK_PROGRESS,
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Sprint,
    SprintCadenceDeliveryLog,
    Week,
)

User = get_user_model()


def _parse_result(completions):
    return PlanSprintParseResult(
        completed_items=[
            ParsedCompletion(item_kind=kind, item_id=item_id, confidence=1.0)
            for kind, item_id in completions
        ],
        summary='Progress update',
        blockers=[],
    )


@tag('core')
@override_settings(LLM_API_KEY='sk-test', LLM_PROVIDER='anthropic')
class SlackProgressNotificationTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            email='slack-member@test.com',
            password='pw',
            slack_user_id='U_MEMBER',
        )
        self.sprint = Sprint.objects.create(
            name='Slack Sprint',
            slug='slack-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        self.plan = Plan.objects.create(
            member=self.member,
            sprint=self.sprint,
            shared_at=timezone.now(),
        )
        self.week = Week.objects.create(plan=self.plan, week_number=1)

    def _thread(self, ts='1700000000.000100'):
        thread = SlackThread.objects.create(
            channel_id='C_PLAN',
            thread_ts=ts,
            slack_user_id='U_MEMBER',
            member=self.member,
            plan=self.plan,
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=thread,
            ts=ts,
            slack_user_id='U_MEMBER',
            text='Finished things',
            posted_at=timezone.now(),
            is_root=True,
        )
        return thread

    def test_auto_apply_creates_one_member_notification_for_new_changes(self):
        cp = Checkpoint.objects.create(
            week=self.week,
            description='Draft prototype',
        )
        deliverable = Deliverable.objects.create(
            plan=self.plan,
            description='Record demo',
        )
        thread = self._thread()

        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            return_value=_parse_result([
                ('checkpoint', cp.pk),
                ('deliverable', deliverable.pk),
            ]),
        ):
            event = apply_thread_progress(thread)

        notification = Notification.objects.get(user=self.member)
        self.assertEqual(notification.notification_type, 'slack_progress')
        self.assertIn('2 items', notification.title)
        self.assertIn('Draft prototype', notification.body)
        self.assertIn('Record demo', notification.body)
        self.assertEqual(
            notification.url,
            (
                f'/sprints/slack-sprint/plan/{self.plan.pk}'
                f'?progress_event={event.pk}#slack-progress'
            ),
        )
        self.assertEqual(
            SprintCadenceDeliveryLog.objects.filter(
                kind=SPRINT_CADENCE_KIND_SLACK_PROGRESS,
                progress_event=event,
                source_message_ts='1700000000.000100',
            ).count(),
            1,
        )

        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            return_value=_parse_result([('checkpoint', cp.pk)]),
        ) as mock_parse:
            apply_thread_progress(thread)

        mock_parse.assert_not_called()
        self.assertEqual(
            Notification.objects.filter(
                user=self.member,
                notification_type='slack_progress',
            ).count(),
            1,
        )

    def test_already_done_parsed_items_do_not_create_member_notification(self):
        cp = Checkpoint.objects.create(
            week=self.week,
            description='Already done',
            done_at=timezone.now(),
        )
        thread = self._thread()

        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            return_value=_parse_result([('checkpoint', cp.pk)]),
        ):
            event = apply_thread_progress(thread)

        self.assertIsNotNone(event)
        self.assertEqual(AppliedProgressChange.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(SprintCadenceDeliveryLog.objects.count(), 0)


@tag('core')
class SlackProgressMemberUndoTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email='owner-progress@test.com',
            password='pw',
        )
        self.other = User.objects.create_user(
            email='other-progress@test.com',
            password='pw',
        )
        self.sprint = Sprint.objects.create(
            name='Undo Sprint',
            slug='undo-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        self.plan = Plan.objects.create(
            member=self.owner,
            sprint=self.sprint,
            shared_at=timezone.now(),
        )
        self.week = Week.objects.create(plan=self.plan, week_number=1)
        self.thread = SlackThread.objects.create(
            channel_id='C_PLAN',
            thread_ts='1700000000.000300',
            slack_user_id='U_OWNER',
            member=self.owner,
            plan=self.plan,
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=self.thread,
            ts='1700000000.000300',
            slack_user_id='U_OWNER',
            text='Done',
            posted_at=timezone.now(),
            is_root=True,
        )
        self.event = IngestedProgressEvent.objects.create(
            thread=self.thread,
            plan=self.plan,
            summary='Progress',
            source_message_ts='1700000000.000300',
        )
        now = timezone.now()
        self.cp = Checkpoint.objects.create(
            week=self.week,
            description='Auto checkpoint',
            done_at=now,
        )
        self.deliverable = Deliverable.objects.create(
            plan=self.plan,
            description='Auto deliverable',
            done_at=now,
        )
        self.manual = NextStep.objects.create(
            plan=self.plan,
            description='Manual completion',
            done_at=now,
        )
        AppliedProgressChange.objects.create(
            event=self.event,
            item_kind='checkpoint',
            checkpoint=self.cp,
            previous_done_at=None,
        )
        AppliedProgressChange.objects.create(
            event=self.event,
            item_kind='deliverable',
            deliverable=self.deliverable,
            previous_done_at=None,
        )

    def _plan_url(self):
        return (
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
            )
            + f'?progress_event={self.event.pk}#slack-progress'
        )

    def _undo_url(self):
        return reverse(
            'undo_slack_progress',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.plan.pk,
                'event_id': self.event.pk,
            },
        )

    def test_owner_plan_page_shows_callout_for_notification_target(self):
        self.client.force_login(self.owner)

        response = self.client.get(self._plan_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="slack-progress-callout"')
        self.assertContains(response, 'Auto checkpoint')
        self.assertContains(response, 'Auto deliverable')
        self.assertContains(response, 'Undo these updates')

    def test_owner_can_undo_auto_changes_without_touching_manual_completion(self):
        manual_done = self.manual.done_at
        self.client.force_login(self.owner)

        response = self.client.post(self._undo_url())

        self.assertEqual(response.status_code, 302)
        self.cp.refresh_from_db()
        self.deliverable.refresh_from_db()
        self.manual.refresh_from_db()
        self.assertIsNone(self.cp.done_at)
        self.assertIsNone(self.deliverable.done_at)
        self.assertEqual(self.manual.done_at, manual_done)
        self.assertFalse(
            IngestedProgressEvent.objects.filter(pk=self.event.pk).exists()
        )

    def test_non_owner_cannot_view_or_undo_owner_progress_event(self):
        self.client.force_login(self.other)

        view_response = self.client.get(self._plan_url())
        undo_response = self.client.post(self._undo_url())

        self.assertEqual(view_response.status_code, 404)
        self.assertEqual(undo_response.status_code, 404)
        self.cp.refresh_from_db()
        self.assertIsNotNone(self.cp.done_at)

    def test_get_to_undo_is_rejected(self):
        self.client.force_login(self.owner)

        response = self.client.get(self._undo_url())

        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            IngestedProgressEvent.objects.filter(pk=self.event.pk).exists()
        )
