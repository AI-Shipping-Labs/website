"""Tests for `#plan-sprints` ingest (issue #889, Phase 1).

Slack is mocked at the service boundary — no live API calls. We patch
``SlackCommunityService`` so the ingest task drives off fixture
payloads exactly as it would off real ``conversations.history`` /
``conversations.replies`` responses.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from crm.models import SlackChannelIngest, SlackMessage, SlackThread
from crm.tasks import ingest_plan_sprints
from plans.models import Plan, Sprint

User = get_user_model()

CHANNEL = 'C_TEST_PLANSPRINTS'

SLACK_ON = dict(
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN='xoxb-test',
    SLACK_ENVIRONMENT='test',
    SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID=CHANNEL,
)


def _msg(ts, user, text, thread_ts=None):
    """Build a Slack message dict like the API returns."""
    m = {'ts': ts, 'user': user, 'text': text}
    if thread_ts:
        m['thread_ts'] = thread_ts
    return m


class FakeSlackService:
    """Stand-in for SlackCommunityService driven by fixture payloads.

    ``history`` is the list of top-level messages; ``replies`` maps a
    ``thread_ts`` to that thread's full message list (root + replies).
    """

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
        return f'https://slack.example/archives/{channel_id}/p{message_ts}'


def _run_with(service):
    with patch(
        'crm.tasks.ingest_plan_sprints.SlackCommunityService',
        return_value=service,
    ):
        return ingest_plan_sprints()


@override_settings(**SLACK_ON)
class IngestMatchingTests(TestCase):
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

    def test_matched_member_with_active_plan_links_both(self):
        root = _msg('1700000000.000100', 'U_MEMBER', 'Done with week 1')
        service = FakeSlackService(
            history=[root],
            replies={'1700000000.000100': [root]},
            display={'U_MEMBER': 'Member One'},
        )
        run = _run_with(service)

        thread = SlackThread.objects.get(thread_ts='1700000000.000100')
        self.assertEqual(thread.member, self.member)
        self.assertEqual(thread.plan, self.plan)
        self.assertEqual(run.members_matched, 1)

    def test_member_without_active_plan_sets_member_plan_null(self):
        self.sprint.status = 'completed'
        self.sprint.save(update_fields=['status'])

        root = _msg('1700000000.000200', 'U_MEMBER', 'Update with no active plan')
        service = FakeSlackService(
            history=[root], replies={'1700000000.000200': [root]},
        )
        _run_with(service)

        thread = SlackThread.objects.get(thread_ts='1700000000.000200')
        self.assertEqual(thread.member, self.member)
        self.assertIsNone(thread.plan)

    def test_unmatched_author_persists_thread_with_null_member(self):
        root = _msg('1700000000.000300', 'U_STRANGER', 'Who am I')
        service = FakeSlackService(
            history=[root], replies={'1700000000.000300': [root]},
        )
        _run_with(service)

        thread = SlackThread.objects.get(thread_ts='1700000000.000300')
        self.assertIsNone(thread.member)
        self.assertIsNone(thread.plan)
        self.assertEqual(thread.messages.count(), 1)


@override_settings(**SLACK_ON)
class IngestThreadPersistenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='m@example.com', password='x', slack_user_id='U_A',
        )

    def test_full_thread_persists_root_and_replies_in_order(self):
        root = _msg('1700000001.000000', 'U_A', 'Root message')
        r1 = _msg('1700000002.000000', 'U_B', 'First reply', thread_ts='1700000001.000000')
        r2 = _msg('1700000003.000000', 'U_A', 'Second reply', thread_ts='1700000001.000000')
        service = FakeSlackService(
            history=[root],
            replies={'1700000001.000000': [root, r1, r2]},
            display={'U_A': 'Alice', 'U_B': 'Bob'},
        )
        _run_with(service)

        thread = SlackThread.objects.get(thread_ts='1700000001.000000')
        messages = list(thread.messages.all())
        self.assertEqual(len(messages), 3)
        self.assertEqual(
            [m.ts for m in messages],
            ['1700000001.000000', '1700000002.000000', '1700000003.000000'],
        )
        self.assertTrue(messages[0].is_root)
        self.assertFalse(messages[1].is_root)
        self.assertEqual(messages[0].author_display, 'Alice')
        self.assertEqual(messages[1].author_display, 'Bob')
        self.assertEqual(messages[1].text, 'First reply')
        self.assertEqual(thread.reply_count, 2)

    def test_bot_and_system_messages_are_not_persisted(self):
        root = _msg('1700000010.000000', 'U_A', 'Real update')
        join_notice = {
            'ts': '1700000011.000000', 'subtype': 'channel_join',
            'user': 'U_A', 'text': 'has joined the channel',
        }
        bot_msg = {'ts': '1700000012.000000', 'bot_id': 'B1', 'text': 'Bot says hi'}
        service = FakeSlackService(
            history=[root, join_notice, bot_msg],
            replies={'1700000010.000000': [root]},
        )
        _run_with(service)

        self.assertEqual(SlackThread.objects.count(), 1)
        self.assertFalse(
            SlackMessage.objects.filter(ts='1700000011.000000').exists()
        )
        self.assertFalse(
            SlackMessage.objects.filter(ts='1700000012.000000').exists()
        )


@override_settings(**SLACK_ON)
class IngestIncrementalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='inc@example.com', password='x', slack_user_id='U_A',
        )

    def test_new_reply_on_known_thread_is_appended_on_later_run(self):
        root = _msg('1700000100.000000', 'U_A', 'Root')
        r1 = _msg('1700000101.000000', 'U_B', 'Reply 1', thread_ts='1700000100.000000')

        # Day 1: root + 1 reply.
        day1 = FakeSlackService(
            history=[root], replies={'1700000100.000000': [root, r1]},
        )
        _run_with(day1)

        thread = SlackThread.objects.get(thread_ts='1700000100.000000')
        self.assertEqual(thread.reply_count, 1)

        # Day 2: same thread now has a second reply.
        r2 = _msg('1700000102.000000', 'U_C', 'Reply 2', thread_ts='1700000100.000000')
        day2 = FakeSlackService(
            history=[root], replies={'1700000100.000000': [root, r1, r2]},
        )
        run2 = _run_with(day2)

        thread.refresh_from_db()
        self.assertEqual(thread.messages.count(), 3)
        self.assertEqual(thread.reply_count, 2)
        self.assertEqual(thread.last_seen_ingest, run2)
        self.assertEqual(run2.replies_added, 1)
        # No duplicate thread.
        self.assertEqual(
            SlackThread.objects.filter(thread_ts='1700000100.000000').count(), 1
        )

    def test_rerun_with_no_new_content_adds_no_rows(self):
        root = _msg('1700000200.000000', 'U_A', 'Root')
        r1 = _msg('1700000201.000000', 'U_B', 'Reply', thread_ts='1700000200.000000')
        service = FakeSlackService(
            history=[root], replies={'1700000200.000000': [root, r1]},
        )
        _run_with(service)
        threads_before = SlackThread.objects.count()
        messages_before = SlackMessage.objects.count()

        # Re-run over the exact same window/payload.
        run2 = _run_with(service)

        self.assertEqual(SlackThread.objects.count(), threads_before)
        self.assertEqual(SlackMessage.objects.count(), messages_before)
        self.assertEqual(run2.replies_added, 0)
        self.assertEqual(run2.threads_persisted, 0)


@override_settings(**SLACK_ON)
class IngestFirstCaptureRepliesCountTests(TestCase):
    """Regression for issue #927: first-capture replies must be counted.

    Before the fix, ``replies_added`` discarded ``new_replies`` for any thread
    created this run, so a first/backfill run always reported 0 even with many
    replies persisted.
    """

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='fc@example.com', password='x', slack_user_id='U_A',
        )

    def test_first_capture_counts_replies_not_root(self):
        root = _msg('1700000400.000000', 'U_A', 'Root')
        r1 = _msg('1700000401.000000', 'U_B', 'Reply 1', thread_ts='1700000400.000000')
        r2 = _msg('1700000402.000000', 'U_C', 'Reply 2', thread_ts='1700000400.000000')
        service = FakeSlackService(
            history=[root],
            replies={'1700000400.000000': [root, r1, r2]},
        )
        run = _run_with(service)

        thread = SlackThread.objects.get(thread_ts='1700000400.000000')
        self.assertEqual(thread.messages.count(), 3)
        self.assertEqual(thread.reply_count, 2)
        # The bug: this used to be 0 on a first-capture run.
        self.assertEqual(run.replies_added, 2)
        self.assertEqual(run.threads_persisted, 1)

    def test_multi_thread_first_run_sums_all_replies(self):
        a = _msg('1700000500.000000', 'U_A', 'A root')
        a1 = _msg('1700000501.000000', 'U_B', 'A r1', thread_ts='1700000500.000000')
        a2 = _msg('1700000502.000000', 'U_C', 'A r2', thread_ts='1700000500.000000')
        b = _msg('1700000510.000000', 'U_A', 'B root')  # 0 replies
        c = _msg('1700000520.000000', 'U_A', 'C root')
        c1 = _msg('1700000521.000000', 'U_B', 'C r1', thread_ts='1700000520.000000')
        c2 = _msg('1700000522.000000', 'U_C', 'C r2', thread_ts='1700000520.000000')
        c3 = _msg('1700000523.000000', 'U_D', 'C r3', thread_ts='1700000520.000000')
        service = FakeSlackService(
            history=[a, b, c],
            replies={
                '1700000500.000000': [a, a1, a2],
                '1700000510.000000': [b],
                '1700000520.000000': [c, c1, c2, c3],
            },
        )
        run = _run_with(service)

        self.assertEqual(run.threads_persisted, 3)
        self.assertEqual(run.replies_added, 5)


@override_settings(**SLACK_ON)
class IngestPaginationTests(TestCase):
    def test_history_pages_are_all_ingested(self):
        # Two top-level messages that the real service would have returned
        # across two pages; the wrapper already flattens pagination, so we
        # assert the task ingests every message the service hands back.
        m1 = _msg('1700000300.000000', 'U_X', 'Page 1 thread')
        m2 = _msg('1700000301.000000', 'U_Y', 'Page 2 thread')
        service = FakeSlackService(
            history=[m1, m2],
            replies={
                '1700000300.000000': [m1],
                '1700000301.000000': [m2],
            },
        )
        run = _run_with(service)

        self.assertEqual(SlackThread.objects.count(), 2)
        self.assertEqual(run.threads_persisted, 2)


class IngestDisabledTests(TestCase):
    @override_settings(SLACK_ENABLED=False)
    def test_noop_when_slack_disabled(self):
        result = ingest_plan_sprints()
        self.assertIsNone(result)
        self.assertEqual(SlackThread.objects.count(), 0)
        self.assertEqual(SlackChannelIngest.objects.count(), 0)

    @override_settings(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN='xoxb-test',
        SLACK_ENVIRONMENT='test',
        SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID='',
    )
    def test_noop_when_channel_unset(self):
        result = ingest_plan_sprints()
        self.assertIsNone(result)
        self.assertEqual(SlackThread.objects.count(), 0)
        self.assertEqual(SlackChannelIngest.objects.count(), 0)


@override_settings(**SLACK_ON)
class HistoryPaginationWrapperTests(TestCase):
    """The service wrapper itself follows ``next_cursor`` (boundary test)."""

    @patch('community.services.slack.requests.post')
    def test_fetch_history_follows_cursor(self, mock_post):
        from community.services.slack import SlackCommunityService

        page1 = {
            'ok': True,
            'messages': [{'ts': '1.0', 'user': 'U', 'text': 'a'}],
            'response_metadata': {'next_cursor': 'CURSOR2'},
        }
        page2 = {
            'ok': True,
            'messages': [{'ts': '2.0', 'user': 'U', 'text': 'b'}],
            'response_metadata': {'next_cursor': ''},
        }
        responses = [page1, page2]

        def side_effect(*args, **kwargs):
            class R:
                status_code = 200

                def json(self_inner):
                    return responses.pop(0)
            return R()

        mock_post.side_effect = side_effect

        service = SlackCommunityService()
        messages = service.fetch_conversation_history('C1')

        self.assertEqual(len(messages), 2)
        self.assertEqual(mock_post.call_count, 2)
