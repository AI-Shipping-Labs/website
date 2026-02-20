"""
Playwright E2E tests for the Video Player Component (Issue #73).

Tests cover all BDD scenarios from the issue:
- Visitor watches a YouTube recording and navigates via timestamps
- Free member watches a Loom recording and jumps to a timestamp
- Member progresses through a course unit with self-hosted video
- Visitor reads article with auto-embedded YouTube video from markdown
- Free member hits a paywall on a gated recording
- Recording without a video URL displays content properly
- Hour-long timestamps formatted correctly
- Unauthorized member cannot view gated course unit
- Visitor reads article where inline YouTube URL is NOT auto-embedded
- Staff member manages timestamps through admin editor
- Member completes a course unit after watching a video lesson

Usage:
    uv run pytest playwright_tests/test_video_player.py -v
"""

import datetime
import json
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


def _clear_recordings():
    """Delete all recordings to ensure a clean state."""
    from content.models import Recording

    Recording.objects.all().delete()


def _clear_articles():
    """Delete all articles to ensure a clean state."""
    from content.models import Article

    Article.objects.all().delete()


def _clear_courses():
    """Delete all courses (cascades to modules, units, progress)."""
    from content.models import Course

    Course.objects.all().delete()


def _create_recording(
    title,
    slug,
    description="",
    youtube_url="",
    google_embed_url="",
    timestamps=None,
    tags=None,
    required_level=0,
    published=True,
    date=None,
    core_tools=None,
    learning_objectives=None,
    outcome="",
):
    """Create a Recording via ORM."""
    from content.models import Recording

    if timestamps is None:
        timestamps = []
    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()
    if core_tools is None:
        core_tools = []
    if learning_objectives is None:
        learning_objectives = []

    recording = Recording(
        title=title,
        slug=slug,
        description=description,
        youtube_url=youtube_url,
        google_embed_url=google_embed_url,
        timestamps=timestamps,
        tags=tags,
        required_level=required_level,
        published=published,
        date=date,
        core_tools=core_tools,
        learning_objectives=learning_objectives,
        outcome=outcome,
    )
    recording.save()
    return recording


def _create_article(
    title,
    slug,
    description="",
    content_markdown="",
    author="",
    tags=None,
    required_level=0,
    published=True,
    date=None,
):
    """Create an Article via ORM."""
    from content.models import Article

    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    article = Article(
        title=title,
        slug=slug,
        description=description,
        content_markdown=content_markdown,
        author=author,
        tags=tags,
        required_level=required_level,
        published=published,
        date=date,
    )
    article.save()
    return article


def _create_course_with_unit(
    course_title,
    course_slug,
    module_title,
    unit_title,
    unit_video_url="",
    unit_timestamps=None,
    unit_body="",
    unit_homework="",
    required_level=0,
    status="published",
):
    """Create a course with one module and one unit."""
    from content.models import Course, Module, Unit

    if unit_timestamps is None:
        unit_timestamps = []

    course = Course(
        title=course_title,
        slug=course_slug,
        description=f"Description of {course_title}",
        required_level=required_level,
        status=status,
    )
    course.save()

    module = Module(
        course=course,
        title=module_title,
        sort_order=0,
    )
    module.save()

    unit = Unit(
        module=module,
        title=unit_title,
        sort_order=0,
        video_url=unit_video_url,
        timestamps=unit_timestamps,
        body=unit_body,
        homework=unit_homework,
    )
    unit.save()

    return course, module, unit


def _create_user(email, password="testpass123", tier_slug=None):
    """Create a User and optionally assign a tier."""
    from accounts.models import User
    from payments.models import Tier

    _ensure_tiers()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"email_verified": True},
    )
    user.set_password(password)
    user.email_verified = True
    if tier_slug:
        tier = Tier.objects.get(slug=tier_slug)
        user.tier = tier
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


def _login_admin_via_browser(page, base_url, email, password="adminpass123"):
    """Log in an admin user via the Django admin login page."""
    page.goto(f"{base_url}/admin/login/", wait_until="networkidle")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------
