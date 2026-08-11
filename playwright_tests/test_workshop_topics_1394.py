"""Browser journeys for curated workshop topics and editorial rows (#1394)."""

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.test_workshops import _clear_workshops, _create_workshop

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "issue-1394"


def _create_catalog_workshop(
    *, slug, title, tags, pages=0, description="Catalog workshop.",
    skill_level="", cover_image_url="",
):
    return _create_workshop(
        slug=slug,
        title=title,
        tags=tags,
        pages=pages,
        recording=pages,
        pages_data=[],
        with_event=False,
        instructor="Alexey",
        description=description,
        skill_level=skill_level,
        cover_image_url=cover_image_url,
        code_repo_url="",
    )


def _open_catalog(page, django_server, query=""):
    response = page.goto(
        f"{django_server}/workshops/catalog{query}",
        wait_until="domcontentloaded",
    )
    assert response is not None and response.status == 200
    expect(page.locator('[data-testid="workshop-catalog"]')).to_be_visible()


def test_visitor_filters_with_curated_topics(page, django_server):
    _clear_workshops()
    _create_catalog_workshop(
        slug="agent-function",
        title="Function Calling Agents",
        tags=["function-calling", "personal-brand"],
    )
    _create_catalog_workshop(
        slug="rag-search",
        title="RAG Search Workshop",
        tags=["rag", "elasticsearch"],
    )
    _create_catalog_workshop(
        slug="production-app",
        title="Production App Workshop",
        tags=["python", "django"],
    )
    _create_catalog_workshop(
        slug="career",
        title="Career Workshop",
        tags=["career", "linkedin"],
        pages=10,
    )
    _create_workshop(
        slug="draft-coding",
        title="Draft Coding Workshop",
        tags=["coding-assistants"],
        status="draft",
        pages_data=[],
        with_event=False,
        instructor=None,
    )

    _open_catalog(page, django_server)

    topic_filter = page.locator('[data-testid="workshop-topic-filter"]')
    expect(topic_filter).to_be_visible()
    expect(topic_filter.get_by_role("link")).to_have_count(5)
    expect(topic_filter.get_by_text("All", exact=True)).to_be_visible()
    expect(topic_filter.get_by_text("AI Agents", exact=True)).to_be_visible()
    expect(topic_filter.get_by_text("RAG & Search", exact=True)).to_be_visible()
    expect(topic_filter.get_by_text("Production & Apps", exact=True)).to_be_visible()
    expect(topic_filter.get_by_text("Career", exact=True)).to_be_visible()
    expect(page.locator('[data-testid="workshop-facet-topic"]')).to_have_count(0)
    expect(page.locator('[data-testid="workshop-facet-technology"]')).to_have_count(0)
    expect(page.locator('[data-testid="workshop-access-filters"]')).to_have_count(0)
    expect(page.get_by_text("Draft Coding Workshop", exact=True)).to_have_count(0)

    page.locator('[data-testid="workshop-topic-ai-agents"]').click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(re.compile(r"/workshops/catalog\?topic=ai-agents$"))
    expect(
        page.locator('[data-testid="workshop-topic-ai-agents"]')
    ).to_have_attribute("aria-current", "page")
    expect(page.locator('[data-workshop-slug="agent-function"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="rag-search"]')).to_have_count(0)
    agent_row = page.locator('[data-workshop-slug="agent-function"]')
    expect(agent_row.locator('[data-testid="workshop-card-topic"]')).to_have_text(
        "AI Agents"
    )
    expect(agent_row).not_to_contain_text("function-calling")
    expect(agent_row).not_to_contain_text("personal-brand")

    page.locator('[data-testid="workshop-topic-all"]').click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(re.compile(r"/workshops/catalog$"))
    expect(
        page.locator('[data-testid="workshop-topic-all"]')
    ).to_have_attribute("aria-current", "page")
    expect(page.locator('[data-testid="workshop-card"]')).to_have_count(4)
    expect(page.locator('[data-testid^="workshop-skill-filter-"]')).to_have_count(0)
    expect(page.locator('[data-testid^="workshop-technology-option-"]')).to_have_count(0)
    expect(page.locator('[data-testid="workshop-active-filters"]')).to_have_count(0)

    page.locator('[data-testid="workshop-topic-ai-agents"]').click()
    page.wait_for_load_state("domcontentloaded")
    page.locator('[data-workshop-slug="agent-function"] a').first.click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(re.compile(r"/workshops/agent-function$"))
    expect(page.locator('[data-testid="workshop-title"]')).to_have_text(
        "Function Calling Agents"
    )

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOT_DIR / "curated-topic-editorial-list.png",
        full_page=True,
    )


