"""Operator journeys for outbound email history (#1283)."""

import os
import re
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1283")


def _reset():
    from accounts.models import EmailAlias, User
    from email_app.models import EmailCampaign, EmailLog, SesEvent

    SesEvent.objects.all().delete()
    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    EmailAlias.objects.all().delete()
    User.objects.exclude(email="admin@test.com").delete()
    connection.close()


def _seed_history(*, bulk=False):
    from django.utils import timezone

    from accounts.models import EmailAlias, User
    from email_app.models import EmailCampaign, EmailLog, SesEvent

    member = User.objects.create_user(
        email="history-1283@example.com", password="pw", first_name="History",
    )
    other = User.objects.create_user(email="other-1283@example.com", password="pw")
    EmailAlias.objects.create(user=member, email="old-1283@example.com")
    campaign = EmailCampaign.objects.create(
        subject="1283 Campaign briefing", body="Body", status="sent",
    )
    primary = EmailLog.objects.create(
        user=member, recipient_email=member.email, email_type="welcome",
        subject="1283 Welcome", ses_message_id="pw-1283-sent",
    )
    alias = EmailLog.objects.create(
        recipient_email="OLD-1283@example.com", email_type="campaign",
        subject=campaign.subject, campaign=campaign, ses_message_id="pw-1283-alias",
        opens=1, clicks=1,
    )
    EmailLog.objects.create(
        user=member, recipient_email="historic-1283@elsewhere.test",
        email_type="account_notice", subject="", bounced_at=timezone.now(),
        complained_at=timezone.now(), ses_message_id="pw-1283-old",
    )
    external = EmailLog.objects.create(
        recipient_email="external-1283@example.net", email_type="external_notice",
        subject="", ses_message_id="pw-1283-external",
    )
    EmailLog.objects.create(
        user=other, recipient_email=other.email, email_type="welcome",
        subject="Other user", ses_message_id="pw-1283-other",
    )
    SesEvent.objects.create(
        event_type="delivery", message_id="pw-delivery-1283", raw_payload={},
        recipient_email=primary.recipient_email, user=member, email_log=primary,
    )
    SesEvent.objects.create(
        event_type="delivery", message_id="pw-alias-delivery-1283", raw_payload={},
        recipient_email="old-1283@example.com",
    )
    if bulk:
        for index in range(52):
            EmailLog.objects.create(
                recipient_email=f"bulk-1283-{index:02d}@example.com",
                email_type="bulk_1283", subject="Bulk accepted",
            )
    connection.close()
    return {"member": member.pk, "primary": primary.pk, "alias": alias.pk, "external": external.pk}


def _staff_page(browser):
    return _auth_context(browser, "admin@test.com").new_page()


def _dismiss_analytics(page):
    button = page.get_by_role("button", name="Keep analytics off")
    try:
        button.wait_for(state="visible", timeout=3_000)
    except PlaywrightTimeoutError:
        return
    else:
        button.click()
        expect(button).to_be_hidden()


def _expect_keyboard_focus(locator):
    """Rendered #1283 controls expose and accept the canonical focus ring."""
    expect(locator).to_have_attribute(
        "class", re.compile(r"\bfocus-visible:ring-2\b"),
    )
    locator.focus()
    expect(locator).to_be_focused()


