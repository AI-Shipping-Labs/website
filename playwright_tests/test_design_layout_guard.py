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

List rows follow the row-actions rule: for every ``<li>`` inside ``<main>``
with a heading, each row action must start below the row's meta block (the
row child holding the heading) or, for an action inside that block, below the
heading itself: at 390px always, and at every width when the row has two or
more actions (a Skip control counts).  At every width, the actions that share
one action group must share the same text decoration and colour.  Close and
dismiss controls are exempt from both checks; Skip is exempt from the style
check only.  A Skip on its button's line, just to its left, is part of that
button's group, so the group may stay pinned from ``sm`` up.

Rows in one list share one action placement: in every ``ul``/``ol`` inside
``<main>`` with two or more heading rows that have actions, every action group
starts at the row's text column at 390px, and from ``sm`` up the groups are
either all below the meta or all pinned with every button on one right edge.
A Skip sits on its button's line: before it from ``sm`` up, after it on phones.
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


ROW_ACTION_PROBLEMS_JS = """
(checkPlacement) => {
  const shown = (el) => {
    const box = el.getBoundingClientRect();
    if (box.width <= 1 || box.height <= 1) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  // Close, dismiss, and skip controls retire the row rather than act on it,
  // so they never have to match the action style.  Close and dismiss are
  // exempt from placement too; a Skip beside another action makes two
  // actions, which always share an action row below the meta.
  const dismissal = (el) => /^(close|dismiss|skip)/i.test(el.getAttribute('aria-label') || '')
    || [...el.attributes].some((attr) => attr.name.includes('dismiss'));
  const skip = (el) => /^skip/i.test(el.getAttribute('aria-label') || '');
  const text = (el) => ((el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ')).slice(0, 60);
  const own = (row, el) => el.closest('li') === row;
  const problems = [];
  for (const row of document.querySelectorAll('main li')) {
    if (!shown(row) || row.closest('dialog, [role="dialog"]')) continue;
    const title = [...row.querySelectorAll('h1, h2, h3, h4, h5, h6')].find((el) => own(row, el) && shown(el));
    if (!title) continue;
    const meta = [...row.children].find((child) => child.contains(title));
    const actions = [...row.querySelectorAll('a[href], button')].filter(
      (el) => own(row, el) && shown(el) && !title.contains(el) && !dismissal(el));
    const skips = [...row.querySelectorAll('button')].filter(
      (el) => own(row, el) && shown(el) && skip(el));
    const placed = actions.concat(skips);
    // A quiet Skip on its button's line, just left of it, is part of that
    // one action group, so the group may stay pinned right from sm up.
    const inlineSkip = actions.length === 1 && skips.length === 1
      && skips[0].parentElement === actions[0].parentElement && (() => {
        const skipBox = skips[0].getBoundingClientRect();
        const actionBox = actions[0].getBoundingClientRect();
        const centre = (box) => box.top + box.height / 2;
        return Math.abs(centre(skipBox) - centre(actionBox)) <= 4 && skipBox.right <= actionBox.left + 1;
      })();
    if (checkPlacement || (placed.length >= 2 && !inlineSkip)) {
      const titleBottom = title.getBoundingClientRect().bottom;
      for (const action of placed) {
        const floor = meta && !meta.contains(action) ? meta.getBoundingClientRect().bottom : titleBottom;
        const top = action.getBoundingClientRect().top;
        if (top < floor - 2) {
          problems.push(`row "${text(title)}" has "${text(action)}" beside its meta `
            + `(action top ${Math.round(top)} < meta bottom ${Math.round(floor)})`);
        }
      }
    }
    const groups = new Map();
    for (const action of actions) {
      const group = groups.get(action.parentElement) || [];
      group.push(action);
      groups.set(action.parentElement, group);
    }
    for (const group of groups.values()) {
      const looks = group.map((el) => {
        const style = getComputedStyle(el);
        return `${style.textDecorationLine} ${style.color}`;
      });
      if (new Set(looks).size > 1) {
        problems.push(`row "${text(title)}" mixes action styles: `
          + group.map((el, i) => `"${text(el)}" ${looks[i]}`).join(', '));
      }
    }
  }
  return problems;
}
"""


