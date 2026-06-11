"""Force-reparse of already-watermarked `#plan-sprints` threads (issue #927).

Covers the ``backfill_plan_sprints --reparse`` operator path: re-reading
``conversations.replies`` for EXISTING persisted threads in the ``--since``
window, appending genuinely-new reply rows, recomputing ``reply_count``, and
re-running the Phase 2 parse + auto-apply. Slack is mocked at the service
boundary and the LLM at the parse boundary — no live calls.

Also guards the impact claim from the issue: on a first-capture run the Phase 2
parse input includes the reply text (replies reach the parser on first capture).
"""

import datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from crm.models import (
    AppliedProgressChange,
    IngestedProgressEvent,
    SlackChannelIngest,
    SlackMessage,
    SlackThread,
)
from crm.services.plan_sprint_parse import (
    ParsedCompletion,
    PlanSprintParseResult,
)
from crm.tasks import ingest_plan_sprints, reparse_plan_sprints
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

# A thread ts well inside any "since 2025-12-01" window.
ROOT_TS = '1768435200.000100'  # 2026-01-15 00:00:00 UTC


def _msg(ts, user, text, thread_ts=None):
    m = {'ts': ts, 'user': user, 'text': text}
    if thread_ts:
        m['thread_ts'] = thread_ts
    return m


class FakeSlackService:
    """Service stub. ``replies`` maps thread_ts -> full message list."""

    def __init__(self, history, replies, display=None):
        self._history = history
        self._replies = replies
        self._display = display or {}

    def fetch_conversation_history(self, channel_id, oldest=None, limit=200):
        return list(self._history)

    def fetch_conversation_replies(self, channel_id, thread_ts, limit=200):
        return list(self._replies.get(thread_ts, []))

    def lookup_user_display_name(self, slack_user_id):
        return self._display.get(slack_user_id, '')

    def get_message_permalink(self, channel_id, message_ts):
        return f'https://slack.example/{channel_id}/{message_ts}'


def _patch_service(service):
    return patch(
        'crm.tasks.ingest_plan_sprints.SlackCommunityService',
        return_value=service,
    )


def _patch_parse(result_or_side_effect):
    kwargs = {}
    if isinstance(result_or_side_effect, PlanSprintParseResult):
        kwargs['return_value'] = result_or_side_effect
    else:
        kwargs['side_effect'] = result_or_side_effect
    return patch(
        'crm.tasks.apply_plan_sprint_progress.parse_plan_sprint_thread',
        **kwargs,
    )


class ReparseBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@example.com', password='x', slack_user_id='U_MEMBER',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 1, 1),
            status='active',
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)
        cls.cp = Checkpoint.objects.create(week=cls.week, description='cp')

    def _existing_thread(self, ts=ROOT_TS, root_text='Root update'):
        """Persist a thread (root only) the way an earlier run would have."""
        thread = SlackThread.objects.create(
            channel_id=CHANNEL, thread_ts=ts, slack_user_id='U_MEMBER',
            member=self.member, plan=self.plan,
            posted_at=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            reply_count=0,
        )
        SlackMessage.objects.create(
            thread=thread, ts=ts, slack_user_id='U_MEMBER', text=root_text,
            posted_at=thread.posted_at, is_root=True,
        )
        return thread


