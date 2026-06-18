"""Backfill tests for turning matched Slack threads into member notes."""

import datetime
import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from crm.models import CRMRecord, SlackMessage, SlackThread
from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()


class SlackThreadNoteBackfillTest(TestCase):
    def test_backfill_creates_one_internal_tagged_note_per_matched_thread(self):
        member = User.objects.create_user(
            email='backfill@test.com',
            password='pw',
            slack_user_id='U_BACKFILL',
        )
        sprint = Sprint.objects.create(
            name='May 2026 Accountability Sprint',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        plan = Plan.objects.create(member=member, sprint=sprint)
        thread = SlackThread.objects.create(
            channel_id='C_PLAN',
            thread_ts='1700000000.000000',
            slack_user_id='U_BACKFILL',
            member=member,
            plan=plan,
            posted_at=timezone.now(),
            permalink='https://slack.example/thread',
            reply_count=1,
        )
        SlackMessage.objects.create(
            thread=thread,
            ts='1700000000.000000',
            slack_user_id='U_BACKFILL',
            author_display='Member',
            text='_This week:_\n• _Shipped API:_ /query\n• .',
            posted_at=timezone.now(),
            is_root=True,
        )
        SlackMessage.objects.create(
            thread=thread,
            ts='1700000001.000000',
            slack_user_id='U_OTHER',
            author_display='Coach',
            text='B_locker:_\n• No blockers',
            posted_at=timezone.now(),
        )

        module = importlib.import_module('crm.migrations.0007_slackthread_interview_note')
        module.backfill_slack_threads_to_notes(apps, None)

        thread.refresh_from_db()
        self.assertIsNotNone(thread.interview_note_id)
        self.assertEqual(InterviewNote.objects.count(), 1)
        note = thread.interview_note
        self.assertEqual(note.member, member)
        self.assertEqual(note.plan, plan)
        self.assertEqual(note.visibility, 'internal')
        self.assertEqual(note.source_type, 'slack')
        self.assertEqual(note.tags, ['slack', 'plan-sprints', 'sprint:may-2026'])
        self.assertEqual(note.source_metadata['thread_ts'], thread.thread_ts)
        self.assertEqual(note.source_metadata['permalink'], thread.permalink)
        self.assertIn('This week:', note.body)
        self.assertIn('/query', note.body)
        self.assertIn('Blocker:', note.body)
        self.assertNotIn('• .', note.body)
        self.assertEqual(SlackMessage.objects.filter(thread=thread).count(), 2)
        self.assertTrue(CRMRecord.objects.filter(user=member).exists())

