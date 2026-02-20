"""
Playwright E2E tests for Downloadable Resources (Issue #77).

Tests cover all 12 BDD scenarios from the issue:
- Visitor browses the downloads catalog and evaluates resources by type and size
- Anonymous visitor encounters a lead magnet and is prompted to sign up
- Anonymous visitor on a gated download sees upgrade CTA, not a signup prompt
- Authorized member downloads a file and the download count increments
- Insufficient-tier member sees upgrade CTA with the file URL never exposed
- Visitor narrows the downloads catalog by clicking a tag chip
- Visitor filters by a tag with no matching downloads and sees helpful empty state
- Shortcode embeds a download card inside an article for an anonymous reader
- Authenticated reader sees a direct download button on an in-article shortcode card
- Free member reads an article with a gated download shortcode and sees upgrade path
- Staff member creates a new download via Studio and it appears on the public listing
- Regular member cannot access the Studio download management area

Usage:
    uv run pytest playwright_tests/test_downloadable_resources.py -v
"""

import datetime
import os

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


def _ensure_tiers():
    """Ensure membership tiers exist."""
    from payments.models import Tier

    TIERS = [
        {"slug": "free", "name": "Free", "level": 0},
        {"slug": "basic", "name": "Basic", "level": 10},
        {"slug": "main", "name": "Main", "level": 20},
        {"slug": "premium", "name": "Premium", "level": 30},
    ]
    for tier_data in TIERS:
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


def _create_staff_user(email="admin@test.com", password=DEFAULT_PASSWORD):
    """Create a staff/superuser."""
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
    user.save()
    return user


def _create_download(
    title,
    slug,
    description="",
    file_url="https://example.com/file.pdf",
    file_type="pdf",
    file_size_bytes=0,
    cover_image_url="",
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
        cover_image_url=cover_image_url,
        required_level=required_level,
        tags=tags,
        published=published,
    )
    download.save()
    return download


def _create_article(
    title,
    slug,
    content_html="",
    description="",
    required_level=0,
    published=True,
    tags=None,
):
    """Create an Article via ORM for shortcode testing."""
    from content.models import Article

    if tags is None:
        tags = []

    article = Article(
        title=title,
        slug=slug,
        description=description,
        content_markdown="",
        content_html=content_html,
        date=datetime.date.today(),
        published=published,
        tags=tags,
        required_level=required_level,
    )
    # Skip auto-rendering by setting content_markdown to empty
    # and providing content_html directly
    article.save()
    return article


def _clear_downloads():
    """Delete all downloads to ensure a clean state."""
    from content.models import Download

    Download.objects.all().delete()


def _clear_articles():
    """Delete all articles to ensure a clean state."""
    from content.models import Article

    Article.objects.all().delete()


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


# ---------------------------------------------------------------
# Scenario 1: Visitor browses the downloads catalog and evaluates
#              resources by type and size
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1VisitorBrowsesCatalog:
    """Visitor browses the downloads catalog and evaluates resources
    by type and size."""

    def test_downloads_catalog_shows_type_badges_sizes_descriptions(
        self, django_server
    ):
        """Two published downloads exist. Anonymous visitor sees both
        download cards with titles, file type badges, human-readable
        file sizes, and descriptions."""
        _clear_downloads()
        _create_download(
            title="AI Cheat Sheet",
            slug="ai-cheat-sheet",
            description="A comprehensive cheat sheet for AI concepts.",
            file_url="https://example.com/cheatsheet.pdf",
            file_type="pdf",
            file_size_bytes=2_500_000,  # 2.5 MB -> displayed as "2.4 MB"
            tags=["ai", "reference"],
        )
        _create_download(
            title="Starter Kit",
            slug="starter-kit",
            description="Everything you need to get started.",
            file_url="https://example.com/starter.zip",
            file_type="zip",
            file_size_bytes=9_961_472,  # 9.5 MB
            tags=["starter"],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/downloads",
                    wait_until="networkidle",
                )
                body = page.content()

                # Both download card titles are visible
                assert "AI Cheat Sheet" in body
                assert "Starter Kit" in body

                # File type badges
                assert "PDF" in body
                assert "ZIP" in body

                # File sizes: 2,500,000 bytes = 2.4 MB
                assert "2.4 MB" in body
                # 9,961,472 bytes = 9.5 MB
                assert "9.5 MB" in body

                # Each card displays its description
                assert "comprehensive cheat sheet" in body
                assert "Everything you need to get started" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Anonymous visitor encounters a lead magnet and is
