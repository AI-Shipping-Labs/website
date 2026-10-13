"""Issue #1716: the skill-gap page maps a background to next steps.

A Free member arrives from the field-guide series index, picks a
background, reads the analysis, and follows a next step to a real
platform page. An anonymous visitor gets the sign-in card and none of the
background content.
"""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: seeds the DB and injects session cookies.
pytestmark = pytest.mark.local_only

FREE_EMAIL = "skill-gap-free-1716@test.com"
SERIES_SLUG = "ai-engineering-field-guide"
FIRST_STEP_SLUG = "build-your-own-search-engine"
FIRST_STEP_TITLE = "Build Your Own Search Engine"
LOCKED_STEP_SLUG = "eval-framework-batch-flex"


def _seed_series_index_and_first_step():
    """The series index (with its /skill-gap link) and the first DE step."""
    from django.db import connection

    from content.models import Article, Workshop

    Article.objects.filter(slug=SERIES_SLUG).delete()
    Article.objects.create(
        title="The AI Engineering Field Guide",
        slug=SERIES_SLUG,
        description="A data-driven guide to the AI engineer role.",
        content_markdown=(
            "## Using this guide\n\n"
            "Not sure where to start? [Tell us your background and get your "
            "gap list](/skill-gap).\n"
        ),
        date=datetime.date(2026, 9, 16),
        published=True,
    )
    Workshop.objects.filter(slug=FIRST_STEP_SLUG).delete()
    Workshop.objects.create(
        title=FIRST_STEP_TITLE,
        slug=FIRST_STEP_SLUG,
        description="Build the retrieval layer of a RAG system.",
        date=datetime.date(2026, 5, 14),
        status="published",
        landing_required_level=0,
        pages_required_level=5,
    )
    # A paid step: a Free member must see it is locked before clicking.
    Workshop.objects.filter(slug=LOCKED_STEP_SLUG).delete()
    Workshop.objects.create(
        title="Build an Agent Evaluation Framework with OpenAI Batch and Flex",
        slug=LOCKED_STEP_SLUG,
        description="Test sets and LLM-as-judge.",
        date=datetime.date(2026, 7, 3),
        status="published",
        landing_required_level=0,
        pages_required_level=10,
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
@browser_journey
def test_free_member_maps_background_to_next_steps(django_server, django_db_blocker, browser):
    with django_db_blocker.unblock():
        create_user(FREE_EMAIL, tier_slug="free")
        _seed_series_index_and_first_step()
        context = auth_context(browser, FREE_EMAIL)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/blog/{SERIES_SLUG}", wait_until="domcontentloaded")
        page.get_by_role("link", name="Tell us your background and get your gap list").click()
        expect(page).to_have_url(f"{django_server}/skill-gap")
        expect(page.get_by_test_id("skill-gap-option")).to_have_count(5)

        page.get_by_test_id("skill-gap-option").filter(has_text="Data Engineer").click()
        page.get_by_test_id("skill-gap-submit").click()

        expect(page).to_have_url(f"{django_server}/skill-gap?background=data-engineer")
        expect(page.get_by_role("heading", level=1)).to_have_text(
            "From Data Engineer to AI engineer",
        )
        expect(page.get_by_test_id("skill-gap-strength").first).to_be_visible()
        expect(page.get_by_test_id("skill-gap-gap").first).to_have_text(
            "LLM provider APIs (OpenAI, Anthropic) and structured outputs",
        )
        expect(page.get_by_test_id("skill-gap-path-step")).to_have_count(5)
        expect(page.get_by_test_id("skill-gap-timeline")).to_contain_text("3-4 months")
        steps = page.get_by_test_id("skill-gap-next-step-link")
        expect(steps).to_have_count(3)
        expect(steps.nth(0).get_by_test_id("skill-gap-next-step-access")).to_have_text(
            "Free with sign-in",
        )
        expect(steps.nth(1).get_by_test_id("skill-gap-next-step-access")).to_have_text(
            "Basic or above",
        )
        # The sprint program index has no single tier, so no access badge.
        expect(steps.nth(2).get_by_test_id("skill-gap-next-step-access")).to_have_count(0)

        steps.first.click()
        expect(page).to_have_url(f"{django_server}/workshops/{FIRST_STEP_SLUG}")
        expect(page.get_by_role("heading", level=1)).to_have_text(FIRST_STEP_TITLE)
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
@browser_journey
def test_anonymous_visitor_is_asked_to_sign_in(django_server, page):
    page.goto(f"{django_server}/skill-gap", wait_until="domcontentloaded")

    card = page.get_by_test_id("skill-gap-gated-card")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Sign in to see your skill-gap analysis")
    expect(page.get_by_test_id("skill-gap-signup-cta")).to_be_visible()
    expect(page.get_by_test_id("skill-gap-picker")).to_have_count(0)
    expect(page.get_by_test_id("skill-gap-strengths")).to_have_count(0)
    expect(page.get_by_text("Data Engineer")).to_have_count(0)

    page.get_by_test_id("skill-gap-signin-cta").click()
    expect(page).to_have_url(f"{django_server}/accounts/login/?next=%2Fskill-gap")
