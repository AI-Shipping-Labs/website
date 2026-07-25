"""Issue #1317 — Unspecified difficulty facet on /projects.

An empty-difficulty project is reachable in the default list but had no
facet pill. These flows verify the reserved ?difficulty=unspecified pill
surfaces, isolates, and returns from the empty-difficulty cohort.

Seeds Projects via Project.objects.create, so it is local_only and cannot
run against the deployed dev environment.
"""

import datetime
import os

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _seed_mixed():
    """Beginner/intermediate/advanced plus one unrated project."""
    from django.db import connection

    from content.models import Project

    Project.objects.all().delete()
    Project.objects.create(
        title="Beginner Rated",
        slug="beginner-rated-1317",
        description="A rated beginner project.",
        date=datetime.date(2026, 1, 1),
        difficulty="beginner",
        tags=["python"],
        published=True,
    )
    Project.objects.create(
        title="Intermediate Rated",
        slug="intermediate-rated-1317",
        description="A rated intermediate project.",
        date=datetime.date(2026, 1, 2),
        difficulty="intermediate",
        tags=["ai"],
        published=True,
    )
    Project.objects.create(
        title="Advanced Rated",
        slug="advanced-rated-1317",
        description="A rated advanced project.",
        date=datetime.date(2026, 1, 3),
        difficulty="advanced",
        tags=["agents"],
        published=True,
    )
    Project.objects.create(
        title="Unrated Showcase",
        slug="unrated-showcase-1317",
        description="A project saved without a difficulty.",
        date=datetime.date(2026, 1, 4),
        difficulty="",
        tags=["python"],
        published=True,
    )
    connection.close()


def _seed_all_rated():
    from django.db import connection

    from content.models import Project

    Project.objects.all().delete()
    Project.objects.create(
        title="Beginner Rated",
        slug="beginner-rated-1317",
        description="A rated beginner project.",
        date=datetime.date(2026, 1, 1),
        difficulty="beginner",
        tags=["python"],
        published=True,
    )
    Project.objects.create(
        title="Advanced Rated",
        slug="advanced-rated-1317",
        description="A rated advanced project.",
        date=datetime.date(2026, 1, 3),
        difficulty="advanced",
        tags=["agents"],
        published=True,
    )
    connection.close()


def _open(browser, base_url, path="/projects"):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    page.locator("h1").filter(has_text="Pet & Portfolio Project Ideas").wait_for()
    return context, page


@pytest.mark.django_db(transaction=True)
def test_visitor_discovers_and_isolates_unrated_projects(django_server, browser):
    _seed_mixed()
    context, page = _open(browser, django_server)
    try:
        # The difficulty facet shows all three rated pills plus Unspecified.
        for testid in (
            "project-difficulty-beginner",
            "project-difficulty-intermediate",
            "project-difficulty-advanced",
            "project-difficulty-unspecified",
        ):
            assert page.locator(f'[data-testid="{testid}"]').count() == 1, testid

        page.locator('[data-testid="project-difficulty-unspecified"]').click()
        page.wait_for_load_state("networkidle")

        # Only the unrated project remains.
        assert page.locator("text=Unrated Showcase").count() == 1
        assert page.locator("text=Beginner Rated").count() == 0
        assert page.locator("text=Intermediate Rated").count() == 0
        assert page.locator("text=Advanced Rated").count() == 0

        pill = page.locator('[data-testid="project-difficulty-unspecified"]')
        assert pill.get_attribute("aria-current") == "page"
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_visitor_returns_from_unspecified_to_full_catalog(django_server, browser):
    _seed_mixed()
    context, page = _open(
        browser, django_server, path="/projects?difficulty=unspecified"
    )
    try:
        assert page.locator("text=Unrated Showcase").count() == 1
        assert page.locator("text=Beginner Rated").count() == 0

        page.locator('[data-testid="project-difficulty-clear"]').click()
        page.wait_for_load_state("networkidle")

        assert page.locator("text=Unrated Showcase").count() == 1
        assert page.locator("text=Beginner Rated").count() == 1
        assert page.locator("text=Advanced Rated").count() == 1
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_facet_hides_unspecified_when_all_rated(django_server, browser):
    _seed_all_rated()
    context, page = _open(browser, django_server)
    try:
        assert page.locator('[data-testid="project-difficulty-beginner"]').count() == 1
        assert page.locator('[data-testid="project-difficulty-advanced"]').count() == 1
        assert (
            page.locator('[data-testid="project-difficulty-unspecified"]').count() == 0
        )
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_empty_unspecified_view_shows_way_back(django_server, browser):
    _seed_all_rated()
    context, page = _open(
        browser, django_server, path="/projects?difficulty=unspecified"
    )
    try:
        assert page.locator("text=No projects match this difficulty").count() == 1
        cta = page.locator('a[href="/projects"]', has_text="View all projects")
        assert cta.count() >= 1
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_combine_unspecified_with_topic_filter(django_server, browser):
    _seed_mixed()
    context, page = _open(browser, django_server)
    try:
        page.locator('[data-testid="project-difficulty-unspecified"]').click()
        page.wait_for_load_state("networkidle")
        page.locator('[data-testid="project-tag-python"]').first.click()
        page.wait_for_load_state("networkidle")

        # Only the unrated python project remains.
        assert page.locator("text=Unrated Showcase").count() == 1
        assert page.locator("text=Beginner Rated").count() == 0

        diff_pill = page.locator('[data-testid="project-difficulty-unspecified"]')
        assert diff_pill.get_attribute("aria-current") == "page"
        tag_pill = page.locator('[data-testid="project-tag-python"]').first
        assert tag_pill.get_attribute("aria-current") == "page"
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_keyboard_user_can_operate_unspecified_pill(django_server, browser):
    _seed_mixed()
    context, page = _open(browser, django_server)
    try:
        pill = page.locator('[data-testid="project-difficulty-unspecified"]')
        pill.focus()
        focused = page.evaluate(
            "() => document.activeElement.getAttribute('data-testid')"
        )
        assert focused == "project-difficulty-unspecified"
        with page.expect_navigation():
            page.keyboard.press("Enter")
        page.wait_for_url("**/projects?difficulty=unspecified")
        assert page.url.endswith("/projects?difficulty=unspecified")
        assert page.locator("text=Unrated Showcase").count() == 1
    finally:
        context.close()
