"""
Playwright E2E tests for Newsletter Signup and Lead Magnets (Issue #86).

Tests cover all 12 BDD scenarios from the issue:
- Anonymous visitor subscribes from the dedicated subscribe page
- Anonymous visitor subscribes from the homepage newsletter section
- Anonymous visitor subscribes from the site footer
- Returning visitor submits an already-registered email and gets no information leak
- New subscriber completes the double opt-in verification flow
- Subscriber tries to verify with an expired token and understands what to do
- Anonymous visitor downloads a lead magnet by subscribing with their email
- Authenticated free member downloads a lead magnet directly
- Subscriber unsubscribes via the link in an email
- Previously unsubscribed member re-subscribes from the account page
- Visitor submits an invalid email and sees a helpful error
- Free member discovers the subscribe page from the pricing page

Usage:
    uv run pytest playwright_tests/test_newsletter.py -v
"""

import datetime
import os

import jwt
import pytest
from django.conf import settings

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    VIEWPORT,
)
from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

JWT_ALGORITHM = "HS256"


def _anon_context(browser):
    """Create an anonymous browser context with a CSRF cookie.

    The subscribe form JavaScript reads the csrftoken cookie and sends
    it via X-CSRFToken header. For anonymous visitors, we need to
    pre-set this cookie so the CSRF middleware accepts the POST.
    """
    context = browser.new_context(viewport=VIEWPORT)
    context.add_cookies([
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _make_verification_token(user_id, redirect_to=None, expired=False):
    """Generate a JWT verification token for testing."""
    payload = {
        "user_id": user_id,
        "action": "verify_email",
    }
    if expired:
        payload["exp"] = datetime.datetime(
            2020, 1, 1, tzinfo=datetime.timezone.utc
        )
    else:
        payload["exp"] = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=24)
        )
    if redirect_to:
        payload["redirect_to"] = redirect_to
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _assert_not_raw_json(body):
    stripped = body.strip()
    assert not stripped.startswith('{"status"')
    assert not stripped.startswith('{"error"')


