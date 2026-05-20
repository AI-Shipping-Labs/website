"""Playwright E2E for /account/ Slack card behaviour after issue #730.

Issue #730 made two surgical changes to ``templates/accounts/account.html``:

1. The Slack card include moved from between Membership and Email
   Preferences to immediately after the Profile section -- so when a
   not-yet-joined Main+ member lands on /account/ the Join Slack CTA is
   the second card on the page, not the fourth.
2. The include is wrapped in ``{% if not slack_connected %}`` so members
   who already joined Slack see nothing in that slot. The connected-state
   panel duplicated information the user already has inside Slack.

These three browser-level scenarios mirror the issue's Playwright spec:

* Connected Main member sees no Slack confirmation noise on /account/.
* Not-yet-joined Main member sees the Join CTA between Profile and
  Membership.
* Free user sees no Slack card at all (regression -- the partial
  short-circuits below the Main tier).

Usage:
    uv run pytest playwright_tests/test_account_slack_card_730.py -v
"""

import os

import pytest

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

SLACK_INVITE_URL = "https://join.slack.com/t/test-730/shared_invite/zz"


def _set_slack_member(email, *, slack_member, slack_user_id=""):
    """Toggle the ``slack_member`` flag (and optionally the Slack ID)."""
    from django.db import connection

    from accounts.models import User

    user = User.objects.get(email=email)
    user.slack_member = slack_member
    user.slack_user_id = slack_user_id
    user.save(update_fields=["slack_member", "slack_user_id"])
    connection.close()
    return user


@pytest.mark.django_db(transaction=True)
class TestConnectedMemberSeesNoSlackCardOnAccount:
    """Issue #730 scenario 1: connected Main member sees no Slack card."""

    def test_no_slack_card_for_connected_main_user(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            create_user("connected-730@test.com", tier_slug="main")
            _set_slack_member(
                "connected-730@test.com",
                slack_member=True,
                slack_user_id="U0CONNECTED1",
            )

        settings.SLACK_INVITE_URL = SLACK_INVITE_URL

        context = auth_context(browser, "connected-730@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )

            # The connected-state Slack card must not render at all.
            assert page.locator(
                '[data-testid="slack-account-card"]'
            ).count() == 0
            assert page.locator(
                '[data-testid="slack-account-card-id-row"]'
            ).count() == 0
            # And neither headline nor verbose copy from the panel.
            body = page.content()
            assert "Connected to Slack" not in body
            assert (
                "You are a member of the AI Shipping Labs community workspace."
                not in body
            )
            # The Slack ID must not leak as plain text either -- the
            # partial was the only place it appeared on /account/.
            assert "U0CONNECTED1" not in body

            # The other account sections are still visible -- /account/
            # is functional, only the Slack confirmation noise is gone.
            assert page.locator("#profile-section").is_visible()
            assert page.locator("#email-preferences-section").is_visible()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestNotYetJoinedMainMemberSeesCtaNearTopOfAccount:
    """Issue #730 scenario 2: not-yet-joined Main member sees the CTA."""

    def test_join_cta_renders_above_membership_card(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            create_user("notjoined-730@test.com", tier_slug="main")

        settings.SLACK_INVITE_URL = SLACK_INVITE_URL

        context = auth_context(browser, "notjoined-730@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )

            join_card = page.locator('[data-testid="slack-account-card"]')
            assert join_card.count() == 1
            join_anchor = page.locator(
                '[data-testid="slack-account-card-join"]'
            )
            assert join_anchor.count() == 1
            assert join_anchor.first.get_attribute("href") == SLACK_INVITE_URL
            assert join_anchor.first.get_attribute("target") == "_blank"
            assert join_anchor.first.get_attribute("rel") == "noopener"

            # The Slack card sits between Profile and Membership in the
            # main column. Read bounding boxes to verify visual order.
            profile_box = page.locator("#profile-section").bounding_box()
            join_box = join_card.first.bounding_box()
            # The Membership card has no id; anchor on its lucide crown
            # icon, which is unique to that section in this template.
            # Lucide swaps <i data-lucide="..."> into inline <svg> on load,
            # but preserves the data-lucide attribute — match by attribute.
            membership_box = page.locator(
                '#profile-section ~ div [data-lucide="crown"]'
            ).first.bounding_box()
            email_box = page.locator(
                "#email-preferences-section"
            ).bounding_box()

            assert profile_box is not None
            assert join_box is not None
            assert membership_box is not None
            assert email_box is not None

            assert profile_box["y"] < join_box["y"], (
                "Slack join card must render below Profile"
            )
            assert join_box["y"] < membership_box["y"], (
                "Slack join card must render above Membership"
            )
            assert membership_box["y"] < email_box["y"], (
                "Membership must still render above Email Preferences"
            )
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestFreeUserSeesNoSlackCardOnAccount:
    """Issue #730 scenario 3: Free tier renders no Slack content."""

    def test_free_user_sees_no_slack_card(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            create_user("free-730@test.com", tier_slug="free")

        settings.SLACK_INVITE_URL = SLACK_INVITE_URL

        context = auth_context(browser, "free-730@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )

            assert page.locator(
                '[data-testid="slack-account-card"]'
            ).count() == 0
            assert page.locator(
                '[data-testid="slack-account-card-join"]'
            ).count() == 0
            body = page.content()
            assert "Join our Slack community" not in body
            assert "Connected to Slack" not in body
        finally:
            context.close()
