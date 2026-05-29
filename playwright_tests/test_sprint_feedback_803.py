"""Playwright E2E tests for sprint feedback (issue #803).

Covers the spec scenarios that need a real browser: staff attaching +
distributing a feedback questionnaire, the per-member completion table, a
member filling in and submitting their feedback, required-question
validation, partial-save resume, staff reading a submitted response, and
the access gates (cross-member 404, anonymous redirect, non-staff 403).

Most validation logic is also covered by Django ``TestCase`` modules;
these scenarios exercise the user-visible flows end to end.

Screenshots are written to ``.tmp/aisl-issue-803-screenshots`` for tester
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

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "aisl-issue-803-screenshots"


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _reset():
    import datetime

    from plans.models import Sprint, SprintEnrollment, SprintFeedbackRequest
    from questionnaires.models import Questionnaire, Response

    Response.objects.all().delete()
    SprintFeedbackRequest.objects.all().delete()
    Questionnaire.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.filter(slug="may-2026").delete()

    sprint = Sprint.objects.create(
        name="May 2026", slug="may-2026",
        start_date=datetime.date(2026, 5, 1), status="active",
        min_tier_level=0,
    )
    connection.close()
    return sprint


def _make_feedback_questionnaire():
    from questionnaires.models import Question, Questionnaire

    q = Questionnaire.objects.create(
        title="May Sprint Feedback", purpose="feedback",
    )
    Question.objects.create(
        questionnaire=q, question_type="long_text",
        prompt="How did this sprint go for you?", order=0, is_required=True,
    )
    Question.objects.create(
        questionnaire=q, question_type="single_choice",
        prompt="Will you join the next sprint?", order=1,
    )
    from questionnaires.models import Question as Q
    choice = Q.objects.get(prompt="Will you join the next sprint?")
    from questionnaires.models import QuestionOption
    QuestionOption.objects.create(question=choice, label="Yes", order=0)
    QuestionOption.objects.create(question=choice, label="No", order=1)
    connection.close()
    return q


def _enroll(sprint_slug, email):
    from accounts.models import User
    from plans.models import Sprint, SprintEnrollment

    sprint = Sprint.objects.get(slug=sprint_slug)
    user = User.objects.get(email=email)
    SprintEnrollment.objects.get_or_create(sprint=sprint, user=user)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestStaffAttachAndDistribute:
    @pytest.mark.core
    def test_attach_then_distribute_shows_completion(self, django_server, browser):
        _ensure_tiers()
        sprint = _reset()
        _create_staff_user("admin@test.com")
        for email in ("a@test.com", "b@test.com", "c@test.com"):
            _create_user(email, tier_slug="main", email_verified=True)
            _enroll("may-2026", email)
        _make_feedback_questionnaire()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )
        # Attach.
        page.locator(
            '[data-testid="sprint-feedback-questionnaire-select"]'
        ).select_option(label="May Sprint Feedback")
        page.locator('[data-testid="sprint-feedback-attach-button"]').click()
        page.locator(
            '[data-testid="sprint-feedback-questionnaire-link"]'
        ).wait_for(state="visible")
        _shot(page, "studio_attached")

        # Distribute.
        page.locator('[data-testid="sprint-feedback-distribute-button"]').click()
        page.locator('[data-testid="sprint-feedback-aggregate"]').wait_for(
            state="visible",
        )
        assert "0 of 3 submitted" in page.locator(
            '[data-testid="sprint-feedback-aggregate"]'
        ).inner_text()
        rows = page.locator('[data-testid="sprint-feedback-completion-row"]')
        assert rows.count() == 3
        statuses = page.locator('[data-testid="sprint-feedback-completion-status"]')
        for i in range(statuses.count()):
            assert statuses.nth(i).inner_text().strip() == "Not started"
        _shot(page, "studio_distributed")

    @pytest.mark.core
    def test_redistribute_picks_up_new_member(self, django_server, browser):
        _ensure_tiers()
        sprint = _reset()
        _create_staff_user("admin@test.com")
        for email in ("a@test.com", "b@test.com", "c@test.com"):
            _create_user(email, tier_slug="main", email_verified=True)
            _enroll("may-2026", email)
        q = _make_feedback_questionnaire()

        # Pre-distribute via service so we only test the re-run in the UI.
        from plans.models import SprintFeedbackRequest
        from plans.services import distribute_sprint_feedback
        fr = SprintFeedbackRequest.objects.create(sprint=sprint, questionnaire=q)
        distribute_sprint_feedback(fr)
        # Fourth member enrolls afterward.
        _create_user("d@test.com", tier_slug="main", email_verified=True)
        _enroll("may-2026", "d@test.com")
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="sprint-feedback-distribute-button"]').click()
        page.locator(
            '[data-testid="sprint-feedback-aggregate"]:has-text("0 of 4 submitted")'
        ).wait_for(state="visible")
        rows = page.locator('[data-testid="sprint-feedback-completion-row"]')
        assert rows.count() == 4
        assert "1 feedback response(s) created" in page.content()


@pytest.mark.django_db(transaction=True)
class TestMemberFillIn:
    def _setup_distributed(self, member_email="member@test.com"):
        from accounts.models import User
        from plans.models import SprintFeedbackRequest
        from plans.services import distribute_sprint_feedback
        from questionnaires.models import Response

        sprint = _reset()
        _create_staff_user("admin@test.com")
        _create_user(member_email, tier_slug="main", email_verified=True)
        _enroll("may-2026", member_email)
        q = _make_feedback_questionnaire()
        fr = SprintFeedbackRequest.objects.create(sprint=sprint, questionnaire=q)
        distribute_sprint_feedback(fr)
        member = User.objects.get(email=member_email)
        response = Response.objects.get(questionnaire=q, respondent=member)
        connection.close()
        return sprint, response

    @pytest.mark.core
    def test_member_finds_and_submits_feedback(self, django_server, browser):
        _ensure_tiers()
        sprint, response = self._setup_distributed()

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{sprint.slug}",
            wait_until="domcontentloaded",
        )
        cta = page.locator('[data-testid="sprint-feedback-cta-link"]')
        cta.wait_for(state="visible")
        assert "shape the next one" in page.locator(
            '[data-testid="sprint-feedback-cta-copy"]'
        ).inner_text()
        _shot(page, "member_cta")
        cta.click()

        page.locator('[data-testid="questionnaire-input-long-text"]').fill(
            "It went really well, lots of progress.",
        )
        page.locator(
            '[data-testid="questionnaire-input-single-choice"] input[type=radio]'
        ).first.check()
        page.locator('[data-testid="questionnaire-submit-button"]').click()

        page.wait_for_url(f"{django_server}/sprints/{sprint.slug}")
        page.locator(
            '[data-testid="sprint-feedback-cta-submitted"]'
        ).wait_for(state="visible")
        assert page.locator(
            '[data-testid="sprint-feedback-cta-link"]'
        ).count() == 0
        _shot(page, "member_after_submit")

    @pytest.mark.core
    def test_required_blank_blocks_submit(self, django_server, browser):
        _ensure_tiers()
        sprint, response = self._setup_distributed()

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{sprint.slug}/feedback/{response.pk}",
            wait_until="domcontentloaded",
        )
        # Submit with the required long-text left blank.
        page.locator('[data-testid="questionnaire-submit-button"]').click()
        err = page.locator('[data-testid="sprint-feedback-error"]')
        err.wait_for(state="visible")
        assert "How did this sprint go for you?" in err.inner_text()
        _shot(page, "member_required_error")

        # Still draft: the member CTA is still present on detail.
        page.goto(
            f"{django_server}/sprints/{sprint.slug}",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="sprint-feedback-cta-link"]').wait_for(
            state="visible",
        )

    @pytest.mark.core
    def test_partial_save_then_resume(self, django_server, browser):
        _ensure_tiers()
        sprint, response = self._setup_distributed()

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{sprint.slug}/feedback/{response.pk}",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="questionnaire-input-long-text"]').fill(
            "Partial answer saved",
        )
        page.locator('[data-testid="questionnaire-save-button"]').click()
        # Reopen.
        page.goto(
            f"{django_server}/sprints/{sprint.slug}/feedback/{response.pk}",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="questionnaire-input-long-text"]'
        ).input_value() == "Partial answer saved"
        # Detail still shows the CTA (not submitted).
        page.goto(
            f"{django_server}/sprints/{sprint.slug}",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="sprint-feedback-cta-link"]').wait_for(
            state="visible",
        )


@pytest.mark.django_db(transaction=True)
class TestStaffReadsSubmitted:
    @pytest.mark.core
    def test_staff_reads_submitted_response_from_sprint(self, django_server, browser):
        from accounts.models import User
        from plans.models import SprintFeedbackRequest
        from plans.services import distribute_sprint_feedback
        from questionnaires.models import Response
        from questionnaires.services import save_response_answers as _save

        _ensure_tiers()
        sprint = _reset()
        _create_staff_user("admin@test.com")
        _create_user("member@test.com", tier_slug="main", email_verified=True)
        _enroll("may-2026", "member@test.com")
        q = _make_feedback_questionnaire()
        fr = SprintFeedbackRequest.objects.create(sprint=sprint, questionnaire=q)
        distribute_sprint_feedback(fr)
        member = User.objects.get(email="member@test.com")
        response = Response.objects.get(questionnaire=q, respondent=member)
        rq = response.response_questions.get(prompt="How did this sprint go for you?")

        class _P(dict):
            def getlist(self, k):
                v = self.get(k)
                return [] if v is None else [v]

        _save(response, _P({f"question_{rq.pk}": "The mentoring was great"}))
        response.mark_submitted()
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )
        assert "1 of 1 submitted" in page.locator(
            '[data-testid="sprint-feedback-aggregate"]'
        ).inner_text()
        page.locator('[data-testid="sprint-feedback-response-link"]').click()
        page.locator('text=The mentoring was great').wait_for(state="visible")
        _shot(page, "staff_reads_response")


@pytest.mark.django_db(transaction=True)
class TestAccessGates:
    def _setup_two_members(self):
        from accounts.models import User
        from plans.models import SprintFeedbackRequest
        from plans.services import distribute_sprint_feedback
        from questionnaires.models import Response

        sprint = _reset()
        _create_staff_user("admin@test.com")
        for email in ("a@test.com", "b@test.com"):
            _create_user(email, tier_slug="main", email_verified=True)
            _enroll("may-2026", email)
        q = _make_feedback_questionnaire()
        fr = SprintFeedbackRequest.objects.create(sprint=sprint, questionnaire=q)
        distribute_sprint_feedback(fr)
        member_b = User.objects.get(email="b@test.com")
        response_b = Response.objects.get(questionnaire=q, respondent=member_b)
        connection.close()
        return sprint, response_b

    @pytest.mark.core
    def test_member_cannot_open_other_members_response(self, django_server, browser):
        _ensure_tiers()
        sprint, response_b = self._setup_two_members()

        context = _auth_context(browser, "a@test.com")
        page = context.new_page()
        resp = page.goto(
            f"{django_server}/sprints/{sprint.slug}/feedback/{response_b.pk}",
            wait_until="domcontentloaded",
        )
        assert resp is not None
        assert resp.status == 404
        assert "How did this sprint go for you?" not in page.content()

    @pytest.mark.core
    def test_anonymous_redirected_to_login(self, django_server, browser):
        _ensure_tiers()
        sprint, response_b = self._setup_two_members()

        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{sprint.slug}/feedback/{response_b.pk}",
            wait_until="domcontentloaded",
        )
        assert "/accounts/login/" in page.url
        assert "How did this sprint go for you?" not in page.content()
        context.close()

    @pytest.mark.core
    def test_non_staff_cannot_reach_studio_attach(self, django_server, browser):
        _ensure_tiers()
        sprint, _ = self._setup_two_members()
        _create_user("main@test.com", tier_slug="main", email_verified=True)

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        resp = page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/feedback/attach",
            wait_until="domcontentloaded",
        )
        assert resp is not None
        # POST-only view returns 405 for GET when staff, but a non-staff
        # user is rejected by the staff gate first (403).
        assert resp.status == 403
