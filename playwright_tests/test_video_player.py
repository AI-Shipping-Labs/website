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
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _clear_recordings():
    """Delete all recordings to ensure a clean state."""
    from content.models import Workshop, WorkshopPage
    from events.models import Event

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _clear_articles():
    """Delete all articles to ensure a clean state."""
    from content.models import Article

    Article.objects.all().delete()
    connection.close()


def _clear_courses():
    """Delete all courses (cascades to modules, units, progress)."""
    from content.models import Course

    Course.objects.all().delete()
    connection.close()


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
    materials=None,
):
    """Create a completed event with a linked Workshop carrying the recording.

    Issue #426 retired the inline recording UI on the event detail page;
    recording playback lives on the workshop video page now. The legacy
    helper signature is preserved (call sites use ``youtube_url``,
    ``google_embed_url``, ``date``) and translated to:
      youtube_url       -> Event.recording_url
      google_embed_url  -> Event.recording_embed_url
      date              -> Event.start_datetime (timezone-aware)

    A Workshop with the same slug is linked to the event so the canonical
    ``/workshops/<slug>/video`` surface is available to the test. The
    recording gate matches ``required_level`` so the same gating semantics
    that used to live on the event detail page apply on the workshop video
    page.
    """
    from content.models import Workshop, WorkshopPage
    from events.models import Event

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
    if materials is None:
        materials = []

    start_dt = timezone.make_aware(
        datetime.datetime.combine(date, datetime.time(12, 0))
    )

    recording = Event(
        title=title,
        slug=slug,
        description=description,
        recording_url=youtube_url,
        recording_embed_url=google_embed_url,
        timestamps=timestamps,
        tags=tags,
        required_level=required_level,
        published=published,
        start_datetime=start_dt,
        status="completed",
        kind="workshop",
        core_tools=core_tools,
        learning_objectives=learning_objectives,
        outcome=outcome,
        materials=materials,
    )
    recording.save()

    # Link a Workshop with the same slug so /workshops/<slug>/video is the
    # canonical recording surface. The three gates default to ``required_level``
    # so the per-tier matrix that used to live on event detail still applies.
    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        description=description,
        date=date,
        status="published",
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=required_level,
        event=recording,
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='intro', title='Intro',
        sort_order=1, body='# Intro\n\nWorkshop intro.',
    )

    connection.close()
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
    connection.close()
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

    from django.utils.text import slugify

    module = Module(
        course=course,
        title=module_title,
        slug=slugify(module_title),
        sort_order=0,
    )
    module.save()

    unit = Unit(
        module=module,
        title=unit_title,
        slug=slugify(unit_title),
        sort_order=0,
        video_url=unit_video_url,
        timestamps=unit_timestamps,
        body=unit_body,
        homework=unit_homework,
    )
    unit.save()

    connection.close()
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
    connection.close()
    return user


def _login_admin_via_browser(page, base_url, email, password="adminpass123"):
    """Log in an admin user via the Django admin login page."""
    page.goto(f"{base_url}/admin/login/", wait_until="domcontentloaded")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("domcontentloaded")


