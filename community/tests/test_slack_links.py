"""Tests for the shared ``build_slack_profile_url`` helper (issue #700).

The helper used to live in ``studio.views.users`` as a private
function. Issue #700 lifts it into ``community.services.slack_links``
so both the Studio user-detail page and the member-facing /account/
Slack card can share one implementation.
"""

from django.test import SimpleTestCase

from community.services.slack_links import build_slack_profile_url


class BuildSlackProfileUrlTest(SimpleTestCase):
    """``build_slack_profile_url`` returns the canonical web URL or ''."""

    def test_returns_url_when_both_ids_present(self):
        self.assertEqual(
            build_slack_profile_url("U01ADA123", "T01TEAM456"),
            "https://app.slack.com/client/T01TEAM456/U01ADA123",
        )

    def test_returns_empty_when_user_id_missing(self):
        self.assertEqual(build_slack_profile_url("", "T01TEAM456"), "")
        self.assertEqual(build_slack_profile_url(None, "T01TEAM456"), "")

    def test_returns_empty_when_team_id_missing(self):
        self.assertEqual(build_slack_profile_url("U01ADA123", ""), "")
        self.assertEqual(build_slack_profile_url("U01ADA123", None), "")

    def test_returns_empty_when_both_missing(self):
        self.assertEqual(build_slack_profile_url("", ""), "")
        self.assertEqual(build_slack_profile_url(None, None), "")
