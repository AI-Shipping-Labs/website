"""
Playwright E2E tests for the Logged-in User Home Dashboard (Issue #104).

Tests cover browser-valued BDD scenarios from the issue:
- Anonymous visitor still sees the public marketing homepage
- Free member sees personalized dashboard after login
- Free member with no activity sees helpful empty states that guide next steps
- Basic member resumes an in-progress course from the dashboard
- Main member sees upcoming registered events and navigates to event detail
- Main member sees the Community quick action that Free members do not
- Free member discovers gated content in recent content and finds the upgrade path
- Premium member sees active polls and navigates to vote
- Dashboard omits the duplicate notifications card while the header bell remains
- Dashboard omits the removed welcome-card upgrade link

Usage:
    uv run pytest playwright_tests/test_dashboard.py -v
"""

import datetime
import os
from pathlib import Path

import pytest
from django.utils import timezone
from freezegun import freeze_time

from playwright_tests.conftest import (
    VIEWPORT,
)
from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_site_config_tiers as _ensure_site_config_tiers,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only
SCREENSHOT_DIR = (
    Path(__file__).parent.parent / ".tmp" / "aisl-issue-1211-screenshots"
)


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _anon_context(browser):
    """Create an anonymous browser context."""
    context = browser.new_context(viewport=VIEWPORT)
    return context


def _clear_dashboard_data():
    """Delete data that affects the dashboard to ensure clean state."""
    from content.models import (
        Article,
        Course,
        Enrollment,
        Module,
        Unit,
        UserContentCompletion,
        UserCourseProgress,
        Workshop,
        WorkshopPage,
    )
    from events.models import Event, EventRegistration, EventSeries
    from notifications.models import Notification
    from plans.models import Plan, Sprint, SprintEnrollment
    from voting.models import Poll, PollOption, PollVote

    Notification.objects.all().delete()
    PollVote.objects.all().delete()
    PollOption.objects.all().delete()
    Poll.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    Enrollment.objects.all().delete()
    UserCourseProgress.objects.all().delete()
    UserContentCompletion.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Article.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _create_article(
    title,
    slug,
    description="",
    required_level=0,
    published=True,
    date=None,
):
    """Create an Article via ORM."""
    from content.models import Article

    if date is None:
        date = datetime.date.today()
    article = Article(
        title=title,
        slug=slug,
        description=description,
        content_markdown=f"# {title}\n\nSome content here.",
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
    required_level=0,
    published=True,
    date=None,
):
    """Helper to create a completed event with a recording via the ORM.

    The events/recordings unification merged Recording into Event. This
    helper keeps the legacy external kwarg (`date`) so call sites do
    not change, and translates it internally to a timezone-aware
    `start_datetime`. The event is created with status='completed' so
    it appears on /events?filter=past.
    """
    from events.models import Event

    if date is None:
        date = datetime.date.today()

    start_dt = timezone.make_aware(
        datetime.datetime.combine(date, datetime.time(12, 0))
    )

    recording = Event(
        title=title,
        slug=slug,
        description=description,
        required_level=required_level,
        published=published,
        start_datetime=start_dt,
        status="completed",
    )
    recording.save()
    connection.close()
    return recording


def _create_course(
    title,
    slug,
    description="",
    required_level=0,
    status="published",
):
    """Create a Course via ORM."""
    from content.models import Course

    course = Course(
        title=title,
        slug=slug,
        description=description,
        required_level=required_level,
        status=status,
    )
    course.save()
    connection.close()
    return course


def _create_module(course, title, sort_order=0):
    """Create a Module within a course."""
    from django.utils.text import slugify

    from content.models import Module

    module = Module(
        course=course,
        title=title,
        slug=slugify(title),
        sort_order=sort_order,
    )
    module.save()
    connection.close()
    return module


def _create_unit(module, title, sort_order=0):
    """Create a Unit within a module."""
    from django.utils.text import slugify

    from content.models import Unit

    unit = Unit(
        module=module,
        title=title,
        slug=slugify(title),
        sort_order=sort_order,
        body=f"# {title}\n\nLesson content.",
    )
    unit.save()
    connection.close()
    return unit


def _mark_unit_completed(user, unit, completed_at=None):
    """Mark a unit as completed for a user."""
    from content.models import UserCourseProgress

    if completed_at is None:
        completed_at = timezone.now()
    progress, created = UserCourseProgress.objects.get_or_create(
        user=user,
        unit=unit,
        defaults={"completed_at": completed_at},
    )
    if not created:
        progress.completed_at = completed_at
        progress.save()
    connection.close()
    return progress


def _enroll_user(user, course):
    """Create an active course Enrollment for dashboard Continue learning."""
    from content.models import Enrollment

    enrollment, _ = Enrollment.objects.get_or_create(user=user, course=course)
    connection.close()
    return enrollment


def _register_user_for_event(user, event):
    """Create an EventRegistration for dashboard checks."""
    from events.models import EventRegistration

    registration, _ = EventRegistration.objects.get_or_create(
        user=user,
        event=event,
    )
    connection.close()
    return registration


def _create_event(
    title,
    slug,
    description="",
    required_level=0,
    status="upcoming",
    start_datetime=None,
):
    """Create an Event via ORM."""
    from events.models import Event

    if start_datetime is None:
        start_datetime = timezone.now() + datetime.timedelta(days=7)
    event = Event(
        title=title,
        slug=slug,
        description=description,
        required_level=required_level,
        status=status,
        start_datetime=start_datetime,
    )
    event.save()
    connection.close()
    return event


def _create_sprint(
    name,
    slug,
    *,
    min_tier_level=20,
    status="active",
    start_offset_days=-7,
    duration_weeks=6,
):
    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=datetime.date.today() + datetime.timedelta(
            days=start_offset_days,
        ),
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=min_tier_level,
    )
    connection.close()
    return sprint


def _create_plan(user, sprint, *, shared=True):
    from plans.models import Plan

    plan = Plan.objects.create(
        member=user,
        sprint=sprint,
        shared_at=timezone.now() if shared else None,
    )
    connection.close()
    return plan


