"""Playwright coverage for sprint-plan pre-sprint actions (#1044)."""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_plan_data():
    from accounts.models import Token
    from plans.models import (
        Checkpoint,
        Deliverable,
        InterviewNote,
        NextStep,
        Plan,
        Resource,
        Sprint,
        SprintEnrollment,
        Week,
    )

    Checkpoint.objects.all().delete()
    Week.objects.all().delete()
    Resource.objects.all().delete()
    Deliverable.objects.all().delete()
    NextStep.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Token.objects.filter(
        name__in=["member-plan-editor", "studio-plan-editor"],
    ).delete()
    connection.close()


def _seed_plan(owner_email="member@test.com", teammate_email="teammate@test.com"):
    from accounts.models import User
    from plans.models import NextStep, Plan, Sprint, SprintEnrollment, Week

    sprint = Sprint.objects.create(
        name="May 2026 sprint",
        slug="may-2026",
        # date-rot-ok: pre-sprint action labels are independent of current sprint state.
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=6,
    )
    owner = User.objects.get(email=owner_email)
    teammate = User.objects.get(email=teammate_email)
    SprintEnrollment.objects.create(sprint=sprint, user=owner)
    SprintEnrollment.objects.create(sprint=sprint, user=teammate)
    plan = Plan.objects.create(member=owner, sprint=sprint, visibility="cohort")
    Week.objects.create(plan=plan, week_number=1, position=0)
    for idx, description in enumerate([
        "Watch the missed kickoff recording",
        "Send your project GitHub link",
        "Share repo",
    ]):
        NextStep.objects.create(
            plan=plan,
            description=description,
            position=idx,
        )
    connection.close()
    return {"sprint_slug": sprint.slug, "plan_id": plan.pk}


def _get_row_anchor_coords(source_row, target_row):
    """Return mouse start and target coordinates for dragging between rows."""
    sb = source_row.bounding_box()
    tb = target_row.bounding_box()
    sx = sb["x"] + sb["width"] - 6
    sy = sb["y"] + sb["height"] / 2
    tx = tb["x"] + tb["width"] - 6
    target_y = tb["y"] + min(5, max(2, tb["height"] / 4))
    return sx, sy, tx, target_y


def _wait_for_sortable_dom_reorder(page, source_id, target_id, tx, target_y):
    """Wait for SortableJS animation (150ms) to settle DOM order."""
    script = """([srcId, tgtId]) => {
        const rows = Array.from(document.querySelectorAll('[data-testid="next-step-row"]'));
        const srcIdx = rows.findIndex(r => r.getAttribute('data-next-step-id') === srcId);
        const tgtIdx = rows.findIndex(r => r.getAttribute('data-next-step-id') === tgtId);
        return srcIdx !== -1 && tgtIdx !== -1 && srcIdx < tgtIdx;
    }"""
    for i in range(20):
        if page.evaluate(script, [source_id, target_id]):
            return
        page.wait_for_timeout(50)
        nudge = -1
        if i % 2 == 0:
            nudge = 1
        page.mouse.move(tx, target_y - nudge)


def _drag_row_above(page, source_row, target_row):
    """Drag ``source_row`` above ``target_row`` through SortableJS."""
    page.wait_for_function("typeof window.Sortable !== 'undefined'")
    target_row.scroll_into_view_if_needed()
    source_row.scroll_into_view_if_needed()
    sx, sy, tx, target_y = _get_row_anchor_coords(source_row, target_row)

    page.mouse.move(sx, sy)
    page.mouse.down()
    nudge_dir = 1
    if target_y < sy:
        nudge_dir = -1
    page.mouse.move(sx, sy + nudge_dir * 2, steps=3)
    page.mouse.move(sx, sy + nudge_dir * 6, steps=5)

    steps = max(int(max(abs(target_y - sy), abs(tx - sx))), 30)
    page.mouse.move(tx, target_y, steps=steps)

    source_id = source_row.get_attribute("data-next-step-id")
    target_id = target_row.get_attribute("data-next-step-id")
    _wait_for_sortable_dom_reorder(page, source_id, target_id, tx, target_y)
    page.mouse.up()


