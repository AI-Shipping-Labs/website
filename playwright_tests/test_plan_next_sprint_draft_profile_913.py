"""Playwright E2E for member-profile injection into the draft (issue #913).

The #883 member-profile context (onboarding answers + CRM persona/summary/
next-steps) is fed into the #891 next-sprint draft path so the LLM draft is
informed by the profile, not just plan state + recent updates.

The LLM is stubbed throughout: the in-process server runs in the same
process as the test, so ``patch('plans.services.next_sprint_draft.llm.
complete', ...)`` intercepts the real generate path while the staff user
clicks the real "Draft next sprint plan" button. The stub's ``side_effect``
inspects the user message it receives, so the test verifies the profile
actually reached generation (not merely that a draft rendered).

The LLM service is enabled for the server thread via IntegrationSetting DB
overrides (mirrors ``test_onboarding_ai_804._llm_enabled``).

Scenarios mirror the issue spec:
  - Staff drafts for a member with a known onboarding profile -> the draft
    reflects the stated goal/persona (profile was fed into generation).
  - Staff drafts for a member who skipped onboarding -> the draft still
    generates from plan state alone, no error, editor stays usable.

Usage:
    uv run pytest playwright_tests/test_plan_next_sprint_draft_profile_913.py -v
"""

import datetime
import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


@contextmanager
def _llm_enabled():
    """Enable the LLM service for the server thread via DB config."""
    from django.db import connection

    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    keys = ["LLM_API_KEY", "LLM_PROVIDER"]
    IntegrationSetting.objects.update_or_create(
        key="LLM_API_KEY", defaults={"value": "sk-test-fake"},
    )
    IntegrationSetting.objects.update_or_create(
        key="LLM_PROVIDER", defaults={"value": "anthropic"},
    )
    clear_config_cache()
    connection.close()
    try:
        yield
    finally:
        IntegrationSetting.objects.filter(key__in=keys).delete()
        clear_config_cache()
        connection.close()


def _ensure_onboarding_seed():
    """Idempotently re-create the onboarding seed (#801 migration data).

    Other transactional Playwright suites (e.g. ``test_studio_questionnaires``,
    ``test_sprint_feedback_803``) create-and-truncate questionnaires within the
    shared transactional test DB, which orphans the migration-seeded
    ``onboarding-general`` row. Re-running the data-migration ``seed()`` keeps
    this suite self-contained regardless of test ordering, so
    ``_onboarding_questionnaire()`` never hits ``DoesNotExist``.
    """
    import importlib

    from django.apps import apps as django_apps
    from django.db import connection

    seed_module = importlib.import_module(
        "questionnaires.migrations.0003_seed_personas_and_onboarding",
    )
    seed_module.seed(django_apps, None)
    connection.close()


def _onboarding_questionnaire():
    from questionnaires.models import Questionnaire

    return Questionnaire.objects.get(slug="onboarding-general")


def _seed_profiled_member(email):
    """A member with a submitted onboarding response + CRM record."""
    from crm.models import CRMRecord
    from questionnaires.models import Answer, Response, ResponseQuestion

    member = create_user(email)
    response = Response.objects.create(
        questionnaire=_onboarding_questionnaire(),
        respondent=member,
        status="submitted",
    )
    qa = [
        ("What are your goals?", "Switch into an AI engineering role"),
        ("Background?", "Ten years of backend Java"),
    ]
    for order, (prompt, text) in enumerate(qa):
        rq = ResponseQuestion.objects.create(
            response=response, question_type="long_text",
            prompt=prompt, order=order,
        )
        Answer.objects.create(response=response, question=rq, text_value=text)
    CRMRecord.objects.create(
        user=member, persona="Sam — Technical Professional",
        summary="Strong engineer, needs a portfolio piece.",
        next_steps="Ship a RAG project this sprint.",
    )
    return member


def _sprint(slug):
    from plans.models import Sprint

    return Sprint.objects.create(
        name=f"Sprint {slug}", slug=slug,
        start_date=timezone.localdate() - datetime.timedelta(days=7),
        duration_weeks=4,
        status="active",
    )


def _plan_with_week(member, sprint):
    from plans.models import Plan, Week

    plan = Plan.objects.create(member=member, sprint=sprint)
    Week.objects.create(plan=plan, week_number=1, position=0)
    return plan


