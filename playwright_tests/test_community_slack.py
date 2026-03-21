"""
Playwright E2E tests for Community / Slack Integration (Issue #82).

Only browser-based scenarios remain here (1-5, 10, and the dashboard
check from 11). Scenarios 6-9 and the backend part of 11 were moved
to community/tests/test_services.py because they exercise backend
tasks/services directly with no browser interaction.

Usage:
    uv run pytest playwright_tests/test_community_slack.py -v
"""

import json
import os
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    DJANGO_BASE_URL,
    VIEWPORT,
    DEFAULT_PASSWORD,
    create_staff_user as _create_admin_user,
    create_session_for_user as _create_session_for_user,
    auth_context as _auth_context,
)


os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_tiers():
    """Ensure membership tiers exist."""
    from payments.models import Tier

    TIERS = [
        {
            "slug": "free",
            "name": "Free",
            "level": 0,
            "features": ["Newsletter emails", "Access to open content"],
        },
        {
            "slug": "basic",
            "name": "Basic",
            "level": 10,
            "features": [
                "Exclusive articles",
                "Tutorials with code examples",
                "AI tool breakdowns",
                "Research notes",
                "Curated social posts",
            ],
        },
        {
            "slug": "main",
            "name": "Main",
            "level": 20,
            "features": [
                "Everything in Basic",
                "Slack community access",
                "Group coding sessions",
                "Project-based learning",
                "Community hackathons",
                "Career discussions",
                "Personal brand guidance",
                "Topic voting",
            ],
        },
        {
            "slug": "premium",
            "name": "Premium",
            "level": 30,
            "features": [
                "Everything in Main",
                "All mini-courses",
                "Mini-course topic voting",
                "Resume/LinkedIn/GitHub teardowns",
            ],
        },
    ]
    for tier_data in TIERS:
        from payments.models import Tier

        Tier.objects.get_or_create(
            slug=tier_data["slug"], defaults=tier_data
        )


def _create_user(email, tier_slug="free", password=DEFAULT_PASSWORD):
    """Create a user with the given tier."""
    from accounts.models import User
    from payments.models import Tier

    _ensure_tiers()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"email_verified": True},
    )
    user.set_password(password)
    tier = Tier.objects.get(slug=tier_slug)
    user.tier = tier
    user.email_verified = True
    user.save()
    return user


def _clear_audit_logs():
    """Delete all community audit logs."""
    from community.models import CommunityAuditLog

    CommunityAuditLog.objects.all().delete()


# ---------------------------------------------------------------
# Scenario 1: Main member sees the Community quick action on
#              their dashboard
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1MainMemberSeesCommunityQuickAction:
    """Main member sees the Community quick action on their dashboard."""

    def test_main_member_sees_community_action_card(self, django_server, browser):
        """Given a user logged in as main@test.com (Main tier, level 20).
        Navigate to / (authenticated dashboard), scroll to Quick Actions.
        A 'Community' action card appears with a link to /community.
        Click it and verify navigation."""
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to / (authenticated dashboard)
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # The dashboard should show the Quick Actions section
        assert "Quick Actions" in body

        # Step 2: Verify the Community action card is present
        community_card = page.locator(
            'a[href="/community"]'
        )
        assert community_card.count() >= 1

        community_text = community_card.first.inner_text()
        assert "Community" in community_text

        # Step 3: Click the Community action card
        community_card.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: User navigates to /community
        assert "/community" in page.url
# ---------------------------------------------------------------
# Scenario 2: Basic member does not see the Community quick action
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2BasicMemberNoCommunityAction:
    """Basic member does not see the Community quick action."""

    def test_basic_member_does_not_see_community_card(self, django_server, browser):
        """Given a user logged in as basic@test.com (Basic tier, level 10).
        Navigate to / (authenticated dashboard), scroll to Quick Actions.
        Quick actions include Browse Courses, View Recordings, and Submit
        a Project. No Community action card is shown."""
        _ensure_tiers()
        _create_user("basic@test.com", tier_slug="basic")

        context = _auth_context(browser, "basic@test.com")
        page = context.new_page()
        # Step 1: Navigate to / (authenticated dashboard)
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # The dashboard should show Quick Actions
        assert "Quick Actions" in body

        # Step 2: Verify standard quick actions are present
        assert "Browse Courses" in body
        assert "View Recordings" in body
        assert "Submit Project" in body

        # Then: No Community action card is shown
        community_link = page.locator(
            'a[href="/community"]'
        )
        assert community_link.count() == 0
