"""Studio plan share button (issue #732)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from email_app.models import EmailLog
from notifications.models import Notification
from plans.models import Plan, Sprint

User = get_user_model()


@tag('core')
class PlanShareViewTest(TestCase):
    """POST /studio/plans/<id>/share/ fires bell + email, sets shared_at."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        # A fresh plan per test so re-share tests start from a clean
        # ``shared_at = None`` baseline. ``setUpTestData`` would
        # otherwise leak state across tests.
        self.plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
        )

    def _share_url(self):
        return f'/studio/plans/{self.plan.pk}/share/'

    def test_anonymous_redirects_to_login_no_side_effects(self):
        response = self.client.post(self._share_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(Notification.objects.count(), 0)

    def test_non_staff_returns_403_no_side_effects(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.post(self._share_url())
        self.assertEqual(response.status_code, 403)
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_get_is_rejected(self):
        """The share endpoint is POST-only."""
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(self._share_url())
        self.assertEqual(response.status_code, 405)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_first_share_sets_timestamp_and_creates_bell_and_email(
        self, mock_ses,
    ):
        mock_ses.return_value = 'msg-1'
        self.client.login(email='staff@test.com', password='pw')

        response = self.client.post(self._share_url())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'/studio/plans/{self.plan.pk}/edit/',
        )
        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.shared_at)
        bell_qs = Notification.objects.filter(
            user=self.member, notification_type='plan_shared',
        )
        self.assertEqual(bell_qs.count(), 1)
        log_qs = EmailLog.objects.filter(
            user=self.member, email_type='plan_shared',
        )
        self.assertEqual(log_qs.count(), 1)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_reshare_creates_second_bell_and_second_email(self, mock_ses):
        """Operator-driven re-share fires both legs again. NOT a no-op."""
        mock_ses.return_value = 'msg-1'
        self.client.login(email='staff@test.com', password='pw')

        # First share.
        self.client.post(self._share_url())
        self.plan.refresh_from_db()
        first_shared_at = self.plan.shared_at
        self.assertIsNotNone(first_shared_at)

        # Re-share.
        response = self.client.post(self._share_url())
        self.assertEqual(response.status_code, 302)

        self.plan.refresh_from_db()
        # The timestamp moved forward to the new now() — re-share
        # always re-stamps, never preserves the original time.
        self.assertIsNotNone(self.plan.shared_at)
        self.assertGreaterEqual(self.plan.shared_at, first_shared_at)

        bell_qs = Notification.objects.filter(
            user=self.member, notification_type='plan_shared',
        )
        self.assertEqual(bell_qs.count(), 2)
        log_qs = EmailLog.objects.filter(
            user=self.member, email_type='plan_shared',
        )
        self.assertEqual(log_qs.count(), 2)

    @patch('studio.views.plans.logger.exception')
    @patch(
        'notifications.services.notification_service.'
        'NotificationService.create_plan_shared'
    )
    def test_helper_exception_does_not_unwind_shared_at(
        self, mock_helper, mock_log_exc,
    ):
        """Bell helper failure must NOT roll back the ``shared_at`` save."""
        mock_helper.side_effect = Exception('boom')

        self.client.login(email='staff@test.com', password='pw')
        response = self.client.post(self._share_url())

        # Operator does not see a 500 — the share view redirects normally.
        self.assertEqual(response.status_code, 302)
        # ``shared_at`` was still saved.
        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.shared_at)
        # The view logged the exception.
        self.assertTrue(mock_log_exc.called)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_success_message_names_the_member(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.post(self._share_url(), follow=True)
        messages = list(response.context['messages'])
        self.assertTrue(any('member@test.com' in str(m) for m in messages))


@tag('core')
class PlanEditorShareButtonTest(TestCase):
    """The editor renders the right button label and the confirm prompt."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_unshared_plan_shows_share_button(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)
        response = self.client.get(f'/studio/plans/{plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'action="/studio/plans/{plan.pk}/share/"',
        )
        self.assertContains(response, 'data-testid="plan-share-button"')
        self.assertContains(response, 'Share with member')
        # No confirm prompt on the first-share form. Other editor lifecycle
        # actions intentionally require confirmation.
        self.assertContains(
            response,
            'data-testid="plan-share-form" >',
        )
        # The re-share testid is absent.
        self.assertNotContains(response, 'data-testid="plan-reshare-button"')

    def test_shared_plan_shows_reshare_button_with_confirm(self):
        from django.utils import timezone
        plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
            shared_at=timezone.now(),
        )
        response = self.client.get(f'/studio/plans/{plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-reshare-button"')
        self.assertContains(response, 'Re-share with member')
        # Confirm prompt is wired on the re-share form.
        self.assertContains(response, "onsubmit=\"return confirm(")
        # The first-share testid is absent.
        self.assertNotContains(response, 'data-testid="plan-share-button"')
