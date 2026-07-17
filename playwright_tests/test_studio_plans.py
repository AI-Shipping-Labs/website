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

import os

import pytest

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
        _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )
        sprint_name = "Sidebar planning sprint"
        sprint_slug = "sidebar-planning-sprint"
        sprint_start_date = (
            timezone.localdate() + datetime.timedelta(days=21)
        ).isoformat()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

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
        page.locator('[data-testid="plan-member-search"]').fill(
            "member@test.com",
        )
        page.locator(
            '[data-testid="plan-member-suggestions"]'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="plan-member-suggestion"]'
            '[data-email="member@test.com"]'
        ).first.click()
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
            f"{django_server}/studio/users/{member.pk}/notes/new?plan_id={plan.pk}",
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
        # The member-note form still redirects to the legacy user-profile
        # anchor, but the profile no longer renders the notes section
        # (issue #560). The plan detail page still includes the
        # ``_member_notes.html`` partial, so we navigate back there — the
        # natural surface for this plan-driven narrative.
        page.wait_for_url(f"{django_server}/studio/users/{member.pk}/#member-notes")
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/",
            wait_until="domcontentloaded",
        )

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
            f"{django_server}/studio/users/{member.pk}/notes/new?plan_id={plan.pk}",
        )
        page.locator('select[name="visibility"]').select_option("external")
        page.locator('select[name="plan_id"]').select_option(str(plan.pk))
        page.locator('textarea[name="body"]').fill(
            "Aim for one shipped prototype by week 3",
        )
        page.locator('button[type="submit"]').click()
        page.wait_for_url(f"{django_server}/studio/users/{member.pk}/#member-notes")
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/",
            wait_until="domcontentloaded",
        )

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
