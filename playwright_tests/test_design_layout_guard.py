"""Rendered guard for the design-system reading-order rule.

``_docs/design-system.md`` (Spacing and Layout) puts a section title first and
its links or actions below it, never pinned opposite it on the same line.  The
static guard in ``content/tests/test_design_layout_lint.py`` reads templates;
this guard measures the real page, so it also catches layouts produced by CSS,
JavaScript, includes, or data-dependent markup.

For every visible ``h1``-``h3`` inside ``<main>``, every visible link or button
in the heading's following siblings must start below the heading's bottom
edge.  List and table rows, dialogs, and close/dismiss controls are the
documented exceptions.  Every page must also fit the viewport without
horizontal scrolling.  Each surface is checked at 1280px and 390px.
"""

import datetime
import os
import uuid

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

VIEWPORTS = ({"width": 1280, "height": 900}, {"width": 390, "height": 844})

LAYOUT_PROBLEMS_JS = """
() => {
  const shown = (el) => {
    const box = el.getBoundingClientRect();
    if (box.width <= 1 || box.height <= 1) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && style.position !== 'fixed';
  };
  const exemptHeading = (el) => el.closest('li, tr, td, th, summary, dialog, [role="dialog"]');
  const dismissal = (el) => /^(close|dismiss)/i.test(el.getAttribute('aria-label') || '')
    || [...el.attributes].some((attr) => attr.name.includes('dismiss'));
  const text = (el) => ((el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ')).slice(0, 60);
  const pinned = [];
  for (const heading of document.querySelectorAll('main h1, main h2, main h3')) {
    if (!shown(heading) || exemptHeading(heading)) continue;
    const headingBox = heading.getBoundingClientRect();
    for (let sibling = heading.nextElementSibling; sibling; sibling = sibling.nextElementSibling) {
      const actions = sibling.matches('a[href], button') ? [sibling] : [...sibling.querySelectorAll('a[href], button')];
      for (const action of actions) {
        if (!shown(action) || dismissal(action)) continue;
        const actionBox = action.getBoundingClientRect();
        if (actionBox.top < headingBox.bottom - 2) {
          pinned.push(`${heading.tagName} "${text(heading)}" has "${text(action)}" beside it `
            + `(action top ${Math.round(actionBox.top)} < heading bottom ${Math.round(headingBox.bottom)})`);
        }
      }
    }
  }
  const root = document.documentElement;
  const overflow = root.scrollWidth > window.innerWidth + 1
    ? [`page is ${root.scrollWidth}px wide in a ${window.innerWidth}px viewport`] : [];
  return {pinned, overflow};
}
"""


def _assert_layout(page, base_url, paths):
    """``paths`` maps each path to headings that must render there.

    The expected headings keep the guard honest: a fixture that stops
    producing a section would otherwise pass by checking nothing.
    """
    problems = []
    for viewport in VIEWPORTS:
        page.set_viewport_size(viewport)
        for path, expected_headings in paths.items():
            response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
            assert response is not None and response.ok, f"{path} returned {response and response.status}"
            page.wait_for_load_state("load")
            for heading in expected_headings:
                expect(
                    page.locator("main").get_by_role("heading", name=heading).first
                ).to_be_visible()
            found = page.evaluate(LAYOUT_PROBLEMS_JS)
            for problem in found["pinned"] + found["overflow"]:
                problems.append(f"{viewport['width']}px {path}: {problem}")
    assert problems == [], "Design-system layout violations:\n" + "\n".join(problems)


def _consented_context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        "name": "aslab_analytics_consent", "value": "denied",
        "domain": "127.0.0.1", "path": "/",
    }])
    return context


