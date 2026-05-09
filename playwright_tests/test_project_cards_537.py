"""Issue #537 project listing card scanability checks.

Screenshots are written to ``/tmp/aisl-issue-537-screenshots`` for manual
review across the requested themes and viewports.
"""

import datetime
import os
from pathlib import Path

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path("/tmp/aisl-issue-537-screenshots")
DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 393, "height": 851}


def _seed_projects():
    from django.db import connection

    from content.models import Project

    Project.objects.all().delete()
    Project.objects.create(
        title="Official Agent Marketplace",
        slug="official-agent-marketplace",
        description="A first-party idea with several topic cues.",
        date=datetime.date(2026, 1, 1),
        author="AI Shipping Labs",
        difficulty="intermediate",
        tags=["agents", "marketplace", "rag", "evaluation", "deployment", "python"],
        published=True,
    )
    Project.objects.create(
        title="Alexey Long Title Project For Responsive Wrapping In The Card Grid",
        slug="alexey-long-title-project",
        description="Another first-party project with a long title.",
        date=datetime.date(2026, 1, 2),
        author="Alexey Grigorev",
        difficulty="advanced",
        tags=["automation", "llms", "github", "workflows"],
        required_level=10,
        published=True,
    )
    Project.objects.create(
        title="Community Habit Builder",
        slug="community-habit-builder",
        description="A member-submitted project idea.",
        date=datetime.date(2026, 1, 3),
        author="Community Member With A Long Display Name",
        difficulty="beginner",
        tags=["habits", "productivity"],
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


def _assert_theme(page, theme):
    has_dark_class = page.evaluate(
        "() => document.documentElement.classList.contains('dark')"
    )
    assert has_dark_class is (theme == "dark")


def _open_projects(browser, base_url, viewport, theme, path="/projects"):
    context = browser.new_context(viewport=viewport)
    _set_theme(context, theme)
    page = context.new_page()
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    _assert_theme(page, theme)
    page.locator("h1").filter(has_text="Pet & Portfolio Project Ideas").wait_for()
    return context, page


@pytest.mark.django_db(transaction=True)
def test_project_listing_cards_are_compact_and_differentiated(
    django_server, browser,
):
    _seed_projects()

    context, page = _open_projects(browser, django_server, DESKTOP, "light")
    try:
        official_card_tags = page.locator(
            'article:has(a[href="/projects/official-agent-marketplace"]) '
            '[data-testid="project-card-tags"]'
        )
        assert official_card_tags.inner_text().splitlines() == [
            "agents",
            "marketplace",
            "rag",
            "+3",
        ]
        assert page.locator('[data-testid="project-official-badge"]').count() == 2
        assert page.locator('[data-lucide="arrow-right"]').first.is_visible()
        assert page.locator("text=Basic or above").count() == 1
        assert _doc_overflow(page) <= 1
        _capture(page, "projects-desktop-light-1280x900")
    finally:
        context.close()

    for theme in ("dark",):
        context, page = _open_projects(browser, django_server, DESKTOP, theme)
        try:
            assert _doc_overflow(page) <= 1
            _capture(page, f"projects-desktop-{theme}-1280x900")
        finally:
            context.close()

    for theme in ("light", "dark"):
        context, page = _open_projects(browser, django_server, MOBILE, theme)
        try:
            assert _doc_overflow(page) <= 1
            assert page.locator('[data-lucide="arrow-right"]').first.is_hidden()
            _capture(page, f"projects-mobile-{theme}-393x851")
        finally:
            context.close()

    context, page = _open_projects(
        browser,
        django_server,
        DESKTOP,
        "light",
        path="/projects?difficulty=intermediate",
    )
    try:
        assert page.locator("text=Official Agent Marketplace").count() == 1
        assert page.locator("text=Community Habit Builder").count() == 0
        _capture(page, "projects-filtered-intermediate-light-1280x900")
    finally:
        context.close()
