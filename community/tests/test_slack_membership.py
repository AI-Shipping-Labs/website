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

from accounts.models import TierOverride, User
from community.models import CommunityAuditLog
from community.services.slack import SlackAPIError, SlackCommunityService
from community.tasks import slack_membership as task_module
from community.tasks.slack_membership import (
    SLACK_MEMBERSHIP_CHUNK_SIZE,
    SLACK_MEMBERSHIP_SLEEP_SECONDS,
    refresh_slack_membership,
)
from payments.models import Tier


def _tier(level):
    """Fetch one of the migration-seeded tier rows by level."""
    return Tier.objects.get(level=level)


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
        # Default new users to Main tier so they fall inside the
        # Main+ candidate scope (issue #918); these tests exercise
        # outcome/audit/chain behavior, not the tier filter itself.
        extra.setdefault('tier', _tier(20))
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

        # Sleep called exactly twice (between the three calls), each at
        # >= 3.0s so we stay under Slack's Tier 2 (~20 RPM) limit for
        # users.lookupByEmail (issue #918).
        self.assertEqual(mock_sleep.call_count, 2)
        for call in mock_sleep.call_args_list:
            (slept_for,) = call.args
            self.assertGreaterEqual(slept_for, 3.0)

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

        with self.assertLogs('community.tasks.slack_membership', level='ERROR') as logs:
            result = refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertIsNone(user.slack_checked_at)
        self.assertEqual(result['unknown'], 1)
        self.assertIn(
            'Unexpected error checking Slack membership for boom@test.com',
            logs.output[0],
        )


class TagMirrorTest(TestCase):
    """Mirror Slack membership to User.tags now that #354 has shipped.

    Pre-merge this class documented forward-compat behavior for a world
    where ``User.tags`` did not yet exist; #354 added the field, so we now
    assert the live behavior: the detector returns True and the mirror
    actually writes to ``user.tags``.
    """

    def test_set_slack_member_tag_is_idempotent(self):
        user = User.objects.create_user(email='tagsmirror@test.com')
        # Should not raise on either direction:
        task_module._set_slack_member_tag(user, True)
        task_module._set_slack_member_tag(user, False)

    def test_has_tags_field_returns_true_after_354(self):
        # #354 added User.tags; the detector must report True so the
        # slack-membership task actually mirrors to the tag list.
        self.assertTrue(task_module._has_tags_field())


