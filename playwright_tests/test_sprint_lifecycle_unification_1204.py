"""Playwright coverage for sprint lifecycle/admin-status unification (#1204)."""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _ended_start_date():
    return datetime.date.today() - datetime.timedelta(weeks=8)


def _clear_sprints():
    from django.db import connection

    from events.models import EventSeries
    from plans.models import (
        Plan,
        PlanRequest,
        Sprint,
        SprintAccountabilityPartner,
        SprintEnrollment,
        SprintFeedbackRequest,
        SprintFeedbackSummary,
    )

    SprintAccountabilityPartner.objects.all().delete()
    SprintFeedbackSummary.objects.all().delete()
    SprintFeedbackRequest.objects.all().delete()
    PlanRequest.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    EventSeries.objects.filter(slug__startswith="issue-1204").delete()
    connection.close()


def _create_ended_sprint(slug="issue-1204-ended", status="active"):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name="Issue 1204 Ended Sprint",
        slug=slug,
        start_date=_ended_start_date(),
        duration_weeks=6,
        status=status,
        min_tier_level=0,
    )
    connection.close()
    return sprint


def _create_operator_sprint_with_work():
    from django.db import connection

    from events.models import EventSeries
    from plans.models import (
        Plan,
        SprintAccountabilityPartner,
        SprintEnrollment,
    )

    sprint = _create_ended_sprint()
    staff = _create_staff_user("issue-1204-staff-seed@test.com")
    member = _create_user("issue-1204-member-a@test.com", tier_slug="main")
    partner = _create_user("issue-1204-member-b@test.com", tier_slug="main")
    series = EventSeries.objects.create(
        name="Issue 1204 Sprint Calls",
        slug="issue-1204-sprint-calls",
        start_time=datetime.time(18, 0),
    )
    sprint.event_series = series
    sprint.save(update_fields=["event_series"])
    SprintEnrollment.objects.create(sprint=sprint, user=member, enrolled_by=staff)
    SprintEnrollment.objects.create(sprint=sprint, user=partner, enrolled_by=staff)
    Plan.objects.create(sprint=sprint, member=member, goal="Ship lifecycle UI")
    SprintAccountabilityPartner.objects.create(
        sprint=sprint,
        member=member,
        partner=partner,
        assigned_by=staff,
    )
    connection.close()
    return sprint


def _sprint_status(sprint_id):
    from django.db import connection

    from plans.models import Sprint

    status = Sprint.objects.get(pk=sprint_id).status
    connection.close()
    return status


def _create_staff_token():
    from django.db import connection

    from accounts.models import Token
    from playwright_tests.conftest import create_staff_user

    staff = create_staff_user("issue-1204-api-staff@test.com")
    token, plaintext = Token.create_for_user(user=staff, name="issue 1204 api")
    connection.close()
    return plaintext


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_staff_scans_lifecycle_status_and_marks_completed(
    django_server, browser,
):
    _clear_sprints()
    _ensure_tiers()
    _create_staff_user("issue-1204-operator@test.com")
    sprint = _create_operator_sprint_with_work()

    context = _auth_context(browser, "issue-1204-operator@test.com")
    page = context.new_page()

    page.goto(f"{django_server}/studio/sprints/", wait_until="domcontentloaded")
    row = page.locator(
        f'tr:has(a[href="/studio/sprints/{sprint.pk}/"])'
    )
    expect(row.locator('[data-testid="sprint-list-lifecycle"]')).to_contain_text(
        "Ended"
    )
    expect(
        row.locator('[data-testid="sprint-list-admin-status"]')
    ).to_contain_text("Active")
    expect(row.locator('[data-testid="sprint-list-pending-count"]')).to_be_visible()
    expect(row.get_by_role("link", name="View")).to_be_visible()
    expect(row.get_by_role("link", name="Edit")).to_be_visible()

    row.get_by_role("link", name="View").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-testid="sprint-lifecycle-badge"]')).to_contain_text(
        "Ended"
    )
    expect(
        page.locator('[data-testid="sprint-admin-status-badge"]')
    ).to_contain_text("Active")

    page.locator('[data-testid="sprint-complete-button"]').click()
    page.wait_for_load_state("domcontentloaded")

    expect(page.locator('[data-testid="messages-region"]')).to_contain_text(
        "marked completed"
    )
    expect(page.locator('[data-testid="sprint-lifecycle-badge"]')).to_contain_text(
        "Ended"
    )
    expect(
        page.locator('[data-testid="sprint-admin-status-badge"]')
    ).to_contain_text("Completed")
    expect(page.locator('[data-testid="sprint-event-series-link"]')).to_contain_text(
        "Issue 1204 Sprint Calls"
    )
    expect(page.locator('[data-testid="sprint-members-section"]')).to_be_visible()
    assert page.locator('[data-testid="sprint-enrolled-member-row"]').count() == 2
    expect(
        page.locator('[data-testid="sprint-member-partner"]').first
    ).to_be_visible()
    assert _sprint_status(sprint.pk) == "completed"
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_member_pages_keep_date_lifecycle_not_admin_status(django_server, browser):
    _clear_sprints()
    _ensure_tiers()
    _create_user("issue-1204-main@test.com", tier_slug="main")
    sprint = _create_ended_sprint(status="completed")

    context = _auth_context(browser, "issue-1204-main@test.com")
    page = context.new_page()

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
    card = page.locator(
        f'[data-testid="sprints-sprint-card"]:has(a[href$="/sprints/{sprint.slug}"])'
    )
    expect(card.locator('[data-testid="sprints-sprint-status"]')).to_contain_text(
        "Ended"
    )
    expect(card).not_to_contain_text("Admin status")

    card.locator(f'a[href$="/sprints/{sprint.slug}"]').first.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-testid="sprint-status-badge"]')).to_contain_text(
        "Ended"
    )
    expect(page.locator("body")).not_to_contain_text("Admin status")
    expect(page.locator('[data-testid="sprint-status-badge"]')).not_to_contain_text(
        "Completed"
    )
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_staff_token_api_reads_lifecycle_and_patches_status(
    django_server, browser,
):
    _clear_sprints()
    _ensure_tiers()
    sprint = _create_ended_sprint(status="active")
    token = _create_staff_token()
    api_context = browser.new_context()
    request = api_context.request
    headers = {"Authorization": f"Token {token}"}

    detail = request.get(f"{django_server}/api/sprints/{sprint.slug}", headers=headers)
    assert detail.status == 200
    body = detail.json()
    assert body["status"] == "active"
    assert body["lifecycle_badge"] == {"state": "ended", "label": "Ended"}

    updated = request.patch(
        f"{django_server}/api/sprints/{sprint.slug}",
        headers=headers,
        data={"status": "completed"},
    )
    assert updated.status == 200
    updated_body = updated.json()
    assert updated_body["status"] == "completed"
    assert updated_body["lifecycle_badge"] == {"state": "ended", "label": "Ended"}

    context = _auth_context(browser, "issue-1204-api-staff@test.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/sprints/{sprint.pk}/",
        wait_until="domcontentloaded",
    )
    expect(
        page.locator('[data-testid="sprint-admin-status-badge"]')
    ).to_contain_text("Completed")
    context.close()
    api_context.close()
