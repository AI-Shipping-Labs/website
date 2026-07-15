"""Tests for `#plan-sprints` ingest (issue #889, Phase 1).

Slack is mocked at the service boundary — no live API calls. We patch
``SlackCommunityService`` so the ingest task drives off fixture
payloads exactly as it would off real ``conversations.history`` /
``conversations.replies`` responses.
"""

import datetime
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import EmailAlias
from community.services.slack import SlackAPIError
from crm.models import CRMRecord, SlackChannelIngest, SlackMessage, SlackThread
from crm.tasks import ingest_plan_sprints
from plans.models import InterviewNote, Plan, Sprint

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

    def __init__(self, history, replies, display=None, profiles=None):
        self._history = history
        self._replies = replies
        self._display = display or {}
        self._profiles = profiles or {}
        self.history_calls = 0
        self.reply_calls = []

    def fetch_conversation_history(self, channel_id, oldest=None, limit=200):
        self.history_calls += 1
        return list(self._history)

    def fetch_conversation_replies(self, channel_id, thread_ts, limit=200):
        self.reply_calls.append(thread_ts)
        return list(self._replies.get(thread_ts, []))

    def lookup_user_profile_by_id(self, slack_user_id):
        return self._profiles.get(slack_user_id)

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
        self.assertIsNotNone(thread.interview_note)
        self.assertEqual(thread.interview_note.member, self.member)
        self.assertEqual(thread.interview_note.plan, self.plan)
        self.assertEqual(thread.interview_note.visibility, 'internal')
        self.assertEqual(thread.interview_note.source_type, 'slack')
        self.assertEqual(
            thread.interview_note.tags,
            ['slack', 'plan-sprints', 'sprint:active-sprint'],
        )
        self.assertEqual(
            thread.interview_note.source_metadata['thread_ts'],
            '1700000000.000100',
        )
        self.assertTrue(CRMRecord.objects.filter(user=self.member).exists())

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
        self.assertIsNone(thread.interview_note)

    def test_profile_email_alias_resolves_canonical_member(self):
        self.member.slack_user_id = ''
        self.member.save(update_fields=['slack_user_id'])
        EmailAlias.objects.create(
            user=self.member,
            email='member-alias@example.com',
            source=EmailAlias.SOURCE_MANUAL,
        )
        root = _msg('1700000000.000400', 'U_ALIAS', 'Alias update')
        service = FakeSlackService(
            history=[root],
            replies={'1700000000.000400': [root]},
            profiles={'U_ALIAS': {'email': 'MEMBER-ALIAS@example.com'}},
        )

        _run_with(service)

        thread = SlackThread.objects.get(thread_ts='1700000000.000400')
        self.assertEqual(thread.member, self.member)
        self.member.refresh_from_db()
        self.assertEqual(self.member.slack_user_id, 'U_ALIAS')


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
        self.assertEqual(InterviewNote.objects.count(), 1)
        note = InterviewNote.objects.get()
        self.assertEqual(thread.interview_note, note)
        self.assertIn('Reply 2', note.body)
        self.assertEqual(note.source_metadata['latest_message_ts'], '1700000102.000000')
        # No duplicate thread.
        self.assertEqual(
            SlackThread.objects.filter(thread_ts='1700000100.000000').count(), 1
        )

    def test_daily_run_revisits_old_root_when_history_has_only_new_roots(self):
        root_ts = f'{timezone.now().timestamp() - 60:.6f}'
        reply1_ts = f'{timezone.now().timestamp() - 30:.6f}'
        reply2_ts = f'{timezone.now().timestamp():.6f}'
        root = _msg(root_ts, 'U_A', 'Yesterday root')
        r1 = _msg(reply1_ts, 'U_B', 'Yesterday reply', thread_ts=root_ts)
        _run_with(FakeSlackService(
            history=[root], replies={root_ts: [root, r1]},
        ))

        # The forward-watermarked history call returns NO old root on day 2.
        r2 = _msg(reply2_ts, 'U_C', 'Today late reply', thread_ts=root_ts)
        day2 = FakeSlackService(
            history=[], replies={root_ts: [root, r1, r2]},
        )
        run2 = _run_with(day2)

        thread = SlackThread.objects.get(thread_ts=root_ts)
        self.assertEqual(thread.reply_count, 2)
        self.assertEqual(thread.messages.count(), 3)
        self.assertEqual(run2.replies_added, 1)
        self.assertEqual(run2.known_threads_checked, 1)
        self.assertEqual(day2.reply_calls, [root_ts])

    def test_reply_failure_is_terminal_and_next_run_retries_same_watermark(self):
        root_ts = f'{timezone.now().timestamp():.6f}'
        root = _msg(root_ts, 'U_A', 'Root')

        class FailingReplies(FakeSlackService):
            def fetch_conversation_replies(self, channel_id, thread_ts, limit=200):
                raise SlackAPIError('reply denied', error_code='missing_scope')

        failed = _run_with(FailingReplies(history=[root], replies={}))
        self.assertEqual(failed.status, 'error')
        self.assertEqual(failed.latest_ts, failed.oldest_ts)
        self.assertIsNotNone(failed.finished_at)
        self.assertIsNone(failed.lease_expires_at)
        self.assertFalse(SlackThread.objects.filter(thread_ts=root_ts).exists())

        retry = _run_with(FakeSlackService(
            history=[root], replies={root_ts: [root]},
        ))
        self.assertEqual(retry.status, 'success')
        self.assertTrue(SlackThread.objects.filter(thread_ts=root_ts).exists())

    def test_privacy_erased_root_is_not_rehydrated_from_slack(self):
        root_ts = f'{timezone.now().timestamp():.6f}'
        SlackThread.objects.create(
            channel_id=CHANNEL,
            thread_ts=root_ts,
            posted_at=timezone.now(),
            privacy_erased=True,
        )
        root = _msg(root_ts, 'U_DELETED', 'must stay erased')
        service = FakeSlackService(
            history=[root], replies={root_ts: [root]},
        )

        run = _run_with(service)

        tombstone = SlackThread.objects.get(thread_ts=root_ts)
        self.assertTrue(tombstone.privacy_erased)
        self.assertEqual(tombstone.messages.count(), 0)
        self.assertEqual(service.reply_calls, [])
        self.assertEqual(run.threads_persisted, 0)

    @override_settings(PLAN_SPRINTS_THREAD_REFRESH_DAYS=1)
    def test_active_sprint_thread_is_revisited_beyond_recent_window(self):
        sprint = Sprint.objects.create(
            name='Still active', slug='still-active',
            start_date=datetime.date(2020, 1, 1), status='active',
        )
        Plan.objects.create(member=self.member, sprint=sprint)
        root_ts = f'{(timezone.now() - timezone.timedelta(days=10)).timestamp():.6f}'
        reply_ts = f'{timezone.now().timestamp():.6f}'
        root = _msg(root_ts, 'U_A', 'Old active root')
        _run_with(FakeSlackService(history=[root], replies={root_ts: [root]}))

        reply = _msg(reply_ts, 'U_B', 'Late active reply', thread_ts=root_ts)
        run = _run_with(FakeSlackService(
            history=[], replies={root_ts: [root, reply]},
        ))

        self.assertEqual(run.known_threads_checked, 1)
        self.assertEqual(run.replies_added, 1)

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

    @patch('community.services.slack.requests.post')
    def test_fetch_replies_uses_user_token_and_follows_cursor(self, mock_post):
        from community.services.slack import SlackCommunityService

        pages = [
            {'ok': True, 'messages': [{'ts': '1.0'}],
             'response_metadata': {'next_cursor': 'NEXT'}},
            {'ok': True, 'messages': [{'ts': '2.0'}],
             'response_metadata': {'next_cursor': ''}},
        ]

        class Response:
            status_code = 200

            def json(self):
                return pages.pop(0)

        mock_post.side_effect = lambda *args, **kwargs: Response()
        service = SlackCommunityService(
            bot_token='xoxb-bot', reply_user_token='xoxp-reader',
        )

        messages = service.fetch_conversation_replies('C1', '1.0')

        self.assertEqual([message['ts'] for message in messages], ['1.0', '2.0'])
        self.assertEqual(mock_post.call_count, 2)
        for call in mock_post.call_args_list:
            self.assertEqual(
                call.kwargs['headers']['Authorization'], 'Bearer xoxp-reader',
            )
        self.assertEqual(mock_post.call_args_list[1].kwargs['json']['cursor'], 'NEXT')

    def test_fetch_replies_without_user_token_fails_visibly(self):
        from community.services.slack import SlackCommunityService

        service = SlackCommunityService(
            bot_token='xoxb-bot', reply_user_token='',
        )
        with self.assertRaisesRegex(SlackAPIError, 'user token is required'):
            service.fetch_conversation_replies('C1', '1.0')

    @patch('community.services.slack.requests.post')
    def test_network_and_invalid_json_are_normalized(self, mock_post):
        from community.services.slack import SlackCommunityService

        service = SlackCommunityService(bot_token='xoxb-bot')
        mock_post.side_effect = requests.Timeout('slow')
        with self.assertRaises(SlackAPIError) as network:
            service.fetch_conversation_history('C1')
        self.assertEqual(network.exception.error_code, 'network_error')

        class InvalidJsonResponse:
            status_code = 200

            def json(self):
                raise ValueError('bad json')

        mock_post.side_effect = None
        mock_post.return_value = InvalidJsonResponse()
        with self.assertRaises(SlackAPIError) as invalid:
            service.fetch_conversation_history('C1')
        self.assertEqual(invalid.exception.error_code, 'invalid_json')


@override_settings(**SLACK_ON)
class IngestLeaseTests(TransactionTestCase):
    def test_active_lease_prevents_second_provider_run(self):
        from crm.tasks.ingest_plan_sprints import _acquire_run

        first = _acquire_run(CHANNEL, '1.0')
        self.assertIsNotNone(first)
        second = _acquire_run(CHANNEL, '1.0')
        self.assertIsNone(second)

        service = FakeSlackService(history=[], replies={})
        returned = _run_with(service)
        self.assertEqual(returned.pk, first.pk)
        self.assertEqual(service.history_calls, 0)
        self.assertEqual(
            SlackChannelIngest.objects.filter(status='running').count(), 1,
        )

    def test_expired_lease_is_terminalized_before_replacement(self):
        from crm.tasks.ingest_plan_sprints import _acquire_run

        expired = SlackChannelIngest.objects.create(
            channel_id=CHANNEL,
            status='running',
            lease_expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        replacement = _acquire_run(CHANNEL, '2.0')

        expired.refresh_from_db()
        self.assertEqual(expired.status, 'error')
        self.assertIn('lease expired', expired.error.lower())
        self.assertIsNotNone(expired.finished_at)
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.status, 'running')