def _row_action_problems(page, viewport):
    return page.evaluate(ROW_ACTION_PROBLEMS_JS, viewport["width"] < 640)


# Rows in one list share one action placement (design-system Row actions).
# For every list in <main> with two or more rows that each have a heading and
# an action group: below sm every group starts at its row's text column; from
# sm up every group is either below the meta at the text column or pinned
# right with every row's button on one right edge, never a mix.  A Skip must
# share its button's line: to its left from sm up, after it below sm.
LIST_PLACEMENT_JS = """
(phone) => {
  const shown = (el) => {
    const box = el.getBoundingClientRect();
    if (box.width <= 1 || box.height <= 1) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const retire = (el) => /^(close|dismiss|skip)/i.test(el.getAttribute('aria-label') || '')
    || [...el.attributes].some((attr) => attr.name.includes('dismiss'));
  const skip = (el) => /^skip/i.test(el.getAttribute('aria-label') || '');
  const text = (el) => ((el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ')).slice(0, 60);
  const centre = (box) => box.top + box.height / 2;
  const near = (a, b) => Math.abs(a - b) <= 4;
  const problems = [];
  const checked = [];
  for (const list of document.querySelectorAll('main ul, main ol')) {
    if (!shown(list) || list.closest('dialog, [role="dialog"]')) continue;
    const rows = [];
    for (const row of list.children) {
      if (row.tagName !== 'LI' || !shown(row)) continue;
      const own = (el) => el.closest('li') === row;
      const title = [...row.querySelectorAll('h1, h2, h3, h4, h5, h6')].find((el) => own(el) && shown(el));
      if (!title) continue;
      const inText = (el) => el.closest('p, h1, h2, h3, h4, h5, h6') !== null;
      const actions = [...row.querySelectorAll('a[href], button')].filter(
        (el) => own(el) && shown(el) && !inText(el) && !retire(el));
      if (actions.length === 0) continue;
      const skips = [...row.querySelectorAll('button')].filter((el) => own(el) && shown(el) && skip(el));
      rows.push({title, actions, skips});
    }
    if (rows.length < 2) continue;
    const name = (list.closest('[data-testid]') || list).getAttribute('data-testid') || list.tagName;
    checked.push({name, rows: rows.length});
    const placements = rows.map(({title, actions, skips}) => {
      // Layout places each control by its margin box; a Skip's negative
      // margin widens its hit area without moving where the group starts.
      const left = Math.min(...actions.concat(skips).map(
        (el) => el.getBoundingClientRect().left - parseFloat(getComputedStyle(el).marginLeft)));
      const textLeft = title.getBoundingClientRect().left;
      const buttonRight = Math.max(...actions.map((el) => el.getBoundingClientRect().right));
      for (const skipButton of skips) {
        const skipBox = skipButton.getBoundingClientRect();
        const buttonBox = actions[0].getBoundingClientRect();
        const sameLine = near(centre(skipBox), centre(buttonBox));
        const ordered = phone ? skipBox.left >= buttonBox.right - 1 : skipBox.right <= buttonBox.left + 1;
        if (!sameLine || !ordered) {
          problems.push(`${name} row "${text(title)}": Skip is not on its button's line `
            + `${phone ? 'after' : 'before'} "${text(actions[0])}"`);
        }
      }
      return {title, below: near(left, textLeft), buttonRight};
    });
    if (phone || placements.some((row) => row.below)) {
      for (const row of placements.filter((row) => !row.below)) {
        problems.push(`${name} row "${text(row.title)}" pins its actions while `
          + (phone ? 'phone rows put them below the text' : 'other rows put them below the text'));
      }
    } else {
      const edge = Math.max(...placements.map((row) => row.buttonRight));
      for (const row of placements.filter((row) => !near(row.buttonRight, edge))) {
        problems.push(`${name} row "${text(row.title)}" button ends at ${Math.round(row.buttonRight)}, `
          + `off the list's right edge ${Math.round(edge)}`);
      }
    }
  }
  return {problems, checked};
}
"""