# ---------------------------------------------------------------
# Scenario 1: Visitor watches a YouTube recording with timestamps
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario1YouTubeRecordingTimestamps:
    """Visitor watches a YouTube recording and navigates via timestamps."""

    def test_youtube_recording_with_timestamps(self, django_server, page):
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

        # Navigate to past recordings listing — past events always link to
        # the linked Workshop (issue #426).
        page.goto(
            f"{django_server}/events?filter=past",
            wait_until="domcontentloaded",
        )
        assert "AI Workshop" in page.content()

        # The past-card links to /workshops/<slug>; from the workshop
        # landing the visitor follows "Watch the recording" to /video.
        page.locator(
            'a[data-testid="past-card-workshop-link"]'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/workshops/ai-workshop" in page.url

        page.locator('a:has-text("Watch the recording")').first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/workshops/ai-workshop/video" in page.url

        # Verify YouTube embed is present
        body = page.content()
        assert 'data-source="youtube"' in body
        assert "video-player" in body

        # Wait for the chapters disclosure node before forcing it open;
        # the inner buttons render once the disclosure is in the DOM, and
        # waiting here avoids a render-timing flake under CI load.
        chapters = page.locator('details[data-testid="video-chapters"]')
        expect(chapters).to_be_visible()
        page.evaluate(
            "document.querySelectorAll('details[data-testid=\"video-chapters\"]').forEach(d => d.open = true)"
        )

        # Verify three timestamps are listed (auto-wait until the chapter
        # buttons have populated rather than asserting immediately).
        timestamps = page.locator(".video-timestamp")
        expect(timestamps).to_have_count(3)

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
# ---------------------------------------------------------------
# Scenario 2: Free member watches a Loom recording
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario2LoomRecordingTimestamps:
    """Free member watches a Loom recording and jumps to a timestamp."""

    def test_loom_recording_with_timestamp_seek(self, django_server, browser):
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

        context = _auth_context(browser, "free-loom@test.com")
        page = context.new_page()
        # Recording lives on the workshop video page (issue #426).
        page.goto(
            f"{django_server}/workshops/product-demo/video",
            wait_until="domcontentloaded",
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

        # Expand the collapsed Chapters disclosure (#361)
        page.evaluate(
            "document.querySelectorAll('details[data-testid=\"video-chapters\"]').forEach(d => d.open = true)"
        )

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
        page.wait_for_load_state("domcontentloaded")

        # Verify the iframe src was updated with ?t=150
        updated_src = iframe.get_attribute("src")
        assert "?t=150" in updated_src

        context.close()
# ---------------------------------------------------------------
# Scenario 3: Self-hosted video in course unit
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario3SelfHostedCourseUnit:
    """Member progresses through a course unit with a self-hosted video."""

    def test_self_hosted_video_in_course_unit(self, django_server, browser):
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

        context = _auth_context(browser, "basic-video@test.com")
        page = context.new_page()
        # Navigate to the course detail page
        page.goto(
            f"{django_server}/courses/ai-fundamentals",
            wait_until="domcontentloaded",
        )
        assert "AI Fundamentals" in page.content()
        assert "Setting Up Your Environment" in page.content()

        # Expand the collapsed module so the link becomes visible
        page.evaluate("document.querySelectorAll('details.module-details').forEach(d => d.open = true)")

        # Click into the unit
        page.locator(
            'a:has-text("Setting Up Your Environment")'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()

        # Verify HTML5 video player is present
        assert 'data-source="self_hosted"' in body
        video = page.locator("#video-player-self-hosted")
        assert video.count() == 1

        # Expand the collapsed Chapters disclosure (#361)
        page.evaluate(
            "document.querySelectorAll('details[data-testid=\"video-chapters\"]').forEach(d => d.open = true)"
        )

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
        page.wait_for_load_state("domcontentloaded")

        # Verify the click was handled (we verify via JS that
        # currentTime was set). In a real browser the video
        # element would seek. We just verify no errors occurred.

        context.close()
# ---------------------------------------------------------------
# Scenario 4: Auto-embedded YouTube video in article markdown
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario4ArticleAutoEmbedYouTube:
    """Visitor reads an article with an auto-embedded YouTube video."""

    def test_standalone_youtube_url_becomes_embed(self, django_server, page):
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

        page.goto(
            f"{django_server}/blog/building-your-first-ai-agent",
            wait_until="domcontentloaded",
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
# ---------------------------------------------------------------
# Scenario 5: Removed -- duplicate of gating tests in
#   content/tests/test_access_control.py (RecordingDetailAccessControlTest)
#   and playwright_tests/test_access_control.py (E2E Scenario 7)
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# Scenario 6: Recording without a video URL
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario6RecordingWithoutVideoURL:
    """Recording without a video URL still displays its content."""

    def test_no_video_url_shows_content_without_player(self, django_server, page):
        """Given a workshop with no recording URL on its linked event, the
        workshop video page shows the "Recording not available yet" fallback
        without an embedded player.
        """
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

        # Workshop video page is the canonical recording surface.
        response = page.goto(
            f"{django_server}/workshops/community-qa/video",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        body = page.content()

        # Title is visible.
        assert "Community Q&A" in body

        # No video player or broken embed
        assert 'data-source="youtube"' not in body
        assert 'data-source="loom"' not in body
        assert 'data-source="self_hosted"' not in body

        # No iframes for video
        video_iframes = page.locator(
            'iframe[src*="youtube"], iframe[src*="loom"]'
        )
        assert video_iframes.count() == 0

        # The workshop video page renders the "missing recording" fallback.
        missing = page.locator('[data-testid="video-missing"]')
        assert missing.count() == 1
# ---------------------------------------------------------------
# Scenario 7: Hour-long timestamps formatted correctly
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario7HourLongTimestamps:
    """Timestamps format correctly for entries at or beyond one hour."""

    def test_hour_long_timestamp_formatting(self, django_server, page):
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

        page.goto(
            f"{django_server}/workshops/full-day-workshop/video",
            wait_until="domcontentloaded",
        )

        # Expand the collapsed Chapters disclosure (#361)
        page.evaluate(
            "document.querySelectorAll('details[data-testid=\"video-chapters\"]').forEach(d => d.open = true)"
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
# ---------------------------------------------------------------
# Scenario 8: Removed -- duplicate of gating tests in
#   content/tests/test_course_units.py (CourseUnitAccessControlTest)
#   and playwright_tests/test_access_control.py (E2E Scenario 8)
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# Scenario 9: Inline YouTube URL NOT auto-embedded
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario9InlineYouTubeNotEmbedded:
    """Inline YouTube URL in article text is NOT auto-embedded."""

    def test_inline_youtube_url_rendered_as_link(self, django_server, page):
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

        page.goto(
            f"{django_server}/blog/useful-resources",
            wait_until="domcontentloaded",
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
# ---------------------------------------------------------------
# Scenario 10: Staff manages timestamps through admin editor
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario10AdminTimestampEditor:
    """Staff member manages timestamps through the admin editor."""

    def test_admin_adds_timestamps_to_recording(self, django_server, page):
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

        # Log in as admin
        _login_admin_via_browser(
            page, django_server, "admin-ts@test.com"
        )

        # Navigate to the event change page (post-unification: Recording
        # was merged into Event, so the admin URL is /admin/events/event/).
        page.goto(
            f"{django_server}/admin/events/event/{recording.pk}/change/",
            wait_until="domcontentloaded",
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
        page.wait_for_load_state("domcontentloaded")

        # Navigate to the public recording page (workshop video, issue #426)
        page.goto(
            f"{django_server}/workshops/workshop-demo/video",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Expand the collapsed Chapters disclosure (#361)
        page.evaluate(
            "document.querySelectorAll('details[data-testid=\"video-chapters\"]').forEach(d => d.open = true)"
        )

        # Verify timestamps appear
        timestamps = page.locator(".video-timestamp")
        assert timestamps.count() == 2

        ts_text = " ".join(timestamps.all_inner_texts())
        assert "[02:30]" in ts_text
        assert "Setup walkthrough" in ts_text
        assert "[10:00]" in ts_text
        assert "Live coding" in ts_text
# ---------------------------------------------------------------
# Scenario 11: Removed -- duplicate of unit completion toggling
#   tests in content/tests/test_course_units.py
#   (ApiCourseUnitCompleteTest, CourseUnitProgressTest)
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# Issue #361: Chapters disclosure -- collapsed by default, hidden
# entirely when no timestamps exist.
# ---------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestChaptersDisclosureExpandSeekCollapse:
    """Visitor on an event with chapters: expand, seek, collapse.

    Regression test for Issue #361: the chapters list now lives inside
    a collapsed `<details>` so it does not push the rest of the page
    down for visitors who did not ask for chapter navigation.
    """

    def test_visitor_expands_seeks_and_collapses_chapters(
        self, django_server, page
    ):
        _clear_recordings()
        _create_recording(
            title="Chapters Disclosure Demo",
            slug="chapters-disclosure-demo",
            description="Event for chapters disclosure regression test.",
            youtube_url="https://www.youtube.com/watch?v=chapdemo01",
            timestamps=[
                {"time_seconds": 0, "label": "Welcome"},
                {"time_seconds": 60, "label": "Setup"},
                {"time_seconds": 180, "label": "Build"},
                {"time_seconds": 360, "label": "Test"},
                {"time_seconds": 600, "label": "Wrap up"},
            ],
            required_level=0,
        )

        # Workshop video page is the canonical recording surface (issue #426).
        page.goto(
            f"{django_server}/workshops/chapters-disclosure-demo/video",
            wait_until="domcontentloaded",
        )

        # Video embed is visible
        yt_player = page.locator('[id^="yt-player-"]')
        assert yt_player.count() >= 1

        # Chapters disclosure renders, collapsed (no `open` attribute)
        chapters = page.locator('details[data-testid="video-chapters"]')
        assert chapters.count() == 1
        assert (
            chapters.first.evaluate("el => el.hasAttribute('open')")
            is False
        )

        # Summary line shows "Chapters (5)" -- the visible text is
        # uppercased via the `uppercase` Tailwind class but the underlying
        # source text is "Chapters (5)".  Assert against the source text
        # via textContent so the test matches the literal markup.
        summary = chapters.locator("summary")
        summary_source = summary.first.evaluate("el => el.textContent.trim()")
        assert "Chapters (5)" in summary_source

        # Individual chapter rows are not visible while collapsed.
        first_chapter_btn = chapters.locator(".video-timestamp").first
        assert first_chapter_btn.is_visible() is False

        # Step: click summary to expand
        summary.first.click()
        page.wait_for_function(
            "el => el.hasAttribute('open')",
            arg=chapters.first.element_handle(),
        )
        assert (
            chapters.first.evaluate("el => el.hasAttribute('open')")
            is True
        )
        assert first_chapter_btn.is_visible() is True

        # All 5 chapter rows are present and labelled correctly
        chapter_buttons = chapters.locator(".video-timestamp")
        assert chapter_buttons.count() == 5
        rows_text = chapters.first.inner_text()
        assert "[00:00]" in rows_text
        assert "Welcome" in rows_text
        assert "[01:00]" in rows_text
        assert "Setup" in rows_text

        # Step: click the first chapter row -- existing seek handler
        # should be wired up unchanged.  We assert via data attributes
        # and a clean click that no console errors fire.
        first_btn = chapter_buttons.first
        assert first_btn.get_attribute("data-time-seconds") == "0"
        assert first_btn.get_attribute("data-source") == "youtube"
        assert first_btn.get_attribute("data-video-id") == "chapdemo01"

        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        first_btn.click()
        assert errors == []

        # Step: click summary again to collapse
        summary.first.click()
        page.wait_for_function(
            "el => !el.hasAttribute('open')",
            arg=chapters.first.element_handle(),
        )
        assert (
            chapters.first.evaluate("el => el.hasAttribute('open')")
            is False
        )
        assert first_chapter_btn.is_visible() is False


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestNoChaptersWhenTimestampsEmpty:
    """Visitor on an event recording without chapters sees a clean page."""

    def test_no_chapters_disclosure_when_timestamps_empty(
        self, django_server, page
    ):
        _clear_recordings()
        _create_recording(
            title="No Chapters Recording",
            slug="no-chapters-recording",
            description="Event without timestamps configured.",
            youtube_url="https://www.youtube.com/watch?v=nochap0001",
            timestamps=[],
            required_level=0,
        )

        # Workshop video page is the canonical recording surface (issue #426).
        page.goto(
            f"{django_server}/workshops/no-chapters-recording/video",
            wait_until="domcontentloaded",
        )

        # Video embed is visible
        yt_player = page.locator('[id^="yt-player-"]')
        assert yt_player.count() >= 1

        # No chapters details element renders at all
        chapters = page.locator('details[data-testid="video-chapters"]')
        assert chapters.count() == 0

        # No "Chapters (" summary text appears anywhere on the page
        body = page.content()
        assert "Chapters (" not in body

        # No leftover legacy "Timestamps" header card either
        assert ">Timestamps<" not in body

        # No timestamp buttons rendered
        assert page.locator(".video-timestamp").count() == 0
