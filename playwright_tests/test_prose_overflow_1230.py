"""Browser regressions for locally scrollable rendered prose (#1230)."""

import datetime
import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

MOBILE = {"width": 393, "height": 851}
DESKTOP = {"width": 1280, "height": 900}

WIDE_TABLE = """| Topic | Foundation | Delivery | Operations | Ownership |
| --- | --- | --- | --- | --- |
| Python Fundamentals | Retrieval Augmented Generation | Evaluation Framework | Production Observability | Platform Engineering Enablement |
| Agent Architecture | Context Window Management | Continuous Integration | Incident Response Automation | Developer Experience Systems |
"""

WRAPPING_PROSE = (
    "This ordinary paragraph must keep wrapping naturally inside the reading "
    "column while the comparison table below retains intrinsic cell widths. "
    "The inline link https://example.com/a/very/long/path/that/must/wrap/inside/"
    "the/reading/column also must not widen the document.\n\n"
    "- This ordinary list item contains enough explanatory prose to wrap over "
    "several lines on a narrow mobile viewport without horizontal page scroll.\n\n"
)

WIDE_MERMAID = """```mermaid
flowchart LR
    Ingest[Ingest raw events] --> Validate[Validate incoming schema]
    Validate --> Enrich[Enrich with member profile]
    Enrich --> Score[Score with evaluation model]
    Score --> Route[Route the production request]
    Route --> Persist[Persist the final outcome]
    Persist --> Notify[Notify downstream systems]
    Notify --> Audit[Write the complete audit log]
```
"""


def _close_connection():
    from django.db import connection

    connection.close()


def _reset_content():
    from content.models import Article, Course, Project, Workshop, WorkshopPage

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Course.objects.all().delete()
    Project.objects.all().delete()
    Article.objects.all().delete()
    _close_connection()


def _assert_document_fits(page):
    sizes = page.evaluate(
        """() => ({
            scrollWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
        })"""
    )
    assert sizes["scrollWidth"] <= sizes["viewportWidth"] + 1, sizes


def _assert_table_scrolls_locally(table):
    expect(table).to_be_visible()
    metrics = table.evaluate(
        """el => ({
            scrollWidth: el.scrollWidth,
            clientWidth: el.clientWidth,
            overflowX: getComputedStyle(el).overflowX,
            cells: Array.from(el.querySelectorAll('td')).map(td => ({
                text: td.textContent.trim(),
                whiteSpace: getComputedStyle(td).whiteSpace,
            })),
        })"""
    )
    assert metrics["scrollWidth"] > metrics["clientWidth"] + 1, metrics
    assert metrics["overflowX"] in {"auto", "scroll"}, metrics
    assert metrics["cells"], metrics
    assert all(cell["whiteSpace"] == "nowrap" for cell in metrics["cells"]), metrics
    assert any(cell["text"] == "Python Fundamentals" for cell in metrics["cells"])

    pan = table.evaluate(
        """el => {
            el.scrollLeft = el.scrollWidth;
            const last = el.querySelector('tr:last-child td:last-child');
            const outer = el.getBoundingClientRect();
            const inner = last.getBoundingClientRect();
            return {
                scrollLeft: el.scrollLeft,
                rightmostReachable: inner.right <= outer.right + 1,
            };
        }"""
    )
    assert pan["scrollLeft"] > 0, pan
    assert pan["rightmostReachable"], pan


def _assert_wrapper_contract(wrapper):
    values = wrapper.evaluate(
        """el => ({
            maxWidth: getComputedStyle(el).maxWidth,
            overflowX: getComputedStyle(el).overflowX,
        })"""
    )
    assert values["maxWidth"] == "100%", values
    assert values["overflowX"] in {"auto", "scroll"}, values


def _assert_ordinary_prose_wraps(wrapper):
    paragraph = wrapper.locator("p").first
    list_item = wrapper.locator("li").first
    expect(paragraph).to_be_visible()
    expect(list_item).to_be_visible()
    for element in (paragraph, list_item):
        values = element.evaluate(
            """el => ({
                whiteSpace: getComputedStyle(el).whiteSpace,
                scrollWidth: el.scrollWidth,
                clientWidth: el.clientWidth,
            })"""
        )
        assert values["whiteSpace"] == "normal", values
        assert values["scrollWidth"] <= values["clientWidth"] + 1, values


