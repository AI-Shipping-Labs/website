"""BDD Playwright coverage for issue #1177 related-content rails."""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_content():
    from content.models import Article, Course, Project, Tutorial, Workshop, WorkshopPage
    from events.models import Event

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    Article.objects.all().delete()
    Tutorial.objects.all().delete()
    Project.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _article(
    title,
    slug,
    *,
    tags=None,
    date=None,
    description="Public article summary.",
    content_markdown="# Article\n\nReadable article body.",
    required_level=0,
    published=True,
):
    from content.models import Article

    article = Article.objects.create(
        title=title,
        slug=slug,
        description=description,
        content_markdown=content_markdown,
        date=date or datetime.date(2026, 1, 1),
        tags=tags or [],
        required_level=required_level,
        published=published,
    )
    connection.close()
    return article


def _tutorial(
    title,
    slug,
    *,
    tags=None,
    date=None,
    description="Public tutorial summary.",
    content_html="<p>Readable tutorial body.</p>",
    required_level=0,
    published=True,
):
    from content.models import Tutorial

    tutorial = Tutorial.objects.create(
        title=title,
        slug=slug,
        description=description,
        content_html=content_html,
        date=date or datetime.date(2026, 1, 1),
        tags=tags or [],
        required_level=required_level,
        published=published,
    )
    connection.close()
    return tutorial


def _project(
    title,
    slug,
    *,
    tags=None,
    date=None,
    description="Public project summary.",
    content_markdown="# Project\n\nReadable project body.",
    required_level=0,
    published=True,
    status="published",
):
    from content.models import Project

    project = Project.objects.create(
        title=title,
        slug=slug,
        description=description,
        content_markdown=content_markdown,
        date=date or datetime.date(2026, 1, 1),
        tags=tags or [],
        required_level=required_level,
        published=published,
        status=status,
    )
    connection.close()
    return project


def _workshop(
    title,
    slug,
    *,
    tags=None,
    date=None,
    description="Workshop public summary.",
    pages_required_level=0,
    recording_required_level=0,
    status="published",
):
    from content.models import Workshop

    workshop = Workshop.objects.create(
        title=title,
        slug=slug,
        description=description,
        date=date or datetime.date(2026, 1, 1),
        tags=tags or [],
        landing_required_level=0,
        pages_required_level=pages_required_level,
        recording_required_level=recording_required_level,
        status=status,
    )
    connection.close()
    return workshop


def _course(
    title,
    slug,
    *,
    tags=None,
    description="Course public summary.",
    required_level=0,
    status="published",
):
    from content.models import Course

    course = Course.objects.create(
        title=title,
        slug=slug,
        description=description,
        tags=tags or [],
        required_level=required_level,
        status=status,
    )
    connection.close()
    return course


