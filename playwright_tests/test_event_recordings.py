"""
Playwright E2E tests for Event Recordings (Issue #74).

Tests cover all 10 BDD scenarios from the issue:
- Visitor browses recordings and watches an open one
- Visitor filters recordings by tag to find a topic
- Free user tries to watch a gated recording and sees upgrade path
- Basic member watches a Basic-gated recording successfully
- Reader navigates from a recording detail back to filtered listing via tag
- Visitor paginates through a large recording collection
- Visitor paginates a filtered listing without losing the tag filter
- Empty state when no recordings exist at all
- Empty state when no recordings match a tag filter
- Insufficient-tier paid member sees gated CTA with correct tier name

Usage:
    uv run pytest playwright_tests/test_event_recordings.py -v
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


def _create_recording(
    title,
    slug,
    description="",
    youtube_url="",
    timestamps=None,
    materials=None,
    tags=None,
    required_level=0,
    published=True,
    date=None,
):
    """Create a Recording via ORM."""
    from content.models import Recording

    if timestamps is None:
        timestamps = []
    if materials is None:
        materials = []
    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    recording = Recording(
        title=title,
        slug=slug,
        description=description,
        youtube_url=youtube_url,
        timestamps=timestamps,
        materials=materials,
        tags=tags,
        required_level=required_level,
        published=published,
        date=date,
    )
    recording.save()
    return recording


def _clear_recordings():
    """Delete all recordings to ensure a clean state."""
    from content.models import Recording

    Recording.objects.all().delete()


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
# Scenario 1: Visitor browses recordings and watches an open one
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1VisitorBrowsesAndWatchesOpen:
    """Anonymous visitor browses recordings and watches an open one."""

    def test_visitor_browses_and_watches_open_recording(self, django_server):
        """Given two published open recordings, the visitor sees both on the
        listing, clicks into one, and sees the full video player with
        timestamps and materials."""
        _clear_recordings()
        _create_recording(
            title="Building AI Agents",
            slug="building-ai-agents",
            description="Learn how to build AI agents from scratch.",
            youtube_url="https://www.youtube.com/watch?v=agents123",
            timestamps=[
                {"time_seconds": 0, "label": "Introduction"},
                {"time_seconds": 300, "label": "Architecture"},
                {"time_seconds": 900, "label": "Implementation"},
            ],
            materials=[
                {"title": "Workshop Slides", "url": "https://example.com/slides.pdf"},
                {"title": "GitHub Repo", "url": "https://example.com/repo"},
            ],
            tags=["ai", "agents"],
            date=datetime.date(2026, 2, 15),
        )
        _create_recording(
            title="Advanced RAG Pipelines",
            slug="advanced-rag-pipelines",
            description="Deep dive into RAG pipeline architectures.",
            youtube_url="https://www.youtube.com/watch?v=rag456",
            tags=["ai", "rag"],
            date=datetime.date(2026, 2, 10),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /event-recordings
                page.goto(
                    f"{django_server}/event-recordings",
                    wait_until="networkidle",
                )
                body = page.content()

                # Both recording titles are visible
                assert "Building AI Agents" in body
                assert "Advanced RAG Pipelines" in body

                # Building AI Agents appears before Advanced RAG Pipelines
                # (more recent date = first in listing)
                agents_pos = body.index("Building AI Agents")
                rag_pos = body.index("Advanced RAG Pipelines")
                assert agents_pos < rag_pos

                # Step 2: Click the "Building AI Agents" card link
                page.locator(
                    'a:has-text("Building AI Agents")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                # The recording detail page loads
                assert "/event-recordings/building-ai-agents" in page.url

                body = page.content()

                # Title in the heading
                assert "Building AI Agents" in body

                # YouTube iframe present (video player)
                assert "youtube" in body.lower() or "iframe" in body.lower()

                # Timestamps are listed as clickable elements
                assert "[00:00]" in body
                assert "Introduction" in body
                assert "[05:00]" in body
                assert "Architecture" in body
                assert "[15:00]" in body
                assert "Implementation" in body

                # Materials section shows links
                assert "Workshop Slides" in body
                assert "GitHub Repo" in body

                # Material links open in new tab (target="_blank")
                slides_link = page.locator(
                    'a:has-text("Workshop Slides")'
                ).first
                assert slides_link.get_attribute("target") == "_blank"
                assert slides_link.get_attribute("href") == "https://example.com/slides.pdf"

                # No upgrade message or lock icon on the page
                assert "Upgrade to" not in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Visitor filters recordings by tag to find a topic
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2VisitorFiltersRecordingsByTag:
    """Visitor filters recordings by tag to find a topic."""

    def test_visitor_filters_by_tag(self, django_server):
        """Given three recordings with different tags, the visitor can filter
        by tag, see correct results, and clear the filter."""
        _clear_recordings()
        _create_recording(
            title="Intro to LangChain",
            slug="intro-to-langchain",
            description="Getting started with LangChain.",
            youtube_url="https://www.youtube.com/watch?v=lc123",
            tags=["langchain", "python"],
            date=datetime.date(2026, 2, 15),
        )
        _create_recording(
            title="Django REST APIs",
            slug="django-rest-apis",
            description="Building REST APIs with Django.",
            youtube_url="https://www.youtube.com/watch?v=dj456",
            tags=["django", "python"],
            date=datetime.date(2026, 2, 14),
        )
        _create_recording(
            title="Prompt Engineering",
            slug="prompt-engineering",
            description="Master prompt engineering techniques.",
            youtube_url="https://www.youtube.com/watch?v=pe789",
            tags=["prompts"],
            date=datetime.date(2026, 2, 13),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /event-recordings
                page.goto(
                    f"{django_server}/event-recordings",
                    wait_until="networkidle",
                )
                body = page.content()

                # All three recordings are visible
                assert "Intro to LangChain" in body
                assert "Django REST APIs" in body
                assert "Prompt Engineering" in body

                # Tag filter chips appear
                assert "langchain" in body
                assert "python" in body
                assert "django" in body
                assert "prompts" in body

                # Step 2: Click the "python" tag chip in the filter bar
                # The tag filter chips are links in the filter bar
                filter_bar = page.locator(
                    'text=Filter by tag:'
                ).locator("..")
                python_chip = filter_bar.locator('a:has-text("python")')
                python_chip.first.click()
                page.wait_for_load_state("networkidle")

                # URL updates to include tag=python
                assert "tag=python" in page.url

                body = page.content()

                # "Intro to LangChain" and "Django REST APIs" are visible
                assert "Intro to LangChain" in body
                assert "Django REST APIs" in body

                # "Prompt Engineering" is no longer visible
                # Check the recording cards specifically, not the tag chips
                recording_cards = page.locator("article")
                cards_text = " ".join(
                    [card.inner_text() for card in recording_cards.all()]
                )
                assert "Prompt Engineering" not in cards_text

                # The "python" chip is highlighted as an active filter
                active_filters = page.locator(
                    'text=Active filters:'
                ).locator("..")
                assert "python" in active_filters.inner_text()

                # Step 3: Click the "Clear all" link
                clear_link = page.locator('a:has-text("Clear all")')
                assert clear_link.count() >= 1
                clear_link.first.click()
                page.wait_for_load_state("networkidle")

                # URL returns to /event-recordings without tag parameters
                assert "tag=" not in page.url
                assert "/event-recordings" in page.url

                # All three recordings are visible again
                body = page.content()
                assert "Intro to LangChain" in body
                assert "Django REST APIs" in body
                assert "Prompt Engineering" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Free user tries to watch a gated recording
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3FreeUserSeesUpgradePath:
    """Free user tries to watch a gated recording and sees upgrade path."""

    def test_free_user_sees_gated_recording_with_upgrade_cta(
        self, django_server
    ):
        """Given a Basic-gated recording and a Free-tier user, the user sees
        a lock icon on the listing, a blurred placeholder on detail, and an
        upgrade CTA linking to /pricing."""
        _clear_recordings()
        _create_user("free-rec@test.com", tier_slug="free")
        _create_recording(
            title="Premium Workshop on Fine-Tuning",
            slug="premium-workshop-fine-tuning",
            description="An in-depth workshop covering fine-tuning LLMs for production use cases.",
            youtube_url="https://www.youtube.com/watch?v=ft999",
            required_level=10,  # Basic tier
            tags=["fine-tuning"],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free-rec@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /event-recordings
                page.goto(
                    f"{django_server}/event-recordings",
                    wait_until="networkidle",
                )
                body = page.content()

                # Recording appears in the listing with a lock icon
                assert "Premium Workshop on Fine-Tuning" in body
                # The lock icon is rendered via data-lucide="lock"
                # next to gated recording titles
                recording_card = page.locator(
                    'article:has-text("Premium Workshop on Fine-Tuning")'
                )
                lock_icon = recording_card.locator('[data-lucide="lock"]')
                assert lock_icon.count() >= 1

                # Step 2: Click on "Premium Workshop on Fine-Tuning"
                page.locator(
                    'a:has-text("Premium Workshop on Fine-Tuning")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                body = page.content()

                # Title visible
                assert "Premium Workshop on Fine-Tuning" in body

                # Description visible
                assert "in-depth workshop covering fine-tuning" in body

                # No video player or YouTube iframe shown
                main_element = page.locator("main")
                main_html = main_element.inner_html()
                assert 'data-source="youtube"' not in main_html
                # Ensure no iframe embed in main content
                assert "<iframe" not in main_html.lower() or "ft999" not in main_html

                # Blurred placeholder and upgrade CTA visible
                assert "blur" in body
                assert "Upgrade to Basic to watch this recording" in body

                # Step 3: Click "View Pricing" link
                pricing_link = page.locator('a:has-text("View Pricing")')
                assert pricing_link.count() >= 1
                pricing_link.first.click()
                page.wait_for_load_state("networkidle")

                # User lands on /pricing
                assert "/pricing" in page.url
                pricing_body = page.content()
                assert "Free" in pricing_body
                assert "Basic" in pricing_body
                assert "Main" in pricing_body
                assert "Premium" in pricing_body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Basic member watches a Basic-gated recording
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4BasicMemberWatchesBasicRecording:
    """Basic member watches a Basic-gated recording successfully."""

    def test_basic_member_sees_full_recording(self, django_server):
        """Given a Basic-gated recording with timestamps and materials,
        a Basic-tier user sees the full video player, timestamps, and
        materials without any upgrade prompts."""
        _clear_recordings()
        _create_user("basic-rec@test.com", tier_slug="basic")
        _create_recording(
            title="AI Tool Breakdown: Cursor",
            slug="ai-tool-breakdown-cursor",
            description="A deep dive into the Cursor AI code editor.",
            youtube_url="https://www.youtube.com/watch?v=cursor456",
            timestamps=[
                {"time_seconds": 0, "label": "Overview"},
                {"time_seconds": 300, "label": "Live demo"},
            ],
            materials=[
                {"title": "Slides", "url": "https://example.com/slides.pdf"},
            ],
            required_level=10,  # Basic tier
            tags=["ai-tools"],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic-rec@test.com")
            page = context.new_page()
            try:
                # Navigate directly to the recording detail page
                page.goto(
                    f"{django_server}/event-recordings/ai-tool-breakdown-cursor",
                    wait_until="networkidle",
                )

                body = page.content()

                # Full video player is visible with YouTube embed
                assert "youtube" in body.lower() or "iframe" in body.lower()

                # No upgrade message or blurred overlay
                assert "Upgrade to" not in body

                # Timestamps are listed
                assert "[00:00]" in body
                assert "Overview" in body
                assert "[05:00]" in body
                assert "Live demo" in body

                # Verify timestamps are clickable elements
                timestamps = page.locator(".video-timestamp")
                assert timestamps.count() == 2

                # Materials section shows a "Slides" link
                assert "Slides" in body
                slides_link = page.locator(
                    'a:has-text("Slides")'
                ).first
                assert slides_link.get_attribute("href") == "https://example.com/slides.pdf"

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Reader navigates from detail back to filtered listing
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5NavigateFromDetailToFilteredListing:
    """Reader navigates from a recording detail back to filtered listing
    via tag."""

    def test_navigate_detail_to_filtered_listing_and_back(
        self, django_server
    ):
        """Given two recordings with different tags, the visitor clicks a
        tag chip on the detail page, sees filtered results, clicks back
        into the recording, then uses the back link to return to the
        full listing."""
        _clear_recordings()
        _create_recording(
            title="Building Chatbots",
            slug="building-chatbots",
            description="How to build chatbots with LLMs.",
            youtube_url="https://www.youtube.com/watch?v=cb111",
            tags=["chatbots", "python"],
            date=datetime.date(2026, 2, 15),
        )
        _create_recording(
            title="Deploy with Docker",
            slug="deploy-with-docker",
            description="Containerize your ML applications.",
            youtube_url="https://www.youtube.com/watch?v=dk222",
            tags=["docker", "devops"],
            date=datetime.date(2026, 2, 14),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to recording detail page
                page.goto(
                    f"{django_server}/event-recordings/building-chatbots",
                    wait_until="networkidle",
                )
                body = page.content()

                # Tag chips visible in the recording header
                assert "chatbots" in body
                assert "python" in body

                # Step 2: Click the "python" tag chip on the detail page
                tag_link = page.locator(
                    'a[href="/event-recordings?tag=python"]'
                )
                assert tag_link.count() >= 1
                tag_link.first.click()
                page.wait_for_load_state("networkidle")

                # User is taken to /event-recordings?tag=python
                assert "tag=python" in page.url
                assert "/event-recordings" in page.url

                body = page.content()

                # Building Chatbots appears (has python tag)
                assert "Building Chatbots" in body

                # Deploy with Docker does not appear (no python tag)
                recording_cards = page.locator("article")
                cards_text = " ".join(
                    [card.inner_text() for card in recording_cards.all()]
                )
                assert "Deploy with Docker" not in cards_text

                # Step 3: Click the "Building Chatbots" card to return
                page.locator(
                    'a:has-text("Building Chatbots")'
                ).first.click()
                page.wait_for_load_state("networkidle")
                assert "/event-recordings/building-chatbots" in page.url

                # Step 4: Click the "Back to Event Recordings" link
                back_link = page.locator(
                    'a:has-text("Back to Event Recordings")'
                )
                assert back_link.count() >= 1
                back_link.first.click()
                page.wait_for_load_state("networkidle")

                # User returns to /event-recordings with no filters
                assert "/event-recordings" in page.url
                # The back link goes to /event-recordings (no tag param)
                # Both recordings are visible
                body = page.content()
                assert "Building Chatbots" in body
                assert "Deploy with Docker" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Visitor paginates through a large collection
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6PaginateLargeCollection:
    """Visitor paginates through a large recording collection."""

    def test_visitor_paginates_25_recordings(self, django_server):
        """Given 25 published recordings, the visitor sees 20 on page 1
        with pagination controls, then navigates to page 2 with 5, then
        back to page 1."""
        _clear_recordings()
        for i in range(25):
            _create_recording(
                title=f"Workshop {i + 1:03d}",
                slug=f"workshop-{i + 1:03d}",
                description=f"Description for workshop {i + 1}.",
                youtube_url=f"https://www.youtube.com/watch?v=ws{i + 1}",
                tags=["workshop"],
                date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /event-recordings
                page.goto(
                    f"{django_server}/event-recordings",
                    wait_until="networkidle",
                )

                # Exactly 20 recording cards on the first page
                recording_cards = page.locator("article")
                assert recording_cards.count() == 20

                body = page.content()

                # Pagination indicator visible
                assert "Page 1 of 2" in body

                # "Next" link visible
                next_link = page.locator('a:has-text("Next")')
                assert next_link.count() >= 1

                # Step 2: Click "Next"
                next_link.first.click()
                page.wait_for_load_state("networkidle")

                # URL contains page=2
                assert "page=2" in page.url

                # Remaining 5 recording cards
                recording_cards = page.locator("article")
                assert recording_cards.count() == 5

                # "Previous" link visible
                prev_link = page.locator('a:has-text("Previous")')
                assert prev_link.count() >= 1

                # Step 3: Click "Previous"
                prev_link.first.click()
                page.wait_for_load_state("networkidle")

                # Back to page 1 with 20 recordings
                recording_cards = page.locator("article")
                assert recording_cards.count() == 20

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Paginate a filtered listing without losing the tag
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7PaginateFilteredListing:
    """Visitor paginates a filtered listing without losing the tag filter."""

    def test_paginate_filtered_by_tag(self, django_server):
        """Given 22 recordings tagged 'agents' and 3 tagged 'other',
        filtering by 'agents' shows 20 on page 1, then page 2 has 2,
        and the tag filter stays active."""
        _clear_recordings()
        for i in range(22):
            _create_recording(
                title=f"Agent Workshop {i + 1:03d}",
                slug=f"agent-workshop-{i + 1:03d}",
                description=f"Agent workshop {i + 1}.",
                youtube_url=f"https://www.youtube.com/watch?v=aw{i + 1}",
                tags=["agents"],
                date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            )
        for i in range(3):
            _create_recording(
                title=f"Other Workshop {i + 1:03d}",
                slug=f"other-workshop-{i + 1:03d}",
                description=f"Other workshop {i + 1}.",
                youtube_url=f"https://www.youtube.com/watch?v=ow{i + 1}",
                tags=["other"],
                date=datetime.date(2026, 3, 1) + datetime.timedelta(days=i),
            )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /event-recordings?tag=agents
                page.goto(
                    f"{django_server}/event-recordings?tag=agents",
                    wait_until="networkidle",
                )

                # Only agents-tagged recordings shown, 20 on first page
                recording_cards = page.locator("article")
                assert recording_cards.count() == 20

                # "Next" link visible
                next_link = page.locator('a:has-text("Next")')
                assert next_link.count() >= 1

                # Step 2: Click "Next"
                next_link.first.click()
                page.wait_for_load_state("networkidle")

                # URL contains both tag=agents and page=2
                assert "tag=agents" in page.url
                assert "page=2" in page.url

                # Remaining 2 agents-tagged recordings
                recording_cards = page.locator("article")
                assert recording_cards.count() == 2

                # The "agents" tag filter is still active
                body = page.content()
                active_filters = page.locator(
                    'text=Active filters:'
                ).locator("..")
                assert "agents" in active_filters.inner_text()

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Empty state when no recordings exist
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8EmptyStateNoRecordings:
    """Empty state when no recordings exist at all."""

    def test_empty_state_shows_helpful_message(self, django_server):
        """Given no published recordings, the page loads with the heading
        and a helpful message, and no tag filter chips."""
        _clear_recordings()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /event-recordings
                response = page.goto(
                    f"{django_server}/event-recordings",
                    wait_until="networkidle",
                )

                # Page loads without errors
                assert response.status == 200

                body = page.content()

                # Heading is present (& is HTML-encoded as &amp;)
                heading = page.locator("h1")
                assert "Workshops" in heading.inner_text()
                assert "Learning Materials" in heading.inner_text()

                # Helpful empty state message
                assert (
                    "No resources yet. Check back soon for workshops and learning materials."
                    in body
                )

                # No tag filter chips (no "Filter by tag:")
                assert "Filter by tag:" not in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 9: Empty state when no recordings match a tag filter
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9EmptyStateNoMatchingTag:
    """Empty state when no recordings match a tag filter."""

    def test_no_matching_tag_shows_message_and_clear_link(
        self, django_server
    ):
        """Given published recordings but none tagged 'quantum-computing',
        filtering by that tag shows an empty message with a link to view
        all recordings."""
        _clear_recordings()
        _create_recording(
            title="Some Recording",
            slug="some-recording",
            description="A recording about AI.",
            youtube_url="https://www.youtube.com/watch?v=sr123",
            tags=["ai"],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /event-recordings?tag=quantum-computing
                page.goto(
                    f"{django_server}/event-recordings?tag=quantum-computing",
                    wait_until="networkidle",
                )

                body = page.content()

                # No recording cards visible
                recording_cards = page.locator("article")
                assert recording_cards.count() == 0

                # Empty state message
                assert "No recordings found with the selected tags." in body

                # "View all recordings" link
                view_all_link = page.locator(
                    'a:has-text("View all recordings")'
                )
                assert view_all_link.count() >= 1
                href = view_all_link.first.get_attribute("href")
                assert "/event-recordings" in href

                # Step 2: Click "View all recordings"
                view_all_link.first.click()
                page.wait_for_load_state("networkidle")

                # User returns to the full unfiltered listing
                assert "/event-recordings" in page.url
                body = page.content()
                assert "Some Recording" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Insufficient-tier paid member sees correct CTA
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10InsufficientTierSeesCTAWithCorrectName:
    """Insufficient-tier paid member sees gated CTA with correct tier name."""

    def test_basic_member_sees_main_tier_cta(self, django_server):
        """Given a Main-gated recording and a Basic-tier user, the user
        sees the upgrade CTA mentioning 'Main' (not 'Basic'), with a
        lock icon on the listing and a View Pricing link."""
        _clear_recordings()
        _create_user("basic-insuf@test.com", tier_slug="basic")
        _create_recording(
            title="Main-Only Deep Dive",
            slug="main-only-deep-dive",
            description="An exclusive deep dive into advanced ML deployment strategies for Main tier members.",
            youtube_url="https://www.youtube.com/watch?v=maindd789",
            required_level=20,  # Main tier
            tags=["deployment"],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic-insuf@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /event-recordings
                page.goto(
                    f"{django_server}/event-recordings",
                    wait_until="networkidle",
                )
                body = page.content()

                # Recording appears with a lock icon
                assert "Main-Only Deep Dive" in body
                recording_card = page.locator(
                    'article:has-text("Main-Only Deep Dive")'
                )
                lock_icon = recording_card.locator('[data-lucide="lock"]')
                assert lock_icon.count() >= 1

                # Step 2: Click on "Main-Only Deep Dive"
                page.locator(
                    'a:has-text("Main-Only Deep Dive")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                body = page.content()

                # Title and description are visible
                assert "Main-Only Deep Dive" in body
                assert "exclusive deep dive" in body

                # Video is hidden -- no YouTube iframe present
                main_element = page.locator("main")
                main_html = main_element.inner_html()
                assert "<iframe" not in main_html.lower() or "maindd789" not in main_html

                # CTA reads "Upgrade to Main" not "Upgrade to Basic"
                assert "Upgrade to Main to watch this recording" in body
                assert "Upgrade to Basic" not in body

                # "View Pricing" link to /pricing is available
                pricing_link = page.locator('a:has-text("View Pricing")')
                assert pricing_link.count() >= 1
                href = pricing_link.first.get_attribute("href")
                assert "/pricing" in href

            finally:
                browser.close()