@override_settings(**{**SLACK_ON, **LLM_ON})
class ReparseRereadsRepliesTests(ReparseBase):
    def test_reparse_appends_new_replies_to_existing_thread(self):
        thread = self._existing_thread()
        # The mocked replies endpoint now returns 2 replies not yet persisted.
        root = _msg(ROOT_TS, 'U_MEMBER', 'Root update')
        r1 = _msg('1768435201.000000', 'U_B', 'Reply 1', thread_ts=ROOT_TS)
        r2 = _msg('1768435202.000000', 'U_C', 'Reply 2', thread_ts=ROOT_TS)
        service = FakeSlackService(history=[], replies={ROOT_TS: [root, r1, r2]})
        result = PlanSprintParseResult(
            completed_items=[
                ParsedCompletion(item_kind='checkpoint', item_id=self.cp.id),
            ],
            summary='Did the checkpoint.',
            blockers=[],
        )
        with _patch_service(service), _patch_parse(result):
            run = reparse_plan_sprints(since=datetime.date(2025, 12, 1))

        self.assertEqual(run.status, 'success')
        thread.refresh_from_db()
        # 2 new reply rows under the SAME thread (no duplicate thread).
        self.assertEqual(thread.messages.count(), 3)
        self.assertEqual(thread.reply_count, 2)
        self.assertEqual(SlackThread.objects.filter(thread_ts=ROOT_TS).count(), 1)
        self.assertEqual(run.replies_added, 2)
        # Phase 2 re-ran for the thread.
        event = IngestedProgressEvent.objects.get(thread=thread)
        self.assertEqual(event.summary, 'Did the checkpoint.')
        self.cp.refresh_from_db()
        self.assertIsNotNone(self.cp.done_at)

    def test_reparse_is_idempotent_on_complete_thread(self):
        thread = self._existing_thread()
        root = _msg(ROOT_TS, 'U_MEMBER', 'Root update')
        r1 = _msg('1768435201.000000', 'U_B', 'Reply 1', thread_ts=ROOT_TS)
        service = FakeSlackService(history=[], replies={ROOT_TS: [root, r1]})
        result = PlanSprintParseResult(
            completed_items=[
                ParsedCompletion(item_kind='checkpoint', item_id=self.cp.id),
            ],
            summary='Did the checkpoint.',
            blockers=[],
        )
        # First reparse: appends the reply + applies the checkpoint.
        with _patch_service(service), _patch_parse(result):
            reparse_plan_sprints(since=datetime.date(2025, 12, 1))

        messages_after_first = SlackMessage.objects.count()
        changes_after_first = AppliedProgressChange.objects.count()
        self.assertEqual(changes_after_first, 1)

        # Second reparse over the identical replies: 0 new rows, 0 new changes.
        # The parse must NOT be called again (watermark guard short-circuits).
        with _patch_service(service), _patch_parse(result) as parse_mock:
            run2 = reparse_plan_sprints(since=datetime.date(2025, 12, 1))

        self.assertEqual(SlackMessage.objects.count(), messages_after_first)
        self.assertEqual(AppliedProgressChange.objects.count(), changes_after_first)
        self.assertEqual(run2.replies_added, 0)
        parse_mock.assert_not_called()
        thread.refresh_from_db()
        self.assertEqual(thread.reply_count, 1)

    def test_reparse_dry_run_persists_nothing(self):
        self._existing_thread()
        root = _msg(ROOT_TS, 'U_MEMBER', 'Root update')
        r1 = _msg('1768435201.000000', 'U_B', 'Reply 1', thread_ts=ROOT_TS)
        service = FakeSlackService(history=[], replies={ROOT_TS: [root, r1]})
        result = PlanSprintParseResult(
            completed_items=[
                ParsedCompletion(item_kind='checkpoint', item_id=self.cp.id),
            ],
            summary='Did the checkpoint.',
            blockers=[],
        )
        messages_before = SlackMessage.objects.count()
        ingest_before = SlackChannelIngest.objects.count()

        with _patch_service(service), _patch_parse(result):
            run = reparse_plan_sprints(since=datetime.date(2025, 12, 1), dry_run=True)

        # The run reports what it WOULD add...
        self.assertEqual(run.replies_added, 1)
        # ...but nothing is committed: no new reply, no ingest row, no event.
        self.assertEqual(SlackMessage.objects.count(), messages_before)
        self.assertEqual(SlackChannelIngest.objects.count(), ingest_before)
        self.assertEqual(IngestedProgressEvent.objects.count(), 0)
        self.cp.refresh_from_db()
        self.assertIsNone(self.cp.done_at)


@override_settings(**{**SLACK_ON, **LLM_ON})
class ReparseCommandTests(ReparseBase):
    def _call(self, service, parse_result, *args):
        out = StringIO()
        with _patch_service(service), _patch_parse(parse_result):
            call_command('backfill_plan_sprints', *args, stdout=out)
        return out.getvalue()

    def test_reparse_commit_persists_and_summarizes(self):
        self._existing_thread()
        root = _msg(ROOT_TS, 'U_MEMBER', 'Root update')
        r1 = _msg('1768435201.000000', 'U_B', 'Reply 1', thread_ts=ROOT_TS)
        service = FakeSlackService(history=[], replies={ROOT_TS: [root, r1]})
        result = PlanSprintParseResult(
            completed_items=[], summary='s', blockers=[],
        )
        out = self._call(
            service, result, '--since', '2025-12-01', '--reparse', '--commit',
        )
        self.assertIn('Re-parse complete', out)
        self.assertIn('1 threads re-read', out)
        self.assertIn('1 replies added', out)
        self.assertIn('1 members matched', out)
        self.assertEqual(SlackMessage.objects.filter(ts='1768435201.000000').count(), 1)

    def test_reparse_dry_run_is_default_and_persists_nothing(self):
        self._existing_thread()
        root = _msg(ROOT_TS, 'U_MEMBER', 'Root update')
        r1 = _msg('1768435201.000000', 'U_B', 'Reply 1', thread_ts=ROOT_TS)
        service = FakeSlackService(history=[], replies={ROOT_TS: [root, r1]})
        result = PlanSprintParseResult(
            completed_items=[], summary='s', blockers=[],
        )
        out = self._call(service, result, '--since', '2025-12-01', '--reparse')
        self.assertIn('DRY-RUN', out)
        self.assertEqual(SlackMessage.objects.filter(ts='1768435201.000000').count(), 0)


@override_settings(**{**SLACK_ON, **LLM_ON})
class FirstCaptureParseSeesRepliesTests(ReparseBase):
    """Regression guard for the issue's impact claim: on a first-capture run the
    Phase 2 parse input includes reply text, not just the root."""

    def test_parse_input_includes_reply_text_on_first_capture(self):
        root = _msg('1768435300.000000', 'U_MEMBER', 'Root with no signal')
        reply = _msg(
            '1768435301.000000', 'U_MEMBER',
            'Actually finished the checkpoint', thread_ts='1768435300.000000',
        )
        service = FakeSlackService(
            history=[root],
            replies={'1768435300.000000': [root, reply]},
        )
        captured = {}

        def fake_parse(parse_input):
            captured['input'] = parse_input
            return PlanSprintParseResult(
                completed_items=[], summary='ok', blockers=[],
            )

        with _patch_service(service), _patch_parse(fake_parse):
            run = ingest_plan_sprints(since=datetime.date(2025, 12, 1))

        self.assertEqual(run.status, 'success')
        texts = [text for (_author, _ts, text) in captured['input'].messages]
        self.assertIn('Actually finished the checkpoint', texts)