def _event(
    title,
    slug,
    *,
    tags=None,
    days_offset=-2,
    status="completed",
    published=True,
):
    from events.models import Event

    event = Event.objects.create(
        title=title,
        slug=slug,
        description="Event public summary.",
        start_datetime=timezone.now() + datetime.timedelta(days=days_offset),
        status=status,
        published=published,
        tags=tags or [],
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestRelatedContentRecommendations:
    @pytest.mark.core
    def test_anonymous_article_reader_clicks_ranked_internal_recommendation(
        self, django_server, page,
    ):
        _clear_content()
        _article(
            "Open Agents Article",
            "open-agents",
            tags=["agents", "mcp"],
            date=datetime.date(2026, 1, 1),
        )
        _article(
            "Related Blog Article",
            "related-blog",
            tags=["agents"],
            date=datetime.date(2026, 1, 2),
        )
        _workshop(
            "Agent Workshop",
            "agent-workshop",
            tags=["agents"],
            date=datetime.date(2026, 1, 3),
        )
        _project(
            "Top Ranked Agent Project",
            "top-ranked-agent-project",
            tags=["agents", "mcp"],
            date=datetime.date(2026, 1, 4),
        )

        page.goto(f"{django_server}/blog/open-agents", wait_until="domcontentloaded")

        rail = page.get_by_test_id("related-content-rail")
        assert rail.is_visible()
        cards = page.get_by_test_id("related-content-card")
        assert cards.count() == 3
        first_href = cards.first.get_attribute("href")
        assert first_href == "/projects/top-ranked-agent-project"

        cards.first.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith("/projects/top-ranked-agent-project")
        assert page.get_by_role(
            "heading", name="Top Ranked Agent Project", exact=True
        ).is_visible()

    @pytest.mark.core
    def test_gated_article_keeps_paywall_and_shows_safe_related_path(
        self, django_server, page,
    ):
        _clear_content()
        _ensure_tiers()
        _article(
            "Gated Agents Article",
            "gated-agents",
            tags=["agents"],
            description="Safe gated article summary.",
            content_markdown="# Gated\n\nSECRET PAID BODY",
            required_level=10,
        )
        _project(
            "Open Agents Project",
            "open-agents-project",
            tags=["agents"],
            description="Open project summary.",
            content_markdown="# Project\n\nOpen project body.",
        )

        page.goto(f"{django_server}/blog/gated-agents", wait_until="domcontentloaded")

        assert (
            "Create a free account or choose Basic to read this article"
            in page.content()
        )
        assert "SECRET PAID BODY" not in page.content()
        rail = page.get_by_test_id("related-content-rail")
        assert rail.is_visible()
        assert "Open Agents Project" in rail.inner_text()

        rail.get_by_text("Open Agents Project").click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith("/projects/open-agents-project")
        assert "Open project body" in page.content()

    @pytest.mark.core
    def test_free_member_sees_tutorial_related_tier_indicators_and_paid_gate(
        self, django_server, browser,
    ):
        _clear_content()
        _ensure_tiers()
        _create_user("free-related-1177@test.com", tier_slug="free")
        _tutorial(
            "Open Python Tutorial",
            "open-python-tutorial",
            tags=["python", "agents"],
            date=datetime.date(2026, 2, 1),
        )
        _article(
            "Paid Agents Article",
            "paid-agents-article",
            tags=["agents"],
            date=datetime.date(2026, 2, 3),
            required_level=10,
        )
        _project(
            "Open Python Project",
            "open-python-project",
            tags=["python"],
            date=datetime.date(2026, 2, 2),
        )

        context = _auth_context(browser, "free-related-1177@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/tutorials/open-python-tutorial",
            wait_until="domcontentloaded",
        )

        paid_card = page.get_by_test_id("related-content-card").filter(
            has_text="Paid Agents Article"
        )
        open_card = page.get_by_test_id("related-content-card").filter(
            has_text="Open Python Project"
        )
        assert paid_card.get_by_test_id("related-content-type").inner_text() == "Article"
        assert "Basic or above" in paid_card.get_by_test_id(
            "related-content-tier"
        ).inner_text()
        assert open_card.get_by_test_id("related-content-tier").count() == 0

        paid_card.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith("/blog/paid-agents-article")
        body = page.content()
        assert "Upgrade to Basic to read this article" in body
        assert "Create a free account" not in body
        context.close()

    def test_project_recommends_related_workshop(self, django_server, page):
        _clear_content()
        _project("RAG Project", "rag-project", tags=["rag"])
        _workshop("RAG Workshop", "rag-workshop", tags=["rag"])

        page.goto(f"{django_server}/projects/rag-project", wait_until="domcontentloaded")

        card = page.get_by_test_id("related-content-card").filter(
            has_text="RAG Workshop"
        )
        assert card.count() == 1
        assert card.get_attribute("href") == "/workshops/rag-workshop"

        card.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith("/workshops/rag-workshop")
        assert page.get_by_test_id("workshop-title").inner_text() == "RAG Workshop"

    def test_workshop_landing_keeps_visitors_in_content_loop(
        self, django_server, page,
    ):
        _clear_content()
        _workshop("Evaluation Workshop", "evaluation-workshop", tags=["evaluation"])
        _article(
            "Evaluation Article",
            "evaluation-article",
            tags=["evaluation"],
            date=datetime.date(2026, 3, 2),
        )
        _course("Evaluation Course", "evaluation-course", tags=["evaluation"])
        _event("Evaluation Event", "evaluation-event", tags=["evaluation"])

        page.goto(
            f"{django_server}/workshops/evaluation-workshop",
            wait_until="domcontentloaded",
        )

        rail = page.get_by_test_id("related-content-rail")
        assert rail.is_visible()
        assert page.locator(
            "[role='dialog']:not([data-testid='analytics-consent-panel'])"
        ).count() == 0
        article_card = page.get_by_test_id("related-content-card").filter(
            has_text="Evaluation Article"
        )
        article_card.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith("/blog/evaluation-article")

    def test_past_event_recommends_related_learning_material(
        self, django_server, page,
    ):
        _clear_content()
        event = _event("LlmOps Past Event", "llmops-past-event", tags=["llmops"])
        _workshop("LlmOps Workshop", "llmops-workshop", tags=["llmops"])
        _article("LlmOps Article", "llmops-article", tags=["llmops"])

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        rail = page.get_by_test_id("related-content-rail")
        assert rail.is_visible()
        workshop_card = page.get_by_test_id("related-content-card").filter(
            has_text="LlmOps Workshop"
        )
        assert workshop_card.count() == 1

        workshop_card.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith("/workshops/llmops-workshop")

    def test_untagged_content_falls_back_to_newest_internal_pages(
        self, django_server, page,
    ):
        _clear_content()
        _project("Untagged Project", "untagged-project", tags=[])
        _article(
            "Older Fallback Article",
            "older-fallback-article",
            date=datetime.date(2026, 4, 1),
        )
        _workshop(
            "Middle Fallback Workshop",
            "middle-fallback-workshop",
            date=datetime.date(2026, 4, 2),
        )
        _tutorial(
            "Newest Fallback Tutorial",
            "newest-fallback-tutorial",
            date=datetime.date(2026, 4, 3),
        )

        page.goto(
            f"{django_server}/projects/untagged-project",
            wait_until="domcontentloaded",
        )

        rail = page.get_by_test_id("related-content-rail")
        assert "More from AI Shipping Labs" in rail.inner_text()
        titles = page.get_by_test_id("related-content-title").all_inner_texts()
        assert titles == [
            "Newest Fallback Tutorial",
            "Middle Fallback Workshop",
            "Older Fallback Article",
        ]

    def test_unpublished_content_is_never_recommended(self, django_server, page):
        _clear_content()
        _article("RAG Article", "rag-article", tags=["rag"])
        _project("Published RAG Project", "published-rag-project", tags=["rag"])
        _tutorial(
            "Draft RAG Tutorial",
            "draft-rag-tutorial",
            tags=["rag"],
            published=False,
        )

        page.goto(f"{django_server}/blog/rag-article", wait_until="domcontentloaded")

        rail = page.get_by_test_id("related-content-rail")
        assert "Published RAG Project" in rail.inner_text()
        assert "Draft RAG Tutorial" not in rail.inner_text()

    def test_minimal_database_does_not_render_empty_rail(self, django_server, page):
        _clear_content()
        _article("Only Public Article", "only-public-article", tags=["agents"])

        page.goto(
            f"{django_server}/blog/only-public-article",
            wait_until="domcontentloaded",
        )

        assert page.get_by_test_id("related-content-rail").count() == 0
        assert page.get_by_role(
            "heading", name="Only Public Article", exact=True
        ).is_visible()
