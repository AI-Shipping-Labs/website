import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from crm.models import SlackMessage, SlackThread
from crm.services.slack_note_sync import sync_thread_to_interview_note
from crm.tasks import purge_plan_sprints_raw_text
from plans.models import Plan, Sprint

User = get_user_model()


@override_settings(PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS=30)
class PlanSprintsRawTextRetentionTests(TestCase):
    def test_redacts_only_expired_text_and_rebuilds_canonical_note(self):
        member = User.objects.create_user(email='retain@test.com', password='x')
        sprint = Sprint.objects.create(
            name='Retention', slug='retention',
            start_date=datetime.date(2026, 1, 1), status='active',
        )
        plan = Plan.objects.create(member=member, sprint=sprint)
        thread = SlackThread.objects.create(
            channel_id='C1', thread_ts='1.0', member=member, plan=plan,
            posted_at=timezone.now() - timezone.timedelta(days=40),
        )
        old = SlackMessage.objects.create(
            thread=thread, ts='1.0', text='expired private update',
            posted_at=timezone.now() - timezone.timedelta(days=40), is_root=True,
        )
        recent = SlackMessage.objects.create(
            thread=thread, ts='2.0', text='recent reply',
            posted_at=timezone.now() - timezone.timedelta(days=2),
        )
        sync_thread_to_interview_note(thread)
        self.assertIn('expired private update', thread.interview_note.body)

        result = purge_plan_sprints_raw_text()

        old.refresh_from_db()
        recent.refresh_from_db()
        thread.interview_note.refresh_from_db()
        self.assertEqual(old.text, '')
        self.assertEqual(recent.text, 'recent reply')
        self.assertNotIn('expired private update', thread.interview_note.body)
        self.assertIn('recent reply', thread.interview_note.body)
        self.assertEqual(result, {'messages_redacted': 1, 'threads_refreshed': 1})

    def test_empty_sweep_is_idempotent(self):
        self.assertEqual(
            purge_plan_sprints_raw_text(),
            {'messages_redacted': 0, 'threads_refreshed': 0},
        )
