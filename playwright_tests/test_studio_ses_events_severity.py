"""Playwright coverage for SES-event severity explanations (#849).

Exercises the Studio SES events list + detail pages: severity indicators,
the bounce-type info affordance (hover + keyboard focus), the diagnostic
plain-English interpretation, and the 3-strike consequence note. Rows are
seeded directly via the ``SesEvent`` model in each test.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Local-only: seeds the DB + injects a session cookie, cannot run against
# the deployed dev environment.
pytestmark = [pytest.mark.local_only, pytest.mark.core]

STAFF_EMAIL = "ses-severity-staff@test.com"


def _reset_events():
    from email_app.models import SesEvent

    SesEvent.objects.all().delete()
    connection.close()


def _make_event(**kwargs):
    """Create one SesEvent, returning its pk. Closes the connection after."""
    import uuid

    from email_app.models import SesEvent

    defaults = {
        "recipient_email": "",
        "bounce_type": "",
        "bounce_subtype": "",
        "diagnostic_code": "",
        "action_taken": "",
        "raw_payload": {},
        "message_id": f"msg-{uuid.uuid4()}",
    }
    defaults.update(kwargs)
    event = SesEvent.objects.create(**defaults)
    pk = event.pk
    connection.close()
    return pk


def _soft_bounce_threshold():
    from accounts.utils.bounce import SOFT_BOUNCE_THRESHOLD

    return SOFT_BOUNCE_THRESHOLD


def _login(browser):
    _ensure_tiers()
    _create_staff_user(STAFF_EMAIL)
    return _auth_context(browser, STAFF_EMAIL)


@pytest.mark.django_db(transaction=True)
def test_permanent_bounce_flagged_serious_on_list_and_detail(django_server, browser):
    _reset_events()
    pk = _make_event(
        event_type="bounce_permanent",
        recipient_email="hardbounce@test.com",
        bounce_type="Permanent",
        bounce_subtype="General",
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/", wait_until="domcontentloaded")

    severity = page.locator(f'[data-testid="ses-event-severity-{pk}"]')
    assert severity.is_visible()
    assert severity.get_attribute("data-severity") == "high"
    # Conveyed by text/aria, not colour alone.
    assert "Serious" in severity.inner_text()
    assert "Serious" in (severity.get_attribute("aria-label") or "")

    page.goto(f"{django_server}/studio/ses-events/{pk}/", wait_until="domcontentloaded")
    detail_sev = page.locator('[data-testid="ses-event-detail-severity"]')
    assert detail_sev.get_attribute("data-severity") == "high"
    assert "Serious" in detail_sev.inner_text()
    consequence = page.locator('[data-testid="ses-event-detail-consequence"]')
    text = consequence.inner_text().lower()
    assert "unsubscribe" in text
    assert "immediately" in text
    context.close()


@pytest.mark.django_db(transaction=True)
def test_transient_bounce_shown_temporary_with_threshold(django_server, browser):
    _reset_events()
    pk = _make_event(
        event_type="bounce_transient",
        recipient_email="soft@test.com",
        bounce_type="Transient",
        bounce_subtype="General",
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/{pk}/", wait_until="domcontentloaded")

    detail_sev = page.locator('[data-testid="ses-event-detail-severity"]')
    assert detail_sev.get_attribute("data-severity") == "medium"
    assert "Temporary" in detail_sev.inner_text()

    consequence = page.locator('[data-testid="ses-event-detail-consequence"]')
    text = consequence.inner_text().lower()
    assert "temporary" in text
    assert str(_soft_bounce_threshold()) in consequence.inner_text()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_real_diagnostic_decoded_to_plain_english(django_server, browser):
    _reset_events()
    diagnostic = (
        "smtp; 550 4.4.7 Message expired: unable to deliver in 840 "
        "minutes.<421 4.4.1 Failed to establish connection>"
    )
    pk = _make_event(
        event_type="bounce_transient",
        recipient_email="expired@test.com",
        bounce_type="Transient",
        diagnostic_code=diagnostic,
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/{pk}/", wait_until="domcontentloaded")

    raw = page.locator('[data-testid="ses-event-detail-diagnostic"]')
    assert "4.4.7" in raw.inner_text()
    assert "Message expired" in raw.inner_text()

    explain = page.locator('[data-testid="ses-event-detail-diagnostic-explain"]')
    assert explain.is_visible()
    explain_text = explain.inner_text().lower()
    assert "expired" in explain_text
    assert "connect" in explain_text
    context.close()


@pytest.mark.django_db(transaction=True)
def test_unknown_diagnostic_does_not_break_page(django_server, browser):
    _reset_events()
    pk = _make_event(
        event_type="bounce_transient",
        recipient_email="weird@test.com",
        bounce_type="Transient",
        diagnostic_code="smtp; 599 9.9.9 totally unknown failure",
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/{pk}/", wait_until="domcontentloaded")

    raw = page.locator('[data-testid="ses-event-detail-diagnostic"]')
    assert "9.9.9" in raw.inner_text()
    assert page.locator(
        '[data-testid="ses-event-detail-diagnostic-explain"]'
    ).count() == 0
    body = page.locator("body").inner_text().lower()
    assert "undefined" not in body
    assert "none\n" not in body  # crude guard against a leaked None
    context.close()


@pytest.mark.django_db(transaction=True)
def test_bounce_subtype_info_affordance_on_list_and_detail(django_server, browser):
    _reset_events()
    pk = _make_event(
        event_type="bounce_permanent",
        recipient_email="noemail@test.com",
        bounce_type="Permanent",
        bounce_subtype="NoEmail",
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/", wait_until="domcontentloaded")

    info = page.locator(f'[data-testid="ses-event-bounce-info-{pk}"]')
    assert info.is_visible()
    assert info.evaluate("el => el.tagName") == "BUTTON"
    accessible = (info.get_attribute("aria-label") or "").lower()
    assert "does not exist" in accessible
    assert "does not exist" in (info.get_attribute("title") or "").lower()

    page.goto(f"{django_server}/studio/ses-events/{pk}/", wait_until="domcontentloaded")
    detail_explain = page.locator(
        '[data-testid="ses-event-detail-bounce-subtype-explain"]'
    )
    assert "does not exist" in detail_explain.inner_text().lower()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_bounce_info_affordance_is_keyboard_focusable(django_server, browser):
    _reset_events()
    pk = _make_event(
        event_type="bounce_permanent",
        recipient_email="kbd@test.com",
        bounce_type="Permanent",
        bounce_subtype="NoEmail",
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/", wait_until="domcontentloaded")

    info = page.locator(f'[data-testid="ses-event-bounce-info-{pk}"]')
    info.focus()
    is_focused = info.evaluate("el => el === document.activeElement")
    assert is_focused
    # Explanation is in the DOM (title/aria-label), not injected on mouseover.
    assert (info.get_attribute("aria-label") or "").strip() != ""
    context.close()


@pytest.mark.django_db(transaction=True)
def test_complaint_flagged_serious(django_server, browser):
    _reset_events()
    pk = _make_event(
        event_type="complaint",
        recipient_email="spammer@test.com",
        bounce_subtype="abuse",
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/", wait_until="domcontentloaded")

    severity = page.locator(f'[data-testid="ses-event-severity-{pk}"]')
    assert severity.get_attribute("data-severity") == "high"

    page.goto(f"{django_server}/studio/ses-events/{pk}/", wait_until="domcontentloaded")
    consequence = page.locator('[data-testid="ses-event-detail-consequence"]')
    text = consequence.inner_text().lower()
    assert "unsubscribe" in text or "suppress" in text
    context.close()


@pytest.mark.django_db(transaction=True)
def test_delivery_event_is_informational(django_server, browser):
    _reset_events()
    pk = _make_event(
        event_type="delivery",
        recipient_email="delivered@test.com",
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/{pk}/", wait_until="domcontentloaded")

    detail_sev = page.locator('[data-testid="ses-event-detail-severity"]')
    assert detail_sev.get_attribute("data-severity") == "info"
    assert "Informational" in detail_sev.inner_text()
    # No alarming "unsubscribed" consequence in the badge itself.
    assert "unsubscribed" not in detail_sev.inner_text().lower()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_list_renders_event_without_bounce_fields(django_server, browser):
    _reset_events()
    pk = _make_event(
        event_type="open",
        recipient_email="opener@test.com",
    )
    context = _login(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/ses-events/", wait_until="domcontentloaded")

    row = page.locator(f'[data-testid="ses-event-row-{pk}"]')
    assert row.is_visible()
    # No bounce info affordance when there are no bounce fields.
    assert page.locator(f'[data-testid="ses-event-bounce-info-{pk}"]').count() == 0
    severity = page.locator(f'[data-testid="ses-event-severity-{pk}"]')
    assert severity.get_attribute("data-severity") == "info"
    assert "Informational" in severity.inner_text()
    context.close()
