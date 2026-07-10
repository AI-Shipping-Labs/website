"""Browser-level metadata coverage for SEO descriptions (issue #1174)."""

import datetime
import os

import pytest

from playwright_tests.conftest import goto_with_retry

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

pytestmark = pytest.mark.local_only


def _clear_seed_data():
    from content.models import Article, Course, Project, Tutorial, Workshop
    from events.models import Event

    Workshop.objects.all().delete()
    Article.objects.all().delete()
    Tutorial.objects.all().delete()
    Project.objects.all().delete()
    Course.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _seed_metadata_content():
    from content.models import (
        Article,
        Course,
        Module,
        Project,
        Tutorial,
        Unit,
        Workshop,
        WorkshopPage,
    )
    from events.models import Event

    _clear_seed_data()

    article = Article.objects.create(
        title="SEO Metadata Article",
        slug="seo-metadata-article-1174",
        description=(
            "Readable **article** snippet with [links](https://example.com) "
            "and <span>HTML</span>."
        ),
        content_markdown="# SEO Metadata Article\n\nArticle body.",
        date=datetime.date(2026, 7, 1),
        published=True,
        required_level=0,
    )
    tutorial = Tutorial.objects.create(
        title="Standalone SEO Tutorial",
        slug="standalone-seo-tutorial-1174",
        description="A focused tutorial snippet for public crawler metadata.",
        content_markdown="# Standalone SEO Tutorial\n\nTutorial body.",
        date=datetime.date(2026, 7, 2),
        published=True,
        required_level=0,
    )
    project = Project.objects.create(
        title="Sparse SEO Project",
        slug="sparse-seo-project-1174",
        description="",
        content_markdown="",
        date=datetime.date(2026, 7, 3),
        published=True,
        required_level=0,
    )
    course = Course.objects.create(
        title="SEO Metadata Course",
        slug="seo-metadata-course-1174",
        description="Learn crawler-friendly descriptions for course pages.",
        status="published",
    )
    module = Module.objects.create(
        course=course,
        title="Metadata Module",
        slug="metadata-module",
        sort_order=1,
        overview="# Metadata Module\n\nPlan clean overview snippets.",
    )
    unit = Unit.objects.create(
        module=module,
        title="Metadata Lesson",
        slug="metadata-lesson",
        sort_order=1,
        body=(
            "# Metadata Lesson\n\n"
            "```python\nprint('skip raw code')\n```\n"
            "Turn lesson prose into a clean search result snippet."
        ),
    )
    event = Event.objects.create(
        title="SEO Metadata Event",
        slug="seo-metadata-event-1174",
        description="A **live** session about useful search previews.",
        # date-rot-ok: fixed UTC instant verifies exact timezone-strip metadata copy.
        start_datetime=datetime.datetime(
            2026,
            5,
            21,
            14,
            0,
            tzinfo=datetime.UTC,
        ),
        status="completed",
        recording_url="https://www.youtube.com/watch?v=abc1174",
        published=True,
        required_level=0,
    )
    workshop = Workshop.objects.create(
        title="SEO Metadata Workshop",
        slug="seo-metadata-workshop-1174",
        description="A workshop on public metadata for AI learning pages.",
        date=datetime.date(2026, 7, 4),
        status="published",
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        event=event,
    )
    first_page = WorkshopPage.objects.create(
        workshop=workshop,
        title="First Metadata Page",
        slug="first-metadata-page",
        sort_order=1,
        body="# First Metadata Page\n\nWrite a distinct first-page snippet.",
    )
    second_page = WorkshopPage.objects.create(
        workshop=workshop,
        title="Second Metadata Page",
        slug="second-metadata-page",
        sort_order=2,
        body="Compare a second tutorial page with different crawler copy.",
    )
    connection.close()
    return {
        "article": article.get_absolute_url(),
        "tutorial": tutorial.get_absolute_url(),
        "project": project.get_absolute_url(),
        "course": course.get_absolute_url(),
        "module": module.get_absolute_url(),
        "unit": unit.get_absolute_url(),
        "event": event.get_absolute_url(),
        "workshop": workshop.get_absolute_url(),
        "workshop_video": f"{workshop.get_absolute_url()}/video",
        "workshop_first_page": first_page.get_absolute_url(),
        "workshop_second_page": second_page.get_absolute_url(),
    }


def _meta(page, selector):
    return page.locator(selector).first.get_attribute("content") or ""


def _canonical_href(page):
    return page.locator('link[rel="canonical"]').first.get_attribute("href") or ""


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_public_metadata_descriptions_are_content_specific(django_server, page):
    urls = _seed_metadata_content()

    goto_with_retry(page, f"{django_server}{urls['workshop']}")
    workshop_description = _meta(page, 'meta[name="description"]')
    assert workshop_description
    assert len(workshop_description) <= 160
    assert "workshop on public metadata" in workshop_description
    assert _meta(page, 'meta[property="og:description"]') == workshop_description
    assert _canonical_href(page).endswith(urls["workshop"])

    goto_with_retry(page, f"{django_server}{urls['workshop_first_page']}")
    first_description = _meta(page, 'meta[name="description"]')
    assert "First Metadata Page in SEO Metadata Workshop" in first_description
    assert "distinct first-page snippet" in first_description
    assert _meta(page, 'meta[property="og:title"]') == (
        "First Metadata Page | SEO Metadata Workshop"
    )
    assert _meta(page, 'meta[property="og:url"]').endswith(
        urls["workshop_first_page"]
    )

    goto_with_retry(page, f"{django_server}{urls['workshop_second_page']}")
    second_description = _meta(page, 'meta[name="description"]')
    assert "Second Metadata Page in SEO Metadata Workshop" in second_description
    assert "different crawler copy" in second_description
    assert second_description != first_description

    goto_with_retry(page, f"{django_server}{urls['workshop_video']}")
    video_description = _meta(page, 'meta[name="description"]')
    assert video_description.startswith("Recording for SEO Metadata Workshop:")
    assert _meta(page, 'meta[property="og:title"]') == (
        "SEO Metadata Workshop - Recording"
    )
    assert _meta(page, 'meta[property="og:url"]').endswith(
        urls["workshop_video"]
    )
    assert _canonical_href(page).endswith(urls["workshop_video"])

    goto_with_retry(page, f"{django_server}{urls['article']}")
    article_description = _meta(page, 'meta[name="description"]')
    assert article_description == "Readable article snippet with links and HTML."
    assert " **" not in article_description
    assert "<span>" not in article_description

    goto_with_retry(page, f"{django_server}{urls['unit']}")
    unit_description = _meta(page, 'meta[name="description"]')
    assert "Turn lesson prose into a clean search result snippet." in unit_description
    assert "```" not in unit_description
    assert "skip raw code" not in unit_description

    goto_with_retry(page, f"{django_server}{urls['event']}")
    event_description = _meta(page, 'meta[name="description"]')
    assert event_description.startswith(
        "Thu, May 21 · 10:00 NYC · 14:00 UTC · 16:00 CET · 19:30 IST"
    )
    assert len(event_description) <= 200
    assert _meta(page, 'meta[property="og:description"]') == event_description
    assert _meta(page, 'meta[name="twitter:description"]') == event_description

    for key in ("tutorial", "project", "course", "module"):
        goto_with_retry(page, f"{django_server}{urls[key]}")
        description = _meta(page, 'meta[name="description"]')
        assert description, key
        assert len(description) <= 160
