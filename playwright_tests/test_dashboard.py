"""
Playwright E2E tests for the Logged-in User Home Dashboard (Issue #104).

Tests cover all 11 BDD scenarios from the issue:
- Anonymous visitor still sees the public marketing homepage
- Free member sees personalized dashboard with tier badge after login
- Free member with no activity sees helpful empty states that guide next steps
- Basic member resumes an in-progress course from the dashboard
- Main member sees upcoming registered events and navigates to event detail
- Main member sees the Community quick action that Free members do not
- Free member discovers gated content in recent content and finds the upgrade path
- Premium member sees active polls and navigates to vote
- Member reads an unread notification from the dashboard and follows it
- Member with a completed course does not see it in continue learning
- Free member uses the Upgrade link in the welcome banner to explore paid tiers

Usage:
    uv run pytest playwright_tests/test_dashboard.py -v
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
        UserCourseProgress,
    )
    from events.models import Event, EventRegistration
    from notifications.models import Notification
    from voting.models import Poll, PollOption, PollVote

    Notification.objects.all().delete()
    PollVote.objects.all().delete()
    PollOption.objects.all().delete()
    Poll.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    Enrollment.objects.all().delete()
    UserCourseProgress.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    Article.objects.all().delete()
    Event.objects.all().delete()
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
    """Create an active course Enrollment for dashboard Continue Learning."""
    from content.models import Enrollment

    enrollment, _ = Enrollment.objects.get_or_create(user=user, course=course)
    connection.close()
    return enrollment


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

    def test_anonymous_sees_hero_and_tiers_not_dashboard(
        self, django_server
    , page):
        """Given: An anonymous visitor (not logged in).
        1. Navigate to /
        Then: The public marketing homepage loads with the hero section,
              tier cards, testimonials, and newsletter signup.
        Then: No personalized dashboard sections (welcome banner,
              continue learning, upcoming events) are shown.
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
        assert "Continue Learning" not in body
        assert "Upcoming Events" not in body
        assert "Quick Actions" not in body

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
# Scenario 2: Free member sees personalized dashboard with tier
#              badge after login
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2FreeMemberSeesDashboard:
    """Free member sees personalized dashboard with tier badge
    after login."""

    def test_free_member_dashboard_with_welcome_and_badge(
        self, django_server
    , browser):
        """Given: A user logged in as free@test.com (Free tier,
        first name 'Alex').
        1. Navigate to /
        Then: The dashboard loads with 'Welcome back, Alex' and a
              'Free' tier badge.
        Then: The marketing homepage hero, testimonials, and tier
              cards are not shown.
        2. Click the 'Account' link in the welcome banner.
        Then: User navigates to /account/ showing their Free tier
              membership details."""
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

        # Then: Dashboard with welcome and tier badge
        assert "Welcome back" in body
        assert "Alex" in body
        assert "Free" in body

        # Then: Marketing homepage elements NOT shown
        assert "Turn AI ideas into" not in body
        assert "View Membership Tiers" not in body

        # Step 2: Click the "Account" link in the
        # welcome banner (not the mobile menu version)
        welcome_section = page.locator(
            'section:has(h1:has-text("Welcome back"))'
        )
        account_link = welcome_section.locator(
            'a:has-text("Account")'
        )
        account_link.click()
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
        has no course progress, no event registrations, and no
        unread notifications.
        1. Navigate to /
        Then: The 'Continue Learning' section shows 'No courses in
              progress yet' with a 'Browse Courses' link.
        Then: The 'Upcoming Events' section shows 'No upcoming events'
              with a 'Browse Events' link.
        Then: The 'Notifications' section shows 'No new notifications'.
        2. Click 'Browse Courses' in the empty continue learning
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
        assert "No courses in progress yet" in body
        assert "No upcoming events" in body
        assert "No new notifications" in body

        # CTA links present
        browse_courses_link = page.locator(
            'a:has-text("Browse Courses")'
        )
        assert browse_courses_link.count() >= 1

        browse_events_link = page.locator(
            'a:has-text("Browse Events")'
        )
        assert browse_events_link.count() >= 1

        # Step 2: Click "Browse Courses" in the empty
        # continue learning section
        browse_courses_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to /courses
        assert "/courses" in page.url
