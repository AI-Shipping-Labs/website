"""Playwright coverage for the content include/widget merge (#1101)."""

import datetime
import os

import pytest
from django.utils import timezone

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_content():
    from content.models import Article, Workshop, WorkshopPage
    from events.models import Event

    Article.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_learning_path_article():
    from content.models import Article

    article = Article.objects.create(
        title="AI Engineer Learning Path",
        slug="ai-engineer-learning-path",
        description="A visual learning path for becoming an AI engineer.",
        content_markdown=(
            "## Learning Stages\n\n"
            "Python & LLM Foundations\n\n"
            "## Skills by Category\n\n"
            "GenAI Skills\n\n"
            "## Tools and Frameworks\n\n"
            "OpenAI API\n\n"
            "## Responsibilities\n\n"
            "Build AI Systems\n\n"
            "## Portfolio Projects\n\n"
            "Production RAG System\n"
        ),
        page_type="learning_path",
        data_json={
            "learning_stages": [
                {
                    "stage": "1",
                    "title": "Python & LLM Foundations",
                    "items": ["Python fluency"],
                }
            ]
        },
        date=datetime.date(2026, 1, 15),
        published=True,
    )
    connection.close()
    return article


def _create_regular_article():
    from content.models import Article

    article = Article.objects.create(
        title="Regular Blog Post",
        slug="regular-blog-post",
        description="Regular article description.",
        content_markdown="# Regular Blog Post\n\nNormal content.",
        page_type="blog",
        date=datetime.date(2026, 2, 1),
        published=True,
    )
    connection.close()
    return article


def _create_event_recap():
    from events.models import Event

    event = Event.objects.create(
        title="AI Shipping Labs Community Launch",
        slug="community-launch",
        start_datetime=timezone.now() - datetime.timedelta(days=2),
        status="completed",
        recap_html=(
            '<section id="watch-stream">'
            "<h2>Watch the recording</h2>"
            '<iframe src="https://www.youtube.com/embed/WQAs1LNxdvM"></iframe>'
            "</section>"
            "<section><h2>What you need to know</h2>"
            "<article><h3>Execution</h3><p>Ship real projects.</p></article>"
            "</section>"
        ),
    )
    connection.close()
    return event


def _create_workshop_with_rendered_include():
    from content.models import Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        slug="include-workshop",
        title="Include Workshop",
        description="Workshop description.",
        date=datetime.date(2026, 4, 21),
        status="published",
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
    )
    page = WorkshopPage.objects.create(
        workshop=workshop,
        slug="overview",
        title="Overview",
        sort_order=1,
        body="Before.\n\nIncluded workshop example.\n\nAfter.",
    )
    body_html = (
        "<p>Before.</p>"
        '<details class="example"><summary>Show example</summary>'
        "<p>Included workshop example.</p>"
        "</details>"
        "<p>After.</p>"
    )
    WorkshopPage.objects.filter(pk=page.pk).update(body_html=body_html)
    page.body_html = body_html
    connection.close()
    return page


@pytest.mark.django_db(transaction=True)
class TestIncludeMergeLearningPath:
    @pytest.mark.core
    def test_visitor_reads_migrated_learning_path(self, django_server, page):
        _clear_content()
        _create_learning_path_article()

        page.goto(
            f"{django_server}/blog/ai-engineer-learning-path",
            wait_until="domcontentloaded",
        )
        body = page.content()

        assert "AI Engineer Learning Path" in body
        assert "Python & LLM Foundations" in body
        assert "GenAI Skills" in body
        assert "OpenAI API" in body
        assert "Build AI Systems" in body
        assert "Production RAG System" in body
        assert "<!-- include:" not in body
        assert "<!-- widget:" not in body

        jsonld = page.locator('head script[type="application/ld+json"]')
        assert jsonld.count() >= 1
        assert '"@type": "Course"' in jsonld.first.inner_text()

    @pytest.mark.core
    def test_legacy_learning_path_url_redirects_to_blog_article(self, django_server, page):
        _clear_content()
        _create_learning_path_article()

        response = page.request.get(
            f"{django_server}/learning-path/ai-engineer",
            max_redirects=0,
        )
        assert response.status == 301
        assert response.headers["location"] == "/blog/ai-engineer-learning-path"

        page.goto(
            f"{django_server}/learning-path/ai-engineer",
            wait_until="domcontentloaded",
        )
        assert page.url.endswith("/blog/ai-engineer-learning-path")
        assert "AI Engineer Learning Path" in page.content()

    @pytest.mark.core
    def test_blog_list_excludes_learning_path_articles(self, django_server, page):
        _clear_content()
        _create_regular_article()
        _create_learning_path_article()

        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        body = page.content()

        assert "Regular Blog Post" in body
        assert "AI Engineer Learning Path" not in body


@pytest.mark.django_db(transaction=True)
class TestIncludeMergeRegressions:
    @pytest.mark.core
    def test_visitor_reads_existing_event_recap(self, django_server, page):
        _clear_content()
        event = _create_event_recap()

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        assert "AI Shipping Labs Community Launch" in body
        assert "Watch the recording" in body
        assert "Execution" in body
        assert "Ship real projects." in body
        assert "<!-- include:" not in body

    @pytest.mark.core
    def test_visitor_reads_workshop_page_with_rendered_include(self, django_server, page):
        _clear_content()
        workshop_page = _create_workshop_with_rendered_include()

        page.goto(
            f"{django_server}{workshop_page.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        assert "Include Workshop" in body
        assert "Included workshop example." in body
        assert '<details class="example">' in body
        assert "<!-- include:" not in body
