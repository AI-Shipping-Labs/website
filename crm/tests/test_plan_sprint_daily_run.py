"""Daily-run integration for Phase 2 parse + auto-apply (issue #890).

Drives the real ``ingest_plan_sprints`` task with Slack stubbed at the
service boundary (as in Phase 1) and the LLM parse stubbed at the parse
boundary. Covers: the parse step rides the daily run and auto-applies; an
LLM-off run degrades to a pure Phase-1 capture run (no events); a per-thread
LLM failure is logged without failing the run or blocking other threads.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from crm.models import IngestedProgressEvent, SlackThread
from crm.services.plan_sprint_parse import (
    ParsedCompletion,
    PlanSprintParseResult,
)
from crm.tasks import ingest_plan_sprints
from integrations.services.llm import LLMError
from plans.models import Checkpoint, Plan, Sprint, Week

User = get_user_model()

CHANNEL = 'C_TEST_PLANSPRINTS'

SLACK_ON = dict(
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN='xoxb-test',
    SLACK_ENVIRONMENT='test',
    SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID=CHANNEL,
)
LLM_ON = dict(LLM_API_KEY='sk-test', LLM_PROVIDER='anthropic')
LLM_OFF = dict(LLM_API_KEY='')


def _msg(ts, user, text, thread_ts=None):
    m = {'ts': ts, 'user': user, 'text': text}
    if thread_ts:
        m['thread_ts'] = thread_ts
    return m


class FakeSlackService:
    def __init__(self, history, replies):
        self._history = history
        self._replies = replies

    def fetch_conversation_history(self, channel_id, oldest=None, limit=200):
        return list(self._history)

    def fetch_conversation_replies(self, channel_id, thread_ts, limit=200):
        return list(self._replies.get(thread_ts, []))

    def lookup_user_display_name(self, slack_user_id):
        return ''

    def get_message_permalink(self, channel_id, message_ts):
        return f'https://slack.example/{channel_id}/{message_ts}'


def _run_with(service):
    with patch(
        'crm.tasks.ingest_plan_sprints.SlackCommunityService',
        return_value=service,
    ):
        return ingest_plan_sprints()


class DailyRunBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@example.com', password='x', slack_user_id='U_MEMBER',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)
        cls.cp = Checkpoint.objects.create(week=cls.week, description='cp')


@override_settings(**{**SLACK_ON, **LLM_ON})
class DailyRunAutoApplyTests(DailyRunBase):
    def test_daily_run_parses_and_auto_applies(self):
        root = _msg('1700000000.000100', 'U_MEMBER', 'Finished cp')
        service = FakeSlackService(
            history=[root], replies={'1700000000.000100': [root]},
        )
        result = PlanSprintParseResult(
            completed_items=[
                ParsedCompletion(item_kind='checkpoint', item_id=self.cp.id),
            ],
            summary='Did the checkpoint.',
            blockers=[],
        )
        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            return_value=result,
        ):
            run = _run_with(service)

        self.assertEqual(run.status, 'success')
        self.cp.refresh_from_db()
        self.assertIsNotNone(self.cp.done_at)
        thread = SlackThread.objects.get(thread_ts='1700000000.000100')
        event = IngestedProgressEvent.objects.get(thread=thread)
        self.assertEqual(event.summary, 'Did the checkpoint.')
        self.assertEqual(event.changes.count(), 1)

    def test_per_thread_llm_failure_does_not_fail_run(self):
        root = _msg('1700000000.000200', 'U_MEMBER', 'Finished cp')
        service = FakeSlackService(
            history=[root], replies={'1700000000.000200': [root]},
        )
        with patch(
            'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
            side_effect=LLMError('boom'),
        ):
            run = _run_with(service)

        # The run still succeeds (capture happened); no event applied.
        self.assertEqual(run.status, 'success')
        self.assertTrue(
            SlackThread.objects.filter(thread_ts='1700000000.000200').exists()
        )
        self.assertEqual(IngestedProgressEvent.objects.count(), 0)
        self.cp.refresh_from_db()
        self.assertIsNone(self.cp.done_at)


@override_settings(**{**SLACK_ON, **LLM_OFF})
class DailyRunLLMOffTests(DailyRunBase):
    def test_llm_off_run_is_pure_capture_no_events(self):
        root = _msg('1700000000.000300', 'U_MEMBER', 'Finished cp')
        service = FakeSlackService(
            history=[root], replies={'1700000000.000300': [root]},
        )
        # No parse stub: the real parse callable should raise Unavailable
        # before any LLM call, and the run keeps going as a capture run.
        run = _run_with(service)

        self.assertEqual(run.status, 'success')
        self.assertTrue(
            SlackThread.objects.filter(thread_ts='1700000000.000300').exists()
        )
        self.assertEqual(IngestedProgressEvent.objects.count(), 0)
        self.cp.refresh_from_db()
        self.assertIsNone(self.cp.done_at)
