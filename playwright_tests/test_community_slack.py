"""
Playwright E2E tests for Community / Slack Integration (Issue #82).

Tests cover all 11 BDD scenarios from the issue:
- Main member sees the Community quick action on their dashboard
- Basic member does not see the Community quick action
- Free member discovers community access on the activities page
- Anonymous visitor sees community access on the pricing page
- Premium member also sees the Community quick action
- New Main member receives community invite after completing checkout
- Member downgrades from Main to Basic and loses community access
- Cancelled member re-subscribes to Main and regains community access
- Email matcher links a new Slack user who joined after purchasing Main
- Admin reviews community audit log in Django admin
- Subscription deletion triggers community removal

Scenarios 1-5 and 10 are full E2E browser tests.
Scenarios 6-9 and 11 exercise the backend tasks/services directly
with mocked Slack API calls and ORM assertions.

Usage:
    uv run pytest playwright_tests/test_community_slack.py -v
"""

import json
import os
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone
from playwright.sync_api import sync_playwright

from playwright_tests.conftest import DJANGO_BASE_URL


# Allow Django ORM calls from within sync_playwright (which runs an
# event loop internally). Without this, Django 6 raises
# SynchronousOnlyOperation when we create sessions inside test methods.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


VIEWPORT = {"width": 1280, "height": 720}

DEFAULT_PASSWORD = "TestPass123!"


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


def _create_admin_user(email="admin@test.com", password=DEFAULT_PASSWORD):
    """Create a superuser for admin tests."""
    from accounts.models import User

    _ensure_tiers()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "email_verified": True,
            "is_staff": True,
            "is_superuser": True,
        },
    )
    user.set_password(password)
    user.is_staff = True
    user.is_superuser = True
    user.email_verified = True
    user.save()
    return user