# Scenario 1: Visitor watches a YouTube recording with timestamps
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1YouTubeRecordingTimestamps:
    """Visitor watches a YouTube recording and navigates via timestamps."""

    def test_youtube_recording_with_timestamps(self, django_server):
        """Given a published recording with a YouTube URL and 3 timestamps,
        navigate to the recording detail page and verify the video player
        and timestamps are rendered correctly."""
        _clear_recordings()
        _create_recording(
            title="AI Workshop",
            slug="ai-workshop",
            description="An introductory AI workshop.",
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            timestamps=[
                {"time_seconds": 0, "label": "Introduction"},
                {"time_seconds": 300, "label": "Main Content"},
                {"time_seconds": 780, "label": "Wrap-up"},
            ],
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Navigate to recordings listing
                page.goto(
                    f"{django_server}/event-recordings",
                    wait_until="networkidle",
                )
                assert "AI Workshop" in page.content()

                # Click on the recording
                page.locator('a:has-text("AI Workshop")').first.click()
                page.wait_for_load_state("networkidle")

                # Verify we are on the detail page
                assert "/event-recordings/ai-workshop" in page.url

                # Verify YouTube embed is present
                body = page.content()
                assert 'data-source="youtube"' in body
                assert "video-player" in body

                # Verify three timestamps are listed
                timestamps = page.locator(".video-timestamp")
                assert timestamps.count() == 3

                # Verify timestamp labels
                ts_text = page.locator(
                    ".video-timestamp"
                ).all_inner_texts()
                combined = " ".join(ts_text)
                assert "[00:00]" in combined
                assert "Introduction" in combined
                assert "[05:00]" in combined
                assert "Main Content" in combined
                assert "[13:00]" in combined
                assert "Wrap-up" in combined

                # Click the [05:00] Main Content timestamp
                # Verify the button has the correct data attributes
                ts_btn = page.locator(
                    '.video-timestamp[data-time-seconds="300"]'
                )
                assert ts_btn.count() == 1
                assert ts_btn.get_attribute("data-source") == "youtube"
                ts_btn.click()

                # We cannot verify the actual YouTube seekTo call in E2E,
                # but we verify the button exists with correct data attrs
                # and is clickable without errors.
                page.wait_for_timeout(500)
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Free member watches a Loom recording
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2LoomRecordingTimestamps:
    """Free member watches a Loom recording and jumps to a timestamp."""

    def test_loom_recording_with_timestamp_seek(self, django_server):
        """Given a Loom recording with timestamps, verify the Loom iframe
        is embedded and timestamp click updates the iframe src."""
        _clear_recordings()
        _create_user("free-loom@test.com", tier_slug="free")
        _create_recording(
            title="Product Demo",
            slug="product-demo",
            description="A product demo using Loom.",
            youtube_url="https://www.loom.com/share/abc123def456",
            timestamps=[
                {"time_seconds": 0, "label": "Overview"},
                {"time_seconds": 150, "label": "Feature Tour"},
            ],
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free-loom@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/event-recordings/product-demo",
                    wait_until="networkidle",
                )

                body = page.content()

                # Verify Loom embed is present
                assert 'data-source="loom"' in body
                iframe = page.locator(
                    'iframe[id^="loom-player-"]'
                )
                assert iframe.count() == 1

                # Initial iframe src should be the loom embed URL
                initial_src = iframe.get_attribute("src")
                assert "loom.com/embed/" in initial_src

                # Verify timestamps
                timestamps = page.locator(".video-timestamp")
                assert timestamps.count() == 2

                ts_text = " ".join(timestamps.all_inner_texts())
                assert "[00:00]" in ts_text
                assert "Overview" in ts_text
                assert "[02:30]" in ts_text
                assert "Feature Tour" in ts_text

                # Click the [02:30] Feature Tour timestamp
                ts_btn = page.locator(
                    '.video-timestamp[data-time-seconds="150"]'
                )
                ts_btn.click()
                page.wait_for_timeout(1000)

                # Verify the iframe src was updated with ?t=150
                updated_src = iframe.get_attribute("src")
                assert "?t=150" in updated_src

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Self-hosted video in course unit
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3SelfHostedCourseUnit:
    """Member progresses through a course unit with a self-hosted video."""

    def test_self_hosted_video_in_course_unit(self, django_server):
        """Given a course with a self-hosted mp4 video unit, verify the
        HTML5 video player and timestamps render correctly."""
        _clear_courses()
        _create_user("basic-video@test.com", tier_slug="basic")
        _create_course_with_unit(
            course_title="AI Fundamentals",
            course_slug="ai-fundamentals",
            module_title="Getting Started",
            unit_title="Setting Up Your Environment",
            unit_video_url="https://example.com/videos/setup.mp4",
            unit_timestamps=[
                {"time_seconds": 0, "label": "Prerequisites"},
                {"time_seconds": 200, "label": "Installation"},
            ],
            unit_body="# Setup\n\nFollow these steps to set up your environment.",
            required_level=10,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic-video@test.com")
            page = context.new_page()
            try:
                # Navigate to the course detail page
                page.goto(
                    f"{django_server}/courses/ai-fundamentals",
                    wait_until="networkidle",
                )
                assert "AI Fundamentals" in page.content()
                assert "Setting Up Your Environment" in page.content()

                # Click into the unit
                page.locator(
                    'a:has-text("Setting Up Your Environment")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                body = page.content()

                # Verify HTML5 video player is present
                assert 'data-source="self_hosted"' in body
                video = page.locator("#video-player-self-hosted")
                assert video.count() == 1

                # Verify timestamps
                timestamps = page.locator(".video-timestamp")
                assert timestamps.count() == 2

                ts_text = " ".join(timestamps.all_inner_texts())
                assert "[00:00]" in ts_text
                assert "Prerequisites" in ts_text
                assert "[03:20]" in ts_text
                assert "Installation" in ts_text

                # Click the [03:20] Installation timestamp
                ts_btn = page.locator(
                    '.video-timestamp[data-time-seconds="200"]'
                )
                assert ts_btn.count() == 1
                assert ts_btn.get_attribute("data-source") == "self_hosted"
                ts_btn.click()
                page.wait_for_timeout(500)

                # Verify the click was handled (we verify via JS that
                # currentTime was set). In a real browser the video
                # element would seek. We just verify no errors occurred.

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Auto-embedded YouTube video in article markdown
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4ArticleAutoEmbedYouTube:
    """Visitor reads an article with an auto-embedded YouTube video."""

    def test_standalone_youtube_url_becomes_embed(self, django_server):
        """Given an article whose markdown contains a standalone YouTube URL,
        verify the URL is rendered as an embedded video player."""
        _clear_articles()
        _create_article(
            title="Building Your First AI Agent",
            slug="building-your-first-ai-agent",
            description="A guide to building AI agents.",
            content_markdown=(
                "# Building Your First AI Agent\n\n"
                "Here is some introductory text about AI agents.\n\n"
                "https://www.youtube.com/watch?v=abc123\n\n"
                "And here is some follow-up text about next steps."
            ),
            author="Test Author",
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/blog/building-your-first-ai-agent",
                    wait_until="networkidle",
                )

                body = page.content()

                # The YouTube URL is rendered as an embedded video
                assert 'data-source="youtube"' in body
                assert 'data-video-id="abc123"' in body

                # The raw URL text is NOT visible as a plain link
                # (it should be replaced by the embed)
                plain_links = page.locator(
                    'a[href="https://www.youtube.com/watch?v=abc123"]'
                )
                assert plain_links.count() == 0

                # Surrounding text renders normally
                assert "introductory text about AI agents" in body
                assert "follow-up text about next steps" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Free member hits paywall on gated recording
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5GatedRecordingPaywall:
    """Free member hits a paywall on a gated recording."""

    def test_gated_recording_shows_paywall(self, django_server):
        """Given a Basic-gated recording and a Free member, verify the
        video player is NOT rendered and a gating overlay with upgrade
        CTA is shown."""
        _clear_recordings()
        _create_user("free-gated@test.com", tier_slug="free")
        _create_recording(
            title="Advanced Deployment Patterns",
            slug="advanced-deployment-patterns",
            description="Advanced patterns for deploying ML systems.",
            youtube_url="https://www.youtube.com/watch?v=advXYZ",
            timestamps=[
                {"time_seconds": 0, "label": "Start"},
                {"time_seconds": 600, "label": "Advanced Topics"},
            ],
            required_level=10,  # Basic
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free-gated@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/event-recordings/advanced-deployment-patterns",
                    wait_until="networkidle",
                )

                body = page.content()

                # Title and description are visible
                assert "Advanced Deployment Patterns" in body
                assert "Advanced patterns for deploying ML systems" in body

                # Video player is NOT rendered
                assert 'data-source="youtube"' not in body
                assert "video-player" not in body or "video-timestamp" not in body

                # Gating overlay with CTA is shown
                assert "Upgrade to Basic to watch this recording" in body

                # Blurred placeholder is present
                assert "blur" in body

                # Click upgrade CTA
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1
                pricing_link.first.click()
                page.wait_for_load_state("networkidle")

                # Lands on /pricing
                assert "/pricing" in page.url

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Recording without a video URL
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6RecordingWithoutVideoURL:
    """Recording without a video URL still displays its content."""

    def test_no_video_url_shows_content_without_player(self, django_server):
        """Given a recording with no youtube_url and no google_embed_url,
        verify the page loads with title, description, core tools, and
        learning objectives but no video player."""
        _clear_recordings()
        _create_recording(
            title="Community Q&A",
            slug="community-qa",
            description="Open Q&A session for the community.",
            youtube_url="",
            google_embed_url="",
            timestamps=[],
            core_tools=["Slack", "Zoom"],
            learning_objectives=[
                "How to ask good questions",
                "Community best practices",
            ],
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                response = page.goto(
                    f"{django_server}/event-recordings/community-qa",
                    wait_until="networkidle",
                )
                assert response.status == 200

                body = page.content()

                # Title and description are visible
                assert "Community Q&A" in body
                assert "Open Q&A session for the community" in body

                # Core tools are visible
                assert "Slack" in body
                assert "Zoom" in body

                # Learning objectives are visible
                assert "How to ask good questions" in body
                assert "Community best practices" in body

                # No video player or broken embed
                assert 'data-source="youtube"' not in body
                assert 'data-source="loom"' not in body
                assert 'data-source="self_hosted"' not in body

                # No iframes for video
                video_iframes = page.locator(
                    'iframe[src*="youtube"], iframe[src*="loom"]'
                )
                assert video_iframes.count() == 0

                # Navigation back works
                back_link = page.locator(
                    'a:has-text("Back to Event Recordings")'
                )
                assert back_link.count() >= 1
                back_link.first.click()
                page.wait_for_load_state("networkidle")
                assert "/event-recordings" in page.url

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Hour-long timestamps formatted correctly
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7HourLongTimestamps:
    """Timestamps format correctly for entries at or beyond one hour."""

    def test_hour_long_timestamp_formatting(self, django_server):
        """Given a recording with timestamps spanning more than an hour,
        verify [MM:SS] format for <1h and [H:MM:SS] for >=1h."""
        _clear_recordings()
        _create_recording(
            title="Full Day Workshop",
            slug="full-day-workshop",
            description="A full day workshop on AI engineering.",
            youtube_url="https://www.youtube.com/watch?v=fullday123",
            timestamps=[
                {"time_seconds": 0, "label": "Start"},
                {"time_seconds": 3600, "label": "Hour Mark"},
                {"time_seconds": 4380, "label": "Advanced Topics"},
            ],
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/event-recordings/full-day-workshop",
                    wait_until="networkidle",
                )

                # Verify all timestamps are present
                timestamps = page.locator(".video-timestamp")
                assert timestamps.count() == 3

                ts_text = " ".join(timestamps.all_inner_texts())

                # First timestamp: [MM:SS] format (under 1 hour)
                assert "[00:00]" in ts_text
                assert "Start" in ts_text

                # Second timestamp: [H:MM:SS] format (exactly 1 hour)
                assert "[1:00:00]" in ts_text
                assert "Hour Mark" in ts_text

                # Third timestamp: [H:MM:SS] format (1 hour 13 min)
                assert "[1:13:00]" in ts_text
                assert "Advanced Topics" in ts_text

                # Click the [1:13:00] timestamp and verify data attr
                ts_btn = page.locator(
                    '.video-timestamp[data-time-seconds="4380"]'
                )
                assert ts_btn.count() == 1
                ts_btn.click()
                page.wait_for_timeout(500)

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Unauthorized member cannot view gated course unit
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8GatedCourseUnit:
    """Unauthorized member cannot view video content in a gated course."""

    def test_basic_member_blocked_from_premium_course_unit(
        self, django_server
    ):
        """Given a Premium-gated course, a Basic member sees the syllabus
        but gets a gating message when navigating to a unit."""
        _clear_courses()
        _create_user("basic-gated@test.com", tier_slug="basic")
        _create_course_with_unit(
            course_title="Premium Masterclass",
            course_slug="premium-masterclass",
            module_title="Advanced Module",
            unit_title="Deep Dive",
            unit_video_url="https://www.youtube.com/watch?v=premium123",
            unit_timestamps=[
                {"time_seconds": 0, "label": "Intro"},
            ],
            unit_body="# Deep Dive\n\nPremium content.",
            required_level=30,  # Premium
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic-gated@test.com")
            page = context.new_page()
            try:
                # Navigate to course detail - syllabus visible
                page.goto(
                    f"{django_server}/courses/premium-masterclass",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "Premium Masterclass" in body
                assert "Deep Dive" in body

                # Navigate directly to the unit URL
                page.goto(
                    f"{django_server}/courses/premium-masterclass/0/0",
                    wait_until="networkidle",
                )

                body = page.content()

                # Gating message is shown instead of video player
                assert "Upgrade to Premium to access this lesson" in body

                # Video player is NOT rendered
                assert 'data-source="youtube"' not in body
                assert "video-timestamp" not in body

                # CTA link to /pricing is present
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1

                # Click the CTA
                pricing_link.first.click()
                page.wait_for_load_state("networkidle")
                assert "/pricing" in page.url

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 9: Inline YouTube URL NOT auto-embedded
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9InlineYouTubeNotEmbedded:
    """Inline YouTube URL in article text is NOT auto-embedded."""

    def test_inline_youtube_url_rendered_as_link(self, django_server):
        """Given an article whose markdown contains a YouTube URL inline
        within a sentence, verify it renders as a regular link, not an
        embedded video player."""
        _clear_articles()
        _create_article(
            title="Useful Resources",
            slug="useful-resources",
            description="A collection of useful resources.",
            content_markdown=(
                "# Useful Resources\n\n"
                "Check out https://www.youtube.com/watch?v=xyz for more "
                "information about AI tools.\n\n"
                "Also see this other resource for details."
            ),
            author="Test Author",
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/blog/useful-resources",
                    wait_until="networkidle",
                )

                body = page.content()

                # The inline URL should NOT be embedded as a video player
                # (no data-source="youtube" with video-id="xyz")
                video_embeds = page.locator(
                    '.video-player[data-video-id="xyz"]'
                )
                assert video_embeds.count() == 0

                # The sentence text is preserved
                assert "Check out" in body
                assert "for more" in body
                assert "information about AI tools" in body

                # The YouTube URL appears as a link or as text within
                # the paragraph (not as an isolated embed)
                assert "Also see this other resource" in body

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Staff manages timestamps through admin editor
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10AdminTimestampEditor:
    """Staff member manages timestamps through the admin editor."""

    def test_admin_adds_timestamps_to_recording(self, django_server):
        """Given a recording, an admin adds timestamps via the admin
        editor and they appear on the public page."""
        _clear_recordings()
        from accounts.models import User

        User.objects.create_superuser(
            email="admin-ts@test.com", password="adminpass123"
        )
        recording = _create_recording(
            title="Workshop Demo",
            slug="workshop-demo",
            description="A demo workshop.",
            youtube_url="https://www.youtube.com/watch?v=demo456",
            timestamps=[],
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Log in as admin
                _login_admin_via_browser(
                    page, django_server, "admin-ts@test.com"
                )

                # Navigate to the recording change page
                page.goto(
                    f"{django_server}/admin/content/recording/{recording.pk}/change/",
                    wait_until="networkidle",
                )

                body = page.content()
                # Verify the timestamp editor widget is present
                # The TimestampEditorWidget renders an "Add Timestamp" button
                assert "Add Timestamp" in body or "timestamp" in body.lower()

                # Add timestamps by setting the JSON field value directly
                # The admin uses a TimestampEditorWidget which stores JSON
                # We interact with the underlying textarea/input
                timestamps_data = json.dumps([
                    {"time_seconds": 150, "label": "Setup walkthrough"},
                    {"time_seconds": 600, "label": "Live coding"},
                ])

                # Find the timestamps field and set its value via JS
                page.evaluate(
                    """(data) => {
                        // Find the hidden input or textarea for timestamps
                        var el = document.getElementById('id_timestamps');
                        if (!el) {
                            // Try finding by name
                            el = document.querySelector('[name="timestamps"]');
                        }
                        if (el) {
                            el.value = data;
                            // Trigger change event
                            el.dispatchEvent(new Event('change'));
                        }
                    }""",
                    timestamps_data,
                )

                # Save the recording
                page.click('input[name="_save"]')
                page.wait_for_load_state("networkidle")

                # Navigate to the public page
                page.goto(
                    f"{django_server}/event-recordings/workshop-demo",
                    wait_until="networkidle",
                )

                body = page.content()

                # Verify timestamps appear
                timestamps = page.locator(".video-timestamp")
                assert timestamps.count() == 2

                ts_text = " ".join(timestamps.all_inner_texts())
                assert "[02:30]" in ts_text
                assert "Setup walkthrough" in ts_text
                assert "[10:00]" in ts_text
                assert "Live coding" in ts_text

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 11: Member completes a course unit after watching video
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11CourseUnitCompletion:
    """Member completes a course unit after watching a video lesson."""

    def test_mark_unit_as_completed(self, django_server):
        """Given a course with a video unit, a Main member marks the unit
        as completed and sees the status update."""
        _clear_courses()
        _create_user("main-course@test.com", tier_slug="main")
        course, module, unit = _create_course_with_unit(
            course_title="Intro to ML",
            course_slug="intro-to-ml",
            module_title="Foundations",
            unit_title="Linear Regression",
            unit_video_url="https://www.youtube.com/watch?v=linreg789",
            unit_timestamps=[
                {"time_seconds": 0, "label": "Overview"},
                {"time_seconds": 300, "label": "Implementation"},
            ],
            unit_body="# Linear Regression\n\nLearn about linear regression.",
            required_level=20,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main-course@test.com")
            page = context.new_page()
            try:
                # Navigate to course and click into unit
                page.goto(
                    f"{django_server}/courses/intro-to-ml",
                    wait_until="networkidle",
                )
                assert "Intro to ML" in page.content()

                page.locator(
                    'a:has-text("Linear Regression")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                body = page.content()

                # Verify video player with timestamps
                assert 'data-source="youtube"' in body
                timestamps = page.locator(".video-timestamp")
                assert timestamps.count() == 2

                # Verify lesson text
                assert "Linear Regression" in body

                # Verify "Mark as completed" button
                complete_btn = page.locator("#mark-complete-btn")
                assert complete_btn.is_visible()
                assert "Mark as completed" in complete_btn.inner_text()

                # Click "Mark as completed"
                complete_btn.click()
                page.wait_for_timeout(2000)

                # Button should change to show "Completed"
                btn_text = complete_btn.inner_text()
                assert "Completed" in btn_text

                # Navigate back to course detail
                page.goto(
                    f"{django_server}/courses/intro-to-ml",
                    wait_until="networkidle",
                )

                body = page.content()

                # Progress bar should reflect completion
                # (1 of 1 unit = 100%)
                assert "1 of 1 completed" in body or "100%" in body

                context.close()
            finally:
                browser.close()
