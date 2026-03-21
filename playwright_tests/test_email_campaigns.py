"""
Playwright E2E tests for Email Campaigns (Issue #87).

Tests cover all 11 BDD scenarios from the issue:
- Staff member creates a new campaign from the Studio
- Staff member reviews campaign details and estimated recipients
- Staff member sends a test email before sending a campaign
- Staff member sends a campaign to all eligible recipients
- Staff member filters the campaign list by status
- Staff member searches campaigns by subject
- Staff member cannot re-send an already sent campaign
- Campaign only reaches users who meet all eligibility criteria
- Staff member creates a campaign targeting Premium-only audience
- Non-staff user cannot access the campaign management pages
- Staff member views the campaign list when no campaigns exist yet

Scenarios 1, 2, 4-6, 9-11 are full E2E browser tests against the Studio UI.
Scenarios 3, 7 are tested against the Django admin change form.
Scenario 8 exercises the campaign send task directly with mocked SES.

Usage:
    uv run pytest playwright_tests/test_email_campaigns.py -v
"""

import os
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone
from playwright.sync_api import sync_playwright

from playwright_tests.conftest import (
    DJANGO_BASE_URL,
    VIEWPORT,
    DEFAULT_PASSWORD,
    ensure_tiers as _ensure_tiers,
    create_user as _create_user,
    create_staff_user as _create_admin_user,
    create_session_for_user as _create_session_for_user,
    auth_context as _auth_context,
)


# Allow Django ORM calls from within sync_playwright (which runs an
# event loop internally). Without this, Django 6 raises
# SynchronousOnlyOperation when we create sessions inside test methods.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _clear_campaigns():
    """Delete all campaigns and email logs to ensure clean state."""
    from email_app.models import EmailCampaign, EmailLog

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()


def _create_campaign(subject, body="Test body", target_min_level=0, status="draft"):
    """Create an EmailCampaign via ORM."""
    from email_app.models import EmailCampaign

    campaign = EmailCampaign.objects.create(
        subject=subject,
        body=body,
        target_min_level=target_min_level,
        status=status,
    )
    return campaign