def _seed_course():
    from content.models import Course, Module, Unit

    create_user("overflow-course-1230@example.com", tier_slug="basic")
    course = Course.objects.create(
        title="Wide Table Course 1230",
        slug="wide-table-course-1230",
        status="published",
        required_level=10,
    )
    module = Module.objects.create(
        course=course,
        title="Comparison Module",
        slug="comparison-module",
        sort_order=0,
    )
    first = Unit.objects.create(
        module=module,
        title="Compare Foundations",
        slug="compare-foundations",
        sort_order=0,
        body=WRAPPING_PROSE + WIDE_TABLE,
    )
    second = Unit.objects.create(
        module=module,
        title="Continue Building",
        slug="continue-building",
        sort_order=1,
        body="The existing reader journey continues here.",
    )
    _close_connection()
    return first, second


def _seed_workshop(*, gated):
    from content.models import Workshop, WorkshopPage

    required_level = 10 if gated else 0
    workshop = Workshop.objects.create(
        slug=f"{'gated' if gated else 'open'}-overflow-workshop-1230",
        title=f"{'Gated' if gated else 'Open'} Overflow Workshop 1230",
        status="published",
        date=datetime.date(2026, 7, 13),
        landing_required_level=0,
        pages_required_level=required_level,
        recording_required_level=required_level,
    )
    body = (
        WRAPPING_PROSE + WIDE_TABLE + "\n\n" + ("teaser context " * 160)
        if gated
        else WRAPPING_PROSE + WIDE_MERMAID
    )
    page = WorkshopPage.objects.create(
        workshop=workshop,
        slug="architecture",
        title="Architecture",
        sort_order=0,
        content_id=uuid.uuid4(),
        body=body,
    )
    WorkshopPage.objects.create(
        workshop=workshop,
        slug="next-steps",
        title="Next Steps",
        sort_order=1,
        body="Keep building through the existing workshop reader.",
    )
    _close_connection()
    return page


def _seed_projects():
    from content.models import Project

    project = Project.objects.create(
        title="Wide Architecture Project 1230",
        slug="wide-architecture-project-1230",
        description="A project with locally pannable architecture content.",
        date=datetime.date(2026, 7, 13),
        author="AI Shipping Labs",
        tags=["architecture", "agents"],
        source_code_url="https://github.com/example/wide-project",
        demo_url="https://example.com/wide-project",
        content_markdown=WRAPPING_PROSE + WIDE_TABLE,
        required_level=0,
        published=True,
    )
    Project.objects.create(
        title="Related Architecture Project",
        slug="related-architecture-project-1230",
        description="A related project card remains available.",
        date=datetime.date(2026, 7, 12),
        tags=["architecture"],
        content_markdown="Related content.",
        required_level=0,
        published=True,
    )
    gated = Project.objects.create(
        title="Protected Architecture Project 1230",
        slug="protected-architecture-project-1230",
        description="A protected project whose full architecture stays gated.",
        date=datetime.date(2026, 7, 11),
        content_markdown=WIDE_TABLE + "\n\nPROTECTED_PROJECT_BODY_1230",
        required_level=10,
        published=True,
    )
    _close_connection()
    return project, gated


