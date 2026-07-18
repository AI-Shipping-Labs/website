"""Tests for sprint-end recap delivery (issue #1201)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_PREMIUM
from email_app.models import EmailLog
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from notifications.models import Notification
from plans.models import (
    SPRINT_END_DELIVERY_STATUS_EMAIL_FAILED,
    SPRINT_END_DELIVERY_STATUS_SENT,
    Checkpoint,
    Plan,
    Sprint,
    SprintEndDeliveryLog,
    SprintEnrollment,
    SprintFeedbackRequest,
    Week,
)
from plans.tasks.sprint_end import (
    AUTO_DISTRIBUTE_FEEDBACK_KEY,
    build_sprint_end_next_action,
    send_sprint_end_recaps,
)
from questionnaires.models import Question, Questionnaire, Response
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag('core')
class SprintEndRecapTaskTest(TierSetupMixin, TestCase):
    today = datetime.date(2026, 7, 10)

    def _member(self, email, *, active=True, tier=None):
        return User.objects.create_user(
            email=email,
            password='pw',
            is_active=active,
            tier=tier or self.main_tier,
        )

    def _sprint(self, slug, *, start=None, status='active', min_level=LEVEL_MAIN):
        return Sprint.objects.create(
            name=slug.replace('-', ' ').title(),
            slug=slug,
            start_date=start or datetime.date(2026, 5, 1),
            duration_weeks=6,
            status=status,
            min_tier_level=min_level,
        )

    def _shared_plan(self, sprint, member, *, checkpoints=3, done=0):
        plan = Plan.objects.create(
            sprint=sprint,
            member=member,
            shared_at=timezone.now(),
        )
        week = Week.objects.create(plan=plan, week_number=1, position=0)
        for index in range(checkpoints):
            Checkpoint.objects.create(
                week=week,
                description=f'Checkpoint {index}',
                position=index,
                done_at=timezone.now() if index < done else None,
            )
        return plan

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_selects_only_eligible_ended_shared_member_plans(self, mock_ses):
        mock_ses.return_value = 'ses-ok'
        ended = self._sprint('ended')
        eligible = self._member('eligible@test.com')
        self._shared_plan(ended, eligible, checkpoints=3, done=2)

        inactive = self._member('inactive@test.com', active=False)
        self._shared_plan(ended, inactive)

        unshared = self._member('unshared@test.com')
        Plan.objects.create(sprint=ended, member=unshared)

        no_enrollment = self._member('no-enrollment@test.com')
        plan = self._shared_plan(ended, no_enrollment)
        SprintEnrollment.objects.filter(sprint=ended, user=no_enrollment).delete()

        future = self._sprint(
            'future',
            start=self.today + datetime.timedelta(days=7),
        )
        self._shared_plan(future, self._member('future@test.com'))

        current = self._sprint(
            'current',
            start=self.today - datetime.timedelta(days=7),
        )
        self._shared_plan(current, self._member('current@test.com'))

        draft = self._sprint('draft', status='draft')
        self._shared_plan(draft, self._member('draft@test.com'))
        cancelled = self._sprint('cancelled', status='cancelled')
        self._shared_plan(cancelled, self._member('cancelled@test.com'))

        summary = send_sprint_end_recaps(today=self.today)

        self.assertEqual(summary['eligible_count'], 1)
        self.assertEqual(summary['sent_count'], 1)
        self.assertEqual(SprintEndDeliveryLog.objects.count(), 1)
        log = SprintEndDeliveryLog.objects.get()
        self.assertEqual(log.member, eligible)
        self.assertEqual(log.plan.sprint, ended)
        self.assertEqual(log.status, SPRINT_END_DELIVERY_STATUS_SENT)
        notification = Notification.objects.get(user=eligible)
        self.assertEqual(notification.notification_type, 'sprint_recap')
        self.assertIn('2 of 3 checkpoints', notification.body)
        self.assertEqual(
            EmailLog.objects.filter(
                user=eligible,
                email_type='sprint_end_recap',
            ).count(),
            1,
        )
        self.assertFalse(
            SprintEndDeliveryLog.objects.filter(plan=plan).exists(),
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_zero_checkpoint_plan_has_clear_copy(self, mock_ses):
        mock_ses.return_value = 'ses-ok'
        sprint = self._sprint('zero')
        member = self._member('zero@test.com')
        self._shared_plan(sprint, member, checkpoints=0)

        send_sprint_end_recaps(today=self.today)

        notification = Notification.objects.get(user=member)
        self.assertIn('no checkpoints yet', notification.body)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_legacy_blank_checkpoint_does_not_inflate_recap(self, mock_ses):
        mock_ses.return_value = 'ses-ok'
        sprint = self._sprint('meaningful-progress')
        member = self._member('meaningful-progress@test.com')
        plan = self._shared_plan(sprint, member, checkpoints=2, done=1)
        week = plan.weeks.get(week_number=1)
        Checkpoint.objects.create(
            week=week,
            description='  \n\t ',
            done_at=timezone.now(),
            position=2,
        )

        send_sprint_end_recaps(today=self.today)

        notification = Notification.objects.get(user=member)
        self.assertIn('1 of 2 checkpoints', notification.body)
        self.assertNotIn('2 of 3 checkpoints', notification.body)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_delivery_is_idempotent_for_same_sprint_member(self, mock_ses):
        mock_ses.return_value = 'ses-ok'
        sprint = self._sprint('idempotent')
        member = self._member('member@test.com')
        self._shared_plan(sprint, member)

        first = send_sprint_end_recaps(today=self.today)
        second = send_sprint_end_recaps(today=self.today)

        self.assertEqual(first['sent_count'], 1)
        self.assertEqual(second['skipped_count'], 1)
        self.assertEqual(SprintEndDeliveryLog.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(user=member).count(), 1)
        self.assertEqual(
            EmailLog.objects.filter(
                user=member,
                email_type='sprint_end_recap',
            ).count(),
            1,
        )
        self.assertEqual(mock_ses.call_count, 1)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_email_failure_is_recorded_without_blocking_other_members(
        self,
        mock_ses,
    ):
        mock_ses.side_effect = [RuntimeError('SES down'), 'ses-ok']
        sprint = self._sprint('resilient')
        failed_member = self._member('failed@test.com')
        ok_member = self._member('ok@test.com')
        self._shared_plan(sprint, failed_member)
        self._shared_plan(sprint, ok_member)

        summary = send_sprint_end_recaps(today=self.today)

        self.assertEqual(summary['email_failed_count'], 1)
        self.assertEqual(summary['sent_count'], 1)
        self.assertEqual(Notification.objects.filter(user=failed_member).count(), 1)
        failed_log = SprintEndDeliveryLog.objects.get(member=failed_member)
        self.assertEqual(
            failed_log.status,
            SPRINT_END_DELIVERY_STATUS_EMAIL_FAILED,
        )
        self.assertIn('SES down', failed_log.last_error)
        self.assertIsNone(failed_log.email_log)
        ok_log = SprintEndDeliveryLog.objects.get(member=ok_member)
        self.assertEqual(ok_log.status, SPRINT_END_DELIVERY_STATUS_SENT)
        self.assertIsNotNone(ok_log.email_log)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_feedback_auto_distribution_setting_controls_responses(
        self,
        mock_ses,
    ):
        mock_ses.return_value = 'ses-ok'
        questionnaire = Questionnaire.objects.create(
            title='Sprint Feedback',
            slug='sprint-feedback',
            purpose='feedback',
        )
        Question.objects.create(
            questionnaire=questionnaire,
            question_type='long_text',
            prompt='How did it go?',
            order=0,
        )

        off_sprint = self._sprint('feedback-off')
        off_member = self._member('off@test.com')
        self._shared_plan(off_sprint, off_member)
        SprintFeedbackRequest.objects.create(
            sprint=off_sprint,
            questionnaire=questionnaire,
        )

        send_sprint_end_recaps(today=self.today)
        self.assertFalse(Response.objects.filter(respondent=off_member).exists())
        self.assertIsNone(
            SprintEndDeliveryLog.objects.get(member=off_member).feedback_response,
        )

        IntegrationSetting.objects.update_or_create(
            key=AUTO_DISTRIBUTE_FEEDBACK_KEY,
            defaults={
                'value': 'true',
                'is_secret': False,
                'group': 'site',
                'description': 'test',
            },
        )
        clear_config_cache()
        on_sprint = self._sprint('feedback-on')
        on_member = self._member('on@test.com')
        self._shared_plan(on_sprint, on_member)
        SprintFeedbackRequest.objects.create(
            sprint=on_sprint,
            questionnaire=questionnaire,
        )

        send_sprint_end_recaps(today=self.today)

        response = Response.objects.get(respondent=on_member)
        log = SprintEndDeliveryLog.objects.get(member=on_member)
        self.assertEqual(log.feedback_response, response)
        self.assertEqual(log.notification.url, reverse(
            'sprint_feedback_fill',
            kwargs={'sprint_slug': on_sprint.slug, 'response_id': response.pk},
        ))

    def test_next_action_selects_join_prepare_carry_or_none(self):
        ended = self._sprint('ended-action')
        member = self._member('action@test.com')
        next_sprint = self._sprint(
            'next-action',
            start=ended.end_date,
            min_level=LEVEL_MAIN,
        )

        join = build_sprint_end_next_action(ended_sprint=ended, member=member)
        self.assertEqual(join['kind'], 'join_next')
        self.assertEqual(join['next_sprint'], next_sprint)

        SprintEnrollment.objects.create(sprint=next_sprint, user=member)
        prepare = build_sprint_end_next_action(ended_sprint=ended, member=member)
        self.assertEqual(prepare['kind'], 'prepare_plan')
        self.assertEqual(
            prepare['url'],
            reverse('cohort_board', kwargs={'sprint_slug': next_sprint.slug}),
        )

        next_plan = Plan.objects.create(sprint=next_sprint, member=member)
        carry = build_sprint_end_next_action(ended_sprint=ended, member=member)
        self.assertEqual(carry['kind'], 'carry_over')
        self.assertEqual(carry['plan'], next_plan)

        basic_member = self._member(
            'basic@test.com',
            tier=self.basic_tier,
        )
        premium = self._sprint(
            'premium-next',
            start=ended.end_date,
            min_level=LEVEL_PREMIUM,
        )
        none = build_sprint_end_next_action(
            ended_sprint=ended,
            member=basic_member,
        )
        self.assertIsNone(none)
        self.assertTrue(Sprint.objects.filter(pk=premium.pk).exists())
