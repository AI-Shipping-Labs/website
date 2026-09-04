"""Playwright E2E tests for the Studio plans/sprints surfaces (issue #432).

Most behaviour is covered by Django ``TestCase`` modules in
``plans/tests/`` and ``studio/tests/test_plans*.py`` -- per Rule 15, the
server-rendered table-and-form surfaces belong there. These E2E
scenarios are deliberately narrow:

1. Staff member creates a sprint and a plan via the sidebar -- confirms
   the new "Planning" section wiring works in a real browser and that
   navigating between the two list pages plus a successful create cycle
   actually lands on the right detail page.
2. Staff captures an internal member note and then an external one,
   then confirms the page renders them in their separate visibility
   sections (the security-critical UI separation tested live).
3. Non-staff cannot reach the studio plans / sprints pages.
"""

import json
import os
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)
from playwright_tests.conftest import (
    expand_studio_sidebar_section as _expand_studio_sidebar_section,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _search_query(request):
    return parse_qs(urlparse(request.url).query).get("q", [""])[0]


def _fulfill_people_search(route, results):
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"results": results}),
    )


def _release_people_search(page, route, results):
    with page.expect_response(lambda response: response.url == route.request.url) as info:
        _fulfill_people_search(route, results)
    info.value.body()
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => "
        "requestAnimationFrame(resolve)))"
    )


def _expect_picker_dismissed(page, testid):
    suggestions = page.locator(f'[data-testid="{testid}"]')
    expect(suggestions).to_be_hidden()
    expect(suggestions.locator("li")).to_have_count(0)


def _clear_plans_data():
    """Wipe sprints/plans/notes between tests."""
    from plans.models import InterviewNote, Plan, Sprint

    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _seed_studio_markdown_download_plan(member_email):
    import datetime

    from accounts.models import User
    from plans.models import (
        Checkpoint,
        InterviewNote,
        Plan,
        Resource,
        Sprint,
        Week,
        WeekNote,
    )

    member = User.objects.get(email=member_email)
    sprint = Sprint.objects.create(
        name="Studio Download Sprint",
        slug="studio-download-sprint",
        # date-rot-ok: Studio download fixture; current sprint state is not under test.
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=4,
    )
    plan = Plan.objects.create(
        member=member,
        sprint=sprint,
        title="Studio portable plan",
        goal="Download from Studio",
        summary_goal="Confirm staff uses safe Markdown",
        focus_main="Keep internal notes out",
    )
    week = Week.objects.create(
        plan=plan,
        week_number=1,
        theme="Studio export",
        position=0,
    )
    Checkpoint.objects.create(
        week=week,
        description="Studio checkpoint",
        position=0,
    )
    WeekNote.objects.create(
        week=week,
        author=member,
        body="Studio-visible participant note",
    )
    Resource.objects.create(
        plan=plan,
        title="Studio docs",
        url="https://example.com/studio",
    )
    InterviewNote.objects.create(
        member=member,
        plan=plan,
        visibility="internal",
        body="STUDIO_INTERNAL_PLAYWRIGHT_NOTE",
    )
    plan_pk = plan.pk
    connection.close()
    return plan_pk