def _seed_article():
    from content.models import Article

    article = Article.objects.create(
        title="Wide Table Article 1230",
        slug="wide-table-article-1230",
        description="Global table behavior in an article.",
        date=datetime.date(2026, 7, 13),
        author="AI Shipping Labs",
        tags=["tables"],
        content_markdown=WRAPPING_PROSE + WIDE_TABLE,
        required_level=0,
        published=True,
    )
    _close_connection()
    return article


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_mobile_course_table_scrolls_then_reader_continues(django_server, browser):
    _reset_content()
    first, second = _seed_course()
    context = auth_context(browser, "overflow-course-1230@example.com")
    page = context.new_page()
    page.set_viewport_size(MOBILE)
    try:
        page.goto(f"{django_server}{first.get_absolute_url()}", wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Compare Foundations")).to_be_visible()
        prose = page.locator(".prose").filter(has=page.locator("table")).first
        _assert_table_scrolls_locally(prose.locator("table"))
        _assert_ordinary_prose_wraps(prose)
        _assert_document_fits(page)

        expect(page.get_by_role("button", name="Mark as completed").first).to_be_visible()
        next_link = page.get_by_role("link", name="Next: Continue Building").first
        expect(next_link).to_be_visible()
        next_link.click()
        expect(page.get_by_role("heading", name="Continue Building")).to_be_visible()
        assert page.url.endswith(second.get_absolute_url())
        _assert_document_fits(page)
    finally:
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_workshop_diagram_is_reachable_in_guarded_reader(django_server, browser):
    _reset_content()
    create_user("overflow-workshop-1230@example.com", tier_slug="basic")
    workshop_page = _seed_workshop(gated=False)
    context = auth_context(browser, "overflow-workshop-1230@example.com")
    page = context.new_page()
    try:
        for viewport in (DESKTOP, MOBILE):
            page.set_viewport_size(viewport)
            page.goto(
                f"{django_server}{workshop_page.get_absolute_url()}",
                wait_until="domcontentloaded",
            )
            body = page.get_by_test_id("page-body")
            expect(body).to_be_visible()
            _assert_wrapper_contract(body)
            page.wait_for_function(
                """() => {
                    const diagram = document.querySelector('div.mermaid');
                    return diagram && diagram.querySelector('svg')
                        && diagram.textContent.includes('Write the complete audit log');
                }""",
                timeout=15000,
            )
            diagram = body.locator("div.mermaid")
            metrics = diagram.evaluate(
                """el => ({scrollWidth: el.scrollWidth, clientWidth: el.clientWidth})"""
            )
            assert metrics["scrollWidth"] > metrics["clientWidth"] + 1, metrics
            pan = diagram.evaluate(
                """el => { el.scrollLeft = el.scrollWidth; return el.scrollLeft; }"""
            )
            assert pan > 0
            expect(page.get_by_test_id("reader-bottom-nav")).to_be_visible()
            expect(page.get_by_role("heading", name="Questions & Answers")).to_be_visible()
            expect(page.get_by_role("link", name="Next: Next Steps")).to_be_visible()
            _assert_document_fits(page)
    finally:
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_gated_workshop_table_stays_local_and_gate_stays_intact(django_server, page):
    _reset_content()
    workshop_page = _seed_workshop(gated=True)
    page.set_viewport_size(MOBILE)

    response = page.goto(
        f"{django_server}{workshop_page.get_absolute_url()}",
        wait_until="domcontentloaded",
    )
    assert response.status == 403
    teaser = page.get_by_test_id("teaser-body")
    expect(teaser).to_be_visible()
    _assert_wrapper_contract(teaser)
    _assert_table_scrolls_locally(teaser.locator("table"))
    expect(page.get_by_test_id("teaser-body-wrapper")).to_be_visible()
    expect(page.get_by_test_id("page-body")).to_have_count(0)
    expect(page.get_by_role("link", name="View Pricing")).to_be_visible()
    expect(page.get_by_role("link", name="Create a free account")).to_be_visible()
    _assert_document_fits(page)


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_project_table_scrolls_and_gated_project_body_stays_hidden(django_server, page):
    _reset_content()
    project, gated = _seed_projects()
    page.set_viewport_size(MOBILE)

    page.goto(f"{django_server}{project.get_absolute_url()}", wait_until="domcontentloaded")
    body = page.get_by_test_id("project-body")
    expect(body).to_be_visible()
    _assert_wrapper_contract(body)
    _assert_table_scrolls_locally(body.locator("table"))
    _assert_ordinary_prose_wraps(body)
    expect(page.get_by_role("link", name="GitHub source code")).to_be_visible()
    expect(page.get_by_role("link", name="Live Demo")).to_be_visible()
    expect(page.get_by_test_id("related-content-rail")).to_be_visible()
    _assert_document_fits(page)

    page.goto(f"{django_server}{gated.get_absolute_url()}", wait_until="domcontentloaded")
    expect(page.get_by_test_id("project-paywall")).to_be_visible()
    expect(page.get_by_test_id("project-body")).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("PROTECTED_PROJECT_BODY_1230")
    _assert_document_fits(page)


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_article_gets_global_table_behavior_without_changing_prose_wrap(
    django_server, page,
):
    _reset_content()
    article = _seed_article()
    page.set_viewport_size(MOBILE)

    page.goto(f"{django_server}{article.get_absolute_url()}", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Wide Table Article 1230")).to_be_visible()
    prose = page.locator("article .prose").first
    _assert_table_scrolls_locally(prose.locator("table"))
    _assert_ordinary_prose_wraps(prose)
    expect(page.get_by_role("link", name="tables")).to_be_visible()
    expect(page.get_by_role("link", name="Back to Blog")).to_be_visible()
    _assert_document_fits(page)
