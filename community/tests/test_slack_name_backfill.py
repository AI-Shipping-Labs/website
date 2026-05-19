"""Tests for the Slack probe name backfill (issue #699).

Covers:

- ``SlackCommunityService.lookup_user_profile_by_email`` returns the
  profile dict (or None for not-found).
- ``refresh_slack_membership`` calls the profile lookup for "member"
  users and folds ``first_name`` / ``last_name`` into the same save.
- ``real_name`` fallback when ``profile.first_name`` /
  ``profile.last_name`` are blank.
- Do-not-overwrite rule against existing names.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from accounts.models import User
from community.services.slack import SlackCommunityService
from community.tasks.slack_membership import refresh_slack_membership


@override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN='xoxb-test')
class LookupUserProfileByEmailTest(TestCase):
    """``users.lookupByEmail`` returns id + real_name + profile.{first,last}_name."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token='xoxb-test',
            channel_ids=['C001'],
        )

    @patch('community.services.slack.requests.post')
    def test_returns_profile_dict_on_match(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'ok': True,
            'user': {
                'id': 'U999',
                'real_name': 'Alex Grigorev',
                'profile': {
                    'first_name': 'Alex',
                    'last_name': 'Grigorev',
                    'real_name': 'Alex Grigorev',
                },
            },
        }
        mock_post.return_value = mock_response

        result = self.service.lookup_user_profile_by_email('a@test.com')

        self.assertEqual(result['id'], 'U999')
        self.assertEqual(result['first_name'], 'Alex')
        self.assertEqual(result['last_name'], 'Grigorev')
        self.assertEqual(result['real_name'], 'Alex Grigorev')

    @patch('community.services.slack.requests.post')
    def test_users_not_found_returns_none(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'ok': False, 'error': 'users_not_found',
        }
        mock_post.return_value = mock_response

        result = self.service.lookup_user_profile_by_email('missing@test.com')
        self.assertIsNone(result)

    @patch('community.services.slack.requests.post')
    def test_real_name_falls_back_to_profile(self, mock_post):
        """Some workspaces only populate profile.real_name."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'ok': True,
            'user': {
                'id': 'U_RN',
                'profile': {
                    'real_name': 'Profile Realname',
                    'first_name': '',
                    'last_name': '',
                },
            },
        }
        mock_post.return_value = mock_response

        result = self.service.lookup_user_profile_by_email('rn@test.com')
        self.assertEqual(result['real_name'], 'Profile Realname')


class RefreshSlackMembershipNameBackfillTest(TestCase):
    """``refresh_slack_membership`` folds Slack profile names into save."""

    @patch('community.tasks.slack_membership.get_community_service')
    def test_member_outcome_backfills_first_and_last_name(self, mock_get_service):
        user = User.objects.create_user(email='backfill@test.com')
        self.assertEqual(user.first_name, '')
        self.assertEqual(user.last_name, '')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_BF')
        svc.lookup_user_profile_by_email.return_value = {
            'id': 'U_BF',
            'first_name': 'Alex',
            'last_name': 'Grigorev',
            'real_name': 'Alex Grigorev',
        }
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Alex')
        self.assertEqual(user.last_name, 'Grigorev')
        # Membership flags still set.
        self.assertTrue(user.slack_member)
        self.assertEqual(user.slack_user_id, 'U_BF')

    @patch('community.tasks.slack_membership.get_community_service')
    def test_real_name_fallback_when_profile_first_last_blank(self, mock_get_service):
        user = User.objects.create_user(email='rnfallback@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_RN')
        svc.lookup_user_profile_by_email.return_value = {
            'id': 'U_RN',
            'first_name': '',
            'last_name': '',
            'real_name': 'Alex Grigorev',
        }
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Alex')
        self.assertEqual(user.last_name, 'Grigorev')

    @patch('community.tasks.slack_membership.get_community_service')
    def test_single_token_real_name_fills_first_only(self, mock_get_service):
        user = User.objects.create_user(email='single@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_S')
        svc.lookup_user_profile_by_email.return_value = {
            'id': 'U_S',
            'first_name': '',
            'last_name': '',
            'real_name': 'Madonna',
        }
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Madonna')
        self.assertEqual(user.last_name, '')

    @patch('community.tasks.slack_membership.get_community_service')
    def test_does_not_overwrite_existing_name(self, mock_get_service):
        user = User.objects.create_user(
            email='kept@test.com',
            first_name='Custom',
            last_name='Edit',
        )

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_K')
        svc.lookup_user_profile_by_email.return_value = {
            'id': 'U_K',
            'first_name': 'Wrong',
            'last_name': 'Name',
            'real_name': 'Wrong Name',
        }
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Custom')
        self.assertEqual(user.last_name, 'Edit')

    @patch('community.tasks.slack_membership.get_community_service')
    def test_profile_lookup_failure_does_not_break_membership_update(
        self, mock_get_service,
    ):
        """A profile lookup exception must not undo the membership write."""
        user = User.objects.create_user(email='boom@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_X')
        svc.lookup_user_profile_by_email.side_effect = RuntimeError('boom')
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        # Membership still flipped.
        self.assertTrue(user.slack_member)
        self.assertEqual(user.slack_user_id, 'U_X')
        # Name fields untouched.
        self.assertEqual(user.first_name, '')
        self.assertEqual(user.last_name, '')

    @patch('community.tasks.slack_membership.get_community_service')
    def test_not_member_outcome_does_not_call_profile_lookup(
        self, mock_get_service,
    ):
        User.objects.create_user(email='not@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        refresh_slack_membership(sleep_seconds=0)

        # Profile lookup must not be called for non-members.
        svc.lookup_user_profile_by_email.assert_not_called()