@pytest.mark.django_db(transaction=True)
class TestViewAsMemberReturnToPlan:
    """Staff returns from impersonated plan view to the same plan URL."""

    @pytest.mark.core
    def test_staff_returns_to_same_member_plan_after_view_as_member(
        self, django_server, browser,
    ):
        from accounts.models import User
        from plans.models import Plan, Sprint

        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )
        member = User.objects.get(email="member@test.com")
        sprint = Sprint.objects.create(
            name="Return Sprint",
            slug="return-sprint",
            # date-rot-ok: Studio redirect fixture; current sprint state is not under test.
            start_date="2026-05-01",
            duration_weeks=6,
        )
        plan = Plan.objects.create(member=member, sprint=sprint)
        plan_pk = plan.pk
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/plans/{plan_pk}/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="studio-plan-view-as-member"]').click()
        member_plan_url = (
            f"{django_server}/sprints/return-sprint/plan/{plan_pk}"
        )
        page.wait_for_url(member_plan_url, timeout=10000)
        page.locator("#impersonation-banner").wait_for(state="visible")
        assert "member@test.com" in page.locator(
            "#impersonation-banner"
        ).inner_text()

        # Quiesce the member page before ending impersonation. The stop
        # POST flushes the session (new key, old row deleted), so any
        # member-page fetch still in flight with the old session key
        # would race the transition and can come back to a dead session
        # row; the follow-up requests then land on the login page instead
        # of the staff session (Issue #1560: scheduled-run flake where
        # the final staff goto 302-redirected to /accounts/login/).
        page.wait_for_load_state("networkidle", timeout=15000)

        with page.expect_navigation(
            url=member_plan_url,
            wait_until="domcontentloaded",
            timeout=10000,
        ):
            page.get_by_role("button", name="Return to your account").click()

        assert page.url == member_plan_url
        assert page.locator("#impersonation-banner").count() == 0

        page.goto(
            f"{django_server}/studio/plans/{plan_pk}/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="studio-plan-view-as-member"]').wait_for(
            state="visible",
        )

        context.close()