def _create_session_for_user(email):
    """Create a Django session for the given user and return the session key."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.auth import (
        SESSION_KEY,
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
    )
    from accounts.models import User

    user = User.objects.get(email=email)
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = (
        "django.contrib.auth.backends.ModelBackend"
    )
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return session.session_key


def _auth_context(browser, email):
    """Create an authenticated browser context for the given user."""
    session_key = _create_session_for_user(email)
    context = browser.new_context(viewport=VIEWPORT)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


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

    def test_main_member_sees_community_action_card(self, django_server):
        """Given a user logged in as main@test.com (Main tier, level 20).
        Navigate to / (authenticated dashboard), scroll to Quick Actions.
        A 'Community' action card appears with a link to /community.
        Click it and verify navigation."""
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to / (authenticated dashboard)
                page.goto(
                    f"{django_server}/",
                    wait_until="networkidle",
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
                page.wait_for_load_state("networkidle")

                # Then: User navigates to /community
                assert "/community" in page.url
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Basic member does not see the Community quick action
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2BasicMemberNoCommunityAction:
    """Basic member does not see the Community quick action."""

    def test_basic_member_does_not_see_community_card(self, django_server):
        """Given a user logged in as basic@test.com (Basic tier, level 10).
        Navigate to / (authenticated dashboard), scroll to Quick Actions.
        Quick actions include Browse Courses, View Recordings, and Submit
        a Project. No Community action card is shown."""
        _ensure_tiers()
        _create_user("basic@test.com", tier_slug="basic")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to / (authenticated dashboard)
                page.goto(
                    f"{django_server}/",
                    wait_until="networkidle",
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
            finally:
                browser.close()


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
    ):
        """Given a user logged in as free@test.com (Free tier).
        Navigate to /activities. 'Closed Community Access' is listed
        as an activity available at Main and Premium tiers, not Free
        or Basic. Click the Membership link in the header to navigate
        to the tiers section."""
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /activities
                page.goto(
                    f"{django_server}/activities",
                    wait_until="networkidle",
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
                page.wait_for_load_state("networkidle")

                # Then: User is taken to the homepage tiers section
                assert "/#tiers" in page.url or "tiers" in page.url
            finally:
                browser.close()


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
    ):
        """Given an anonymous visitor (not logged in).
        Navigate to /pricing, review the tier comparison grid.
        The Main tier card lists 'Slack community access'. The Free
        and Basic tier cards do not mention Slack community access.
        Click the Main tier payment link to verify it leads toward
        Stripe checkout."""
        _ensure_tiers()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /pricing
                page.goto(
                    f"{django_server}/pricing",
                    wait_until="networkidle",
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

                # Verify the href is present (it will be a Stripe link or #)
                href = main_cta.first.get_attribute("href")
                assert href is not None
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Premium member also sees the Community quick action
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5PremiumMemberSeesCommunityAction:
    """Premium member also sees the Community quick action."""

    def test_premium_member_sees_community_action_card(
        self, django_server
    ):
        """Given a user logged in as premium@test.com (Premium tier,
        level 30). Navigate to / (authenticated dashboard), scroll to
        Quick Actions. A 'Community' action card appears -- Premium
        includes everything Main has. Click it and verify navigation."""
        _ensure_tiers()
        _create_user("premium@test.com", tier_slug="premium")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "premium@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to / (authenticated dashboard)
                page.goto(
                    f"{django_server}/",
                    wait_until="networkidle",
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
                page.wait_for_load_state("networkidle")

                # Then: User navigates to /community
                assert "/community" in page.url
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: New Main member receives community invite after
#              completing checkout
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6NewMainMemberReceivesInvite:
    """New Main member receives community invite after completing checkout."""

    def test_checkout_completed_triggers_invite_and_audit_log(self):
        """Given a user who just completed Stripe checkout for the Main tier.
        When checkout.session.completed webhook fires and the user's tier
        is updated to Main, a background task is enqueued to invite the
        user to the Slack community. A CommunityAuditLog entry is created
        with action 'invite'. If the email matches a Slack account, the
        user is added to channels. If not, an invite email is sent."""
        from accounts.models import User
        from community.models import CommunityAuditLog
        from community.tasks.hooks import community_invite_task

        _ensure_tiers()
        _clear_audit_logs()
        user = _create_user("new-main@test.com", tier_slug="main")

        # Case 1: Email matches a Slack account
        mock_service = MagicMock()
        mock_service.lookup_user_by_email.return_value = "U12345SLACK"
        mock_service.add_to_channels.return_value = [
            {"channel": "C001", "ok": True},
        ]

        with patch(
            "community.tasks.hooks.get_community_service",
            return_value=mock_service,
        ):
            community_invite_task(user.pk)

        # Then: The service was called with invite
        mock_service.invite.assert_called_once()
        call_user = mock_service.invite.call_args[0][0]
        assert call_user.pk == user.pk

        # Then: A CommunityAuditLog entry is created with action "invite"
        # (The actual audit log creation happens inside the service.invite)
        # Since we mocked the entire service, let's test with the real
        # service but mocked Slack API.
        _clear_audit_logs()
        user.slack_user_id = ""
        user.save(update_fields=["slack_user_id"])

        with patch(
            "community.services.slack.SlackCommunityService._api_call"
        ) as mock_api:
            # Simulate Slack finding the user by email
            mock_api.return_value = {
                "ok": True,
                "user": {"id": "U67890SLACK"},
            }

            from community.services.slack import SlackCommunityService

            service = SlackCommunityService(
                bot_token="xoxb-test", channel_ids=["C001", "C002"]
            )
            service.invite(user)

        # Verify audit log was created
        logs = CommunityAuditLog.objects.filter(user=user, action="invite")
        assert logs.count() == 1
        log = logs.first()
        details = json.loads(log.details)
        assert details["slack_user_id"] == "U67890SLACK"

        # Verify user's slack_user_id was stored
        user.refresh_from_db()
        assert user.slack_user_id == "U67890SLACK"

    def test_invite_sends_email_when_slack_user_not_found(self):
        """If the user's email does not match a Slack account, an invite
        email is sent with the Slack workspace join link."""
        from accounts.models import User
        from community.models import CommunityAuditLog
        from community.services.slack import (
            SlackCommunityService,
            SlackAPIError,
        )

        _ensure_tiers()
        _clear_audit_logs()
        user = _create_user("no-slack@test.com", tier_slug="main")
        user.slack_user_id = ""
        user.save(update_fields=["slack_user_id"])

        with patch(
            "community.services.slack.SlackCommunityService._api_call"
        ) as mock_api:
            # Simulate Slack not finding the user
            mock_api.side_effect = SlackAPIError(
                "users_not_found",
                method="users.lookupByEmail",
                error_code="users_not_found",
            )

            with patch(
                "community.services.slack.send_mail"
            ) as mock_mail:
                service = SlackCommunityService(
                    bot_token="xoxb-test",
                    channel_ids=["C001"],
                )
                service.invite(user)

                # Then: An invite email was sent
                mock_mail.assert_called_once()
                call_kwargs = mock_mail.call_args
                assert "Welcome to AI Shipping Labs community" in (
                    call_kwargs[1].get("subject", "")
                    or call_kwargs[0][0]
                )

        # Then: Audit log records the email-sent status
        logs = CommunityAuditLog.objects.filter(user=user, action="invite")
        assert logs.count() == 1
        details = json.loads(logs.first().details)
        assert details["status"] == "email_sent"
        assert details["reason"] == "slack_user_not_found"


# ---------------------------------------------------------------
# Scenario 7: Member downgrades from Main to Basic and loses
#              community access
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7DowngradeLosesCommunityAccess:
    """Member downgrades from Main to Basic and loses community access."""

    def test_scheduled_removal_removes_user_from_channels(self):
        """Given a Main-tier user who currently has Slack community access.
        When they downgrade and the scheduled removal task runs, the user
        is removed from all Slack community channels. A CommunityAuditLog
        entry is created with action 'remove'."""
        from accounts.models import User
        from payments.models import Tier
        from community.models import CommunityAuditLog
        from community.tasks.removal import scheduled_community_removal

        _ensure_tiers()
        _clear_audit_logs()

        user = _create_user("downgrade@test.com", tier_slug="main")
        user.slack_user_id = "UDOWNGRADE"
        user.save(update_fields=["slack_user_id"])

        # Simulate the billing period ending: user is now Basic
        basic_tier = Tier.objects.get(slug="basic")
        user.tier = basic_tier
        user.save(update_fields=["tier"])

        with patch(
            "community.tasks.removal.get_community_service"
        ) as mock_get_svc:
            mock_service = MagicMock()
            mock_get_svc.return_value = mock_service

            # Run the scheduled removal task
            scheduled_community_removal(user.pk)

        # Then: service.remove was called
        mock_service.remove.assert_called_once()
        call_user = mock_service.remove.call_args[0][0]
        assert call_user.pk == user.pk

    def test_scheduled_removal_skips_if_user_resubscribed(self):
        """If the user re-subscribed before the removal ran, the task
        should skip the removal since the user's tier is back to Main+."""
        from accounts.models import User
        from community.tasks.removal import scheduled_community_removal

        _ensure_tiers()
        _clear_audit_logs()

        user = _create_user("resubbed@test.com", tier_slug="main")
        user.slack_user_id = "URESUBBED"
        user.save(update_fields=["slack_user_id"])

        # User re-subscribed: still on Main tier when removal runs
        with patch(
            "community.tasks.removal.get_community_service"
        ) as mock_get_svc:
            mock_service = MagicMock()
            mock_get_svc.return_value = mock_service

            scheduled_community_removal(user.pk)

        # Then: service.remove was NOT called (user re-subscribed)
        mock_service.remove.assert_not_called()