def test_catalog_uses_3xl_editorial_rows_and_conditional_media(
    page, django_server,
):
    _clear_workshops()
    _create_catalog_workshop(
        slug="text-first",
        title="Text-first Workshop",
        tags=["ai-agents"],
        description="A concise workshop summary for scanning and comparison.",
        skill_level="intermediate",
    )
    _create_catalog_workshop(
        slug="authored-cover",
        title="Workshop with Authored Cover",
        tags=["rag"],
        cover_image_url="https://example.com/workshop-cover.png",
    )
    _create_workshop(
        slug="auto-only",
        title="Workshop with Generated Banner Only",
        tags=["python"],
        pages_data=[],
        with_event=False,
        instructor="Alexey",
        auto_banner_url="https://example.com/generated-workshop-banner.png",
        code_repo_url="",
    )

    _open_catalog(page, django_server)

    catalog = page.locator('[data-testid="workshop-catalog"]')
    expect(catalog.locator("div.mx-auto").first).to_have_class(
        re.compile(r"\bmax-w-3xl\b")
    )
    expect(page.locator('[data-testid="workshops-list"]')).to_be_visible()
    expect(page.locator('[data-testid="workshops-grid"]')).to_have_count(0)

    text_row = page.locator('[data-workshop-slug="text-first"]')
    expect(text_row).to_have_class(re.compile(r"\bborder-b\b"))
    expect(text_row.locator('[data-testid="workshop-card-link"]')).to_have_class(
        re.compile(r"\bsm:flex-row\b")
    )
    expect(text_row.locator('[data-testid="workshop-card-title"]')).to_be_visible()
    expect(
        text_row.locator('[data-testid="workshop-card-description"]')
    ).to_be_visible()
    metadata = text_row.locator('[data-testid="workshop-card-metadata"]')
    expect(metadata.locator('[data-testid="workshop-card-date"]')).to_be_visible()
    expect(metadata.locator('[data-testid="workshop-card-instructor"]')).to_be_visible()
    expect(metadata.locator('[data-testid="workshop-skill-badge"]')).to_have_text(
        "Intermediate"
    )
    expect(text_row.locator('[data-testid^="workshop-card-preview"]')).to_have_count(0)
    expect(text_row.locator(".aspect-video")).to_have_count(0)

    cover_row = page.locator('[data-workshop-slug="authored-cover"]')
    expect(
        cover_row.locator('[data-testid="content-card-editorial-media"]')
    ).to_be_visible()
    expect(
        cover_row.locator('[data-testid="workshop-card-preview-image"]')
    ).to_have_attribute("src", "https://example.com/workshop-cover.png")
    expect(
        cover_row.locator('[data-testid="workshop-card-preview-image"]')
    ).to_have_count(1)

    auto_row = page.locator('[data-workshop-slug="auto-only"]')
    expect(auto_row.locator('[data-testid^="workshop-card-preview"]')).to_have_count(0)
    expect(auto_row.locator(".aspect-video")).to_have_count(0)
    expect(
        auto_row.locator('img[src="https://example.com/generated-workshop-banner.png"]')
    ).to_have_count(0)