@pytest.mark.django_db(transaction=True)
class TestStudioDownloadsMarkdownPlan:
    @pytest.mark.core
    def test_staff_downloads_member_safe_markdown(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member-download@test.com",
            tier_slug="free",
            email_verified=True,
        )
        plan_pk = _seed_studio_markdown_download_plan(
            "member-download@test.com",
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/plans/{plan_pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_label("More actions").click()
        button = page.get_by_role("link", name="Download Markdown")
        button.wait_for(state="visible")

        with page.expect_download() as download_info:
            button.click()
        download = download_info.value
        assert download.suggested_filename == (
            f"sprint-plan-studio-download-sprint-{plan_pk}.md"
        )
        with open(download.path(), encoding="utf-8") as f:
            markdown = f.read()

        assert "# Studio portable plan" in markdown
        assert "Studio-visible participant note" in markdown
        assert "STUDIO_INTERNAL_PLAYWRIGHT_NOTE" not in markdown

        context.close()


@pytest.mark.django_db(transaction=True)
class TestStaffCreatesSprintAndPlanFromSidebar:
    """Sidebar navigation + create flow for sprints and plans."""

    def test_create_sprint_then_plan_via_sidebar(self, django_server, browser):
        import datetime

        from django.utils import timezone

        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        member = _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
            first_name="Member",
        )
        sprint_name = "Sidebar planning sprint"
        sprint_slug = "sidebar-planning-sprint"
        sprint_start_date = (
            timezone.localdate() + datetime.timedelta(days=21)
        ).isoformat()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        member_result = {
            "id": member.pk,
            "email": member.email,
            "display_name": "Member",
            "first_name": "Member",
            "last_name": "",
            "tier_level": 0,
            "has_community_access": False,
        }
        stale_result = {
            "id": 999999,
            "email": "stale-plan@test.com",
            "display_name": "Stale Plan Member",
            "first_name": "Stale",
            "last_name": "Member",
            "tier_level": 0,
            "has_community_access": False,
        }
        hold_once = {"member@test.com", "escape-old", "older-member"}
        pending = {}

        def control_people_search(route):
            query = _search_query(route.request)
            if query in hold_once:
                hold_once.remove(query)
                pending[query] = route
                return
            results = [member_result] if query == "member@test.com" else []
            _fulfill_people_search(route, results)

        page.route("**/studio/api/users/search/**", control_people_search)

        # Step 1: land on the dashboard.
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Step 2: expand Planning, then click the Sprints link in the sidebar.
        _expand_studio_sidebar_section(page, "planning")
        page.locator(
            '#studio-sidebar-nav a[href="/studio/sprints/"]'
        ).click()
        page.wait_for_url(f"{django_server}/studio/sprints/")
        # Empty-state copy.
        page.locator("text=No sprints yet").wait_for(state="visible")

        # Step 3: create a sprint. Scope the locator to the header
        # ``data-testid="sprints-header"`` so we click the header
        # "New sprint" CTA and not the empty-state CTA (issue #756 +
        # #752 means BOTH render the same accessible name on an empty
        # list; an unscoped ``get_by_role`` resolves to two elements
        # and Playwright's strict mode refuses the click — issue #776).
        # ``exact=True`` is required because Playwright's accessible-
        # name match is substring by default.
        page.locator(
            '[data-testid="sprints-header"]'
        ).get_by_role("link", name="New sprint", exact=True).click()
        page.wait_for_url(f"{django_server}/studio/sprints/new")
        page.locator('input[name="name"]').fill(sprint_name)
        page.locator('input[name="slug"]').fill(sprint_slug)
        page.locator('input[name="start_date"]').fill(sprint_start_date)
        page.locator('input[name="duration_weeks"]').fill("6")
        page.locator('select[name="status"]').select_option("draft")
        page.locator('button[type="submit"]').click()

        # Detail page renders the sprint name in its <h1>.
        page.locator(f'h1:has-text("{sprint_name}")').wait_for(state="visible")

        # Step 4: jump to Plans via the sidebar.
        _expand_studio_sidebar_section(page, "planning")
        page.locator('#studio-sidebar-nav a[href="/studio/plans/"]').click()
        page.wait_for_url(f"{django_server}/studio/plans/")

        # Abandon a held list-filter lookup by tabbing into the adjacent text
        # search. Its late response must stay dismissed, leaving Filter fully
        # clickable through the ordinary form flow.
        list_member = page.locator('[data-testid="plan-list-member-search"]')
        with page.expect_request(
            lambda request: _search_query(request) == "member@test.com"
        ):
            list_member.fill("member@test.com")
        list_member.press("Tab")
        plan_text_search = page.locator('input[name="q"]')
        expect(plan_text_search).to_be_focused()
        _release_people_search(
            page,
            pending["member@test.com"],
            [member_result],
        )
        _expect_picker_dismissed(page, "plan-list-member-suggestions")
        plan_text_search.fill("member@test.com")
        page.get_by_role("button", name="Filter", exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        assert "q=member%40test.com" in page.url

        # Step 5: create a plan. Same scope-by-header rationale as
        # above: the empty-state CTA renders the same "New plan"
        # accessible name as the header CTA, so we narrow to
        # ``data-testid="plans-header"`` to keep the click unique on
        # an empty list (issue #776).
        page.locator(
            '[data-testid="plans-header"]'
        ).get_by_role("link", name="New plan", exact=True).click()
        page.wait_for_url(f"{django_server}/studio/plans/new")
        # Issue #735 swapped the inline ``<select name="member">`` for the
        # reusable people picker (testid prefix ``plan-member``). Drive
        # the picker via its real surface: type into the search input,
        # wait for the suggestion list to render, then click the row.
        plan_member = page.locator('[data-testid="plan-member-search"]')
        with page.expect_request(
            lambda request: _search_query(request) == "escape-old"
        ):
            plan_member.fill("escape-old")
        with page.expect_response(
            lambda response: _search_query(response.request) == "member@test.com"
        ):
            plan_member.fill("member@test.com")
        expect(
            page.locator('[data-testid="plan-member-suggestions"]')
        ).to_be_visible()
        plan_member.press("Escape")
        _release_people_search(page, pending["escape-old"], [stale_result])
        _expect_picker_dismissed(page, "plan-member-suggestions")

        with page.expect_request(
            lambda request: _search_query(request) == "older-member"
        ):
            plan_member.fill("older-member")
        with page.expect_response(
            lambda response: _search_query(response.request) == "member@test.com"
        ):
            plan_member.fill("member@test.com")
        page.locator(
            '[data-testid="plan-member-suggestions"]'
        ).wait_for(state="visible")
        plan_member.press("ArrowDown")
        plan_member.press("Enter")
        _release_people_search(
            page,
            pending["older-member"],
            [stale_result],
        )
        expect(plan_member).to_have_value("Member")
        expect(page.locator('#plan-member-id')).to_have_value(str(member.pk))
        _expect_picker_dismissed(page, "plan-member-suggestions")
        page.locator('select[name="sprint"]').select_option(
            label=sprint_name,
        )
        page.locator('button[type="submit"]').click()

        # Plan detail page renders the member email AND the sprint name,
        # plus the two empty visibility sections.
        page.locator('h1:has-text("member@test.com")').wait_for(state="visible")
        page.locator(
            '[data-testid="internal-notes-heading"]'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="external-notes-heading"]'
        ).wait_for(state="visible")
        # Both sections are empty placeholders.
        empty_internal = page.locator(
            '[data-testid="internal-notes"] >> text=No internal notes yet.'
        )
        empty_external = page.locator(
            '[data-testid="external-notes"] >> text=No external notes yet.'
        )
        empty_internal.wait_for(state="visible")
        empty_external.wait_for(state="visible")


@pytest.mark.django_db(transaction=True)
class TestMoveUnfinishedPlanItems:
    """Staff moves unfinished plan items into a selected later sprint."""

    def test_staff_moves_unfinished_items_to_selected_later_sprint(
        self, django_server, browser,
    ):
        from accounts.models import User
        from plans.models import Checkpoint, Deliverable, Plan, Sprint, Week

        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )

        member = User.objects.get(email="member@test.com")
        may = Sprint.objects.create(
            # date-rot-ok: fixed ordering fixture for Studio plan filters.
            name="May Sprint", slug="may-2026", start_date="2026-05-01",
            duration_weeks=4,
        )
        june = Sprint.objects.create(
            # date-rot-ok: fixed ordering fixture for Studio plan filters.
            name="June Sprint", slug="june-2026", start_date="2026-06-01",
            duration_weeks=4,
        )
        july = Sprint.objects.create(
            # date-rot-ok: fixed ordering fixture for Studio plan filters.
            name="July Sprint", slug="july-2026", start_date="2026-07-01",
            duration_weeks=4,
        )
        source = Plan.objects.create(member=member, sprint=may)
        for n in range(1, 5):
            Week.objects.create(plan=source, week_number=n, position=n - 1)
        source_week = source.weeks.get(week_number=1)
        Checkpoint.objects.create(
            week=source_week, description="Move checkpoint A", position=0,
        )
        Checkpoint.objects.create(
            week=source_week, description="Move checkpoint B", position=1,
        )
        Checkpoint.objects.create(
            week=source_week,
            description="Completed checkpoint stays",
            position=2,
            done_at="2026-05-10T10:00:00Z",
        )
        Deliverable.objects.create(
            plan=source, description="Move deliverable", position=0,
        )
        source_id = source.pk
        june_id = june.pk
        july_id = july.pk
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/plans/{source_id}/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="studio-header-overflow"] summary').click()
        page.get_by_test_id("studio-plan-move-unfinished").click()
        page.wait_for_url(
            f"{django_server}/studio/plans/{source_id}/move-unfinished/"
        )

        page.get_by_test_id("move-unfinished-target-name").wait_for(
            state="visible"
        )
        assert page.get_by_test_id("move-unfinished-target-name").inner_text() == (
            "June Sprint"
        )
        assert page.get_by_test_id("move-unfinished-checkpoints").inner_text() == "2"
        assert page.get_by_test_id("move-unfinished-deliverables").inner_text() == "1"
        assert page.get_by_test_id("move-unfinished-total").inner_text() == "3"

        page.get_by_test_id("move-unfinished-target").select_option("july-2026")
        assert page.get_by_test_id("move-unfinished-target-name").inner_text() == (
            "July Sprint"
        )
        page.get_by_test_id("move-unfinished-confirm").click()
        page.wait_for_url(f"{django_server}/studio/plans/{source_id}/")

        page.locator("text=Moved 3 unfinished items to \"July Sprint\"").wait_for(
            state="visible"
        )
        page.locator("text=Completed checkpoint stays").wait_for(state="visible")
        assert page.locator("text=Move checkpoint A").count() == 0
        assert page.locator("text=Move deliverable").count() == 0

        # The selected July plan receives the moved items; June remains absent.
        july_plan = Plan.objects.get(member=member, sprint_id=july_id)
        assert not Plan.objects.filter(member=member, sprint_id=june_id).exists()
        connection.close()
        page.goto(
            f"{django_server}/studio/plans/{july_plan.pk}/",
            wait_until="domcontentloaded",
        )
        page.locator("text=Move checkpoint A").wait_for(state="visible")
        page.locator("text=Move checkpoint B").wait_for(state="visible")
        page.locator("text=Move deliverable").wait_for(state="visible")


