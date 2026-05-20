"""PATCH /api/plans/<id>/ ``shared_at`` behaviour (issue #732)."""

import datetime
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import Token
from email_app.models import EmailLog
from notifications.models import Notification
from plans.models import Plan, Sprint

User = get_user_model()


class PlansApiShareTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        # ``Token`` only mints for staff (see ``accounts.models.token``).
        # To exercise the bearer-is-not-admin branch we create the token
        # while the user is staff, then demote them — the token still
        # authenticates but ``token_required`` will reject it (see
        # ``accounts.auth.token_required``).
        cls.former_staff = User.objects.create_user(
            email='former-staff@test.com', password='pw', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.former_staff_token = Token.objects.create(
            user=cls.former_staff, name='fs',
        )
        cls.former_staff.is_staff = False
        cls.former_staff.save(update_fields=['is_staff'])
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        # Fresh plan per test so state from one test does not leak into
        # the next (notably ``shared_at`` and the related logs).
        self.plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
        )

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _patch(self, payload, *, token=None):
        return self.client.patch(
            f'/api/plans/{self.plan.id}',
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(token),
        )


@tag('core')
class PlanSharePatchTest(PlansApiShareTestBase):
    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_patch_shared_at_clamps_to_server_now_and_fires(self, mock_ses):
        """A client-supplied ISO ts is IGNORED — server clamps to now().
        Bell + email both fire."""
        mock_ses.return_value = 'msg-1'
        before = Plan.objects.get(pk=self.plan.pk).shared_at
        self.assertIsNone(before)

        # Far-future client value — the server must NOT save 2030.
        response = self._patch({'shared_at': '2030-01-01T00:00:00Z'})

        self.assertEqual(response.status_code, 200)
        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.shared_at)
        # The saved timestamp is NOT the client-provided 2030 value.
        self.assertNotEqual(self.plan.shared_at.year, 2030)

        # Bell + email both fired.
        self.assertEqual(
            Notification.objects.filter(
                user=self.member, notification_type='plan_shared',
            ).count(),
            1,
        )
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            1,
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_patch_shared_at_null_clears_silently(self, mock_ses):
        """PATCH with ``{"shared_at": null}`` clears the timestamp and
        fires NOTHING — un-share is silent by design."""
        mock_ses.return_value = 'msg-1'
        # Pre-set the plan to shared so we can verify the un-share clears it.
        from django.utils import timezone
        self.plan.shared_at = timezone.now()
        self.plan.save(update_fields=['shared_at'])

        before_bells = Notification.objects.count()
        before_emails = EmailLog.objects.count()

        response = self._patch({'shared_at': None})

        self.assertEqual(response.status_code, 200)
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)

        # Nothing fired — same counts as before.
        self.assertEqual(Notification.objects.count(), before_bells)
        self.assertEqual(EmailLog.objects.count(), before_emails)

    def test_patch_shared_at_from_non_staff_bearer_is_rejected(self):
        """Only staff bearers can flip ``shared_at``.

        ``accounts.auth.token_required`` rejects every non-staff token
        with 401 before it can reach the view, so the practical answer
        for a non-staff bearer is 401. Either way the request must NOT
        mutate ``shared_at`` and must NOT fire any notification."""
        response = self._patch(
            {'shared_at': '2026-05-20T10:00:00Z'},
            token=self.former_staff_token,
        )
        # 401 from the auth layer is the practical outcome; assert it
        # is a rejection (not 200) so the contract is air-tight.
        self.assertIn(response.status_code, (401, 403))

        # No side effects regardless of which rejection code we got.
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(
            Notification.objects.filter(
                user=self.member, notification_type='plan_shared',
            ).count(),
            0,
        )
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            0,
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_patch_shared_at_reshare_fires_again(self, mock_ses):
        """API PATCH re-share path mirrors the Studio button: a second
        non-null PATCH after the first one creates a second bell and a
        second email log."""
        mock_ses.return_value = 'msg-1'
        self._patch({'shared_at': '2026-05-20T10:00:00Z'})
        self._patch({'shared_at': '2026-05-21T10:00:00Z'})

        self.assertEqual(
            Notification.objects.filter(
                user=self.member, notification_type='plan_shared',
            ).count(),
            2,
        )
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            2,
        )

    @patch('api.views.plans.logger.exception')
    @patch(
        'notifications.services.notification_service.'
        'NotificationService.create_plan_shared'
    )
    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_helper_failure_does_not_unwind_shared_at_save(
        self, mock_ses, mock_helper, mock_log_exc,
    ):
        mock_ses.return_value = 'msg-1'
        mock_helper.side_effect = Exception('boom')

        response = self._patch({'shared_at': '2026-05-20T10:00:00Z'})

        # Response is still 200 — the PATCH committed.
        self.assertEqual(response.status_code, 200)
        # ``shared_at`` was saved despite the helper exception.
        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.shared_at)
        # The exception was logged.
        self.assertTrue(mock_log_exc.called)
