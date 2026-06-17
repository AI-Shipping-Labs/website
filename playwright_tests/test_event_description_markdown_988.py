"""Playwright E2E for issue #988: event and event-series descriptions render
through the shared markdown renderer (markdown + linkify), matching
workshops/courses, instead of raw text.

Eight scenarios on the public event-detail and series pages:

1. Bullet list in an event description renders as a real <ul>.
2. Bare URL in an event description is a clickable new-tab anchor.
3. Bare URL in an event-series description is a clickable new-tab anchor.
4. Event-series description renders markdown formatting (heading/emphasis/list).
5. Markdown link in a description is not double-linked.
6. A <script> tag in a description does not execute.
7. Event and course descriptions render identically for the same markdown.
8. An empty description does not break the event page.

Usage:
    uv run pytest playwright_tests/test_event_description_markdown_988.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: this module seeds DB rows directly, so it cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear():
    from content.models import Course
    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    Course.objects.filter(slug__startswith="md988-").delete()
    connection.close()


def _create_event(*, slug, title, description, status="upcoming"):
    from events.models import Event

    event = Event.objects.create(
        slug=slug,
        title=title,
        description=description,
        start_datetime=timezone.now() + datetime.timedelta(days=7),
        status=status,
        required_level=0,
    )
    connection.close()
    return event


def _create_public_series(*, slug, name, description):
    """Create an EventSeries that is publicly visible (active + one published
    upcoming occurrence)."""
    from events.models import Event, EventSeries

    series = EventSeries.objects.create(
        name=name,
        slug=slug,
        description=description,
        is_active=True,
        start_time=datetime.time(18, 0),
    )
    Event.objects.create(
        slug=f"{slug}-session-1",
        title=f"{name} Session 1",
        start_datetime=timezone.now() + datetime.timedelta(days=7),
        status="upcoming",
        event_series=series,
        series_position=1,
        origin="studio",
        required_level=0,
    )
    connection.close()
    return series


# ---------------------------------------------------------------------------
# Scenario 1: bullet list renders as a real list
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
class TestScenarioBulletList:
    @pytest.mark.core
    def test_event_bullet_list_renders_as_ul(self, django_server, page):
        _clear()
        _ensure_tiers()
        event = _create_event(
            slug="md988-bullets",
            title="Cloudflare Vectorize",
            description=(
                "What we'll explore:\n\n"
                "- Vectorize indexes\n"
                "- Workers AI bindings\n"
                "- Deploying to the edge"
            ),
        )
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        prose = page.locator("div.prose")
        # The bullets render as distinct <li> entries inside a real <ul>.
        items = prose.locator("ul li")
        assert items.count() == 3
        texts = [items.nth(i).inner_text().strip() for i in range(3)]
        assert "Vectorize indexes" in texts
        assert "Workers AI bindings" in texts
        # The raw markdown dash-prefixed line is not shown verbatim.
        assert "- Vectorize indexes" not in prose.inner_text()


# ---------------------------------------------------------------------------
# Scenario 2: bare URL in an event description is a clickable link
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
class TestScenarioEventBareUrl:
    @pytest.mark.core
    def test_event_bare_url_is_new_tab_anchor(self, django_server, page):
        _clear()
        _ensure_tiers()
        url = "https://developers.cloudflare.com/vectorize/"
        event = _create_event(
            slug="md988-url",
            title="Bare URL Event",
            description=f"Reference material: {url}",
        )
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        link = page.locator(f'div.prose a[href="{url}"]')
        assert link.count() == 1
        assert link.first.get_attribute("target") == "_blank"
        rel = link.first.get_attribute("rel") or ""
        assert "noopener" in rel
        assert "noreferrer" in rel


# ---------------------------------------------------------------------------
# Scenario 3: bare URL in an event-series description is a clickable link
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
class TestScenarioSeriesBareUrl:
    @pytest.mark.core
    def test_series_bare_url_is_new_tab_anchor(self, django_server, page):
        _clear()
        _ensure_tiers()
        url = "https://github.com/DataTalksClub/llm-zoomcamp"
        series = _create_public_series(
            slug="md988-llm-office-hours",
            name="LLM Zoomcamp Office Hours",
            description=f"Course content: {url}",
        )
        resp = page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert resp.status == 200
        link = page.locator(f'div.prose a[href="{url}"]')
        assert link.count() == 1
        assert link.first.get_attribute("target") == "_blank"
        assert "noopener" in (link.first.get_attribute("rel") or "")


# ---------------------------------------------------------------------------
# Scenario 4: event-series description renders markdown formatting
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
class TestScenarioSeriesMarkdownFormatting:
    @pytest.mark.core
    def test_series_heading_emphasis_list(self, django_server, page):
        _clear()
        _ensure_tiers()
        series = _create_public_series(
            slug="md988-formatted",
            name="Formatted Series",
            description=(
                "## Weekly Plan\n\n"
                "Bring your *own* project.\n\n"
                "- Week one\n- Week two"
            ),
        )
        resp = page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert resp.status == 200
        prose = page.locator("div.prose")
        assert prose.locator("h2", has_text="Weekly Plan").count() == 1
        assert prose.locator("em", has_text="own").count() == 1
        assert prose.locator("ul li").count() == 2


# ---------------------------------------------------------------------------
# Scenario 5: markdown links are not double-linked
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
class TestScenarioNoDoubleLink:
    @pytest.mark.core
    def test_markdown_link_single_anchor(self, django_server, page):
        _clear()
        _ensure_tiers()
        url = "https://example.com/docs"
        event = _create_event(
            slug="md988-mdlink",
            title="Doc Link Event",
            description=f"Read [the docs]({url}) before joining.",
        )
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        prose = page.locator("div.prose")
        anchors = prose.locator(f'a[href="{url}"]')
        # Exactly one anchor with the label text; no nested/duplicate link.
        assert anchors.count() == 1
        assert anchors.first.inner_text().strip() == "the docs"
        # The raw URL is not separately rendered as visible text.
        assert url not in prose.inner_text()


# ---------------------------------------------------------------------------
# Scenario 6: a script tag does not execute
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
class TestScenarioScriptInert:
    @pytest.mark.core
    def test_script_does_not_execute(self, django_server, page):
        _clear()
        _ensure_tiers()
        event = _create_event(
            slug="md988-xss",
            title="XSS Event",
            description=(
                "Welcome!\n\n"
                "<script>window.__xss_ran = true;</script>\n\n"
                "- legitimate bullet"
            ),
        )

        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        # No injected script ran (the description script does not set the flag
        # in a way that affects the page, and no alert dialog appeared).
        ran = page.evaluate("() => window.__xss_ran === true ? 'ran' : 'inert'")
        assert ran == "inert"
        assert dialogs == []
        # Legitimate markdown still renders.
        prose = page.locator("div.prose")
        assert prose.locator("ul li", has_text="legitimate bullet").count() == 1


# ---------------------------------------------------------------------------
# Scenario 7: event and course render the same markdown identically
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
class TestScenarioEventCourseParity:
    @pytest.mark.core
    def test_event_and_course_render_same(self, django_server, page):
        from content.models import Course

        _clear()
        _ensure_tiers()
        source = (
            "## Agenda\n\n"
            "We will *ship* something.\n\n"
            "- Plan\n- Build\n\n"
            "Docs: https://example.com/agenda"
        )
        event = _create_event(
            slug="md988-parity-evt",
            title="Parity Event",
            description=source,
        )
        Course.objects.create(
            slug="md988-parity-course",
            title="Parity Course",
            description=source,
            status="published",
        )
        connection.close()

        # Event detail.
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        evt_prose = page.locator("div.prose").first
        assert evt_prose.locator("h2", has_text="Agenda").count() == 1
        assert evt_prose.locator("ul li").count() == 2
        assert evt_prose.locator(
            'a[href="https://example.com/agenda"]'
        ).count() == 1

        # Course detail renders the same heading, list, and linkified URL.
        page.goto(
            f"{django_server}/courses/md988-parity-course",
            wait_until="domcontentloaded",
        )
        course_prose = page.locator("div.prose").first
        assert course_prose.locator("h2", has_text="Agenda").count() == 1
        assert course_prose.locator("ul li").count() == 2
        assert course_prose.locator(
            'a[href="https://example.com/agenda"]'
        ).count() == 1


# ---------------------------------------------------------------------------
# Scenario 8: empty description does not break the event page
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
class TestScenarioEmptyDescription:
    @pytest.mark.core
    def test_empty_description_page_loads(self, django_server, page):
        _clear()
        _ensure_tiers()
        event = _create_event(
            slug="md988-empty",
            title="No Description Event",
            description="",
        )
        resp = page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert resp.status == 200
        # The page title/heading is present, confirming a clean render.
        assert page.locator("h1", has_text="No Description Event").count() >= 1
        # No stray raw markdown / empty description artifacts.
        assert page.locator("div.prose").count() == 0
