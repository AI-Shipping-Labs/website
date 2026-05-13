"""Issue #544 public catalog listing card screenshots.

Screenshots are written to ``/tmp/aisl-issue-544-screenshots`` for manual
review across the requested themes and viewports.
"""

import datetime
import os
from pathlib import Path

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path("/tmp/aisl-issue-544-screenshots")
DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 393, "height": 851}


def _seed_catalog_content():
    from django.db import connection
    from django.utils import timezone

    from content.models import Article, Course, CuratedLink, Download, Workshop
    from events.models import Event

    Article.objects.all().delete()
    Course.objects.all().delete()
    CuratedLink.objects.all().delete()
    Download.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()

    many_tags = ["agents", "rag", "python", "evaluation", "deployment"]
    Course.objects.create(
        title="Production AI Agents Course",
        slug="production-ai-agents-course",
        description="Build production-ready AI agents with tests and observability.",
        status="published",
        tags=many_tags,
    )
    Workshop.objects.create(
        title="Agent Evaluation Workshop",
        slug="agent-evaluation-workshop",
        description="A practical workshop on evaluating agent behavior.",
        date=datetime.date(2026, 4, 21),
        status="published",
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
        tags=many_tags,
    )
    Article.objects.create(
        title="Designing Agent Feedback Loops",
        slug="designing-agent-feedback-loops",
        description="Patterns for making agent systems easier to inspect.",
        content_markdown="# Designing Agent Feedback Loops",
        author="AI Shipping Labs",
        date=datetime.date(2026, 1, 3),
        published=True,
        tags=many_tags,
    )
    Download.objects.create(
        title="Agent Evaluation Checklist",
        slug="agent-evaluation-checklist",
        description="A compact PDF checklist for reviewing agent behavior.",
        file_url="https://example.com/checklist.pdf",
        file_type="pdf",
        file_size_bytes=262144,
        required_level=0,
        tags=many_tags,
        published=True,
    )
    CuratedLink.objects.create(
        item_id="agent-evaluation-resource",
        title="Agent Evaluation Resource",
        url="https://example.com/resource",
        description="A curated external resource for evaluation workflows.",
        category="tools",
        source="Example",
        required_level=0,
        tags=many_tags,
        published=True,
    )
    Event.objects.create(
        title="Agent Evaluation Recording",
        slug="agent-evaluation-recording",
        description="A recorded session about evaluating agent workflows.",
        start_datetime=timezone.now() - datetime.timedelta(days=14),
        status="completed",
        recording_url="https://youtu.be/example",
        tags=many_tags,
        published=True,
    )
    connection.close()


def _set_theme(context, theme):
    context.add_init_script(
        f"""
            localStorage.setItem('theme', '{theme}');
            document.documentElement.classList.toggle('dark', '{theme}' === 'dark');
        """
    )


def _doc_overflow(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth - "
        "document.documentElement.clientWidth"
    )


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _open_page(browser, base_url, path, viewport, theme):
    context = browser.new_context(viewport=viewport)
    _set_theme(context, theme)
    page = context.new_page()
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    has_dark_class = page.evaluate(
        "() => document.documentElement.classList.contains('dark')"
    )
    assert has_dark_class is (theme == "dark")
    return context, page


@pytest.mark.django_db(transaction=True)
def test_public_catalog_cards_default_ci_smoke(
    django_server, browser,
):
    _seed_catalog_content()

    routes = [
        ("/courses", "Production AI Agents Course"),
        ("/workshops", "Agent Evaluation Workshop"),
        ("/blog", "Designing Agent Feedback Loops"),
        ("/downloads", "Agent Evaluation Checklist"),
        ("/resources", "Agent Evaluation Resource"),
        ("/events?filter=past", "Agent Evaluation Recording"),
        ("/blog?tag=agents", "Designing Agent Feedback Loops"),
    ]

    for path, text in routes:
        context, page = _open_page(browser, django_server, path, DESKTOP, "light")
        try:
            page.get_by_text(text).first.wait_for()
            assert _doc_overflow(page) <= 1
        finally:
            context.close()


@pytest.mark.manual_visual
@pytest.mark.django_db(transaction=True)
def test_public_catalog_cards_have_consistent_density_screenshots(
    django_server, browser,
):
    _seed_catalog_content()

    routes = [
        ("courses", "/courses", "Production AI Agents Course"),
        ("workshops", "/workshops", "Agent Evaluation Workshop"),
        ("blog", "/blog", "Designing Agent Feedback Loops"),
        ("downloads", "/downloads", "Agent Evaluation Checklist"),
        ("resources", "/resources", "Agent Evaluation Resource"),
        ("events-past", "/events?filter=past", "Agent Evaluation Recording"),
        ("blog-filtered-agents", "/blog?tag=agents", "Designing Agent Feedback Loops"),
    ]

    for label, path, text in routes:
        for theme in ("light", "dark"):
            context, page = _open_page(browser, django_server, path, DESKTOP, theme)
            try:
                page.get_by_text(text).first.wait_for()
                assert page.get_by_text("+2").count() >= 1
                assert _doc_overflow(page) <= 1
                _capture(page, f"{label}-desktop-{theme}-1280x900")
            finally:
                context.close()

            context, page = _open_page(browser, django_server, path, MOBILE, theme)
            try:
                page.get_by_text(text).first.wait_for()
                assert page.get_by_text("+2").count() >= 1
                assert _doc_overflow(page) <= 1
                _capture(page, f"{label}-mobile-{theme}-393x851")
            finally:
                context.close()