def _profile_aware_complete(seen):
    """A ``llm.complete`` stub that records the user message it received.

    Returns a draft whose ``goal`` reflects the onboarding goal only when
    the profile block actually reached the prompt — proving the wiring end
    to end rather than asserting on a hard-coded stub alone.
    """

    def _complete(messages, *args, **kwargs):
        from integrations.services.llm import LLMResult

        user_text = "".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        seen.append(user_text)
        if (
            "=== Member profile ===" in user_text
            and "Switch into an AI engineering role" in user_text
        ):
            goal = "Land an AI engineering role via a shipped RAG project"
        else:
            goal = "Continue from plan state only"
        return LLMResult(tool_input={
            "summary_current_situation": "Has a backend background.",
            "summary_goal": "Build an evaluated RAG pipeline.",
            "summary_main_gap": "No portfolio piece yet.",
            "summary_weekly_hours": "~6 hours/week",
            "goal": goal,
            "suggested_next_steps": ["Scope a RAG project", "Build an eval set"],
            "rationale": "Grounded in the onboarding profile and plan state.",
        })

    return _complete


@pytest.mark.django_db(transaction=True)
class TestDraftReflectsMemberProfile:
    def test_staff_drafts_profile_informed_next_sprint(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from django.db import connection

            _ensure_onboarding_seed()
            create_staff_user("admin-913a@test.com")
            member = _seed_profiled_member("m-913a@test.com")
            plan = _plan_with_week(member, _sprint("jun-913a"))
            plan_id = plan.pk
            connection.close()

        seen = []
        context = auth_context(browser, "admin-913a@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/",
                wait_until="domcontentloaded",
            )
            with django_db_blocker.unblock(), _llm_enabled(), patch(
                "plans.services.next_sprint_draft.llm.complete",
                side_effect=_profile_aware_complete(seen),
            ):
                dialogs = []
                page.once(
                    "dialog",
                    lambda dialog: (
                        dialogs.append(dialog.message), dialog.accept()
                    ),
                )
                page.locator(
                    '[data-testid="studio-header-overflow"] summary'
                ).click()
                page.locator(
                    '[data-testid="studio-plan-draft-next-sprint"]'
                ).click()
                page.wait_for_load_state("domcontentloaded")
                assert dialogs and "held for review, not published" in dialogs[0]

            panel = page.locator('[data-testid="next-sprint-draft-panel"]')
            assert panel.count() == 1
            body = page.content()
            # The draft reflects the member's stated onboarding goal/persona,
            # which only happens when the profile reached generation.
            assert "Land an AI engineering role via a shipped RAG project" in body
            # And the stub confirms the profile block was in the prompt.
            assert seen and "=== Member profile ===" in seen[0]
            assert "Switch into an AI engineering role" in seen[0]
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestDraftForMemberWhoSkippedOnboarding:
    def test_draft_still_generates_from_plan_state_only(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from django.db import connection

            create_staff_user("admin-913b@test.com")
            # No onboarding response and no CRM record for this member.
            # Account signup activity is still part of the member profile
            # context after #1054.
            member = create_user("m-913b@test.com")
            plan = _plan_with_week(member, _sprint("jun-913b"))
            plan_id = plan.pk
            connection.close()

        seen = []
        context = auth_context(browser, "admin-913b@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/",
                wait_until="domcontentloaded",
            )
            with django_db_blocker.unblock(), _llm_enabled(), patch(
                "plans.services.next_sprint_draft.llm.complete",
                side_effect=_profile_aware_complete(seen),
            ):
                dialogs = []
                page.once(
                    "dialog",
                    lambda dialog: (
                        dialogs.append(dialog.message), dialog.accept()
                    ),
                )
                page.locator(
                    '[data-testid="studio-header-overflow"] summary'
                ).click()
                page.locator(
                    '[data-testid="studio-plan-draft-next-sprint"]'
                ).click()
                page.wait_for_load_state("domcontentloaded")
                assert dialogs and "held for review, not published" in dialogs[0]

            # The draft still generates — no error, panel renders, editor usable.
            panel = page.locator('[data-testid="next-sprint-draft-panel"]')
            assert panel.count() == 1
            body = page.content()
            assert "Continue from plan state only" in body
            assert "Scope a RAG project" in body
            # The profile block may contain recent activity even when the
            # member skipped onboarding, but it must not invent onboarding /
            # CRM facts and the draft still falls back to plan state.
            assert seen
            assert "=== Member profile ===" in seen[0]
            assert "Recent activity:" in seen[0]
            assert "Signup: Signed up" in seen[0]
            assert "Switch into an AI engineering role" not in seen[0]
            assert "CRM summary:" not in seen[0]
            assert "Onboarding answers:" not in seen[0]
            assert (
                "(no recent updates — draft from plan state only)"
                in seen[0]
            )
        finally:
            context.close()