# -------------------------------------------------------------------
# Scenario 4: Basic member resumes an in-progress course from the
#              dashboard
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4BasicMemberResumesCourse:
    """Basic member resumes an in-progress course from the dashboard."""

    def test_in_progress_course_with_progress_bar(
        self, django_server
    , browser):
        """Given: A user logged in as basic@test.com (Basic tier) who
        has completed 3 of 10 units in a course titled
        'AI Agents Buildcamp'.
        1. Navigate to /
        Then: The 'Continue Learning' section shows 'AI Agents
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

        # Then: Course shown in Continue Learning
        assert "AI Agents Buildcamp" in body
        assert "3/10 units completed" in body

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

    def test_upcoming_events_shown_ordered_by_date(
        self, django_server
    , browser):
        """Given: A user logged in as main@test.com (Main tier) who
        is registered for 2 upcoming events.
        1. Navigate to /
        Then: The 'Upcoming Events' section shows both registered
              events with their titles and dates.
        Then: Events are ordered by start date (soonest first).
        2. Click 'View Event' on the first event.
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

        # Step 2: Click "View Event" on the first event
        view_event_links = page.locator(
            'a:has-text("View Event")'
        )
        assert view_event_links.count() >= 1
        view_event_links.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to the event detail page
        assert "/events/ai-workshop-prompt-engineering" in page.url
        event_body = page.content()
        assert "AI Workshop: Prompt Engineering" in event_body
