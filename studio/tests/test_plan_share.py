"""Studio plan share / re-share actions (issues #732, #1455)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailLog
from notifications.models import Notification
from plans.models import Plan, PlanReadyEmailLog, Sprint

User = get_user_model()


@tag('core')
class PlanShareViewTest(TestCase):
    """POST /studio/plans/<id>/share/ requires an explicit intent."""

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

    def _post(self, intent=None, *, follow=False, **extra):
        data = dict(extra)
        if intent is not None:
            data['intent'] = intent
        return self.client.post(self._share_url(), data, follow=follow)

    def test_anonymous_redirects_to_login_no_side_effects(self):
        response = self._post('ready')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(Notification.objects.count(), 0)

    def test_non_staff_returns_403_no_side_effects(self):
        self.client.login(email='member@test.com', password='pw')
        response = self._post('ready')
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
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_missing_intent_delivers_nothing(self, mock_ses):
        self.client.login(email='staff@test.com', password='pw')

        response = self._post()

        self.assertEqual(response.status_code, 302)
        self.assertFalse(mock_ses.called)
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_unknown_intent_delivers_nothing(self, mock_ses):
        self.client.login(email='staff@test.com', password='pw')

        self._post('publish')

        self.assertFalse(mock_ses.called)
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(Notification.objects.count(), 0)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_reshare_intent_on_unshared_plan_delivers_nothing(self, mock_ses):
        self.client.login(email='staff@test.com', password='pw')

        response = self._post('reshare', follow=True)

        self.assertFalse(mock_ses.called)
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertTrue(any(
            'has not been shared yet' in str(m)
            for m in response.context['messages']
        ))

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_first_share_sets_timestamp_and_creates_bell_and_email(
        self, mock_ses,
    ):
        mock_ses.return_value = 'msg-1'
        self.client.login(email='staff@test.com', password='pw')

        response = self._post('ready')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'/studio/plans/{self.plan.pk}/',
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
        self.assertEqual(
            PlanReadyEmailLog.objects.filter(plan=self.plan).count(), 1,
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_editor_share_returns_to_the_editor(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        self.client.login(email='staff@test.com', password='pw')

        response = self._post(
            'ready', return_to=f'/studio/plans/{self.plan.pk}/edit/',
        )

        self.assertEqual(
            response['Location'],
            f'/studio/plans/{self.plan.pk}/edit/',
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_replayed_ready_intent_does_not_duplicate_delivery(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        self.client.login(email='staff@test.com', password='pw')
        self._post('ready')
        self.plan.refresh_from_db()
        first_shared_at = self.plan.shared_at

        response = self._post('ready', follow=True)

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.shared_at, first_shared_at)
        self.assertEqual(
            Notification.objects.filter(
                notification_type='plan_shared',
            ).count(),
            1,
        )
        self.assertEqual(
            EmailLog.objects.filter(email_type='plan_shared').count(), 1,
        )
        self.assertTrue(any(
            'already' in str(m) for m in response.context['messages']
        ))

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_ready_intent_on_historically_shared_plan_does_not_send(
        self, mock_ses,
    ):
        shared_at = timezone.now()
        self.plan.shared_at = shared_at
        self.plan.save(update_fields=['shared_at'])
        self.client.login(email='staff@test.com', password='pw')

        response = self._post('ready', follow=True)

        self.assertFalse(mock_ses.called)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.shared_at, shared_at)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertTrue(any(
            'Re-share with member' in str(m)
            for m in response.context['messages']
        ))

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_reshare_creates_second_bell_and_second_email(self, mock_ses):
        """Operator-driven re-share fires both legs again. NOT a no-op."""
        mock_ses.return_value = 'msg-1'
        self.client.login(email='staff@test.com', password='pw')

        # First share.
        self._post('ready')
        self.plan.refresh_from_db()
        first_shared_at = self.plan.shared_at
        self.assertIsNotNone(first_shared_at)

        # Re-share.
        response = self._post('reshare')
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
        'NotificationService.create_plan_shared_delivery'
    )
    def test_reshare_helper_exception_does_not_unwind_shared_at(
        self, mock_helper, mock_log_exc,
    ):
        """Bell helper failure must NOT roll back the ``shared_at`` save."""
        self.plan.shared_at = timezone.now() - datetime.timedelta(days=1)
        self.plan.save(update_fields=['shared_at'])
        original_shared_at = self.plan.shared_at
        mock_helper.side_effect = Exception('boom')

        self.client.login(email='staff@test.com', password='pw')
        response = self._post('reshare', follow=True)

        # Operator does not see a 500 — the share view redirects normally.
        self.assertEqual(response.status_code, 200)
        # ``shared_at`` was still re-stamped.
        self.plan.refresh_from_db()
        self.assertGreater(self.plan.shared_at, original_shared_at)
        # The view logged the exception.
        self.assertTrue(mock_log_exc.called)

    @patch(
        'notifications.services.notification_service.'
        'NotificationService.create_plan_shared_delivery'
    )
    def test_reshare_email_failure_is_reported_truthfully(self, mock_delivery):
        from notifications.services.notification_service import (
            PlanSharedDelivery,
        )

        self.plan.shared_at = timezone.now()
        self.plan.save(update_fields=['shared_at'])
        notification = Notification.objects.create(
            user=self.member,
            title='Your plan is ready',
            body='body',
            notification_type='plan_shared',
        )
        mock_delivery.return_value = PlanSharedDelivery(
            notification=notification,
            email_log=None,
            email_error='ses exploded',
        )
        self.client.login(email='staff@test.com', password='pw')

        response = self._post('reshare', follow=True)

        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('email failed to send' in m for m in messages))
        self.assertFalse(any(
            'A new bell notification and email' in m for m in messages
        ))

    @patch(
        'notifications.services.notification_service.'
        'NotificationService.create_plan_shared_delivery'
    )
    def test_failed_first_share_does_not_claim_the_member_was_notified(
        self, mock_delivery,
    ):
        mock_delivery.side_effect = Exception('ses exploded')
        self.client.login(email='staff@test.com', password='pw')

        response = self._post('ready', follow=True)

        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('failed' in m for m in messages))
        self.assertFalse(any('Plan shared with' in m for m in messages))

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_success_message_names_the_member(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        self.client.login(email='staff@test.com', password='pw')
        response = self._post('ready', follow=True)
        messages = list(response.context['messages'])
        self.assertTrue(any('member@test.com' in str(m) for m in messages))


@tag('core')
class PlanDetailShareActionTest(TestCase):
    """The Studio plan detail header exposes the share state + action."""

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

    def _detail(self, plan):
        return self.client.get(f'/studio/plans/{plan.pk}/')

    def test_unshared_plan_shows_not_shared_and_share_action(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)

        response = self._detail(plan)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-detail-share-state"')
        self.assertContains(response, 'Not shared')
        self.assertContains(response, 'data-testid="plan-detail-share-button"')
        self.assertContains(response, 'Share with member')
        self.assertContains(
            response, f'action="/studio/plans/{plan.pk}/share/"',
        )
        self.assertContains(response, 'name="intent" value="ready"')
        self.assertNotContains(
            response, 'data-testid="plan-detail-reshare-button"',
        )
        # The first-share action must not sit behind a confirm dialog.
        self.assertContains(
            response, 'data-testid="plan-detail-share-form">',
        )

    def test_shared_plan_shows_shared_date_and_confirmed_reshare(self):
        plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
            shared_at=timezone.now(),
        )

        response = self._detail(plan)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Shared ')
        self.assertNotContains(response, 'Not shared')
        self.assertContains(
            response, 'data-testid="plan-detail-reshare-button"',
        )
        self.assertContains(response, 'Re-share with member')
        self.assertContains(response, 'name="intent" value="reshare"')
        self.assertContains(response, 'another bell notification and another')
        self.assertNotContains(
            response, 'data-testid="plan-detail-share-button"',
        )

    def test_existing_header_actions_are_preserved(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)

        response = self._detail(plan)

        self.assertContains(response, 'Edit plan')
        self.assertContains(
            response, 'data-testid="studio-plan-view-as-member"',
        )
        self.assertContains(response, 'data-testid="plan-access-card"')


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
        self.assertContains(response, 'name="intent" value="ready"')
        # No confirm prompt on the first-share form. Other editor lifecycle
        # actions intentionally require confirmation.
        self.assertContains(
            response,
            'data-testid="plan-share-form" >',
        )
        # The re-share testid is absent.
        self.assertNotContains(response, 'data-testid="plan-reshare-button"')

    def test_shared_plan_shows_reshare_button_with_confirm(self):
        plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
            shared_at=timezone.now(),
        )
        response = self.client.get(f'/studio/plans/{plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-reshare-button"')
        self.assertContains(response, 'Re-share with member')
        self.assertContains(response, 'name="intent" value="reshare"')
        # Confirm prompt is wired on the re-share form.
        self.assertContains(response, "onsubmit=\"return confirm(")
        self.assertContains(response, 'another bell notification and another')
        # The first-share testid is absent.
        self.assertNotContains(response, 'data-testid="plan-share-button"')
