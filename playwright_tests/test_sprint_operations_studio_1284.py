"""Core Studio sprint-operations journey and visual matrix (#1284)."""

import datetime
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1284")


def _seed_operations_fixture():
    from plans.models import Checkpoint, NextStep, Plan, Sprint, SprintEnrollment, Week

    staff = create_user("ops-staff@test.com", is_staff=True)
    alice = create_user("ops-alice@test.com", first_name="Alice")
    bob = create_user("ops-bob@test.com", first_name="Bob")
    ghost = create_user("ops-ghost@test.com", first_name="Ghost")
    outsider = create_user("ops-outsider@test.com", first_name="Outsider")
    sprint_start = timezone.localdate() - datetime.timedelta(days=7)
    previous = Sprint.objects.create(
        name="Previous Sprint",
        slug="ops-previous-1284",
        start_date=sprint_start - datetime.timedelta(days=35),
    )
    sprint = Sprint.objects.create(
        name="Operations Sprint",
        slug="ops-sprint-1284",
        start_date=sprint_start,
        status="active",
    )
    previous_plan = Plan.objects.create(member=alice, sprint=previous)
    NextStep.objects.create(
        plan=previous_plan,
        description="Carry this unfinished task",
    )
    alice_plan = Plan.objects.create(
        member=alice,
        sprint=sprint,
        visibility="cohort",
        title="Ship the cohort workflow",
        focus_main="PRIVATE-FOCUS-SENTINEL",
        shared_at=timezone.now(),
    )
    bob_plan = Plan.objects.create(
        member=bob,
        sprint=sprint,
        visibility="private",
        title="Private operating plan",
        focus_main="BOB-PRIVATE-FOCUS-SENTINEL",
    )
    ghost_plan = Plan.objects.create(
        member=ghost,
        sprint=sprint,
        visibility="cohort",
        title="GHOST-PLAN-SENTINEL",
    )
    SprintEnrollment.objects.filter(sprint=sprint, user=ghost).delete()
    week = Week.objects.create(plan=alice_plan, week_number=1)
    Checkpoint.objects.create(
        week=week,
        description="Finished checkpoint",
        done_at=timezone.now(),
    )
    Checkpoint.objects.create(week=week, description="Next checkpoint")
    Checkpoint.objects.create(week=week, description="   \n")
    Week.objects.create(plan=alice_plan, week_number=2)
    connection.close()
    return staff, alice, ghost, outsider, sprint, alice_plan, bob_plan, ghost_plan


def _dialog_controller(page):
    state = {"accept": False, "messages": []}

    def handle(dialog):
        state["messages"].append(dialog.message)
        dialog.accept() if state["accept"] else dialog.dismiss()

    page.on("dialog", handle)
    return state


def _dismiss_analytics_consent(page):
    deny = page.get_by_test_id("analytics-consent-deny")
    if deny.count() and deny.is_visible():
        deny.click()
        expect(page.get_by_test_id("analytics-consent-panel")).to_be_hidden()


def _set_theme(page, theme):
    page.evaluate("theme => localStorage.setItem('theme', theme)", theme)
    page.reload(wait_until="domcontentloaded")
    expected = theme == "dark"
    assert page.locator("html").evaluate("el => el.classList.contains('dark')") is expected


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )


