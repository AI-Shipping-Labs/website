from unittest.mock import MagicMock

from django.test import TestCase

from accounts.models import User
from accounts.signals import (
    _extract_slack_user_id,
    set_slack_user_id_on_social_login,
    set_slack_user_id_on_social_signup,
)


def _make_sociallogin(provider, extra_data, user):
    """Build a minimal mock sociallogin object for signal testing."""
    sociallogin = MagicMock()
    sociallogin.account.provider = provider
    sociallogin.account.extra_data = extra_data
    sociallogin.user = user
    return sociallogin


class ExtractSlackUserIdTest(TestCase):
    """Tests for the _extract_slack_user_id helper."""

    def test_extracts_from_top_level_claim(self):
        data = {"https://slack.com/user_id": "U12345"}
        self.assertEqual(_extract_slack_user_id(data), "U12345")

    def test_extracts_from_nested_user_object(self):
        data = {"user": {"id": "U67890", "name": "Test"}}
        self.assertEqual(_extract_slack_user_id(data), "U67890")

    def test_top_level_claim_takes_precedence(self):
        data = {
            "https://slack.com/user_id": "U_TOP",
            "user": {"id": "U_NESTED"},
        }
        self.assertEqual(_extract_slack_user_id(data), "U_TOP")

    def test_returns_empty_string_when_missing(self):
        data = {"email": "test@example.com"}
        self.assertEqual(_extract_slack_user_id(data), "")

    def test_returns_empty_string_for_empty_dict(self):
        self.assertEqual(_extract_slack_user_id({}), "")


class SlackSignalSetUserIdTest(TestCase):
    """Tests for the set_slack_user_id_on_social_login signal handler."""

    def test_slack_login_sets_slack_user_id(self):
        """After a Slack social login, the user's slack_user_id field
        is populated from the Slack extra data."""
        user = User.objects.create_user(email="slack@example.com")
        self.assertEqual(user.slack_user_id, "")

        extra_data = {
            "https://slack.com/user_id": "U_SLACK_123",
            "https://slack.com/team_id": "T_TEAM_123",
            "email": "slack@example.com",
            "email_verified": True,
        }
        sociallogin = _make_sociallogin("slack", extra_data, user)

        set_slack_user_id_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "U_SLACK_123")

    def test_slack_login_links_existing_account_by_email(self):
        """When a user with an existing account logs in via Slack, the
        Slack identity is linked and slack_user_id is set."""
        user = User.objects.create_user(
            email="existing@example.com", password="testpass123"
        )
        self.assertEqual(user.slack_user_id, "")

        extra_data = {
            "https://slack.com/user_id": "U_EXISTING",
            "https://slack.com/team_id": "T_TEAM",
            "email": "existing@example.com",
            "email_verified": True,
        }
        sociallogin = _make_sociallogin("slack", extra_data, user)

        set_slack_user_id_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "U_EXISTING")

    def test_slack_login_does_not_overwrite_existing_slack_user_id(self):
        """If the user already has a slack_user_id, do not overwrite it."""
        user = User.objects.create_user(email="already@example.com")
        user.slack_user_id = "U_ORIGINAL"
        user.save(update_fields=["slack_user_id"])

        extra_data = {"https://slack.com/user_id": "U_NEW"}
        sociallogin = _make_sociallogin("slack", extra_data, user)

        set_slack_user_id_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "U_ORIGINAL")

    def test_non_slack_provider_is_ignored(self):
        """Signal handler ignores non-Slack providers."""
        user = User.objects.create_user(email="google@example.com")
        extra_data = {"sub": "google-id-123"}
        sociallogin = _make_sociallogin("google", extra_data, user)

        set_slack_user_id_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "")

    def test_unsaved_user_is_skipped(self):
        """Signal handler does nothing if the user has no pk (not yet saved)."""
        user = User(email="unsaved@example.com")
        extra_data = {"https://slack.com/user_id": "U_UNSAVED"}
        sociallogin = _make_sociallogin("slack", extra_data, user)

        # Should not raise
        set_slack_user_id_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

    def test_slack_login_with_nested_user_id(self):
        """Uses the fallback user.id field when top-level claim is absent."""
        user = User.objects.create_user(email="nested@example.com")
        extra_data = {
            "user": {"id": "U_NESTED_ID", "name": "Nested User"},
            "email": "nested@example.com",
        }
        sociallogin = _make_sociallogin("slack", extra_data, user)

        set_slack_user_id_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "U_NESTED_ID")


class SlackSignalSetUserIdOnSignupTest(TestCase):
    """Tests for the set_slack_user_id_on_social_signup signal handler."""

    def test_slack_signup_sets_slack_user_id(self):
        """After a new Slack social account is added, slack_user_id is set."""
        user = User.objects.create_user(email="newslack@example.com")
        extra_data = {
            "https://slack.com/user_id": "U_NEW_SLACK",
            "email": "newslack@example.com",
            "email_verified": True,
        }
        sociallogin = _make_sociallogin("slack", extra_data, user)

        set_slack_user_id_on_social_signup(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "U_NEW_SLACK")

    def test_non_slack_signup_ignored(self):
        """Signal handler ignores non-Slack providers on signup."""
        user = User.objects.create_user(email="github@example.com")
        extra_data = {"login": "githubuser"}
        sociallogin = _make_sociallogin("github", extra_data, user)

        set_slack_user_id_on_social_signup(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "")


class SlackOAuthSetsSlackMemberTest(TestCase):
    """Issue #358: Slack OAuth proves workspace membership."""

    def test_slack_login_sets_slack_member_true(self):
        user = User.objects.create_user(email='oauth@example.com')
        self.assertFalse(user.slack_member)
        self.assertIsNone(user.slack_checked_at)

        extra_data = {
            'https://slack.com/user_id': 'U_OAUTH',
            'email': 'oauth@example.com',
        }
        sociallogin = _make_sociallogin('slack', extra_data, user)

        set_slack_user_id_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        self.assertIsNotNone(user.slack_checked_at)

    def test_slack_signup_sets_slack_member_true(self):
        user = User.objects.create_user(email='newoauth@example.com')

        extra_data = {
            'https://slack.com/user_id': 'U_NEWOAUTH',
            'email': 'newoauth@example.com',
        }
        sociallogin = _make_sociallogin('slack', extra_data, user)

        set_slack_user_id_on_social_signup(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        self.assertIsNotNone(user.slack_checked_at)

    def test_non_slack_provider_does_not_flip_slack_member(self):
        user = User.objects.create_user(email='google@example.com')
        extra_data = {'sub': 'google-id'}
        sociallogin = _make_sociallogin('google', extra_data, user)

        set_slack_user_id_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertFalse(user.slack_member)
        self.assertIsNone(user.slack_checked_at)


class SlackLoginMarksEmailVerifiedTest(TestCase):
    """Verify the existing mark_email_verified_on_social_login signal handler
    also fires for Slack logins (it works for all OAuth providers)."""

    def test_slack_login_marks_email_verified(self):
        from accounts.signals import mark_email_verified_on_social_login

        user = User.objects.create_user(email="verify@example.com")
        self.assertFalse(user.email_verified)

        extra_data = {
            "https://slack.com/user_id": "U_VERIFY",
            "email": "verify@example.com",
            "email_verified": True,
        }
        sociallogin = _make_sociallogin("slack", extra_data, user)

        mark_email_verified_on_social_login(
            sender=None, request=MagicMock(), sociallogin=sociallogin
        )

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