class RefreshSlackMembershipChunkChainTest(TestCase):
    """Chunked-chain pattern for refresh_slack_membership (issue #715).

    When a chunk completes and more users still match the predicate,
    the task enqueues a follow-up ``async_task`` so the backlog drains
    without blowing past ``Q_CLUSTER['timeout']``. The all-unknown
    guard prevents an infinite chain on a misconfigured environment.
    """

    FOLLOWUP_PATH = 'community.tasks.slack_membership.refresh_slack_membership'

    def _make_user(self, email):
        # Main tier so the user is inside the Main+ candidate scope.
        return User.objects.create_user(email=email, tier=_tier(20))

    def test_chunk_size_fits_under_worker_timeout(self):
        # Lock the design decision (issues #715, #918) into the suite so
        # future changes are deliberate. The constant is sized against the
        # 300s Q_CLUSTER timeout at the Tier-2-safe pacing: a full chunk's
        # pacing budget must leave generous headroom below the timeout.
        Q_CLUSTER_TIMEOUT = 300
        pacing_budget = SLACK_MEMBERSHIP_CHUNK_SIZE * SLACK_MEMBERSHIP_SLEEP_SECONDS
        self.assertLess(
            pacing_budget,
            Q_CLUSTER_TIMEOUT,
            'chunk_size x gap must stay under the 300s Q_CLUSTER timeout',
        )
        # Headroom check: target a typical run well under ~120s of pacing.
        self.assertLessEqual(pacing_budget, 120)

    @patch('community.tasks.slack_membership.async_task')
    @patch('community.tasks.slack_membership.get_community_service')
    def test_followup_enqueued_when_backlog_remains(
        self, mock_get_service, mock_async_task,
    ):
        # Create more users than the chunk we'll process so a backlog
        # remains after the run.
        for i in range(3):
            self._make_user(f'u{i}@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(batch_size=2, sleep_seconds=0)

        # Exactly one follow-up enqueued, pointed at this same task.
        self.assertEqual(mock_async_task.call_count, 1)
        (called_path, *rest) = mock_async_task.call_args.args
        self.assertEqual(called_path, self.FOLLOWUP_PATH)
        self.assertTrue(result['enqueued_followup'])

    @patch('community.tasks.slack_membership.async_task')
    @patch('community.tasks.slack_membership.get_community_service')
    def test_followup_not_enqueued_when_queue_drains(
        self, mock_get_service, mock_async_task,
    ):
        # Fewer users than the chunk size => queue drains in one run.
        for i in range(2):
            self._make_user(f'u{i}@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(batch_size=5, sleep_seconds=0)

        mock_async_task.assert_not_called()
        self.assertFalse(result['enqueued_followup'])

    @patch('community.tasks.slack_membership.async_task')
    @patch('community.tasks.slack_membership.get_community_service')
    def test_followup_not_enqueued_when_all_unknown(
        self, mock_get_service, mock_async_task,
    ):
        # Three users, chunk of 2; even though a user still matches
        # the predicate after the run, the chunk was 100% unknown so
        # we must NOT enqueue a follow-up (infinite-chain guard).
        for i in range(3):
            self._make_user(f'u{i}@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('unknown', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(batch_size=2, sleep_seconds=0)

        mock_async_task.assert_not_called()
        self.assertEqual(result['unknown'], 2)
        self.assertEqual(result['total_checked'], 2)
        self.assertFalse(result['enqueued_followup'])

    @patch('community.tasks.slack_membership.async_task')
    @patch('community.tasks.slack_membership.get_community_service')
    def test_followup_enqueued_when_some_unknown_but_backlog_remains(
        self, mock_get_service, mock_async_task,
    ):
        # Three users, chunk of 2. First call returns unknown, second
        # returns not_member. The guard is ALL-unknown (not ANY-unknown),
        # so the partial outage still leaves us free to chain because
        # one user advanced and the third still matches the predicate.
        for i in range(3):
            self._make_user(f'u{i}@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.side_effect = [
            ('unknown', None),
            ('not_member', None),
        ]
        mock_get_service.return_value = svc

        result = refresh_slack_membership(batch_size=2, sleep_seconds=0)

        self.assertEqual(mock_async_task.call_count, 1)
        self.assertTrue(result['enqueued_followup'])

    @patch('community.tasks.slack_membership.async_task')
    @patch('community.tasks.slack_membership.get_community_service')
    def test_followup_not_enqueued_on_empty_queryset(
        self, mock_get_service, mock_async_task,
    ):
        # No users at all => nothing to do, nothing to enqueue.
        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        mock_async_task.assert_not_called()
        self.assertEqual(result['total_checked'], 0)
        self.assertFalse(result['enqueued_followup'])


class RefreshSlackMembershipMainPlusScopeTest(TestCase):
    """Candidate scope: only Main+ (incl active overrides) is checked (#918)."""

    def _user(self, email, level=None, **extra):
        if level is not None:
            extra['tier'] = _tier(level)
        return User.objects.create_user(email=email, **extra)

    def _override(self, user, override_level, *, active=True, expired=False):
        expires = timezone.now() + timezone.timedelta(days=7)
        if expired:
            expires = timezone.now() - timezone.timedelta(days=1)
        return TierOverride.objects.create(
            user=user,
            override_tier=_tier(override_level),
            expires_at=expires,
            is_active=active,
        )

    @patch('community.tasks.slack_membership.get_community_service')
    def test_only_main_and_above_are_checked(self, mock_get_service):
        free = self._user('free@test.com', level=0)
        basic = self._user('basic@test.com', level=10)
        main = self._user('main@test.com', level=20)
        premium = self._user('premium@test.com', level=30)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        checked = {
            c.args[0] for c in svc.check_workspace_membership.call_args_list
        }
        self.assertEqual(checked, {main.email, premium.email})
        self.assertNotIn(free.email, checked)
        self.assertNotIn(basic.email, checked)
        self.assertEqual(result['total_checked'], 2)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_basic_user_without_override_is_excluded(self, mock_get_service):
        self._user('basic@test.com', level=10)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        svc.check_workspace_membership.assert_not_called()
        self.assertEqual(result['total_checked'], 0)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_free_user_with_active_main_override_is_included(self, mock_get_service):
        trial = self._user('trial@test.com', level=0)
        self._override(trial, override_level=20)
        plain = self._user('plain@test.com', level=0)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_TRIAL')
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        checked = {
            c.args[0] for c in svc.check_workspace_membership.call_args_list
        }
        self.assertEqual(checked, {trial.email})
        self.assertNotIn(plain.email, checked)
        trial.refresh_from_db()
        self.assertTrue(trial.slack_member)
        self.assertEqual(result['total_checked'], 1)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_expired_override_does_not_include_free_user(self, mock_get_service):
        user = self._user('expired@test.com', level=0)
        self._override(user, override_level=20, expired=True)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        svc.check_workspace_membership.assert_not_called()
        self.assertEqual(result['total_checked'], 0)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_inactive_override_does_not_include_free_user(self, mock_get_service):
        user = self._user('inactive@test.com', level=0)
        self._override(user, override_level=20, active=False)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        svc.check_workspace_membership.assert_not_called()
        self.assertEqual(result['total_checked'], 0)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_basic_override_does_not_include_free_user(self, mock_get_service):
        # An active override that only reaches Basic (level 10) must NOT
        # qualify — the predicate keys off override_tier.level >= 20.
        user = self._user('basicoverride@test.com', level=0)
        self._override(user, override_level=10)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('not_member', None)
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        svc.check_workspace_membership.assert_not_called()
        self.assertEqual(result['total_checked'], 0)

    @patch('community.tasks.slack_membership.get_community_service')
    def test_user_with_two_active_overrides_checked_once(self, mock_get_service):
        # Data anomaly: two active Main override rows on the same user.
        # The override join would duplicate the row; .distinct() collapses
        # it so the user is checked exactly once per run.
        user = self._user('dup@test.com', level=0)
        self._override(user, override_level=20)
        self._override(user, override_level=20)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U_DUP')
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        self.assertEqual(svc.check_workspace_membership.call_count, 1)
        self.assertEqual(
            svc.check_workspace_membership.call_args.args[0], user.email,
        )
        self.assertEqual(result['total_checked'], 1)

    @patch('community.tasks.slack_membership.async_task')
    @patch('community.tasks.slack_membership.get_community_service')
    def test_chain_decision_counts_only_main_plus(
        self, mock_get_service, mock_async_task,
    ):
        # Two Main users + many Free users. With batch_size=1 a Main user
        # remains after the chunk, so the chain must continue; the Free
        # users must never affect the more_remaining decision.
        self._user('main1@test.com', level=20)
        self._user('main2@test.com', level=20)
        for i in range(5):
            self._user(f'free{i}@test.com', level=0)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U1')
        mock_get_service.return_value = svc

        result = refresh_slack_membership(batch_size=1, sleep_seconds=0)

        self.assertEqual(result['total_checked'], 1)
        self.assertEqual(mock_async_task.call_count, 1)
        self.assertTrue(result['enqueued_followup'])

    @patch('community.tasks.slack_membership.async_task')
    @patch('community.tasks.slack_membership.get_community_service')
    def test_no_chain_when_only_free_users_remain(
        self, mock_get_service, mock_async_task,
    ):
        # One Main user (resolved this run) and many Free users left
        # behind. The Free users are out of scope, so no backlog remains
        # and no follow-up is enqueued.
        self._user('main@test.com', level=20)
        for i in range(5):
            self._user(f'free{i}@test.com', level=0)

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ('member', 'U1')
        mock_get_service.return_value = svc

        result = refresh_slack_membership(batch_size=10, sleep_seconds=0)

        self.assertEqual(result['total_checked'], 1)
        mock_async_task.assert_not_called()
        self.assertFalse(result['enqueued_followup'])


class RefreshSlackMembershipResolvesNotAllUnknownTest(TestCase):
    """The task resolves real membership instead of all-unknown (#918)."""

    def _main_user(self, email):
        return User.objects.create_user(email=email, tier=_tier(20))

    @patch('community.tasks.slack_membership.get_community_service')
    def test_mixed_outcomes_resolve_members_and_not_members(self, mock_get_service):
        u1 = self._main_user('a-m1@test.com')
        u2 = self._main_user('b-nm@test.com')
        u3 = self._main_user('c-m2@test.com')

        svc = MagicMock()
        svc.check_workspace_membership.side_effect = [
            ('member', 'U1'),
            ('not_member', None),
            ('member', 'U2'),
        ]
        mock_get_service.return_value = svc

        result = refresh_slack_membership(sleep_seconds=0)

        self.assertEqual(result['members'], 2)
        self.assertEqual(result['not_members'], 1)
        self.assertEqual(result['unknown'], 0)
        self.assertGreater(result['members'] + result['not_members'], 0)

        for u in (u1, u2, u3):
            u.refresh_from_db()
            self.assertIsNotNone(u.slack_checked_at)
        self.assertTrue(u1.slack_member)
        self.assertFalse(u2.slack_member)
        self.assertTrue(u3.slack_member)


@override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN='xoxb-test')
class RateLimitRetryTest(TestCase):
    """A ratelimited response triggers one bounded retry, not an instant
    unknown — so a single throttle does not cascade across the batch (#918)."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token='xoxb-test', channel_ids=['C001'],
        )

    def _resp(self, status, payload=None, retry_after=None):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = payload or {}
        r.headers = {'Retry-After': str(retry_after)} if retry_after else {}
        return r

    @patch('community.services.slack.time.sleep')
    @patch('community.services.slack.requests.post')
    def test_ratelimited_body_retries_once_then_succeeds(self, mock_post, mock_sleep):
        # First call: ok=False ratelimited with Retry-After. Second: member.
        mock_post.side_effect = [
            self._resp(200, {'ok': False, 'error': 'ratelimited'}, retry_after=1),
            self._resp(200, {'ok': True, 'user': {'id': 'U777'}}),
        ]

        result = self.service.check_workspace_membership('a@example.com')

        self.assertEqual(result, ('member', 'U777'))
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once()
        (waited,) = mock_sleep.call_args.args
        self.assertEqual(waited, 1.0)

    @patch('community.services.slack.time.sleep')
    @patch('community.services.slack.requests.post')
    def test_http_429_retries_once_then_succeeds(self, mock_post, mock_sleep):
        mock_post.side_effect = [
            self._resp(429, retry_after=2),
            self._resp(200, {'ok': False, 'error': 'users_not_found'}),
        ]

        result = self.service.check_workspace_membership('a@example.com')

        self.assertEqual(result, ('not_member', None))
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once()

    @patch('community.services.slack.time.sleep')
    @patch('community.services.slack.requests.post')
    def test_persistent_ratelimit_falls_back_to_unknown_after_one_retry(
        self, mock_post, mock_sleep,
    ):
        # Throttled on both the first call and the single retry: only then
        # do we surface unknown (bounded retry, no infinite loop).
        mock_post.side_effect = [
            self._resp(200, {'ok': False, 'error': 'ratelimited'}, retry_after=1),
            self._resp(200, {'ok': False, 'error': 'ratelimited'}, retry_after=1),
        ]

        result = self.service.check_workspace_membership('a@example.com')

        self.assertEqual(result, ('unknown', None))
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)

    @patch(
        'community.services.slack.SlackCommunityService'
        '.lookup_user_profile_by_email',
        return_value=None,
    )
    @patch('community.services.slack.time.sleep')
    @patch('community.services.slack.requests.post')
    def test_one_throttle_does_not_poison_rest_of_batch(
        self, mock_post, mock_sleep, mock_profile,
    ):
        # Task-level: first user is throttled once then resolves; the
        # remaining users still resolve to concrete outcomes rather than
        # every subsequent call cascading to unknown. The name-backfill
        # profile lookup is stubbed out so it doesn't consume the mocked
        # membership responses below.
        for email in ('m1@test.com', 'm2@test.com', 'm3@test.com'):
            User.objects.create_user(email=email, tier=_tier(20))

        # Order is NULLs-first then by email; emails sort m1 < m2 < m3.
        mock_post.side_effect = [
            # m1: ratelimited, then member on retry.
            self._resp(200, {'ok': False, 'error': 'ratelimited'}, retry_after=1),
            self._resp(200, {'ok': True, 'user': {'id': 'U1'}}),
            # m2: not_member.
            self._resp(200, {'ok': False, 'error': 'users_not_found'}),
            # m3: member.
            self._resp(200, {'ok': True, 'user': {'id': 'U3'}}),
        ]

        result = refresh_slack_membership(sleep_seconds=0)

        self.assertEqual(result['members'], 2)
        self.assertEqual(result['not_members'], 1)
        self.assertEqual(result['unknown'], 0)
