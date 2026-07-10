"""Sprint cadence notification coverage for issue #1200."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailLog
from email_app.services.email_service import EmailService
from notifications.models import Notification
from plans.models import (
    SPRINT_CADENCE_KIND_WEEK_NOTE_PROMPT,
    SPRINT_CADENCE_KIND_WEEK_START,
    SPRINT_CADENCE_STATUS_EMAIL_FAILED,
    Checkpoint,
    Plan,
    Sprint,
    SprintCadenceDeliveryLog,
    Week,
    WeekNote,
)
from plans.services.sprint_cadence import send_sprint_cadence_notifications

User = get_user_model()


def _fake_send(_service, user, template_name, _context):
    if user.email.startswith('fail-'):
        raise RuntimeError(f'SES rejected {user.email}')
    return EmailLog.objects.create(
        user=user,
        email_type=template_name,
        ses_message_id=f'ses-{user.pk}',
    )


@tag('core')
class SprintCadenceNotificationTests(TestCase):
    def _user(self, email, **kwargs):
        defaults = {'password': 'pw', 'email_verified': True}
        defaults.update(kwargs)
        return User.objects.create_user(email=email, **defaults)

    def _plan(self, email, *, shared=True, sprint=None, user_kwargs=None):
        member = self._user(email, **(user_kwargs or {}))
        sprint = sprint or self.sprint
        return Plan.objects.create(
            member=member,
            sprint=sprint,
            shared_at=timezone.now() if shared else None,
        )

    def _week(self, plan, number, *, position, theme=''):
        return Week.objects.create(
            plan=plan,
            week_number=number,
            position=position,
            theme=theme,
        )

    def setUp(self):
        self.today = datetime.date(2026, 5, 15)
        self.sprint = Sprint.objects.create(
            name='May Sprint',
            slug='may-sprint',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
            status='active',
        )

    @patch.object(EmailService, 'send', autospec=True, side_effect=_fake_send)
    def test_week_start_selects_only_active_shared_plans_and_is_idempotent(
        self,
        _send,
    ):
        eligible = self._plan('eligible@test.com')
        week1 = self._week(eligible, 1, position=0, theme='Prep')
        week2 = self._week(eligible, 2, position=1, theme='Build')
        week3 = self._week(eligible, 3, position=2, theme='Ship prototype')
        for index in range(4):
            cp = Checkpoint.objects.create(
                week=week3,
                description=f'Checkpoint {index}',
            )
            if index < 2:
                cp.done_at = timezone.now()
                cp.save(update_fields=['done_at'])

        unshared = self._plan('unshared@test.com', shared=False)
        self._week(unshared, 3, position=2, theme='Hidden')

        ended_sprint = Sprint.objects.create(
            name='Ended',
            slug='ended',
            start_date=datetime.date(2026, 4, 1),
            duration_weeks=4,
            status='active',
        )
        ended = self._plan('ended@test.com', sprint=ended_sprint)
        self._week(ended, 1, position=0)

        inactive = self._plan(
            'inactive@test.com',
            user_kwargs={'is_active': False},
        )
        self._week(inactive, 3, position=2)

        first = send_sprint_cadence_notifications(today=self.today)
        second = send_sprint_cadence_notifications(today=self.today)

        self.assertEqual(first['week_start_created'], 1)
        self.assertEqual(second['week_start_created'], 0)
        self.assertEqual(
            Notification.objects.filter(
                user=eligible.member,
                notification_type='sprint_week_start',
            ).count(),
            1,
        )
        notification = Notification.objects.get(user=eligible.member)
        self.assertEqual(notification.url, f'/sprints/may-sprint/plan/{eligible.pk}#week-{week3.pk}')
        self.assertIn('Week 3', notification.title)
        self.assertIn('Ship prototype', notification.title)
        self.assertIn('2 unfinished checkpoints', notification.body)
        self.assertIn('Week 2 note', notification.body)
        self.assertFalse(week1.notes.exists())
        self.assertFalse(week2.notes.exists())
        self.assertEqual(
            Notification.objects.exclude(user=eligible.member).count(),
            0,
        )
        self.assertEqual(
            SprintCadenceDeliveryLog.objects.filter(
                kind=SPRINT_CADENCE_KIND_WEEK_START,
                plan=eligible,
                week=week3,
            ).count(),
            1,
        )

    @patch.object(EmailService, 'send', autospec=True, side_effect=_fake_send)
    def test_week_note_prompt_is_due_at_week_end_and_suppressed_by_note(
        self,
        _send,
    ):
        prompt_plan = self._plan('prompt@test.com')
        prompt_week = self._week(prompt_plan, 2, position=1, theme='Validate')
        noted_plan = self._plan('noted@test.com')
        noted_week = self._week(noted_plan, 2, position=1, theme='Validate')
        WeekNote.objects.create(
            week=noted_week,
            body='Already wrote it.',
            author=noted_plan.member,
        )

        result = send_sprint_cadence_notifications(
            today=datetime.date(2026, 5, 14),
        )

        self.assertEqual(result['week_note_prompt_created'], 1)
        self.assertEqual(
            Notification.objects.filter(
                user=prompt_plan.member,
                notification_type='week_note_prompt',
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(user=noted_plan.member).count(),
            0,
        )
        log = SprintCadenceDeliveryLog.objects.get(
            kind=SPRINT_CADENCE_KIND_WEEK_NOTE_PROMPT,
            plan=prompt_plan,
            week=prompt_week,
        )
        self.assertIsNotNone(log.notification)

    @patch.object(EmailService, 'send', autospec=True, side_effect=_fake_send)
    def test_email_preferences_filter_email_but_keep_in_app_notification(
        self,
        mock_send,
    ):
        opted_in = self._plan('opted-in@test.com')
        self._week(opted_in, 1, position=0)
        opted_out = self._plan('opted-out@test.com')
        opted_out.member.email_preferences = {'sprint_cadence_emails': False}
        opted_out.member.save(update_fields=['email_preferences'])
        self._week(opted_out, 1, position=0)
        unverified = self._plan(
            'unverified@test.com',
            user_kwargs={'email_verified': False},
        )
        self._week(unverified, 1, position=0)
        unsubscribed = self._plan(
            'unsubscribed@test.com',
            user_kwargs={'unsubscribed': True},
        )
        self._week(unsubscribed, 1, position=0)

        result = send_sprint_cadence_notifications(
            today=datetime.date(2026, 5, 1),
        )

        self.assertEqual(result['week_start_created'], 4)
        self.assertEqual(Notification.objects.count(), 4)
        self.assertEqual(result['emails_sent'], 1)
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(
            EmailLog.objects.values_list('user__email', flat=True).get(),
            'opted-in@test.com',
        )
        self.assertEqual(
            SprintCadenceDeliveryLog.objects.filter(
                email_log__isnull=True,
            ).count(),
            3,
        )

    @patch.object(EmailService, 'send', autospec=True, side_effect=_fake_send)
    def test_email_failure_is_logged_without_blocking_other_members(
        self,
        _send,
    ):
        failing = self._plan('fail-member@test.com')
        self._week(failing, 1, position=0)
        passing = self._plan('pass-member@test.com')
        self._week(passing, 1, position=0)

        result = send_sprint_cadence_notifications(
            today=datetime.date(2026, 5, 1),
        )

        self.assertEqual(result['week_start_created'], 2)
        self.assertEqual(result['emails_sent'], 1)
        self.assertEqual(result['emails_failed'], 1)
        self.assertEqual(Notification.objects.count(), 2)
        failed_log = SprintCadenceDeliveryLog.objects.get(plan=failing)
        self.assertEqual(failed_log.status, SPRINT_CADENCE_STATUS_EMAIL_FAILED)
        self.assertIn('SES rejected fail-member@test.com', failed_log.last_error)
        self.assertIsNotNone(failed_log.notification)
        self.assertIsNone(failed_log.email_log)
        self.assertEqual(EmailLog.objects.count(), 1)
