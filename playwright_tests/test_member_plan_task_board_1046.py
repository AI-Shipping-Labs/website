"""Playwright coverage for the member sprint task board (#1046)."""

import datetime
import os
import re

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_plan_data():
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
    connection.close()


def _seed_board(owner_visibility="private", include_week2_task=True):
    from accounts.models import User
    from plans.models import Checkpoint, Plan, Sprint, SprintEnrollment, Week

    sprint = Sprint.objects.create(
        name="Task Board Sprint",
        slug="task-board-sprint",
        start_date=timezone.localdate() - datetime.timedelta(days=7),
        duration_weeks=2,
    )
    owner = User.objects.get(email="owner@test.com")
    teammate = User.objects.get(email="teammate@test.com")
    SprintEnrollment.objects.create(sprint=sprint, user=owner)
    SprintEnrollment.objects.create(sprint=sprint, user=teammate)
    plan = Plan.objects.create(
        member=owner,
        sprint=sprint,
        visibility=owner_visibility,
    )
    week1 = Week.objects.create(plan=plan, week_number=1, position=0)
    week2 = Week.objects.create(plan=plan, week_number=2, position=1)
    Checkpoint.objects.create(
        week=week1,
        description="Ship demo",
        position=0,
        done_at=timezone.now(),
    )
    Checkpoint.objects.create(
        week=week1,
        description="Write notes",
        position=1,
    )
    Checkpoint.objects.create(
        week=week1,
        description="Record walkthrough",
        position=2,
    )
    if include_week2_task:
        Checkpoint.objects.create(
            week=week2,
            description="Review feedback",
            position=0,
        )
    Plan.objects.create(member=teammate, sprint=sprint, visibility="private")
    connection.close()
    return {"sprint_slug": sprint.slug, "plan_id": plan.pk}


def _drag_card(page, source_card, target_card, drop="before"):
    handle = source_card.locator("[data-checkpoint-drag-handle]").first
    handle.scroll_into_view_if_needed()
    source_box = handle.bounding_box()
    assert source_box is not None
    sx = source_box["x"] + source_box["width"] / 2
    sy = source_box["y"] + source_box["height"] / 2
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + 10, sy + 10, steps=5)

    target_card.scroll_into_view_if_needed()
    target_box = target_card.bounding_box()
    assert target_box is not None
    tx = target_box["x"] + target_box["width"] / 2
    if drop == "before":
        approach_y = target_box["y"] - 2
        commit_y = target_box["y"] + 5
    else:
        approach_y = target_box["y"] + target_box["height"] + 2
        commit_y = target_box["y"] + target_box["height"] - 5

    steps = max(int(max(abs(approach_y - sy), abs(tx - sx))), 30)
    page.mouse.move(tx, approach_y, steps=steps)
    page.mouse.move(tx, commit_y, steps=2)
    page.mouse.up()


def _drag_card_to_week(page, source_card, week_number):
    handle = source_card.locator("[data-checkpoint-drag-handle]").first
    handle.scroll_into_view_if_needed()
    source_box = handle.bounding_box()
    assert source_box is not None
    sx = source_box["x"] + source_box["width"] / 2
    sy = source_box["y"] + source_box["height"] / 2
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + 10, sy + 10, steps=5)

    target_list = page.locator(
        f'[data-testid="plan-week"]:has-text("Week {week_number}") '
        f'[data-testid="checkpoint-list"]'
    )
    target_list.scroll_into_view_if_needed()
    target_box = target_list.bounding_box()
    assert target_box is not None
    tx = target_box["x"] + target_box["width"] / 2
    ty = target_box["y"] + min(24, target_box["height"] / 2)

    steps = max(int(max(abs(ty - sy), abs(tx - sx))), 30)
    page.mouse.move(tx, ty, steps=steps)
    page.mouse.up()


def _week_descriptions(page, week_number):
    return _week_text_locators(page, week_number).all_text_contents()


def _week_text_locators(page, week_number):
    return page.locator(
        f'[data-testid="plan-week"]:has-text("Week {week_number}") '
        f'[data-testid="plan-checkpoint"] [data-checkpoint-text]'
    )