#              prompted to sign up
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2AnonymousLeadMagnetSignup:
    """Anonymous visitor encounters a lead magnet and is prompted
    to sign up."""

    def test_anonymous_sees_signup_button_for_free_download(
        self, django_server
    ):
        """A free download (required_level=0) shows 'Sign Up to Download'
        button for anonymous visitors, linking to /accounts/signup with
        a next parameter."""
        _clear_downloads()
        _create_download(
            title="Free PDF Guide",
            slug="free-pdf-guide",
            description="A free guide for everyone.",
            file_url="https://example.com/guide.pdf",
            file_type="pdf",
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/downloads",
                    wait_until="networkidle",
                )
                body = page.content()

                # Card for "Free PDF Guide" is visible
                assert "Free PDF Guide" in body

                # Shows "Sign Up to Download" button
                signup_btn = page.locator(
                    'a:has-text("Sign Up to Download")'
                )
                assert signup_btn.count() >= 1

                # Button links to /accounts/signup with next parameter
                href = signup_btn.first.get_attribute("href")
                assert "/accounts/signup" in href
                assert "next=" in href
                assert "/api/downloads/free-pdf-guide/file" in href

                # Click the signup button
                signup_btn.first.click()
                page.wait_for_load_state("networkidle")

                # Visitor lands on the signup page
                assert "/accounts/signup" in page.url
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Anonymous visitor on a gated download sees upgrade
#              CTA, not a signup prompt
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3AnonymousGatedDownloadUpgradeCTA:
    """Anonymous visitor on a gated download sees upgrade CTA,
    not a signup prompt."""

    def test_anonymous_sees_upgrade_cta_for_gated_download(
        self, django_server
    ):
        """A gated download (required_level=10) shows 'Upgrade to Basic
        to download' with a 'View Pricing' link. No direct file download
        link is exposed."""
        _clear_downloads()
        _create_download(
            title="Basic Toolkit",
            slug="basic-toolkit",
            description="A toolkit for Basic members.",
            file_url="https://example.com/toolkit.zip",
            file_type="zip",
            required_level=10,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/downloads",
                    wait_until="networkidle",
                )
                body = page.content()

                # Shows upgrade CTA, not signup form
                assert "Upgrade to Basic to download" in body
                # No signup form should be shown
                signup_btn = page.locator(
                    'a:has-text("Sign Up to Download")'
                )
                assert signup_btn.count() == 0

                # "View Pricing" link points to /pricing
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1
                href = pricing_link.first.get_attribute("href")
                assert "/pricing" in href

                # No direct file download link is present
                file_link = page.locator(
                    'a[href*="/api/downloads/basic-toolkit/file"]'
                )
                assert file_link.count() == 0
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Authorized member downloads a file and the download
#              count increments
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4AuthorizedMemberDownloads:
    """Authorized member downloads a file and the download count
    increments."""

    def test_basic_member_downloads_file_and_count_increments(
        self, django_server
    ):
        """A Basic-tier member sees a 'Download' link for a Basic-gated
        download. Clicking it triggers a redirect (302) to the file URL
        and increments download_count."""
        _clear_downloads()
        _create_user("basic@test.com", tier_slug="basic")
        download = _create_download(
            title="Member Resource",
            slug="member-resource",
            description="A resource for members.",
            file_url="https://example.com/member.pdf",
            file_type="pdf",
            required_level=10,
        )
        initial_count = download.download_count

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/downloads",
                    wait_until="networkidle",
                )
                body = page.content()

                # Shows "Download" link, no upgrade CTA, no signup
                download_link = page.locator(
                    'a[href="/api/downloads/member-resource/file"]'
                )
                assert download_link.count() >= 1
                assert "Upgrade to" not in body
                assert "Sign Up to Download" not in body

                # Use Playwright's route interception to capture
                # the 302 redirect response without following it
                redirect_status = None
                redirect_location = None

                def handle_route(route):
                    nonlocal redirect_status, redirect_location
                    # Fetch the request manually without following
                    # the redirect
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
                    "**/api/downloads/member-resource/file",
                    handle_route,
                )

                # Click the download link
                download_link.first.click()
                page.wait_for_load_state("networkidle")

                # The server responds with a redirect (302)
                assert redirect_status == 302
                assert "example.com/member.pdf" in redirect_location

                # Verify download_count incremented
                from content.models import Download

                download_obj = Download.objects.get(
                    slug="member-resource"
                )
                assert download_obj.download_count == initial_count + 1
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Insufficient-tier member sees upgrade CTA with the
#              file URL never exposed
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5InsufficientTierUpgradeCTA:
    """Insufficient-tier member sees upgrade CTA with the file URL
    never exposed."""

    def test_basic_member_cannot_access_premium_download(
        self, django_server
    ):
        """A Basic-tier member viewing a Premium-gated download sees
        'Upgrade to Premium to download'. The actual file URL
        (https://example.com/secret.pdf) never appears in the page
        source, and no download link exists."""
        _clear_downloads()
        _create_user("basic@test.com", tier_slug="basic")
        _create_download(
            title="Premium Report",
            slug="premium-report",
            description="An exclusive premium report.",
            file_url="https://example.com/secret.pdf",
            file_type="pdf",
            required_level=30,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/downloads",
                    wait_until="networkidle",
                )
                body = page.content()

                # Shows upgrade CTA with correct tier name
                assert "Upgrade to Premium to download" in body

                # "View Pricing" link
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1
                href = pricing_link.first.get_attribute("href")
                assert "/pricing" in href

                # The actual file URL never appears in the page source
                assert "https://example.com/secret.pdf" not in body

                # No download link for this download exists in the DOM
                file_link = page.locator(
                    'a[href*="/api/downloads/premium-report/file"]'
                )
                assert file_link.count() == 0
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Visitor narrows the downloads catalog by clicking
#              a tag chip
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6VisitorFiltersByTag:
    """Visitor narrows the downloads catalog by clicking a tag chip."""

    def test_tag_filter_narrows_to_matching_downloads(
        self, django_server
    ):
        """Two downloads with different tags. Clicking a tag chip
        filters to show only matching downloads. A link to clear
        the filter is available."""
        _clear_downloads()
        _create_download(
            title="Doc A",
            slug="doc-a",
            description="A document about Python and AI.",
            file_url="https://example.com/doc-a.pdf",
            file_type="pdf",
            tags=["python", "ai"],
        )
        _create_download(
            title="Doc B",
            slug="doc-b",
            description="A document about Django.",
            file_url="https://example.com/doc-b.pdf",
            file_type="pdf",
            tags=["django"],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Both cards visible
                page.goto(
                    f"{django_server}/downloads",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "Doc A" in body
                assert "Doc B" in body

                # Step 2: Click the "python" tag chip on Doc A's card
                python_chip = page.locator(
                    'a[href*="tag=python"]'
                ).first
                python_chip.click()
                page.wait_for_load_state("networkidle")

                # URL updates to /downloads?tag=python
                assert "tag=python" in page.url

                # Only Doc A is visible
                body = page.content()
                assert "Doc A" in body

                # Doc B is hidden because it lacks the "python" tag
                # Check within article cards specifically
                cards = page.locator("article")
                cards_text = " ".join(
                    [card.inner_text() for card in cards.all()]
                )
                assert "Doc B" not in cards_text

                # A link to clear the filter and return to /downloads
                # is available
                clear_link = page.locator(
                    'a[href="/downloads"]'
                )
                assert clear_link.count() >= 1
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Visitor filters by a tag with no matching downloads
#              and sees helpful empty state
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7EmptyTagFilter:
    """Visitor filters by a tag with no matching downloads and sees
    helpful empty state."""

    def test_nonexistent_tag_shows_empty_message_and_recovery_link(
        self, django_server
    ):
        """No published downloads tagged 'nonexistent'. The page shows
        an empty message and a 'View all downloads' link."""
        _clear_downloads()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/downloads?tag=nonexistent",
                    wait_until="networkidle",
                )
                body = page.content()

                # No download cards appear
                cards = page.locator("article")
                assert cards.count() == 0

                # The message is displayed
                assert (
                    "No downloads found with the selected tags."
                    in body
                )

                # "View all downloads" link pointing to /downloads
                view_all_link = page.locator(
                    'a:has-text("View all downloads")'
                )
                assert view_all_link.count() >= 1
                href = view_all_link.first.get_attribute("href")
                assert href == "/downloads"
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Shortcode embeds a download card inside an article
#              for an anonymous reader
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8ShortcodeAnonymousReader:
    """Shortcode embeds a download card inside an article for an
    anonymous reader."""

    def test_anonymous_sees_inline_card_with_signup_button(
        self, django_server
    ):
        """A published article contains the shortcode
        {{download:inline-pdf}}. An anonymous visitor sees the inline
        download card with title, description, file type badge, and
        'Sign Up to Download Free' button."""
        _clear_downloads()
        _clear_articles()
        _create_download(
            title="Inline Resource",
            slug="inline-pdf",
            description="Get it here",
            file_url="https://example.com/inline.pdf",
            file_type="pdf",
            required_level=0,
            published=True,
        )
        _create_article(
            title="Article With Download",
            slug="article-with-download",
            content_html=(
                "<p>Here is some intro text.</p>"
                "{{download:inline-pdf}}"
                "<p>More content after.</p>"
            ),
            description="An article containing a download shortcode.",
            published=True,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/blog/article-with-download",
                    wait_until="networkidle",
                )
                body = page.content()

                # Inline download card appears with title
                assert "Inline Resource" in body
                # Description
                assert "Get it here" in body
                # PDF badge
                assert "PDF" in body

                # "Sign Up to Download Free" button with next param
                signup_btn = page.locator(
                    'a:has-text("Sign Up to Download Free")'
                )
                assert signup_btn.count() >= 1
                href = signup_btn.first.get_attribute("href")
                assert "/accounts/signup" in href
                assert "next=" in href
                assert "/api/downloads/inline-pdf/file" in href

                # No direct download link is exposed
                direct_link = page.locator(
                    'a[href="/api/downloads/inline-pdf/file"]'
                )
                assert direct_link.count() == 0
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 9: Authenticated reader sees a direct download button
#              on an in-article shortcode card
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9AuthenticatedShortcodeDownload:
    """Authenticated reader sees a direct download button on an
    in-article shortcode card."""

    def test_authenticated_user_sees_direct_download_link(
        self, django_server
    ):
        """A logged-in Free-tier user viewing the same article with
        {{download:inline-pdf}} sees a 'Download PDF' link and no
        'Sign Up to Download Free' prompt."""
        _clear_downloads()
        _clear_articles()
        _create_user("free@test.com", tier_slug="free")
        _create_download(
            title="Inline Resource",
            slug="inline-pdf",
            description="Get it here",
            file_url="https://example.com/inline.pdf",
            file_type="pdf",
            required_level=0,
            published=True,
        )
        _create_article(
            title="Article With Download",
            slug="article-with-download",
            content_html=(
                "<p>Intro text.</p>"
                "{{download:inline-pdf}}"
                "<p>More content.</p>"
            ),
            description="An article containing a download shortcode.",
            published=True,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/blog/article-with-download",
                    wait_until="networkidle",
                )
                body = page.content()

                # "Download PDF" link pointing to the file endpoint
                download_link = page.locator(
                    'a:has-text("Download PDF")'
                )
                assert download_link.count() >= 1
                href = download_link.first.get_attribute("href")
                assert "/api/downloads/inline-pdf/file" in href

                # "Sign Up to Download Free" prompt is absent
                signup_btn = page.locator(
                    'a:has-text("Sign Up to Download Free")'
                )
                assert signup_btn.count() == 0
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Free member reads an article with a gated download
#               shortcode and sees upgrade path
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10FreeUserGatedShortcode:
    """Free member reads an article with a gated download shortcode
    and sees upgrade path."""

    def test_free_user_sees_upgrade_cta_in_shortcode_card(
        self, django_server
    ):
        """A Free-tier user viewing an article with {{download:gated-slides}}
        (required_level=10) sees 'Upgrade to Basic to download' and a
        'View Pricing' link. No download link is present."""
        _clear_downloads()
        _clear_articles()
        _create_user("free@test.com", tier_slug="free")
        _create_download(
            title="Gated Slides",
            slug="gated-slides",
            description="Slides for Basic members.",
            file_url="https://example.com/slides.pdf",
            file_type="slides",
            required_level=10,
            published=True,
        )
        _create_article(
            title="Article With Gated Download",
            slug="article-gated-download",
            content_html=(
                "<p>Check out these slides.</p>"
                "{{download:gated-slides}}"
            ),
            description="An article with a gated download shortcode.",
            published=True,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/blog/article-gated-download",
                    wait_until="networkidle",
                )
                body = page.content()

                # Inline card for "Gated Slides" shows upgrade CTA
                assert "Gated Slides" in body
                assert "Upgrade to Basic to download" in body

                # "View Pricing" link to /pricing
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1
                href = pricing_link.first.get_attribute("href")
                assert "/pricing" in href

                # No download link is present
                file_link = page.locator(
                    'a[href*="/api/downloads/gated-slides/file"]'
                )
                assert file_link.count() == 0
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 11: Staff member creates a new download via Studio and
#               it appears on the public listing
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11StaffCreatesDownloadViaStudio:
    """Staff member creates a new download via Studio and it appears
    on the public listing."""

    def test_staff_creates_download_in_studio(self, django_server):
        """Staff navigates to Studio, clicks 'New Download', fills in
        the form, submits, and the download appears on the public
        /downloads listing."""
        _clear_downloads()
        _create_staff_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                # Step 1: Navigate to /studio/downloads/
                staff_ctx = _auth_context(browser, "admin@test.com")
                staff_page = staff_ctx.new_page()
                staff_page.goto(
                    f"{django_server}/studio/downloads/",
                    wait_until="networkidle",
                )

                body = staff_page.content()
                assert "Downloads" in body

                # Step 2: Click "New Download"
                new_btn = staff_page.locator(
                    'a:has-text("New Download")'
                )
                assert new_btn.count() >= 1
                new_btn.first.click()
                staff_page.wait_for_load_state("networkidle")

                # Step 3: Fill in the form
                staff_page.fill(
                    'input[name="title"]', "Test Resource"
                )
                staff_page.fill(
                    'input[name="file_url"]',
                    "https://example.com/test.pdf",
                )
                staff_page.select_option(
                    'select[name="file_type"]', "pdf"
                )
                staff_page.select_option(
                    'select[name="required_level"]', "0"
                )
                staff_page.check('input[name="published"]')

                # Step 4: Submit the form
                staff_page.click(
                    'button:has-text("Create Download")'
                )
                staff_page.wait_for_load_state("networkidle")

                # Redirected to the edit page for the newly created
                # download
                assert "/studio/downloads/" in staff_page.url
                assert "/edit" in staff_page.url

                # The edit form is pre-populated with "Test Resource"
                title_input = staff_page.locator(
                    'input[name="title"]'
                )
                assert title_input.input_value() == "Test Resource"

                staff_ctx.close()

                # Step 5: Navigate to /downloads as anonymous
                anon_ctx = browser.new_context(viewport=VIEWPORT)
                anon_page = anon_ctx.new_page()
                anon_page.goto(
                    f"{django_server}/downloads",
                    wait_until="networkidle",
                )

                # A card for "Test Resource" appears in the listing
                body = anon_page.content()
                assert "Test Resource" in body
                anon_ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 12: Regular member cannot access the Studio download
#               management area
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario12RegularMemberCannotAccessStudio:
    """Regular member cannot access the Studio download management
    area."""

    def test_non_staff_member_is_denied_studio_access(
        self, django_server
    ):
        """A Basic-tier non-staff user navigating to /studio/downloads/
        either gets redirected to the login page or receives a 403."""
        _create_user("basic@test.com", tier_slug="basic")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic@test.com")
            page = context.new_page()
            try:
                response = page.goto(
                    f"{django_server}/studio/downloads/",
                    wait_until="networkidle",
                )

                # Either redirected to login or got 403
                is_redirected = "/accounts/login" in page.url
                is_forbidden = response.status == 403

                assert is_redirected or is_forbidden, (
                    f"Expected redirect to login or 403, "
                    f"got status={response.status} url={page.url}"
                )

                # The user should NOT see the download management UI
                body = page.content()
                assert "New Download" not in body
            finally:
                browser.close()
