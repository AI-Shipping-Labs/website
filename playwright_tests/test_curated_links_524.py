"""
Playwright E2E tests for /resources reorder + new categories (Issue #524).

Covers the eight scenarios from the groomed issue:

1. Visitor sees the new section order on /resources
2. Visitor finds a curated workshop in the Workshops section
3. Visitor finds a curated article in the Articles section
4. Legacy tools and models curated links still appear, folded under Other
5. Empty categories do not render headings
6. Free user hits the upgrade CTA on a gated curated workshop
7. Tag filter works across the new section grouping
8. Page header copy reflects the new grouping

Usage:
    uv run pytest playwright_tests/test_curated_links_524.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _create_curated_link(
    title,
    item_id=None,
    description="",
    url="https://example.com",
    category="workshops",
    tags=None,
    required_level=0,
    sort_order=0,
    published=True,
    source="",
):
    """Create a CuratedLink via ORM."""
    from content.models import CuratedLink

    if tags is None:
        tags = []
    if item_id is None:
        item_id = title.lower().replace(" ", "-")

    link = CuratedLink(
        item_id=item_id,
        title=title,
        description=description,
        url=url,
        category=category,
        tags=tags,
        required_level=required_level,
        sort_order=sort_order,
        published=published,
        source=source,
    )
    link.save()
    connection.close()
    return link


def _clear_curated_links():
    """Delete all curated links to ensure a clean state."""
    from content.models import CuratedLink

    CuratedLink.objects.all().delete()
    connection.close()


SECTION_HEADING_SELECTOR = "h2.text-xl.font-semibold.text-foreground"


# ---------------------------------------------------------------
# Scenario 1: Visitor sees the new section order on /resources
# ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario1NewSectionOrder:
    """Visitor sees the new section order on /resources."""

    def test_section_headings_render_in_canonical_order(
        self, django_server, page
    ):
        """When at least one published link exists in each of the four
        new categories, /resources renders the section headings in this
        exact order: Workshops, Courses, Articles, Other."""
        _clear_curated_links()
        _create_curated_link(
            title="WS Card", category="workshops",
            url="https://example.com/ws", sort_order=1,
        )
        _create_curated_link(
            title="CO Card", category="courses",
            url="https://example.com/co", sort_order=1,
        )
        _create_curated_link(
            title="AR Card", category="articles",
            url="https://example.com/ar", sort_order=1,
        )
        _create_curated_link(
            title="OT Card", category="other",
            url="https://example.com/ot", sort_order=1,
        )

        page.goto(
            f"{django_server}/resources",
            wait_until="domcontentloaded",
        )

        # Read all rendered section headings in DOM order.
        headings = page.locator(SECTION_HEADING_SELECTOR)
        rendered = [headings.nth(i).inner_text() for i in range(headings.count())]
        assert rendered == ["Workshops", "Courses", "Articles", "Other"]

        # Tools / Models section headings must not appear.
        assert "Tools" not in rendered
        assert "Models" not in rendered


# ---------------------------------------------------------------
# Scenario 2: Curated workshop appears in the Workshops section
# ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario2WorkshopInWorkshopsSection:
    """Visitor finds a curated workshop in the Workshops section."""

    def test_workshop_card_renders_under_workshops_with_graduation_icon(
        self, django_server, page
    ):
        _clear_curated_links()
        _create_curated_link(
            title="Build an LLM agent in a weekend",
            description="Hands-on workshop on shipping LLM agents.",
            url="https://example.com/agent-weekend",
            category="workshops",
            required_level=0,
            sort_order=1,
        )

        page.goto(
            f"{django_server}/resources",
            wait_until="domcontentloaded",
        )

        # Section heading present
        ws_heading = page.locator(
            f"{SECTION_HEADING_SELECTOR}:has-text('Workshops')"
        )
        assert ws_heading.count() == 1

        # Card is present and is an <a> opening in a new tab to the URL
        card = page.locator(
            'a:has-text("Build an LLM agent in a weekend")'
        ).first
        assert card.get_attribute("target") == "_blank"
        assert card.get_attribute("href") == "https://example.com/agent-weekend"

        # Badge shows the Workshops label and graduation-cap icon
        badge_icon = card.locator('[data-lucide="graduation-cap"]')
        assert badge_icon.count() >= 1
        # Badge label text is "Workshops"
        assert "Workshops" in card.inner_text()


# ---------------------------------------------------------------
# Scenario 3: Curated article appears in the Articles section
# ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario3ArticleInArticlesSection:
    """Visitor finds a curated article in the Articles section."""

    def test_article_card_renders_under_articles_with_file_text_icon(
        self, django_server, page
    ):
        _clear_curated_links()
        _create_curated_link(
            title="Why your RAG pipeline keeps lying",
            description="A deep dive on RAG failure modes.",
            url="https://example.com/rag-lies",
            category="articles",
            required_level=0,
            sort_order=1,
        )

        page.goto(
            f"{django_server}/resources",
            wait_until="domcontentloaded",
        )

        ar_heading = page.locator(
            f"{SECTION_HEADING_SELECTOR}:has-text('Articles')"
        )
        assert ar_heading.count() == 1

        card = page.locator(
            'a:has-text("Why your RAG pipeline keeps lying")'
        ).first
        assert card.get_attribute("href") == "https://example.com/rag-lies"

        # Badge file-text icon present
        file_text_icon = card.locator('[data-lucide="file-text"]')
        assert file_text_icon.count() >= 1
        assert "Articles" in card.inner_text()


# ---------------------------------------------------------------
# Scenario 4: Legacy tools and models fold under Other
# ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario4LegacyToolsModelsFoldIntoOther:
    """Legacy tools and models curated links still appear, folded
    under Other."""

    def test_legacy_links_appear_in_other_section_only(
        self, django_server, page
    ):
        _clear_curated_links()
        _create_curated_link(
            title="ripgrep",
            description="Recursive search tool.",
            url="https://example.com/rg",
            category="tools",
            sort_order=1,
        )
        _create_curated_link(
            title="Llama 3",
            description="Open-weight LLM family.",
            url="https://example.com/llama-3",
            category="models",
            sort_order=2,
        )
        _create_curated_link(
            title="Common Crawl",
            description="Open web crawl data.",
            url="https://example.com/cc",
            category="other",
            sort_order=3,
        )

        page.goto(
            f"{django_server}/resources",
            wait_until="domcontentloaded",
        )

        # Only the Other section heading is rendered
        headings = page.locator(SECTION_HEADING_SELECTOR)
        rendered = [headings.nth(i).inner_text() for i in range(headings.count())]
        assert rendered == ["Other"]

        # All three cards are present
        body = page.content()
        assert "ripgrep" in body
        assert "Llama 3" in body
        assert "Common Crawl" in body


# ---------------------------------------------------------------
# Scenario 5: Empty categories do not render headings
# ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario5EmptyCategoriesNotRendered:
    """Only categories with at least one published link render their
    heading."""

    def test_only_courses_heading_appears_when_only_courses_exist(
        self, django_server, page
    ):
        _clear_curated_links()
        _create_curated_link(
            title="Solo Course",
            description="Only course.",
            url="https://example.com/solo",
            category="courses",
            sort_order=1,
        )

        page.goto(
            f"{django_server}/resources",
            wait_until="domcontentloaded",
        )

        headings = page.locator(SECTION_HEADING_SELECTOR)
        rendered = [headings.nth(i).inner_text() for i in range(headings.count())]
        assert rendered == ["Courses"]


# ---------------------------------------------------------------
# Scenario 6: Free user hits upgrade CTA on a gated curated workshop
# ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario6FreeUserGatedWorkshopUpgradeCTA:
    """A free member sees a lock + upgrade CTA on a Basic-gated workshop
    and `View Plans` lands on /pricing."""

    def test_gated_workshop_shows_upgrade_cta(
        self, django_server, browser
    ):
        _clear_curated_links()
        _create_user("free-524@test.com", tier_slug="free")
        _create_curated_link(
            title="Advanced agent evals",
            description="Advanced evals for agentic workflows.",
            url="https://example.com/advanced-agent-evals-secret",
            category="workshops",
            required_level=10,  # Basic tier
            sort_order=1,
        )

        context = _auth_context(browser, "free-524@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/resources",
            wait_until="domcontentloaded",
        )

        body = page.content()
        # URL is hidden from the DOM
        assert "advanced-agent-evals-secret" not in body
        # Title is present
        assert "Advanced agent evals" in body

        gated_card = page.locator(
            '.gated-link:has-text("Advanced agent evals")'
        )
        # Lock icon appears
        lock_icons = gated_card.locator('[data-lucide="lock"]')
        assert lock_icons.count() >= 1

        # Click the gated card to reveal CTA
        gated_card.click()
        cta = gated_card.locator(".gated-cta")
        cta.wait_for(state="visible", timeout=3000)
        assert "Upgrade to Basic to access this resource" in cta.inner_text()

        # `View Plans` link
        view_plans = cta.locator('a:has-text("View Plans")')
        assert view_plans.count() >= 1
        view_plans.first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/pricing" in page.url


# ---------------------------------------------------------------
# Scenario 7: Tag filter works across the new section grouping
# ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario7TagFilterAcrossNewSections:
    """Tag filter narrows results across all four section groupings."""

    def test_filter_by_tag_keeps_matching_sections(
        self, django_server, page
    ):
        _clear_curated_links()
        _create_curated_link(
            title="Agents Workshop",
            url="https://example.com/agents-ws",
            category="workshops",
            tags=["agents"],
            sort_order=1,
        )
        _create_curated_link(
            title="Agents Article",
            url="https://example.com/agents-ar",
            category="articles",
            tags=["agents"],
            sort_order=1,
        )
        _create_curated_link(
            title="MLOps Course",
            url="https://example.com/mlops-co",
            category="courses",
            tags=["mlops"],
            sort_order=1,
        )

        # Filtered view
        page.goto(
            f"{django_server}/resources?tag=agents",
            wait_until="domcontentloaded",
        )
        headings = page.locator(SECTION_HEADING_SELECTOR)
        rendered = [headings.nth(i).inner_text() for i in range(headings.count())]
        # Only Workshops and Articles render — Courses has no `agents`
        # match. Order is preserved.
        assert rendered == ["Workshops", "Articles"]

        body = page.content()
        assert "Agents Workshop" in body
        assert "Agents Article" in body
        # MLOps card not visible inside link cards
        link_cards = page.locator('.gated-link, a[target="_blank"]')
        cards_text = " ".join(
            [link_cards.nth(i).inner_text() for i in range(link_cards.count())]
        )
        assert "MLOps Course" not in cards_text

        # Clear the filter (navigate back to /resources)
        page.goto(
            f"{django_server}/resources",
            wait_until="domcontentloaded",
        )
        headings = page.locator(SECTION_HEADING_SELECTOR)
        rendered = [headings.nth(i).inner_text() for i in range(headings.count())]
        assert rendered == ["Workshops", "Courses", "Articles"]


# ---------------------------------------------------------------
# Scenario 8: Page header copy reflects the new grouping
# ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario8HeaderCopyReflectsNewGrouping:
    """The /resources page <h1> and intro paragraph use the new copy."""

    def test_h1_and_intro_use_new_copy(self, django_server, page):
        _clear_curated_links()
        _create_curated_link(
            title="Anchor Workshop",
            url="https://example.com/anchor",
            category="workshops",
            sort_order=1,
        )

        page.goto(
            f"{django_server}/resources",
            wait_until="domcontentloaded",
        )
        h1 = page.locator("h1")
        h1_text = h1.inner_text()

        # New copy
        assert "Workshops, Courses & More" in h1_text
        # Old copy is gone
        assert "Tools, Models & Courses" not in h1_text

        # Intro paragraph mentions workshops and articles
        body = page.content()
        assert "workshops" in body
        assert "articles" in body
        # Old "GitHub repos, model hubs, and learning resources" copy is gone.
        assert (
            "Curated links to GitHub repos, model hubs, and learning resources."
            not in body
        )
