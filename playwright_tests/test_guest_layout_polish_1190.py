"""Playwright coverage for guest-page layout polish (#1190)."""

import datetime
import os
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1190")


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _reset_guest_content():
    from django.db import connection

    from content.models import Article, Course, CuratedLink, Workshop, WorkshopPage
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Article.objects.all().delete()
    Course.objects.all().delete()
    CuratedLink.objects.all().delete()
    connection.close()


def _create_article(title, slug, *, cover_image_url="", tags=None):
    from django.db import connection

    from content.models import Article

    Article.objects.create(
        title=title,
        slug=slug,
        description=f"{title} description.",
        date=datetime.date(2026, 7, 1),
        cover_image_url=cover_image_url,
        tags=tags or [],
        published=True,
    )
    connection.close()


def _create_course(title, slug, tags=None):
    from django.db import connection

    from content.models import Course

    Course.objects.create(
        title=title,
        slug=slug,
        status="published",
        description=f"{title} description.",
        tags=tags or [],
    )
    connection.close()


def _create_entitlement_course(title, slug):
    from django.db import connection

    from content.models import Course

    Course.objects.create(
        title=title,
        slug=slug,
        status="published",
        description=f"{title} description.",
        access_mode="entitlement",
        enroll_url="https://maven.com/alexey-grigorev/from-rag-to-agents",
        program_label="Maven",
    )
    connection.close()


def _create_curated_link(title, item_id, *, description="", required_level=0):
    from django.db import connection

    from content.models import CuratedLink

    CuratedLink.objects.create(
        item_id=item_id,
        title=title,
        description=description,
        url=f"https://example.com/{item_id}",
        category="courses",
        tags=["agents"],
        source="Example",
        required_level=required_level,
        published=True,
    )
    connection.close()


def _create_workshop():
    from django.db import connection

    from content.models import Workshop

    workshop = Workshop.objects.create(
        slug="diagram-overflow-1190",
        title="Diagram Overflow 1190",
        status="published",
        date=datetime.date(2026, 7, 2),
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        description=(
            "```mermaid\n"
            "flowchart LR\n"
            "FAQ[FAQ knowledge base] --> Q[generate synthetic questions]\n"
            "Q --> R[rank retrieved passages] --> E[evaluate answers]\n"
            "E --> D[document improvements] --> S[ship evaluation report]\n"
            "```\n"
        ),
    )
    connection.close()
    return workshop.get_absolute_url()