@pytest.mark.django_db(transaction=True)
class TestStaffCapturesInterviewNotes:
    """Add internal then external member notes; confirm UI separation."""

    def test_internal_then_external_note_render_in_separate_sections(
        self, django_server, browser,
    ):
        from accounts.models import User
        from plans.models import Plan, Sprint

        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )

        sprint = Sprint.objects.create(
            name="May 2026 sprint", slug="may-2026",
            # date-rot-ok: Studio plan form fixture; current sprint state is not under test.
            start_date="2026-05-01",
        )
        member = User.objects.get(email="member@test.com")
        plan = Plan.objects.create(member=member, sprint=sprint)
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/",
            wait_until="domcontentloaded",
        )

        # Click "Add member note"; the plan pre-fills sprint context on
        # the member-scoped form.
        page.get_by_test_id("member-notes-add").click()
        page.wait_for_url(
            f"{django_server}/studio/users/{member.pk}/notes/new"
            f"?plan_id={plan.pk}&next=/studio/plans/{plan.pk}/%23member-notes",
        )

        # The visibility selector defaults to internal. The select's
        # value drives selection in modern browsers; assert via JS prop.
        select_value = page.locator('select[name="visibility"]').evaluate(
            "el => el.value",
        )
        assert select_value == "internal"

        page.locator('textarea[name="body"]').fill(
            "Member is changing jobs in 6 weeks - keep plan light",
        )
        page.locator('button[type="submit"]').click()
        page.wait_for_url(f"{django_server}/studio/plans/{plan.pk}/#member-notes")

        # Internal section now has the note; external is still empty.
        page.locator(
            '[data-testid="internal-notes"]'
            ' >> text=Member is changing jobs in 6 weeks - keep plan light'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="external-notes"] >> text=No external notes yet.'
        ).wait_for(state="visible")

        # Add an external note. The ``Add member note`` CTA on a plan
        # detail page pre-fills ``?plan_id=<pk>`` (the plan partial's
        # context-aware behaviour). The plan_id select is then set
        # explicitly below to be robust either way.
        page.get_by_test_id("member-notes-add").click()
        page.wait_for_url(
            f"{django_server}/studio/users/{member.pk}/notes/new"
            f"?plan_id={plan.pk}&next=/studio/plans/{plan.pk}/%23member-notes",
        )
        page.locator('select[name="visibility"]').select_option("external")
        page.locator('select[name="plan_id"]').select_option(str(plan.pk))
        page.locator('textarea[name="body"]').fill(
            "Aim for one shipped prototype by week 3",
        )
        page.locator('button[type="submit"]').click()
        page.wait_for_url(f"{django_server}/studio/plans/{plan.pk}/#member-notes")

        # External now has the new note; internal still has the
        # original. The two sections render independently.
        page.locator(
            '[data-testid="external-notes"]'
            ' >> text=Aim for one shipped prototype by week 3'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="internal-notes"]'
            ' >> text=Member is changing jobs in 6 weeks - keep plan light'
        ).wait_for(state="visible")


@pytest.mark.django_db(transaction=True)
class TestNonStaffBlockedFromPlansPages:
    """Non-staff cannot reach the plans/sprints pages."""

    def test_member_gets_403_on_plans_and_sprints(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()

        response = page.goto(
            f"{django_server}/studio/plans/",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 403

        response = page.goto(
            f"{django_server}/studio/sprints/",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 403
