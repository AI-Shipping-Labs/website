"""Browser journeys for the workshop catalog facet split (#1244)."""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.test_workshops import _clear_workshops, _create_workshop

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "issue-1244-correction"


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _create_catalog_workshop(*, slug, title, tags, pages=0, core_tools=None):
    return _create_workshop(
        slug=slug,
        title=title,
        tags=tags,
        pages=pages,
        recording=pages,
        core_tools=core_tools,
        pages_data=[],
        with_event=False,
        instructor=None,
        description="Catalog facet fixture.",
        code_repo_url="",
    )


def _open_catalog(page, django_server, query=""):
    response = page.goto(
        f"{django_server}/workshops/catalog{query}",
        wait_until="domcontentloaded",
    )
    assert response is not None and response.status == 200
    expect(page.locator('[data-testid="workshop-catalog"]')).to_be_visible()


def test_visitor_sees_and_operates_separate_topic_and_technology_facets(
    page, django_server,
):
    _clear_workshops()
    _create_catalog_workshop(
        slug="rag-search",
        title="RAG Search Workshop",
        tags=["rag", "search", "python"],
    )
    _create_catalog_workshop(
        slug="agents-django",
        title="Agents Django Workshop",
        tags=["ai-agents", "django", "python"],
        pages=10,
    )
    _create_catalog_workshop(
        slug="career-brand",
        title="Career Brand Workshop",
        tags=["career", "personal-brand"],
    )
    _create_workshop(
        slug="draft-react",
        title="Draft React Workshop",
        tags=["draft-only", "react"],
        status="draft",
        pages_data=[],
        with_event=False,
        instructor=None,
    )

    _open_catalog(page, django_server)

    topics = page.locator('[data-testid="workshop-facet-topic"]')
    technologies = page.locator('[data-testid="workshop-facet-technology"]')
    expect(topics).to_be_visible()
    expect(technologies).to_be_visible()
    expect(topics.locator("h3")).to_have_text("Topics")
    expect(technologies.locator("h3")).to_have_text("Technologies")
    expect(topics.locator('[data-facet="topic"]')).to_have_count(4)
    expect(technologies.locator('[data-facet="technology"]')).to_have_count(2)
    expect(topics.get_by_text("rag", exact=True)).to_be_visible()
    expect(topics.get_by_text("django", exact=True)).to_have_count(0)
    expect(technologies.get_by_text("django", exact=True)).to_be_visible()
    expect(technologies.get_by_text("rag", exact=True)).to_have_count(0)
    expect(page.get_by_text("personal-brand", exact=True)).to_have_count(1)
    expect(topics.get_by_text("personal-brand", exact=True)).to_have_count(0)
    expect(technologies.get_by_text("personal-brand", exact=True)).to_have_count(0)
    expect(page.get_by_text("Draft React Workshop", exact=True)).to_have_count(0)
    expect(page.locator('[data-testid="workshop-topic-browser"]')).to_have_count(0)
    expect(page.locator('[data-testid="workshop-topic-options"]')).to_have_count(0)
    expect(page.locator('[data-testid="workshop-tool-filters"]')).to_have_count(0)

    page.locator('[data-testid="workshop-topic-option-rag"]').click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-workshop-slug="rag-search"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="agents-django"]')).to_have_count(0)
    expect(page.locator('[data-testid="workshop-active-tag"]')).to_have_text("rag")
    expect(
        page.locator('[data-testid="workshop-topic-option-rag"]')
    ).to_have_attribute("aria-current", "page")

    page.locator('[data-testid="workshop-topic-option-rag"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.endswith("/workshops/catalog")

    page.locator('[data-testid="workshop-technology-option-django"]').click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-workshop-slug="agents-django"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="rag-search"]')).to_have_count(0)
    expect(
        page.locator('[data-testid="workshop-technology-option-django"]')
    ).to_have_attribute("aria-current", "page")


