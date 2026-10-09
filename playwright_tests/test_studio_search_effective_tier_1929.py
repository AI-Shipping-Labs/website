"""Studio search rows fit the sidebar and label the effective tier (#1929)."""

import csv
import datetime
import io
import os
import re

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only

STAFF_EMAIL = "studio-search-tier-1929@test.com"
LONG_EMAIL = (
    "averyveryveryverylongfirstname.lastname.2026@example-company-mail.com"
)
OVERRIDE_EMAIL = "override-member-1929@test.com"
MOBILE_VIEWPORT = {"width": 390, "height": 844}


def _expiry_label(expires_at):
    """Expected ``until`` text, derived from the generated fixture date."""
    expires_at = expires_at.astimezone(datetime.timezone.utc)
    label = f"{expires_at:%b} {expires_at.day}"
    if expires_at.year != timezone.now().astimezone(datetime.timezone.utc).year:
        label = f"{label}, {expires_at.year}"
    return label


def _grant(email, tier_slug, expires_at, *, is_active=True, source="",
           created_at=None):
    from accounts.models import User
    from payments.models import Tier, TierOverride

    override = TierOverride.objects.create(
        user=User.objects.get(email=email),
        override_tier=Tier.objects.get(slug=tier_slug),
        expires_at=expires_at,
        is_active=is_active,
        source=source,
    )
    if created_at is not None:
        TierOverride.objects.filter(pk=override.pk).update(created_at=created_at)
    connection.close()
    return override


def _reset():
    from payments.models import TierOverride

    TierOverride.objects.all().delete()
    connection.close()


def _search_user_row(page, query, email):
    page.get_by_test_id("studio-global-search-input").fill(query)
    results = page.get_by_test_id("studio-global-search-results")
    option = results.get_by_role("option", name=re.compile(re.escape(email)))
    expect(option).to_be_visible()
    return results, option


def _metadata(option):
    return option.locator("[data-studio-search-result-metadata]")


def _summary(option):
    return option.locator("[data-studio-search-result-summary]")


def _assert_fully_readable(results, option, email):
    summary = _summary(option)
    expect(summary).to_have_text(email)
    expect(summary).to_have_attribute("title", email)
    # Wrapped, not ellipsised: the span is no wider than its box and spans
    # several lines, and the panel itself never scrolls sideways.
    assert summary.evaluate("el => el.scrollWidth <= el.clientWidth")
    assert summary.evaluate(
        "el => el.getBoundingClientRect().height > "
        "parseFloat(getComputedStyle(el).lineHeight) * 1.5"
    )
    assert results.evaluate("el => el.scrollWidth <= el.clientWidth")
    metadata = _metadata(option)
    assert metadata.evaluate("el => el.scrollWidth <= el.clientWidth")
    expect(metadata).to_have_attribute("title", metadata.inner_text())
    label = option.locator("[data-studio-search-result-label]")
    expect(label).to_have_attribute("title", label.inner_text())