# ---------------------------------------------------------------
# Scenario 8: Cancelled member re-subscribes to Main and regains
#              community access
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8ResubscribeRegainsCommunityAccess:
    """Cancelled member re-subscribes to Main and regains community access."""

    def test_reactivate_re_adds_user_to_channels(self):
        """Given a user who previously had Main tier, cancelled, was
        removed from Slack channels, and re-subscribes to Main. A
        background task is enqueued to reactivate the user. The user
        is re-added to community channels using their previously linked
        slack_user_id. A CommunityAuditLog entry is created with action
        'reactivate'."""
        from accounts.models import User
        from community.models import CommunityAuditLog
        from community.services.slack import SlackCommunityService

        _ensure_tiers()
        _clear_audit_logs()

        user = _create_user("reactivate@test.com", tier_slug="main")
        user.slack_user_id = "UREACTIVATE"
        user.save(update_fields=["slack_user_id"])

        with patch(
            "community.services.slack.SlackCommunityService._api_call"
        ) as mock_api:
            mock_api.return_value = {"ok": True}

            service = SlackCommunityService(
                bot_token="xoxb-test", channel_ids=["C001", "C002"]
            )
            service.reactivate(user)

        # Then: Audit log entry with action "reactivate"
        logs = CommunityAuditLog.objects.filter(
            user=user, action="reactivate"
        )
        assert logs.count() == 1
        details = json.loads(logs.first().details)
        assert details["slack_user_id"] == "UREACTIVATE"
        assert len(details["channels"]) == 2

    def test_reactivate_task_calls_service(self):
        """The community_reactivate_task loads the user and delegates
        to the service."""
        from community.tasks.hooks import community_reactivate_task

        _ensure_tiers()
        user = _create_user("reactivate-task@test.com", tier_slug="main")

        with patch(
            "community.tasks.hooks.get_community_service"
        ) as mock_get_svc:
            mock_service = MagicMock()
            mock_get_svc.return_value = mock_service

            community_reactivate_task(user.pk)

        mock_service.reactivate.assert_called_once()
        call_user = mock_service.reactivate.call_args[0][0]
        assert call_user.pk == user.pk