def _enroll_sprint(user, sprint):
    from plans.models import SprintEnrollment

    enrollment = SprintEnrollment.objects.create(user=user, sprint=sprint)
    connection.close()
    return enrollment


def _register_user_for_event(user, event):
    """Register a user for an event."""
    from events.models import EventRegistration

    reg, created = EventRegistration.objects.get_or_create(
        event=event,
        user=user,
    )
    connection.close()
    return reg


def _create_poll(
    title,
    poll_type="topic",
    description="",
    status="open",
    allow_proposals=False,
    max_votes_per_user=3,
    closes_at=None,
):
    """Create a Poll via ORM."""
    from voting.models import Poll

    poll = Poll(
        title=title,
        description=description,
        poll_type=poll_type,
        status=status,
        allow_proposals=allow_proposals,
        max_votes_per_user=max_votes_per_user,
        closes_at=closes_at,
    )
    poll.save()
    connection.close()
    return poll


def _create_poll_option(poll, title, description=""):
    """Create a PollOption via ORM."""
    from voting.models import PollOption

    option = PollOption(
        poll=poll,
        title=title,
        description=description,
    )
    option.save()
    connection.close()
    return option


def _create_notification(
    user,
    title,
    body="",
    url="",
    notification_type="new_content",
    read=False,
):
    """Create a Notification for the given user."""
    from notifications.models import Notification

    connection.close()
    return Notification.objects.create(
        user=user,
        title=title,
        body=body,
        url=url,
        notification_type=notification_type,
        read=read,
    )


# -------------------------------------------------------------------
# Scenario 1: Anonymous visitor still sees the public marketing
#              homepage
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1AnonymousVisitorSeesPublicHomepage:
    """Anonymous visitor still sees the public marketing homepage."""

    @pytest.mark.core
    def test_anonymous_sees_hero_and_tiers_not_dashboard(
        self, django_server
    , page):
        """Given: An anonymous visitor (not logged in).
        1. Navigate to /
        Then: The public marketing homepage loads with the hero section,
              tier cards, testimonials, and newsletter signup.
        Then: No personalized dashboard sections (continue learning,
              upcoming events) are shown.
        2. Click 'View Membership Tiers' in the hero section
        Then: The page scrolls to the tiers section showing Free,
              Basic, Main, and Premium options."""
        _ensure_tiers()
        _ensure_site_config_tiers()

        # Step 1: Navigate to /
        response = page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        body = page.content()

        # Then: Public homepage elements are present
        assert "Turn AI ideas into" in body
        assert "real projects" in body
        assert "View Membership Tiers" in body

        # Testimonials section
        assert "testimonials" in body.lower() or "Rolando" in body

        # Newsletter signup (in footer or dedicated section)
        assert "Subscribe" in body

        # Then: No dashboard sections shown
        assert "Welcome back" not in body
        assert "Continue learning" not in body
        assert "Upcoming events" not in body
        assert "Quick actions" not in body

        # Step 2: Click "View Membership Tiers"
        tiers_link = page.locator(
            'a:has-text("View Membership Tiers")'
        )
        assert tiers_link.count() >= 1
        tiers_link.first.click()

        # Wait for scroll to settle
        page.wait_for_load_state("domcontentloaded")

        # Then: Tiers section is visible with all tier names
        tiers_section = page.locator("#tiers")
        assert tiers_section.count() >= 1
        tiers_text = tiers_section.inner_text()
        assert "Basic" in tiers_text
        assert "Main" in tiers_text
        assert "Premium" in tiers_text
