"""
Playwright E2E tests for Curated Links (Issue #76).

Tests cover all 10 BDD scenarios from the issue:
- Visitor browses curated links organized by category
- Visitor clicks an open link and it opens in a new tab
- Free user encounters a gated link and sees upgrade CTA on click
- Basic member accesses a Basic-gated link successfully
- Basic member is still gated from Main-tier links
- Visitor filters links by tag
- Visitor clears tag filter to see all links
- Empty state when tag filter matches nothing
- Backward compatibility -- /collection URL works same as /resources
- Visitor sees no content when no links are published

Usage:
    uv run pytest playwright_tests/test_curated_links.py -v
"""

import os

import pytest
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


def _create_curated_link(
    title,
    item_id=None,
    description="",
    url="https://example.com",
    category="tools",
    tags=None,
    required_level=0,
    sort_order=0,
    published=True,
    source="",
):
    """Create a CuratedLink via ORM."""
    from content.models import CuratedLink

    if tags is None:
        tags = []
    if item_id is None:
        # Generate a unique item_id from the title
        item_id = title.lower().replace(" ", "-")

    link = CuratedLink(
        item_id=item_id,
        title=title,
        description=description,
        url=url,
        category=category,
        tags=tags,
        required_level=required_level,
        sort_order=sort_order,
        published=published,
        source=source,
    )
    link.save()
    return link