def test_legacy_raw_tag_bookmark_filters_and_clears_without_raw_controls(
    page, django_server,
):
    _clear_workshops()
    _create_catalog_workshop(
        slug="python-bookmark",
        title="Python Bookmark Workshop",
        tags=["python"],
    )
    _create_catalog_workshop(
        slug="rag-bookmark",
        title="RAG Bookmark Workshop",
        tags=["rag"],
    )

    _open_catalog(page, django_server, "?tag=python")

    expect(page.get_by_text("Python Bookmark Workshop", exact=True)).to_be_visible()
    expect(page.get_by_text("RAG Bookmark Workshop", exact=True)).to_have_count(0)
    expect(page.locator('[data-testid="workshop-active-filters"]')).to_have_count(0)
    expect(page.locator('[data-testid="workshop-active-tag"]')).to_have_count(0)
    expect(page.locator('[data-testid="workshop-facet-topic"]')).to_have_count(0)

    all_topics = page.locator('[data-testid="workshop-topic-all"]')
    expect(all_topics).to_have_attribute("href", "/workshops/catalog")
    all_topics.click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(re.compile(r"/workshops/catalog$"))
    expect(page.get_by_text("Python Bookmark Workshop", exact=True)).to_be_visible()
    expect(page.get_by_text("RAG Bookmark Workshop", exact=True)).to_be_visible()


def test_retired_filter_parameters_leave_the_archive_discoverable(
    page, django_server,
):
    _clear_workshops()
    _create_catalog_workshop(
        slug="free-beginner",
        title="Free Beginner Workshop",
        tags=["python"],
        skill_level="beginner",
    )
    _create_catalog_workshop(
        slug="paid-advanced",
        title="Paid Advanced Workshop",
        tags=["rag"],
        pages=10,
        skill_level="advanced",
    )

    _open_catalog(
        page,
        django_server,
        "?access=free&skill_level=beginner&tool=Claude+Code",
    )

    expect(page.get_by_text("Free Beginner Workshop", exact=True)).to_be_visible()
    expect(page.get_by_text("Paid Advanced Workshop", exact=True)).to_be_visible()
    expect(
        page.locator('[data-testid="workshop-topic-all"]')
    ).to_have_attribute("aria-current", "page")
    for retired_selector in (
        '[data-testid="workshop-access-filters"]',
        '[data-testid^="workshop-access-filter-"]',
        '[data-testid^="workshop-skill-filter-"]',
        '[data-testid="workshop-facet-technology"]',
        '[data-testid^="workshop-technology-option-"]',
        '[data-testid="workshop-active-filters"]',
    ):
        expect(page.locator(retired_selector)).to_have_count(0)


def test_mobile_editorial_rows_wrap_topics_without_horizontal_overflow(
    page, django_server,
):
    _clear_workshops()
    for slug, tags in (
        ("agents", ["ai-agents"]),
        ("rag", ["rag"]),
        ("engineering", ["evaluation"]),
        ("coding", ["coding-assistants"]),
        ("production", ["python"]),
        ("career", ["career"]),
    ):
        _create_catalog_workshop(
            slug=slug,
            title=f"{slug.title()} Workshop with a Readable Long Title",
            tags=tags,
            description="Readable workshop summary that may wrap on a phone.",
        )

    page.set_viewport_size({"width": 390, "height": 844})
    _open_catalog(page, django_server)

    expect(page.locator('[data-testid="workshop-topic-filter"]')).to_be_visible()
    expect(page.locator('[data-testid="workshops-list"]')).to_be_visible()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth"
    )
    first_row = page.locator('[data-testid="workshop-card"]').first
    expect(first_row.locator('[data-testid="workshop-card-title"]')).to_be_visible()
    expect(first_row.locator('[data-testid="workshop-card-metadata"]')).to_be_visible()

    page.locator('[data-testid="workshop-topic-ai-agents"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth"
    )
    page.locator('[data-workshop-slug="agents"] a').first.click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(re.compile(r"/workshops/agents$"))
    expect(page.locator('[data-testid="workshop-title"]')).to_be_visible()
