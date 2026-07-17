"""Playwright coverage for Studio conventions cleanup (#1196)."""

import os
from datetime import timedelta
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
from django.conf import settings  # noqa: E402
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1196")


def _reset_state():
    from accounts.models import User
    from events.models import Event, EventSeries
    from payments.models import PaymentAccountMismatch

    PaymentAccountMismatch.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    User.objects.exclude(is_staff=True).delete()
    connection.close()


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _button_classes(page, selector):
    return page.locator(selector).get_attribute("class") or ""


def _assert_no_raw_blue(classes):
    assert "bg-blue-600" not in classes
    assert "hover:bg-blue-700" not in classes


def _create_zoom_event(slug, *, status="upcoming"):
    from events.models import Event

    start = timezone.now() + timedelta(days=14)
    event = Event.objects.create(
        title=slug.replace("-", " ").title(),
        slug=slug,
        status=status,
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        platform="zoom",
        origin="studio",
        timezone="UTC",
    )
    connection.close()
    return event


def _create_series_with_event(slug):
    from events.models import Event, EventSeries

    series = EventSeries.objects.create(
        name=slug.replace("-", " ").title(),
        slug=slug,
        start_time=timezone.now().time(),
        timezone="UTC",
    )
    start = timezone.now() + timedelta(days=21)
    Event.objects.create(
        title=f"{series.name} Session",
        slug=f"{slug}-session",
        status="upcoming",
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        platform="zoom",
        origin="studio",
        timezone="UTC",
        event_series=series,
    )
    connection.close()
    return series


def _run_series_zoom_inline(series_id):
    from events.tasks.create_series_zoom_meetings import (
        create_series_zoom_meetings,
    )

    return create_series_zoom_meetings(series_id)


@pytest.mark.django_db(transaction=True)
def test_event_edit_actions_use_canonical_styles_and_keep_fetch_paths(
    django_server, browser,
):
    _ensure_tiers()
    _reset_state()
    _create_staff_user("studio-1196-event@test.com")
    event = _create_zoom_event("studio-1196-event")

    ctx = _auth_context(browser, "studio-1196-event@test.com")
    page = ctx.new_page()
    page.route(
        f"{django_server}/studio/events/{event.pk}/create-zoom",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"meeting_id":"z1196","join_url":"https://zoom.us/j/z1196"}',
        ),
    )
    page.route(
        f"{django_server}/studio/events/{event.pk}/announce-slack",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok":true}',
        ),
    )

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")

    zoom_classes = _button_classes(page, "#create-zoom-btn")
    _assert_no_raw_blue(zoom_classes)
    assert "bg-accent" in zoom_classes
    assert "text-accent-foreground" in zoom_classes

    notify_classes = _button_classes(page, "#notify-subscribers-btn")
    slack_classes = _button_classes(page, "#post-to-slack-btn")
    assert "bg-accent" in notify_classes
    _assert_no_raw_blue(slack_classes)
    assert "border-border" in slack_classes
    assert "text-foreground" in slack_classes

    page.locator("#create-zoom-btn").click()
    page.locator("#zoom-status").wait_for(state="visible", timeout=10000)
    assert "Zoom meeting created" in page.locator("#zoom-status").inner_text()

    page.locator("#post-to-slack-btn").click()
    page.locator("#slack-status").wait_for(state="visible", timeout=10000)
    assert "Slack announcement posted successfully" in page.locator("#slack-status").inner_text()
    _capture(page, "event-edit-actions")
    ctx.close()


@pytest.mark.django_db(transaction=True)
def test_event_series_actions_use_secondary_styles_and_zoom_post_still_runs(
    django_server, browser,
):
    _ensure_tiers()
    _reset_state()
    _create_staff_user("studio-1196-series@test.com")
    series = _create_series_with_event("studio-1196-series")

    ctx = _auth_context(browser, "studio-1196-series@test.com")
    page = ctx.new_page()
    page.goto(
        f"{django_server}/studio/event-series/{series.pk}/",
        wait_until="domcontentloaded",
    )

    for selector in (
        '[data-testid="event-series-announce-slack"]',
        '[data-testid="series-create-zoom"]',
    ):
        classes = _button_classes(page, selector)
        _assert_no_raw_blue(classes)
        assert "border-border" in classes
        assert "text-foreground" in classes

    for selector in (
        '[data-testid="event-series-notify"]',
        '[data-testid="event-series-announce-slack"]',
        '[data-testid="series-create-zoom"]',
        '[data-testid="event-series-metadata-save"]',
        '[data-testid="event-series-delete-form"]',
    ):
        assert page.locator(selector).count() == 1

    with patch(
        "studio.views.event_series.enqueue_create_series_zoom_meetings",
        side_effect=_run_series_zoom_inline,
    ), patch(
        "events.tasks.create_series_zoom_meetings.create_meeting",
        return_value={"meeting_id": "z1196", "join_url": "https://zoom.us/j/z1196"},
    ):
        page.locator('[data-testid="series-create-zoom"]').click()
        page.wait_for_url(f"**/studio/event-series/{series.pk}/", timeout=10000)

    page.locator('[data-testid="series-zoom-summary-counts"]').wait_for(
        state="visible",
        timeout=10000,
    )
    assert "Created 1" in page.locator('[data-testid="series-zoom-summary-counts"]').inner_text()
    _capture(page, "event-series-actions")
    ctx.close()


