"""Playwright E2E for archetype-aware onboarding questioning (issue #823).

The AI chat must tailor its DELTA questions to the inferred archetype
DURING the conversation and route the completed response to that
archetype's questionnaire. The LLM is mocked in-process at
``questionnaires.onboarding_ai.llm.complete`` so the assistant turns are
deterministic -- CI never makes a live call. The mocked assistant turn
stands in for what an archetype-aware model would say given the
archetype-aware system prompt (whose content is asserted in the Django
suite ``test_onboarding_ai_archetype_823.py``); here we verify the
user-facing flow: the archetype-appropriate question shows in the
transcript, the completion routes to the right questionnaire, and no
codename leaks.

Screenshots are written to ``.tmp/aisl-issue-823-screenshots``.
"""

import os
import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "aisl-issue-823-screenshots"

PERSONA_NAMES = ["Alex", "Priya", "Sam", "Taylor"]

# Base extraction; per-test we override persona_signal + a few fields so
# the completion routes to the inferred persona's questionnaire.
BASE_EXTRACTION = {
    "persona_signal": "alex",
    "eng_comfort": 4,
    "ai_comfort": 2,
    "primary_goal": "Ship a RAG chatbot for my docs",
    "goal_category": "ship_new",
    "time_commitment_hours_per_week": 8,
    "time_profile": "steady",
    "main_blocker": "scoping",
    "secondary_blockers": ["time"],
    "accountability_preference": ["Weekly check-ins"],
    "current_project": "A docs assistant",
    "project_stage": "idea",
    "target_outcome": "A deployed assistant my team uses",
    "career_direction": "ai_engineer",
    "tech_stack_known": ["Python"],
    "tech_stack_gaps": ["vector DBs"],
    "in_scope": ["retrieval"],
    "out_of_scope": ["fine-tuning"],
    "coding_agent_use": "boilerplate_only",
    "support_wanted": ["Architecture"],
    "learning_track_links": [],
    "hard_deadline": None,
    "plan_horizon": "single_sprint",
    "notes": "Moving from backend to AI.",
}

# Archetype-appropriate follow-ups the mocked assistant returns. These
# mirror the delta emphasis the archetype-aware system prompt produces.
ALEX_FOLLOWUP = (
    "Which AI area would you like to focus on first -- RAG, agents, or "
    "evaluation? And would you prefer a project-first or foundations-first "
    "path?"
)
TAYLOR_FOLLOWUP = (
    "Which part of the production pipeline -- deployment, CI/CD, or the "
    "evaluation loop -- do you most want hands-on experience with, and "
    "where do you see your career direction heading?"
)


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _ensure_onboarding_seed():
    import importlib

    from django.apps import apps as django_apps

    seed_module = importlib.import_module(
        "questionnaires.migrations.0003_seed_personas_and_onboarding",
    )
    seed_module.seed(django_apps, None)
    connection.close()


def _reset():
    from questionnaires.models import Response

    _ensure_onboarding_seed()
    Response.objects.all().delete()
    connection.close()


@contextmanager
def _llm_enabled():
    """Enable the LLM (non-streaming POST path) via DB config."""
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    keys = [
        "LLM_API_KEY",
        "LLM_PROVIDER",
        "ONBOARDING_AI_ENABLED",
        "ONBOARDING_AI_STREAMING",
    ]
    IntegrationSetting.objects.update_or_create(
        key="LLM_API_KEY", defaults={"value": "sk-test-fake"},
    )
    IntegrationSetting.objects.update_or_create(
        key="LLM_PROVIDER", defaults={"value": "anthropic"},
    )
    IntegrationSetting.objects.update_or_create(
        key="ONBOARDING_AI_ENABLED", defaults={"value": "true"},
    )
    # Exercise the deterministic non-streaming POST path.
    IntegrationSetting.objects.update_or_create(
        key="ONBOARDING_AI_STREAMING", defaults={"value": "false"},
    )
    clear_config_cache()
    connection.close()
    try:
        yield
    finally:
        IntegrationSetting.objects.filter(key__in=keys).delete()
        clear_config_cache()
        connection.close()


def _reply(text="", tool_input=None):
    from integrations.services.llm import LLMResult

    return LLMResult(
        text=text, tool_input=tool_input,
        tool_name="record_onboarding" if tool_input else None,
    )


def _onboarding_response(email):
    from accounts.models import User
    from questionnaires.models import Response

    user = User.objects.get(email=email)
    resp = (
        Response.objects.filter(
            respondent=user, questionnaire__purpose="onboarding",
        )
        .order_by("created_at")
        .first()
    )
    connection.close()
    return resp


def _open_chat(page, django_server):
    page.goto(f"{django_server}/onboarding/chat", wait_until="domcontentloaded")
    page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
        state="visible",
    )


def _send(page, message, *, reply_text="", tool_input=None):
    with patch(
        "questionnaires.onboarding_ai.llm.complete",
        return_value=_reply(text=reply_text, tool_input=tool_input),
    ):
        page.locator('[data-testid="onboarding-chat-input"]').fill(message)
        page.locator('[data-testid="onboarding-chat-send"]').click()
        page.wait_for_load_state("domcontentloaded")