def _course_with_cohort(email):
    """A running cohort course with lessons, sessions, homework, and a project."""
    from content.models import (
        Cohort,
        CohortEnrollment,
        Course,
        Enrollment,
        Homework,
        Module,
        Unit,
        UserCourseProgress,
    )
    from events.models import Event, EventSeries

    user = create_user(email)
    course = Course.objects.create(
        title="Layout guard course with a deliberately long title for wrapping",
        slug="layout-guard-course", status="published", required_level=0,
    )
    first = Module.objects.create(
        course=course, title="Foundations", slug="foundations", sort_order=1,
        available_after_days=0,
    )
    second = Module.objects.create(
        course=course, title="Retrieval in practice with evaluation", slug="retrieval",
        sort_order=2, available_after_days=0,
    )
    third = Module.objects.create(
        course=course, title="Agentic flows", slug="agentic-flows", sort_order=3,
        available_after_days=14,
    )
    done = Unit.objects.create(module=first, title="Orientation", slug="orientation", sort_order=1)
    lesson = Unit.objects.create(
        module=second, title="Retrieval basics", slug="retrieval-basics", sort_order=1,
        body="## What you build\n\nA small retrieval pipeline.\n",
    )
    Unit.objects.create(module=second, title="Retrieval evaluation", slug="retrieval-eval", sort_order=2)
    session = Unit.objects.create(
        module=second, title="Session 2", slug="session", kind="event",
        sort_order=3, session_position=2,
    )
    homework_unit = Unit.objects.create(
        module=second, title="Retrieval homework", slug="retrieval-homework",
        kind="homework", sort_order=4, content_id=uuid.uuid4(),
    )
    Unit.objects.create(
        module=third, title="Session 3", slug="session", kind="event",
        sort_order=2, session_position=3,
    )
    UserCourseProgress.objects.create(user=user, unit=done, completed_at=timezone.now())
    Enrollment.objects.create(user=user, course=course)
    today = timezone.localdate()
    series = EventSeries.objects.create(name="Layout guard sessions", slug="layout-guard-sessions")
    cohort = Cohort.objects.create(
        course=course, name="Layout cohort", external_key="layout",
        start_date=today - datetime.timedelta(days=8),
        end_date=today + datetime.timedelta(days=30),
        event_series=series,
    )
    CohortEnrollment.objects.create(user=user, cohort=cohort)
    Homework.objects.create(
        cohort=cohort, content_id=homework_unit.content_id,
        slug="retrieval-homework", title="Retrieval homework",
        due_date=timezone.now() + datetime.timedelta(days=5),
    )
    now = timezone.now()
    Event.objects.create(
        event_series=series, slug="layout-guard-session-2", title="Second session event",
        status="completed", series_position=2,
        start_datetime=now - datetime.timedelta(days=1),
        end_datetime=now - datetime.timedelta(days=1) + datetime.timedelta(hours=1),
        description="Bring questions about retrieval.",
    )
    Event.objects.create(
        event_series=series, slug="layout-guard-session-3", title="Third session event",
        status="upcoming", series_position=3,
        start_datetime=now + datetime.timedelta(days=6),
        end_datetime=now + datetime.timedelta(days=6, hours=1),
        description="Agents in practice.",
    )
    connection.close()
    return user, course, second, lesson, session


@pytest.mark.core
@browser_journey
def test_course_home_tabs_keep_actions_below_their_headings(django_server, browser):
    user, course, _module, _lesson, _session = _course_with_cohort("layout-guard-home@test.com")
    context = _consented_context(browser, user.email)
    page = context.new_page()
    base = f"/courses/{course.slug}/home"
    _assert_layout(page, django_server, {
        f"{base}?cohort=layout": ["Next live session"],
        f"{base}/syllabus?cohort=layout": [],
        f"{base}/sessions?cohort=layout": ["Live sessions"],
        f"{base}/homework?cohort=layout": ["Homework"],
        f"{base}/projects?cohort=layout": ["Projects"],
    })
    context.close()


@pytest.mark.core
@browser_journey
def test_course_reader_pages_keep_actions_below_their_headings(django_server, browser):
    user, _course, module, lesson, session = _course_with_cohort("layout-guard-reader@test.com")
    context = _consented_context(browser, user.email)
    page = context.new_page()
    _assert_layout(page, django_server, {
        f"{module.get_absolute_url()}?cohort=layout": ["Live sessions", "Homework and projects"],
        f"{lesson.get_absolute_url()}?cohort=layout": [lesson.title],
        f"{session.get_absolute_url()}?cohort=layout": [],
    })
    context.close()


@pytest.mark.core
@browser_journey
def test_workshop_and_dashboard_keep_actions_below_their_headings(django_server, browser):
    from content.models import Workshop, WorkshopPage

    user = create_user("layout-guard-workshop@test.com", tier_slug="main", first_name="Layout")
    workshop = Workshop.objects.create(
        slug="layout-guard-workshop",
        title="Build a retrieval agent end to end with evaluation",
        description="A hands-on workshop with tutorial pages.",
        date=timezone.localdate(),
        status="published",
        landing_required_level=0,
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug="setup", title="Set up the environment",
        body="Install the tools.", sort_order=0,
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug="build", title="Build the agent",
        body="Write the loop.", sort_order=1,
    )
    connection.close()
    context = _consented_context(browser, user.email)
    page = context.new_page()
    _assert_layout(page, django_server, {
        workshop.get_absolute_url(): [workshop.title, "Tutorial pages"],
        "/": ["For you"],
    })
    context.close()
