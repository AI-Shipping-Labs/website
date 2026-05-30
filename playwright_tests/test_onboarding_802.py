"""Playwright E2E tests for the form-first member onboarding flow (#802).

Covers the user-visible scenarios from the issue: self-identifying by
archetype (with the persona-name-never-leaked guarantee), the generic
route for "not sure" / "both", completing onboarding and losing the
dashboard prompt, required-question validation, resuming a partial draft,
the post-submit confirmation, cross-member 404, anonymous redirect, the
Studio per-member customization + persona assignment, and the graceful
no-questionnaire degrade.

The four personas + their onboarding questionnaires (and the generic
``onboarding-general`` fallback) are seeded by migration
``questionnaires.0003`` so they exist in the test DB.

Screenshots are written to ``.tmp/aisl-issue-802-screenshots`` for tester
review.
"""

import os
from pathlib import Path

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

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "aisl-issue-802-screenshots"

PERSONA_NAMES = ["Alex", "Priya", "Sam", "Taylor"]


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _ensure_onboarding_seed():
    """Idempotently re-create the onboarding seed (#801 migration data).

    Other Playwright suites (e.g. sprint feedback) delete all
    questionnaires within the shared transactional test DB, which would
    orphan the migration-seeded personas. Re-running the seed keeps this
    suite self-contained regardless of test ordering.
    """
    import importlib

    from django.apps import apps as django_apps

    seed_module = importlib.import_module(
        'questionnaires.migrations.0003_seed_personas_and_onboarding',
    )
    seed_module.seed(django_apps, None)
    connection.close()


def _force_form_first_onboarding():
    """Keep this #802 suite on the form-first fallback path.

    Local operator env can enable the newer AI chat path globally. These
    tests intentionally verify the legacy form flow, so pin the Studio
    config flag off in the shared test DB before browser navigation.
    """
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key="ONBOARDING_AI_ENABLED",
        defaults={
            "value": "false",
            "is_secret": False,
            "group": "llm",
            "description": "",
        },
    )
    clear_config_cache()
    connection.close()


def _reset_responses():
    from questionnaires.models import Response

    _ensure_onboarding_seed()
    _force_form_first_onboarding()
    Response.objects.all().delete()
    connection.close()


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


def _archetype_for_generic():
    """A persona archetype label that should appear on the self-ID page."""
    from questionnaires.models import Persona

    archetype = (
        Persona.objects.filter(
            is_active=True, default_questionnaire__isnull=False,
        )
        .order_by("order", "name")
        .first()
        .archetype
    )
    connection.close()
    return archetype


