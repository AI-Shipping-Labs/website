"""Playwright E2E for the AI onboarding chat flow (issue #804).

Covers the user-visible scenarios from the issue: chatting through the
interview to completion (same #800 artifacts as the form), switching to
the form, graceful fallback when the LLM call fails, the form-only path
when the LLM is disabled, the already-onboarded confirmation, the
staff-only persona signal, cross-member isolation, and the anonymous
redirect.

The Django dev server runs in the SAME process as the test (a background
thread), so the LLM boundary is mocked in-process with
``unittest.mock.patch('questionnaires.onboarding_ai.llm.complete', ...)``
-- CI never makes a live call. The LLM service is enabled by writing the
``LLM_API_KEY`` config to the DB (read via ``get_config``).

Screenshots are written to ``.tmp/aisl-issue-804-screenshots`` for tester
review.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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

pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "aisl-issue-804-screenshots"

PERSONA_NAMES = ["Alex", "Priya", "Sam", "Taylor"]

# A complete, valid extraction the mocked tool call returns to finish the
# interview. Mirrors the appendix schema.
EXTRACTION = {
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
def _llm_enabled(enabled=True):
    """Enable/disable the LLM service via DB config for the server thread."""
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    if enabled:
        IntegrationSetting.objects.update_or_create(
            key="LLM_API_KEY", defaults={"value": "sk-test-fake"},
        )
        IntegrationSetting.objects.update_or_create(
            key="LLM_PROVIDER", defaults={"value": "anthropic"},
        )
    else:
        IntegrationSetting.objects.filter(
            key__in=["LLM_API_KEY", "LLM_PROVIDER"],
        ).delete()
    clear_config_cache()
    connection.close()
    try:
        yield
    finally:
        IntegrationSetting.objects.filter(
            key__in=["LLM_API_KEY", "LLM_PROVIDER"],
        ).delete()
        clear_config_cache()
        connection.close()


def _reply(text="", tool_input=None):
    """Build a fake LLMResult for the mocked complete()."""
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


@pytest.mark.django_db(transaction=True)
class TestChatCompletion:
    @pytest.mark.core
    def test_member_completes_onboarding_by_chatting(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("chatter@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "chatter@test.com")
        page = context.new_page()

        with _llm_enabled():
            # Dashboard prompt invites onboarding.
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            page.locator('[data-testid="onboarding-prompt"]').wait_for(
                state="visible",
            )

            # Follow the CTA -> the chat surface greets them.
            page.locator('[data-testid="onboarding-prompt-cta"]').click()
            page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
                state="visible",
            )
            assert page.locator(
                '[data-testid="onboarding-chat-assistant"]'
            ).count() >= 1
            _shot(page, "chat_greeting")

            # Reply once: the assistant asks a sensible follow-up, no name leak.
            with patch(
                "questionnaires.onboarding_ai.llm.complete",
                return_value=_reply(text="Thanks! What blocks your consistency?"),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "I want to ship a RAG app",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                page.wait_for_load_state("domcontentloaded")
            transcript = page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).inner_text()
            assert "What blocks your consistency?" in transcript
            for name in PERSONA_NAMES:
                assert name not in transcript, f"persona name {name} leaked"
            _shot(page, "chat_followup")

            # Final reply completes the interview.
            with patch(
                "questionnaires.onboarding_ai.llm.complete",
                return_value=_reply(text="All set!", tool_input=dict(EXTRACTION)),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "Here is everything",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                page.wait_for_load_state("domcontentloaded")
            assert "plan" in page.content().lower()
            _shot(page, "chat_complete")

            # Back on the dashboard, the prompt is gone.
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            assert page.locator('[data-testid="onboarding-prompt"]').count() == 0

        # Same #800 artifacts as the form: one submitted Response w/ answers.
        resp = _onboarding_response("chatter@test.com")
        assert resp.status == "submitted"
        from accounts.models import User
        from questionnaires.models import Answer, Response

        user = User.objects.get(email="chatter@test.com")
        assert Response.objects.filter(
            respondent=user, questionnaire__purpose="onboarding",
        ).count() == 1
        assert Answer.objects.filter(response=resp).exists()
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestSwitchToForm:
    @pytest.mark.core
    def test_member_switches_to_form(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("switcher@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "switcher@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat", wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-switch-to-form"]').click()
            page.locator('[data-testid="questionnaire-response-form"]').wait_for(
                state="visible",
            )
            _shot(page, "switched_to_form")
            # They can submit the form (generic questions are optional).
            page.locator('[data-testid="questionnaire-submit-button"]').click()
            page.wait_for_load_state("domcontentloaded")

        resp = _onboarding_response("switcher@test.com")
        assert resp.status == "submitted"


@pytest.mark.django_db(transaction=True)
class TestFailureFallback:
    @pytest.mark.core
    def test_llm_failure_falls_back_to_form(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("failover@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "failover@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat", wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
                state="visible",
            )
            from integrations.services.llm import LLMError

            with patch(
                "questionnaires.onboarding_ai.llm.complete",
                side_effect=LLMError("down"),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "my next message",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                page.wait_for_load_state("domcontentloaded")
            # No server error -- the form fallback renders.
            page.locator('[data-testid="questionnaire-response-form"]').wait_for(
                state="visible",
            )
            _shot(page, "failure_fallback")
            page.locator('[data-testid="questionnaire-submit-button"]').click()
            page.wait_for_load_state("domcontentloaded")

        resp = _onboarding_response("failover@test.com")
        assert resp.status == "submitted"


@pytest.mark.django_db(transaction=True)
class TestLlmDisabled:
    @pytest.mark.core
    def test_form_only_when_llm_disabled(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("noai@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "noai@test.com")
        page = context.new_page()
        with _llm_enabled(enabled=False):
            page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
            # The form-first flow renders; chat is never offered.
            page.locator('[data-testid="onboarding-identify-form"]').wait_for(
                state="visible",
            )
            assert page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).count() == 0
            _shot(page, "llm_disabled_form")


@pytest.mark.django_db(transaction=True)
class TestAlreadyOnboarded:
    @pytest.mark.core
    def test_already_onboarded_not_restarted(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("done@test.com", tier_slug="main", email_verified=True)
        from accounts.models import User
        from questionnaires.models import Questionnaire, Response

        user = User.objects.get(email="done@test.com")
        generic = Questionnaire.objects.get(slug="onboarding-general")
        Response.objects.create(
            questionnaire=generic, respondent=user, status="submitted",
        )
        connection.close()

        context = _auth_context(browser, "done@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
            page.locator('[data-testid="onboarding-complete-title"]').wait_for(
                state="visible",
            )
            assert page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).count() == 0
            _shot(page, "already_onboarded")


@pytest.mark.django_db(transaction=True)
class TestPersonaSignalStaffOnly:
    @pytest.mark.core
    def test_persona_signal_staff_only(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_staff_user("staff804@test.com")
        _create_user("signal@test.com", tier_slug="main", email_verified=True)

        # Complete the AI interview for the member (mocked).
        from questionnaires.services_onboarding_ai import (
            get_or_create_ai_onboarding_response,
            run_member_turn,
        )

        with _llm_enabled():
            from accounts.models import User

            member = User.objects.get(email="signal@test.com")
            response, conversation = get_or_create_ai_onboarding_response(member)
            with patch(
                "questionnaires.onboarding_ai.llm.complete",
                return_value=_reply(text="done", tool_input=dict(EXTRACTION)),
            ):
                run_member_turn(conversation, "all answered")
            resp_qid = response.questionnaire_id
            resp_id = response.pk
            connection.close()

            # Member sees no persona name / signal on the dashboard.
            member_ctx = _auth_context(browser, "signal@test.com")
            member_page = member_ctx.new_page()
            member_page.goto(f"{django_server}/", wait_until="domcontentloaded")
            dash_main = member_page.locator("main").inner_text()
            for name in PERSONA_NAMES:
                assert name not in dash_main
            # The raw internal signal value is never rendered member-facing.
            assert "persona_signal" not in dash_main.lower()

            # Staff sees the inferred persona signal in Studio.
            staff_ctx = _auth_context(browser, "staff804@test.com")
            staff_page = staff_ctx.new_page()
            staff_page.goto(
                f"{django_server}/studio/questionnaires/{resp_qid}/responses/{resp_id}/",
                wait_until="domcontentloaded",
            )
            signal_panel = staff_page.locator(
                '[data-testid="response-detail-persona-signal"]'
            )
            signal_panel.wait_for(state="visible")
            assert "alex" in signal_panel.inner_text().lower()
            _shot(staff_page, "staff_persona_signal")


@pytest.mark.django_db(transaction=True)
class TestAccessControl:
    @pytest.mark.core
    def test_anonymous_redirected_to_login(self, django_server, browser):
        context = browser.new_context()
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat", wait_until="domcontentloaded",
            )
        assert "/accounts/login/" in page.url
        assert page.locator(
            '[data-testid="onboarding-chat-transcript"]'
        ).count() == 0

    @pytest.mark.core
    def test_member_cannot_open_other_members_conversation(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("ownerB@test.com", tier_slug="main", email_verified=True)
        _create_user("intruderB@test.com", tier_slug="main", email_verified=True)

        from accounts.models import User
        from questionnaires.models import Questionnaire, Response

        owner = User.objects.get(email="ownerB@test.com")
        generic = Questionnaire.objects.get(slug="onboarding-general")
        resp = Response.objects.create(
            questionnaire=generic, respondent=owner, status="draft",
        )
        resp_id = resp.pk
        connection.close()

        # The chat URL is keyed to the logged-in member (no id in the URL),
        # so member B's response id only resolves via the form-fill view,
        # which 404s for a non-owner.
        context = _auth_context(browser, "intruderB@test.com")
        page = context.new_page()
        with _llm_enabled():
            response = page.goto(
                f"{django_server}/onboarding/{resp_id}",
                wait_until="domcontentloaded",
            )
        assert response.status == 404