@pytest.mark.django_db(transaction=True)
def test_staff_sprint_operations_board_actions_progress_and_visual_matrix(
    django_server, browser,
):
    ensure_tiers()
    (
        staff, _alice, _ghost, _outsider, sprint, alice_plan, bob_plan,
        _ghost_plan,
    ) = _seed_operations_fixture()
    context = auth_context(browser, staff.email)
    page = context.new_page()
    dialogs = _dialog_controller(page)

    routes = {
        "studio-sprint-detail": f"/studio/sprints/{sprint.pk}/",
        "staff-cohort-board": f"/sprints/{sprint.slug}/board",
        "studio-plans-list": "/studio/plans/",
        "studio-plan-editor": f"/studio/plans/{alice_plan.pk}/edit/",
    }

    page.goto(f"{django_server}{routes['studio-sprint-detail']}")
    cohort_link = page.get_by_test_id("sprint-cohort-board-link")
    expect(cohort_link).to_have_attribute("href", f"/sprints/{sprint.slug}/board")
    expect(cohort_link).to_have_attribute("target", "_blank")
    expect(cohort_link).to_have_attribute("rel", "noopener noreferrer")

    page.goto(f"{django_server}{routes['staff-cohort-board']}")
    expect(page.get_by_test_id("staff-view-label")).to_have_text("Staff view")
    expect(page.get_by_test_id("cohort-board-leave-sprint")).to_have_count(0)
    expect(page.get_by_test_id("ask-team-button")).to_have_count(0)
    expect(page.get_by_text("GHOST-PLAN-SENTINEL")).to_have_count(0)
    expect(page.get_by_text("PRIVATE-FOCUS-SENTINEL")).to_have_count(0)
    expect(page.locator(f'a[href="/studio/plans/{alice_plan.pk}/"]')).to_have_count(1)
    expect(page.locator(f'a[href="/studio/plans/{bob_plan.pk}/"]')).to_have_count(1)

    page.goto(f"{django_server}{routes['studio-plans-list']}")
    expect(page.get_by_test_id(f"plan-list-progress-{alice_plan.pk}")).to_have_text(
        "1/2 checkpoints"
    )

    page.goto(f"{django_server}{routes['studio-plan-editor']}")
    page.get_by_label("More actions").click()
    carry = page.get_by_test_id("studio-plan-carry-over")
    expect(carry).to_be_visible()
    expect(page.get_by_test_id("studio-plan-draft-next-sprint")).to_be_visible()

    from plans.models import NextStep

    lifecycle_requests = []
    page.on(
        "request",
        lambda request: lifecycle_requests.append(request)
        if "/carry-over" in request.url or "/draft-next-sprint" in request.url
        else None,
    )
    carry.click()
    assert "1 unfinished task" in dialogs["messages"][-1]
    assert lifecycle_requests == []
    assert NextStep.objects.filter(plan=alice_plan).count() == 0
    connection.close()

    dialogs["accept"] = True
    page.get_by_test_id("studio-plan-carry-over").click()
    page.wait_for_url(f"{django_server}{routes['studio-plan-editor']}")
    assert len(lifecycle_requests) == 1
    assert NextStep.objects.filter(
        plan=alice_plan, description="Carry this unfinished task",
    ).count() == 1
    connection.close()

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for width, size_label in ((1440, "desktop"), (393, "mobile")):
        page.set_viewport_size({"width": width, "height": 900})
        for theme in ("light", "dark"):
            for surface, route in routes.items():
                page.goto(f"{django_server}{route}", wait_until="domcontentloaded")
                _dismiss_analytics_consent(page)
                _set_theme(page, theme)
                if surface == "studio-plan-editor":
                    page.get_by_label("More actions").click()
                _assert_no_horizontal_overflow(page)
                page.screenshot(
                    path=SCREENSHOT_DIR / f"{surface}-{size_label}-{theme}.png",
                    full_page=True,
                )
    context.close()


