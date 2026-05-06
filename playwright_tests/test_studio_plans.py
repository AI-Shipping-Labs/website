"""Playwright E2E tests for the Studio plans/sprints surfaces (issue #432).

Most behaviour is covered by Django ``TestCase`` modules in
``plans/tests/`` and ``studio/tests/test_plans*.py`` -- per Rule 15, the
server-rendered table-and-form surfaces belong there. These E2E
scenarios are deliberately narrow:

1. Staff member creates a sprint and a plan via the sidebar -- confirms
   the new "Members" section wiring works in a real browser and that
   navigating between the two list pages plus a successful create cycle
   actually lands on the right detail page.
2. Staff captures an internal interview note and then an external one,
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

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_plans_data():
    """Wipe sprints/plans/notes between tests."""
    from plans.models import InterviewNote, Plan, Sprint

    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestStaffCreatesSprintAndPlanFromSidebar:
    """Sidebar navigation + create flow for sprints and plans."""

    def test_create_sprint_then_plan_via_sidebar(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        # Step 1: land on the dashboard.
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Step 2: click the Sprints link in the sidebar.
        page.locator(
            '#studio-sidebar-nav a[href="/studio/sprints/"]'
        ).click()
        page.wait_for_url(f"{django_server}/studio/sprints/")
        # Empty-state copy.
        page.locator("text=No sprints yet").wait_for(state="visible")

        # Step 3: create a sprint. Use a role+name selector so we click
        # the header "New sprint" CTA and not the empty-state "Create
        # your first sprint" link, which both target the same href.
        # ``exact=True`` is required because Playwright's accessible-
        # name match is substring by default.
        page.get_by_role("link", name="New sprint", exact=True).click()
        page.wait_for_url(f"{django_server}/studio/sprints/new")
        page.locator('input[name="name"]').fill("May 2026 sprint")
        page.locator('input[name="slug"]').fill("may-2026")
        page.locator('input[name="start_date"]').fill("2026-05-01")
        page.locator('input[name="duration_weeks"]').fill("6")
        page.locator('select[name="status"]').select_option("draft")
        page.locator('button[type="submit"]').click()

        # Detail page renders the sprint name in its <h1>.
        page.locator('h1:has-text("May 2026 sprint")').wait_for(state="visible")

        # Step 4: jump to Plans via the sidebar.
        page.locator('#studio-sidebar-nav a[href="/studio/plans/"]').click()
        page.wait_for_url(f"{django_server}/studio/plans/")

        # Step 5: create a plan. Same strict-mode rationale as above:
        # the empty-state "Create a new plan" link shares the href with
        # the header "New plan" CTA. ``exact=True`` distinguishes
        # "New plan" from "Create a new plan".
        page.get_by_role("link", name="New plan", exact=True).click()
        page.wait_for_url(f"{django_server}/studio/plans/new")
        page.locator('select[name="member"]').select_option(
            label="member@test.com",
        )
        page.locator('select[name="sprint"]').select_option(
            label="May 2026 sprint",
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
class TestStaffCapturesInterviewNotes:
    """Add internal then external notes; confirm UI separation."""

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
            start_date="2026-05-01",
        )
        member = User.objects.get(email="member@test.com")
        plan = Plan.objects.create(member=member, sprint=sprint, status="draft")
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/",
            wait_until="domcontentloaded",
        )

        # Click "Add interview note".
        page.locator('a[href="/studio/plans/{}/notes/new"]'.format(plan.pk)).click()
        page.wait_for_url(
            f"{django_server}/studio/plans/{plan.pk}/notes/new",
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
        page.wait_for_url(f"{django_server}/studio/plans/{plan.pk}/")

        # Internal section now has the note; external is still empty.
        page.locator(
            '[data-testid="internal-notes"]'
            ' >> text=Member is changing jobs in 6 weeks - keep plan light'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="external-notes"] >> text=No external notes yet.'
        ).wait_for(state="visible")

        # Add an external note.
        page.locator('a[href="/studio/plans/{}/notes/new"]'.format(plan.pk)).click()
        page.wait_for_url(
            f"{django_server}/studio/plans/{plan.pk}/notes/new",
        )
        page.locator('select[name="visibility"]').select_option("external")
        page.locator('textarea[name="body"]').fill(
            "Aim for one shipped prototype by week 3",
        )
        page.locator('button[type="submit"]').click()
        page.wait_for_url(f"{django_server}/studio/plans/{plan.pk}/")

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