# -------------------------------------------------------------------
# Scenario 2: Free member sees personalized dashboard after login
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2FreeMemberSeesDashboard:
    """Free member sees personalized dashboard after login."""

    @pytest.mark.core
    def test_free_member_dashboard_without_marketing_homepage(
        self, django_server
    , browser):
        """Given: A user logged in as free@test.com (Free tier,
        first name 'Alex').
        1. Navigate to /
        Then: The dashboard loads with member-only sections.
        Then: The marketing homepage hero, testimonials, and tier
              cards are not shown.
        2. Open the account menu and click Account.
        Then: User navigates to /account/."""
        _clear_dashboard_data()
        _create_user(
            "free@test.com",
            tier_slug="free",
            first_name="Alex",
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Dashboard identity header and member-only sections render.
        assert page.get_by_role("heading", name="Welcome back, Alex").count() == 1
        assert page.locator('[data-testid="dashboard-tier-pill"]').inner_text() == "Free"
        assert "Continue learning" in body
        assert "Quick actions" in body

        # Then: Marketing homepage elements NOT shown
        assert "Turn AI ideas into" not in body
        assert "View Membership Tiers" not in body

        # Step 2: Click the account-menu Account link.
        page.locator("#account-menu-trigger").click()
        page.locator("#account-menu-dropdown").get_by_role(
            "menuitem", name="Account"
        ).click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to /account/
        assert "/account" in page.url
# -------------------------------------------------------------------
# Scenario 3: Free member with no activity sees helpful empty states
#              that guide next steps
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3EmptyStatesGuideNextSteps:
    """Free member with no activity sees helpful empty states that
    guide next steps."""

    def test_empty_dashboard_shows_ctas(
        self, django_server
    , browser):
        """Given: A user logged in as free@test.com (Free tier) who
        has no course progress, no event registrations, and no unread
        notifications.
        1. Navigate to /
        Then: The 'Continue learning' section shows no courses or
              workshops in progress with Browse courses and Browse
              Workshops links.
        Then: The 'Upcoming events' section shows 'No upcoming events'
              with a 'Browse events' link.
        Then: The dashboard body does not show a notifications empty-state card.
        2. Click 'Browse courses' in the empty continue learning
           section.
        Then: User navigates to /courses and sees the course catalog."""
        _clear_dashboard_data()
        _create_user("free@test.com", tier_slug="free")

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Empty state messages
        assert "No courses or workshops in progress yet" in body
        assert "No upcoming events" in body
        assert "No content available yet" in body
        assert "No new notifications" not in body
        assert page.locator('[data-testid="member-empty-state"]').count() >= 3

        # CTA links present
        browse_courses_link = page.locator(
            'a:has-text("Browse courses")'
        )
        assert browse_courses_link.count() >= 1
        assert browse_courses_link.first.get_attribute("href") == "/courses"
        browse_workshops_link = page.locator(
            'a:has-text("Browse workshops")'
        )
        assert browse_workshops_link.count() >= 1
        assert browse_workshops_link.first.get_attribute("href") == "/workshops"

        browse_events_link = page.locator(
            'a:has-text("Browse events")'
        )
        assert browse_events_link.count() >= 1
        assert browse_events_link.first.get_attribute("href") == "/events"
        browse_blog_link = page.locator('a:has-text("Browse blog")')
        assert browse_blog_link.count() >= 1
        assert browse_blog_link.first.get_attribute("href") == "/blog"

        # Step 2: Click "Browse courses" in the empty
        # continue learning section
        browse_courses_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to /courses
        assert "/courses" in page.url


# -------------------------------------------------------------------
# Scenario 3b: Free activation checklist and teaser guide first steps
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3bFreeActivationDashboard:
    """Free activation checklist and teaser navigation."""

    @pytest.mark.core
    def test_new_free_member_can_navigate_checklist_and_teaser(
        self, django_server, browser
    ):
        _clear_dashboard_data()
        _ensure_tiers()
        _ensure_site_config_tiers()
        _create_course(
            title="AI Hero",
            slug="aihero",
            description="Start building useful AI products.",
            required_level=0,
        )
        _create_user("free-activation@test.com", tier_slug="free")

        context = _auth_context(browser, "free-activation@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        assert page.get_by_role("heading", name="Welcome back").count() == 1
        assert page.locator('[data-testid="dashboard-tier-pill"]').inner_text() == "Free"
        checklist = page.locator('[data-testid="free-activation-checklist"]')
        assert checklist.count() == 1
        assert "Start AI Hero" in checklist.inner_text()
        assert "Register for a free event" in checklist.inner_text()
        assert "Learn how sprints and plans work" in checklist.inner_text()

        first_empty_state = page.locator('[data-testid="member-empty-state"]').first
        checklist_box = checklist.bounding_box()
        empty_box = first_empty_state.bounding_box()
        assert checklist_box is not None
        assert empty_box is not None
        assert checklist_box["y"] < empty_box["y"]
        assert page.locator('[data-testid="onboarding-prompt"]').count() == 0
        _shot(page, "free-dashboard-desktop")

        page.locator('[data-testid="free-activation-action-ai-hero"]').click()
        page.wait_for_url("**/courses/aihero*", timeout=10000)
        assert "/courses/aihero" in page.url

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="free-activation-action-events"]').click()
        page.wait_for_url("**/events*", timeout=10000)
        assert "/events" in page.url

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="free-activation-action-sprints"]').click()
        page.wait_for_url("**/activities#community-sprints", timeout=10000)
        assert "/activities#community-sprints" in page.url

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        teaser = page.locator('[data-testid="free-plan-teaser"]')
        assert teaser.count() == 1
        teaser_text = teaser.inner_text()
        assert "personal plan" in teaser_text
        assert "sprint with accountability" in teaser_text
        assert "Slack/community support" in teaser_text
        page.locator('[data-testid="free-plan-teaser-cta"]').click()
        page.wait_for_url("**/pricing*", timeout=10000)
        pricing_body = page.content()
        assert "Basic" in pricing_body
        assert "Main" in pricing_body
        assert "Premium" in pricing_body

    @pytest.mark.core
    def test_free_member_activity_marks_checklist_items_complete(
        self, django_server, browser
    ):
        _clear_dashboard_data()
        user = _create_user("free-progress@test.com", tier_slug="free")
        course = _create_course(
            title="AI Hero",
            slug="aihero",
            description="Start building useful AI products.",
            required_level=0,
        )
        module = _create_module(course, "Start", sort_order=1)
        unit = _create_unit(module, "First build", sort_order=1)
        _enroll_user(user, course)
        _mark_unit_completed(user, unit)
        event = _create_event(
            title="Open Event",
            slug="open-event",
            required_level=0,
            status="upcoming",
            start_datetime=timezone.now() + datetime.timedelta(days=3),
        )
        _register_user_for_event(user, event)

        context = _auth_context(browser, "free-progress@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        assert (
            page.locator('[data-testid="free-activation-item-ai-hero"]')
            .get_attribute("data-complete")
            == "true"
        )
        assert (
            page.locator('[data-testid="free-activation-item-events"]')
            .get_attribute("data-complete")
            == "true"
        )
        assert (
            page.locator('[data-testid="free-activation-item-sprints"]')
            .get_attribute("data-complete")
            == "false"
        )
        assert page.locator(
            '[data-testid="free-activation-complete-ai-hero"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="free-activation-complete-events"]'
        ).count() == 1