def _list_placement(page, viewport):
    return page.evaluate(LIST_PLACEMENT_JS, viewport["width"] < 640)


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
            found_rows = _row_action_problems(page, viewport)
            found_lists = _list_placement(page, viewport)["problems"]
            for problem in found["pinned"] + found["overflow"] + found_rows + found_lists:
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
        recording_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        recap_notes="What we covered in session 2.",
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
    _assert_row_guard_rejects_the_old_session_row(page, django_server, f"{base}?cohort=layout")
    context.close()


# Rebuilds the course Home session row as it shipped before the row-actions
# rule: "Watch recording" and "Read recap" in a group pinned beside the meta
# by a ``justify-between`` row (the owner's 390px screenshot).
OLD_SESSION_ROW_JS = """
(row) => {
  row.className = 'flex min-w-0 items-center justify-between gap-3 py-2';
  row.firstElementChild.className = 'min-w-0 flex-1';
  const action = row.lastElementChild;
  const group = document.createElement('div');
  group.className = 'flex shrink-0 flex-wrap items-center justify-end gap-x-4';
  for (const label of ['Watch recording', 'Read recap']) {
    const link = action.cloneNode(true);
    link.className = 'inline-flex min-h-[44px] shrink-0 items-center gap-1 text-sm font-medium text-accent hover:underline';
    link.firstChild.textContent = `${label} `;
    group.appendChild(link);
  }
  action.replaceWith(group);
}
"""


def _assert_row_guard_rejects_the_old_session_row(page, base_url, path):
    """The row-action checks pass on the shipped row and fail on the old one."""
    phone = VIEWPORTS[1]
    page.set_viewport_size(phone)
    page.goto(f"{base_url}{path}", wait_until="load")
    card = page.get_by_test_id("course-home-current-module")
    row = card.get_by_test_id("course-home-live-session-row").filter(has_text="Recording · Recap")
    expect(row.get_by_role("link", name="Open session")).to_be_visible()
    assert _row_action_problems(page, phone) == []

    row.evaluate(OLD_SESSION_ROW_JS)
    pinned = _row_action_problems(page, phone)
    assert any('"Watch recording" beside its meta' in problem for problem in pinned), pinned
    assert any('"Read recap" beside its meta' in problem for problem in pinned), pinned

    row.get_by_role("link", name="Read recap").evaluate("(link) => link.classList.add('underline')")
    mixed = _row_action_problems(page, phone)
    assert any("mixes action styles" in problem for problem in mixed), mixed


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
    homework_url = lesson.get_absolute_url().replace("retrieval-basics", "retrieval-homework")
    _assert_titles_align_with_sidebar(page, django_server, [
        f"{module.get_absolute_url()}?cohort=layout",
        f"{lesson.get_absolute_url()}?cohort=layout",
        f"{session.get_absolute_url()}?cohort=layout",
        f"{homework_url}?cohort=layout",
    ])
    context.close()


def _assert_titles_align_with_sidebar(page, base_url, paths):
    """From ``lg`` the page h1 starts level with the sidebar navigation card."""
    problems = []
    for width in (1280, 2048):
        page.set_viewport_size({"width": width, "height": 900})
        for path in paths:
            page.goto(f"{base_url}{path}", wait_until="load")
            title = page.locator("#content-sidebar-main h1").first.bounding_box()
            card = page.locator("#sidebar-nav").bounding_box()
            assert title is not None and card is not None, path
            offset = title["y"] - card["y"]
            if abs(offset) > 4:
                problems.append(f"{width}px {path}: h1 top is {offset:+.1f}px from the sidebar card top")
    assert problems == [], "Reader titles out of line with the sidebar:\n" + "\n".join(problems)


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