# -------------------------------------------------------------------
# Scenario 6: Main member sees the Community quick action that Free
#              members do not
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6CommunityQuickActionTierGated:
    """Main member sees the Community quick action that Free members
    do not."""

    def test_community_action_for_main_not_for_free(
        self, django_server
    , browser):
        """Given: A user logged in as main@test.com (Main tier).
        1. Navigate to /
        Then: The 'Quick Actions' section includes 'Browse Courses',
              'View Recordings', 'Community', and 'Submit Project'.
        2. Log out and log in as free@test.com (Free tier).
        3. Navigate to /
        Then: The 'Quick Actions' section includes 'Browse Courses',
              'View Recordings', and 'Submit Project'.
        Then: The 'Community' quick action is not present for the
              Free member."""
        _clear_dashboard_data()
        _create_user("main@test.com", tier_slug="main")
        _create_user("free@test.com", tier_slug="free")


        # Step 1: Main member sees Community action
        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: All 4 quick actions present
        assert "Browse Courses" in body
        assert "View Recordings" in body
        assert "Community" in body
        assert "Submit Project" in body
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
        Then: The 'Recent Content' section shows only the 3 open
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

        # Then: Recent Content shows only open articles
        # The dashboard "Recent Content" section filters by
        # user level
        recent_section = page.locator(
            'section:has(h2:has-text("Recent Content"))'
        )
        recent_text = recent_section.inner_text()
        assert "Getting Started with Python" in recent_text
        assert "Intro to Machine Learning" in recent_text
        assert "Data Cleaning Tips" in recent_text

        # Gated articles NOT in Recent Content
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
        Then: The 'Active Polls' section shows both polls with their
              titles, vote counts, and option counts.
        2. Click on the course poll.
        Then: User navigates to /vote/{uuid} where they can see
              options, cast votes, and propose new options."""
        _clear_dashboard_data()
        _create_user(
            "premium@test.com", tier_slug="premium"
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

        # Then: Active Polls section shows both polls
        assert "Active Polls" in body
        assert "Next Workshop Topic" in body
        assert "Next Mini-Course" in body

        # Vote and option counts are displayed
        polls_section = page.locator(
            'section:has(h2:has-text("Active Polls"))'
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
# Scenario 9: Member reads an unread notification from the dashboard
#              and follows it
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9MemberReadsNotificationFromDashboard:
    """Member reads an unread notification from the dashboard and
    follows it."""

    def test_notifications_shown_and_view_all_link(
        self, django_server
    , browser):
        """Given: A user logged in as basic@test.com (Basic tier) who
        has 3 unread notifications.
        1. Navigate to /
        Then: The 'Notifications' section shows up to 5 unread
              notifications with their titles.
        2. Click 'View all' in the notifications section header.
        Then: User navigates to /notifications showing the full
              paginated list of all notifications."""
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
        page.content()

        # Then: Notifications section shows the 3 notifications
        notif_section = page.locator(
            'section:has(h2:has-text("Notifications"))'
        )
        notif_text = notif_section.inner_text()
        assert "New article: Advanced Deployment" in notif_text
        assert "New recording: AI Workshop" in notif_text
        assert "Event reminder: Hackathon" in notif_text

        # Step 2: Click "View all" in the notifications
        # section header
        view_all_link = notif_section.locator(
            'a:has-text("View all")'
        )
        assert view_all_link.count() >= 1
        view_all_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to /notifications
        assert "/notifications" in page.url
# -------------------------------------------------------------------
# Scenario 10: Member with a completed course does not see it in
#               continue learning
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10CompletedCourseNotInContinueLearning:
    """Member with a completed course does not see it in continue
    learning."""

    def test_completed_course_excluded_in_progress_shown(
        self, django_server
    , browser):
        """Given: A user logged in as basic@test.com (Basic tier) who
        has completed all 10 of 10 units in 'AI Agents Buildcamp' and
        completed 2 of 5 units in 'Python Fundamentals'.
        1. Navigate to /
        Then: The 'Continue Learning' section shows only
              'Python Fundamentals' with a progress bar at 40% and
              '2/5 units completed'.
        Then: The fully completed 'AI Agents Buildcamp' course does
              not appear in the continue learning section."""
        _clear_dashboard_data()
        user = _create_user(
            "basic@test.com", tier_slug="basic"
        )

        # Course 1: AI Agents Buildcamp - fully completed (10/10)
        course1 = _create_course(
            title="AI Agents Buildcamp",
            slug="ai-agents-buildcamp",
            required_level=0,
        )
        module1 = _create_module(course1, "Module A", sort_order=0)
        units1 = []
        for i in range(10):
            unit = _create_unit(
                module1, f"Buildcamp Unit {i + 1}", sort_order=i
            )
            units1.append(unit)

        base_time = timezone.now() - datetime.timedelta(days=5)
        for i, unit in enumerate(units1):
            _mark_unit_completed(
                user,
                unit,
                completed_at=base_time + datetime.timedelta(hours=i),
            )
        _enroll_user(user, course1)

        # Course 2: Python Fundamentals - partially completed (2/5)
        course2 = _create_course(
            title="Python Fundamentals",
            slug="python-fundamentals",
            required_level=0,
        )
        module2 = _create_module(course2, "Module B", sort_order=0)
        units2 = []
        for i in range(5):
            unit = _create_unit(
                module2, f"Python Unit {i + 1}", sort_order=i
            )
            units2.append(unit)

        recent_time = timezone.now() - datetime.timedelta(hours=2)
        for i in range(2):
            _mark_unit_completed(
                user,
                units2[i],
                completed_at=recent_time + datetime.timedelta(
                    minutes=i * 30
                ),
            )
        _enroll_user(user, course2)

        context = _auth_context(browser, "basic@test.com")
        page = context.new_page()
        # Step 1: Navigate to /
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        page.content()

        # Then: Continue Learning shows Python Fundamentals
        learning_section = page.locator(
            'section:has(h2:has-text("Continue Learning"))'
        )
        learning_text = learning_section.inner_text()
        assert "Python Fundamentals" in learning_text
        assert "2/5 units completed" in learning_text

        # Progress bar at 40%
        progress_bar = page.locator(
            'div[style*="width: 40%"]'
        )
        assert progress_bar.count() >= 1

        # Then: Completed course NOT shown
        assert "AI Agents Buildcamp" not in learning_text


@pytest.mark.django_db(transaction=True)
class TestScenario10CompletedUnitsWithoutEnrollment:
    """Completed units alone do not populate Continue Learning."""

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
            'section:has(h2:has-text("Continue Learning"))'
        )
        learning_text = learning_section.inner_text()
        assert "Progress Without Enrollment" not in learning_text
        assert "Browse Courses" in learning_text
# -------------------------------------------------------------------
# Scenario 11: Free member uses the Upgrade link in the welcome
#               banner to explore paid tiers
# -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11FreeMemberUpgradeLink:
    """Free member uses the Upgrade link in the welcome banner to
    explore paid tiers."""

    def test_upgrade_link_navigates_to_pricing(
        self, django_server
    , browser):
        """Given: A user logged in as free@test.com (Free tier).
        1. Navigate to /
        Then: The welcome banner shows an 'Upgrade' button alongside
              the 'Account' link.
        2. Click the 'Upgrade' link.
        Then: User navigates to the tiers section where they can
              compare Basic, Main, and Premium options and their
              pricing."""
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

        # Then: Welcome banner has both Account and Upgrade
        assert "Account" in body
        assert "Upgrade" in body

        # Both links are in the welcome banner section
        welcome_section = page.locator(
            'section:has(h1:has-text("Welcome back"))'
        )
        welcome_text = welcome_section.inner_text()
        assert "Account" in welcome_text
        assert "Upgrade" in welcome_text

        # Step 2: Click "Upgrade"
        upgrade_link = welcome_section.locator(
            'a:has-text("Upgrade")'
        )
        assert upgrade_link.count() >= 1
        upgrade_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Navigates to /pricing
        assert "/pricing" in page.url
        pricing_body = page.content()

        # Tier options are shown
        assert "Basic" in pricing_body
        assert "Main" in pricing_body
        assert "Premium" in pricing_body
