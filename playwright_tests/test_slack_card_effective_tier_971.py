"""E2E: Join Slack card uses effective (override-aware) tier (issue #971).

Policy (Option A): an active, non-expired ``TierOverride`` grants
Slack/community access. The Join Slack card on both /dashboard and
/account must agree with the gated /community/slack join redirect, which
already resolves via ``content.access.get_user_level``.

Scenarios mirror the issue's Playwright spec:
* Comped member (Free base + active Main override) finds the Join Slack
  card on /dashboard and reaches the gated redirect (not a deny page).
* The same member sees the Join Slack card on /account.
* A member whose override expired sees the card on neither page.

Usage:
    uv run pytest playwright_tests/test_slack_card_effective_tier_971.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context as _auth_context

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: seeds DB rows and injects session cookies. Cannot run against
# the deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

SLACK_INVITE_URL = "https://join.slack.com/t/test-971/shared_invite/zz"

# A fixed future / past expiry so the override window is deterministic.
ACTIVE_EXPIRES = datetime.datetime(2099, 5, 6, 0, 0, 0)


def _seed_users():
    """Create the comped (active override) and expired-override members.

    Must be called inside ``django_db_blocker.unblock()``.
    """
    from django.db import connection

    from accounts.models import TierOverride, User
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tiers = {t.slug: t for t in Tier.objects.all()}

    User.objects.filter(
        email__in=["comped-971@test.com", "expired-971@test.com"]
    ).delete()

    # Free base + active Main override, not yet a Slack member.
    comped = User.objects.create_user(
        email="comped-971@test.com", password=None, email_verified=True,
    )
    comped.tier = tiers["free"]
    comped.slack_member = False
    comped.save()
    TierOverride.objects.create(
        user=comped,
        original_tier=tiers["free"],
        override_tier=tiers["main"],
        expires_at=timezone.make_aware(ACTIVE_EXPIRES),
        is_active=True,
    )

    # Free base + EXPIRED Main override.
    expired = User.objects.create_user(
        email="expired-971@test.com", password=None, email_verified=True,
    )
    expired.tier = tiers["free"]
    expired.slack_member = False
    expired.save()
    TierOverride.objects.create(
        user=expired,
        original_tier=tiers["free"],
        override_tier=tiers["main"],
        expires_at=timezone.now() - datetime.timedelta(days=1),
        is_active=True,
    )
    connection.close()


@pytest.fixture
def seeded(django_server, django_db_blocker):
    with django_db_blocker.unblock():
        _seed_users()
    return True


@pytest.mark.django_db(transaction=True)
class TestCompedMemberSeesJoinCard:
    """Active Main override grants the Join card on both pages."""

    @pytest.mark.core
    def test_dashboard_shows_join_card_and_cta_reaches_redirect(
        self, django_server, seeded, browser, settings,
    ):
        settings.SLACK_INVITE_URL = SLACK_INVITE_URL
        ctx = _auth_context(browser, "comped-971@test.com")
        try:
            page = ctx.new_page()
            page.goto(
                f"{django_server}/", wait_until="domcontentloaded",
            )

            join = page.locator('[data-testid="slack-account-card-join"]')
            assert join.count() == 1
            assert join.first.get_attribute("href") == "/community/slack"

            # Following the CTA reaches the gated redirect, NOT the deny
            # page — the effective Main tier grants access. The redirect
            # 302s to the (configured) invite URL.
            join.first.click()
            page.wait_for_load_state("domcontentloaded")
            assert page.locator(
                '[data-testid="slack-join-denied"]'
            ).count() == 0
        finally:
            ctx.close()

    def test_account_shows_join_card(
        self, django_server, seeded, browser, settings,
    ):
        settings.SLACK_INVITE_URL = SLACK_INVITE_URL
        ctx = _auth_context(browser, "comped-971@test.com")
        try:
            page = ctx.new_page()
            page.goto(
                f"{django_server}/account/", wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="slack-account-card-join"]'
            ).count() == 1
        finally:
            ctx.close()


@pytest.mark.django_db(transaction=True)
class TestExpiredOverrideHidesJoinCard:
    """An expired override grants no community access on either page."""

    def test_dashboard_and_account_hide_join_card(
        self, django_server, seeded, browser, settings,
    ):
        settings.SLACK_INVITE_URL = SLACK_INVITE_URL
        ctx = _auth_context(browser, "expired-971@test.com")
        try:
            page = ctx.new_page()
            page.goto(
                f"{django_server}/", wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="slack-account-card-join"]'
            ).count() == 0

            page.goto(
                f"{django_server}/account/", wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="slack-account-card-join"]'
            ).count() == 0
        finally:
            ctx.close()