@pytest.mark.django_db(transaction=True)
class TestSelfIdentification:
    @pytest.mark.core
    def test_dashboard_prompt_then_self_id_by_archetype(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_responses()
        _create_user("new@test.com", tier_slug="main", email_verified=True)
        archetype = _archetype_for_generic()

        context = _auth_context(browser, "new@test.com")
        page = context.new_page()

        # Dashboard prompt invites onboarding.
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        prompt = page.locator('[data-testid="onboarding-prompt"]')
        prompt.wait_for(state="visible")
        _shot(page, "dashboard_prompt")

        # Follow the CTA to /onboarding/.
        page.locator('[data-testid="onboarding-prompt-cta"]').click()
        page.locator('[data-testid="onboarding-title"]').wait_for(state="visible")
        assert "How do you identify yourself?" in page.locator(
            '[data-testid="onboarding-title"]'
        ).inner_text()

        # Archetype descriptions are listed.
        options = page.locator('[data-testid="onboarding-option-label"]')
        labels = [options.nth(i).inner_text() for i in range(options.count())]
        assert any(archetype in label for label in labels)

        # No internal persona name leaks into the form.
        form_html = page.locator(
            '[data-testid="onboarding-identify-form"]'
        ).inner_html()
        for name in PERSONA_NAMES:
            assert name not in form_html, f"persona name {name} leaked"
        _shot(page, "self_id")

        # Pick the first archetype option and continue.
        page.locator('[data-testid="onboarding-option"] input[type="radio"]').first.check()
        page.locator('[data-testid="onboarding-continue-button"]').click()

        # Land on the fill-in form.
        page.locator('[data-testid="onboarding-fill-title"]').wait_for(
            state="visible",
        )
        assert page.locator(
            '[data-testid="questionnaire-response-form"]'
        ).is_visible()
        _shot(page, "fill_in")

    @pytest.mark.core
    def test_not_sure_and_both_route_to_generic(self, django_server, browser):
        _ensure_tiers()
        _reset_responses()
        _create_user("unsure@test.com", tier_slug="main", email_verified=True)
        _create_user("both@test.com", tier_slug="main", email_verified=True)

        # "None of these / not sure".
        ctx_a = _auth_context(browser, "unsure@test.com")
        page_a = ctx_a.new_page()
        page_a.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        page_a.locator(
            '[data-testid="onboarding-option"] input[value="none"]'
        ).check()
        page_a.locator('[data-testid="onboarding-continue-button"]').click()
        page_a.locator('[data-testid="onboarding-fill-title"]').wait_for(
            state="visible",
        )
        resp_a = _onboarding_response("unsure@test.com")
        assert resp_a.questionnaire.slug == "onboarding-general"

        # "More than one / both".
        ctx_b = _auth_context(browser, "both@test.com")
        page_b = ctx_b.new_page()
        page_b.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        page_b.locator(
            '[data-testid="onboarding-option"] input[value="multiple"]'
        ).check()
        page_b.locator('[data-testid="onboarding-continue-button"]').click()
        page_b.locator('[data-testid="onboarding-fill-title"]').wait_for(
            state="visible",
        )
        resp_b = _onboarding_response("both@test.com")
        assert resp_b.questionnaire.slug == "onboarding-general"


@pytest.mark.django_db(transaction=True)
class TestCompleteAndResume:
    @pytest.mark.core
    def test_complete_then_prompt_disappears(self, django_server, browser):
        _ensure_tiers()
        _reset_responses()
        _create_user("finish@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "finish@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        page.locator(
            '[data-testid="onboarding-option"] input[value="none"]'
        ).check()
        page.locator('[data-testid="onboarding-continue-button"]').click()
        page.locator('[data-testid="questionnaire-response-form"]').wait_for(
            state="visible",
        )

        # The seeded generic questions are optional; submit immediately.
        page.locator('[data-testid="questionnaire-submit-button"]').click()
        # Redirected to the dashboard with a thank-you referencing the plan.
        page.wait_for_load_state("domcontentloaded")
        assert "plan" in page.content().lower()
        _shot(page, "after_submit")

        # The dashboard prompt is gone.
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.locator('[data-testid="onboarding-prompt"]').count() == 0

    @pytest.mark.core
    def test_required_blank_blocks_submit(self, django_server, browser):
        _ensure_tiers()
        _reset_responses()
        _create_user("req@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "req@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        page.locator(
            '[data-testid="onboarding-option"] input[value="none"]'
        ).check()
        page.locator('[data-testid="onboarding-continue-button"]').click()
        page.locator('[data-testid="questionnaire-response-form"]').wait_for(
            state="visible",
        )

        # Add a required question to this member's response so submit is gated.
        resp = _onboarding_response("req@test.com")
        from questionnaires.models import ResponseQuestion

        ResponseQuestion.objects.create(
            response=resp, source_question=None, question_type="text",
            prompt="What is your must-answer goal?", is_required=True,
            order=999,
        )
        connection.close()

        page.reload(wait_until="domcontentloaded")
        page.locator('[data-testid="questionnaire-submit-button"]').click()
        # Re-renders with the missing required question named.
        page.locator('[data-testid="onboarding-error"]').wait_for(state="visible")
        assert "must-answer goal" in page.locator(
            '[data-testid="onboarding-error"]'
        ).inner_text()
        _shot(page, "required_error")

        # Still draft: the dashboard prompt persists.
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.locator('[data-testid="onboarding-prompt"]').count() == 1

    @pytest.mark.core
    def test_resume_partial_draft(self, django_server, browser):
        _ensure_tiers()
        _reset_responses()
        _create_user("resume@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "resume@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        page.locator(
            '[data-testid="onboarding-option"] input[value="none"]'
        ).check()
        page.locator('[data-testid="onboarding-continue-button"]').click()
        page.locator('[data-testid="questionnaire-response-form"]').wait_for(
            state="visible",
        )

        # Type into the first long-text input and save the draft.
        first_text = page.locator(
            '[data-testid="questionnaire-input-long-text"]'
        ).first
        first_text.fill("My half-finished answer")
        page.locator('[data-testid="questionnaire-save-button"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Reopen /onboarding/: taken straight to fill-in, answer pre-filled.
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        page.locator('[data-testid="questionnaire-response-form"]').wait_for(
            state="visible",
        )
        # Self-ID is not re-asked.
        assert page.locator('[data-testid="onboarding-identify-form"]').count() == 0
        assert "My half-finished answer" in page.locator(
            '[data-testid="questionnaire-input-long-text"]'
        ).first.input_value()
        _shot(page, "resume")

        # Exactly one response exists.
        from accounts.models import User
        from questionnaires.models import Response

        user = User.objects.get(email="resume@test.com")
        assert Response.objects.filter(respondent=user).count() == 1
        connection.close()

    @pytest.mark.core
    def test_completed_shows_confirmation_not_restart(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_responses()
        _create_user("alreadydone@test.com", tier_slug="main", email_verified=True)
        from accounts.models import User
        from questionnaires.models import Questionnaire, Response

        user = User.objects.get(email="alreadydone@test.com")
        generic = Questionnaire.objects.get(slug="onboarding-general")
        Response.objects.create(
            questionnaire=generic, respondent=user, status="submitted",
        )
        connection.close()

        context = _auth_context(browser, "alreadydone@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        page.locator('[data-testid="onboarding-complete-title"]').wait_for(
            state="visible",
        )
        assert page.locator('[data-testid="onboarding-identify-form"]').count() == 0
        _shot(page, "completed_confirmation")


@pytest.mark.django_db(transaction=True)
class TestAccessControl:
    @pytest.mark.core
    def test_anonymous_redirected_to_login(self, django_server, browser):
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        assert "/accounts/login/" in page.url
        assert page.locator('[data-testid="onboarding-title"]').count() == 0

    @pytest.mark.core
    def test_member_cannot_open_other_members_response(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_responses()
        _create_user("owner@test.com", tier_slug="main", email_verified=True)
        _create_user("intruder@test.com", tier_slug="main", email_verified=True)
        from accounts.models import User
        from questionnaires.models import Questionnaire, Response

        owner = User.objects.get(email="owner@test.com")
        generic = Questionnaire.objects.get(slug="onboarding-general")
        resp = Response.objects.create(
            questionnaire=generic, respondent=owner, status="draft",
        )
        resp_id = resp.pk
        connection.close()

        context = _auth_context(browser, "intruder@test.com")
        page = context.new_page()
        response = page.goto(
            f"{django_server}/onboarding/{resp_id}",
            wait_until="domcontentloaded",
        )
        assert response.status == 404


@pytest.mark.django_db(transaction=True)
class TestStaffCustomization:
    @pytest.mark.core
    def test_customize_one_members_questions(self, django_server, browser):
        _ensure_tiers()
        _reset_responses()
        _create_staff_user("admin@test.com")
        _create_user("cust@test.com", tier_slug="main", email_verified=True)
        _create_user("untouched@test.com", tier_slug="main", email_verified=True)

        from accounts.models import User
        from questionnaires.models import Questionnaire, Response
        from questionnaires.services import build_response_questions

        generic = Questionnaire.objects.get(slug="onboarding-general")
        cust = User.objects.get(email="cust@test.com")
        other = User.objects.get(email="untouched@test.com")
        cust_resp = Response.objects.create(
            questionnaire=generic, respondent=cust,
        )
        build_response_questions(cust_resp)
        other_resp = Response.objects.create(
            questionnaire=generic, respondent=other,
        )
        build_response_questions(other_resp)
        q_id = generic.pk
        cust_resp_id = cust_resp.pk
        other_resp_id = other_resp.pk
        # The prompt we will edit + the base count, for later assertions.
        edit_rq = cust_resp.response_questions.filter(
            source_question__isnull=False,
        ).order_by("order").first()
        edit_rq_id = edit_rq.pk
        original_base_prompt = edit_rq.source_question.prompt
        # A base question we will remove from cust's response only.
        remove_rq = cust_resp.response_questions.filter(
            source_question__isnull=False,
        ).order_by("-order").first()
        remove_rq_id = remove_rq.pk
        removed_base_prompt = remove_rq.source_question.prompt
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # Add a one-off custom question.
        page.goto(
            f"{django_server}/studio/questionnaires/{q_id}/responses/{cust_resp_id}/questions/new",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="response-question-prompt-input"]').fill(
            "Custom-only question for this member",
        )
        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "Custom-only question for this member" in page.content()
        _shot(page, "staff_added_question")

        # Edit an existing question's prompt.
        page.goto(
            f"{django_server}/studio/questionnaires/{q_id}/responses/{cust_resp_id}/questions/{edit_rq_id}/edit",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="response-question-prompt-input"]').fill(
            "Edited prompt for this member only",
        )
        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "Edited prompt for this member only" in page.content()

        # Remove a question (accept the confirm dialog).
        page.on("dialog", lambda d: d.accept())
        page.goto(
            f"{django_server}/studio/questionnaires/{q_id}/responses/{cust_resp_id}/",
            wait_until="domcontentloaded",
        )
        # Find the remove form for the target question and submit it directly.
        page.evaluate(
            """(rqId) => {
                const forms = document.querySelectorAll('form[action*="/questions/"]');
                for (const f of forms) {
                    if (f.action.includes('/questions/' + rqId + '/delete')) {
                        f.submit();
                        return;
                    }
                }
            }""",
            remove_rq_id,
        )
        page.wait_for_load_state("domcontentloaded")
        _shot(page, "staff_customized_detail")

        # Base questionnaire is unchanged.
        page.goto(
            f"{django_server}/studio/questionnaires/{q_id}/",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Custom-only question for this member" not in body
        assert "Edited prompt for this member only" not in body
        assert original_base_prompt in body
        assert removed_base_prompt in body

        # The other member's response is unaffected.
        page.goto(
            f"{django_server}/studio/questionnaires/{q_id}/responses/{other_resp_id}/",
            wait_until="domcontentloaded",
        )
        other_body = page.content()
        assert "Custom-only question for this member" not in other_body
        assert "Edited prompt for this member only" not in other_body
        assert removed_base_prompt in other_body

    @pytest.mark.core
    def test_assign_persona_not_leaked_to_member(self, django_server, browser):
        _ensure_tiers()
        _reset_responses()
        _create_staff_user("admin2@test.com")
        _create_user("personamember@test.com", tier_slug="main", email_verified=True)

        from accounts.models import User
        from crm.models import CRMRecord
        from questionnaires.models import Persona

        member = User.objects.get(email="personamember@test.com")
        record = CRMRecord.objects.create(user=member)
        persona = Persona.objects.filter(is_active=True).first()
        crm_id = record.pk
        persona_label = persona.display_label
        persona_name = persona.name
        persona_archetype = persona.archetype
        persona_pk = persona.pk
        connection.close()

        # Staff assigns the structured persona.
        staff_ctx = _auth_context(browser, "admin2@test.com")
        staff_page = staff_ctx.new_page()
        staff_page.goto(
            f"{django_server}/studio/crm/{crm_id}/",
            wait_until="domcontentloaded",
        )
        staff_page.locator(
            '[data-testid="crm-persona-ref-select"]'
        ).select_option(str(persona_pk))
        staff_page.locator('[data-testid="crm-snapshot-save"]').click()
        staff_page.wait_for_load_state("domcontentloaded")
        # Staff sees the persona name + archetype together.
        assert persona_label in staff_page.content()
        _shot(staff_page, "staff_persona_assigned")

        # The member never sees the persona on member-facing pages. Scope
        # to the visible <main> content -- the base template's author meta
        # tag ("Alexey Grigorev") is site chrome, not a persona-name leak.
        member_ctx = _auth_context(browser, "personamember@test.com")
        member_page = member_ctx.new_page()
        member_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        dash_main = member_page.locator("main").inner_text()
        assert persona_name not in dash_main
        assert persona_archetype not in dash_main


@pytest.mark.django_db(transaction=True)
class TestNotReady:
    @pytest.mark.core
    def test_friendly_message_when_no_questionnaire(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_responses()
        _create_user("early@test.com", tier_slug="main", email_verified=True)

        # Simulate an unseeded environment: detach + remove onboarding qsts.
        from questionnaires.models import Persona, Questionnaire

        Persona.objects.update(default_questionnaire=None)
        Questionnaire.objects.filter(purpose="onboarding").delete()
        connection.close()

        context = _auth_context(browser, "early@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        page.locator('[data-testid="onboarding-not-ready"]').wait_for(
            state="visible",
        )
        _shot(page, "not_ready")
