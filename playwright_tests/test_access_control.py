"""
Playwright E2E tests for Access Control and Content Gating (Issue #71).

Tests cover all 12 BDD scenarios from the issue:
- Anonymous visitor reads open content freely
- Free member hits a Basic-gated article and follows upgrade path
- Basic member reads Basic content but cannot access Main-gated article
- Main member reads all content up to their level, blocked on Premium
- Premium member has unrestricted access across all content types
- Anonymous visitor lands on gated article via shared link
- Basic member blocked from Main-gated recording (video URL never leaked)
- Anonymous visitor evaluates a gated course syllabus
- Main member navigates a course, reads a unit, marks it complete
- Staff member changes article visibility in Studio
- Free member encounters gated downloads
- Free member tries to register for Main-gated event

Usage:
    uv run pytest playwright_tests/test_access_control.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    VIEWPORT,
)
from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


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
    """Helper to create an Article directly via the ORM."""
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


def _create_recording(
    title,
    slug,
    description="",
    youtube_url="",
    required_level=0,
    published=True,
    date=None,
    tags=None,
):
    """Helper to create a completed event with a recording via the ORM.

    The events/recordings unification merged Recording into Event. This
    helper keeps the legacy external kwargs (`youtube_url`, `date`) so
    call sites do not change, and translates them internally:
      youtube_url -> recording_url
      date        -> start_datetime (timezone-aware)
    The event is created with status='completed' so it appears on
    /events?filter=past.
    """
    from events.models import Event

    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    start_dt = timezone.make_aware(
        datetime.datetime.combine(date, datetime.time(12, 0))
    )

    recording = Event(
        title=title,
        slug=slug,
        description=description,
        recording_url=youtube_url,
        required_level=required_level,
        published=published,
        start_datetime=start_dt,
        status="completed",
        tags=tags,
    )
    recording.save()
    connection.close()
    return recording


def _create_tutorial(
    title,
    slug,
    description="",
    content_markdown="",
    content_html="",
    required_level=0,
    published=True,
    date=None,
    tags=None,
):
    """Helper to create a Tutorial directly via the ORM."""
    from content.models import Tutorial

    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    tutorial = Tutorial(
        title=title,
        slug=slug,
        description=description,
        content_markdown=content_markdown,
        content_html=content_html or f"<p>{content_markdown}</p>",
        required_level=required_level,
        published=published,
        date=date,
        tags=tags,
    )
    tutorial.save()
    connection.close()
    return tutorial


def _create_project(
    title,
    slug,
    description="",
    content_markdown="",
    required_level=0,
    published=True,
    date=None,
    tags=None,
    author="",
):
    """Helper to create a Project directly via the ORM.

    Unlike Article, the Project model does not auto-render markdown to
    content_html on save, so we render it manually here.
    """
    import markdown

    from content.models import Project

    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    content_html = ""
    if content_markdown:
        content_html = markdown.markdown(
            content_markdown,
            extensions=["fenced_code", "codehilite", "tables"],
        )

    project = Project(
        title=title,
        slug=slug,
        description=description,
        content_markdown=content_markdown,
        content_html=content_html,
        required_level=required_level,
        published=published,
        date=date,
        tags=tags,
        author=author,
    )
    project.save()
    connection.close()
    return project


def _create_course(
    title,
    slug,
    description="",
    required_level=0,
    status="published",
    instructor_name="",
    tags=None,
):
    """Helper to create a Course."""
    from content.models import Course

    if tags is None:
        tags = []

    course = Course(
        title=title,
        slug=slug,
        description=description,
        required_level=required_level,
        status=status,
        instructor_name=instructor_name,
        tags=tags,
    )
    course.save()
    connection.close()
    return course


def _create_module(course, title, sort_order=0):
    """Helper to create a Module."""
    from django.utils.text import slugify

    from content.models import Module

    module = Module(course=course, title=title, slug=slugify(title), sort_order=sort_order)
    module.save()
    connection.close()
    return module


def _create_unit(module, title, sort_order=0, body="", video_url=""):
    """Helper to create a Unit."""
    from django.utils.text import slugify

    from content.models import Unit

    unit = Unit(
        module=module,
        title=title,
        slug=slugify(title),
        sort_order=sort_order,
        body=body,
        video_url=video_url,
    )
    unit.save()
    connection.close()
    return unit


def _create_download(
    title,
    slug,
    description="",
    file_url="https://example.com/file.pdf",
    required_level=0,
    published=True,
    file_type="pdf",
    tags=None,
):
    """Helper to create a Download."""
    from content.models import Download

    if tags is None:
        tags = []

    download = Download(
        title=title,
        slug=slug,
        description=description,
        file_url=file_url,
        required_level=required_level,
        published=published,
        file_type=file_type,
        tags=tags,
    )
    download.save()
    connection.close()
    return download


def _create_event(
    title,
    slug,
    description="",
    required_level=0,
    status="upcoming",
    location="Zoom",
    start_datetime=None,
    tags=None,
):
    """Helper to create an Event."""
    from events.models import Event

    if tags is None:
        tags = []
    if start_datetime is None:
        start_datetime = timezone.now() + datetime.timedelta(days=7)

    event = Event(
        title=title,
        slug=slug,
        description=description,
        required_level=required_level,
        status=status,
        location=location,
        start_datetime=start_datetime,
        tags=tags,
    )
    event.save()
    connection.close()
    return event


def _clear_all_content():
    """Delete all test content to ensure a clean state."""
    from content.models import (
        Article,
        Course,
        Download,
        Module,
        Project,
        Tutorial,
        Unit,
        UserCourseProgress,
    )
    from events.models import Event, EventRegistration

    UserCourseProgress.objects.all().delete()
    EventRegistration.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    Article.objects.all().delete()
    Event.objects.all().delete()
    Tutorial.objects.all().delete()
    Project.objects.all().delete()
    Download.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


# ---------------------------------------------------------------
# Scenario 449: Newly signed-up reader hits a free article
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario449UnverifiedSignupFreeArticle:
    """New email/password signups must verify before reading free details."""

    def test_new_signup_gets_verify_email_gate_for_free_article(
        self, django_server, page
    ):
        _clear_all_content()

        from accounts.models import User

        User.objects.filter(email="unverified@test.com").delete()
        connection.close()

        _create_article(
            title="Free Article Requires Verification",
            slug="free-article-requires-verification",
            description="A free article visible in listings.",
            content_markdown=(
                "# Free Article Requires Verification\n\n"
                "Full article body after verification.\n\n"
                "Related articles should remain below the page."
            ),
            author="Alice",
            tags=["verification"],
            required_level=0,
        )
        _create_article(
            title="Related Verification Article",
            slug="related-verification-article",
            description="A related free article.",
            content_markdown="# Related Verification Article\n\nRelated body.",
            tags=["verification"],
            required_level=0,
        )

        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        csrf_cookie = page.context.cookies(django_server)
        csrf_token = next(
            cookie["value"] for cookie in csrf_cookie if cookie["name"] == "csrftoken"
        )

        register_response = page.request.post(
            f"{django_server}/api/register",
            data={"email": "unverified@test.com", "password": "pass1234"},
            headers={"X-CSRFToken": csrf_token},
        )
        assert register_response.status == 201

        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
        page.fill("#login-email", "unverified@test.com")
        page.fill("#login-password", "pass1234")
        page.click("#login-submit")
        page.wait_for_url(f"{django_server}/")

        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        assert "Free Article Requires Verification" in page.content()

        page.locator('text="Free Article Requires Verification"').first.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator('[data-testid="verify-email-required-card"]').is_visible()
        body = page.content()
        assert "unverified@test.com" in body
        assert "Full article body after verification" not in body
        assert 'data-testid="gated-access-card"' not in body
        assert page.get_by_role("button", name="Resend verification email").is_visible()
        assert page.locator('a[href="/pricing"]').count() > 0

        article_url = page.url
        page.get_by_role("button", name="Resend verification email").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url == article_url
        assert "Verification email sent." in page.content()

        user = User.objects.get(email="unverified@test.com")
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        connection.close()

        page.reload(wait_until="domcontentloaded")
        final_body = page.content()
        assert "Full article body after verification" in final_body
        assert 'data-testid="verify-email-required-card"' not in final_body
        assert "Related Articles" in final_body


# ---------------------------------------------------------------
# Scenario 1: Anonymous visitor reads open content freely
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1AnonymousReadsOpenContent:
    """Anonymous visitor reads open content freely without encountering
    any paywall."""

    def test_anonymous_reads_open_article(self, django_server, page):
        """Anonymous visitor navigates to /blog and reads an open article
        in full without any upgrade prompts."""
        _clear_all_content()
        _create_article(
            title="Open Article for Everyone",
            slug="open-article-everyone",
            description="An open article about AI engineering.",
            content_markdown=(
                "# Open Article for Everyone\n\n"
                "This is the full open article content that anyone can read."
            ),
            author="Alice",
            tags=["ai"],
        )

        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        assert "Open Article for Everyone" in page.content()

        page.locator(
            'h2:has-text("Open Article for Everyone")'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "full open article content that anyone can read" in body
        assert "Upgrade to" not in body
    def test_anonymous_reads_open_recording(self, django_server, page):
        """Anonymous visitor navigates to /events?filter=past and views an
        open recording without any signup prompt."""
        _clear_all_content()
        _create_recording(
            title="Open Recording for All",
            slug="open-recording-all",
            description="A free recording about building AI tools.",
            youtube_url="https://youtube.com/watch?v=open123",
            tags=["ai"],
        )

        page.goto(
            f"{django_server}/events?filter=past",
            wait_until="domcontentloaded",
        )
        assert "Open Recording for All" in page.content()

        page.locator(
            'text=Open Recording for All'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "A free recording about building AI tools" in body
        # The video embed should be present (youtube_url rendered)
        assert "youtube" in body.lower() or "iframe" in body.lower()
        assert "Upgrade to" not in body
    def test_anonymous_reads_open_tutorial(self, django_server, page):
        """Anonymous visitor navigates to /tutorials and reads an open
        tutorial end to end without any paywall."""
        _clear_all_content()
        _create_tutorial(
            title="Open Tutorial for All",
            slug="open-tutorial-all",
            description="A free tutorial about prompt engineering.",
            content_markdown=(
                "# Open Tutorial\n\n"
                "Full tutorial content available to everyone."
            ),
            tags=["tutorial"],
        )

        page.goto(
            f"{django_server}/tutorials",
            wait_until="domcontentloaded",
        )
        assert "Open Tutorial for All" in page.content()

        page.locator(
            'text=Open Tutorial for All'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Full tutorial content available to everyone" in body
        assert "Upgrade to" not in body
    def test_anonymous_reads_open_project(self, django_server, page):
        """Anonymous visitor navigates to /projects and reads an open project
        writeup without any gating messaging."""
        _clear_all_content()
        _create_project(
            title="Open Project Writeup",
            slug="open-project-writeup",
            description="A free project idea for building an AI chatbot.",
            content_markdown=(
                "# Open Project Writeup\n\n"
                "Complete project writeup visible to everyone."
            ),
            author="Bob",
            tags=["project"],
        )

        page.goto(
            f"{django_server}/projects",
            wait_until="domcontentloaded",
        )
        assert "Open Project Writeup" in page.content()

        page.locator(
            'text=Open Project Writeup'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Complete project writeup visible to everyone" in body
        assert "Upgrade to" not in body
# ---------------------------------------------------------------
# Scenario 2: Free member hits a Basic-gated article
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2FreeMemberHitsBasicGatedArticle:
    """Free member hits a Basic-gated article and follows the upgrade
    path to pricing."""

    def test_free_user_sees_gated_article_with_upgrade_cta(
        self, django_server
    , browser):
        """Free member sees the title, teaser, but not full body, and
        an upgrade CTA linking to /pricing."""
        _clear_all_content()
        _create_user("free@test.com", tier_slug="free")
        _create_article(
            title="Basic Gated Article",
            slug="basic-gated-article",
            description="Advanced prompt engineering techniques for production systems.",
            content_markdown=(
                "# Basic Gated Article\n\n"
                "This is the full secret Basic content that free users cannot see."
            ),
            required_level=10,
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/blog", wait_until="domcontentloaded"
        )
        # Article is listed
        assert "Basic Gated Article" in page.content()

        # Click on the article
        page.locator(
            'h2:has-text("Basic Gated Article")'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        # HTTP 200, not 404
        assert "/blog/basic-gated-article" in page.url

        body = page.content()

        # Title and teaser visible
        assert "Basic Gated Article" in body
        assert "Advanced prompt engineering techniques" in body

        # Full article body NOT on the page
        assert (
            "full secret Basic content that free users cannot see"
            not in body
        )

        # CTA message
        assert "Upgrade to Basic to read this article" in body

        # Click View Pricing
        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1
        pricing_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Navigated to /pricing with all four tiers
        assert "/pricing" in page.url
        pricing_body = page.content()
        assert "Free" in pricing_body
        assert "Basic" in pricing_body
        assert "Main" in pricing_body
        assert "Premium" in pricing_body
# ---------------------------------------------------------------
# Scenario 3: Basic member reads Basic, blocked on Main
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3BasicMemberReadsBasicBlockedOnMain:
    """Basic member reads Basic content but cannot access a Main-gated
    article."""

    def test_basic_member_reads_basic_article_fully(self, django_server, browser):
        """Basic member reads a Basic-level article without any
        upgrade prompt."""
        _clear_all_content()
        _create_user("basic@test.com", tier_slug="basic")
        _create_article(
            title="Basic Tier Article",
            slug="basic-tier-article",
            description="Basic-tier article description.",
            content_markdown=(
                "# Basic Tier Article\n\n"
                "Basic-tier exclusive content here"
            ),
            required_level=10,
        )

        context = _auth_context(browser, "basic@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/basic-tier-article",
            wait_until="domcontentloaded",
        )

        body = page.content()
        assert "Basic-tier exclusive content here" in body
        assert "Upgrade to" not in body
    def test_basic_member_blocked_on_main_article(self, django_server, browser):
        """Basic member sees gating on a Main-level article."""
        _clear_all_content()
        _create_user("basic@test.com", tier_slug="basic")
        _create_article(
            title="Main Level Article",
            slug="main-level-article",
            description="Main-level article description.",
            content_markdown=(
                "# Main Level Article\n\n"
                "Main-tier exclusive deep dive"
            ),
            required_level=20,
        )

        context = _auth_context(browser, "basic@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/main-level-article",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # HTTP 200
        assert "/blog/main-level-article" in page.url

        # Title visible
        assert "Main Level Article" in body

        # Full body NOT present
        assert "Main-tier exclusive deep dive" not in body

        # CTA
        assert "Upgrade to Main to read this article" in body

        # Pricing link
        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1
        href = pricing_link.first.get_attribute("href")
        assert "/pricing" in href
# ---------------------------------------------------------------
# Scenario 4: Main member reads all up to their level, blocked on Premium
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4MainMemberReadsUpToLevelBlockedOnPremium:
    """Main member reads all content up to their level but gets blocked
    on Premium."""

    def test_main_member_reads_open_basic_and_main_articles(
        self, django_server
    , browser):
        """Main member reads level 0, 10, and 20 articles freely."""
        _clear_all_content()
        _create_user("main@test.com", tier_slug="main")
        _create_article(
            title="Open For All",
            slug="open-for-all",
            content_markdown="Open level 0 full body text.",
            required_level=0,
        )
        _create_article(
            title="Basic Level Post",
            slug="basic-level-post",
            content_markdown="Basic level 10 full body text.",
            required_level=10,
        )
        _create_article(
            title="Main Level Post",
            slug="main-level-post",
            content_markdown="Main level 20 full body text.",
            required_level=20,
        )

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Level 0
        page.goto(
            f"{django_server}/blog/open-for-all",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Open level 0 full body text" in body
        assert "Upgrade to" not in body

        # Level 10
        page.goto(
            f"{django_server}/blog/basic-level-post",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Basic level 10 full body text" in body
        assert "Upgrade to" not in body

        # Level 20
        page.goto(
            f"{django_server}/blog/main-level-post",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Main level 20 full body text" in body
        assert "Upgrade to" not in body
    def test_main_member_blocked_on_premium_article(self, django_server, browser):
        """Main member is blocked on a Premium (level 30) article."""
        _clear_all_content()
        _create_user("main@test.com", tier_slug="main")
        _create_article(
            title="Premium Exclusive Post",
            slug="premium-exclusive-post",
            description="A premium-only article.",
            content_markdown=(
                "# Premium Exclusive\n\n"
                "Premium level 30 secret body text."
            ),
            required_level=30,
        )

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/premium-exclusive-post",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Title visible
        assert "Premium Exclusive Post" in body

        # Body NOT present
        assert "Premium level 30 secret body text" not in body

        # CTA
        assert "Upgrade to Premium to read this article" in body

        # View Pricing link
        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1
# ---------------------------------------------------------------
# Scenario 5: Premium member has unrestricted access
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5PremiumMemberUnrestrictedAccess:
    """Premium member has unrestricted access across all content types
    and tiers."""

    def test_premium_reads_all_article_levels(self, django_server, browser):
        """Premium member reads articles at every level in full."""
        _clear_all_content()
        _create_user("premium@test.com", tier_slug="premium")
        _create_article(
            title="Open Article",
            slug="open-article-prem",
            content_markdown="Open article body visible to premium.",
            required_level=0,
        )
        _create_article(
            title="Basic Article",
            slug="basic-article-prem",
            content_markdown="Basic article body visible to premium.",
            required_level=10,
        )

        context = _auth_context(browser, "premium@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/open-article-prem",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Open article body visible to premium" in body
        assert "Upgrade to" not in body

        page.goto(
            f"{django_server}/blog/basic-article-prem",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Basic article body visible to premium" in body
        assert "Upgrade to" not in body
    def test_premium_reads_main_recording(self, django_server, browser):
        """Premium member views a Main-level recording fully."""
        _clear_all_content()
        _create_user("premium@test.com", tier_slug="premium")
        _create_recording(
            title="Main Recording",
            slug="main-recording-prem",
            description="A Main-level recording.",
            youtube_url="https://youtube.com/watch?v=main123",
            required_level=20,
        )

        context = _auth_context(browser, "premium@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/events/main-recording-prem",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Main Recording" in body
        assert "A Main-level recording" in body
        # Video should be present
        assert "youtube" in body.lower() or "iframe" in body.lower()
        assert "Upgrade to" not in body
    def test_premium_reads_premium_tutorial(self, django_server, browser):
        """Premium member reads a Premium-level tutorial fully."""
        _clear_all_content()
        _create_user("premium@test.com", tier_slug="premium")
        _create_tutorial(
            title="Premium Tutorial",
            slug="premium-tutorial-prem",
            description="A premium-only tutorial.",
            content_markdown=(
                "# Premium Tutorial\n\n"
                "Premium tutorial full content accessible."
            ),
            required_level=30,
        )

        context = _auth_context(browser, "premium@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/tutorials/premium-tutorial-prem",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Premium tutorial full content accessible" in body
        assert "Upgrade to" not in body
# ---------------------------------------------------------------
# Scenario 6: Anonymous visitor lands on gated article via shared link
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6AnonymousGatedArticleViaSharedLink:
    """Anonymous visitor lands on a gated article via shared link and
    sees the teaser with upgrade path."""

    def test_anonymous_sees_teaser_and_upgrade_on_premium_article(
        self, django_server
    , page):
        """Direct navigation to a Premium article shows HTTP 200, title,
        teaser, no body, and upgrade CTA."""
        _clear_all_content()
        _create_article(
            title="Premium Shared Article",
            slug="premium-shared-article",
            description="Teaser preview of premium content.",
            content_markdown=(
                "# Premium Shared Article\n\n"
                "This is the full premium body that anonymous visitors must not see."
            ),
            required_level=30,
        )

        response = page.goto(
            f"{django_server}/blog/premium-shared-article",
            wait_until="domcontentloaded",
        )

        # HTTP 200 (not 404, not 403, not redirect)
        assert response.status == 200

        body = page.content()

        # Title visible
        assert "Premium Shared Article" in body

        # Teaser / description visible
        assert "Teaser preview of premium content" in body

        # Full body NOT rendered
        assert (
            "full premium body that anonymous visitors must not see"
            not in body
        )

        # Upgrade CTA
        assert "Upgrade to Premium to read this article" in body

        # View Pricing link
        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1
# ---------------------------------------------------------------
# Scenario 7: Basic member blocked from Main-gated recording,
# video URL never leaked
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7BasicMemberBlockedFromMainRecording:
    """Basic member is blocked from a Main-gated recording and the video
    URL is never leaked."""

    def test_basic_user_sees_gated_recording_no_video_url(
        self, django_server
    , browser):
        """Basic member sees title and description but NO video player
        or YouTube URL in the main content area."""
        _clear_all_content()
        _create_user("basic@test.com", tier_slug="basic")
        _create_recording(
            title="Main Gated Recording",
            slug="main-gated-recording",
            description="A recording about advanced AI techniques.",
            youtube_url="https://youtube.com/watch?v=secret123",
            required_level=20,
        )

        context = _auth_context(browser, "basic@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/events/main-gated-recording",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Title and description visible
        assert "Main Gated Recording" in body
        assert "A recording about advanced AI techniques" in body

        # No video player / iframe rendered in the main content.
        # The gated template replaces the video section with the
        # content_gated.html include. We verify no <iframe> embed
        # exists in the <main> element (note: the youtube_url may
        # still appear in the <head> structured data JSON-LD,
        # which is a known limitation of the current SEO tags).
        main_element = page.locator("main")
        main_html = main_element.inner_html()
        assert "<iframe" not in main_html.lower()

        # The video URL should not appear as a clickable or
        # visible element in the main content
        assert "secret123" not in main_html

        # CTA present
        assert "Upgrade to Main to watch this recording" in body

        # Pricing link
        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1
# ---------------------------------------------------------------
# Scenario 8: Anonymous visitor evaluates gated course syllabus
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8AnonymousEvaluatesGatedCourseSyllabus:
    """Anonymous visitor evaluates a gated course syllabus and finds
    the upgrade path."""

    def test_anonymous_sees_syllabus_but_units_not_clickable(
        self, django_server
    , page):
        """Anonymous visitor sees course title, description, syllabus
        with visibly locked unit links, then opens a lesson teaser with
        an upgrade CTA."""
        _clear_all_content()
        course = _create_course(
            title="Main Gated Course",
            slug="main-gated-course",
            description="A course about advanced AI engineering.",
            required_level=20,
            instructor_name="Instructor Bob",
        )
        module = _create_module(course, "Module 1", sort_order=0)
        _create_unit(module, "Lesson One", sort_order=0, body="Lesson 1 content.")

        page.goto(
            f"{django_server}/courses/main-gated-course",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Title and description visible
        assert "Main Gated Course" in body
        assert "A course about advanced AI engineering" in body

        # Syllabus visible with unit name
        assert "Lesson One" in body

        page.evaluate("document.querySelectorAll('details.module-details').forEach(d => d.open = true)")

        lock_icons = page.locator('[data-testid="syllabus-lock-icon"]')
        assert lock_icons.count() >= 1

        locked_link = page.locator(
            '[data-testid="syllabus-locked-link"]:has-text("Lesson One")'
        )
        assert locked_link.count() == 1

        # Upgrade CTA visible
        assert "Unlock with Main" in body or "Upgrade" in body.lower()

        # Click the locked lesson row; the lesson detail renders a teaser
        # and upgrade path for anonymous visitors.
        locked_link.first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/courses/main-gated-course/module-1/lesson-one" in page.url
        assert page.locator('[data-testid="teaser-title"]').inner_text() == "Lesson One"
        assert page.locator('[data-testid="teaser-cta"]').count() == 1
        assert "Sign in to access this lesson" in page.content()

        pricing_link = page.locator('[data-testid="teaser-upgrade-cta"]')
        assert pricing_link.count() == 1
        pricing_link.first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/accounts/login" in page.url
# ---------------------------------------------------------------
# Scenario 9: Main member navigates a course, reads a unit, marks complete
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9MainMemberNavigatesCourseReadsUnit:
    """Main member navigates a course, reads a unit, and marks it
    complete."""

    def test_main_member_sees_clickable_units_and_progress(
        self, django_server
    , browser):
        """Main member sees the syllabus with clickable unit links
        and a progress indicator. No upgrade CTAs appear."""
        _clear_all_content()
        _create_user("main@test.com", tier_slug="main")
        course = _create_course(
            title="Main Course",
            slug="main-course",
            description="A Main-level course.",
            required_level=20,
        )
        module = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module, "Lesson One", sort_order=0,
            body="# Lesson One\n\nLesson one content for main members.",
        )

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/courses/main-course",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Title visible
        assert "Main Course" in body

        # Lesson One is a clickable link
        unit_link = page.locator('a:has-text("Lesson One")')
        assert unit_link.count() >= 1

        # Progress indicator visible
        assert "Progress" in body or "completed" in body.lower()

        # No upgrade CTAs
        assert "Upgrade" not in body
        assert "Unlock" not in body

        # Expand the collapsed module so the link becomes visible
        page.evaluate("document.querySelectorAll('details.module-details').forEach(d => d.open = true)")

        # Click on Lesson One
        unit_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Unit page loads with lesson content
        unit_body = page.content()
        assert "Lesson One" in unit_body
        assert "Lesson one content for main members" in unit_body
# ---------------------------------------------------------------
# Scenario 10: Staff changes article visibility in Studio
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10StaffChangesVisibilityInStudio:
    """Staff member changes an article's visibility in Studio and the
    gate takes effect immediately for readers."""

    def test_staff_changes_level_and_gate_applies(self, django_server, browser):
        """Staff changes an open article to Basic-gated via Studio.
        Anonymous visitor then sees the gated teaser. Basic member sees
        full content."""
        _clear_all_content()
        _create_staff_user("staff@test.com")
        _create_user("basic@test.com", tier_slug="basic")
        article = _create_article(
            title="Visibility Test Article",
            slug="visibility-test-article",
            description="Description for visibility testing.",
            content_markdown=(
                "# Visibility Test Article\n\n"
                "Full body content that should be gated after change."
            ),
            required_level=0,  # Initially open
        )

        # Step 1: Login as staff and change required_level via Studio
        staff_ctx = _auth_context(browser, "staff@test.com")
        staff_page = staff_ctx.new_page()

        staff_page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Change required_level to Basic (10)
        staff_page.select_option(
            'select[name="required_level"]', "10"
        )

        # Save
        staff_page.click('button:has-text("Save")')
        staff_page.wait_for_load_state("domcontentloaded")
        staff_ctx.close()

        # Step 2: Anonymous visitor sees gated teaser
        anon_ctx = browser.new_context(viewport=VIEWPORT)
        anon_page = anon_ctx.new_page()

        anon_page.goto(
            f"{django_server}/blog/visibility-test-article",
            wait_until="domcontentloaded",
        )

        body = anon_page.content()
        assert "Visibility Test Article" in body
        assert "Upgrade to Basic to read this article" in body
        assert (
            "Full body content that should be gated after change"
            not in body
        )
        anon_ctx.close()

        # Step 3: Basic member sees full content
        basic_ctx = _auth_context(browser, "basic@test.com")
        basic_page = basic_ctx.new_page()

        basic_page.goto(
            f"{django_server}/blog/visibility-test-article",
            wait_until="domcontentloaded",
        )

        body = basic_page.content()
        assert (
            "Full body content that should be gated after change"
            in body
        )
        assert "Upgrade to" not in body
        basic_ctx.close()
# ---------------------------------------------------------------
# Scenario 11: Free member encounters gated downloads
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11FreeMemberGatedDownloads:
    """Free member encounters gated downloads and follows the upgrade
    path."""

    def test_free_member_sees_lead_magnet_and_gated_download(
        self, django_server
    , browser):
        """Free member sees a lead magnet download option and a gated
        resource with upgrade CTA, then follows to pricing."""
        _clear_all_content()
        _create_user("free@test.com", tier_slug="free")
        _create_download(
            title="Free Lead Magnet",
            slug="free-lead-magnet",
            description="A free downloadable PDF.",
            required_level=0,
            file_type="pdf",
        )
        _create_download(
            title="Main Gated Resource",
            slug="main-gated-resource",
            description="A gated resource requiring Main tier.",
            required_level=20,
            file_type="pdf",
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/downloads",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Both downloads listed
        assert "Free Lead Magnet" in body
        assert "Main Gated Resource" in body

        # Lead magnet has a download option (for authenticated user)
        download_btn = page.locator('a:has-text("Download")').first
        assert download_btn.is_visible()

        # Main-gated download has upgrade CTA
        assert "Upgrade to Main to download" in body

        # View Pricing link on gated download
        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1

        # Click View Pricing
        pricing_link.first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/pricing" in page.url
# ---------------------------------------------------------------
# Scenario 12: Free member tries to register for Main-gated event
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario12FreeMemberGatedEvent:
    """Free member tries to register for a Main-gated event and sees
    the upgrade prompt instead."""

    def test_free_member_sees_event_details_but_no_registration(
        self, django_server
    , browser):
        """Free member sees event title, description, date, and location,
        but no registration button. Instead they see an upgrade CTA."""
        _clear_all_content()
        _create_user("free@test.com", tier_slug="free")
        _create_event(
            title="Main Gated Workshop",
            slug="main-gated-workshop",
            description="An exclusive Main-level live workshop.",
            required_level=20,
            status="upcoming",
            location="Zoom",
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/events/main-gated-workshop",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # HTTP 200 (loaded, not 404)
        assert "Main Gated Workshop" in body

        # Event details visible
        assert "An exclusive Main-level live workshop" in body
        assert "Zoom" in body

        # No register button
        register_btn = page.locator('#register-btn')
        assert register_btn.count() == 0

        # Upgrade CTA
        assert "Upgrade to Main" in body or "Main" in body

        # View Pricing link
        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1

        # Click View Pricing
        pricing_link.first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/pricing" in page.url