def _create_event():
    from django.db import connection

    from events.models import Event

    event = Event.objects.create(
        title="Inline Dash Agenda 1190",
        slug="inline-dash-agenda-1190",
        description=(
            "Bring: - your current CV - one job description\n\n"
            "We will discuss domain-specific examples."
        ),
        start_datetime=timezone.now() + datetime.timedelta(days=3),
        status="upcoming",
        published=True,
    )
    connection.close()
    return event.get_absolute_url()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_blog_cards_are_text_first_with_one_topic_eyebrow(django_server, page):
    _reset_guest_content()
    _create_article(
        "Covered 1190 Article",
        "covered-1190-article",
        cover_image_url="https://example.com/cover.png",
        tags=["agents"],
    )
    _create_article(
        "Coverless 1190 Article",
        "coverless-1190-article",
        tags=["rag"],
    )

    page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
    expect(page.get_by_test_id("blog-card-thumbnail")).to_have_count(0)
    expect(page.get_by_test_id("blog-card-thumbnail-fallback")).to_have_count(0)
    expect(page.get_by_test_id("blog-card").locator("picture, img")).to_have_count(0)
    # Both articles (agents, rag) map to the AI Engineering topic, so each
    # text-first row carries one non-clickable curated eyebrow.
    card = page.locator('article:has-text("Coverless 1190 Article")')
    eyebrow = card.get_by_test_id("blog-card-topic")
    expect(eyebrow).to_have_count(1)
    expect(eyebrow).to_be_visible()
    assert eyebrow.text_content().strip() == "AI Engineering"
    assert card.locator('[data-testid="blog-card-topic"] a').count() == 0
    _shot(page, "blog-text-first-feed")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_workshop_diagram_uses_local_overflow_not_page_overflow(
    django_server, page,
):
    _reset_guest_content()
    workshop_url = _create_workshop()

    for width, name in ((1440, "workshop-diagram-desktop"), (390, "workshop-diagram-mobile")):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{django_server}{workshop_url}", wait_until="networkidle")
        description = page.get_by_test_id("workshop-description")
        expect(description).to_be_visible()
        assert "overflow-x-auto" in (description.get_attribute("class") or "")
        page_overflows = page.evaluate(
            "() => document.documentElement.scrollWidth > window.innerWidth + 1"
        )
        assert not page_overflows
        _shot(page, name)


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_resources_cards_drop_category_pills_and_keep_gated_cta(
    django_server, page,
):
    _reset_guest_content()
    _create_curated_link(
        "Compact 1190 Resource",
        "compact-1190-resource",
        description="",
    )
    _create_curated_link(
        "Described 1190 Resource",
        "described-1190-resource",
        description="A compact resource card with real description text.",
    )
    _create_curated_link(
        "Gated 1190 Resource",
        "gated-1190-resource",
        description="A gated resource.",
        required_level=10,
    )

    page.goto(f"{django_server}/resources", wait_until="domcontentloaded")
    compact_card = page.locator('a:has-text("Compact 1190 Resource")')
    expect(compact_card).to_be_visible()
    expect(compact_card.locator('[data-lucide="book-open"]')).to_have_count(0)
    expect(compact_card.locator("p").filter(has_text="A compact resource")).to_have_count(0)

    gated_card = page.get_by_role(
        "button", name="Show access options for Gated 1190 Resource",
    )
    expect(gated_card.locator('[data-lucide="book-open"]')).to_have_count(0)
    gated_card.click()
    expect(
        gated_card.get_by_role("link", name="View membership tiers")
    ).to_be_visible()
    _shot(page, "resources-compact-gated")


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
@browser_journey
def test_courses_two_card_catalog_grid_stays_left_aligned(django_server, page):
    """Issue #1719: a 2-card filtered catalog must render the canonical
    grid, flush with the left-aligned heading above it — not centred and
    width-capped as the old low-count special case did.

    Visual contract (exact Tailwind class string) — `visual_regression`,
    not `core`, per _docs/testing-guidelines.md ("Policy for class /
    Tailwind / layout assertions"); the two markers are orthogonal.
    """
    _reset_guest_content()
    _create_course("Small 1190 Course One", "small-1190-course-one", ["small"])
    _create_course("Small 1190 Course Two", "small-1190-course-two", ["small"])
    _create_course("Other 1190 Course", "other-1190-course", ["other"])

    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{django_server}/courses?tag=small", wait_until="domcontentloaded")
    grid = page.get_by_test_id("courses-grid")
    expect(grid).to_be_visible()
    classes = grid.get_attribute("class") or ""
    assert classes == "grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
    assert "mx-auto" not in classes
    assert "max-w-" not in classes

    heading = page.get_by_role("heading", name="Structured Learning Paths")
    heading_box = heading.bounding_box()
    grid_box = grid.bounding_box()
    assert heading_box is not None
    assert grid_box is not None
    assert grid_box["x"] == pytest.approx(heading_box["x"], abs=1)
    _shot(page, "courses-two-card-grid")


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
@browser_journey
def test_courses_one_card_filtered_catalog_grid_stays_left_aligned(django_server, page):
    """Issue #1719: a tag filter narrowing the standard catalog to a
    single card must not centre/cap that lone card either.

    Visual contract — `visual_regression`, not `core` (see above).
    """
    _reset_guest_content()
    _create_course("Lone 1190 Course", "lone-1190-course", ["lonely"])
    _create_course("Other 1190 Course", "other-1190-course-2", ["other"])

    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{django_server}/courses?tag=lonely", wait_until="domcontentloaded")
    grid = page.get_by_test_id("courses-grid")
    expect(grid).to_be_visible()
    classes = grid.get_attribute("class") or ""
    assert classes == "grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
    assert "mx-auto" not in classes
    assert "max-w-" not in classes

    heading = page.get_by_role("heading", name="Structured Learning Paths")
    heading_box = heading.bounding_box()
    grid_box = grid.bounding_box()
    assert heading_box is not None
    assert grid_box is not None
    assert grid_box["x"] == pytest.approx(heading_box["x"], abs=1)
    _shot(page, "courses-one-card-grid")


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
@browser_journey
def test_courses_sold_separately_single_card_grid_stays_left_aligned(django_server, page):
    """Issue #1719 (the reported bug): the single-card "External courses"
    section (Maven buildcamp) must render flush with its own
    left-aligned heading, not centred under a capped width.

    Visual contract — `visual_regression`, not `core` (see above).
    """
    _reset_guest_content()
    _create_course("Standard 1190 Course", "standard-1190-course")
    _create_entitlement_course("Maven 1190 Buildcamp", "maven-1190-buildcamp")

    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{django_server}/courses", wait_until="domcontentloaded")
    grid = page.get_by_test_id("sold-separately-grid")
    expect(grid).to_be_visible()
    classes = grid.get_attribute("class") or ""
    assert "grid gap-6 sm:grid-cols-2 lg:grid-cols-3" in classes
    assert "mx-auto" not in classes
    assert "max-w-" not in classes

    heading = page.get_by_role("heading", name="External courses")
    heading_box = heading.bounding_box()
    grid_box = grid.bounding_box()
    assert heading_box is not None
    assert grid_box is not None
    assert grid_box["x"] == pytest.approx(heading_box["x"], abs=1)
    _shot(page, "courses-sold-separately-single-card-grid")


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
@browser_journey
def test_courses_six_card_catalog_keeps_three_column_grid(django_server, page):
    """Regression: a full catalog above the column count still renders
    the same canonical three-column grid (unaffected by the fix).

    Visual contract — `visual_regression`, not `core` (see above).
    """
    _reset_guest_content()
    for index in range(6):
        _create_course(f"Full 1190 Course {index}", f"full-1190-course-{index}")

    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{django_server}/courses", wait_until="domcontentloaded")
    grid = page.get_by_test_id("courses-grid")
    expect(grid).to_be_visible()
    classes = grid.get_attribute("class") or ""
    assert classes == "grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
    column_count = grid.evaluate(
        "(el) => getComputedStyle(el).gridTemplateColumns.split(' ').length"
    )
    assert column_count == 3
    _shot(page, "courses-six-card-grid")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
