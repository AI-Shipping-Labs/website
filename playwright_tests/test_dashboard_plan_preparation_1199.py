"""Playwright coverage for dashboard plan-preparation state (#1199)."""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
]

SCREENSHOT_DIR = (
    Path(__file__).resolve().parents[1] / ".tmp" / "aisl-issue-1199-screenshots"
)


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=False)


def _ensure_onboarding_seed():
    import importlib

    from django.apps import apps as django_apps

    seed_module = importlib.import_module(
        "questionnaires.migrations.0003_seed_personas_and_onboarding",
    )
    seed_module.seed(django_apps, None)
    connection.close()


def _force_form_first_onboarding():
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


def _prepare_onboarding_flow():
    ensure_tiers()
    _ensure_onboarding_seed()
    _force_form_first_onboarding()


def _answer_visible_form(page):
    for i in range(page.locator('[data-testid="questionnaire-input-text"]').count()):
        page.locator('[data-testid="questionnaire-input-text"]').nth(i).fill(
            "Ship a focused AI project",
        )
    for i in range(page.locator('[data-testid="questionnaire-input-long-text"]').count()):
        page.locator('[data-testid="questionnaire-input-long-text"]').nth(i).fill(
            "I want a practical plan with weekly accountability.",
        )
    for i in range(page.locator('[data-testid="questionnaire-input-number"]').count()):
        page.locator('[data-testid="questionnaire-input-number"]').nth(i).fill("5")
    for i in range(page.locator('[data-testid="questionnaire-question-row"]').count()):
        row = page.locator('[data-testid="questionnaire-question-row"]').nth(i)
        radios = row.locator('input[type="radio"]')
        if radios.count():
            radios.first.check()
        checkboxes = row.locator('input[type="checkbox"]')
        if checkboxes.count():
            checkboxes.first.check()
    for i in range(page.locator('[data-testid="questionnaire-option-free-text"]').count()):
        page.locator('[data-testid="questionnaire-option-free-text"]').nth(i).fill(
            "Other useful context",
        )


def _submitted_onboarding(email):
    from django.utils import timezone

    from accounts.models import User
    from questionnaires.models import Answer, Questionnaire, Response, ResponseQuestion

    user = User.objects.get(email=email)
    questionnaire, _ = Questionnaire.objects.get_or_create(
        slug="dashboard-plan-prep-1199",
        defaults={"title": "Dashboard Plan Prep", "purpose": "onboarding"},
    )
    response, _ = Response.objects.update_or_create(
        respondent=user,
        questionnaire=questionnaire,
        defaults={"status": "submitted", "submitted_at": timezone.now()},
    )
    question, _ = ResponseQuestion.objects.get_or_create(
        response=response,
        prompt="What do you want to ship?",
        defaults={"question_type": "long_text", "order": 1},
    )
    Answer.objects.update_or_create(
        response=response,
        question=question,
        defaults={"text_value": "A focused AI shipping project"},
    )
    connection.close()


def _create_plan(email, slug, *, shared):
    from django.utils import timezone

    from accounts.models import User
    from plans.models import Plan, Sprint

    user = User.objects.get(email=email)
    sprint = Sprint.objects.create(
        name=slug.replace("-", " ").title(),
        slug=slug,
        start_date=datetime.date.today() - datetime.timedelta(days=7),
        duration_weeks=6,
        status="active",
        min_tier_level=20,
    )
    Plan.objects.create(
        member=user,
        sprint=sprint,
        goal=f"{slug} staff-only draft goal",
        shared_at=timezone.now() if shared else None,
    )
    connection.close()
    return sprint


def test_member_finishes_onboarding_then_sees_dashboard_waiting_state(
    django_server, browser,
):
    _prepare_onboarding_flow()
    create_user("finish1199@example.com", tier_slug="main", email_verified=True)

    context = auth_context(browser, "finish1199@example.com")
    page = context.new_page()
    page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
    page.locator('[data-testid="onboarding-option"] input[value="none"]').check()
    page.locator('[data-testid="onboarding-continue-button"]').click()
    page.locator('[data-testid="questionnaire-response-form"]').wait_for(
        state="visible",
    )
    _answer_visible_form(page)
    page.locator('[data-testid="questionnaire-submit-button"]').click()

    page.locator('[data-testid="onboarding-complete-title"]').wait_for(
        state="visible",
    )
    completion_text = page.locator("main").inner_text()
    assert "Alexey and Valeria" in completion_text
    assert "1-2 business days" in completion_text
    assert "bell notification and email" in completion_text
    assert page.locator('[data-testid="onboarding-complete-row"]').count() > 0
    _shot(page, "01-onboarding-complete")

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    card = page.locator('[data-testid="dashboard-plan-preparing-card"]')
    card.wait_for(state="visible")
    card_text = card.inner_text()
    assert "Your plan is being prepared" in card_text
    assert "Alexey and Valeria" in card_text
    assert "1-2 business days" in card_text
    assert "bell" in card_text
    assert "email" in card_text
    assert page.locator('[data-testid="onboarding-prompt"]').count() == 0
    assert page.get_by_text("Open my plan").count() == 0
    _shot(page, "02-dashboard-preparing")

    page.locator('[data-testid="dashboard-plan-preparing-cta"]').click()
    page.locator('[data-testid="onboarding-complete-title"]').wait_for(
        state="visible",
    )
    assert page.url.rstrip("/").endswith("/onboarding")
    assert "bell notification and email" in page.locator("main").inner_text()
    assert page.locator('[data-testid="onboarding-complete-row"]').count() > 0
    _shot(page, "03-review-onboarding")


def test_unshared_staff_draft_stays_in_preparing_state(django_server, browser):
    _prepare_onboarding_flow()
    create_user("draft1199@example.com", tier_slug="main", email_verified=True)
    _submitted_onboarding("draft1199@example.com")
    sprint = _create_plan("draft1199@example.com", "draft-1199", shared=False)

    context = auth_context(browser, "draft1199@example.com")
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator('[data-testid="dashboard-plan-preparing-card"]').wait_for(
        state="visible",
    )
    assert page.locator('[data-testid="account-sprint-plan-card"]').count() == 0
    assert page.locator('[data-testid="dashboard-active-sprint"]').count() == 0
    assert page.get_by_text("Open my plan").count() == 0
    assert page.get_by_text(sprint.name).count() == 0
    assert page.get_by_text("View cohort").count() == 0
    assert "staff-only draft goal" not in page.locator("body").inner_text()
    _shot(page, "04-draft-still-preparing")


def test_shared_plan_replaces_waiting_state(django_server, browser):
    _prepare_onboarding_flow()
    create_user("shared1199@example.com", tier_slug="main", email_verified=True)
    _submitted_onboarding("shared1199@example.com")
    _create_plan("shared1199@example.com", "shared-1199", shared=True)

    context = auth_context(browser, "shared1199@example.com")
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator('[data-testid="account-sprint-plan-card"]').wait_for(
        state="visible",
    )
    assert page.get_by_text("Open my plan").count() == 1
    assert page.locator('[data-testid="dashboard-plan-preparing-card"]').count() == 0
    assert page.locator('[data-testid="onboarding-prompt"]').count() == 0
    _shot(page, "05-shared-plan")
