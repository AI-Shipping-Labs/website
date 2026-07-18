"""Playwright E2E for the next-sprint draft assist (issue #891, Phase 3).

The LLM is stubbed: for the "draft already generated" surfaces we seed a
``NextSprintPlanDraft`` directly in the DB (the generate path is exercised
exhaustively at the Django-test layer), then drive the staff-only Studio
editor panel + dismiss. For the carry-over + AI-off degradation path we
click the real button after forcing the LLM off via an IntegrationSetting
DB override (``LLM_PROVIDER`` -> unimplemented), so the action takes the
documented carry-over-only branch regardless of the ambient environment.

Scenarios mirror the issue spec:
  - Staff reviews a generated draft in the editor (panel + carried tasks).
  - Staff dismisses the draft; the panel disappears, carried tasks remain.
  - The button degrades gracefully when AI is off (carry-over only).
  - A draft renders for a member with no prior plan.
  - A non-staff member cannot trigger the draft action.

Usage:
    uv run pytest playwright_tests/test_plan_next_sprint_draft_891.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

_DRAFT_RESULT = {
    "summary_current_situation": "Shipped a first RAG prototype.",
    "summary_goal": "Turn it into an evaluated pipeline.",
    "summary_main_gap": "No eval harness yet.",
    "summary_weekly_hours": "~6 hours/week",
    "goal": "Ship an evaluated RAG pipeline",
    "suggested_next_steps": ["Build a small eval set", "Wire retrieval metrics"],
    "rationale": "Recent updates show retrieval works but quality is unmeasured.",
}


def _sprint(slug, start):
    from plans.models import Sprint

    return Sprint.objects.create(
        name=f"Sprint {slug}", slug=slug, start_date=start,
        duration_weeks=4, status="active",
    )


def _plan_with_week(member, sprint):
    from plans.models import Plan, Week

    plan = Plan.objects.create(member=member, sprint=sprint)
    week = Week.objects.create(plan=plan, week_number=1, position=0)
    return plan, week


def _seed_draft(plan, *, source_plan=None, update_count=2):
    from django.utils import timezone

    from plans.models import NextSprintPlanDraft

    return NextSprintPlanDraft.objects.create(
        plan=plan,
        result_json=dict(_DRAFT_RESULT),
        source_plan=source_plan,
        update_count=update_count,
        model_name="claude-sonnet-4-5",
        generated_at=timezone.now(),
    )


def _csrf_from(context):
    for cookie in context.cookies():
        if cookie["name"] == "csrftoken":
            return cookie["value"]
    return ""


@pytest.mark.django_db(transaction=True)
class TestReviewGeneratedDraft:
    def test_editor_shows_draft_panel_with_suggestions(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from django.db import connection

            create_staff_user("admin-891a@test.com")
            member = create_user("m-891a@test.com")
            sprint = _sprint("jun-891a", datetime.date(2026, 6, 1))
            plan, _week = _plan_with_week(member, sprint)
            _seed_draft(plan)
            plan_id = plan.pk
            connection.close()

        context = auth_context(browser, "admin-891a@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/edit/",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="next-sprint-draft-panel"]'
            ).count() == 1
            body = page.content()
            assert "AI draft for the next sprint" in body
            assert "Ship an evaluated RAG pipeline" in body
            assert "Build a small eval set" in body
            assert "recent #plan-sprints update" in body
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestDismissDraft:
    def test_dismiss_removes_panel(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from django.db import connection

            create_staff_user("admin-891b@test.com")
            member = create_user("m-891b@test.com")
            sprint = _sprint("jun-891b", datetime.date(2026, 6, 1))
            plan, _week = _plan_with_week(member, sprint)
            _seed_draft(plan)
            plan_id = plan.pk
            connection.close()

        context = auth_context(browser, "admin-891b@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/edit/",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="next-sprint-draft-panel"]'
            ).count() == 1
            page.locator('[data-testid="next-sprint-draft-dismiss"]').click()
            page.wait_for_load_state("domcontentloaded")

            assert page.locator(
                '[data-testid="next-sprint-draft-panel"]'
            ).count() == 0
            with django_db_blocker.unblock():
                from plans.models import NextSprintPlanDraft

                assert NextSprintPlanDraft.objects.filter(plan=plan_id).count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestDegradesWhenAiOff:
    def test_carry_over_only_no_panel(
        self, django_server, django_db_blocker, browser, settings,
    ):
        # Force the AI-off branch deterministically rather than relying on the
        # ambient environment having no LLM_API_KEY. The in-process server
        # reads llm.is_enabled() via integrations.config.get_config(), where a
        # non-empty IntegrationSetting DB row always wins over any env value.
        # Pointing LLM_PROVIDER at an unimplemented provider makes
        # is_provider_implemented() (and therefore is_enabled()) return False
        # regardless of whether LLM_API_KEY is set in the environment. This
        # mirrors how the AI-on tests (test_onboarding_ai_804) toggle LLM via
        # IntegrationSetting DB overrides.
        with django_db_blocker.unblock():
            from django.db import connection

            from integrations.config import clear_config_cache
            from integrations.models import IntegrationSetting
            from plans.models import Checkpoint

            IntegrationSetting.objects.update_or_create(
                key="LLM_PROVIDER", defaults={"value": "__disabled__"},
            )
            clear_config_cache()

            create_staff_user("admin-891c@test.com")
            member = create_user("m-891c@test.com")
            prev = _sprint("may-891c", datetime.date(2026, 5, 1))
            nxt = _sprint("jun-891c", datetime.date(2026, 6, 1))
            source, source_week = _plan_with_week(member, prev)
            source_week.week_number = 3
            source_week.position = 2
            source_week.save(update_fields=["week_number", "position"])
            Checkpoint.objects.create(
                week=source_week, description="Carry me forward", position=0,
            )
            dest, _dest_week = _plan_with_week(member, nxt)
            dest_id = dest.pk
            connection.close()

        context = auth_context(browser, "admin-891c@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{dest_id}/",
                wait_until="domcontentloaded",
            )
            dialogs = []

            def accept_draft(dialog):
                dialogs.append(dialog.message)
                dialog.accept()

            page.once("dialog", accept_draft)
            page.locator(
                '[data-testid="studio-header-overflow"] summary'
            ).click()
            page.locator(
                '[data-testid="studio-plan-draft-next-sprint"]'
            ).click()
            page.wait_for_load_state("domcontentloaded")
            assert dialogs and "draft" in dialogs[0].lower()
            assert "held for review, not published" in dialogs[0]

            body = page.content()
            assert "AI draft was skipped because AI is off" in body
            assert page.locator(
                '[data-testid="next-sprint-draft-panel"]'
            ).count() == 0
            with django_db_blocker.unblock():
                from plans.models import Checkpoint, NextSprintPlanDraft

                assert Checkpoint.objects.filter(week__plan=dest_id).count() == 1
                assert [
                    c.description
                    for c in Checkpoint.objects.filter(
                        week__plan=dest_id,
                        week__week_number=1,
                    )
                ] == ["Carry me forward"]
                assert Checkpoint.objects.filter(
                    week__plan=dest_id,
                    week__week_number=3,
                ).count() == 0
                assert NextSprintPlanDraft.objects.filter(plan=dest_id).count() == 0
        finally:
            context.close()
            with django_db_blocker.unblock():
                from integrations.config import clear_config_cache
                from integrations.models import IntegrationSetting

                IntegrationSetting.objects.filter(key="LLM_PROVIDER").delete()
                clear_config_cache()


@pytest.mark.django_db(transaction=True)
class TestDraftForMemberWithNoPriorPlan:
    def test_panel_renders_from_state_only(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from django.db import connection

            create_staff_user("admin-891d@test.com")
            member = create_user("m-891d@test.com")
            sprint = _sprint("jun-891d", datetime.date(2026, 6, 1))
            plan, _week = _plan_with_week(member, sprint)
            # No prior plan; seed a draft directly (generated from state only).
            _seed_draft(plan, source_plan=None, update_count=0)
            plan_id = plan.pk
            connection.close()

        context = auth_context(browser, "admin-891d@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/edit/",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="next-sprint-draft-panel"]'
            ).count() == 1
            assert "Build a small eval set" in page.content()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestNonStaffCannotDraft:
    def test_member_post_to_draft_is_denied(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from django.db import connection

            create_staff_user("admin-891e@test.com")
            member = create_user("m-891e@test.com")
            sprint = _sprint("jun-891e", datetime.date(2026, 6, 1))
            plan, _week = _plan_with_week(member, sprint)
            create_user("nonstaff-891e@test.com")
            plan_id = plan.pk
            connection.close()

        context = auth_context(browser, "nonstaff-891e@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            resp = context.request.post(
                f"{django_server}/studio/plans/{plan_id}/draft-next-sprint/",
                headers={"X-CSRFToken": _csrf_from(context)},
            )
            assert resp.status in (302, 401, 403, 404)
            with django_db_blocker.unblock():
                from plans.models import NextSprintPlanDraft

                assert NextSprintPlanDraft.objects.filter(plan=plan_id).count() == 0
        finally:
            context.close()
