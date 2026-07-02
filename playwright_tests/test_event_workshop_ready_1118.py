"""Playwright coverage for event-level workshop-ready broadcasts (#1118)."""

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

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _clear_state():
    from content.models import Workshop, WorkshopPage
    from email_app.models import EmailLog
    from events.models import Event, EventHost, EventRegistration, Host
    from notifications.models import EventReminderLog, Notification

    EmailLog.objects.all().delete()
    Notification.objects.all().delete()
    EventReminderLog.objects.all().delete()
    EventRegistration.objects.all().delete()
    EventHost.objects.all().delete()
    Host.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _seed_event_with_workshop(
    slug="pw-workshop-ready",
    workshop_status="published",
    registrants=2,
):
    from content.models import Workshop
    from events.models import Event, EventRegistration

    start = datetime.datetime(2026, 6, 8, 16, 0, tzinfo=datetime.UTC)
    event = Event.objects.create(
        title="Playwright Workshop Event",
        slug=slug,
        start_datetime=start,
        end_datetime=start + datetime.timedelta(hours=1),
        status="completed",
    )
    workshop = None
    if workshop_status is not None:
        workshop = Workshop.objects.create(
            title="Playwright Workshop Notes",
            slug=f"{slug}-notes",
            description="A practical write-up for the event registrants.",
            date=datetime.date(2026, 6, 8),
            status=workshop_status,
            landing_required_level=0,
            pages_required_level=5,
            recording_required_level=5,
            event=event,
        )
    users = []
    for idx in range(registrants):
        user = _create_user(f"ready-member-{idx}@test.com", tier_slug="free")
        EventRegistration.objects.create(event=event, user=user)
        users.append(user)
    connection.close()
    return event, workshop, users


def _notification_count_for(email):
    from accounts.models import User
    from notifications.models import Notification

    user = User.objects.get(email=email)
    count = Notification.objects.filter(
        user=user,
        title="Workshop ready: Playwright Workshop Notes",
    ).count()
    connection.close()
    return count


@pytest.mark.django_db(transaction=True)
def test_staff_notifies_registrants_and_member_opens_workshop_notification(
    django_server,
    browser,
):
    _ensure_tiers()
    _clear_state()
    _create_staff_user("ready-staff@test.com")
    event, workshop, users = _seed_event_with_workshop(registrants=2)

    staff_ctx = _auth_context(browser, "ready-staff@test.com")
    staff_page = staff_ctx.new_page()
    staff_page.goto(
        f"{django_server}/studio/events/{event.pk}/edit",
        wait_until="domcontentloaded",
    )

    button = staff_page.locator('[data-testid="notify-workshop-ready-button"]')
    expect(button).to_be_visible()
    button.click()
    staff_page.wait_for_url(f"**/studio/events/{event.pk}/edit*")

    body = staff_page.locator("body").inner_text()
    assert "2 emailed" in body
    assert "2 in-app notifications" in body
    assert "Audience: event registrants plus host" in body
    staff_ctx.close()

    member_ctx = _auth_context(browser, users[0].email)
    member_page = member_ctx.new_page()
    member_page.goto(f"{django_server}/notifications", wait_until="domcontentloaded")

    expect(member_page.get_by_text("Workshop ready: Playwright Workshop Notes")).to_be_visible()
    link = member_page.locator(f'a[href="/workshops/{workshop.slug}"]').first
    link.click()
    member_page.wait_for_url(f"**/workshops/{workshop.slug}")
    expect(member_page.get_by_text("Playwright Workshop Notes")).to_be_visible()
    member_ctx.close()


@pytest.mark.django_db(transaction=True)
def test_disabled_state_before_linked_workshop_is_published(django_server, browser):
    _ensure_tiers()
    _clear_state()
    _create_staff_user("ready-staff@test.com")
    event, _workshop, _users = _seed_event_with_workshop(
        slug="pw-draft-workshop",
        workshop_status="draft",
        registrants=0,
    )

    ctx = _auth_context(browser, "ready-staff@test.com")
    page = ctx.new_page()
    page.goto(
        f"{django_server}/studio/events/{event.pk}/edit",
        wait_until="domcontentloaded",
    )

    expect(page.locator('[data-testid="notify-workshop-ready-button-disabled"]')).to_be_visible()
    expect(page.locator('[data-testid="workshop-ready-disabled-reason"]')).to_contain_text(
        "linked workshop must be published",
    )
    ctx.close()


@pytest.mark.django_db(transaction=True)
def test_staff_discovers_event_workflow_from_workshop_detail(django_server, browser):
    _ensure_tiers()
    _clear_state()
    _create_staff_user("ready-staff@test.com")
    event, workshop, _users = _seed_event_with_workshop(registrants=0)

    ctx = _auth_context(browser, "ready-staff@test.com")
    page = ctx.new_page()
    page.goto(
        f"{django_server}/studio/workshops/{workshop.pk}/",
        wait_until="domcontentloaded",
    )

    link = page.locator('[data-testid="workshop-ready-event-workflow-link"]')
    expect(link).to_be_visible()
    link.click()
    page.wait_for_url(f"**/studio/events/{event.pk}/edit#workshop-ready-panel")
    expect(page.locator('[data-testid="workshop-ready-panel"]')).to_be_visible()
    ctx.close()


@pytest.mark.django_db(transaction=True)
def test_non_registrant_does_not_see_another_events_notification(
    django_server,
    browser,
):
    _ensure_tiers()
    _clear_state()
    _create_staff_user("ready-staff@test.com")
    event, _workshop, _users = _seed_event_with_workshop(registrants=1)
    non_registrant = _create_user("not-registered-ready@test.com", tier_slug="free")

    staff_ctx = _auth_context(browser, "ready-staff@test.com")
    staff_page = staff_ctx.new_page()
    staff_page.goto(
        f"{django_server}/studio/events/{event.pk}/edit",
        wait_until="domcontentloaded",
    )
    staff_page.locator('[data-testid="notify-workshop-ready-button"]').click()
    staff_page.wait_for_url(f"**/studio/events/{event.pk}/edit*")
    staff_ctx.close()

    member_ctx = _auth_context(browser, non_registrant.email)
    member_page = member_ctx.new_page()
    member_page.goto(f"{django_server}/notifications", wait_until="domcontentloaded")

    assert "Workshop ready: Playwright Workshop Notes" not in member_page.locator("body").inner_text()
    member_ctx.close()


@pytest.mark.django_db(transaction=True)
def test_rerun_reports_already_sent_without_duplicate_notifications(
    django_server,
    browser,
):
    _ensure_tiers()
    _clear_state()
    _create_staff_user("ready-staff@test.com")
    event, _workshop, users = _seed_event_with_workshop(registrants=1)

    ctx = _auth_context(browser, "ready-staff@test.com")
    page = ctx.new_page()
    page.goto(
        f"{django_server}/studio/events/{event.pk}/edit",
        wait_until="domcontentloaded",
    )

    button = page.locator('[data-testid="notify-workshop-ready-button"]')
    button.click()
    page.wait_for_url(f"**/studio/events/{event.pk}/edit*")
    assert _notification_count_for(users[0].email) == 1

    button = page.locator('[data-testid="notify-workshop-ready-button"]')
    button.click()
    page.wait_for_url(f"**/studio/events/{event.pk}/edit*")
    body = page.locator("body").inner_text()
    assert "1 already sent" in body
    assert _notification_count_for(users[0].email) == 1
    ctx.close()