@browser_journey
def test_courses_low_count_grid_unaffected_on_mobile_viewport(django_server, page):
    """Regression: the fix only touched the ``lg:`` classes, so a
    low-count grid on a mobile viewport (below the ``sm:`` breakpoint)
    keeps rendering as a single column, same as before the fix."""
    _reset_guest_content()
    _create_course("Mobile 1190 Course One", "mobile-1190-course-one", ["small"])
    _create_course("Mobile 1190 Course Two", "mobile-1190-course-two", ["small"])

    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{django_server}/courses?tag=small", wait_until="domcontentloaded")
    grid = page.get_by_test_id("courses-grid")
    expect(grid).to_be_visible()
    column_count = grid.evaluate(
        "(el) => getComputedStyle(el).gridTemplateColumns.split(' ').length"
    )
    assert column_count == 1
    _shot(page, "courses-low-count-grid-mobile")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_event_inline_dash_agenda_renders_as_list(django_server, page):
    _reset_guest_content()
    event_url = _create_event()

    page.goto(f"{django_server}{event_url}", wait_until="domcontentloaded")
    body = page.locator("body")
    expect(body).to_contain_text("your current CV")
    expect(body).to_contain_text("one job description")
    assert "Bring: - your current CV" not in body.inner_text()
    expect(body).to_contain_text("domain-specific")
    expect(page.locator(".prose li")).to_have_count(2)
    _shot(page, "event-inline-dash-list")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_about_linkedin_mobile_tap_targets(django_server, page):
    _reset_guest_content()
    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{django_server}/about", wait_until="domcontentloaded")

    links = page.locator('a[aria-label="LinkedIn"]')
    expect(links).to_have_count(2)
    for index in range(2):
        box = links.nth(index).bounding_box()
        assert box is not None
        assert box["width"] >= 44
        assert box["height"] >= 44
        assert "focus-visible:ring-2" in (links.nth(index).get_attribute("class") or "")
    _shot(page, "about-linkedin-mobile")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_auth_and_subscribe_pages_suppress_footer_newsletter(django_server, page):
    _reset_guest_content()
    pages = [
        ("/accounts/login/", "login-no-footer-newsletter", "login-form"),
        ("/accounts/register/", "register-no-footer-newsletter", "register-form"),
        ("/subscribe", "subscribe-no-footer-newsletter", "subscribe-page"),
    ]

    for path, screenshot_name, required_selector in pages:
        page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
        expect(page.locator("#newsletter")).to_have_count(0)
        expect(page.locator("body")).not_to_contain_text(
            "Build AI in public, with a group."
        )
        if required_selector == "subscribe-page":
            expect(page.locator("form.subscribe-form")).to_have_count(1)
        else:
            expect(page.locator(f"#{required_selector}")).to_be_visible()
        _shot(page, screenshot_name)


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_discovery_pages_keep_footer_newsletter(django_server, page):
    _reset_guest_content()
    _create_article("Discovery 1190 Article", "discovery-1190-article")

    for path in ("/", "/blog"):
        page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
        expect(page.locator("#newsletter")).to_be_visible()
        expect(page.locator("body")).to_contain_text(
            "Build AI in public, with a group."
        )
