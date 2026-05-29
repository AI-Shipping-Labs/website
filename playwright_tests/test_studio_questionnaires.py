"""Playwright E2E tests for the Studio questionnaire surfaces (issue #800).

Most behaviour is covered by Django ``TestCase`` modules in
``questionnaires/tests/`` -- per the testing guidelines, server-rendered
table-and-form surfaces belong there. These E2E scenarios exercise the
real browser flows the spec lists: authoring a questionnaire, adding the
six question types (with and without options), editing metadata,
deleting questions, reviewing collected responses (including blanks and
per-respondent custom questions), and the access gates.

Screenshots are written to ``.tmp/aisl-issue-800-screenshots`` for tester
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
from playwright_tests.conftest import (
    expand_studio_sidebar_section as _expand_studio_sidebar_section,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Local-only fixtures (DB seeding, session-cookie injection); cannot run
# against the deployed dev environment.
pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "aisl-issue-800-screenshots"


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _clear_questionnaire_data():
    from questionnaires.models import Questionnaire

    Questionnaire.objects.all().delete()
    connection.close()


def _create_questionnaire(title="Existing", purpose="general", slug=None):
    from questionnaires.models import Questionnaire

    q = Questionnaire.objects.create(
        title=title, purpose=purpose, slug=slug or "",
    )
    connection.close()
    return q


@pytest.mark.django_db(transaction=True)
class TestStaffAuthorsQuestionnaire:
    @pytest.mark.core
    def test_author_onboarding_questionnaire_from_scratch(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        _expand_studio_sidebar_section(page, "planning")
        page.locator(
            '#studio-sidebar-nav a[href="/studio/questionnaires/"]'
        ).click()
        page.wait_for_url(f"{django_server}/studio/questionnaires/")
        page.locator("text=No questionnaires yet").wait_for(state="visible")

        page.locator(
            '[data-testid="questionnaires-header"]'
        ).get_by_role("link", name="New questionnaire", exact=True).click()
        page.wait_for_url(f"{django_server}/studio/questionnaires/new")

        page.locator('[data-testid="questionnaire-title-input"]').fill("Onboarding Intake")
        page.locator('[data-testid="questionnaire-purpose-select"]').select_option("onboarding")
        page.locator('button[type="submit"]').click()

        page.locator(
            '[data-testid="questionnaire-detail-title"]:has-text("Onboarding Intake")'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="questionnaire-detail-purpose-badge"]:has-text("Onboarding")'
        ).wait_for(state="visible")
        # Success flash.
        page.locator("text=created").first.wait_for(state="visible")
        _shot(page, "questionnaire_detail_after_create")

        # Back to the list: row shows purpose Onboarding and 0 questions.
        page.goto(
            f"{django_server}/studio/questionnaires/",
            wait_until="domcontentloaded",
        )
        row = page.locator(
            '[data-testid="questionnaire-row"]',
            has=page.locator(
                '[data-testid="questionnaire-title"]:has-text("Onboarding Intake")'
            ),
        )
        row.wait_for(state="visible")
        assert row.locator(
            '[data-testid="questionnaire-purpose-badge"]'
        ).inner_text() == "Onboarding"
        assert row.locator(
            '[data-testid="questionnaire-question-count"]'
        ).inner_text() == "0"

    @pytest.mark.core
    def test_validation_blocks_blank_title(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/questionnaires/new",
            wait_until="domcontentloaded",
        )
        page.locator('button[type="submit"]').click()
        page.locator(
            '[data-testid="questionnaire-form-error"]:has-text("Title is required")'
        ).wait_for(state="visible")

        page.goto(
            f"{django_server}/studio/questionnaires/",
            wait_until="domcontentloaded",
        )
        page.locator("text=No questionnaires yet").wait_for(state="visible")

    def test_empty_state_has_cta(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/questionnaires/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="studio-empty-state-fresh"]').wait_for(state="visible")
        page.locator(
            '[data-testid="studio-empty-state-fresh"]'
        ).get_by_role("link", name="New questionnaire", exact=True).wait_for(
            state="visible",
        )


@pytest.mark.django_db(transaction=True)
class TestStaffAddsQuestions:
    def _open_detail(self, django_server, browser, questionnaire):
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/questionnaires/{questionnaire.pk}/",
            wait_until="domcontentloaded",
        )
        return page

    @pytest.mark.core
    def test_add_long_text_question(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")
        q = _create_questionnaire(title="Intake")

        page = self._open_detail(django_server, browser, q)
        page.locator('[data-testid="questionnaire-add-question-link"]').click()
        page.wait_for_url(
            f"{django_server}/studio/questionnaires/{q.pk}/questions/new",
        )
        page.locator('[data-testid="question-type-select"]').select_option("long_text")
        page.locator('[data-testid="question-prompt-input"]').fill(
            "What do you hope to achieve in the next 6 to 8 weeks?",
        )
        page.locator('[data-testid="question-is-required-input"]').check()
        page.locator('button[type="submit"]').click()

        page.wait_for_url(f"{django_server}/studio/questionnaires/{q.pk}/")
        page.locator(
            'text=What do you hope to achieve in the next 6 to 8 weeks?'
        ).wait_for(state="visible")
        assert page.locator(
            '[data-testid="questionnaire-detail-question-count"]'
        ).inner_text() == "1"

    def test_add_multiple_choice_then_remove_an_option(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")
        q = _create_questionnaire(title="Intake")

        page = self._open_detail(django_server, browser, q)
        page.locator('[data-testid="questionnaire-add-question-link"]').click()
        page.locator('[data-testid="question-type-select"]').select_option("multiple_choice")
        page.locator('[data-testid="question-prompt-input"]').fill(
            "Which areas do you want to focus on?",
        )
        page.locator('[data-testid="question-options-input"]').fill(
            "RAG\nAgents\nDeployment\nEvaluation",
        )
        page.locator('button[type="submit"]').click()
        page.wait_for_url(f"{django_server}/studio/questionnaires/{q.pk}/")

        options = page.locator('[data-testid="questionnaire-question-option"]')
        options.first.wait_for(state="visible")
        assert options.count() == 4

        # Edit: remove Deployment.
        page.get_by_role("link", name="Edit", exact=True).first.click()
        page.locator('[data-testid="question-options-input"]').fill(
            "RAG\nAgents\nEvaluation",
        )
        page.locator('button[type="submit"]').click()
        page.wait_for_url(f"{django_server}/studio/questionnaires/{q.pk}/")

        options = page.locator('[data-testid="questionnaire-question-option"]')
        options.first.wait_for(state="visible")
        assert options.count() == 3
        assert "Deployment" not in page.locator(
            '[data-testid="questionnaire-question-options"]'
        ).inner_text()

    def test_number_question_saves_without_options(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")
        q = _create_questionnaire(title="Intake")

        page = self._open_detail(django_server, browser, q)
        page.locator('[data-testid="questionnaire-add-question-link"]').click()
        page.locator('[data-testid="question-type-select"]').select_option("number")
        page.locator('[data-testid="question-prompt-input"]').fill(
            "How many hours per week can you commit?",
        )
        page.locator('button[type="submit"]').click()

        page.wait_for_url(f"{django_server}/studio/questionnaires/{q.pk}/")
        type_cell = page.locator('[data-testid="questionnaire-question-type"]')
        type_cell.first.wait_for(state="visible")
        assert type_cell.first.inner_text() == "Number"

    def test_delete_a_base_question(self, django_server, browser):
        from questionnaires.models import Question

        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")
        q = _create_questionnaire(title="Intake")
        Question.objects.create(questionnaire=q, question_type="text", prompt="First Q", order=0)
        Question.objects.create(questionnaire=q, question_type="text", prompt="Second Q", order=1)
        connection.close()

        page = self._open_detail(django_server, browser, q)
        rows = page.locator('[data-testid="questionnaire-question-row"]')
        rows.first.wait_for(state="visible")
        assert rows.count() == 2

        page.locator(
            '[data-testid="questionnaire-question-delete-button"]'
        ).first.click()
        page.wait_for_url(f"{django_server}/studio/questionnaires/{q.pk}/")
        rows = page.locator('[data-testid="questionnaire-question-row"]')
        rows.first.wait_for(state="visible")
        assert rows.count() == 1
        assert page.locator(
            '[data-testid="questionnaire-detail-question-count"]'
        ).inner_text() == "1"


@pytest.mark.django_db(transaction=True)
class TestStaffEditsMetadata:
    def test_edit_title_and_purpose(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")
        q = _create_questionnaire(title="Draft Feedback", purpose="general")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/questionnaires/{q.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="questionnaire-title-input"]').fill("May Sprint Feedback")
        page.locator('[data-testid="questionnaire-purpose-select"]').select_option("feedback")
        page.locator('button[type="submit"]').click()

        page.locator(
            '[data-testid="questionnaire-detail-title"]:has-text("May Sprint Feedback")'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="questionnaire-detail-purpose-badge"]:has-text("Feedback")'
        ).wait_for(state="visible")

        page.goto(
            f"{django_server}/studio/questionnaires/",
            wait_until="domcontentloaded",
        )
        page.locator('text=May Sprint Feedback').wait_for(state="visible")


@pytest.mark.django_db(transaction=True)
class TestStaffReviewsResponses:
    def _seed_response(self):
        from accounts.models import User
        from questionnaires.models import (
            Answer,
            Question,
            Questionnaire,
            Response,
            ResponseQuestion,
        )

        q = Questionnaire.objects.create(title="Sprint Feedback", purpose="feedback")
        # Two base questions back the standard response questions; the
        # third response question is a one-off (no ``source_question``)
        # so only it is flagged custom.
        base_well = Question.objects.create(
            questionnaire=q, question_type="long_text",
            prompt="What went well?", order=0,
        )
        base_else = Question.objects.create(
            questionnaire=q, question_type="text",
            prompt="Anything else?", order=1,
        )
        member = User.objects.get(email="member@test.com")
        response = Response.objects.create(questionnaire=q, respondent=member)
        response.mark_submitted()

        answered = ResponseQuestion.objects.create(
            response=response, source_question=base_well,
            question_type="long_text", prompt="What went well?", order=0,
        )
        ResponseQuestion.objects.create(
            response=response, source_question=base_else,
            question_type="text", prompt="Anything else?", order=1,
        )
        ResponseQuestion.objects.create(
            response=response, source_question=None, question_type="text",
            prompt="Custom for this member", order=2,
        )
        Answer.objects.create(
            response=response, question=answered, text_value="The pairing sessions",
        )
        connection.close()
        return q, response

    @pytest.mark.core
    def test_review_collected_response_in_full(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")
        _create_user("member@test.com", tier_slug="free", email_verified=True)
        q, response = self._seed_response()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/questionnaires/{q.pk}/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="questionnaire-responses-link"]').click()
        page.wait_for_url(f"{django_server}/studio/questionnaires/{q.pk}/responses/")

        row = page.locator('[data-testid="questionnaire-response-row"]')
        row.wait_for(state="visible")
        assert "member@test.com" in row.inner_text()
        assert "Submitted" in row.inner_text()

        page.get_by_role("link", name="View", exact=True).first.click()
        page.wait_for_url(
            f"{django_server}/studio/questionnaires/{q.pk}/responses/{response.pk}/",
        )
        page.locator('text=What went well?').wait_for(state="visible")
        page.locator('text=The pairing sessions').wait_for(state="visible")
        # The unanswered question is shown with an explicit blank marker.
        page.locator('text=Anything else?').wait_for(state="visible")
        page.locator('[data-testid="response-detail-blank"]').first.wait_for(state="visible")
        _shot(page, "response_detail")

    @pytest.mark.core
    def test_custom_question_is_flagged(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_staff_user("admin@test.com")
        _create_user("member@test.com", tier_slug="free", email_verified=True)
        q, response = self._seed_response()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/questionnaires/{q.pk}/responses/{response.pk}/",
            wait_until="domcontentloaded",
        )
        page.locator('text=Custom for this member').wait_for(state="visible")
        flags = page.locator('[data-testid="response-detail-custom-flag"]')
        flags.first.wait_for(state="visible")
        # Only the one-off question (no base source) is flagged; the two
        # standard questions are not.
        assert flags.count() == 1


@pytest.mark.django_db(transaction=True)
class TestAccessGates:
    @pytest.mark.core
    def test_non_staff_member_gets_403(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_user("main@test.com", tier_slug="main", email_verified=True)
        _create_questionnaire(title="Secret Intake")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        response = page.goto(
            f"{django_server}/studio/questionnaires/",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 403
        assert "Secret Intake" not in page.content()

    @pytest.mark.core
    def test_anonymous_redirected_to_login(self, django_server, browser):
        _ensure_tiers()
        _clear_questionnaire_data()
        _create_questionnaire(title="Secret Intake")

        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/questionnaires/",
            wait_until="domcontentloaded",
        )
        assert "/accounts/login/" in page.url
        assert "Secret Intake" not in page.content()
        context.close()
