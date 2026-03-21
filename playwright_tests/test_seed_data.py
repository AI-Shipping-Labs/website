"""
Playwright E2E tests for the test data seeding command (Issue #103).

Tests cover all 12 BDD scenarios from the issue:
- Developer seeds an empty database and gets all content types populated
- Developer verifies users across all four tiers are created with predictable emails
- Developer verifies seeded articles span multiple access levels
- Developer verifies courses are created with modules, units, and nested structure
- Developer verifies events include a mix of statuses and have registrations
- Developer verifies recordings are linked to past events
- Developer runs seed_data twice and confirms idempotency (no duplicates)
- Developer uses --flush to clear and re-seed all data from scratch
- Developer verifies polls are created with options and votes from eligible users
- Developer verifies supplementary content: curated links, downloads, projects, notifications, and subscribers
- Developer seeds data and then browses the site as an admin to verify content appears
- Developer verifies cohort enrollments are created for gated courses

Usage:
    uv run pytest playwright_tests/test_seed_data.py -v
"""

import io
import os
import re

import pytest
from django.core.management import call_command

from playwright_tests.conftest import (
    DJANGO_BASE_URL,
    VIEWPORT,
    ensure_tiers as _ensure_tiers,
    create_session_for_user as _create_session_for_user,
    auth_context as _auth_context,
)


os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
ADMIN_EMAIL = "admin@aishippinglabs.com"
ADMIN_PASSWORD = "admin123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flush_all_seed_data():
    """Remove all data that the seed_data command creates."""
    from django.contrib.auth import get_user_model
    from content.models import (
        Article, Course, Module, Unit, Cohort, CohortEnrollment,
        Recording, Project, CuratedLink, Download,
    )
    from email_app.models import NewsletterSubscriber
    from events.models import Event, EventRegistration
    from notifications.models import Notification
    from voting.models import Poll, PollOption, PollVote

    User = get_user_model()

    PollVote.objects.all().delete()
    PollOption.objects.all().delete()
    Poll.objects.all().delete()
    Notification.objects.all().delete()
    EventRegistration.objects.all().delete()
    CohortEnrollment.objects.all().delete()
    Cohort.objects.all().delete()
    Recording.objects.all().delete()
    Event.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    Article.objects.all().delete()
    Project.objects.all().delete()
    CuratedLink.objects.filter(item_id__startswith="seed-").delete()
    Download.objects.all().delete()
    NewsletterSubscriber.objects.all().delete()

    # Delete only the seeded users.
    # NOTE: Tier uses on_delete=PROTECT on User.tier, so we must delete
    # users before tiers. However, other test infrastructure may depend
    # on tiers, so we do NOT delete tiers here.
    seeded_emails = [
        "admin@aishippinglabs.com", "free@test.com", "basic@test.com",
        "main@test.com", "premium@test.com", "alice@test.com",
        "charlie@test.com", "diana@test.com",
    ]
    User.objects.filter(email__in=seeded_emails).delete()


def _run_seed_data(flush=False):
    """Run the seed_data management command and capture output."""
    out = io.StringIO()
    args = []
    if flush:
        args.append("--flush")
    call_command("seed_data", *args, stdout=out)
    return out.getvalue()


def _parse_summary(output):
    """Parse the summary section from seed_data output into a dict of label -> count."""
    summary = {}
    for line in output.splitlines():
        line = line.strip()
        # Match lines like "  Tiers: 4"
        match = re.match(r"(.+?):\s+(\d+)", line)
        if match:
            label = match.group(1).strip().lower()
            count = int(match.group(2))
            summary[label] = count
    return summary




def _login_admin_via_browser(page, base_url, email, password=ADMIN_PASSWORD):
    """Log in an admin user via the Django admin login page."""
    page.goto(f"{base_url}/admin/login/", wait_until="domcontentloaded")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("domcontentloaded")


