"""Retroactive `#plan-sprints` backfill (issue #904).

Covers the ``oldest_ts`` / ``since`` override on ``ingest_plan_sprints``,
the dry-run rollback, idempotent re-runs, and the
``backfill_plan_sprints`` management command. Slack is mocked at the
service boundary — no live API calls.
"""

import datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from crm.models import SlackChannelIngest, SlackThread
from crm.tasks import ingest_plan_sprints

User = get_user_model()

CHANNEL = 'C_TEST_PLANSPRINTS'

SLACK_ON = dict(
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN='xoxb-test',
    SLACK_ENVIRONMENT='test',
    SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID=CHANNEL,
)


def _msg(ts, user, text, thread_ts=None):
    m = {'ts': ts, 'user': user, 'text': text}
    if thread_ts:
        m['thread_ts'] = thread_ts
    return m


class RecordingSlackService:
    """Fake service that records the ``oldest`` it was asked to read from.

    ``history`` is filtered to messages with ``ts >= oldest`` so a test can
    assert that an older ``oldest`` actually pulls older threads.
    """

    def __init__(self, history, replies, display=None):
        self._history = history
        self._replies = replies
        self._display = display or {}
        self.oldest_seen = None

    def fetch_conversation_history(self, channel_id, oldest=None, limit=200):
        self.oldest_seen = oldest
        if oldest is None:
            return list(self._history)
        return [m for m in self._history if m['ts'] >= oldest]

    def fetch_conversation_replies(self, channel_id, thread_ts, limit=200):
        return list(self._replies.get(thread_ts, []))

    def lookup_user_display_name(self, slack_user_id):
        return self._display.get(slack_user_id, '')

    def get_message_permalink(self, channel_id, message_ts):
        return f'https://slack.example/archives/{channel_id}/p{message_ts}'


def _run_with(service, **kwargs):
    with patch(
        'crm.tasks.ingest_plan_sprints.SlackCommunityService',
        return_value=service,
    ):
        return ingest_plan_sprints(**kwargs)


# Two threads: an OLD one (early Jan 2026) and a RECENT one (last week of
# the default 7-day window relative to the recent ts). The default run only
# sees the recent thread; a ``since`` that predates the old one sees both.
OLD_TS = '1768435200.000100'    # 2026-01-15 00:00:00 UTC (after the since
                                # date below, but well before the 7-day
                                # first-run default window of "now")
RECENT_TS = '1900000000.000100'  # far-future, always inside any window


@override_settings(**SLACK_ON)
class SinceOverrideTests(TestCase):
    def _service(self):
        old = _msg(OLD_TS, 'U_X', 'Old update from January')
        recent = _msg(RECENT_TS, 'U_Y', 'Recent update')
        return RecordingSlackService(
            history=[old, recent],
            replies={OLD_TS: [old], RECENT_TS: [recent]},
        )

    def test_default_run_skips_old_history(self):
        # No prior run -> 7-day first-run default. The old January thread is
        # outside that window; only the recent thread is captured.
        service = self._service()
        run = _run_with(service)
        self.assertEqual(run.status, 'success')
        captured = set(SlackThread.objects.values_list('thread_ts', flat=True))
        self.assertIn(RECENT_TS, captured)
        self.assertNotIn(OLD_TS, captured)

    def test_since_override_pulls_older_history(self):
        service = self._service()
        run = _run_with(service, since=datetime.date(2025, 12, 1))
        self.assertEqual(run.status, 'success')
        # The service was asked to read from before the old thread's ts.
        self.assertLess(service.oldest_seen, OLD_TS)
        captured = set(SlackThread.objects.values_list('thread_ts', flat=True))
        self.assertIn(OLD_TS, captured)
        self.assertIn(RECENT_TS, captured)

    def test_oldest_ts_override_pulls_older_history(self):
        service = self._service()
        run = _run_with(service, oldest_ts='1700000000.000000')
        self.assertEqual(run.status, 'success')
        self.assertEqual(service.oldest_seen, '1700000000.000000')
        self.assertIn(
            OLD_TS,
            set(SlackThread.objects.values_list('thread_ts', flat=True)),
        )

    def test_idempotent_rerun_creates_no_duplicate_threads(self):
        service = self._service()
        _run_with(service, since=datetime.date(2025, 12, 1))
        first_count = SlackThread.objects.count()
        # A second backfill over the same window adds no new threads.
        _run_with(self._service(), since=datetime.date(2025, 12, 1))
        self.assertEqual(SlackThread.objects.count(), first_count)


@override_settings(**SLACK_ON)
class DryRunTests(TestCase):
    def _service(self):
        old = _msg(OLD_TS, 'U_X', 'Old update')
        return RecordingSlackService(history=[old], replies={OLD_TS: [old]})

    def test_dry_run_persists_nothing(self):
        service = self._service()
        run = _run_with(service, since=datetime.date(2025, 12, 1), dry_run=True)
        # Counts reflect what WOULD have been written...
        self.assertEqual(run.threads_persisted, 1)
        # ...but nothing is committed: no thread rows and no ingest row.
        self.assertEqual(SlackThread.objects.count(), 0)
        self.assertEqual(SlackChannelIngest.objects.count(), 0)

    def test_commit_after_dry_run_persists(self):
        _run_with(self._service(), since=datetime.date(2025, 12, 1), dry_run=True)
        self.assertEqual(SlackThread.objects.count(), 0)
        _run_with(self._service(), since=datetime.date(2025, 12, 1), dry_run=False)
        self.assertEqual(SlackThread.objects.count(), 1)


@override_settings(**SLACK_ON)
class BackfillCommandTests(TestCase):
    def _service(self):
        old = _msg(OLD_TS, 'U_X', 'Old update')
        return RecordingSlackService(history=[old], replies={OLD_TS: [old]})

    def _call(self, service, *args):
        out = StringIO()
        with patch(
            'crm.tasks.ingest_plan_sprints.SlackCommunityService',
            return_value=service,
        ):
            call_command('backfill_plan_sprints', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_is_default_and_persists_nothing(self):
        out = self._call(self._service(), '--since', '2025-12-01')
        self.assertIn('DRY-RUN', out)
        self.assertEqual(SlackThread.objects.count(), 0)

    def test_commit_persists(self):
        out = self._call(self._service(), '--since', '2025-12-01', '--commit')
        self.assertIn('Backfill complete', out)
        self.assertEqual(SlackThread.objects.count(), 1)
        self.assertTrue(SlackChannelIngest.objects.filter(status='success').exists())

    def test_bad_since_raises(self):
        with self.assertRaises(CommandError):
            call_command('backfill_plan_sprints', '--since', 'not-a-date')

    def test_future_since_raises(self):
        future = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
        with self.assertRaises(CommandError):
            call_command('backfill_plan_sprints', '--since', future)


class BackfillCommandDisabledTests(TestCase):
    @override_settings(SLACK_ENABLED=False)
    def test_slack_disabled_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command('backfill_plan_sprints', '--since', '2025-12-01')
