"""Playwright coverage for sprint-end member recap moments (issue #1201)."""

import datetime
import os

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


def _reset():
    from email_app.models import EmailLog
    from notifications.models import Notification
    from plans.models import Plan, Sprint, SprintEndDeliveryLog, SprintEnrollment
    from questionnaires.models import Questionnaire, Response

    SprintEndDeliveryLog.objects.all().delete()
    Notification.objects.filter(notification_type="sprint_recap").delete()
    EmailLog.objects.filter(email_type="sprint_end_recap").delete()
    Response.objects.filter(questionnaire__slug__startswith="recap-").delete()
    Questionnaire.objects.filter(slug__startswith="recap-").delete()
    Plan.objects.filter(sprint__slug__startswith="recap-").delete()
    SprintEnrollment.objects.filter(sprint__slug__startswith="recap-").delete()
    Sprint.objects.filter(slug__startswith="recap-").delete()
    connection.close()


def _create_plan(member_email, *, slug, done=9, total=12, shared=True):
    from accounts.models import User
    from plans.models import Checkpoint, Plan, Sprint, Week

    member = User.objects.get(email=member_email)
    sprint = Sprint.objects.create(
        name=slug.replace("-", " ").title(),
        slug=slug,
        # date-rot-ok: fixed ended sprint window for deterministic recap tests.
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=4,
        status="active",
        min_tier_level=20,
    )
    plan = Plan.objects.create(
        member=member,
        sprint=sprint,
        shared_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC) if shared else None,
    )
    week = Week.objects.create(plan=plan, week_number=1, position=0)
    for index in range(total):
        Checkpoint.objects.create(
            week=week,
            description=f"Checkpoint {index + 1}",
            position=index,
            done_at=(
                datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC)
                if index < done else None
            ),
        )
    connection.close()
    return sprint, plan


def _run_recap(today=datetime.date(2026, 7, 10)):
    from plans.tasks.sprint_end import send_sprint_end_recaps

    result = send_sprint_end_recaps(today=today)
    connection.close()
    return result


def _add_feedback(sprint_slug, member_email):
    from accounts.models import User
    from plans.models import Sprint, SprintFeedbackRequest
    from plans.services import distribute_sprint_feedback
    from questionnaires.models import Question, Questionnaire, Response

    sprint = Sprint.objects.get(slug=sprint_slug)
    questionnaire = Questionnaire.objects.create(
        title=f"{sprint.name} Feedback",
        slug=f"recap-{sprint.slug}-feedback",
        purpose="feedback",
    )
    Question.objects.create(
        questionnaire=questionnaire,
        question_type="long_text",
        prompt="What should we improve next time?",
        order=0,
        is_required=True,
    )
    request = SprintFeedbackRequest.objects.create(
        sprint=sprint,
        questionnaire=questionnaire,
    )
    distribute_sprint_feedback(request)
    member = User.objects.get(email=member_email)
    response = Response.objects.get(questionnaire=questionnaire, respondent=member)
    connection.close()
    return response


@pytest.mark.django_db(transaction=True)
class TestSprintEndRecapJourney:
    @pytest.mark.core
    def test_member_opens_recap_notification_and_plan(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("recap-member@test.com", tier_slug="main", email_verified=True)
        sprint, plan = _create_plan("recap-member@test.com", slug="recap-ended")
        _run_recap()

        context = _auth_context(browser, "recap-member@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator("#notification-bell-btn").click()
        item = page.locator("#notification-list a").filter(has_text="Sprint recap")
        item.wait_for(state="visible")
        assert "9 of 12 checkpoints" in page.locator("#notification-list").inner_text()
        item.click()
        page.wait_for_url(f"{django_server}/sprints/{sprint.slug}/plan/{plan.pk}")
        page.locator('[data-testid="member-plan"]').wait_for(state="visible")

    @pytest.mark.core
    def test_feedback_cta_submits_and_returns_thank_you_state(
        self,
        django_server,
        browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("recap-feedback@test.com", tier_slug="main", email_verified=True)
        sprint, _plan = _create_plan("recap-feedback@test.com", slug="recap-feedback")
        _add_feedback(sprint.slug, "recap-feedback@test.com")
        _run_recap()

        context = _auth_context(browser, "recap-feedback@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="account-sprint-plan-feedback"]').click()
        page.locator('[data-testid="questionnaire-input-long-text"]').fill(
            "More pairing time would help.",
        )
        page.locator('[data-testid="questionnaire-submit-button"]').click()
        page.wait_for_url(f"{django_server}/sprints/{sprint.slug}")
        page.locator('[data-testid="sprint-feedback-cta-submitted"]').wait_for(
            state="visible",
        )

    @pytest.mark.core
    def test_next_plan_cta_lands_on_existing_carry_over_panel(
        self,
        django_server,
        browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("recap-carry@test.com", tier_slug="main", email_verified=True)
        ended, _old_plan = _create_plan(
            "recap-carry@test.com",
            slug="recap-carry-ended",
            done=1,
            total=3,
        )
        from accounts.models import User
        from plans.models import Plan, Sprint, Week

        member = User.objects.get(email="recap-carry@test.com")
        next_sprint = Sprint.objects.create(
            name="Recap Next",
            slug="recap-carry-next",
            start_date=ended.end_date,
            duration_weeks=4,
            status="active",
            min_tier_level=20,
        )
        next_plan = Plan.objects.create(
            member=member,
            sprint=next_sprint,
        )
        Week.objects.create(plan=next_plan, week_number=1, position=0)
        connection.close()
        _run_recap()

        context = _auth_context(browser, "recap-carry@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        cta = page.locator('[data-testid="account-sprint-plan-next-action"]')
        cta.wait_for(state="visible")
        assert "Carry over unfinished work" in cta.inner_text()
        cta.click()
        page.wait_for_url(
            f"{django_server}/sprints/{next_sprint.slug}/plan/{next_plan.pk}",
        )
        page.locator('[data-testid="plan-carry-over-panel"]').wait_for(
            state="visible",
        )

    @pytest.mark.core
    def test_enrolled_sprint_card_polish(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("recap-enrolled@test.com", tier_slug="main", email_verified=True)
        _create_plan(
            "recap-enrolled@test.com",
            slug="recap-enrolled",
            done=0,
            total=0,
        )

        context = _auth_context(browser, "recap-enrolled@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
        page.locator('[data-testid="sprints-sprint-enrolled"]').wait_for(
            state="visible",
        )
        assert "You're enrolled" in page.content()
        assert "Use the next step below to continue" not in page.content()
