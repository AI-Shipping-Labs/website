"""Tests for the OAuth name-capture signal (issue #699).

Covers the new ``populate_name_from_social`` handler wired to both
``pre_social_login`` and ``social_account_added``. For each supported
provider:

- Google (OIDC) — ``given_name`` + ``family_name``, fallback to
  combined ``name``.
- GitHub — combined ``name`` only (provider does not split).
- Slack (OIDC) — same as Google with fallback to combined ``name``.

Plus the cross-cutting "do not overwrite" rule and the
"unsaved user" guard.
"""

from unittest.mock import MagicMock

from django.test import TestCase

from accounts.models import User
from accounts.signals import populate_name_from_social


def _make_sociallogin(provider, extra_data, user):
    """Build a minimal mock sociallogin object for signal testing."""
    sociallogin = MagicMock()
    sociallogin.account.provider = provider
    sociallogin.account.extra_data = extra_data
    sociallogin.user = user
    return sociallogin


class GoogleOAuthNameTest(TestCase):
    """Google ships ``given_name`` and ``family_name`` as OIDC claims."""

    def test_google_pre_split_fills_first_and_last(self):
        user = User.objects.create_user(email="g@test.com")
        extra = {
            "sub": "google-id-123",
            "given_name": "Alex",
            "family_name": "Grigorev",
            "email": "g@test.com",
        }
        sociallogin = _make_sociallogin("google", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_google_falls_back_to_combined_name(self):
        """When OIDC response lacks given/family but ships ``name``."""
        user = User.objects.create_user(email="gfallback@test.com")
        extra = {
            "sub": "google-id",
            "name": "Alex Grigorev",
            "email": "gfallback@test.com",
        }
        sociallogin = _make_sociallogin("google", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_google_does_not_overwrite_existing_name(self):
        user = User.objects.create_user(
            email="gpre@test.com", first_name="Custom", last_name="Edit",
        )
        extra = {"given_name": "Alex", "family_name": "Grigorev"}
        sociallogin = _make_sociallogin("google", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Custom")
        self.assertEqual(user.last_name, "Edit")


class GitHubOAuthNameTest(TestCase):
    """GitHub only fills the combined ``name`` claim."""

    def test_github_combined_name_splits_on_last_whitespace(self):
        user = User.objects.create_user(email="gh@test.com")
        extra = {
            "login": "alexg",
            "name": "Alex Grigorev",
            "email": "gh@test.com",
        }
        sociallogin = _make_sociallogin("github", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_github_three_token_name_splits_on_last_whitespace(self):
        user = User.objects.create_user(email="gh3@test.com")
        extra = {"login": "salvador", "name": "Salvador Castillo Raya"}
        sociallogin = _make_sociallogin("github", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Salvador Castillo")
        self.assertEqual(user.last_name, "Raya")

    def test_github_single_token_name_fills_first_only(self):
        user = User.objects.create_user(email="ghmadonna@test.com")
        extra = {"login": "madonna", "name": "Madonna"}
        sociallogin = _make_sociallogin("github", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Madonna")
        self.assertEqual(user.last_name, "")

    def test_github_missing_name_claim_is_noop(self):
        user = User.objects.create_user(email="ghnone@test.com")
        extra = {"login": "noname"}
        sociallogin = _make_sociallogin("github", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")


class SlackOAuthNameTest(TestCase):
    """Slack OIDC ships ``given_name`` / ``family_name`` with a combined fallback."""

    def test_slack_pre_split_fills_first_and_last(self):
        user = User.objects.create_user(email="s@test.com")
        extra = {
            "https://slack.com/user_id": "U_S",
            "given_name": "Alex",
            "family_name": "Grigorev",
            "email": "s@test.com",
        }
        sociallogin = _make_sociallogin("slack", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_slack_falls_back_to_combined_name(self):
        user = User.objects.create_user(email="sfallback@test.com")
        extra = {
            "https://slack.com/user_id": "U_S",
            "name": "Alex Grigorev",
        }
        sociallogin = _make_sociallogin("slack", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")


class PopulateNameFromSocialEdgeCasesTest(TestCase):
    """Cross-provider edge cases."""

    def test_unsaved_user_is_skipped(self):
        """Handler does nothing if the user has no pk yet."""
        user = User(email="unsaved@test.com")
        extra = {"given_name": "Alex", "family_name": "Grigorev"}
        sociallogin = _make_sociallogin("google", extra, user)

        # Must not raise.
        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )
        self.assertEqual(user.first_name, "")

    def test_empty_extra_data_is_noop(self):
        user = User.objects.create_user(email="empty-extra@test.com")
        sociallogin = _make_sociallogin("google", {}, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")

    def test_unknown_provider_is_ignored(self):
        user = User.objects.create_user(email="unknown@test.com")
        extra = {"given_name": "Alex", "family_name": "Grigorev"}
        sociallogin = _make_sociallogin("twitter", extra, user)

        populate_name_from_social(
            sender=None, request=MagicMock(), sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")


class PopulateNameSignalWiringTest(TestCase):
    """Verify the handler is wired to both signals in apps.py."""

    def test_handler_connected_to_both_signals(self):
        import weakref

        from allauth.socialaccount.signals import (
            pre_social_login,
            social_account_added,
        )

        # ``Signal.receivers`` is a list of tuples; entry[1] is the
        # registered function (or a weakref to it when ``weak=True``).
        def _collect(signal):
            out = set()
            for entry in signal.receivers:
                receiver = entry[1]
                if isinstance(receiver, weakref.ReferenceType):
                    deref = receiver()
                    if deref is not None:
                        out.add(deref)
                else:
                    out.add(receiver)
            return out

        self.assertIn(populate_name_from_social, _collect(pre_social_login))
        self.assertIn(populate_name_from_social, _collect(social_account_added))