# -------------------------------------------------------------------
# Scenario 4: Basic member resumes an in-progress course from the
#              dashboard
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4BasicMemberResumesCourse:
    """Basic member resumes an in-progress course from the dashboard."""

    @pytest.mark.core
    def test_in_progress_course_with_progress_bar(
        self, django_server
    , browser):
        """Given: A user logged in as basic@test.com (Basic tier) who
        has completed 3 of 10 units in a course titled
        'AI Agents Buildcamp'.
        1. Navigate to /
        Then: The 'Continue learning' section shows 'AI Agents
              Buildcamp' with a progress bar at 30% and
              '3/10 units completed'.
        Then: The last completed unit title is displayed below the
              progress bar.
        2. Click 'Continue' on the course card.
        Then: User navigates to the course detail page at
              /courses/ai-agents-buildcamp."""
        _clear_dashboard_data()
        user = _create_user(
            "basic@test.com", tier_slug="basic"
        )

        # Create a course with 10 units
        course = _create_course(
            title="AI Agents Buildcamp",
            slug="ai-agents-buildcamp",
            description="Learn to build AI agents.",
            required_level=0,
        )
        module = _create_module(course, "Module 1", sort_order=0)
        units = []
        for i in range(10):
            unit = _create_unit(
                module, f"Unit {i + 1}", sort_order=i
            )
            units.append(unit)

        # Complete 3 units
        base_time = timezone.now() - datetime.timedelta(hours=10)
        for i in range(3):
            _mark_unit_completed(
                user,
                units[i],
                completed_at=base_time + datetime.timedelta(hours=i),
            )

        _enroll_user(user, course)

        context = _auth_context(browser, "basic@test.com")
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Course shown in Continue learning
        assert "AI Agents Buildcamp" in body
        assert "3/10 units completed" in body
        learning_box = page.locator(
            '[data-testid="dashboard-continue-learning-section"]'
        ).bounding_box()
        onboarding_box = page.locator(
            '[data-testid="onboarding-prompt"]'
        ).bounding_box()
        assert learning_box is not None
        assert onboarding_box is not None
        assert learning_box["y"] < onboarding_box["y"]

        # Progress bar at 30% is rendered (check style attr)
        progress_bar = page.locator(
            'div[style*="width: 30%"]'
        )
        assert progress_bar.count() >= 1

        # Last completed unit title shown
        assert "Unit 3" in body

        # Step 2: Click "Continue"
        continue_btn = page.locator(
            'a:has-text("Continue")'
        ).first
        continue_btn.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to course detail
        assert "/courses/ai-agents-buildcamp" in page.url
# -------------------------------------------------------------------
# Scenario 5: Main member sees upcoming registered events and
#              navigates to event detail
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5MainMemberSeesUpcomingEvents:
    """Main member sees upcoming registered events and navigates
    to event detail."""

    @pytest.mark.core
    def test_upcoming_events_shown_ordered_by_date(
        self, django_server
    , browser):
        """Given: A user logged in as main@test.com (Main tier) who
        is registered for 2 upcoming events.
        1. Navigate to /
        Then: The 'Upcoming events' section shows both registered
              events with their titles and dates.
        Then: Events are ordered by start date (soonest first).
        2. Click 'View event' on the first event.
        Then: User navigates to the event detail page at
              /events/{slug} showing the full event description,
              schedule, and registration status."""
        _clear_dashboard_data()
        user = _create_user(
            "main@test.com", tier_slug="main"
        )

        # Create 2 events: event1 is sooner, event2 is later
        event1 = _create_event(
            title="AI Workshop: Prompt Engineering",
            slug="ai-workshop-prompt-engineering",
            description="Learn prompt engineering techniques.",
            start_datetime=timezone.now() + datetime.timedelta(days=3),
        )
        event2 = _create_event(
            title="RAG Pipeline Deep Dive",
            slug="rag-pipeline-deep-dive",
            description="Deep dive into RAG pipelines.",
            start_datetime=timezone.now() + datetime.timedelta(days=10),
        )

        # Register user for both
        _register_user_for_event(user, event1)
        _register_user_for_event(user, event2)

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Both events shown
        assert "AI Workshop: Prompt Engineering" in body
        assert "RAG Pipeline Deep Dive" in body

        # Events ordered by date: verify that prompt
        # engineering appears before RAG in the HTML
        pos1 = body.index("AI Workshop: Prompt Engineering")
        pos2 = body.index("RAG Pipeline Deep Dive")
        assert pos1 < pos2, (
            "Events should be ordered soonest first"
        )

        # Step 2: Click "View event" on the first event
        view_event_links = page.locator(
            'a:has-text("View event")'
        )
        assert view_event_links.count() >= 1
        view_event_links.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to the event detail page.
        # Issue #673: canonical URL is ``/events/<id>/<slug>``.
        assert event1.get_absolute_url() in page.url
        event_body = page.content()
        assert "AI Workshop: Prompt Engineering" in event_body