@pytest.mark.django_db(transaction=True)
class TestMemberPreSprintActions:
    @pytest.mark.core
    def test_member_completes_and_edits_pre_sprint_actions(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("member@test.com", tier_slug="main", email_verified=True)
        _create_user("teammate@test.com", tier_slug="main", email_verified=True)
        data = _seed_plan()

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        assert page.get_by_role("heading", name="Pre-sprint actions").count() == 1
        assert page.get_by_role("heading", name="Next steps").count() == 0
        rows = page.locator('[data-testid="plan-next-step"]')
        assert rows.nth(0).inner_text().find("Watch the missed kickoff recording") >= 0
        assert rows.nth(1).inner_text().find("Send your project GitHub link") >= 0

        with page.expect_response("**/api/next-steps/*") as done_response:
            rows.nth(0).locator('[data-testid="plan-row-done-toggle"]').check()
        assert done_response.value.ok

        row = page.locator('[data-testid="plan-next-step"]', has_text="Share repo")
        row.locator('[data-testid="plan-item-edit"]').click()
        row.locator('[data-testid="plan-item-markdown-input"]').fill(
            "Share the project GitHub repo in Slack",
        )
        with page.expect_response("**/api/next-steps/*") as edit_response:
            row.locator('[data-testid="plan-item-save"]').click()
        assert edit_response.value.ok

        page.reload(wait_until="domcontentloaded")
        assert page.get_by_role("heading", name="Pre-sprint actions").count() == 1
        assert page.locator('[data-testid="plan-next-step"]').first.locator(
            '[data-testid="plan-row-done-toggle"]',
        ).is_checked()
        assert "Share the project GitHub repo in Slack" in page.locator(
            '[data-testid="plan-next-steps"]',
        ).inner_text()
        context.close()

    def test_teammate_reads_cohort_plan_without_operator_grouping(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("member@test.com", tier_slug="main", email_verified=True)
        _create_user("teammate@test.com", tier_slug="main", email_verified=True)
        data = _seed_plan()

        context = _auth_context(browser, "teammate@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plans/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        assert page.get_by_role("heading", name="Pre-sprint actions").count() == 1
        assert "Facilitator follow-up" not in page.locator("body").inner_text()
        context.close()


def _seed_sprint_test_env():
    """Seed test tiers, users, and sprint plan for staff tests."""
    _ensure_tiers()
    _clear_plan_data()
    _create_staff_user("staff@test.com")
    _create_user("member@test.com", tier_slug="main", email_verified=True)
    _create_user("teammate@test.com", tier_slug="main", email_verified=True)
    return _seed_plan()


def _open_staff_plan_page(browser, django_server, plan_id):
    """Open the studio plan detail page as staff and verify pre-sprint heading."""
    context = _auth_context(browser, "staff@test.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/plans/{plan_id}/",
        wait_until="domcontentloaded",
    )
    assert page.get_by_role("heading", name="Pre-sprint actions").count() == 1
    assert "Facilitator follow-up" not in page.locator("body").inner_text()
    return context, page


def _add_pre_sprint_action(page, text):
    """Add a new pre-sprint action via the inline studio editor."""
    assert page.get_by_test_id("add-next-step").inner_text() == (
        "+ Add pre-sprint action"
    )
    page.get_by_test_id("add-next-step").click()
    editor = page.get_by_test_id("next-step-edit-input")
    editor.fill(text)
    with page.expect_response("**/api/next-steps/*") as edit_response:
        editor.press("Enter")
    assert edit_response.value.ok


def _verify_persisted_actions(page):
    """Verify saved next-step order and done toggles persist across reload."""
    page.locator(
        '[data-testid="save-indicator"][data-state="saved"]'
    ).wait_for()
    page.reload(wait_until="domcontentloaded")
    assert "Review the sprint workshop links" in page.locator(
        '[data-testid="next-steps-panel"]',
    ).inner_text()
    assert page.locator(
        '[data-testid="next-step-row"]',
        has_text="Watch the missed kickoff recording",
    ).locator('[data-testid="next-step-done-toggle"]').is_checked()
    assert "Share repo" in page.locator('[data-testid="next-step-row"]').first.inner_text()


@pytest.mark.django_db(transaction=True)
class TestStudioPreSprintActions:
    def test_staff_reviews_adds_toggles_and_reorders_prep_actions(
        self, django_server, browser,
    ):
        data = _seed_sprint_test_env()
        context, page = _open_staff_plan_page(browser, django_server, data["plan_id"])
        page.goto(
            f"{django_server}/studio/plans/{data['plan_id']}/edit/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_role("heading", name="Pre-sprint actions").count() == 1

        _add_pre_sprint_action(page, "Review the sprint workshop links")

        first_toggle = page.locator('[data-testid="next-step-done-toggle"]').first
        with page.expect_response("**/api/next-steps/*") as done_response:
            first_toggle.check()
        assert done_response.value.ok

        rows = page.locator('[data-testid="next-step-row"]')
        third = rows.nth(2)
        first = rows.nth(0)
        with page.expect_response("**/api/next-steps/*") as reorder_response:
            _drag_row_above(page, third, first)
        assert reorder_response.value.ok
        assert reorder_response.value.json().get("position") == 0

        _verify_persisted_actions(page)
        context.close()
