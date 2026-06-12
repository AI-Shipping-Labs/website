"""Playwright E2E for the streaming AI onboarding chat (issue #806).

Covers the user-visible streaming scenarios from the issue: the assistant
reply appearing token-by-token, a streamed completion landing the SAME
#800 artifacts as the non-streaming path, graceful fallback when the
stream drops mid-reply or fails entirely, the streaming-off path, the
prefer-the-form switch, cross-member isolation, the anonymous redirect,
and the already-onboarded confirmation.

The Django dev server runs in the SAME process as the test, so the LLM
boundary is mocked in-process with
``patch('questionnaires.onboarding_ai.llm.stream', ...)`` (and
``llm.complete`` for the authoritative turn) -- CI never opens a live
stream.

Screenshots are written to ``.tmp/aisl-issue-806-screenshots``.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from playwright_tests.conftest import (
    SETTLE_TIMEOUT_MS,
)
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

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "aisl-issue-806-screenshots"

PERSONA_NAMES = ["Alex", "Priya", "Sam", "Taylor"]

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
def _llm_enabled(enabled=True, streaming=True):
    """Enable/disable the LLM + streaming flag via DB config."""
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    keys = ["LLM_API_KEY", "LLM_PROVIDER", "ONBOARDING_AI_STREAMING"]
    if enabled:
        IntegrationSetting.objects.update_or_create(
            key="LLM_API_KEY", defaults={"value": "sk-test-fake"},
        )
        IntegrationSetting.objects.update_or_create(
            key="LLM_PROVIDER", defaults={"value": "anthropic"},
        )
        IntegrationSetting.objects.update_or_create(
            key="ONBOARDING_AI_STREAMING",
            defaults={"value": "true" if streaming else "false"},
        )
    else:
        IntegrationSetting.objects.filter(key__in=keys).delete()
    clear_config_cache()
    connection.close()
    try:
        yield
    finally:
        IntegrationSetting.objects.filter(key__in=keys).delete()
        clear_config_cache()
        connection.close()


def _stream(deltas, final_text=None, *, tool_input=None, tool_name=None):
    """Side-effect for llm.stream: yield text deltas then a done event.

    The terminal ``done`` event carries the assembled ``LLMResult`` (text
    plus, on the completing turn, ``tool_input``/``tool_name``) — mirroring
    the real backend, which streams text deltas then assembles a final
    message that includes any tool call (#821). The streaming onboarding
    path builds the authoritative result from THIS single generation, so
    no second ``llm.complete()`` round-trip happens on completion.
    """
    from integrations.services.llm import (
        STREAM_DONE,
        STREAM_TEXT_DELTA,
        LLMResult,
        StreamEvent,
    )

    text = final_text if final_text is not None else "".join(deltas)

    def gen(messages, **kwargs):
        for d in deltas:
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text=d)
        yield StreamEvent(
            kind=STREAM_DONE,
            result=LLMResult(
                text=text, tool_input=tool_input, tool_name=tool_name,
            ),
        )

    return gen


def _stream_raises_after_first(deltas):
    """Side-effect for llm.stream: yield first delta then raise LLMError."""
    from integrations.services.llm import (
        STREAM_TEXT_DELTA,
        LLMError,
        StreamEvent,
    )

    def gen(messages, **kwargs):
        yield StreamEvent(kind=STREAM_TEXT_DELTA, text=deltas[0])
        raise LLMError("mid-stream drop")

    return gen


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


@pytest.mark.django_db(transaction=True)
class TestTokenByToken:
    @pytest.mark.core
    def test_reply_renders_incrementally(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("streamer@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "streamer@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
                state="visible",
            )
            reply = "Thanks! What blocks your consistency the most?"
            with patch(
                "questionnaires.onboarding_ai.llm.stream",
                side_effect=_stream(
                    ["Thanks! ", "What blocks ", "your consistency the most?"],
                    reply,
                ),
            ), patch(
                "questionnaires.onboarding_ai.llm.complete",
                return_value=_reply(text=reply),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "I want to ship a RAG app",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                # The assistant bubble fills in via streamed deltas.
                page.locator(
                    '[data-testid="onboarding-chat-assistant"]'
                ).last.wait_for(state="visible")
                page.wait_for_function(
                    "() => document.querySelector("
                    "'[data-testid=\\'onboarding-chat-transcript\\']')"
                    ".innerText.includes('your consistency the most?')",
                    timeout=5000,
                )
            transcript = page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).inner_text()
            assert reply in transcript
            for name in PERSONA_NAMES:
                assert name not in transcript, f"persona name {name} leaked"
            _shot(page, "stream_incremental")


@pytest.mark.django_db(transaction=True)
class TestStreamedCompletion:
    @pytest.mark.core
    def test_completion_lands_same_artifacts(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("streamdone@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "streamdone@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
                state="visible",
            )
            # #821: the completing turn rides the single streamed ``done``
            # event -- the tool call is assembled from THAT generation, so
            # there is no redundant second ``llm.complete()`` round-trip.
            with patch(
                "questionnaires.onboarding_ai.llm.stream",
                side_effect=_stream(
                    ["All set! "], "All set!",
                    tool_input=dict(EXTRACTION), tool_name="record_onboarding",
                ),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "Here is everything",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                # On completion the client redirects to the thank-you home.
                page.wait_for_url(f"{django_server}/", timeout=5000)
            # The onboarding prompt is gone -- onboarding is complete.
            assert page.locator(
                '[data-testid="onboarding-prompt"]'
            ).count() == 0
            _shot(page, "stream_complete")

        resp = _onboarding_response("streamdone@test.com")
        assert resp.status == "submitted"
        from accounts.models import User
        from questionnaires.models import Answer, Response

        user = User.objects.get(email="streamdone@test.com")
        assert Response.objects.filter(
            respondent=user, questionnaire__purpose="onboarding",
        ).count() == 1
        assert Answer.objects.filter(response=resp).exists()
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestMidStreamDrop:
    @pytest.mark.core
    def test_stream_drops_then_v1_delivers_full_reply(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("dropper@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "dropper@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
                state="visible",
            )
            full = "Got it -- tell me about your weekly time."
            # Stream raises after the first delta; the client then re-issues
            # the SAME message via the v1 non-streaming POST (full reload).
            with patch(
                "questionnaires.onboarding_ai.llm.stream",
                side_effect=_stream_raises_after_first(["Got it -- "]),
            ), patch(
                "questionnaires.onboarding_ai.llm.complete",
                return_value=_reply(text=full),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "my next message",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                # The v1 fallback re-renders the chat with the full reply.
                page.wait_for_function(
                    "() => document.querySelector("
                    "'[data-testid=\\'onboarding-chat-transcript\\']')"
                    ".innerText.includes('about your weekly time')",
                    # The mocked stream-drop -> client re-issue -> full v1
                    # server round-trip -> re-render chain needs more headroom
                    # than a single UI-settle on a contended shard (#903). Use
                    # twice the shared settle budget (16s) so a loaded shard
                    # still completes the fallback before the wait expires.
                    timeout=2 * SETTLE_TIMEOUT_MS,
                )
            transcript = page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).inner_text()
            assert full in transcript
            # Exactly one turn for the member message (no duplicate).
            assert transcript.count("my next message") == 1
            _shot(page, "mid_stream_drop_fallback")


@pytest.mark.django_db(transaction=True)
class TestStreamFailureToForm:
    @pytest.mark.core
    def test_hard_failure_falls_back_to_form(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("streamfail@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "streamfail@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
                state="visible",
            )
            from integrations.services.llm import LLMError

            # Stream open fails AND the v1 retry's complete() fails ->
            # the v1 path routes to the #802 form fallback.
            with patch(
                "questionnaires.onboarding_ai.llm.stream",
                side_effect=LLMError("down"),
            ), patch(
                "questionnaires.onboarding_ai.llm.complete",
                side_effect=LLMError("down"),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "my next message",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                page.locator(
                    '[data-testid="questionnaire-response-form"]'
                ).wait_for(state="visible")
            _shot(page, "stream_failure_form")
            page.locator('[data-testid="questionnaire-submit-button"]').click()
            page.wait_for_load_state("domcontentloaded")

        resp = _onboarding_response("streamfail@test.com")
        assert resp.status == "submitted"


@pytest.mark.django_db(transaction=True)
class TestStreamingOff:
    @pytest.mark.core
    def test_chat_works_without_sse_when_streaming_off(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("nostream@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "nostream@test.com")
        page = context.new_page()
        with _llm_enabled(streaming=False):
            page.goto(
                f"{django_server}/onboarding/chat",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
                state="visible",
            )
            # No streaming attribute -> the form posts normally (v1 path).
            assert page.locator(
                '[data-testid="onboarding-chat-form"][data-streaming="1"]'
            ).count() == 0
            with patch(
                "questionnaires.onboarding_ai.llm.complete",
                return_value=_reply(text="What is your main blocker?"),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "I want to ship something",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                page.wait_for_load_state("domcontentloaded")
            transcript = page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).inner_text()
            assert "What is your main blocker?" in transcript
            _shot(page, "streaming_off")


@pytest.mark.django_db(transaction=True)
class TestPreferForm:
    @pytest.mark.core
    def test_switch_to_form_from_streaming_chat(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("prefform@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "prefform@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-switch-to-form"]').click()
            page.locator(
                '[data-testid="questionnaire-response-form"]'
            ).wait_for(state="visible")
            _shot(page, "stream_switch_to_form")
            page.locator('[data-testid="questionnaire-submit-button"]').click()
            page.wait_for_load_state("domcontentloaded")

        resp = _onboarding_response("prefform@test.com")
        assert resp.status == "submitted"


@pytest.mark.django_db(transaction=True)
class TestStreamAccessControl:
    @pytest.mark.core
    def test_anonymous_redirected_to_login(self, django_server, browser):
        # An anonymous visitor hitting the chat surface is redirected to
        # login -- so no stream is ever opened for an unauthenticated user.
        context = browser.new_context()
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat",
                wait_until="domcontentloaded",
            )
        assert "/accounts/login/" in page.url
        assert page.locator(
            '[data-testid="onboarding-chat-transcript"]'
        ).count() == 0

    @pytest.mark.core
    def test_member_cannot_stream_other_members_conversation(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("ownerS@test.com", tier_slug="main", email_verified=True)
        _create_user("intruderS@test.com", tier_slug="main", email_verified=True)

        from accounts.models import User
        from questionnaires.services_onboarding_ai import (
            get_or_create_ai_onboarding_response,
        )

        owner = User.objects.get(email="ownerS@test.com")
        get_or_create_ai_onboarding_response(owner)
        connection.close()

        # The streaming endpoint takes no conversation id: it always
        # resolves the logged-in member's own response, so the intruder
        # streaming a turn can never touch the owner's conversation. We
        # drive the intruder's turn through the real UI (CSRF included).
        context = _auth_context(browser, "intruderS@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/chat",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-chat-transcript"]').wait_for(
                state="visible",
            )
            reply = "Hi intruder, tell me your goal."
            with patch(
                "questionnaires.onboarding_ai.llm.stream",
                side_effect=_stream([reply], reply),
            ), patch(
                "questionnaires.onboarding_ai.llm.complete",
                return_value=_reply(text=reply),
            ):
                page.locator('[data-testid="onboarding-chat-input"]').fill(
                    "hello from intruder",
                )
                page.locator('[data-testid="onboarding-chat-send"]').click()
                page.wait_for_function(
                    "() => document.querySelector("
                    "'[data-testid=\\'onboarding-chat-transcript\\']')"
                    ".innerText.includes('tell me your goal')",
                    timeout=5000,
                )

        from questionnaires.models import OnboardingConversation

        owner_conv = OnboardingConversation.objects.get(
            response__respondent=owner,
        )
        # The owner's transcript is untouched by the intruder's turn.
        assert all(
            t.get("content") != "hello from intruder"
            for t in owner_conv.transcript
        )
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestAlreadyOnboarded:
    @pytest.mark.core
    def test_already_onboarded_not_restarted(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("streamoboarded@test.com", tier_slug="main",
                     email_verified=True)
        from accounts.models import User
        from questionnaires.models import Questionnaire, Response

        user = User.objects.get(email="streamoboarded@test.com")
        generic = Questionnaire.objects.get(slug="onboarding-general")
        Response.objects.create(
            questionnaire=generic, respondent=user, status="submitted",
        )
        connection.close()

        context = _auth_context(browser, "streamoboarded@test.com")
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f"{django_server}/onboarding/", wait_until="domcontentloaded",
            )
            page.locator('[data-testid="onboarding-complete-title"]').wait_for(
                state="visible",
            )
            assert page.locator(
                '[data-testid="onboarding-chat-transcript"]'
            ).count() == 0
            _shot(page, "stream_already_onboarded")