def test_visitor_composes_access_topic_and_technology_filters(page, django_server):
    _clear_workshops()
    _create_catalog_workshop(
        slug="free-rag-django",
        title="Free RAG Django",
        tags=["rag", "django"],
    )
    _create_catalog_workshop(
        slug="free-rag-only",
        title="Free RAG Only",
        tags=["rag"],
    )
    _create_catalog_workshop(
        slug="paid-rag-django",
        title="Paid RAG Django",
        tags=["rag", "django"],
        pages=10,
    )
    _create_catalog_workshop(
        slug="react-only",
        title="React Only Workshop",
        tags=["react"],
    )

    _open_catalog(page, django_server)
    page.locator('[data-testid="workshop-access-filter-free"]').click()
    page.wait_for_load_state("domcontentloaded")
    page.locator('[data-testid="workshop-topic-option-rag"]').click()
    page.wait_for_load_state("domcontentloaded")

    expect(page.locator('[data-workshop-slug="free-rag-django"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="free-rag-only"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="paid-rag-django"]')).to_have_count(0)
    expect(
        page.locator('[data-testid="workshop-access-filter-free"]')
    ).to_have_attribute("aria-current", "page")
    expect(
        page.locator('[data-testid="workshop-topic-option-rag"]')
    ).to_have_attribute("aria-current", "page")

    page.locator('[data-testid="workshop-access-filter-all"]').click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-workshop-slug="paid-rag-django"]')).to_be_visible()

    page.locator('[data-testid="workshop-technology-option-django"]').click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-workshop-slug="free-rag-django"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="paid-rag-django"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="free-rag-only"]')).to_have_count(0)
    expect(
        page.locator('[data-testid="workshop-topic-option-rag"]')
    ).to_have_attribute("aria-current", "page")
    expect(
        page.locator('[data-testid="workshop-technology-option-django"]')
    ).to_have_attribute("aria-current", "page")
    expect(page.locator('[data-testid="workshop-topic-summary"]')).to_have_text(
        "Workshops about rag"
    )
    expect(
        page.locator('[data-testid="workshop-selected-filter-summary"]')
    ).to_have_text("Workshops matching selected filters")
    active_filters = page.locator('[data-testid="workshop-active-filters"]')
    expect(
        active_filters.get_by_role(
            "link", name="Remove rag topic filter", exact=True
        )
    ).to_be_visible()
    expect(
        active_filters.get_by_role(
            "link", name="Remove django technology filter", exact=True
        )
    ).to_be_visible()
    _shot(page, "mixed-rag-django")

    _open_catalog(page, django_server, "?tag=django&tag=react")
    expect(page.locator('[data-testid="workshop-topic-summary"]')).to_have_count(0)
    expect(
        page.locator('[data-testid="workshop-selected-filter-summary"]')
    ).to_have_text("Workshops matching selected filters")
    expect(page.locator('[data-testid="workshops-empty-state"]')).to_contain_text(
        "No workshops found"
    )
    expect(page.locator('[data-testid="workshops-empty-state"]')).to_contain_text(
        "No workshops match the selected filters."
    )
    active_filters = page.locator('[data-testid="workshop-active-filters"]')
    expect(
        active_filters.get_by_role(
            "link", name="Remove django technology filter", exact=True
        )
    ).to_be_visible()
    expect(
        active_filters.get_by_role(
            "link", name="Remove react technology filter", exact=True
        )
    ).to_be_visible()
    _shot(page, "technology-only-empty")


def test_deep_links_card_tags_and_empty_state_keep_existing_journeys(
    page, django_server,
):
    _clear_workshops()
    _create_catalog_workshop(
        slug="free-search",
        title="Free Search Workshop",
        tags=["search", "personal-brand"],
    )
    _create_catalog_workshop(
        slug="free-career",
        title="Free Career Workshop",
        tags=["career"],
    )
    _create_catalog_workshop(
        slug="paid-rag",
        title="Paid RAG Workshop",
        tags=["rag"],
        pages=10,
    )

    _open_catalog(page, django_server, "?access=free&tag=search")
    expect(page.locator('[data-workshop-slug="free-search"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="paid-rag"]')).to_have_count(0)
    expect(
        page.locator('[data-testid="workshop-access-filter-free"]')
    ).to_have_attribute("aria-current", "page")
    expect(
        page.locator('[data-testid="workshop-topic-option-search"]')
    ).to_have_attribute("aria-current", "page")

    page.locator('[data-testid="clear-workshop-filter"]').click()
    page.wait_for_load_state("domcontentloaded")
    search_card = page.locator('[data-workshop-slug="free-search"]')
    search_card.locator('[data-testid="workshop-card-topic"]', has_text="search").click()
    page.wait_for_load_state("domcontentloaded")
    expect(
        page.locator('[data-testid="workshop-topic-option-search"]')
    ).to_have_attribute("aria-current", "page")

    _open_catalog(page, django_server, "?tag=personal-brand")
    expect(page.locator('[data-testid="workshop-topic-summary"]')).to_have_count(0)
    expect(
        page.locator('[data-testid="workshop-selected-filter-summary"]')
    ).to_have_text("Workshops matching selected filters")
    expect(
        page.get_by_role("link", name="Remove personal-brand filter", exact=True)
    ).to_be_visible()
    _shot(page, "excluded-personal-brand")

    _open_catalog(page, django_server)
    page.locator('[data-testid="workshop-access-filter-paid"]').click()
    page.wait_for_load_state("domcontentloaded")
    page.locator('[data-testid="workshop-topic-option-career"]').click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-testid="workshops-empty-state"]')).to_be_visible()
    clear = page.get_by_role("link", name="View all workshops").last
    expect(clear).to_be_visible()
    clear.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-testid="workshops-grid"]')).to_be_visible()


def test_core_tool_collision_prefers_tool_and_empty_group_hides(page, django_server):
    _clear_workshops()
    _create_catalog_workshop(
        slug="python-tag",
        title="Python Tag Workshop",
        tags=["python"],
    )
    _create_catalog_workshop(
        slug="python-tool",
        title="Python Tool Workshop",
        tags=["search"],
        core_tools=["Python"],
    )

    _open_catalog(page, django_server)
    python = page.locator('[data-testid="workshop-technology-option-python"]')
    expect(python).to_have_count(1)
    expect(python).to_have_attribute("data-tool", "Python")
    expect(python).not_to_have_attribute("data-topic", "python")
    python.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-workshop-slug="python-tool"]')).to_be_visible()
    expect(page.locator('[data-workshop-slug="python-tag"]')).to_have_count(0)

    _clear_workshops()
    _create_catalog_workshop(
        slug="topic-only",
        title="Topic Only Workshop",
        tags=["career"],
    )
    _open_catalog(page, django_server)
    expect(page.locator('[data-testid="workshop-facet-topic"]')).to_be_visible()
    expect(
        page.locator('[data-testid="workshop-facet-technology"]')
    ).to_have_count(0)