def _clear_curated_links():
    """Delete all curated links to ensure a clean state."""
    from content.models import CuratedLink

    CuratedLink.objects.all().delete()


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
# Scenario 1: Visitor browses curated links organized by category
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1VisitorBrowsesByCategory:
    """Visitor browses curated links organized by category."""

    def test_links_grouped_under_category_headers(self, django_server):
        """Two published curated links in different categories appear under
        their respective category headers with descriptive subtitles."""
        _clear_curated_links()
        _create_curated_link(
            title="FastAPI Toolkit",
            description="A toolkit for building FastAPI applications.",
            url="https://github.com/example/fastapi-toolkit",
            category="tools",
            tags=["python", "api"],
            sort_order=1,
        )
        _create_curated_link(
            title="LLaMA Hub",
            description="A hub for LLaMA models and fine-tuning resources.",
            url="https://github.com/example/llama-hub",
            category="models",
            tags=["llm", "models"],
            sort_order=1,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/resources",
                    wait_until="networkidle",
                )
                body = page.content()

                # Page heading (& is HTML-encoded as &amp; in raw HTML)
                heading = page.locator("h1")
                assert "Tools, Models & Courses" in heading.inner_text()

                # Both links visible
                assert "FastAPI Toolkit" in body
                assert "LLaMA Hub" in body

                # Category headers present
                assert "Tools" in body
                assert "Models" in body

                # FastAPI Toolkit appears under "Tools" header
                # LLaMA Hub appears under "Models" header
                tools_pos = body.index(">Tools<")
                models_pos = body.index(">Models<")
                fastapi_pos = body.index("FastAPI Toolkit")
                llama_pos = body.index("LLaMA Hub")

                # FastAPI Toolkit comes after Tools header
                assert fastapi_pos > tools_pos
                # LLaMA Hub comes after Models header
                assert llama_pos > models_pos
                # Tools section comes before Models section
                assert tools_pos < models_pos

                # Category descriptions (subtitles) present
                assert "GitHub repos, CLIs, and dev tools" in body
                assert "Model hubs, runtimes, and inference" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Visitor clicks an open link and it opens in a new tab
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2VisitorClicksOpenLink:
    """Visitor clicks an open link and it opens in a new tab."""

    def test_open_link_has_target_blank_and_external_icon(
        self, django_server
    ):
        """A published open curated link opens in a new tab with
        target='_blank' and displays an external-link icon."""
        _clear_curated_links()
        _create_curated_link(
            title="Ollama",
            description="Run LLMs locally with Ollama.",
            url="https://github.com/ollama/ollama",
            category="tools",
            required_level=0,
            sort_order=1,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/resources",
                    wait_until="networkidle",
                )
                body = page.content()

                # Link is visible
                assert "Ollama" in body

                # Find the link card element
                link_card = page.locator(
                    'a:has-text("Ollama")'
                ).first

                # Opens in new tab
                assert link_card.get_attribute("target") == "_blank"

                # Points to the correct URL
                assert (
                    link_card.get_attribute("href")
                    == "https://github.com/ollama/ollama"
                )

                # External link icon present
                external_icon = link_card.locator(
                    '[data-lucide="external-link"]'
                )
                assert external_icon.count() >= 1
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Free user encounters a gated link and sees upgrade CTA
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3FreeUserSeesGatedLink:
    """Free user encounters a gated link and sees upgrade CTA on click."""

    def test_gated_link_shows_lock_icon_and_hides_url(
        self, django_server
    ):
        """A gated link shows a lock icon, hides the actual URL from
        the page source, and reveals an upgrade CTA on click."""
        _clear_curated_links()
        _create_curated_link(
            title="Pro CLI Toolkit",
            description="Advanced CLI tools for production workflows.",
            url="https://example.com/pro-cli-secret-url",
            category="tools",
            required_level=10,  # Basic tier
            sort_order=1,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/resources",
                    wait_until="networkidle",
                )
                body = page.content()

                # Link title is visible
                assert "Pro CLI Toolkit" in body

                # Lock icon present instead of external-link icon
                gated_card = page.locator(
                    '.gated-link:has-text("Pro CLI Toolkit")'
                )
                lock_icon = gated_card.locator('[data-lucide="lock"]')
                assert lock_icon.count() >= 1

                # The actual URL does NOT appear anywhere in the page source
                assert "pro-cli-secret-url" not in body

                # Click on the gated card to reveal the CTA
                gated_card.click()

                # Wait for the CTA to become visible
                cta = gated_card.locator(".gated-cta")
                cta.wait_for(state="visible", timeout=3000)

                # Upgrade prompt appears
                cta_text = cta.inner_text()
                assert "Upgrade to Basic to access this resource" in cta_text

                # "View Plans" link pointing to /pricing
                view_plans_link = cta.locator('a:has-text("View Plans")')
                assert view_plans_link.count() >= 1
                href = view_plans_link.first.get_attribute("href")
                assert "/pricing" in href

                # Click "View Plans" and land on /pricing
                view_plans_link.first.click()
                page.wait_for_load_state("networkidle")
                assert "/pricing" in page.url
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Basic member accesses a Basic-gated link successfully
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4BasicMemberAccessesBasicLink:
    """Basic member accesses a Basic-gated link successfully."""

    def test_basic_member_sees_external_link_icon_no_lock(
        self, django_server
    ):
        """A Basic-tier user sees a Basic-gated link with an external-link
        icon, not a lock icon, and the href is present and clickable."""
        _clear_curated_links()
        _create_user("basic-cl@test.com", tier_slug="basic")
        _create_curated_link(
            title="Exclusive Toolkit",
            description="An exclusive toolkit for Basic members.",
            url="https://example.com/exclusive-toolkit",
            category="tools",
            required_level=10,  # Basic tier
            sort_order=1,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic-cl@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/resources",
                    wait_until="networkidle",
                )
                body = page.content()

                # Link title is visible
                assert "Exclusive Toolkit" in body

                # Find the link card -- it should be an <a> tag, not a div
                link_card = page.locator(
                    'a:has-text("Exclusive Toolkit")'
                ).first

                # External-link icon present (not lock icon)
                external_icon = link_card.locator(
                    '[data-lucide="external-link"]'
                )
                assert external_icon.count() >= 1

                # No lock icon on this card
                lock_icon = link_card.locator('[data-lucide="lock"]')
                assert lock_icon.count() == 0

                # The href is present and points to the correct URL
                href = link_card.get_attribute("href")
                assert href == "https://example.com/exclusive-toolkit"

                # Opens in new tab
                assert link_card.get_attribute("target") == "_blank"

                # No upgrade prompt anywhere
                assert "Upgrade to" not in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Basic member is still gated from Main-tier links
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5BasicMemberGatedFromMainLinks:
    """Basic member is still gated from Main-tier links."""

    def test_basic_member_sees_basic_link_open_and_main_link_locked(
        self, django_server
    ):
        """A Basic-tier user can access a Basic-gated link but sees a
        lock icon and upgrade CTA for a Main-gated link."""
        _clear_curated_links()
        _create_user("basic-cl2@test.com", tier_slug="basic")
        _create_curated_link(
            title="Basic Toolkit",
            description="A toolkit accessible to Basic members.",
            url="https://example.com/basic-toolkit",
            category="tools",
            required_level=10,  # Basic tier
            sort_order=1,
        )
        _create_curated_link(
            title="Community Dashboard",
            description="A dashboard for Main-tier community members.",
            url="https://example.com/community-dashboard-secret",
            category="tools",
            required_level=20,  # Main tier
            sort_order=2,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic-cl2@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/resources",
                    wait_until="networkidle",
                )
                body = page.content()

                # Both links visible
                assert "Basic Toolkit" in body
                assert "Community Dashboard" in body

                # Basic Toolkit: accessible with external-link icon
                basic_card = page.locator(
                    'a:has-text("Basic Toolkit")'
                ).first
                basic_external = basic_card.locator(
                    '[data-lucide="external-link"]'
                )
                assert basic_external.count() >= 1

                # Community Dashboard: gated with lock icon, URL hidden
                assert "community-dashboard-secret" not in body
                gated_card = page.locator(
                    '.gated-link:has-text("Community Dashboard")'
                )
                lock_icon = gated_card.locator('[data-lucide="lock"]')
                assert lock_icon.count() >= 1

                # Click on the gated card
                gated_card.click()

                # Wait for CTA to appear
                cta = gated_card.locator(".gated-cta")
                cta.wait_for(state="visible", timeout=3000)

                # Upgrade prompt mentions "Main"
                cta_text = cta.inner_text()
                assert "Upgrade to Main to access this resource" in cta_text

                # "View Plans" link present
                view_plans_link = cta.locator('a:has-text("View Plans")')
                assert view_plans_link.count() >= 1
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Visitor filters links by tag
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6VisitorFiltersByTag:
    """Visitor filters links by tag."""

    def test_tag_filter_narrows_results(self, django_server):
        """Click the 'python' tag chip and only links with that tag are
        shown. URL updates and active filter indicator appears."""
        _clear_curated_links()
        _create_curated_link(
            title="Python CLI",
            description="A Python command-line tool.",
            url="https://example.com/python-cli",
            category="tools",
            tags=["python", "cli"],
            sort_order=1,
        )
        _create_curated_link(
            title="GPT-4 API",
            description="API access for GPT-4.",
            url="https://example.com/gpt4-api",
            category="models",
            tags=["ai", "llm"],
            sort_order=1,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /resources
                page.goto(
                    f"{django_server}/resources",
                    wait_until="networkidle",
                )
                body = page.content()

                # Both links visible
                assert "Python CLI" in body
                assert "GPT-4 API" in body

                # Tag filter chips appear for all tags
                assert "python" in body
                assert "cli" in body
                assert "ai" in body
                assert "llm" in body

                # Step 2: Click the "python" tag chip
                python_chip = page.locator(
                    'a[href*="tag=python"]'
                ).first
                python_chip.click()
                page.wait_for_load_state("networkidle")

                # URL updates
                assert "tag=python" in page.url
                assert "/resources" in page.url

                body = page.content()

                # Only "Python CLI" is visible
                assert "Python CLI" in body
                # GPT-4 API should not appear in link cards
                # (it may still appear in the tag filter chips area)
                # Check the actual card content areas
                link_cards = page.locator(
                    '.gated-link, a[target="_blank"]'
                )
                cards_text = " ".join(
                    [card.inner_text() for card in link_cards.all()]
                )
                assert "GPT-4 API" not in cards_text

                # Active filter indicator shows "python"
                active_filters = page.locator(
                    'text=Active filters:'
                ).locator("..")
                assert "python" in active_filters.inner_text()

                # "Clear all" link present
                clear_link = page.locator('a:has-text("Clear all")')
                assert clear_link.count() >= 1
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Visitor clears tag filter to see all links
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7VisitorClearsTagFilter:
    """Visitor clears tag filter to see all links."""

    def test_clear_all_restores_all_links(self, django_server):
        """From a filtered view, clicking 'Clear all' restores all links
        and removes the active filter indicator."""
        _clear_curated_links()
        _create_curated_link(
            title="Python CLI",
            description="A Python command-line tool.",
            url="https://example.com/python-cli",
            category="tools",
            tags=["python", "cli"],
            sort_order=1,
        )
        _create_curated_link(
            title="GPT-4 API",
            description="API access for GPT-4.",
            url="https://example.com/gpt4-api",
            category="models",
            tags=["ai", "llm"],
            sort_order=1,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Start on the filtered view
                page.goto(
                    f"{django_server}/resources?tag=python",
                    wait_until="networkidle",
                )
                body = page.content()

                # Only "Python CLI" visible
                assert "Python CLI" in body

                # Step 1: Click "Clear all"
                clear_link = page.locator(
                    'a:has-text("Clear all")'
                ).first
                clear_link.click()
                page.wait_for_load_state("networkidle")

                # URL resets to /resources with no query params
                assert "tag=" not in page.url
                url_path = page.url.split("?")[0]
                assert url_path.rstrip("/").endswith("/resources")

                body = page.content()

                # Both links visible again
                assert "Python CLI" in body
                assert "GPT-4 API" in body

                # "Active filters:" section disappears
                active_section = page.locator(
                    'text=Active filters:'
                )
                assert active_section.count() == 0
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Empty state when tag filter matches nothing
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8EmptyStateNoMatchingTag:
    """Empty state when tag filter matches nothing."""

    def test_no_matching_tag_shows_empty_message(self, django_server):
        """Filtering by a nonexistent tag shows an empty state message
        with a 'View all links' link."""
        _clear_curated_links()
        _create_curated_link(
            title="Python CLI",
            description="A Python command-line tool.",
            url="https://example.com/python-cli",
            category="tools",
            tags=["python"],
            sort_order=1,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Navigate with nonexistent tag
                page.goto(
                    f"{django_server}/resources?tag=rust",
                    wait_until="networkidle",
                )
                body = page.content()

                # No link cards shown
                assert "Python CLI" not in body

                # Empty state message
                assert "No links found with the selected tags" in body

                # "View all links" link pointing to /resources
                view_all_link = page.locator(
                    'a:has-text("View all links")'
                )
                assert view_all_link.count() >= 1
                href = view_all_link.first.get_attribute("href")
                assert href == "/resources"

                # Click the link
                view_all_link.first.click()
                page.wait_for_load_state("networkidle")

                # Back to full listing
                body = page.content()
                assert "Python CLI" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 9: Backward compatibility -- /collection URL works
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9BackwardCompatCollection:
    """Backward compatibility -- /collection URL works same as /resources."""

    def test_collection_url_serves_same_page(self, django_server):
        """Navigating to /collection loads successfully and displays the
        same curated links page as /resources."""
        _clear_curated_links()
        _create_curated_link(
            title="Legacy Link",
            description="A link for backward compatibility testing.",
            url="https://example.com/legacy",
            category="tools",
            sort_order=1,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                response = page.goto(
                    f"{django_server}/collection",
                    wait_until="networkidle",
                )

                # HTTP 200
                assert response.status == 200

                body = page.content()

                # Same heading as /resources (& is HTML-encoded as &amp;)
                heading = page.locator("h1")
                assert "Tools, Models & Courses" in heading.inner_text()

                # Link is visible
                assert "Legacy Link" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Visitor sees no content when no links are published
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10EmptyStateNoLinks:
    """Visitor sees no content when no links are published."""

    def test_empty_state_shows_clean_message(self, django_server):
        """With no published curated links, the page loads with a clean
        empty state message and no category headers."""
        _clear_curated_links()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                response = page.goto(
                    f"{django_server}/resources",
                    wait_until="networkidle",
                )

                # Page loads without errors
                assert response.status == 200

                body = page.content()

                # Empty state message
                assert "No curated links yet. Check back soon." in body

                # No category headers
                # The categories would appear as h2 elements within the
                # grouped_categories section. With no links, none should
                # be rendered.
                category_headers = page.locator(
                    'h2:has-text("Tools"), '
                    'h2:has-text("Models"), '
                    'h2:has-text("Courses"), '
                    'h2:has-text("Other")'
                )
                assert category_headers.count() == 0
            finally:
                browser.close()