def _mixed_checklist_user(email):
    """A Main member whose dashboard and course Home checklists mix skipped,
    done, and open (skippable) steps, the owner's screenshot state."""
    from content.models import Course, Enrollment, Module, Unit, UserCourseProgress

    user = create_user(email, tier_slug="main", first_name="Mixed")
    course = Course.objects.create(
        title="Checklist guard course", slug="checklist-guard-course",
        status="published", required_level=0,
    )
    welcome = Module.objects.create(
        course=course, title="Welcome and orientation", slug="welcome", sort_order=1,
    )
    build = Module.objects.create(course=course, title="Build", slug="build", sort_order=2)
    Unit.objects.create(module=welcome, title="How this course works", slug="how", sort_order=1)
    lesson = Unit.objects.create(module=build, title="First lesson", slug="first", sort_order=1)
    Unit.objects.create(
        module=build, title="First homework", slug="first-homework", kind="homework",
        sort_order=2, content_id=uuid.uuid4(),
    )
    Enrollment.objects.create(user=user, course=course)
    UserCourseProgress.objects.create(user=user, unit=lesson, completed_at=timezone.now())
    user.dashboard_dismissals = [
        "getting_started_skip_onboarding",
        "getting_started_skip_slack",
        "getting_started_skip_ai_hero",
        "free_activation_sprint_guide_seen",
        f"course_checklist_skip:{course.slug}:orientation",
    ]
    user.save(update_fields=["dashboard_dismissals"])
    connection.close()
    return user, course


CHECKLISTS = (
    ("/", "free-activation-checklist", "Set up your account", "Browse events"),
    ("/courses/checklist-guard-course/home", "course-checklist", "Get set up for this course", "Open homework"),
)


@pytest.mark.core
@browser_journey
def test_activation_checklists_share_one_action_placement(django_server, browser):
    user, _course = _mixed_checklist_user("layout-guard-checklist@test.com")
    context = _consented_context(browser, user.email)
    page = context.new_page()
    _assert_layout(page, django_server, {path: [title] for path, _testid, title, _cta in CHECKLISTS})
    for viewport in VIEWPORTS:
        page.set_viewport_size(viewport)
        for path, testid, _title, cta in CHECKLISTS:
            page.goto(f"{django_server}{path}", wait_until="load")
            checklist = page.get_by_test_id(testid)
            expect(checklist.get_by_text("Skipped").first).to_be_visible()
            expect(checklist.get_by_role("button", name="Skip", exact=False).first).to_be_visible()
            expect(checklist.get_by_role("link", name=cta)).to_be_visible()
            checked = _list_placement(page, viewport)["checked"]
            assert any(entry["name"] == testid and entry["rows"] >= 3 for entry in checked), checked
    _assert_list_guard_rejects_old_checklists(page, django_server)
    context.close()


# The checklist as shipped before rows shared one placement: an open step's
# CTA and Skip moved below the description while done and skipped steps kept
# their button pinned right.
MOVE_OPEN_ROW_BELOW_JS = """
(skip) => {
  const row = skip.closest('li');
  row.querySelector('[data-row-actions]').parentElement.className = 'flex min-w-0 flex-1 flex-col gap-2';
}
"""
# The first skippable checklist: Skip stacked under the pinned button.
STACK_SKIP_UNDER_BUTTON_JS = """
(skip) => {
  const group = skip.closest('[data-row-actions]');
  group.className = 'flex shrink-0 flex-col items-end';
  group.appendChild(skip);
}
"""


def _assert_list_guard_rejects_old_checklists(page, base_url):
    desktop = VIEWPORTS[0]
    page.set_viewport_size(desktop)
    path, testid, _title, _cta = CHECKLISTS[0]
    for script, expected in ((MOVE_OPEN_ROW_BELOW_JS, "other rows put them below the text"),
                             (STACK_SKIP_UNDER_BUTTON_JS, "Skip is not on its button's line")):
        page.goto(f"{base_url}{path}", wait_until="load")
        assert _list_placement(page, desktop)["problems"] == []
        page.get_by_test_id(testid).locator("[data-skip-activation]").first.evaluate(script)
        problems = _list_placement(page, desktop)["problems"]
        assert any(expected in problem for problem in problems), problems