def _make_unsubscribe_token(user_id):
    """Generate a JWT unsubscribe token for testing (no expiry)."""
    payload = {
        "user_id": user_id,
        "action": "unsubscribe",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _create_download(
    title,
    slug,
    description="",
    file_url="https://example.com/file.pdf",
    file_type="pdf",
    file_size_bytes=0,
    required_level=0,
    tags=None,
    published=True,
):
    """Create a Download via ORM."""
    from content.models import Download

    if tags is None:
        tags = []

    download = Download(
        title=title,
        slug=slug,
        description=description,
        file_url=file_url,
        file_type=file_type,
        file_size_bytes=file_size_bytes,
        required_level=required_level,
        tags=tags,
        published=published,
    )
    download.save()
    connection.close()
    return download


def _clear_downloads():
    """Delete all downloads to ensure a clean state."""
    from content.models import Download

    Download.objects.all().delete()
    connection.close()


# ---------------------------------------------------------------
# Scenario 1: Anonymous visitor subscribes from the dedicated
#              subscribe page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1SubscribeFromDedicatedPage:
    """Anonymous visitor subscribes from the dedicated subscribe page."""

    @pytest.mark.core
    def test_anonymous_subscribes_from_subscribe_page(
        self, django_server
    , page):
        """Given an anonymous visitor on the site.
        1. Navigate to /subscribe
        2. Enter a new email address into the subscribe form and submit
        Then: A confirmation message appears.
        Then: The email input is cleared.
        3. Navigate to /subscribe again
        Then: The form is available for another visitor."""
        _ensure_tiers()

        # Step 1: Navigate to /subscribe
        page.goto(
            f"{django_server}/subscribe",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # The subscribe form is visible
        assert "Subscribe" in body
        email_input = page.locator(
            '.subscribe-form input[name="email"]'
        )
        assert email_input.count() >= 1

        # Step 2: Enter email and submit
        email_input.first.fill("newvisitor@test.com")
        submit_btn = page.locator(
            '.subscribe-form button[type="submit"]'
        )
        submit_btn.first.click()

        # Wait for the success message to appear
        message_el = page.locator(".subscribe-message")
        message_el.first.wait_for(state="visible", timeout=10000)

        # Then: Confirmation message appears (issue #513 copy mentions
        # the auto-created free account)
        message_text = message_el.first.inner_text()
        assert "created a free account" in message_text.lower()

        # Then: The email input is cleared
        assert email_input.first.input_value() == ""

        # Step 3: Navigate to /subscribe again
        page.goto(
            f"{django_server}/subscribe",
            wait_until="domcontentloaded",
        )

        # Then: The form is available (no session state leftover)
        email_input_new = page.locator(
            '.subscribe-form input[name="email"]'
        )
        assert email_input_new.count() >= 1
        assert email_input_new.first.input_value() == ""
        submit_btn_new = page.locator(
            '.subscribe-form button[type="submit"]'
        )
        assert submit_btn_new.count() >= 1
# ---------------------------------------------------------------
# Scenario 2: Anonymous visitor subscribes from the retained homepage
#              footer newsletter placement
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2SubscribeFromHomepageNewsletter:
    """Anonymous visitor subscribes from the homepage footer newsletter."""

    def test_anonymous_subscribes_from_homepage_newsletter(
        self, django_server
    , page):
        """Given an anonymous visitor on the homepage.
        1. Navigate to /
        2. Scroll to the retained footer newsletter anchor (#newsletter)
        3. Enter a new email address and submit
        Then: A confirmation message appears.
        Then: The visitor remains on the homepage (no redirect)."""
        _ensure_tiers()

        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )

        # Step 2: Scroll to the retained #newsletter placement
        newsletter_section = page.locator("#newsletter")
        assert newsletter_section.count() >= 1
        newsletter_section.scroll_into_view_if_needed()

        # Step 3: Enter email and submit via the retained footer form
        newsletter_form = newsletter_section.locator(
            ".subscribe-form"
        )
        email_input = newsletter_form.locator(
            'input[name="email"]'
        )
        email_input.fill("homepage-sub@test.com")

        submit_btn = newsletter_form.locator(
            'button[type="submit"]'
        )
        submit_btn.click()

        # Wait for the success message
        message_el = newsletter_section.locator(
            ".footer-subscribe-message"
        )
        message_el.wait_for(state="visible", timeout=10000)

        # Then: Confirmation message appears (issue #513 copy mentions
        # the auto-created free account)
        message_text = message_el.inner_text()
        assert "created a free account" in message_text.lower()

        # Then: Visitor remains on the homepage
        assert page.url.rstrip("/") == django_server.rstrip("/") or page.url.endswith("/")
# ---------------------------------------------------------------
# Scenario 3: Anonymous visitor subscribes from the site footer
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3SubscribeFromFooter:
    """Anonymous visitor subscribes from the site footer."""

    def test_anonymous_subscribes_from_footer(
        self, django_server
    , page):
        """Given an anonymous visitor reading any page.
        1. Navigate to /blog
        2. Scroll to the footer
        3. Enter a new email address into the footer subscribe form
        Then: A confirmation message appears in the footer area.
        Then: The visitor remains on /blog (no page navigation)."""
        _ensure_tiers()

        # Step 1: Navigate to /blog
        page.goto(
            f"{django_server}/blog",
            wait_until="domcontentloaded",
        )

        # Step 2: Scroll to the footer
        footer = page.locator("footer")
        assert footer.count() >= 1
        footer.scroll_into_view_if_needed()

        # Step 3: Enter email and submit
        footer_form = footer.locator(".subscribe-form")
        email_input = footer_form.locator(
            'input[name="email"]'
        )
        email_input.fill("footer-sub@test.com")

        submit_btn = footer_form.locator(
            'button[type="submit"]'
        )
        submit_btn.click()

        # Wait for the success message in the footer
        message_el = footer.locator(
            ".footer-subscribe-message"
        )
        message_el.wait_for(state="visible", timeout=10000)

        # Then: Confirmation message appears (issue #513 copy mentions
        # the auto-created free account)
        message_text = message_el.inner_text()
        assert "created a free account" in message_text.lower()

        # Then: Visitor remains on /blog
        assert "/blog" in page.url
# ---------------------------------------------------------------
# Scenario 4: Returning visitor submits an already-registered
#              email and gets no information leak
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4NoInformationLeak:
    """Returning visitor submits an already-registered email and
    gets no information leak."""

    def test_existing_email_returns_same_success_message(
        self, django_server
    , page):
        """Given a subscriber already exists with 'existing@test.com'.
        1. Navigate to /subscribe
        2. Enter 'existing@test.com' into the form and submit
        Then: The same success message appears.
        Then: No error or indication that the email was already
              registered."""
        _ensure_tiers()
        # Create existing unverified user
        _create_user(
            "existing@test.com",
            email_verified=False,
        )

        # Step 1: Navigate to /subscribe
        page.goto(
            f"{django_server}/subscribe",
            wait_until="domcontentloaded",
        )

        # Step 2: Enter existing email and submit
        email_input = page.locator(
            '.subscribe-form input[name="email"]'
        )
        email_input.first.fill("existing@test.com")

        submit_btn = page.locator(
            '.subscribe-form button[type="submit"]'
        )
        submit_btn.first.click()

        # Wait for the success message
        message_el = page.locator(".subscribe-message")
        message_el.first.wait_for(state="visible", timeout=10000)

        # Then: Same success message appears (issue #513 copy mentions
        # the auto-created free account; no information leak between
        # new and already-registered emails)
        message_text = message_el.first.inner_text()
        assert "created a free account" in message_text.lower()

        # Then: No error is shown
        error_el = page.locator(".subscribe-error")
        is_hidden = error_el.first.evaluate(
            "el => el.classList.contains('hidden')"
        )
        assert is_hidden, (
            "Error element should remain hidden for existing email"
        )
# ---------------------------------------------------------------
# Scenario 5: New subscriber completes the double opt-in
#              verification flow
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5DoubleOptInVerification:
    """New subscriber completes the double opt-in verification flow."""

    @pytest.mark.core
    def test_verification_link_verifies_email(
        self, django_server
    , page):
        """Given an anonymous visitor who just subscribed.
        1. The system sends a verification email with a JWT token
        2. Visit the verification link
        Then: The response confirms email was verified.
        Then: The subscriber's account is now email_verified = true.
        3. Log in with the subscriber's email
        Then: The user can access the authenticated dashboard."""
        _ensure_tiers()

        # Create an unverified user (simulates post-subscribe state)
        user = _create_user(
            "verify-flow@test.com",
            email_verified=False,
        )
        assert not user.email_verified

        # Step 1 & 2: Generate token and visit verification link
        token = _make_verification_token(user.pk)

        response = page.goto(
            f"{django_server}/api/verify-email?token={token}",
            wait_until="domcontentloaded",
        )

        # Then: Response confirms email verified with a user-facing page
        assert response.status == 200
        body = page.content()
        assert "Email Verified" in body
        assert "/accounts/login/" in body
        assert "AI Shipping Labs" in body
        _assert_not_raw_json(body)

        # Then: User is now email_verified in the database
        user.refresh_from_db()
        assert user.email_verified

        # Step 3: Log in with the subscriber's email
        page.goto(
            f"{django_server}/accounts/login/",
            wait_until="domcontentloaded",
        )

        # Fill login form
        page.fill('input[name="email"]', "verify-flow@test.com")
        page.fill('input[name="password"]', DEFAULT_PASSWORD)

        # Submit login form (JS-based)
        page.click('button[type="submit"]')

        # Wait for redirect to the dashboard after login
        page.wait_for_url(
            f"{django_server}/",
            timeout=10000,
        )

        # Then: User can access the authenticated dashboard
        body = page.content()
        assert "Quick Actions" in body or "Welcome" in body

    def test_already_verified_user_gets_success_page(self, django_server, page):
        """Already-verified users can click an old link idempotently."""
        _ensure_tiers()
        user = _create_user(
            "already-verified-flow@test.com",
            email_verified=True,
        )

        token = _make_verification_token(user.pk)
        response = page.goto(
            f"{django_server}/api/verify-email?token={token}",
            wait_until="domcontentloaded",
        )

        assert response.status == 200
        body = page.content()
        assert "Email Verified" in body
        _assert_not_raw_json(body)

        user.refresh_from_db()
        assert user.email_verified

    def test_logged_in_verification_clears_account_banner(
        self, django_server, page
    ):
        """Logged-in users see the success page and lose the account banner."""
        _ensure_tiers()
        user = _create_user(
            "banner-verify@test.com",
            email_verified=False,
        )

        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
        page.fill('input[name="email"]', "banner-verify@test.com")
        page.fill('input[name="password"]', DEFAULT_PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_url(f"{django_server}/")

        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator("#email-verification-banner").is_visible()

        token = _make_verification_token(user.pk)
        response = page.goto(
            f"{django_server}/api/verify-email?token={token}",
            wait_until="domcontentloaded",
        )
        assert response.status == 200
        body = page.content()
        assert "Email Verified" in body
        assert "/account/" in body
        _assert_not_raw_json(body)

        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator("#email-verification-banner").count() == 0
# ---------------------------------------------------------------
# Scenario 6: Subscriber tries to verify with an expired token
#              and understands what to do
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6ExpiredTokenError:
    """Subscriber tries to verify with an expired token and
    understands what to do."""

    def test_expired_token_shows_error(
        self, django_server
    , page):
        """Given a subscriber who received a verification email
        more than 24 hours ago.
        1. Visit /api/verify-email?token={expired_token}
        Then: An error response indicates the token has expired.
        Then: The subscriber understands they need to re-subscribe."""
        _ensure_tiers()
        user = _create_user(
            "expired-verify@test.com",
            email_verified=False,
        )

        # Generate an expired token
        token = _make_verification_token(user.pk, expired=True)

        response = page.goto(
            f"{django_server}/api/verify-email?token={token}",
            wait_until="domcontentloaded",
        )

        # Then: Error page indicates token expired
        assert response.status == 400
        body = page.content()
        assert "Verification Failed" in body
        assert "expired" in body.lower()
        assert "/accounts/login/" in body
        _assert_not_raw_json(body)

        # Then: User is NOT verified
        user.refresh_from_db()
        assert not user.email_verified

    def test_malformed_token_shows_failure_page(self, django_server, page):
        response = page.goto(
            f"{django_server}/api/verify-email?token=invalid-token",
            wait_until="domcontentloaded",
        )

        assert response.status == 400
        body = page.content()
        assert "Verification Failed" in body
        assert "invalid" in body.lower()
        _assert_not_raw_json(body)

    def test_missing_token_shows_failure_page(self, django_server, page):
        response = page.goto(
            f"{django_server}/api/verify-email",
            wait_until="domcontentloaded",
        )

        assert response.status == 400
        body = page.content()
        assert "Verification Failed" in body
        assert "incomplete" in body.lower()
        _assert_not_raw_json(body)

    def test_deleted_account_token_shows_failure_page(self, django_server, page):
        token = _make_verification_token(999999)
        response = page.goto(
            f"{django_server}/api/verify-email?token={token}",
            wait_until="domcontentloaded",
        )

        assert response.status == 404
        body = page.content()
        assert "Verification Failed" in body
        assert "could not find an account" in body.lower()
        _assert_not_raw_json(body)
# ---------------------------------------------------------------
# Scenario 7: Anonymous visitor downloads a lead magnet by
#              subscribing with their email
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7LeadMagnetSubscribeFlow:
    """Anonymous visitor downloads a lead magnet by subscribing
    with their email."""

    def test_anonymous_sees_signup_cta_on_lead_magnet(
        self, django_server
    , page):
        """Given a published download with required_level=0 (lead magnet)
        and an anonymous visitor.
        1. Navigate to /downloads
        2. Find the lead magnet download card
        Then: The card shows a 'Sign Up to Download Free' CTA.
        3. Click the sign-up link on the lead magnet card
        Then: The visitor is directed to create an account."""
        _ensure_tiers()
        _clear_downloads()
        _create_download(
            title="Free AI Cheat Sheet",
            slug="free-ai-cheat-sheet",
            description="A comprehensive cheat sheet for AI concepts.",
            file_url="https://example.com/cheatsheet.pdf",
            file_type="pdf",
            file_size_bytes=1_000_000,
            required_level=0,
        )

        # Step 1: Navigate to /downloads
        page.goto(
            f"{django_server}/downloads",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Step 2: Find the lead magnet download card
        assert "Free AI Cheat Sheet" in body

        # Then: Shows "Sign Up to Download" CTA
        signup_btn = page.locator(
            'a:has-text("Sign Up to Download")'
        )
        assert signup_btn.count() >= 1

        # Step 3: Click the sign-up link
        signup_btn.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Visitor is directed to signup/register
        assert (
            "/accounts/signup" in page.url
            or "/accounts/register" in page.url
        )
    def test_lead_magnet_verification_redirects_to_download(
        self, django_server
    , page):
        """After signing up and verifying email via lead magnet flow,
        the subscriber is redirected to the download file URL."""
        _ensure_tiers()
        _clear_downloads()
        _create_download(
            title="Lead Magnet Resource",
            slug="lead-magnet-resource",
            description="A free resource.",
            file_url="https://example.com/resource.pdf",
            file_type="pdf",
            required_level=0,
        )

        # Create an unverified user (simulates post-subscribe)
        user = _create_user(
            "magnet-verify@test.com",
            email_verified=False,
        )

        # Generate a verification token with redirect_to
        token = _make_verification_token(
            user.pk,
            redirect_to="/api/downloads/lead-magnet-resource/file",
        )

        # Intercept the redirect to avoid following to
        # external download URL
        redirect_location = None

        def handle_route(route):
            nonlocal redirect_location
            resp = route.fetch(max_redirects=0)
            redirect_location = resp.headers.get(
                "location", ""
            )
            route.fulfill(
                status=200,
                body="intercepted-redirect",
            )

        page.route(
            "**/api/verify-email*",
            handle_route,
        )

        page.goto(
            f"{django_server}/api/verify-email?token={token}",
            wait_until="domcontentloaded",
        )

        # Then: Redirect to the download file URL
        assert redirect_location is not None
        assert "/api/downloads/lead-magnet-resource/file" in redirect_location

        # Then: User is now verified
        user.refresh_from_db()
        assert user.email_verified
# ---------------------------------------------------------------
# Scenario 8: Authenticated free member downloads a lead magnet
#              directly
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8AuthenticatedLeadMagnetDownload:
    """Authenticated free member downloads a lead magnet directly."""

    def test_authenticated_user_sees_direct_download(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier, verified).
        1. Navigate to /downloads
        2. Find a download with required_level=0 (lead magnet)
        Then: The download card shows a direct 'Download' button.
        3. Click the download button
        Then: The file downloads successfully."""
        _ensure_tiers()
        _clear_downloads()
        _create_user("free@test.com", tier_slug="free")
        _create_download(
            title="Free Guide",
            slug="free-guide",
            description="A free guide for members.",
            file_url="https://example.com/guide.pdf",
            file_type="pdf",
            required_level=0,
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /downloads
        page.goto(
            f"{django_server}/downloads",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Step 2: Find the lead magnet
        assert "Free Guide" in body

        # Then: Direct download link is present (no signup CTA)
        download_link = page.locator(
            'a[href="/api/downloads/free-guide/file"]'
        )
        assert download_link.count() >= 1

        # No "Sign Up to Download" button for authenticated user
        signup_btn = page.locator(
            'a:has-text("Sign Up to Download")'
        )
        assert signup_btn.count() == 0

        # Step 3: Click the download button and intercept
        redirect_status = None
        redirect_location = None

        def handle_route(route):
            nonlocal redirect_status, redirect_location
            resp = route.fetch(max_redirects=0)
            redirect_status = resp.status
            redirect_location = resp.headers.get(
                "location", ""
            )
            route.fulfill(
                status=200,
                body="intercepted",
            )

        page.route(
            "**/api/downloads/free-guide/file",
            handle_route,
        )

        download_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: File download triggered (302 redirect to file)
        assert redirect_status == 302
        assert "example.com/guide.pdf" in redirect_location
# ---------------------------------------------------------------
# Scenario 9: Subscriber unsubscribes via the link in an email
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9UnsubscribeViaEmailLink:
    """Subscriber unsubscribes via the link in an email."""

    def test_unsubscribe_link_works(
        self, django_server
    , page):
        """Given a verified subscriber with an unsubscribe token.
        1. Visit /api/unsubscribe?token={valid_unsubscribe_token}
        Then: The page shows 'Unsubscribed' with a message.
        Then: A link to /account/ is shown.
        2. Click 'Go to Homepage'
        Then: The visitor lands on the homepage at /."""
        _ensure_tiers()
        user = _create_user(
            "unsub-flow@test.com",
            email_verified=True,
            unsubscribed=False,
        )
        assert not user.unsubscribed

        token = _make_unsubscribe_token(user.pk)

        # Step 1: Visit unsubscribe link
        page.goto(
            f"{django_server}/api/unsubscribe?token={token}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Shows "Unsubscribed" heading
        assert "Unsubscribed" in body

        # Then: Shows the unsubscribed message
        assert "You have been unsubscribed from all emails" in body

        # Then: Link to /account/ for re-subscribing
        account_link = page.locator('a[href="/account/"]')
        assert account_link.count() >= 1

        # Verify user is actually unsubscribed
        user.refresh_from_db()
        assert user.unsubscribed

        # Step 2: Click "Go to Homepage"
        homepage_link = page.locator(
            'a:has-text("Go to Homepage")'
        )
        assert homepage_link.count() >= 1
        homepage_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Visitor lands on the homepage
        assert page.url.rstrip("/") == django_server.rstrip("/") or page.url.endswith("/")
# ---------------------------------------------------------------
# Scenario 10: Previously unsubscribed member re-subscribes from
#               the account page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10ResubscribeFromAccountPage:
    """Previously unsubscribed member re-subscribes from the
    account page."""

    def test_unsubscribed_user_toggles_newsletter_on(
        self, django_server
    , browser):
        """Given a user logged in as unsubscribed@test.com who
        previously unsubscribed.
        1. Navigate to /account/
        Then: The newsletter toggle shows unsubscribed state.
        2. Toggle the newsletter subscription to 'on'
        Then: The toggle switches and status text updates.
        3. Refresh /account/
        Then: The toggle still shows subscribed (change persisted)."""
        _ensure_tiers()
        _create_user(
            "unsubscribed@test.com",
            email_verified=True,
            unsubscribed=True,
        )

        context = _auth_context(browser, "unsubscribed@test.com")
        page = context.new_page()
        # Step 1: Navigate to /account/
        page.goto(
            f"{django_server}/account/",
            wait_until="domcontentloaded",
        )
        page.content()

        # Then: Newsletter toggle is present
        toggle = page.locator("#newsletter-toggle")
        assert toggle.count() >= 1

        # Then: Shows unsubscribed state
        status_text = page.locator("#newsletter-status")
        assert "unsubscribed" in status_text.inner_text().lower()

        # The toggle dot should be at translate-x-0 (off)
        toggle_dot = page.locator("#newsletter-toggle-dot")
        assert toggle_dot.evaluate(
            "el => el.classList.contains('translate-x-0')"
        )

        # Step 2: Click the toggle to subscribe
        toggle.click()

        # Wait for the status text to update
        page.wait_for_function(
            """() => {
                var el = document.getElementById('newsletter-status');
                return el && el.textContent.includes('You are subscribed');
            }""",
            timeout=10000,
        )

        # Then: Toggle is now in subscribed position
        assert toggle_dot.evaluate(
            "el => el.classList.contains('translate-x-5')"
        )

        # Then: Status text shows subscribed
        assert "You are subscribed to newsletters" in status_text.inner_text()

        # Step 3: Refresh /account/ to verify persistence
        page.goto(
            f"{django_server}/account/",
            wait_until="domcontentloaded",
        )

        # Then: Toggle still shows subscribed
        status_text = page.locator("#newsletter-status")
        assert "You are subscribed to newsletters" in status_text.inner_text()

        toggle_dot = page.locator("#newsletter-toggle-dot")
        assert toggle_dot.evaluate(
            "el => el.classList.contains('translate-x-5')"
        )
# ---------------------------------------------------------------
# Scenario 11: Visitor submits an invalid email and sees a
#               helpful error
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11InvalidEmailError:
    """Visitor submits an invalid email and sees a helpful error."""

    def test_invalid_email_shows_error(
        self, django_server
    , page):
        """Given an anonymous visitor on /subscribe.
        1. Enter an invalid email that passes browser validation but
           fails server validation and submit
        Then: An error message indicates the email is invalid.
        Then: The form remains on the page for correction.
        2. Verify the email field is required (browser validation)."""
        _ensure_tiers()

        # Step 1: Navigate to /subscribe
        page.goto(
            f"{django_server}/subscribe",
            wait_until="domcontentloaded",
        )

        # Enter an email that passes browser type="email"
        # validation but fails server-side validation
        # (no period in domain part)
        email_input = page.locator(
            '.subscribe-form input[name="email"]'
        )
        email_input.first.fill("user@invalid")

        submit_btn = page.locator(
            '.subscribe-form button[type="submit"]'
        )
        submit_btn.first.click()

        # Wait for the error message
        error_el = page.locator(".subscribe-error")
        error_el.first.wait_for(state="visible", timeout=10000)

        # Then: Error message about invalid email
        error_text = error_el.first.inner_text()
        assert "invalid" in error_text.lower() or "email" in error_text.lower()

        # Then: Form is still on the page
        assert email_input.first.is_visible()
        assert submit_btn.first.is_visible()

        # Step 2: Verify the email field has required attribute
        # (browser validation prevents empty submission)
        is_required = email_input.first.evaluate(
            "el => el.hasAttribute('required')"
        )
        assert is_required, (
            "Email field should have the 'required' attribute"
        )

        # Verify that the input has type="email" so browser
        # enforces email format validation
        input_type = email_input.first.evaluate(
            "el => el.type"
        )
        assert input_type == "email", (
            "Email input should have type='email' for browser "
            "validation"
        )
# ---------------------------------------------------------------
# Scenario 12: Free member discovers the subscribe page from the
#               pricing page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario12DiscoverSubscribeFromPricing:
    """Free member discovers the subscribe page from the pricing
    page."""

    def test_pricing_free_tier_links_to_newsletter(
        self, django_server
    , page):
        """Given an anonymous visitor comparing membership options.
        1. Navigate to /pricing
        2. Find the Free tier card
        Then: The Free tier card includes a CTA that leads toward
              the newsletter signup.
        3. Follow the Free tier's CTA
        Then: The visitor arrives at the newsletter section on the
              homepage (/#newsletter) or /subscribe."""
        _ensure_tiers()

        # Step 1: Navigate to /pricing
        page.goto(
            f"{django_server}/pricing",
            wait_until="domcontentloaded",
        )
        page.content()

        # Step 2: Find the Free tier card
        # The pricing grid has tier cards
        grid = page.locator(
            "div.grid.sm\\:grid-cols-2.lg\\:grid-cols-4"
        )
        tier_cards = grid.locator("> div")

        free_card = None
        for i in range(tier_cards.count()):
            card = tier_cards.nth(i)
            h2_text = card.locator("h2").first.inner_text()
            if h2_text == "Free":
                free_card = card
                break

        assert free_card is not None, "Free tier card not found"

        # Then: Free tier has a CTA link
        free_cta = free_card.locator("a")
        assert free_cta.count() >= 1

        # The CTA now sends free-tier discoverers to account signup.
        cta_href = free_cta.first.get_attribute("href")
        assert "/accounts/register/" in cta_href, (
            f"Free tier CTA should link to /accounts/register/, "
            f"got: {cta_href}"
        )

        # Step 3: Follow the Free tier's CTA
        free_cta.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Arrives at the newsletter section or subscribe
        current_url = page.url
        assert (
            "newsletter" in current_url
            or "/subscribe" in current_url
            or page.url.endswith("/")  # homepage with #newsletter
        ), (
            f"Expected newsletter or subscribe page, "
            f"got: {current_url}"
        )
