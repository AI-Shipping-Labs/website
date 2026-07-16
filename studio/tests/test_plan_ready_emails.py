"""Studio bulk plan-ready email action tests (issue #1055)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailLog
from notifications.models import Notification
from plans.models import (
    PLAN_READY_EMAIL_STATUS_FAILED,
    PLAN_READY_EMAIL_STATUS_SENT,
    Plan,
    PlanReadyEmailLog,
    Sprint,
)

User = get_user_model()


@tag('core')
class StudioPlanReadyEmailTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='other@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        PlanReadyEmailLog.objects.all().delete()
        Plan.objects.all().delete()

    def _detail_url(self):
        return f'/studio/sprints/{self.sprint.pk}/'

    def _send_url(self):
        return f'/studio/sprints/{self.sprint.pk}/send-plan-ready-emails/'

    def _login_staff(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_panel_shows_preview_counts_and_enabled_button(self):
        eligible = Plan.objects.create(member=self.member, sprint=self.sprint)
        sent = Plan.objects.create(member=self.other, sprint=self.sprint)
        PlanReadyEmailLog.objects.create(
            plan=sent,
            sprint=self.sprint,
            member=sent.member,
            status=PLAN_READY_EMAIL_STATUS_SENT,
            sent_at=timezone.now(),
        )
        self._login_staff()

        response = self.client.get(self._detail_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-ready-email-panel"')
        self.assertContains(response, 'data-testid="plan-ready-total-count">2<')
        self.assertContains(response, 'data-testid="plan-ready-eligible-count">1<')
        self.assertContains(response, 'data-testid="plan-ready-already-count">1<')
        self.assertContains(response, 'Emails go to members with unsent sprint plans.')
        self.assertNotContains(response, 'data-testid="plan-ready-email-button" disabled')
        self.assertContains(response, 'Not emailed')
        self.assertContains(response, 'Emailed')
        self.assertContains(
            response, f'href="/studio/users/{eligible.member.pk}/"',
        )

    def test_disabled_when_all_ready_emails_already_sent(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)
        PlanReadyEmailLog.objects.create(
            plan=plan,
            sprint=self.sprint,
            member=plan.member,
            status=PLAN_READY_EMAIL_STATUS_SENT,
            sent_at=timezone.now(),
        )
        self._login_staff()

        response = self.client.get(self._detail_url())

        self.assertContains(response, 'All plan-ready emails have already been sent')
        self.assertContains(response, 'data-testid="plan-ready-eligible-count">0<')
        self.assertContains(response, 'data-testid="plan-ready-email-button"', count=1)
        self.assertContains(response, 'disabled')

    def test_failed_attempt_row_and_count_are_visible(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)
        PlanReadyEmailLog.objects.create(
            plan=plan,
            sprint=self.sprint,
            member=plan.member,
            status=PLAN_READY_EMAIL_STATUS_FAILED,
            last_error='SES timeout',
        )
        self._login_staff()

        response = self.client.get(self._detail_url())

        self.assertContains(response, 'data-testid="plan-ready-failed-count">1<')
        self.assertContains(response, 'Failed')
        self.assertContains(response, 'SES timeout')

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_post_sends_eligible_and_redirects_with_summary(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        eligible = Plan.objects.create(member=self.member, sprint=self.sprint)
        sent = Plan.objects.create(member=self.other, sprint=self.sprint)
        PlanReadyEmailLog.objects.create(
            plan=sent,
            sprint=self.sprint,
            member=sent.member,
            status=PLAN_READY_EMAIL_STATUS_SENT,
            sent_at=timezone.now(),
        )
        self._login_staff()

        response = self.client.post(self._send_url(), follow=True)

        self.assertRedirects(response, self._detail_url())
        messages = [str(message) for message in response.context['messages']]
        self.assertTrue(
            any('1 sent, 1 skipped, 0 failed' in message for message in messages),
        )
        eligible.refresh_from_db()
        self.assertIsNotNone(eligible.shared_at)
        self.assertEqual(
            EmailLog.objects.filter(
                user=eligible.member,
                email_type='plan_shared',
            ).count(),
            1,
        )

    def test_anonymous_redirects_to_login_no_side_effects(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)

        response = self.client.post(self._send_url())

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        plan.refresh_from_db()
        self.assertIsNone(plan.shared_at)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_non_staff_forbidden_no_side_effects(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)
        self.client.login(email='member@test.com', password='pw')

        response = self.client.post(self._send_url())

        self.assertEqual(response.status_code, 403)
        plan.refresh_from_db()
        self.assertIsNone(plan.shared_at)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_get_to_send_endpoint_is_rejected(self):
        self._login_staff()
        response = self.client.get(self._send_url())
        self.assertEqual(response.status_code, 405)