# ---------------------------------------------------------------
# Scenario 9: Email matcher links a new Slack user who joined
#              after purchasing Main
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9EmailMatcherLinksSlackUser:
    """Email matcher links a new Slack user who joined after purchasing Main."""

    def test_email_matcher_finds_and_links_user(self):
        """Given a Main-tier user who purchased membership but was not
        yet in the Slack workspace (slack_user_id is empty). When the
        hourly email matcher background job runs, the job finds the
        user's email in Slack, stores their slack_user_id, adds them
        to community channels, and creates a CommunityAuditLog entry
        with action 'link' and source 'email_matcher'."""
        from accounts.models import User
        from community.models import CommunityAuditLog
        from community.tasks.email_matcher import match_community_emails

        _ensure_tiers()
        _clear_audit_logs()

        user = _create_user("matcher-test@test.com", tier_slug="main")
        user.slack_user_id = ""
        user.save(update_fields=["slack_user_id"])

        mock_service = MagicMock()
        mock_service.lookup_user_by_email.return_value = "UMATCHED123"
        mock_service.add_to_channels.return_value = [
            {"channel": "C001", "ok": True},
            {"channel": "C002", "ok": True},
        ]

        with patch(
            "community.tasks.email_matcher.get_community_service",
            return_value=mock_service,
        ):
            result = match_community_emails()

        # Then: The matcher found and linked the user
        assert result["matched"] >= 1

        # Then: slack_user_id was stored
        user.refresh_from_db()
        assert user.slack_user_id == "UMATCHED123"

        # Then: Audit log with action "link" and source "email_matcher"
        logs = CommunityAuditLog.objects.filter(user=user, action="link")
        assert logs.count() == 1
        details = json.loads(logs.first().details)
        assert details["slack_user_id"] == "UMATCHED123"
        assert details["source"] == "email_matcher"
        assert len(details["channels"]) == 2


# ---------------------------------------------------------------
# Scenario 10: Admin reviews community audit log in Django admin
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10AdminReviewsAuditLog:
    """Admin reviews community audit log in Django admin."""

    def test_admin_sees_audit_log_list_and_detail(self, django_server):
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

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the Community Audit Logs section
                page.goto(
                    f"{django_server}/admin/community/communityauditlog/",
                    wait_until="networkidle",
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
                page.wait_for_load_state("networkidle")

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
                    wait_until="networkidle",
                )
                body = page.content()
                # The main content area should not have an "Add" link
                # for this model (sidebar has add links for other models)
                content_area = page.locator("#content-main")
                content_html = content_area.inner_html()
                assert "communityauditlog/add" not in content_html
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 11: Subscription deletion triggers community removal
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11SubscriptionDeletionTriggersRemoval:
    """Subscription deletion triggers community removal."""

    def test_subscription_deleted_reverts_tier_and_triggers_removal(self):
        """Given a Main-tier user whose subscription is deleted by Stripe.
        When the customer.subscription.deleted webhook fires, the user's
        tier is reverted to Free, a background task is enqueued to remove
        the user from Slack community channels, and a CommunityAuditLog
        entry is created with action 'remove'. The user's dashboard no
        longer shows the Community quick action."""
        from accounts.models import User
        from payments.models import Tier
        from community.models import CommunityAuditLog

        _ensure_tiers()
        _clear_audit_logs()

        user = _create_user("deleted-sub@test.com", tier_slug="main")
        user.stripe_customer_id = "cus_test_deletion"
        user.subscription_id = "sub_test_deletion"
        user.slack_user_id = "UDELETION"
        user.billing_period_end = timezone.now()
        user.save(update_fields=[
            "stripe_customer_id",
            "subscription_id",
            "slack_user_id",
            "billing_period_end",
        ])

        # Verify user is on Main tier before deletion
        assert user.tier.slug == "main"
        assert user.tier.level >= 20

        # Mock the background task enqueue to capture the call.
        # async_task is imported locally inside _community_remove via
        # "from jobs.tasks import async_task", so we patch the source.
        with patch("jobs.tasks.async_task") as mock_async:
            from payments.services import handle_subscription_deleted

            handle_subscription_deleted({
                "id": "sub_test_deletion",
                "customer": "cus_test_deletion",
            })

        # Then: User's tier is reverted to Free
        user.refresh_from_db()
        assert user.tier.slug == "free"
        assert user.subscription_id == ""

        # Then: A background task was enqueued to remove user
        mock_async.assert_called()
        # Check that the community remove task was enqueued
        call_args_list = mock_async.call_args_list
        community_call = None
        for call in call_args_list:
            task_name = call[0][0] if call[0] else ""
            if "community" in task_name and "remove" in task_name:
                community_call = call
                break
        assert community_call is not None, (
            f"Expected community remove task to be enqueued. "
            f"Calls: {call_args_list}"
        )

    def test_deleted_subscription_user_no_community_on_dashboard(
        self, django_server
    ):
        """After subscription deletion, the user's dashboard no longer
        shows the Community quick action."""
        _ensure_tiers()

        # Create user on Free tier (post-deletion state)
        _create_user("deleted-dash@test.com", tier_slug="free")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "deleted-dash@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Quick Actions section is present
                assert "Quick Actions" in body

                # Community action card should NOT be shown for Free user
                community_link = page.locator(
                    'a[href="/community"]'
                )
                assert community_link.count() == 0
            finally:
                browser.close()
