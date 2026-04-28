"""Tests for the signup-time Slack workspace membership probe (issue #358).

The signup view calls ``check_workspace_membership`` synchronously to
flip ``slack_member=True`` for users who already exist in Slack at
signup time. This is best-effort: any error in the Slack call must
not break signup, and the periodic task picks up unprobed users.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import User


class SignupSlackProbeTest(TestCase):
    """Verify register_api integrates the Slack membership probe."""

    url = '/api/register'

    def _register(self, email='probe@example.com', password='secure1234'):
        return self.client.post(
            self.url,
            data=json.dumps({'email': email, 'password': password}),
            content_type='application/json',
        )

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_member_outcome_flips_slack_member_true(self, mock_get_service, _send):
        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_X')
        mock_get_service.return_value = svc

        resp = self._register('inworkspace@example.com')
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email='inworkspace@example.com')
        self.assertTrue(user.slack_member)
        self.assertEqual(user.slack_user_id, 'U_X')
        self.assertIsNotNone(user.slack_checked_at)

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_not_member_outcome_sets_slack_member_false(self, mock_get_service, _send):
        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        resp = self._register('outsider@example.com')
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email='outsider@example.com')
        self.assertFalse(user.slack_member)
        self.assertIsNotNone(user.slack_checked_at)

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_unknown_outcome_leaves_fields_null(self, mock_get_service, _send):
        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('unknown', None)
        mock_get_service.return_value = svc

        resp = self._register('flaky@example.com')
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email='flaky@example.com')
        self.assertFalse(user.slack_member)
        self.assertIsNone(user.slack_checked_at)

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_signup_succeeds_when_slack_service_raises(self, mock_get_service, _send):
        """Acceptance criteria: signup must return 201 even if Slack errors."""
        svc = MagicMock()
        svc.check_workspace_membership.side_effect = RuntimeError('boom')
        mock_get_service.return_value = svc

        resp = self._register('boomy@example.com')
        self.assertEqual(resp.status_code, 201)
        # User must still be created.
        user = User.objects.get(email='boomy@example.com')
        # No probe outcome persisted.
        self.assertFalse(user.slack_member)
        self.assertIsNone(user.slack_checked_at)