@pytest.mark.django_db(transaction=True)
class TestIssue1211StartingSoonEvent:
    @pytest.mark.core
    def test_starting_soon_event_is_first_dashboard_action(
        self, django_server, browser,
    ):
        _clear_dashboard_data()
        user = _create_user("main-event@test.com", tier_slug="main")
        event = _create_event(
            title="Urgent Shipping Call",
            slug="urgent-shipping-call",
            description="Registered members can open the event detail.",
            start_datetime=timezone.now() + datetime.timedelta(minutes=7),
        )
        _register_user_for_event(user, event)

        context = _auth_context(browser, "main-event@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        heading = page.locator('[data-testid="dashboard-heading"]')
        soon = page.locator('[data-testid="starting-soon-card"]')
        upcoming = page.locator('[data-testid="dashboard-upcoming-events-section"]')
        heading.wait_for(state="visible")
        soon.wait_for(state="visible")
        assert heading.bounding_box()["y"] < soon.bounding_box()["y"]
        assert soon.bounding_box()["y"] < upcoming.bounding_box()["y"]
        assert "Urgent Shipping Call" in soon.inner_text()

        soon.locator('[data-testid="starting-soon-event-button"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert event.get_absolute_url() in page.url


@pytest.mark.django_db(transaction=True)
class TestScenario1028DashboardSeriesCollapse:
    @pytest.mark.core
    def test_registered_series_collapses_to_next_occurrence(
        self, django_server, browser
    ):
        from events.models import Event, EventRegistration, EventSeries

        _clear_dashboard_data()
        user = _create_user("series-main@test.com", tier_slug="main")
        now = timezone.now()
        series = EventSeries.objects.create(
            name="LLM Zoomcamp 2026 office hours",
            slug="llm-zoomcamp-2026-office-hours",
            start_time=datetime.time(18, 0),
            timezone="UTC",
        )
        for i in range(3):
            event = Event.objects.create(
                title=f"LLM Zoomcamp Office Hours Session {i + 1}",
                slug=f"llm-zoomcamp-office-hours-{i + 1}",
                start_datetime=now + datetime.timedelta(days=i + 1),
                status="upcoming",
                origin="studio",
                timezone="UTC",
                event_series=series,
                series_position=i + 1,
            )
            EventRegistration.objects.create(user=user, event=event)
        standalone = Event.objects.create(
            title="Standalone Implementation Clinic",
            slug="standalone-implementation-clinic",
            start_datetime=now + datetime.timedelta(days=4),
            status="upcoming",
            origin="studio",
            timezone="UTC",
        )
        EventRegistration.objects.create(user=user, event=standalone)
        connection.close()

        context = _auth_context(browser, "series-main@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        body = page.locator("body").inner_text()

        assert "LLM Zoomcamp Office Hours Session 1" in body
        assert "LLM Zoomcamp Office Hours Session 2" not in body
        assert "LLM Zoomcamp Office Hours Session 3" not in body
        assert "Standalone Implementation Clinic" in body
        assert page.locator(
            '[data-testid="dashboard-event-series-badge"]'
        ).count() == 1
        see_more = page.locator(
            '[data-testid="dashboard-event-series-see-more"]'
        )
        assert see_more.count() == 1
        assert see_more.first.get_attribute("href") == series.get_absolute_url()

        context.close()

    @pytest.mark.core
    @freeze_time("2026-06-17T12:00:00Z")
    def test_visible_dashboard_event_dates_use_member_timezone(
        self, django_server, browser
    ):
        from events.models import Event, EventRegistration, EventSeries

        _clear_dashboard_data()
        user = _create_user("dashboard-local-time@test.com", tier_slug="main")
        user.preferred_timezone = "Europe/Berlin"
        user.save(update_fields=["preferred_timezone"])

        standalone = Event.objects.create(
            title="Standalone Local Time Clinic",
            slug="standalone-local-time-clinic",
            start_datetime=datetime.datetime(
                2026, 6, 24, 16, 0, tzinfo=datetime.timezone.utc
            ),
            status="upcoming",
            origin="studio",
            timezone="UTC",
        )
        EventRegistration.objects.create(user=user, event=standalone)

        series = EventSeries.objects.create(
            name="LLM Zoomcamp 2026 office hours",
            slug="llm-zoomcamp-local-time",
            start_time=datetime.time(18, 0),
            timezone="UTC",
        )
        for i, start_datetime in enumerate(
            [
                datetime.datetime(
                    2026, 6, 25, 16, 0, tzinfo=datetime.timezone.utc
                ),
                datetime.datetime(
                    2026, 7, 2, 16, 0, tzinfo=datetime.timezone.utc
                ),
            ],
            start=1,
        ):
            event = Event.objects.create(
                title=f"LLM Local Time Session {i}",
                slug=f"llm-local-time-session-{i}",
                start_datetime=start_datetime,
                status="upcoming",
                origin="studio",
                timezone="UTC",
                event_series=series,
                series_position=i,
            )
            EventRegistration.objects.create(user=user, event=event)
        connection.close()

        context = _auth_context(browser, "dashboard-local-time@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        body = page.locator("body").inner_text()
        assert "Standalone Local Time Clinic" in body
        assert "LLM Local Time Session 1" in body
        assert "LLM Local Time Session 2" not in body
        assert "Wed, Jun 24, 2026, 18:00 Europe/Berlin" in body
        assert "Thu, Jun 25, 2026, 18:00 Europe/Berlin" in body
        assert "June 24, 2026 at 16:00 UTC" not in body

        row = page.locator('[data-testid="dashboard-upcoming-event-row"]').filter(
            has_text="Standalone Local Time Clinic"
        )
        row.locator('[data-testid="dashboard-event-action"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert standalone.get_absolute_url() in page.url

        context.close()

    @pytest.mark.core
    @freeze_time("2026-06-17T12:00:00Z")
    def test_dashboard_event_dates_fit_390px_mobile(
        self, django_server, browser
    ):
        from events.models import Event, EventRegistration

        _clear_dashboard_data()
        user = _create_user("dashboard-mobile-time@test.com", tier_slug="main")
        user.preferred_timezone = "Europe/Berlin"
        user.save(update_fields=["preferred_timezone"])
        event = Event.objects.create(
            title=(
                "Very Long Dashboard Implementation Clinic With Local Time"
            ),
            slug="very-long-dashboard-local-time",
            start_datetime=datetime.datetime(
                2026, 6, 24, 16, 0, tzinfo=datetime.timezone.utc
            ),
            status="upcoming",
            origin="studio",
            timezone="UTC",
        )
        EventRegistration.objects.create(user=user, event=event)
        connection.close()

        context = _auth_context(browser, "dashboard-mobile-time@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        assert page.locator('[data-testid="dashboard-event-date"]').inner_text() == (
            "Wed, Jun 24, 2026, 18:00 Europe/Berlin"
        )
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        assert not page.evaluate(
            """() => {
                const row = document.querySelector(
                  '[data-testid="dashboard-upcoming-event-row"]'
                );
                const date = row.querySelector(
                  '[data-testid="dashboard-event-date"]'
                );
                const action = row.querySelector(
                  '[data-testid="dashboard-event-action"]'
                );
                const d = date.getBoundingClientRect();
                const a = action.getBoundingClientRect();
                return !(d.right <= a.left || d.left >= a.right ||
                         d.bottom <= a.top || d.top >= a.bottom);
            }"""
        )

        context.close()
# -------------------------------------------------------------------
# Scenario 6: Members see valid high-value quick actions
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6DashboardQuickActions:
    """Members see valid dashboard quick actions."""

    def test_quick_actions_use_current_member_destinations(
        self, django_server, browser,
    ):
        """Given: A user logged in as main@test.com (Main tier).
        1. Navigate to /
        Then: The 'Quick actions' section includes current member
              destinations for courses, workshops, resources, events,
              projects, and activities.
        2. Log out and log in as free@test.com (Free tier).
        3. Navigate to /
        Then: The same valid destinations are present and no
              nonexistent Community link is shown."""
        _clear_dashboard_data()
        _create_user("main@test.com", tier_slug="main")
        _create_user("free@test.com", tier_slug="free")


        # Step 1: Main member sees route-backed dashboard actions.
        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        quick = page.locator('[data-testid="dashboard-quick-actions"]')
        body = quick.inner_html()

        # Then: Current quick actions are present and route-backed.
        assert "Browse courses" in body
        assert "Browse workshops" in body
        assert "Resources" in body
        assert "Events and recordings" in body
        assert "Projects" in body
        assert "Activities" in body
        assert 'href="/community"' not in body

        context.close()

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        quick = page.locator('[data-testid="dashboard-quick-actions"]')
        body = quick.inner_html()

        assert "Browse courses" in body
        assert "Browse workshops" in body
        assert "Resources" in body
        assert "Events and recordings" in body
        assert "Projects" in body
        assert "Activities" in body
        assert 'href="/community"' not in body

    @pytest.mark.core
    def test_quick_actions_are_full_width_after_primary_sections(
        self, django_server, browser,
    ):
        _clear_dashboard_data()
        _create_user("main-actions@test.com", tier_slug="main")

        context = _auth_context(browser, "main-actions@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        primary = page.locator('[data-testid="dashboard-primary-sections"]')
        quick = page.locator('[data-testid="dashboard-quick-actions-section"]')
        assert primary.bounding_box()["y"] < quick.bounding_box()["y"]
        actions = page.locator('[data-testid="dashboard-quick-action"]')
        assert actions.count() == 6
        quick_text = quick.inner_text()
        for label in [
            "Browse courses",
            "Browse workshops",
            "Resources",
            "Events and recordings",
            "Projects",
            "Activities",
        ]:
            assert label in quick_text

        quick.get_by_role("link", name="Activities").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/activities")


@pytest.mark.django_db(transaction=True)
class TestIssue1211SprintAndPlanSurfaces:
    @pytest.mark.core
    def test_planned_main_member_no_onboarding_wall_and_slack_secondary(
        self, django_server, browser, settings,
    ):
        settings.SLACK_INVITE_URL = "https://join.slack.com/issue-1211"
        _clear_dashboard_data()
        user = _create_user("main-plan@test.com", tier_slug="main")
        sprint = _create_sprint("Current Sprint", "current-sprint-1211")
        _create_plan(user, sprint, shared=True)

        context = _auth_context(browser, "main-plan@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        assert page.locator('[data-testid="onboarding-prompt"]').count() == 0
        assert page.locator('[data-testid="account-sprint-plan-card"]').count() == 1
        assert page.get_by_text("Open my plan").count() == 1
        assert page.locator('[data-testid="slack-account-card"]').count() == 1
        assert "Tell us a bit about you so we can build your plan" not in page.content()

        learning_y = page.locator(
            '[data-testid="dashboard-continue-learning-section"]'
        ).bounding_box()["y"]
        slack_y = page.locator('[data-testid="slack-account-card"]').bounding_box()["y"]
        assert learning_y < slack_y
        _shot(page, "main-planned-dashboard-desktop")

    @pytest.mark.core
    def test_planned_member_sees_other_cohorts_without_current_duplicate(
        self, django_server, browser,
    ):
        _clear_dashboard_data()
        user = _create_user("main-cohorts@test.com", tier_slug="main")
        current = _create_sprint("Current Cohort", "current-cohort-1211")
        other = _create_sprint(
            "Other Cohort",
            "other-cohort-1211",
            start_offset_days=-3,
        )
        _create_plan(user, current, shared=True)

        context = _auth_context(browser, "main-cohorts@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        assert page.get_by_text("Other cohorts").count() == 1
        active = page.locator('[data-testid="dashboard-active-sprints"]')
        assert "Other Cohort" in active.inner_text()
        assert "Current Cohort" not in active.inner_text()

        active.locator('[data-testid="dashboard-active-sprint"]').first.click()
        page.wait_for_load_state("domcontentloaded")
        assert f"/sprints/{other.slug}" in page.url

    @pytest.mark.core
    def test_free_member_sees_only_free_open_sprints(
        self, django_server, browser,
    ):
        _clear_dashboard_data()
        _create_user("free-sprints@test.com", tier_slug="free")
        free_sprint = _create_sprint(
            "Free Sprint",
            "free-sprint-1211",
            min_tier_level=0,
        )
        _create_sprint("Main Sprint", "main-sprint-1211", min_tier_level=20)
        _create_sprint("Premium Sprint", "premium-sprint-1211", min_tier_level=30)

        context = _auth_context(browser, "free-sprints@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        active = page.locator('[data-testid="dashboard-active-sprints"]')
        assert "Free Sprint" in active.inner_text()
        assert "Free/open" in active.inner_text()
        assert "Open to Free members" in active.inner_text()
        assert "Main Sprint" not in active.inner_text()
        assert "Premium Sprint" not in active.inner_text()

        active.locator('[data-testid="dashboard-active-sprint"]').first.click()
        page.wait_for_load_state("domcontentloaded")
        assert f"/sprints/{free_sprint.slug}" in page.url


@pytest.mark.django_db(transaction=True)
class TestIssue1211MobileDashboard:
    @pytest.mark.core
    def test_mobile_slack_dismiss_control_is_top_right_and_persists(
        self, django_server, browser, settings,
    ):
        settings.SLACK_INVITE_URL = "https://join.slack.com/mobile-1211"
        _clear_dashboard_data()
        _create_user("main-mobile-slack@test.com", tier_slug="main")

        context = _auth_context(browser, "main-mobile-slack@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        card = page.locator('[data-testid="slack-account-card"]')
        dismiss = page.locator('[data-testid="slack-account-card-dismiss"]')
        card.wait_for(state="visible")
        dismiss.wait_for(state="visible")
        card_box = card.bounding_box()
        dismiss_box = dismiss.bounding_box()
        assert card_box is not None
        assert dismiss_box is not None
        assert dismiss_box["x"] > card_box["x"] + card_box["width"] - 72
        assert dismiss_box["y"] < card_box["y"] + 24
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        _shot(page, "main-mobile-slack")

        dismiss.click()
        card.wait_for(state="detached")
        page.reload(wait_until="domcontentloaded")
        assert page.locator('[data-testid="slack-account-card"]').count() == 0

        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator('[data-testid="slack-account-card-join"]').count() == 1
        assert page.locator('[data-testid="slack-account-card-dismiss"]').count() == 0

    @pytest.mark.core
    def test_mobile_dashboard_links_have_no_overflow_and_navigate(
        self, django_server, browser,
    ):
        _clear_dashboard_data()
        user = _create_user("mobile-dashboard@test.com", tier_slug="main")
        event = _create_event(
            title="Mobile Link Clinic",
            slug="mobile-link-clinic",
            start_datetime=timezone.now() + datetime.timedelta(days=2),
        )
        _register_user_for_event(user, event)
        _create_article(
            title="Mobile Dashboard Article",
            slug="mobile-dashboard-article",
            description="A recent item for the compact mobile dashboard.",
            required_level=0,
        )

        context = _auth_context(browser, "mobile-dashboard@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        for selector in [
            'a:has-text("View all events")',
            '[data-testid="dashboard-quick-actions"] a:has-text("Browse courses")',
        ]:
            box = page.locator(selector).first.bounding_box()
            assert box is not None
            assert box["height"] >= 44
        _shot(page, "mobile-dashboard-links")

        page.get_by_role("link", name="View all events").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/events")

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator(
            '[data-testid="dashboard-quick-actions"] a:has-text("Browse courses")'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/courses")
# -------------------------------------------------------------------
# Scenario 7: Free member discovers gated content in recent content
#              and finds the upgrade path
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7GatedContentInRecentContent:
    """Free member discovers gated content in recent content and
    finds the upgrade path."""

    def test_recent_content_excludes_gated_articles(
        self, django_server
    , browser):
        """Given: A user logged in as free@test.com (Free tier), and
        3 open articles (level 0) plus 2 gated articles (level 10
        Basic) exist.
        1. Navigate to /
        Then: The 'Recent content' section shows only the 3 open
              articles the user can access (gated articles are
              excluded).
        2. Click on one of the articles in the recent content list.
        Then: User navigates to the article detail page at
              /blog/{slug} and can read the full content.
        3. Navigate to /blog to browse all articles.
        Then: The gated articles show a lock icon indicating they
              require a higher tier.
        4. Click on a gated article.
        Then: The article shows a teaser with 'Upgrade to Basic to
              read this article' and a link to /pricing."""
        _clear_dashboard_data()
        _create_user("free@test.com", tier_slug="free")

        # Create 3 open articles
        _create_article(
            title="Getting Started with Python",
            slug="getting-started-python",
            description="A beginner guide to Python.",
            required_level=0,
            date=datetime.date(2026, 2, 25),
        )
        _create_article(
            title="Intro to Machine Learning",
            slug="intro-ml",
            description="Machine learning basics.",
            required_level=0,
            date=datetime.date(2026, 2, 24),
        )
        _create_article(
            title="Data Cleaning Tips",
            slug="data-cleaning-tips",
            description="Tips for cleaning datasets.",
            required_level=0,
            date=datetime.date(2026, 2, 23),
        )

        # Create 2 gated articles (Basic)
        _create_article(
            title="Advanced RAG Techniques",
            slug="advanced-rag-techniques",
            description="Deep dive into RAG patterns.",
            required_level=10,
            date=datetime.date(2026, 2, 26),
        )
        _create_article(
            title="Fine-tuning LLMs Guide",
            slug="fine-tuning-llms",
            description="How to fine-tune language models.",
            required_level=10,
            date=datetime.date(2026, 2, 22),
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        page.content()

        # Then: Recent content shows only open articles
        # The dashboard "Recent content" section filters by
        # user level
        recent_section = page.locator(
            'section:has(h2:has-text("Recent content"))'
        )
        recent_text = recent_section.inner_text()
        assert "Getting Started with Python" in recent_text
        assert "Intro to Machine Learning" in recent_text
        assert "Data Cleaning Tips" in recent_text

        # Gated articles NOT in Recent content
        assert "Advanced RAG Techniques" not in recent_text
        assert "Fine-tuning LLMs Guide" not in recent_text

        # Step 2: Click an open article
        page.locator(
            'a[href="/blog/getting-started-python"]'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigate to article detail
        assert "/blog/getting-started-python" in page.url
        article_body = page.content()
        assert "Getting Started with Python" in article_body

        # Step 3: Navigate to /blog
        page.goto(
            f"{django_server}/blog",
            wait_until="domcontentloaded",
        )
        page.content()

        # Gated articles have lock icons
        gated_card = page.locator(
            'article:has-text("Advanced RAG Techniques")'
        )
        lock_icon = gated_card.locator(
            '[data-lucide="lock"]'
        )
        assert lock_icon.count() >= 1

        # Step 4: Click on a gated article
        page.locator(
            'h2:has-text("Advanced RAG Techniques")'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Paywall message shown
        gated_body = page.content()
        assert "Upgrade to Basic to read this article" in gated_body
        assert "/pricing" in gated_body
# -------------------------------------------------------------------
# Scenario 8: Premium member sees active polls and navigates to vote
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8PremiumMemberSeesActivePolls:
    """Premium member sees active polls and navigates to vote."""

    def test_active_polls_shown_with_vote_counts(
        self, django_server
    , browser):
        """Given: A user logged in as premium@test.com (Premium tier),
        and 2 open polls exist (1 topic poll at Main level, 1 course
        poll at Premium level).
        1. Navigate to /
        Then: The 'Active polls' section shows both polls with their
              titles, vote counts, and option counts.
        2. Click on the course poll.
        Then: User navigates to /vote/{uuid} where they can see
              options, cast votes, and propose new options."""
        _clear_dashboard_data()
        _create_user(
            "premium@test.com", tier_slug="premium"
        )
        _create_article(
            title="Premium Agent Patterns",
            slug="premium-agent-patterns",
            description="Reusable agent patterns for production work.",
            required_level=30,
            date=datetime.date(2026, 2, 27),
        )

        # Create a topic poll (required_level auto-set to 20)
        topic_poll = _create_poll(
            title="Next Workshop Topic",
            poll_type="topic",
            description="Vote for the next workshop!",
        )
        _create_poll_option(topic_poll, "LangChain Deep Dive")
        _create_poll_option(topic_poll, "RAG Pipelines")

        # Create a course poll (required_level auto-set to 30)
        course_poll = _create_poll(
            title="Next Mini-Course",
            poll_type="course",
            description="Vote for the next mini-course!",
            allow_proposals=True,
        )
        _create_poll_option(course_poll, "AI Agents 101")
        _create_poll_option(course_poll, "MLOps Basics")
        _create_poll_option(course_poll, "Computer Vision")

        context = _auth_context(
            browser, "premium@test.com"
        )
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Active polls section shows both polls
        assert "Active polls" in body
        assert "Recent content" in body
        assert "Next Workshop Topic" in body
        assert "Next Mini-Course" in body
        assert "Premium Agent Patterns" in body
        assert page.locator(
            'section:has(h2:has-text("Recent content")) [data-lucide="newspaper"]'
        ).count() == 1
        assert page.locator(
            'section:has(h2:has-text("Recent content")) [data-lucide="sparkles"]'
        ).count() == 0
        _shot(page, "premium-dashboard-desktop")

        # Vote and option counts are displayed
        polls_section = page.locator(
            'section:has(h2:has-text("Active polls"))'
        )
        polls_text = polls_section.inner_text()
        assert "vote" in polls_text.lower()
        assert "option" in polls_text.lower()

        # Step 2: Click on the course poll
        poll_link = page.locator(
            f'a[href="/vote/{course_poll.id}"]'
        )
        assert poll_link.count() >= 1
        poll_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to the poll detail page
        assert f"/vote/{course_poll.id}" in page.url
        poll_body = page.content()
        assert "Next Mini-Course" in poll_body
        assert "AI Agents 101" in poll_body
        assert "MLOps Basics" in poll_body
        assert "Computer Vision" in poll_body
# -------------------------------------------------------------------
# Scenario 9: Dashboard does not duplicate the notification surface
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9DashboardNotificationsSurfaceConsolidated:
    """Dashboard omits the duplicate notification card."""

    @pytest.mark.core
    def test_notifications_card_removed_bell_kept(
        self, django_server
    , browser):
        """Given: A user logged in as basic@test.com (Basic tier) who
        has 3 unread notifications.
        1. Navigate to /
        Then: The dashboard body does not contain a Notifications card or
              duplicate notification titles.
        Then: The header bell remains visible and links to the full archive
              through its dropdown."""
        _clear_dashboard_data()
        user = _create_user(
            "basic@test.com", tier_slug="basic"
        )

        # Create 3 unread notifications
        _create_notification(
            user=user,
            title="New article: Advanced Deployment",
            body="A new article has been published.",
            url="/blog/advanced-deployment",
        )
        _create_notification(
            user=user,
            title="New recording: AI Workshop",
            body="A workshop recording is now available.",
            url="/events/ai-workshop",
        )
        _create_notification(
            user=user,
            title="Event reminder: Hackathon",
            body="The hackathon starts tomorrow.",
            url="/events/hackathon",
        )

        context = _auth_context(browser, "basic@test.com")
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.locator("main").inner_text()

        # Then: The dashboard body does not duplicate notification content.
        assert "New article: Advanced Deployment" not in body
        assert "New recording: AI Workshop" not in body
        assert "Event reminder: Hackathon" not in body
        assert "No new notifications" not in body
        assert page.locator(
            'main section:has(h2:has-text("Notifications"))'
        ).count() == 0
        assert page.locator(
            'main section:has-text("Quick actions")'
        ).count() >= 1

        # Then: The header bell remains the canonical quick-peek entry point.
        page.locator("#notification-bell-btn").click()
        page.locator("#notification-dropdown").wait_for(state="visible")
        dropdown = page.locator("#notification-dropdown")
        dropdown.locator(
            'a:has-text("New article: Advanced Deployment")'
        ).wait_for(state="visible")
        assert "New article: Advanced Deployment" in dropdown.inner_text()
        view_all_link = dropdown.locator('a:has-text("View all")')
        view_all_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to /notifications
        assert "/notifications" in page.url
@pytest.mark.django_db(transaction=True)
class TestScenario10CompletedUnitsWithoutEnrollment:
    """Completed units alone do not populate Continue learning."""

    def test_completed_units_without_enrollment_absent(
        self, django_server, browser
    ):
        _clear_dashboard_data()
        user = _create_user("basic-no-enrollment@test.com", tier_slug="basic")

        course = _create_course(
            title="Progress Without Enrollment",
            slug="progress-without-enrollment",
            required_level=0,
        )
        module = _create_module(course, "Module 1", sort_order=0)
        first_unit = _create_unit(module, "First Unit", sort_order=0)
        _create_unit(module, "Second Unit", sort_order=1)

        _mark_unit_completed(user, first_unit)

        context = _auth_context(browser, "basic-no-enrollment@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        learning_section = page.locator(
            'section:has(h2:has-text("Continue learning"))'
        )
        learning_text = learning_section.inner_text()
        assert "Progress Without Enrollment" not in learning_text
        assert "Browse courses" in learning_text
# -------------------------------------------------------------------
# Scenario 11: Removed welcome-card upgrade link
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11RemovedWelcomeUpgradeLink:
    """Dashboard no longer renders the old welcome-card Upgrade link."""

    @pytest.mark.core
    def test_dashboard_omits_removed_upgrade_card(
        self, django_server
    , browser):
        """Given: A user logged in as free@test.com (Free tier).
        1. Navigate to /
        Then: The old welcome-card Upgrade CTA is absent."""
        _clear_dashboard_data()
        _create_user("free@test.com", tier_slug="free")

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        assert "Welcome back" in body
        assert page.locator('[data-testid="dashboard-tier-pill"]').inner_text() == "Free"
        assert "Upgrade" not in body