@pytest.mark.django_db(transaction=True)
class TestEmailLog1283:
    @pytest.mark.core
    def test_support_scans_log_from_operations_and_opens_profile(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        ids = _seed_history()
        page = _staff_page(browser)
        page.goto(f"{django_server}/studio/email-log/", wait_until="domcontentloaded")

        expect(page.get_by_role("heading", name="Email log")).to_be_visible()
        expect(page.get_by_text("1283 Welcome", exact=True)).to_be_visible()
        expect(page.get_by_test_id("email-log-disposition").first).to_be_visible()
        nav_link = page.locator('a[aria-current="page"]', has_text="Email log")
        expect(nav_link).to_be_visible()
        _expect_keyboard_focus(nav_link)
        _expect_keyboard_focus(page.get_by_role("button", name="Apply filters"))
        _expect_keyboard_focus(
            page.locator(
                f'[data-testid="email-log-row-{ids["alias"]}"] '
                'a[href*="/campaigns/"]'
            )
        )
        recipient_link = page.locator(
            f'[data-testid="email-log-row-{ids["primary"]}"] '
            '[data-testid="email-log-recipient-link"]'
        )
        _expect_keyboard_focus(recipient_link)
        recipient_link.click()
        assert page.url.endswith(f"/studio/users/{ids['member']}/")

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.goto(f"{django_server}/studio/email-log/", wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Email log")).to_be_visible()
        expect(page.locator('[data-testid^="email-log-row-"]').first).to_be_visible()
        _dismiss_analytics(page)
        page.screenshot(path=SCREENSHOT_DIR / "email-log-desktop.png", full_page=True)

    @pytest.mark.core
    def test_investigate_address_clear_and_follow_canonical_history(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        ids = _seed_history()
        page = _staff_page(browser)
        page.goto(f"{django_server}/studio/email-log/?q=EXTERNAL-1283", wait_until="domcontentloaded")
        expect(page.get_by_text("external-1283@example.net", exact=True)).to_be_visible()
        expect(page.get_by_text("Other user", exact=True)).to_have_count(0)
        clear_filters = page.get_by_test_id("email-log-clear-filters")
        _expect_keyboard_focus(clear_filters)
        clear_filters.click()
        expect(page.get_by_text("Other user", exact=True)).to_be_visible()

        page.goto(f"{django_server}/studio/users/{ids['member']}/", wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Email history")).to_be_visible()
        history = page.get_by_test_id("user-email-history-section")
        _expect_keyboard_focus(history.locator('a[href*="/campaigns/"]'))
        _expect_keyboard_focus(page.get_by_test_id("user-email-history-ses-events"))
        view_all = page.get_by_test_id("user-email-history-view-all")
        _expect_keyboard_focus(view_all)
        view_all.click()
        expect(page.locator('[data-testid^="email-log-row-"]')).to_have_count(3)

    @pytest.mark.core
    def test_strongest_status_and_encoded_ses_action(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        ids = _seed_history()
        page = _staff_page(browser)
        page.goto(f"{django_server}/studio/email-log/", wait_until="domcontentloaded")
        expect(page.locator(f'[data-testid="email-log-row-{ids["alias"]}"]')).to_contain_text("Clicked")
        action = page.locator(f'[data-testid="email-log-row-{ids["external"]}"] [data-testid="email-log-ses-events-link"]')
        expect(action).to_have_attribute("href", "/studio/ses-events/?q=external-1283%40example.net")
        action.click()
        assert page.url.endswith(
            "/studio/ses-events/?q=external-1283%40example.net"
        )

    @pytest.mark.core
    def test_combined_filters_paginate_and_mobile_layout(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        _seed_history(bulk=True)
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(
            f"{django_server}/studio/email-log/?q=bulk-1283&kind=bulk_1283&status=sent&since=2020-01-01&until=2030-01-01",
            wait_until="domcontentloaded",
        )
        next_link = page.get_by_test_id("email-log-pager-next").first
        expect(next_link).to_be_visible()
        href = next_link.get_attribute("href")
        for value in ("q=bulk-1283", "kind=bulk_1283", "status=sent", "since=2020-01-01", "until=2030-01-01"):
            assert value in href
        next_link.click()
        expect(page.locator('[data-testid^="email-log-row-"]')).to_have_count(2)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        _dismiss_analytics(page)
        page.screenshot(path=SCREENSHOT_DIR / "email-log-mobile.png", full_page=True)

    @pytest.mark.core
    def test_empty_external_alias_ses_and_staff_boundary(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        ids = _seed_history()
        page = _staff_page(browser)
        page.goto(f"{django_server}/studio/email-log/?q=missing-1283", wait_until="domcontentloaded")
        expect(page.get_by_text("No emails match these filters.")).to_be_visible()
        expect(page.get_by_text("check Worker", exact=False)).to_be_visible()

        page.goto(f"{django_server}/studio/users/{ids['member']}/", wait_until="domcontentloaded")
        page.get_by_test_id("user-email-history-ses-events").click()
        user_filter = page.get_by_test_id("ses-event-user-filter")
        _expect_keyboard_focus(user_filter.locator("a").nth(0))
        _expect_keyboard_focus(user_filter.locator("a").nth(1))
        rows = page.locator('[data-testid^="ses-event-row-"]')
        expect(rows.filter(has_text="history-1283@example.com").first).to_be_visible()
        expect(rows.filter(has_text="old-1283@example.com").first).to_be_visible()

        from accounts.models import User

        no_history = User.objects.create_user(email="no-history-1283@example.com", password="pw")
        connection.close()
        page.goto(f"{django_server}/studio/users/{no_history.pk}/", wait_until="domcontentloaded")
        expect(page.get_by_test_id("user-email-history-empty")).to_be_visible()
        expect(page.get_by_test_id("user-email-history-view-all")).to_be_visible()
        expect(page.get_by_test_id("user-email-history-ses-events")).to_be_visible()

        User.objects.create_user(email="nonstaff-1283@example.com", password="pw")
        connection.close()
        nonstaff = _auth_context(browser, "nonstaff-1283@example.com").new_page()
        nonstaff.goto(f"{django_server}/studio/email-log/", wait_until="domcontentloaded")
        expect(nonstaff).not_to_have_url("**/studio/email-log/")
