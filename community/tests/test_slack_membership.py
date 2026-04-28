"""Tests for Slack workspace membership tracking (issue #358).

Covers:
- ``SlackCommunityService.check_workspace_membership`` three-state result.
- ``community.tasks.slack_membership.refresh_slack_membership`` task.
- Rate-limit throttling behaviour.
- Audit log writes only on state transitions.
- Forward-compatibility with #354 (auto-tag mirror).
- Fallback to ``unknown`` when integration unconfigured.
"""

import json
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from community.models import CommunityAuditLog
from community.services.slack import SlackAPIError, SlackCommunityService
from community.tasks import slack_membership as task_module
from community.tasks.slack_membership import refresh_slack_membership


@override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN='xoxb-test')
class CheckWorkspaceMembershipTest(TestCase):
    """Tests for SlackCommunityService.check_workspace_membership."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token='xoxb-test',
            channel_ids=['C001'],
        )

    @patch('community.services.slack.requests.post')
    def test_member_returns_member_with_uid(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'ok': True, 'user': {'id': 'U999'}}
        mock_post.return_value = mock_response

        result = self.service.check_workspace_membership('a@example.com')
        self.assertEqual(result, ('member', 'U999'))

    @patch('community.services.slack.requests.post')
    def test_users_not_found_returns_not_member(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'ok': False, 'error': 'users_not_found',
        }
        mock_post.return_value = mock_response

        result = self.service.check_workspace_membership('a@example.com')
        self.assertEqual(result, ('not_member', None))

    @patch('community.services.slack.requests.post')
    def test_ratelimited_returns_unknown(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'ok': False, 'error': 'ratelimited',
        }
        mock_post.return_value = mock_response

        result = self.service.check_workspace_membership('a@example.com')
        self.assertEqual(result, ('unknown', None))

    @patch('community.services.slack.requests.post')
    def test_5xx_returns_unknown(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_post.return_value = mock_response

        result = self.service.check_workspace_membership('a@example.com')
        self.assertEqual(result, ('unknown', None))

    @patch('community.services.slack.requests.post')
    def test_network_error_returns_unknown(self, mock_post):
        mock_post.side_effect = requests.ConnectionError('timeout')

        result = self.service.check_workspace_membership('a@example.com')
        self.assertEqual(result, ('unknown', None))


class CheckWorkspaceMembershipUnconfiguredTest(TestCase):
    """When SLACK_ENABLED=False or no token, return unknown without crashing."""

    @override_settings(SLACK_ENABLED=False, SLACK_BOT_TOKEN='xoxb-test')
    def test_disabled_returns_unknown(self):
        service = SlackCommunityService(
            bot_token='xoxb-test', channel_ids=['C001'],
        )
        result = service.check_workspace_membership('a@example.com')
        self.assertEqual(result, ('unknown', None))

    @override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN='')
    def test_empty_token_returns_unknown(self):
        service = SlackCommunityService(
            bot_token='', channel_ids=['C001'],
        )
        result = service.check_workspace_membership('a@example.com')
        self.assertEqual(result, ('unknown', None))


class RefreshSlackMembershipTaskTest(TestCase):
    """Tests for the periodic refresh_slack_membership task."""

    def _make_user(self, email, **extra):
        # Force-set slack_member directly (bypass the default save() so
        # we can simulate the "first check has never happened" state).
        return User.objects.create_user(email=email, **extra)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_member_outcome_sets_slack_member_true_and_timestamp(self, mock_get_service):
        user = self._make_user('member@test.com')
        self.assertIsNone(user.slack_checked_at)
        self.assertFalse(user.slack_member)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_NEW')
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        self.assertIsNotNone(user.slack_checked_at)
        self.assertEqual(user.slack_user_id, 'U_NEW')
        self.assertEqual(result['members'], 1)
        self.assertEqual(result['not_members'], 0)
        self.assertEqual(result['unknown'], 0)
        self.assertEqual(result['transitions'], 1)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_not_member_outcome_sets_slack_member_false_and_timestamp(self, mock_get_service):
        user = self._make_user('not@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertFalse(user.slack_member)
        self.assertIsNotNone(user.slack_checked_at)
        self.assertEqual(result['not_members'], 1)
        # First-ever check counts as a transition even if NULL -> False.
        self.assertEqual(result['transitions'], 1)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_unknown_outcome_leaves_fields_unchanged(self, mock_get_service):
        # Pre-set a timestamp so we can verify it's NOT advanced.
        user = self._make_user('flaky@test.com')
        original_ts = timezone.now() - timezone.timedelta(days=14)
        user.slack_checked_at = original_ts
        user.slack_member = True
        user.save(update_fields=['slack_checked_at', 'slack_member'])

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('unknown', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        # Timestamp must NOT advance — retry next cycle.
        self.assertEqual(user.slack_checked_at, original_ts)
        self.assertTrue(user.slack_member)
        self.assertEqual(result['unknown'], 1)
        self.assertEqual(result['transitions'], 0)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_only_picks_null_or_stale_users(self, mock_get_service):
        # Recently-checked: should be skipped.
        recent = self._make_user('recent@test.com')
        recent.slack_checked_at = timezone.now()
        recent.slack_member = True
        recent.save(update_fields=['slack_checked_at', 'slack_member'])

        # Stale (>7d): should be checked.
        stale = self._make_user('stale@test.com')
        stale.slack_checked_at = timezone.now() - timezone.timedelta(days=10)
        stale.slack_member = False
        stale.save(update_fields=['slack_checked_at', 'slack_member'])

        # Never-checked: should be checked.
        self._make_user('never@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        # Only stale + never checked, in order (NULL first).
        self.assertEqual(result['total_checked'], 2)
        called_emails = [
            call.args[0] for call in svc.check_workspace_membership.call_args_list
        ]
        # NULL slack_checked_at sorts first under ASC ordering.
        self.assertEqual(called_emails[0], 'never@test.com')
        self.assertIn('stale@test.com', called_emails)
        self.assertNotIn('recent@test.com', called_emails)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_batch_size_caps_run(self, mock_get_service):
        # Create 5 stale users; cap to 2 per run.
        for i in range(5):
            self._make_user(f'u{i}@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(batch_size=2, sleep_seconds=0)

        self.assertEqual(result['total_checked'], 2)
        self.assertEqual(svc.check_workspace_membership.call_count, 2)

    @patch('community.tasks.slack_membership.time.sleep')
    @patch('community.tasks.slack_membership.get_community_service')
    def test_self_throttles_between_calls(self, mock_get_service, mock_sleep):
        # Three users -> two inter-call sleeps of >= 1.5s.
        for i in range(3):
            self._make_user(f'u{i}@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        refresh_slack_membership()

        # Sleep called exactly twice (between the three calls), each
        # at >= 1.5s so we stay under Slack's 50 RPM Tier 4 limit.
        self.assertEqual(mock_sleep.call_count, 2)
        for call in mock_sleep.call_args_list:
            (slept_for,) = call.args
            self.assertGreaterEqual(slept_for, 1.5)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_audit_log_written_on_state_transition_only(self, mock_get_service):
        user = self._make_user('flip@test.com')
        # Pre-state: previously checked as not_member.
        user.slack_checked_at = timezone.now() - timezone.timedelta(days=10)
        user.slack_member = False
        user.save(update_fields=['slack_checked_at', 'slack_member'])

        svc = MagicMock()
        # First call: still not_member — no transition, no log.
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        self.assertEqual(
            CommunityAuditLog.objects.filter(user=user, action='check').count(),
            0,
            'Stale re-check returning the same value must not write an audit row',
        )

        # Now flip to member — must log.
        user.slack_checked_at = timezone.now() - timezone.timedelta(days=10)
        user.save(update_fields=['slack_checked_at'])
        svc.check_workspace_membership.return_value = ('member', 'U_NEW')

        refresh_slack_membership(sleep_seconds=0)

        log = CommunityAuditLog.objects.get(user=user, action='check')
        details = json.loads(log.details)
        self.assertFalse(details['previous'])
        self.assertTrue(details['new'])

    @patch('community.tasks.slack_membership.get_community_service')
    def test_first_ever_check_is_logged(self, mock_get_service):
        user = self._make_user('firstcheck@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_X')
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        log = CommunityAuditLog.objects.get(user=user, action='check')
        details = json.loads(log.details)
        self.assertTrue(details['new'])

    @patch('community.tasks.slack_membership.get_community_service')
    def test_does_not_overwrite_existing_slack_user_id(self, mock_get_service):
        user = self._make_user('hasid@test.com')
        user.slack_user_id = 'U_OAUTH'
        user.save(update_fields=['slack_user_id'])

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_API')
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        # OAuth-supplied ID wins; lookup ID is ignored when one is set.
        self.assertEqual(user.slack_user_id, 'U_OAUTH')
        self.assertTrue(user.slack_member)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_unconfigured_integration_is_safe_noop(self, mock_get_service):
        """When the service returns unknown for everyone, nothing crashes."""
        for i in range(3):
            self._make_user(f'u{i}@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('unknown', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        # All users still NULL, no audit logs.
        self.assertEqual(result['unknown'], 3)
        self.assertEqual(
            CommunityAuditLog.objects.filter(action='check').count(), 0,
        )
        for user in User.objects.filter(email__endswith='@test.com'):
            self.assertIsNone(user.slack_checked_at)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_unhandled_exception_in_service_counts_as_unknown(self, mock_get_service):
        user = self._make_user('boom@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.side_effect = SlackAPIError('boom')
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertIsNone(user.slack_checked_at)
        self.assertEqual(result['unknown'], 1)


class TagMirrorTest(TestCase):
    """Forward-compat with #354: mirror to User.tags if the field exists."""

    def test_no_tags_field_makes_tag_write_a_noop(self):
        # User._meta does NOT have 'tags' (since #354 hasn't shipped),
        # so _set_slack_member_tag must be a silent no-op.
        user = User.objects.create_user(email='notagsfield@test.com')
        # Should not raise:
        task_module._set_slack_member_tag(user, True)
        task_module._set_slack_member_tag(user, False)

    def test_has_tags_field_returns_false_today(self):
        # Document the current state: when #354 hasn't shipped, the
        # detector returns False and the mirror is skipped.
        self.assertFalse(task_module._has_tags_field())