# ---------------------------------------------------------------
# Scenario 3: Free member discovers community access is a Main
#              tier benefit on the activities page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3FreeMemberDiscoversCommunityOnActivities:
    """Free member discovers community access is a Main tier benefit
    on the activities page."""

    def test_free_member_sees_community_activity_with_tier_badges(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier).
        Navigate to /activities. 'Closed Community Access' is listed
        as an activity available at Main and Premium tiers, not Free
        or Basic. Click the Membership link in the header to navigate
        to the tiers section."""
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /activities
        page.goto(
            f"{django_server}/activities",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Step 2: Look at the activity cards
        # Then: "Closed Community Access" is listed
        assert "Closed Community Access" in body

        # Find the Closed Community Access activity card
        activity_cards = page.locator(".activity-card")
        community_card = None
        for i in range(activity_cards.count()):
            card = activity_cards.nth(i)
            if "Closed Community Access" in card.inner_text():
                community_card = card
                break

        assert community_card is not None, (
            "Could not find 'Closed Community Access' activity card"
        )

        # Then: The card has data-tiers containing main and premium
        tiers_attr = community_card.get_attribute("data-tiers")
        assert "main" in tiers_attr
        assert "premium" in tiers_attr
        # Free and Basic should NOT be included
        assert "free" not in tiers_attr
        assert "basic" not in tiers_attr

        # Step 3: Click the Membership link in the header
        membership_link = page.locator(
            'header a[href="/#tiers"]'
        ).first
        membership_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: User is taken to the homepage tiers section
        assert "/#tiers" in page.url or "tiers" in page.url
# ---------------------------------------------------------------
# Scenario 4: Anonymous visitor sees community access highlighted
#              in the Main tier on the pricing page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4AnonymousVisitorSeesSlackOnPricingPage:
    """Anonymous visitor sees community access highlighted in the
    Main tier on the pricing page."""

    def test_anonymous_sees_slack_community_in_main_tier(
        self, django_server
    , page):
        """Given an anonymous visitor (not logged in).
        Navigate to /pricing, review the tier comparison grid.
        The Main tier card lists 'Slack community access'. The Free
        and Basic tier cards do not mention Slack community access.
        Click the Main tier payment link to verify it leads toward
        Stripe checkout."""
        _ensure_tiers()

        # Step 1: Navigate to /pricing
        page.goto(
            f"{django_server}/pricing",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Step 2: Review the tier comparison grid
        # Get all tier cards
        grid = page.locator(
            "div.grid.sm\\:grid-cols-2.lg\\:grid-cols-4"
        )
        tier_cards = grid.locator("> div")

        # Then: The Main tier card lists "Slack community access"
        # Find Main card by h2 text
        main_card = None
        free_card = None
        basic_card = None
        for i in range(tier_cards.count()):
            card = tier_cards.nth(i)
            h2_text = card.locator("h2").first.inner_text()
            if h2_text == "Main":
                main_card = card
            elif h2_text == "Free":
                free_card = card
            elif h2_text == "Basic":
                basic_card = card

        assert main_card is not None, "Main tier card not found"
        main_features = main_card.locator("ul").inner_text()
        assert "Slack community access" in main_features

        # Then: Free and Basic do not mention Slack community access
        assert free_card is not None, "Free tier card not found"
        free_features = free_card.locator("ul").inner_text()
        assert "Slack community access" not in free_features

        assert basic_card is not None, "Basic tier card not found"
        basic_features = basic_card.locator("ul").inner_text()
        assert "Slack community access" not in basic_features

        # Step 3: Click the payment link for the Main tier
        main_cta = main_card.locator("a.tier-cta-link")
        assert main_cta.count() >= 1

        # Verify the href points to a Stripe payment link
        href = main_cta.first.get_attribute("href")
        assert href is not None, "Main tier CTA link must have an href"
        assert href.startswith("https://buy.stripe.com/") or href.startswith("#"), (
            f"Expected Stripe payment link or placeholder, got: {href}"
        )
# ---------------------------------------------------------------
# Scenario 5: Premium member also sees the Community quick action
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5PremiumMemberSeesCommunityAction:
    """Premium member also sees the Community quick action."""

    def test_premium_member_sees_community_action_card(
        self, django_server
    , browser):
        """Given a user logged in as premium@test.com (Premium tier,
        level 30). Navigate to / (authenticated dashboard), scroll to
        Quick Actions. A 'Community' action card appears -- Premium
        includes everything Main has. Click it and verify navigation."""
        _ensure_tiers()
        _create_user("premium@test.com", tier_slug="premium")

        context = _auth_context(browser, "premium@test.com")
        page = context.new_page()
        # Step 1: Navigate to / (authenticated dashboard)
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # The dashboard should show Quick Actions
        assert "Quick Actions" in body

        # Step 2: Verify the Community action card is present
        community_card = page.locator(
            'a[href="/community"]'
        )
        assert community_card.count() >= 1

        community_text = community_card.first.inner_text()
        assert "Community" in community_text

        # Step 3: Click the Community action card
        community_card.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: User navigates to /community
        assert "/community" in page.url
# ---------------------------------------------------------------
# Scenario 10: Admin reviews community audit log in Django admin
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10AdminReviewsAuditLog:
    """Admin reviews community audit log in Django admin."""

    def test_admin_sees_audit_log_list_and_detail(self, django_server, browser):
        """Given a staff user logged in to /admin/. Navigate to the
        Community Audit Logs section. The admin sees a list of audit
        log entries with user email, action, and timestamp columns.
        Click on an entry to see full details including Slack API
        response data. The entry is read-only."""
        _ensure_tiers()
        _clear_audit_logs()
        admin_user = _create_admin_user("admin@test.com")

        # Create some audit log entries
        from community.models import CommunityAuditLog

        test_user = _create_user("audited@test.com", tier_slug="main")

        log1 = CommunityAuditLog.objects.create(
            user=test_user,
            action="invite",
            details=json.dumps({
                "slack_user_id": "U12345",
                "channels": [
                    {"channel": "C001", "ok": True},
                    {"channel": "C002", "ok": True},
                ],
            }),
        )
        log2 = CommunityAuditLog.objects.create(
            user=test_user,
            action="remove",
            details=json.dumps({
                "slack_user_id": "U12345",
                "channels": [
                    {"channel": "C001", "ok": True},
                ],
            }),
        )

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Step 1: Navigate to the Community Audit Logs section
        page.goto(
            f"{django_server}/admin/community/communityauditlog/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The admin sees a list of audit log entries
        assert "audited@test.com" in body
        assert "invite" in body.lower() or "Invite" in body
        assert "remove" in body.lower() or "Remove" in body

        # Step 2: Click on the most recent audit log entry
        # (the remove entry, which is listed first due to ordering)
        first_row = page.locator("#result_list tbody tr").first
        first_row.locator("a").first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()

        # Then: The entry shows full details
        assert "audited@test.com" in body
        # Details field shows Slack API response data
        assert "U12345" in body
        assert "C001" in body

        # Then: The entry is read-only -- no save button should
        # appear (has_change_permission returns False)
        save_btn = page.locator('input[name="_save"]')
        assert save_btn.count() == 0

        # Verify there is no "Add community audit log" button
        # on the changelist page (has_add_permission returns False)
        page.goto(
            f"{django_server}/admin/community/communityauditlog/",
            wait_until="domcontentloaded",
        )
        body = page.content()
        # The main content area should not have an "Add" link
        # for this model (sidebar has add links for other models)
        content_area = page.locator("#content-main")
        content_html = content_area.inner_html()
        assert "communityauditlog/add" not in content_html
# ---------------------------------------------------------------
# Scenario 11 (browser part only): After subscription deletion,
# the dashboard no longer shows the Community quick action.
# The backend part of this scenario was moved to
# community/tests/test_services.py.
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11DashboardAfterDeletion:
    """Subscription deletion -- dashboard no longer shows Community."""

    def test_deleted_subscription_user_no_community_on_dashboard(
        self, django_server
    , browser):
        """After subscription deletion, the user's dashboard no longer
        shows the Community quick action."""
        _ensure_tiers()

        # Create user on Free tier (post-deletion state)
        _create_user("deleted-dash@test.com", tier_slug="free")

        context = _auth_context(browser, "deleted-dash@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Quick Actions section is present
        assert "Quick Actions" in body

        # Community action card should NOT be shown for Free user
        community_link = page.locator(
            'a[href="/community"]'
        )
        assert community_link.count() == 0