# ---------------------------------------------------------------------------
# Scenario 1: Developer seeds an empty database and gets all content
#              types populated
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario1SeedEmptyDatabase:
    """Developer seeds an empty database and gets all content types populated.

    Given: A developer with an empty database (migrations applied, no data)
    1. Run python manage.py seed_data
    Then: The command exits with status code 0
    Then: The output includes "Seed data created successfully."
    Then: The summary lists counts for all 13 categories
    Then: Every count in the summary is greater than 0
    """

    def test_seed_data_populates_all_content_types(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()

        output = _run_seed_data()

        # Then: The output includes the success message
        assert "Seed data created successfully." in output

        # Then: The summary lists counts for all 13 categories
        summary = _parse_summary(output)

        expected_categories = [
            "tiers", "users", "articles", "courses", "cohorts",
            "events", "recordings", "projects", "curated links",
            "downloads", "polls", "notifications",
            "newsletter subscribers",
        ]
        for category in expected_categories:
            assert category in summary, (
                f"Category '{category}' not found in summary. "
                f"Available: {list(summary.keys())}"
            )

        # Then: Every count in the summary is greater than 0.
        # Tiers may already exist from the test database setup
        # (conftest or migrations), so they may report 0 created.
        # We verify tiers exist separately.
        for category in expected_categories:
            if category == "tiers":
                # Tiers may have been pre-created; verify they exist
                from payments.models import Tier
                assert Tier.objects.count() >= 4, (
                    "Expected at least 4 tiers to exist"
                )
                continue
            assert summary[category] > 0, (
                f"Expected count > 0 for '{category}', got {summary[category]}"
            )


# ---------------------------------------------------------------------------
# Scenario 2: Developer verifies users across all four tiers are created
#              with predictable emails
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario2UsersAcrossAllTiers:
    """Developer verifies users across all four tiers are created with
    predictable emails.

    Given: A developer with an empty database
    1. Run python manage.py seed_data
    Then: A superuser exists with email admin@aishippinglabs.com
    Then: Users exist at free, basic, main, premium tiers
    Then: The admin user can log in with password admin123
    """

    def test_users_created_with_correct_tiers(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Then: A superuser exists with expected properties
        admin = User.objects.get(email="admin@aishippinglabs.com")
        assert admin.is_staff is True
        assert admin.is_superuser is True

        # Then: Users exist at each tier with predictable emails
        free_user = User.objects.get(email="free@test.com")
        assert free_user.tier.level == 0

        basic_user = User.objects.get(email="basic@test.com")
        assert basic_user.tier.level == 10

        main_user = User.objects.get(email="main@test.com")
        assert main_user.tier.level == 20

        premium_user = User.objects.get(email="premium@test.com")
        assert premium_user.tier.level == 30

        # Then: The admin user can log in with password admin123
        assert admin.check_password("admin123")


# ---------------------------------------------------------------------------
# Scenario 3: Developer verifies seeded articles span multiple access levels
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario3ArticlesSpanAccessLevels:
    """Developer verifies seeded articles span multiple access levels.

    Given: A developer has run seed_data on an empty database
    1. Query the Article table
    Then: At least 5 published articles exist
    Then: At least one article has required_level=0
    Then: At least one article has required_level > 0
    Then: Every article has a non-empty title, description,
          content_markdown, author, and tags
    """

    def test_articles_have_varied_access_levels(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        from content.models import Article

        published_articles = Article.objects.filter(published=True)

        # Then: At least 5 published articles exist
        assert published_articles.count() >= 5

        # Then: At least one article has required_level=0
        open_articles = published_articles.filter(required_level=0)
        assert open_articles.count() >= 1, (
            "Expected at least one open article (required_level=0)"
        )

        # Then: At least one article has required_level > 0
        gated_articles = published_articles.filter(required_level__gt=0)
        assert gated_articles.count() >= 1, (
            "Expected at least one gated article (required_level > 0)"
        )

        # Then: Every article has non-empty title, description,
        #       content_markdown, author, and tags
        for article in published_articles:
            assert article.title, f"Article {article.slug} has empty title"
            assert article.description, (
                f"Article {article.slug} has empty description"
            )
            assert article.content_markdown, (
                f"Article {article.slug} has empty content_markdown"
            )
            assert article.author, (
                f"Article {article.slug} has empty author"
            )
            assert article.tags, (
                f"Article {article.slug} has no tags"
            )


# ---------------------------------------------------------------------------
# Scenario 4: Developer verifies courses are created with modules, units,
#              and nested structure
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario4CoursesWithNestedStructure:
    """Developer verifies courses are created with modules, units, and
    nested structure.

    Given: A developer has run seed_data on an empty database
    1. Query the Course, Module, and Unit tables
    Then: At least 2 courses exist with status "published"
    Then: Each course has at least 1 module
    Then: Each module has at least 1 unit
    Then: At least one course has required_level=0 (free course)
    Then: At least one course has required_level > 0 (gated course)
    Then: At least one unit has a video_url set
    """

    def test_courses_have_modules_and_units(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        from content.models import Course, Module, Unit

        published_courses = Course.objects.filter(status="published")

        # Then: At least 2 courses exist with status "published"
        assert published_courses.count() >= 2

        # Then: Each course has at least 1 module
        for course in published_courses:
            modules = Module.objects.filter(course=course)
            assert modules.count() >= 1, (
                f"Course '{course.title}' has no modules"
            )

            # Then: Each module has at least 1 unit
            for module in modules:
                units = Unit.objects.filter(module=module)
                assert units.count() >= 1, (
                    f"Module '{module.title}' in course "
                    f"'{course.title}' has no units"
                )

        # Then: At least one course has required_level=0
        free_courses = published_courses.filter(required_level=0)
        assert free_courses.count() >= 1, (
            "Expected at least one free course (required_level=0)"
        )

        # Then: At least one course has required_level > 0
        gated_courses = published_courses.filter(required_level__gt=0)
        assert gated_courses.count() >= 1, (
            "Expected at least one gated course (required_level > 0)"
        )

        # Then: At least one unit has a video_url set
        units_with_video = Unit.objects.exclude(video_url="").exclude(
            video_url__isnull=True
        )
        assert units_with_video.count() >= 1, (
            "Expected at least one unit with a video_url"
        )


# ---------------------------------------------------------------------------
# Scenario 5: Developer verifies events include a mix of statuses and
#              have registrations
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario5EventsMixedStatuses:
    """Developer verifies events include a mix of statuses and have
    registrations.

    Given: A developer has run seed_data on an empty database
    1. Query the Event and EventRegistration tables
    Then: At least 3 events exist
    Then: At least one event has status "upcoming"
    Then: At least one event has status "completed"
    Then: At least one upcoming or live event has registrations
    """

    def test_events_have_mixed_statuses_and_registrations(
        self, django_server
    ):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        from events.models import Event, EventRegistration

        all_events = Event.objects.all()

        # Then: At least 3 events exist
        assert all_events.count() >= 3

        # Then: At least one event has status "upcoming"
        upcoming = all_events.filter(status="upcoming")
        assert upcoming.count() >= 1, (
            "Expected at least one upcoming event"
        )

        # Then: At least one event has status "completed"
        completed = all_events.filter(status="completed")
        assert completed.count() >= 1, (
            "Expected at least one completed event"
        )

        # Then: At least one upcoming or live event has registrations
        active_events = all_events.filter(
            status__in=["upcoming", "live"]
        )
        has_registrations = False
        for event in active_events:
            if EventRegistration.objects.filter(event=event).exists():
                has_registrations = True
                break
        assert has_registrations, (
            "Expected at least one upcoming or live event with registrations"
        )


# ---------------------------------------------------------------------------
# Scenario 6: Developer verifies recordings are linked to past events
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario6RecordingsLinkedToEvents:
    """Developer verifies recordings are linked to past events.

    Given: A developer has run seed_data on an empty database
    1. Query the Recording table
    Then: At least 4 recordings exist with published=True
    Then: At least one recording has a non-null event foreign key
          linking it to a completed event
    Then: At least one recording has timestamps data (non-empty list)
    """

    def test_recordings_linked_and_have_timestamps(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        from content.models import Recording

        published_recordings = Recording.objects.filter(published=True)

        # Then: At least 4 recordings exist with published=True
        assert published_recordings.count() >= 4

        # Then: At least one recording has a non-null event FK
        #       linking to a completed event
        recordings_with_event = published_recordings.filter(
            event__isnull=False
        )
        assert recordings_with_event.count() >= 1, (
            "Expected at least one recording linked to an event"
        )

        linked_to_completed = recordings_with_event.filter(
            event__status="completed"
        )
        assert linked_to_completed.count() >= 1, (
            "Expected at least one recording linked to a completed event"
        )

        # Then: At least one recording has timestamps data (non-empty list)
        has_timestamps = False
        for rec in published_recordings:
            if rec.timestamps and len(rec.timestamps) > 0:
                has_timestamps = True
                break
        assert has_timestamps, (
            "Expected at least one recording with non-empty timestamps"
        )


# ---------------------------------------------------------------------------
# Scenario 7: Developer runs seed_data twice and confirms idempotency
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario7Idempotency:
    """Developer runs seed_data twice and confirms idempotency (no duplicates).

    Given: A developer has already run seed_data once on an empty database
    1. Run python manage.py seed_data a second time
    Then: The command exits with status code 0
    Then: The summary shows 0 for every category
    Then: Total counts have not changed from the first run
    """

    def test_second_run_creates_no_duplicates(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()

        from django.contrib.auth import get_user_model
        from content.models import (
            Article, Course, Recording, Project, CuratedLink, Download,
        )
        from events.models import Event
        from email_app.models import NewsletterSubscriber
        from voting.models import Poll

        User = get_user_model()

        # First run
        output1 = _run_seed_data()
        summary1 = _parse_summary(output1)

        # Capture counts after first run
        counts_after_first = {
            "users": User.objects.count(),
            "articles": Article.objects.count(),
            "courses": Course.objects.count(),
            "events": Event.objects.count(),
            "recordings": Recording.objects.count(),
            "projects": Project.objects.count(),
            "curated_links": CuratedLink.objects.count(),
            "downloads": Download.objects.count(),
            "polls": Poll.objects.count(),
            "newsletter_subscribers": NewsletterSubscriber.objects.count(),
        }

        # Second run
        output2 = _run_seed_data()
        summary2 = _parse_summary(output2)

        # Then: The command exits successfully (no exception was raised)
        assert "Seed data created successfully." in output2

        # Then: The summary shows 0 for every category
        for category, count in summary2.items():
            assert count == 0, (
                f"Expected 0 for '{category}' on second run, got {count}"
            )

        # Then: Total counts have not changed from the first run
        counts_after_second = {
            "users": User.objects.count(),
            "articles": Article.objects.count(),
            "courses": Course.objects.count(),
            "events": Event.objects.count(),
            "recordings": Recording.objects.count(),
            "projects": Project.objects.count(),
            "curated_links": CuratedLink.objects.count(),
            "downloads": Download.objects.count(),
            "polls": Poll.objects.count(),
            "newsletter_subscribers": NewsletterSubscriber.objects.count(),
        }

        for key in counts_after_first:
            assert counts_after_first[key] == counts_after_second[key], (
                f"Count for '{key}' changed between runs: "
                f"{counts_after_first[key]} -> {counts_after_second[key]}"
            )


# ---------------------------------------------------------------------------
# Scenario 8: Developer uses --flush to clear and re-seed all data
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario8FlushAndReseed:
    """Developer uses --flush to clear and re-seed all data from scratch.

    Given: A developer has already run seed_data once (data exists)
    1. Run python manage.py seed_data --flush
    Then: The output includes "Flushing existing data..."
    Then: The command exits with status code 0
    Then: The summary shows non-zero counts for all 13 categories
    Then: Total counts match a fresh seed run
    """

    def test_flush_clears_and_reseeds(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()

        # First run (normal seed)
        output1 = _run_seed_data()
        summary1 = _parse_summary(output1)

        # Second run with --flush (should delete and recreate)
        output2 = _run_seed_data(flush=True)

        # Then: The output includes "Flushing existing data..."
        assert "Flushing existing data..." in output2

        # Then: The command exits successfully
        assert "Seed data created successfully." in output2

        # Then: The summary shows non-zero counts for all 13 categories.
        # Tiers may already exist (on_delete=PROTECT prevents flush from
        # deleting them if users outside seeded set reference them), so
        # they may report 0 created. We verify they exist instead.
        summary2 = _parse_summary(output2)

        expected_categories = [
            "tiers", "users", "articles", "courses", "cohorts",
            "events", "recordings", "projects", "curated links",
            "downloads", "polls", "notifications",
            "newsletter subscribers",
        ]
        for category in expected_categories:
            assert category in summary2, (
                f"Category '{category}' not found in flush summary"
            )
            if category == "tiers":
                from payments.models import Tier
                assert Tier.objects.count() >= 4, (
                    "Expected at least 4 tiers to exist after flush"
                )
                continue
            assert summary2[category] > 0, (
                f"Expected non-zero count for '{category}' after flush, "
                f"got {summary2[category]}"
            )

        # Then: Total counts match the first fresh run (excluding tiers
        # which may have been pre-created)
        for category in expected_categories:
            if category == "tiers":
                continue
            assert summary1[category] == summary2[category], (
                f"Count mismatch for '{category}': "
                f"fresh={summary1[category]} vs flush={summary2[category]}"
            )


# ---------------------------------------------------------------------------
# Scenario 9: Developer verifies polls are created with options and votes
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario9PollsWithOptionsAndVotes:
    """Developer verifies polls are created with options and votes from
    eligible users.

    Given: A developer has run seed_data on an empty database
    1. Query the Poll, PollOption, and PollVote tables
    Then: At least 1 topic poll and at least 1 course poll exist with
          status "open"
    Then: Each poll has at least 3 options
    Then: At least one poll has votes recorded from seeded users
    """

    def test_polls_have_options_and_votes(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        from voting.models import Poll, PollOption, PollVote

        open_polls = Poll.objects.filter(status="open")

        # Then: At least 1 topic poll with status "open"
        topic_polls = open_polls.filter(poll_type="topic")
        assert topic_polls.count() >= 1, (
            "Expected at least one open topic poll"
        )

        # Then: At least 1 course poll with status "open"
        course_polls = open_polls.filter(poll_type="course")
        assert course_polls.count() >= 1, (
            "Expected at least one open course poll"
        )

        # Then: Each poll has at least 3 options
        for poll in open_polls:
            options_count = PollOption.objects.filter(poll=poll).count()
            assert options_count >= 3, (
                f"Poll '{poll.title}' has only {options_count} options, "
                f"expected at least 3"
            )

        # Then: At least one poll has votes recorded from seeded users
        has_votes = False
        for poll in open_polls:
            vote_count = PollVote.objects.filter(poll=poll).count()
            if vote_count > 0:
                has_votes = True
                break
        assert has_votes, (
            "Expected at least one poll to have votes from seeded users"
        )


# ---------------------------------------------------------------------------
# Scenario 10: Developer verifies supplementary content: curated links,
#               downloads, projects, notifications, and subscribers
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario10SupplementaryContent:
    """Developer verifies supplementary content: curated links, downloads,
    projects, notifications, and subscribers.

    Given: A developer has run seed_data on an empty database
    1. Query CuratedLink, Download, Project, Notification, and
       NewsletterSubscriber tables
    Then: At least 8 curated links spanning multiple categories
    Then: At least 3 downloads with a mix of required_level values
    Then: At least 3 projects with a mix of difficulty levels
    Then: At least 1 notification for a seeded user
    Then: At least 5 confirmed newsletter subscribers
    """

    def test_supplementary_content_populated(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        from content.models import CuratedLink, Download, Project
        from notifications.models import Notification
        from email_app.models import NewsletterSubscriber

        # Then: At least 8 curated links spanning multiple categories
        curated_links = CuratedLink.objects.all()
        assert curated_links.count() >= 8

        categories = set(
            curated_links.values_list("category", flat=True)
        )
        # The seed data has tools, models, courses, other
        assert len(categories) >= 3, (
            f"Expected curated links across at least 3 categories, "
            f"got {categories}"
        )

        # Then: At least 3 downloads with a mix of required_level values
        downloads = Download.objects.all()
        assert downloads.count() >= 3

        download_levels = set(
            downloads.values_list("required_level", flat=True)
        )
        # Some free (0), some gated (>0)
        assert 0 in download_levels, (
            "Expected at least one free download (required_level=0)"
        )
        gated_downloads = downloads.filter(required_level__gt=0)
        assert gated_downloads.count() >= 1, (
            "Expected at least one gated download (required_level > 0)"
        )

        # Then: At least 3 projects with a mix of difficulty levels
        projects = Project.objects.filter(published=True)
        assert projects.count() >= 3

        difficulties = set(
            projects.values_list("difficulty", flat=True)
        )
        assert len(difficulties) >= 2, (
            f"Expected projects across at least 2 difficulty levels, "
            f"got {difficulties}"
        )

        # Then: At least 1 notification for a seeded user
        notifications = Notification.objects.all()
        assert notifications.count() >= 1, (
            "Expected at least 1 notification for a seeded user"
        )

        # Then: At least 5 confirmed newsletter subscribers
        subscribers = NewsletterSubscriber.objects.filter(is_active=True)
        assert subscribers.count() >= 5, (
            f"Expected at least 5 active newsletter subscribers, "
            f"got {subscribers.count()}"
        )


# ---------------------------------------------------------------------------
# Scenario 11: Developer seeds data and then browses the site as an admin
#               to verify content appears
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario11BrowseSiteAsAdmin:
    """Developer seeds data and then browses the site as an admin to
    verify content appears.

    Given: A developer has run seed_data on an empty database
    1. Log in as admin@aishippinglabs.com with password admin123
    2. Navigate to /blog
    Then: Seeded articles appear with realistic titles
    3. Navigate to /courses
    Then: Seeded courses appear
    4. Navigate to /events
    Then: Seeded events appear with a mix of statuses
    5. Navigate to /event-recordings
    Then: Seeded recordings appear
    """

    def test_browse_site_shows_seeded_content(self, django_server, browser):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        context = _auth_context(browser, ADMIN_EMAIL)
        page = context.new_page()
        # Step 2: Navigate to /blog
        page.goto(
            f"{django_server}/blog",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Seeded articles appear with realistic titles
        # (not lorem ipsum)
        assert "Getting Started with LLM Agents" in body
        assert "RAG Pipeline Best Practices" in body

        # Verify no lorem ipsum placeholder text
        assert "Lorem ipsum" not in body
        assert "lorem ipsum" not in body

        # Step 3: Navigate to /courses
        page.goto(
            f"{django_server}/courses",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Seeded courses appear
        assert "LLM Agents Fundamentals" in body
        assert "RAG in Production" in body

        # Step 4: Navigate to /events
        page.goto(
            f"{django_server}/events",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Seeded events appear with a mix of statuses
        assert "LLM Agents Workshop" in body
        assert "Fine-Tuning Masterclass" in body

        # Verify status indicators are present
        body_text = page.inner_text("body").lower()
        assert "upcoming" in body_text or "completed" in body_text

        # Step 5: Navigate to /event-recordings
        page.goto(
            f"{django_server}/event-recordings",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Seeded recordings appear
        assert "Fine-Tuning Masterclass" in body
        assert "Introduction to Model Context Protocol" in body
# ---------------------------------------------------------------------------
# Scenario 12: Developer verifies cohort enrollments are created for
#               gated courses
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario12CohortEnrollments:
    """Developer verifies cohort enrollments are created for gated courses.

    Given: A developer has run seed_data on an empty database
    1. Query the Cohort and CohortEnrollment tables
    Then: At least 1 active cohort exists linked to a course
    Then: The cohort has enrolled users
    Then: Enrolled users have a tier level sufficient to access the
          cohort's course
    """

    def test_cohort_enrollments_with_sufficient_tier(self, django_server):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()

        from content.models import Cohort, CohortEnrollment

        # Then: At least 1 active cohort exists linked to a course
        active_cohorts = Cohort.objects.filter(is_active=True)
        assert active_cohorts.count() >= 1, (
            "Expected at least one active cohort"
        )

        for cohort in active_cohorts:
            # Then: The cohort has enrolled users
            enrollments = CohortEnrollment.objects.filter(cohort=cohort)
            assert enrollments.count() > 0, (
                f"Cohort '{cohort.name}' has no enrollments"
            )

            # Then: Enrolled users have a tier level sufficient to
            #       access the cohort's course
            course = cohort.course
            required_level = course.required_level
            for enrollment in enrollments:
                user = enrollment.user
                user_level = user.tier.level if user.tier else 0
                assert user_level >= required_level, (
                    f"User '{user.email}' has tier level {user_level} but "
                    f"course '{course.title}' requires level "
                    f"{required_level}"
                )