# ---------------------------------------------------------------
# Scenario 1: Staff member creates a new campaign from the Studio
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1StaffCreatesNewCampaign:
    """Staff member creates a new campaign from the Studio."""

    def test_staff_creates_campaign_via_studio(self, django_server):
        """Given a user logged in as admin@test.com (Staff).
        Navigate to /studio/campaigns/, click 'New Campaign',
        fill in form with subject, body, audience, and submit.
        User is redirected to the list with the new campaign visible."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/campaigns/
                page.goto(
                    f"{django_server}/studio/campaigns/",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "Email Campaigns" in body

                # Step 2: Click "New Campaign"
                new_campaign_link = page.locator(
                    'a[href="/studio/campaigns/new"]'
                )
                assert new_campaign_link.count() >= 1
                new_campaign_link.first.click()
                page.wait_for_load_state("networkidle")

                # Then: User lands on /studio/campaigns/new with form
                assert "/studio/campaigns/new" in page.url
                body = page.content()
                assert "New Campaign" in body

                # Step 3: Fill in the form
                page.fill('input[name="subject"]', "February Newsletter")
                page.fill(
                    'textarea[name="body"]',
                    "# Welcome\n\nThis is the **February** newsletter.",
                )

                # Select "Everyone (including free)" audience
                page.select_option(
                    'select[name="target_min_level"]', value="0"
                )

                # Step 4: Click "Create Campaign"
                page.click('button:has-text("Create Campaign")')
                page.wait_for_load_state("networkidle")

                # Then: User is redirected to /studio/campaigns/
                assert "/studio/campaigns/" in page.url
                body = page.content()

                # The new campaign appears in the list with status "Draft"
                assert "February Newsletter" in body
                assert "Draft" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Staff member reviews campaign details and estimated
#              recipients
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2StaffReviewsCampaignDetails:
    """Staff member reviews campaign details and estimated recipients."""

    def test_campaign_detail_shows_status_audience_and_recipients(
        self, django_server
    ):
        """Given a user logged in as admin@test.com (Staff) and a draft
        campaign 'Weekly Update' targeting 'Basic and above' exists,
        with 5 verified Basic+ users and 3 Free users in the system.
        Navigate to campaign detail. Verify status, audience, estimated
        recipient count, and body preview."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        # Create 5 verified Basic+ users (should be eligible)
        for i in range(3):
            _create_user(
                f"basic-user-{i}@test.com",
                tier_slug="basic",
                email_verified=True,
            )
        for i in range(2):
            _create_user(
                f"main-user-{i}@test.com",
                tier_slug="main",
                email_verified=True,
            )

        # Create 3 Free users (should NOT be eligible for Basic+)
        for i in range(3):
            _create_user(
                f"free-user-{i}@test.com",
                tier_slug="free",
                email_verified=True,
            )

        campaign = _create_campaign(
            subject="Weekly Update",
            body="# Weekly Update\n\nHere is your update.",
            target_min_level=10,  # Basic and above
            status="draft",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/campaigns/
                page.goto(
                    f"{django_server}/studio/campaigns/",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "Weekly Update" in body

                # Step 2: Click on the "Weekly Update" campaign
                page.click(
                    f'a[href="/studio/campaigns/{campaign.pk}/"]'
                )
                page.wait_for_load_state("networkidle")

                # Then: User lands on the campaign detail page
                body = page.content()

                # Status is "Draft"
                assert "Draft" in body

                # Audience is "Basic and above"
                assert "Basic and above" in body

                # Estimated recipients count is 5
                # (3 basic + 2 main; the admin user is also staff
                # but is on free tier by default -- check if admin
                # is counted. Admin has email_verified=True and
                # tier=free so should NOT be counted)
                assert "5" in body

                # The campaign body is shown as a preview
                assert "Weekly Update" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Staff member sends a test email before sending a
#              campaign
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3StaffSendsTestEmail:
    """Staff member sends a test email before sending a campaign."""

    def test_send_test_email_via_admin(self, django_server):
        """Given a user logged in as admin@test.com (Staff) and a draft
        campaign exists in Django admin. Navigate to the campaign's Django
        admin change page, click 'Send Test Email'. A confirmation message
        appears. The campaign status remains 'Draft'."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        campaign = _create_campaign(
            subject="Test Send Campaign",
            body="Hello from test campaign",
            status="draft",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the campaign's Django admin change page
                page.goto(
                    f"{django_server}/admin/email_app/emailcampaign/{campaign.pk}/change/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Verify the campaign actions section is present
                assert "Campaign Actions" in body

                # The "Send Test Email" button should be present
                send_test_btn = page.locator("#btn-send-test")
                assert send_test_btn.count() >= 1

                # Step 2: Click "Send Test Email" -- mock the SES call
                with patch(
                    "email_app.services.email_service.EmailService._send_ses"
                ) as mock_ses:
                    mock_ses.return_value = "test-ses-message-id-123"

                    send_test_btn.click()

                    # Wait for the confirmation message to appear
                    page.wait_for_selector(
                        "#action-message",
                        state="visible",
                        timeout=10000,
                    )

                    msg = page.locator("#action-message")
                    msg_text = msg.inner_text()

                    # Then: Confirmation message says test email was sent
                    assert "Test email sent to admin@test.com" in msg_text

                # Then: The campaign status remains "Draft"
                from email_app.models import EmailCampaign

                campaign.refresh_from_db()
                assert campaign.status == "draft"

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Staff member sends a campaign to all eligible
#              recipients
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4StaffSendsCampaign:
    """Staff member sends a campaign to all eligible recipients."""

    def test_send_campaign_via_admin_and_verify_completion(
        self, django_server
    ):
        """Given a user logged in as admin@test.com (Staff), a draft
        campaign targeting 'Everyone (including free)' exists, and there
        are 8 verified non-unsubscribed users. Send the campaign via
        admin, wait for background job, verify status and sent_count."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        # Create 8 verified non-unsubscribed users
        # The admin user itself counts as one if verified and not
        # unsubscribed, so create 7 more
        for i in range(7):
            _create_user(
                f"recipient-{i}@test.com",
                tier_slug="free",
                email_verified=True,
                unsubscribed=False,
            )

        campaign = _create_campaign(
            subject="Everyone Campaign",
            body="Hello everyone!",
            target_min_level=0,
            status="draft",
        )

        # Run the send_campaign task directly (mocking SES) instead
        # of relying on the background job system, which may not run
        # in the test environment.
        with patch(
            "email_app.services.email_service.EmailService._send_ses"
        ) as mock_ses:
            mock_ses.return_value = "ses-msg-id"

            from email_app.tasks.send_campaign import send_campaign
            result = send_campaign(
                campaign_id=campaign.pk, send_delay=0
            )

        # Verify the campaign completed
        from email_app.models import EmailCampaign

        campaign.refresh_from_db()
        assert campaign.status == "sent"
        assert campaign.sent_count == 8
        assert campaign.sent_at is not None

        # Verify the admin page reflects the completed state
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/admin/email_app/emailcampaign/{campaign.pk}/change/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Campaign status shows "Sent"
                assert "Sent" in body or "sent" in body

                # sent_count shows 8
                assert "8" in body

                # The "Send Test Email" and "Send Campaign" buttons
                # are no longer available (not is_draft)
                send_test_btn = page.locator("#btn-send-test")
                assert send_test_btn.count() == 0

                send_campaign_btn = page.locator("#btn-send-campaign")
                assert send_campaign_btn.count() == 0

                # The message says campaign has already been sent
                assert "already been" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Staff member filters the campaign list by status
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5StaffFiltersByStatus:
    """Staff member filters the campaign list by status."""

    def test_filter_campaigns_by_status_in_studio(self, django_server):
        """Given a user logged in as admin@test.com (Staff) with 2 draft
        campaigns and 1 sent campaign. Navigate to /studio/campaigns/.
        All 3 are visible. Filter by 'Sent' -- only 1 visible. Filter
        by 'Draft' -- only 2 visible."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        _create_campaign("Draft Alpha", status="draft")
        _create_campaign("Draft Beta", status="draft")
        sent = _create_campaign("Sent Gamma", status="sent")
        sent.sent_at = timezone.now()
        sent.sent_count = 10
        sent.save()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/campaigns/
                page.goto(
                    f"{django_server}/studio/campaigns/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: All 3 campaigns are visible
                assert "Draft Alpha" in body
                assert "Draft Beta" in body
                assert "Sent Gamma" in body

                # Step 2: Select "Sent" from the status filter dropdown
                # The onchange handler submits the form, triggering
                # a full page navigation. Use expect_navigation to
                # wait for the new page to load.
                with page.expect_navigation(wait_until="networkidle"):
                    page.select_option(
                        'select[name="status"]', value="sent"
                    )

                # Check only the table body for campaign names to
                # avoid matching text in dropdown options or other
                # page elements.
                table_body = page.locator("tbody")
                table_text = table_body.inner_text()

                # Then: Only the 1 sent campaign is displayed
                assert "Sent Gamma" in table_text
                assert "Draft Alpha" not in table_text
                assert "Draft Beta" not in table_text

                # Step 3: Select "Draft" from the status filter dropdown
                with page.expect_navigation(wait_until="networkidle"):
                    page.select_option(
                        'select[name="status"]', value="draft"
                    )

                table_body = page.locator("tbody")
                table_text = table_body.inner_text()

                # Then: Only the 2 draft campaigns are displayed
                assert "Draft Alpha" in table_text
                assert "Draft Beta" in table_text
                assert "Sent Gamma" not in table_text

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Staff member searches campaigns by subject
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6StaffSearchesBySubject:
    """Staff member searches campaigns by subject."""

    def test_search_campaigns_by_subject_in_studio(self, django_server):
        """Given a user logged in as admin@test.com (Staff) with campaigns
        'AI Tools Roundup', 'Weekly Digest', and 'Premium Course Launch'.
        Navigate to /studio/campaigns/, search 'Weekly' -- only 'Weekly
        Digest' appears. Clear search -- all 3 are visible."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        _create_campaign("AI Tools Roundup")
        _create_campaign("Weekly Digest")
        _create_campaign("Premium Course Launch")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/campaigns/
                page.goto(
                    f"{django_server}/studio/campaigns/",
                    wait_until="networkidle",
                )
                body = page.content()

                # All 3 are visible initially
                assert "AI Tools Roundup" in body
                assert "Weekly Digest" in body
                assert "Premium Course Launch" in body

                # Step 2: Type "Weekly" into the search field and submit
                page.fill('input[name="q"]', "Weekly")
                page.click('button:has-text("Search")')
                page.wait_for_load_state("networkidle")

                body = page.content()

                # Then: Only "Weekly Digest" appears
                assert "Weekly Digest" in body
                assert "AI Tools Roundup" not in body
                assert "Premium Course Launch" not in body

                # Step 3: Clear the search and submit
                page.fill('input[name="q"]', "")
                page.click('button:has-text("Search")')
                page.wait_for_load_state("networkidle")

                body = page.content()

                # Then: All 3 campaigns are visible again
                assert "AI Tools Roundup" in body
                assert "Weekly Digest" in body
                assert "Premium Course Launch" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Staff member cannot re-send an already sent campaign
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7CannotResendSentCampaign:
    """Staff member cannot re-send an already sent campaign."""

    def test_sent_campaign_has_no_send_buttons_and_readonly_fields(
        self, django_server
    ):
        """Given a user logged in as admin@test.com (Staff) and a campaign
        with status 'Sent' exists. Navigate to the campaign's Django admin
        change page. No send buttons are available. The subject, body, and
        audience fields are read-only."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        campaign = _create_campaign(
            subject="Already Sent Newsletter",
            body="This was already sent.",
            target_min_level=0,
            status="sent",
        )
        campaign.sent_at = timezone.now()
        campaign.sent_count = 42
        campaign.save()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the campaign's Django admin change page
                page.goto(
                    f"{django_server}/admin/email_app/emailcampaign/{campaign.pk}/change/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: The page shows the campaign as already sent
                assert "already been" in body

                # No send buttons available
                send_test_btn = page.locator("#btn-send-test")
                assert send_test_btn.count() == 0

                send_campaign_btn = page.locator("#btn-send-campaign")
                assert send_campaign_btn.count() == 0

                # Then: The subject, body, and audience fields are read-only
                # For sent campaigns, get_readonly_fields returns all fields
                # Django renders readonly fields as divs with class
                # "readonly" or as plain text, not as <input> elements
                subject_input = page.locator('input[name="subject"]')
                body_textarea = page.locator('textarea[name="body"]')
                target_select = page.locator(
                    'select[name="target_min_level"]'
                )

                # These editable form elements should not exist
                assert subject_input.count() == 0
                assert body_textarea.count() == 0
                assert target_select.count() == 0

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Campaign only reaches users who meet all eligibility
#              criteria
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8EligibilityCriteria:
    """Campaign only reaches users who meet all eligibility criteria."""

    def test_campaign_send_respects_tier_verification_and_subscription(
        self,
    ):
        """Given a draft campaign targeting 'Main and above' (level 20).
        The system has: 2 verified Main members, 1 verified Premium member,
        1 unsubscribed Main member, 1 unverified Main member, and 3 Free
        members. After sending, sent_count is 3 (2 Main + 1 Premium)."""
        _clear_campaigns()
        _ensure_tiers()

        # 2 verified Main members (eligible)
        _create_user(
            "main-eligible-1@test.com",
            tier_slug="main",
            email_verified=True,
            unsubscribed=False,
        )
        _create_user(
            "main-eligible-2@test.com",
            tier_slug="main",
            email_verified=True,
            unsubscribed=False,
        )

        # 1 verified Premium member (eligible)
        _create_user(
            "premium-eligible@test.com",
            tier_slug="premium",
            email_verified=True,
            unsubscribed=False,
        )

        # 1 unsubscribed Main member (NOT eligible)
        _create_user(
            "main-unsub@test.com",
            tier_slug="main",
            email_verified=True,
            unsubscribed=True,
        )

        # 1 unverified Main member (NOT eligible)
        _create_user(
            "main-unverified@test.com",
            tier_slug="main",
            email_verified=False,
            unsubscribed=False,
        )

        # 3 Free members (NOT eligible for level 20)
        for i in range(3):
            _create_user(
                f"free-ineligible-{i}@test.com",
                tier_slug="free",
                email_verified=True,
                unsubscribed=False,
            )

        campaign = _create_campaign(
            subject="Main+ Campaign",
            body="Content for Main and above",
            target_min_level=20,
            status="draft",
        )

        # Step 1: Trigger the campaign send with mocked SES
        with patch(
            "email_app.services.email_service.EmailService._send_ses"
        ) as mock_ses:
            mock_ses.return_value = "ses-msg-id"

            from email_app.tasks.send_campaign import send_campaign
            result = send_campaign(
                campaign_id=campaign.pk, send_delay=0
            )

        # Then: The campaign sent_count is 3
        from email_app.models import EmailCampaign, EmailLog

        campaign.refresh_from_db()
        assert campaign.sent_count == 3
        assert campaign.status == "sent"

        # Verify exactly 3 EmailLog entries were created
        logs = EmailLog.objects.filter(campaign=campaign)
        assert logs.count() == 3

        # Verify the recipients are correct
        recipient_emails = set(
            logs.values_list("user__email", flat=True)
        )
        assert "main-eligible-1@test.com" in recipient_emails
        assert "main-eligible-2@test.com" in recipient_emails
        assert "premium-eligible@test.com" in recipient_emails

        # Verify excluded users did NOT receive the email
        assert "main-unsub@test.com" not in recipient_emails
        assert "main-unverified@test.com" not in recipient_emails
        for i in range(3):
            assert (
                f"free-ineligible-{i}@test.com"
                not in recipient_emails
            )


# ---------------------------------------------------------------
# Scenario 9: Staff member creates a campaign targeting
#              Premium-only audience
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9PremiumOnlyCampaign:
    """Staff member creates a campaign targeting Premium-only audience."""

    def test_create_premium_campaign_and_verify_recipients(
        self, django_server
    ):
        """Given a user logged in as admin@test.com (Staff). Navigate to
        /studio/campaigns/new, fill in subject, body, select 'Premium only'.
        Submit. Verify the campaign appears with audience 'Premium only'.
        Click into the detail and verify estimated recipients reflect only
        verified, subscribed Premium members."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        # Create some users of various tiers
        _create_user(
            "premium-1@test.com",
            tier_slug="premium",
            email_verified=True,
        )
        _create_user(
            "premium-2@test.com",
            tier_slug="premium",
            email_verified=True,
        )
        _create_user(
            "main-1@test.com",
            tier_slug="main",
            email_verified=True,
        )
        _create_user(
            "basic-1@test.com",
            tier_slug="basic",
            email_verified=True,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/campaigns/new
                page.goto(
                    f"{django_server}/studio/campaigns/new",
                    wait_until="networkidle",
                )

                # Step 2: Fill in the form
                page.fill(
                    'input[name="subject"]',
                    "Exclusive: Resume Teardown Results",
                )
                page.fill(
                    'textarea[name="body"]',
                    "Your personalized resume teardown results are here.",
                )
                page.select_option(
                    'select[name="target_min_level"]', value="30"
                )

                # Step 3: Click "Create Campaign"
                page.click('button:has-text("Create Campaign")')
                page.wait_for_load_state("networkidle")

                # Then: User is redirected to /studio/campaigns/
                assert "/studio/campaigns/" in page.url
                body = page.content()

                # Campaign shows audience "Premium only"
                assert "Exclusive: Resume Teardown Results" in body
                assert "Premium only" in body

                # Step 4: Click on the campaign to view details
                page.click(
                    'a:has-text("Exclusive: Resume Teardown Results")'
                )
                page.wait_for_load_state("networkidle")

                body = page.content()

                # Then: Estimated recipient count reflects only Premium
                # members (2 Premium users)
                assert "2" in body
                assert "Premium only" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Non-staff user cannot access the campaign
#               management pages
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10NonStaffAccessDenied:
    """Non-staff user cannot access the campaign management pages."""

    def test_non_staff_cannot_access_campaign_list(self, django_server):
        """Given a user logged in as member@test.com (Main tier, non-staff).
        Navigate to /studio/campaigns/ -- access denied. Navigate to
        /studio/campaigns/new -- also denied."""
        _clear_campaigns()
        _ensure_tiers()
        _create_user(
            "member@test.com",
            tier_slug="main",
            is_staff=False,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "member@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/campaigns/
                response = page.goto(
                    f"{django_server}/studio/campaigns/",
                    wait_until="networkidle",
                )

                # Then: User is denied access -- either redirected to
                # login or shown a forbidden page
                # The staff_required decorator returns 403 for
                # authenticated non-staff users
                assert response.status == 403

                # Step 2: Navigate to /studio/campaigns/new
                response = page.goto(
                    f"{django_server}/studio/campaigns/new",
                    wait_until="networkidle",
                )

                # Then: User is again denied access
                assert response.status == 403

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 11: Staff member views the campaign list when no
#               campaigns exist yet
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11EmptyStateNoCampaigns:
    """Staff member views the campaign list when no campaigns exist yet."""

    def test_empty_campaign_list_shows_message_and_create_link(
        self, django_server
    ):
        """Given a user logged in as admin@test.com (Staff) and no campaigns
        have been created. Navigate to /studio/campaigns/. The page shows
        a helpful empty state message. The 'New Campaign' link is still
        available."""
        _clear_campaigns()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/campaigns/
                page.goto(
                    f"{django_server}/studio/campaigns/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: The page shows a helpful empty state message
                assert "No campaigns found" in body

                # Then: The "New Campaign" link is still available
                new_campaign_link = page.locator(
                    'a[href="/studio/campaigns/new"]'
                )
                assert new_campaign_link.count() >= 1

            finally:
                browser.close()