@pytest.mark.django_db(transaction=True)
def test_cohort_board_access_matrix(django_server, browser):
    ensure_tiers()
    staff, alice, ghost, outsider, sprint, alice_plan, _bob_plan, ghost_plan = (
        _seed_operations_fixture()
    )
    board_url = f"{django_server}/sprints/{sprint.slug}/board"

    anonymous = browser.new_context()
    anonymous_page = anonymous.new_page()
    anonymous_page.goto(board_url)
    assert "/login" in anonymous_page.url
    anonymous.close()

    for user in (outsider, ghost):
        context = auth_context(browser, user.email)
        page = context.new_page()
        response = page.goto(board_url)
        assert response.status == 404
        context.close()

    member_context = auth_context(browser, alice.email)
    member_page = member_context.new_page()
    response = member_page.goto(board_url)
    assert response.status == 200
    expect(member_page.get_by_test_id("staff-view-label")).to_have_count(0)
    expect(
        member_page.get_by_text("BOB-PRIVATE-FOCUS-SENTINEL")
    ).to_have_count(0)
    member_context.close()

    staff_context = auth_context(browser, staff.email)
    staff_page = staff_context.new_page()
    response = staff_page.goto(board_url)
    assert response.status == 200
    expect(staff_page.get_by_test_id("staff-view-label")).to_have_text("Staff view")
    expect(staff_page.get_by_text("PRIVATE-FOCUS-SENTINEL")).to_have_count(0)
    expect(staff_page.get_by_text("GHOST-PLAN-SENTINEL")).to_have_count(0)
    expect(
        staff_page.locator(f'a[href="/studio/plans/{alice_plan.pk}/"]')
    ).to_have_count(1)
    expect(
        staff_page.locator(f'a[href="/studio/plans/{ghost_plan.pk}/"]')
    ).to_have_count(0)
    staff_context.close()


@pytest.mark.django_db(transaction=True)
def test_checkpoint_blank_drafts_and_meaningful_create_contract(
    django_server, browser,
):
    ensure_tiers()
    staff, _alice, _ghost, _outsider, _sprint, plan, *_ = _seed_operations_fixture()
    context = auth_context(browser, staff.email)
    page = context.new_page()
    writes = []
    page.on(
        "request",
        lambda request: writes.append((request.method, request.url))
        if request.method in {"POST", "PATCH"}
        and ("/api/weeks/" in request.url or "/api/checkpoints/" in request.url)
        else None,
    )
    page.goto(f"{django_server}/studio/plans/{plan.pk}/edit/")
    week = page.locator('[data-week-number="2"]')
    add = week.get_by_test_id("add-checkpoint")
    hint = week.get_by_test_id("empty-week-hint")
    expect(hint).to_be_visible()

    add.click()
    draft = week.get_by_test_id("checkpoint-edit-textarea")
    page.get_by_test_id("summary-goal").click()
    expect(draft).to_have_count(0)
    expect(hint).to_be_visible()

    add.click()
    draft = week.get_by_test_id("checkpoint-edit-textarea")
    draft.fill("   ")
    draft.press("Enter")
    expect(draft).to_have_count(0)
    expect(hint).to_be_visible()

    add.click()
    draft = week.get_by_test_id("checkpoint-edit-textarea")
    draft.press("Escape")
    expect(draft).to_have_count(0)
    expect(hint).to_be_visible()
    assert writes == []

    add.click()
    draft = week.get_by_test_id("checkpoint-edit-textarea")
    draft.fill("Meaningful checkpoint")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and "/api/weeks/" in response.url
        and response.url.endswith("/checkpoints")
    ) as create_response:
        draft.press("Enter")
    assert create_response.value.status == 201
    assert len(writes) == 1
    expect(hint).to_be_hidden()

    from plans.models import Checkpoint

    assert Checkpoint.objects.filter(
        week__plan=plan, week__week_number=2,
        description="Meaningful checkpoint",
    ).count() == 1
    connection.close()

    page.reload(wait_until="domcontentloaded")
    week = page.locator('[data-week-number="2"]')
    checkpoint_text = page.locator(
        '[data-testid="checkpoint-text"]', has_text="Meaningful checkpoint",
    )
    checkpoint = page.locator(
        '[data-week-number="2"] [data-testid="checkpoint-chip"]',
        has=checkpoint_text,
    )
    expect(checkpoint).to_have_count(1)
    checkpoint.get_by_test_id("checkpoint-text").click()
    edit = week.get_by_test_id("checkpoint-edit-textarea")
    edit.fill("  ")
    page.get_by_test_id("summary-goal").click()
    assert len(writes) == 1
    expect(checkpoint).to_contain_text("Meaningful checkpoint")
    expect(page.get_by_test_id("plan-editor-toast")).to_contain_text(
        "delete it instead"
    )
    context.close()
