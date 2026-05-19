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

        with self.assertLogs('accounts.views.auth', level='WARNING') as logs:
            resp = self._register('boomy@example.com')
        self.assertEqual(resp.status_code, 201)
        self.assertIn(
            'Slack membership probe failed during signup for boomy@example.com',
            logs.output[0],
        )
        # User must still be created.
        user = User.objects.get(email='boomy@example.com')
        # No probe outcome persisted.
        self.assertFalse(user.slack_member)
        self.assertIsNone(user.slack_checked_at)


class SignupSlackNameBackfillTest(TestCase):
    """Issue #709: verify signup-time Slack probe backfills first/last name.

    These tests exercise the ``member`` branch in
    ``_probe_slack_membership_on_signup`` calling
    ``_backfill_name_from_slack`` so a freshly-signed-up user whose email
    is already in Slack gets their name populated in the same DB
    round-trip — instead of waiting up to a week for the periodic refresh.
    """

    url = '/api/register'

    def _register(self, email='probe@example.com', password='secure1234'):
        return self.client.post(
            self.url,
            data=json.dumps({'email': email, 'password': password}),
            content_type='application/json',
        )

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_member_with_split_profile_persists_first_and_last(
        self, mock_get_service, _send
    ):
        """Slack profile with split first/last fields lands on the User row."""
        svc = MagicMock(
            spec=['check_workspace_membership', 'lookup_user_profile_by_email']
        )
        svc.check_workspace_membership.return_value = ('member', 'U_A')
        svc.lookup_user_profile_by_email.return_value = {
            'id': 'U_A',
            'first_name': 'Alex',
            'last_name': 'Grigorev',
            'real_name': 'Alex Grigorev',
        }
        mock_get_service.return_value = svc

        resp = self._register('alex@example.com')
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email='alex@example.com')
        self.assertTrue(user.slack_member)
        self.assertEqual(user.first_name, 'Alex')
        self.assertEqual(user.last_name, 'Grigorev')
        svc.lookup_user_profile_by_email.assert_called_once_with('alex@example.com')

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_member_with_real_name_only_splits_on_last_space(
        self, mock_get_service, _send
    ):
        """``real_name`` alone falls back to last-space split."""
        svc = MagicMock(
            spec=['check_workspace_membership', 'lookup_user_profile_by_email']
        )
        svc.check_workspace_membership.return_value = ('member', 'U_B')
        svc.lookup_user_profile_by_email.return_value = {
            'id': 'U_B',
            'first_name': '',
            'last_name': '',
            'real_name': 'Salvador Castillo Raya',
        }
        mock_get_service.return_value = svc

        resp = self._register('sal@example.com')
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email='sal@example.com')
        self.assertEqual(user.first_name, 'Salvador Castillo')
        self.assertEqual(user.last_name, 'Raya')

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_member_with_lookup_raising_still_returns_201(
        self, mock_get_service, _send
    ):
        """Profile lookup blowing up must not break signup.

        Membership flag and ``slack_checked_at`` still persist; the user
        is created; names stay blank; a WARNING is logged.
        """
        svc = MagicMock(
            spec=['check_workspace_membership', 'lookup_user_profile_by_email']
        )
        svc.check_workspace_membership.return_value = ('member', 'U_C')
        svc.lookup_user_profile_by_email.side_effect = RuntimeError('transient')
        mock_get_service.return_value = svc

        with self.assertLogs('community.tasks.slack_membership', level='WARNING') as logs:
            resp = self._register('flaky-profile@example.com')

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            any(
                'Slack profile lookup failed for flaky-profile@example.com' in line
                for line in logs.output
            ),
            f'Expected profile-lookup WARNING in logs, got: {logs.output}',
        )

        user = User.objects.get(email='flaky-profile@example.com')
        self.assertTrue(user.slack_member)
        self.assertEqual(user.slack_user_id, 'U_C')
        self.assertIsNotNone(user.slack_checked_at)
        self.assertEqual(user.first_name, '')
        self.assertEqual(user.last_name, '')

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_not_member_branch_skips_profile_lookup(
        self, mock_get_service, _send
    ):
        """``not_member`` outcome must not call the profile lookup."""
        svc = MagicMock(
            spec=['check_workspace_membership', 'lookup_user_profile_by_email']
        )
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        resp = self._register('outside@example.com')
        self.assertEqual(resp.status_code, 201)

        svc.lookup_user_profile_by_email.assert_not_called()
        user = User.objects.get(email='outside@example.com')
        self.assertFalse(user.slack_member)
        self.assertEqual(user.first_name, '')
        self.assertEqual(user.last_name, '')

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_unknown_branch_skips_profile_lookup(
        self, mock_get_service, _send
    ):
        """``unknown`` outcome must not call the profile lookup."""
        svc = MagicMock(
            spec=['check_workspace_membership', 'lookup_user_profile_by_email']
        )
        svc.check_workspace_membership.return_value = ('unknown', None)
        mock_get_service.return_value = svc

        resp = self._register('foggy@example.com')
        self.assertEqual(resp.status_code, 201)

        svc.lookup_user_profile_by_email.assert_not_called()
        user = User.objects.get(email='foggy@example.com')
        self.assertEqual(user.first_name, '')
        self.assertEqual(user.last_name, '')

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_preexisting_first_name_not_overwritten(
        self, mock_get_service, _send
    ):
        """A pre-existing non-empty ``first_name`` must not be overwritten.

        Fresh signups normally land with blank names, but the helper's
        do-not-overwrite invariant is exercised here as a regression
        guard. We pre-create the user with a first_name set, then call
        the probe directly to simulate the post-create code path.
        """
        from accounts.views.auth import _probe_slack_membership_on_signup

        svc = MagicMock(
            spec=['check_workspace_membership', 'lookup_user_profile_by_email']
        )
        svc.check_workspace_membership.return_value = ('member', 'U_D')
        svc.lookup_user_profile_by_email.return_value = {
            'id': 'U_D',
            'first_name': 'Slack',
            'last_name': 'Override',
            'real_name': 'Slack Override',
        }
        mock_get_service.return_value = svc

        user = User.objects.create_user(
            email='preset@example.com',
            password='secure1234',
            first_name='Existing',
        )
        _probe_slack_membership_on_signup(user)

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        # Existing first_name preserved.
        self.assertEqual(user.first_name, 'Existing')
        # last_name was empty so the Slack value filled it.
        self.assertEqual(user.last_name, 'Override')

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_service_without_lookup_method_is_noop_on_names(
        self, mock_get_service, _send
    ):
        """Defensive: service missing ``lookup_user_profile_by_email``.

        Helper returns False, signup proceeds normally, membership flag
        still flips, names stay blank.
        """
        svc = MagicMock(spec=['check_workspace_membership'])
        svc.check_workspace_membership.return_value = ('member', 'U_E')
        mock_get_service.return_value = svc

        resp = self._register('minimal@example.com')
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email='minimal@example.com')
        self.assertTrue(user.slack_member)
        self.assertEqual(user.first_name, '')
        self.assertEqual(user.last_name, '')

    @patch('accounts.views.auth._send_verification_email')
    @patch('community.services.get_community_service')
    def test_empty_slack_profile_does_not_write_spurious_values(
        self, mock_get_service, _send
    ):
        """Empty profile (no first/last/real_name) writes nothing.

        Helper returns False; ``update_fields`` does NOT include the
        name fields; the user row's names stay at their empty defaults.
        """
        svc = MagicMock(
            spec=['check_workspace_membership', 'lookup_user_profile_by_email']
        )
        svc.check_workspace_membership.return_value = ('member', 'U_F')
        svc.lookup_user_profile_by_email.return_value = {
            'id': 'U_F',
            'first_name': '',
            'last_name': '',
            'real_name': '',
        }
        mock_get_service.return_value = svc

        resp = self._register('blank-profile@example.com')
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email='blank-profile@example.com')
        self.assertTrue(user.slack_member)
        self.assertEqual(user.first_name, '')
        self.assertEqual(user.last_name, '')