@pytest.mark.django_db(transaction=True)
def test_events_list_has_single_primary_create_cta(django_server, browser):
    _ensure_tiers()
    _reset_state()
    _create_staff_user("studio-1196-list@test.com")

    ctx = _auth_context(browser, "studio-1196-list@test.com")
    page = ctx.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    new_event_classes = _button_classes(page, '[data-testid="event-new-button"]')
    new_series_classes = _button_classes(page, '[data-testid="event-series-new-button"]')
    assert "bg-accent" in new_event_classes
    assert "text-accent-foreground" in new_event_classes
    assert "bg-accent" not in new_series_classes
    assert "min-h-[44px]" in new_series_classes
    assert "w-full" in new_series_classes
    assert page.locator('[data-testid="event-series-new-button"]').get_attribute("href").endswith(
        "/studio/event-series/new"
    )
    _capture(page, "events-list-ctas")
    ctx.close()


@pytest.mark.django_db(transaction=True)
def test_payment_mismatch_empty_states_use_shared_component(django_server, browser):
    from accounts.models import User
    from payments.models import PaymentAccountMismatch

    _ensure_tiers()
    _reset_state()
    _create_staff_user("studio-1196-payments@test.com")
    paid = User.objects.create_user(email="paid-1196@test.com", password="pw")
    candidate = User.objects.create_user(email="candidate-1196@test.com", password="pw")
    PaymentAccountMismatch.objects.create(
        stripe_session_id="cs_1196",
        stripe_customer_id="cus_1196",
        stripe_subscription_id="sub_1196",
        stripe_email=candidate.email,
        paid_user=paid,
        candidate_user=candidate,
        reason=PaymentAccountMismatch.REASON_PRIMARY_EMAIL_COLLISION,
    )
    connection.close()

    ctx = _auth_context(browser, "studio-1196-payments@test.com")
    page = ctx.new_page()
    page.goto(
        f"{django_server}/studio/users/payment-mismatches/?status=resolved",
        wait_until="domcontentloaded",
    )
    assert page.locator('[data-testid="studio-empty-state-filter"]').is_visible()
    assert page.locator('[data-empty-state="payment-mismatches"]').is_visible()
    assert "Clear filters" in page.locator('[data-testid="studio-empty-state-filter"]').inner_text()
    assert page.locator("text=No payment mismatches found.").count() == 0

    PaymentAccountMismatch.objects.all().delete()
    connection.close()
    page.goto(
        f"{django_server}/studio/users/payment-mismatches/",
        wait_until="domcontentloaded",
    )
    assert page.locator('[data-testid="studio-empty-state-fresh"]').is_visible()
    assert "Payment audit is clean" in page.locator('[data-testid="studio-empty-state-fresh"]').inner_text()
    _capture(page, "payment-mismatch-empty")
    ctx.close()


@pytest.mark.django_db(transaction=True)
def test_user_tier_and_status_pills_have_light_dark_contrast_classes(
    django_server, browser,
):
    _ensure_tiers()
    _reset_state()
    staff = _create_staff_user("studio-1196-users@test.com")
    basic = _create_user("basic-1196@test.com", tier_slug="basic")
    premium = _create_user("premium-1196@test.com", tier_slug="premium")
    inactive = _create_user("inactive-1196@test.com", tier_slug="free")
    inactive.is_active = False
    inactive.save(update_fields=["is_active"])
    connection.close()

    ctx = _auth_context(browser, "studio-1196-users@test.com")
    page = ctx.new_page()
    page.goto(f"{django_server}/studio/users/?filter=all", wait_until="domcontentloaded")

    assert "text-blue-700" in page.locator(
        f'[data-testid="user-row-{basic.pk}"] [data-testid="user-list-tier-pill"]'
    ).get_attribute("class")
    assert "dark:text-blue-300" in page.locator(
        f'[data-testid="user-row-{basic.pk}"] [data-testid="user-list-tier-pill"]'
    ).get_attribute("class")
    assert "text-amber-700" in page.locator(
        f'[data-testid="user-row-{premium.pk}"] [data-testid="user-list-tier-pill"]'
    ).get_attribute("class")
    assert "text-red-700" in page.locator(
        f'[data-testid="user-row-{inactive.pk}"] [data-testid="user-status"]'
    ).get_attribute("class")
    assert "text-blue-700" in page.locator(
        f'[data-testid="user-row-{staff.pk}"] [data-testid="user-status"]'
    ).get_attribute("class")

    page.goto(f"{django_server}/studio/users/{basic.pk}/", wait_until="domcontentloaded")
    assert "text-green-700" in page.locator(
        '[data-testid="user-detail-status-pill"]'
    ).get_attribute("class")
    _capture(page, "users-pill-contrast")
    ctx.close()


@pytest.mark.django_db(transaction=True)
def test_sidebar_hides_placeholder_version_and_shows_real_version(
    django_server, browser,
):
    _ensure_tiers()
    _reset_state()
    _create_staff_user("studio-1196-version@test.com")
    original_version = settings.VERSION

    ctx = _auth_context(browser, "studio-1196-version@test.com")
    page = ctx.new_page()
    try:
        settings.VERSION = "N/A"
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        assert page.locator("text=vN/A").count() == 0

        settings.VERSION = "2026.07.09"
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        assert page.locator("text=v2026.07.09").count() == 1
    finally:
        settings.VERSION = original_version
        ctx.close()
