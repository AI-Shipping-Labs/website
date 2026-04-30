"""
Playwright E2E tests for the test data seeding command (Issue #103).

Only Scenario 11 remains here -- it requires a real browser to verify
seeded development fixtures appear on the site. All other scenarios (1-10, 12) were
moved to content/tests/test_seed_data.py because they exercise the ORM
directly and never open a browser.

Usage:
    uv run pytest playwright_tests/test_seed_data.py -v
"""

import io
import os

import pytest
from django.core.management import call_command

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
ADMIN_EMAIL = "admin@aishippinglabs.com"
ADMIN_PASSWORD = "admin123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flush_all_seed_data():
    """Remove seed_data fixtures and content so assertions only use seed_data."""
    from django.contrib.auth import get_user_model

    from content.models import (
        Article,
        Cohort,
        CohortEnrollment,
        Course,
        CuratedLink,
        Download,
        Module,
        Project,
        Unit,
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


# ---------------------------------------------------------------------------
# Scenario 11: Developer seeds data and then browses the site as an admin
#               to verify seed-owned fixtures appear
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario11BrowseSiteAsAdmin:
    """Developer seeds data and then browses the site as an admin to
    verify seed-owned fixtures appear.

    Given: A developer has run seed_data on an empty database
    1. Log in as admin@aishippinglabs.com with password admin123
    2. Navigate to /vote
    Then: Seeded polls appear with their deterministic titles and types
    3. Open a seeded poll
    Then: Seeded options and vote counts appear
    """

    def test_browse_site_shows_seeded_content(self, django_server, browser):
        _flush_all_seed_data()
        _ensure_tiers()
        _run_seed_data()
        from django.db import connection
        connection.close()

        context = _auth_context(browser, ADMIN_EMAIL)
        page = context.new_page()

        # Step 2: Navigate to /vote
        page.goto(
            f"{django_server}/vote",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Seeded polls appear with deterministic titles and types
        assert "What topic should our next deep-dive cover?" in body
        assert "Which mini-course should we create next?" in body

        body_text = page.inner_text("body").lower()
        assert "topic poll" in body_text
        assert "course poll" in body_text

        # Step 3: Open a seeded poll and verify seed-owned options/votes.
        page.locator('a[href^="/vote/"]').filter(
            has_text="What topic should our next deep-dive cover?",
        ).first.click()
        page.wait_for_load_state("domcontentloaded")
        detail_text = page.inner_text("body")
        assert "Advanced RAG: GraphRAG and Knowledge Graphs" in detail_text
        assert "LLM Security: Prompt Injection and Defenses" in detail_text
        assert "votes remaining" in detail_text.lower()
        context.close()