def _assert_panel_inside_sidebar(page, results):
    panel = results.bounding_box()
    sidebar = page.locator("#studio-sidebar").bounding_box()
    assert panel["x"] >= sidebar["x"]
    assert panel["x"] + panel["width"] <= sidebar["x"] + sidebar["width"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
@browser_journey
def test_long_email_wraps_inside_sidebar_and_keyboard_opens_user(
    django_server, browser,
):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    member = _create_user(LONG_EMAIL, first_name="Avery")

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    results, option = _search_user_row(page, "averyveryvery", LONG_EMAIL)
    _assert_fully_readable(results, option, LONG_EMAIL)
    _assert_panel_inside_sidebar(page, results)
    expect(_metadata(option)).to_have_text("Free · verified · No bounce")

    search = page.get_by_test_id("studio-global-search-input")
    search.press("ArrowDown")
    search.press("Enter")
    page.wait_for_url(f"{django_server}/studio/users/{member.pk}/")
    context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
@browser_journey
def test_active_override_label_matches_user_detail_pill(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    member = _create_user(OVERRIDE_EMAIL)
    expires_at = timezone.now() + datetime.timedelta(days=21)
    _grant(OVERRIDE_EMAIL, "main", expires_at)

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    _, option = _search_user_row(page, OVERRIDE_EMAIL, OVERRIDE_EMAIL)
    expect(_metadata(option)).to_have_text(
        f"Main (override until {_expiry_label(expires_at)}) · verified · No bounce"
    )

    option.click()
    page.wait_for_url(f"{django_server}/studio/users/{member.pk}/")
    expect(page.get_by_test_id("user-detail-tier-pill")).to_have_text("Main")
    expect(page.get_by_test_id("user-detail-tier-badge")).to_have_attribute(
        "data-tier-source", "override",
    )
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_lapsed_override_shows_base_tier(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    _create_user("lapsed-member-1929@test.com")
    _grant(
        "lapsed-member-1929@test.com", "main",
        timezone.now() - datetime.timedelta(days=1), is_active=True,
    )

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    _, option = _search_user_row(
        page, "lapsed-member-1929", "lapsed-member-1929@test.com",
    )
    expect(_metadata(option)).to_have_text("Free · verified · No bounce")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_member_without_override_shows_subscription_tier(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    _create_user(
        "basic-member-1929@test.com", tier_slug="basic", first_name="Basicora",
    )

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    _, option = _search_user_row(page, "Basicora", "basic-member-1929@test.com")
    expect(_metadata(option)).to_have_text("Basic · verified · No bounce")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_phone_sized_drawer_search_wraps_long_email(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    member = _create_user(LONG_EMAIL, first_name="Avery")

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    page.locator("#studio-sidebar-toggle").click()
    expect(page.locator("#studio-sidebar")).to_be_visible()

    results, option = _search_user_row(page, LONG_EMAIL, LONG_EMAIL)
    _assert_fully_readable(results, option, LONG_EMAIL)
    _assert_panel_inside_sidebar(page, results)

    option.click()
    page.wait_for_url(f"{django_server}/studio/users/{member.pk}/")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_quick_jump_shows_override_label_and_enter_opens_user(
    django_server, browser,
):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    member = _create_user(OVERRIDE_EMAIL)
    expires_at = timezone.now() + datetime.timedelta(days=40)
    _grant(OVERRIDE_EMAIL, "main", expires_at)

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    page.keyboard.press("Control+k")
    expect(page.get_by_test_id("studio-quick-jump")).to_be_visible()

    search = page.get_by_test_id("studio-quick-jump-input")
    search.fill(OVERRIDE_EMAIL)
    option = page.get_by_test_id("studio-quick-jump-results").get_by_role(
        "option", name=re.compile(re.escape(OVERRIDE_EMAIL)),
    )
    expect(_metadata(option)).to_have_text(
        f"Main (override until {_expiry_label(expires_at)}) · verified · No bounce"
    )

    search.press("Enter")
    page.wait_for_url(f"{django_server}/studio/users/{member.pk}/")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_event_roster_and_csv_show_effective_tier(django_server, browser):
    from events.models import Event, EventRegistration

    _reset()
    _create_staff_user(STAFF_EMAIL)
    overridden = _create_user("roster-override-1929@test.com")
    basic = _create_user("roster-basic-1929@test.com", tier_slug="basic")
    expires_at = timezone.now() + datetime.timedelta(days=21)
    _grant("roster-override-1929@test.com", "main", expires_at)
    Event.objects.filter(slug="tier-roster-1929").delete()
    event = Event.objects.create(
        title="Tier roster 1929",
        slug="tier-roster-1929",
        status="upcoming",
        start_datetime=timezone.now() + datetime.timedelta(days=5),
    )
    EventRegistration.objects.create(event=event, user=overridden)
    EventRegistration.objects.create(event=event, user=basic)
    connection.close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/events/{event.pk}/edit",
        wait_until="domcontentloaded",
    )
    rows = page.get_by_test_id("registration-row")
    overridden_row = rows.filter(has_text="roster-override-1929@test.com")
    basic_row = rows.filter(has_text="roster-basic-1929@test.com")
    overridden_row.scroll_into_view_if_needed()
    expect(overridden_row.get_by_test_id("registration-tier")).to_have_text(
        f"Main (override until {_expiry_label(expires_at)})",
    )
    expect(basic_row.get_by_test_id("registration-tier")).to_have_text("Basic")

    with page.expect_download() as download_info:
        page.get_by_test_id("registrations-download-csv").click()
    with open(download_info.value.path(), encoding="utf-8") as handle:
        rows_csv = list(csv.reader(io.StringIO(handle.read())))
    assert rows_csv[0] == ["email", "name", "registered_at", "tier", "joined_at"]
    assert {row[0]: row[3] for row in rows_csv[1:]} == {
        "roster-override-1929@test.com": "Main",
        "roster-basic-1929@test.com": "Basic",
    }
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_coexisting_grants_show_strongest_tier_across_pages(
    django_server, browser,
):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    member = _create_user("maven-staff-1929@test.com")
    now = timezone.now()
    premium_expires = now + datetime.timedelta(days=30)
    _grant(
        "maven-staff-1929@test.com", "premium", premium_expires,
        created_at=now - datetime.timedelta(days=14),
    )
    _grant(
        "maven-staff-1929@test.com", "basic", now + datetime.timedelta(days=90),
        source="maven:cohort-1929", created_at=now - datetime.timedelta(days=7),
    )

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    _, option = _search_user_row(
        page, "maven-staff-1929", "maven-staff-1929@test.com",
    )
    expect(_metadata(option)).to_have_text(
        f"Premium (override until {_expiry_label(premium_expires)}) "
        "· verified · No bounce"
    )
    option.click()
    page.wait_for_url(f"{django_server}/studio/users/{member.pk}/")
    expect(page.get_by_test_id("user-detail-tier-pill")).to_have_text("Premium")

    page.goto(
        f"{django_server}/studio/users/?q=maven-staff-1929%40test.com",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("user-list-tier-pill")).to_have_text("Premium")
    context.close()
