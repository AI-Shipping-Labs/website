"""Playwright coverage for sprint plan-ready email bulk action (#1055)."""

import datetime
import os

import pytest
from playwright.sync_api import expect

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


def _clear_plan_ready_data():
    from email_app.models import EmailLog
    from notifications.models import Notification
    from plans.models import Plan, PlanReadyEmailLog, Sprint, SprintEnrollment

    PlanReadyEmailLog.objects.all().delete()
    EmailLog.objects.filter(email_type="plan_shared").delete()
    Notification.objects.filter(notification_type="plan_shared").delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _seed_sprint_with_plans():
    from accounts.models import User
    from plans.models import (
        PLAN_READY_EMAIL_STATUS_SENT,
        Plan,
        PlanReadyEmailLog,
        Sprint,
    )

    sprint = Sprint.objects.create(
        name="May Sprint",
        slug="may-sprint",
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=4,
    )
    users = {
        email: User.objects.get(email=email)
        for email in (
            "eligible-a@test.com",
            "eligible-b@test.com",
            "sent@test.com",
        )
    }
    Plan.objects.create(member=users["eligible-a@test.com"], sprint=sprint)
    Plan.objects.create(member=users["eligible-b@test.com"], sprint=sprint)
    sent = Plan.objects.create(member=users["sent@test.com"], sprint=sprint)
    PlanReadyEmailLog.objects.create(
        plan=sent,
        sprint=sprint,
        member=sent.member,
        status=PLAN_READY_EMAIL_STATUS_SENT,
        sent_at=datetime.datetime(2026, 5, 2, 12, 0, tzinfo=datetime.UTC),
    )
    sprint_id = sprint.pk
    connection.close()
    return sprint_id


@pytest.mark.django_db(transaction=True)
def test_staff_previews_and_sends_plan_ready_emails(django_server, browser):
    _ensure_tiers()
    _clear_plan_ready_data()
    _create_staff_user("staff@test.com")
    for email in ("eligible-a@test.com", "eligible-b@test.com", "sent@test.com"):
        _create_user(email, tier_slug="free", email_verified=True)
    sprint_id = _seed_sprint_with_plans()

    context = _auth_context(browser, "staff@test.com")
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())

    page.goto(
        f"{django_server}/studio/sprints/{sprint_id}/",
        wait_until="domcontentloaded",
    )

    expect(page.locator('[data-testid="plan-ready-total-count"]')).to_have_text("3")
    expect(page.locator('[data-testid="plan-ready-eligible-count"]')).to_have_text("2")
    expect(page.locator('[data-testid="plan-ready-already-count"]')).to_have_text("1")
    expect(page.locator('[data-testid="plan-ready-email-button"]')).to_be_enabled()

    page.locator('[data-testid="plan-ready-email-button"]').click()
    page.wait_for_url(f"{django_server}/studio/sprints/{sprint_id}/")

    expect(page.get_by_text("2 sent, 1 skipped, 0 failed")).to_be_visible()
    expect(page.locator('[data-testid="plan-ready-eligible-count"]')).to_have_text("0")
    expect(page.locator('[data-testid="plan-ready-already-count"]')).to_have_text("3")
    expect(page.locator('[data-testid="plan-ready-email-button"]')).to_be_disabled()
    expect(page.get_by_text("All plan-ready emails have already been sent")).to_be_visible()

    context.close()


@pytest.mark.django_db(transaction=True)
def test_staff_sees_disabled_empty_plan_ready_action(django_server, browser):
    from plans.models import Sprint

    _ensure_tiers()
    _clear_plan_ready_data()
    _create_staff_user("staff@test.com")
    sprint = Sprint.objects.create(
        name="Empty Sprint",
        slug="empty-sprint",
        start_date=datetime.date(2026, 6, 1),
    )
    sprint_id = sprint.pk
    connection.close()

    context = _auth_context(browser, "staff@test.com")
    page = context.new_page()

    page.goto(
        f"{django_server}/studio/sprints/{sprint_id}/",
        wait_until="domcontentloaded",
    )

    expect(page.locator('[data-testid="plan-ready-total-count"]')).to_have_text("0")
    expect(page.locator('[data-testid="plan-ready-eligible-count"]')).to_have_text("0")
    expect(page.locator('[data-testid="plan-ready-email-button"]')).to_be_disabled()
    expect(page.locator('[data-testid="plan-ready-email-helper"]')).to_contain_text(
        "No plans in this sprint yet.",
    )

    context.close()