@pytest.mark.django_db(transaction=True)
class TestEngineerNewToAiBranch:
    @pytest.mark.core
    def test_engineer_asked_ai_area_not_deployment_ops(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("alexbranch@test.com", tier_slug="main",
                     email_verified=True)
        context = _auth_context(browser, "alexbranch@test.com")
        page = context.new_page()

        with _llm_enabled():
            _open_chat(page, django_server)
            # The member presents as a strong engineer new to AI; the
            # archetype-aware assistant follows up on AI-area / project-first.
            _send(
                page,
                "I'm a senior backend engineer with 8 years in Python. I've "
                "never used an LLM API but want to ship a RAG service.",
                reply_text=ALEX_FOLLOWUP,
            )
            transcript = page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).inner_text()
            # Alex deltas: AI-area + project-first-vs-foundations.
            assert "AI area" in transcript
            assert "project-first" in transcript.lower()
            # NOT Taylor's MLOps/career-direction emphasis.
            assert "career direction" not in transcript.lower()
            _shot(page, "alex_followup")

            # Complete -> routes to the Engineer-transitioning questionnaire.
            extraction = dict(BASE_EXTRACTION, persona_signal="alex")
            _send(page, "Here are my answers", tool_input=extraction)
            assert "plan" in page.content().lower()
            _shot(page, "alex_complete")

        resp = _onboarding_response("alexbranch@test.com")
        assert resp.status == "submitted"
        assert resp.questionnaire.slug == "onboarding-alex"
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestResearchToEngineeringBranch:
    @pytest.mark.core
    def test_researcher_asked_production_deployment_questions(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("taylorbranch@test.com", tier_slug="main",
                     email_verified=True)
        context = _auth_context(browser, "taylorbranch@test.com")
        page = context.new_page()

        with _llm_enabled():
            _open_chat(page, django_server)
            _send(
                page,
                "I'm a researcher with strong modeling and theory, but I've "
                "never deployed anything to production.",
                reply_text=TAYLOR_FOLLOWUP,
            )
            transcript = page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).inner_text()
            # Taylor deltas: deployment / CI-CD / career direction.
            lowered = transcript.lower()
            assert "deployment" in lowered or "ci/cd" in lowered
            assert "career direction" in lowered
            # NOT the Alex AI-area / project-first emphasis.
            assert "project-first" not in lowered
            _shot(page, "taylor_followup")

            extraction = dict(
                BASE_EXTRACTION,
                persona_signal="taylor",
                career_direction="ai_platform_mlops",
            )
            _send(page, "Here are my answers", tool_input=extraction)
            assert "plan" in page.content().lower()
            _shot(page, "taylor_complete")

        resp = _onboarding_response("taylorbranch@test.com")
        assert resp.status == "submitted"
        assert resp.questionnaire.slug == "onboarding-taylor"
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestNoCodenameLeak:
    @pytest.mark.core
    def test_assistant_never_leaks_persona_codename(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("leakask@test.com", tier_slug="main",
                     email_verified=True)
        context = _auth_context(browser, "leakask@test.com")
        page = context.new_page()

        with _llm_enabled():
            _open_chat(page, django_server)
            # The model is goaded into naming a persona; the backstop strips
            # it before it reaches the transcript.
            _send(
                page,
                "Which persona am I -- Alex, Priya, Sam, or Taylor?",
                reply_text=(
                    "You're clearly a Priya, and Alex would also fit your "
                    "profile."
                ),
            )
            transcript = page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            )
            assistant_msgs = page.locator(
                '[data-testid="onboarding-chat-assistant"]'
            ).all_inner_texts()
            assistant_text = "\n".join(assistant_msgs)
            for name in PERSONA_NAMES:
                assert not re.search(
                    rf"\b{re.escape(name)}\b", assistant_text
                ), f"persona codename {name!r} leaked in assistant message"
            _shot(page, "no_codename_leak")
            # Sanity: the assistant did reply (transcript isn't empty).
            assert transcript.inner_text().strip()


@pytest.mark.django_db(transaction=True)
class TestResumeInProgressChat:
    @pytest.mark.core
    def test_member_resumes_existing_chat_not_restarted(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("resumer@test.com", tier_slug="main",
                     email_verified=True)
        context = _auth_context(browser, "resumer@test.com")
        page = context.new_page()

        with _llm_enabled():
            _open_chat(page, django_server)
            _send(
                page,
                "I'm a backend engineer moving into AI.",
                reply_text=ALEX_FOLLOWUP,
            )
            # Navigate away, then return to /onboarding/.
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            page.goto(
                f"{django_server}/onboarding/", wait_until="domcontentloaded",
            )
            page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).wait_for(state="visible")
            transcript = page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).inner_text()
            # The prior member turn + assistant follow-up are still shown
            # (the chat resumed, not restarted from the generic opening).
            assert "backend engineer moving into AI" in transcript
            assert "AI area" in transcript
            _shot(page, "resumed_chat")
