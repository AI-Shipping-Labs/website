"""Accessible account email-preference switch journeys for issue #1530."""

import os
import re

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_tiers,
    goto_with_retry,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.core]


def _member(email, *, workshop_emails=True):
    ensure_tiers()
    create_user(
        email,
        tier_slug="free",
        email_verified=True,
    )
    from accounts.models import User

    user = User.objects.get(email=email)
    if not workshop_emails:
        user.email_preferences = {"workshop_emails": False}
        user.save(update_fields=["email_preferences"])
    return user


@browser_journey
def test_member_reads_toggles_and_persists_mouse_and_keyboard_changes(
    browser, django_server,
):
    user = _member("preference-switches-1530@test.com", workshop_emails=False)
    context = auth_context(browser, user.email)
    page = context.new_page()
    goto_with_retry(page, f"{django_server}/account/")
    section = page.locator("#email-preferences-section")

    expected_initial = {
        "Toggle newsletter subscription": "true",
        "Toggle workshop announcement emails": "false",
        "Toggle sprint reminder emails": "true",
        "Toggle Book Club summary emails": "true",
    }
    expect(section.get_by_role("switch")).to_have_count(4)
    for name, checked in expected_initial.items():
        expect(section.get_by_role("switch", name=name)).to_have_attribute(
            "aria-checked", checked,
        )

    actions = (
        (
            "Toggle newsletter subscription",
            "newsletter-status",
            "Newsletter updates turned off.",
            "false",
            "mouse",
        ),
        (
            "Toggle workshop announcement emails",
            "workshop-emails-status",
            "Workshop announcements turned on.",
            "true",
            "mouse",
        ),
        (
            "Toggle sprint reminder emails",
            "sprint-cadence-emails-status",
            "Sprint reminders turned off.",
            "false",
            "keyboard",
        ),
        (
            "Toggle Book Club summary emails",
            "bookclub-emails-status",
            "Book Club summary emails turned off.",
            "false",
            "mouse",
        ),
    )
    for name, status_id, status_copy, checked, activation in actions:
        toggle = section.get_by_role("switch", name=name)
        if activation == "keyboard":
            toggle.focus()
            page.keyboard.press("Space")
        else:
            toggle.click()
        expect(section.locator(f"#{status_id}")).to_have_text(status_copy)
        expect(toggle).to_have_attribute("aria-checked", checked)
        expect(toggle).not_to_have_attribute("aria-pressed", re.compile(".+"))

    page.reload(wait_until="domcontentloaded")
    section = page.locator("#email-preferences-section")
    for name, _status_id, _copy, checked, _activation in actions:
        expect(section.get_by_role("switch", name=name)).to_have_attribute(
            "aria-checked", checked,
        )
    context.close()


@browser_journey
def test_failed_and_network_saves_leave_accessible_and_visual_state_unchanged(
    browser, django_server,
):
    user = _member("preference-failure-1530@test.com")
    context = auth_context(browser, user.email)
    page = context.new_page()
    attempts = {"count": 0}

    def _fail_save(route):
        attempts["count"] += 1
        if attempts["count"] == 1:
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error": "Preference service unavailable"}',
            )
            return
        route.abort()

    page.route("**/account/api/email-preferences", _fail_save)
    goto_with_retry(page, f"{django_server}/account/")
    section = page.locator("#email-preferences-section")
    toggle = section.get_by_role("switch", name="Toggle newsletter subscription")
    dot = section.locator("#newsletter-toggle-dot")
    status = section.locator("#newsletter-status")

    expect(toggle).to_have_attribute("aria-checked", "true")
    expect(toggle).to_have_class(re.compile(r"\bbg-accent\b"))
    expect(dot).to_have_class(re.compile(r"\btranslate-x-5\b"))

    for expected_alert in (
        "Preference service unavailable",
        "An error occurred. Please try again.",
    ):
        with page.expect_event("dialog") as dialog_info:
            toggle.evaluate("element => element.click()")
        dialog = dialog_info.value
        assert dialog.message == expected_alert
        dialog.dismiss()
        expect(toggle).to_have_attribute("aria-checked", "true")
        expect(toggle).to_have_class(re.compile(r"\bbg-accent\b"))
        expect(dot).to_have_class(re.compile(r"\btranslate-x-5\b"))
        expect(status).to_be_hidden()

    assert attempts["count"] == 2
    context.close()