@pytest.mark.django_db(transaction=True)
class TestMemberTaskBoard1046:
    def test_owner_completes_edits_and_reopens_task(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("owner@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_board()

        context = _auth_context(browser, "owner@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until="networkidle",
            )

            notes = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Write notes",
            )
            assert notes.locator('[data-testid="plan-item-edit"]').count() == 0
            with page.expect_response("**/api/checkpoints/*"):
                notes.locator('[data-testid="plan-row-done-toggle"]').check()
            expect(notes.locator("[data-checkpoint-text]")).to_have_class(
                re.compile("line-through"),
            )
            expect(page.locator('[data-testid="my-plan-progress"]')).to_contain_text(
                "2 of 4 checkpoints done",
            )

            with page.expect_response("**/api/checkpoints/*"):
                notes.locator('[data-testid="plan-row-done-toggle"]').uncheck()
            expect(notes.locator("[data-checkpoint-text]")).not_to_have_class(
                re.compile("line-through"),
            )

            notes.locator("[data-checkpoint-text]").click()
            notes.locator('[data-testid="plan-item-markdown-input"]').fill(
                "Write **RAG** notes",
            )
            with page.expect_response("**/api/checkpoints/*"):
                notes.locator('[data-testid="plan-item-save"]').click()
            expect(
                page.locator('[data-testid="plan-checkpoint"]')
                .filter(has_text="Write RAG notes")
                .locator("strong"),
            ).to_have_text("RAG")

            page.reload(wait_until="networkidle")
            expect(page.get_by_text("Write RAG notes")).to_be_visible()
            expect(page.locator('[data-testid="my-plan-progress"]')).to_contain_text(
                "1 of 4 checkpoints done",
            )
        finally:
            context.close()

    def test_owner_cancels_task_edit(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("owner@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_board()

        context = _auth_context(browser, "owner@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until="networkidle",
            )

            notes = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Write notes",
            )
            notes.locator("[data-checkpoint-text]").click()
            notes.locator('[data-testid="plan-item-markdown-input"]').fill(
                "Wrong text",
            )
            notes.locator('[data-testid="plan-item-markdown-input"]').press(
                "Escape",
            )

            expect(notes.locator("[data-checkpoint-text]")).to_contain_text(
                "Write notes",
            )
            expect(notes.locator("[data-checkpoint-text]")).not_to_contain_text(
                "Wrong text",
            )

            page.reload(wait_until="networkidle")
            expect(
                page.locator(
                    '[data-testid="plan-checkpoint"] [data-checkpoint-text]',
                    has_text="Write notes",
                ),
            ).to_be_visible()
            assert page.locator(
                '[data-testid="plan-checkpoint"] [data-checkpoint-text]',
                has_text="Wrong text",
            ).count() == 0
        finally:
            context.close()

    def test_keyboard_focused_owner_edits_task(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("owner@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_board()

        context = _auth_context(browser, "owner@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until="networkidle",
            )

            notes = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Write notes",
            )
            checkpoint_id = notes.get_attribute("data-checkpoint-id")
            notes = page.locator(
                f'[data-testid="plan-checkpoint"][data-checkpoint-id="{checkpoint_id}"]'
            )
            notes.focus()
            notes.press("F2")
            notes.locator('[data-testid="plan-item-markdown-input"]').fill(
                "Write keyboard notes",
            )
            with page.expect_response("**/api/checkpoints/*"):
                notes.locator('[data-testid="plan-item-save"]').click()

            expect(notes.locator("[data-checkpoint-text]")).to_contain_text(
                "Write keyboard notes",
            )
            expect(notes).to_be_focused()
        finally:
            context.close()

    def test_failed_member_edit_restores_prior_text(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("owner@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_board()

        context = _auth_context(browser, "owner@test.com")
        try:
            page = context.new_page()
            page.route(
                "**/api/checkpoints/*",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"error": "temporary", "code": "temporary"}',
                ),
            )
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until="networkidle",
            )

            notes = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Write notes",
            )
            notes.locator("[data-checkpoint-text]").click()
            notes.locator('[data-testid="plan-item-markdown-input"]').fill(
                "Write changed notes",
            )
            notes.locator('[data-testid="plan-item-save"]').click()

            expect(
                page.locator('[data-testid="member-plan-task-error"]'),
            ).to_be_visible()
            expect(notes.locator("[data-checkpoint-text]")).to_contain_text(
                "Write notes",
            )
            expect(notes.locator("[data-checkpoint-text]")).not_to_contain_text(
                "Write changed notes",
            )

            page.reload(wait_until="networkidle")
            expect(
                page.locator(
                    '[data-testid="plan-checkpoint"] [data-checkpoint-text]',
                    has_text="Write notes",
                ),
            ).to_be_visible()
            assert page.locator(
                '[data-testid="plan-checkpoint"] [data-checkpoint-text]',
                has_text="Write changed notes",
            ).count() == 0
        finally:
            context.close()

    def test_owner_drags_and_bulk_moves_unfinished_tasks(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("owner@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_board()

        context = _auth_context(browser, "owner@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until="networkidle",
            )

            assert (
                page.locator(
                    '[data-testid="move-incomplete-to-next-week"]',
                ).count() == 1
            )

            record = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Record walkthrough",
            )
            notes = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Write notes",
            )
            with page.expect_response("**/api/checkpoints/*/move"):
                _drag_card(page, record, notes)
            expect(page.locator('[data-testid="my-plan-progress"]')).to_contain_text(
                "1 of 4 checkpoints done",
            )
            assert _week_descriptions(page, 1) == [
                "Ship demo",
                "Record walkthrough",
                "Write notes",
            ]

            with page.expect_response("**/api/checkpoints/*/move"):
                page.locator(
                    '[data-testid="move-incomplete-to-next-week"]',
                ).click()
            expect(_week_text_locators(page, 2)).to_have_text(
                ["Record walkthrough", "Write notes", "Review feedback"]
            )
            assert _week_descriptions(page, 1) == ["Ship demo"]

            page.reload(wait_until="networkidle")
            assert _week_descriptions(page, 1) == ["Ship demo"]
            assert _week_descriptions(page, 2) == [
                "Record walkthrough",
                "Write notes",
                "Review feedback",
            ]
        finally:
            context.close()

    def test_owner_drags_task_to_another_week(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("owner@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_board(include_week2_task=False)

        context = _auth_context(browser, "owner@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until="networkidle",
            )

            notes = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Write notes",
            )
            with page.expect_response("**/api/checkpoints/*/move"):
                _drag_card_to_week(page, notes, 2)

            assert _week_descriptions(page, 1) == ["Ship demo", "Record walkthrough"]
            assert _week_descriptions(page, 2) == ["Write notes"]

            page.reload(wait_until="networkidle")
            assert _week_descriptions(page, 1) == ["Ship demo", "Record walkthrough"]
            assert _week_descriptions(page, 2) == ["Write notes"]
        finally:
            context.close()

    def test_failed_member_move_reverts_board_once(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("owner@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_board()

        context = _auth_context(browser, "owner@test.com")
        try:
            page = context.new_page()
            page.route(
                "**/api/checkpoints/*/move",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"error": "temporary", "code": "temporary"}',
                ),
            )
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until="networkidle",
            )

            record = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Record walkthrough",
            )
            notes = page.locator('[data-testid="plan-checkpoint"]').filter(
                has_text="Write notes",
            )
            with page.expect_response("**/api/checkpoints/*/move"):
                _drag_card(page, record, notes)
            expect(
                page.locator('[data-testid="member-plan-task-error"]'),
            ).to_be_visible()
            expect(_week_text_locators(page, 1)).to_have_text(
                ["Ship demo", "Write notes", "Record walkthrough"]
            )
        finally:
            context.close()

    def test_teammate_shared_plan_is_read_only(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("owner@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_board(owner_visibility="cohort")

        context = _auth_context(browser, "teammate@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plans/{data['plan_id']}",
                wait_until="networkidle",
            )

            expect(page.get_by_text("Write notes")).to_be_visible()
            assert page.locator('[data-testid="plan-row-done-toggle"]').count() == 0
            assert page.locator("[data-checkpoint-drag-handle]").count() == 0
            assert page.locator('[data-testid="plan-item-edit"]').count() == 0
            assert (
                page.locator(
                    '[data-testid="move-incomplete-to-next-week"]',
                ).count() == 0
            )
        finally:
            context.close()
