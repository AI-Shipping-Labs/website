"""Encoded Studio Users and CRM list navigation (issue #1541)."""

import csv
import os
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.core, pytest.mark.local_only]

SEARCH = "Ada & Bob +1"
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1541")


def _query(page):
    return parse_qs(urlsplit(page.url).query, keep_blank_values=True)


def _clear_users_except(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _update_user(email, **fields):
    from accounts.models import User

    User.objects.filter(email=email).update(**fields)
    connection.close()


def _set_tags(email, tags):
    from accounts.models import User
    from accounts.utils.tags import set_tags

    set_tags(User.objects.get(email=email), tags)
    connection.close()


def _create_crm(email, *, persona, status="active"):
    from accounts.models import User
    from crm.models import CRMRecord

    record = CRMRecord.objects.create(
        user=User.objects.get(email=email),
        persona=persona,
        status=status,
    )
    connection.close()
    return record.pk


def _open_staff_page(django_server, browser, staff_email, path, params):
    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.goto(
        f"{django_server}{path}?{urlencode(params)}",
        wait_until="domcontentloaded",
    )
    return context, page


def _assert_exact_search(page):
    assert _query(page)["q"] == [SEARCH]


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_users_spaced_search_survives_tier_chip(django_server, browser):
    _ensure_tiers()
    staff_email = "query-tier-staff@test.com"
    _create_staff_user(staff_email)
    _clear_users_except(staff_email)
    _create_user("query-tier-member@test.com", tier_slug="main", first_name=SEARCH)
    _update_user("query-tier-member@test.com", subscription_id="sub_query_tier")

    context, page = _open_staff_page(
        django_server,
        browser,
        staff_email,
        "/studio/users/",
        {"q": SEARCH, "page": "7"},
    )
    page.locator('[data-filter="paid"]').click()
    page.wait_for_load_state("domcontentloaded")

    _assert_exact_search(page)
    assert "page" not in _query(page)
    expect(page.get_by_text("query-tier-member@test.com", exact=True)).to_be_visible()
    _shot(page, "users-special-character-search")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_users_plus_ampersand_search_survives_slack_then_bounce(
    django_server,
    browser,
):
    _ensure_tiers()
    staff_email = "query-slack-staff@test.com"
    _create_staff_user(staff_email)
    _clear_users_except(staff_email)
    _create_user("query-slack-member@test.com", first_name=SEARCH)
    _update_user(
        "query-slack-member@test.com",
        slack_member=False,
        bounce_state="none",
    )

    context, page = _open_staff_page(
        django_server,
        browser,
        staff_email,
        "/studio/users/",
        {"q": SEARCH, "page": "3"},
    )
    page.locator('[data-slack-filter="no"]').click()
    page.wait_for_load_state("domcontentloaded")
    _assert_exact_search(page)
    assert "page" not in _query(page)

    page.locator('[data-bounce-filter="none"]').click()
    page.wait_for_load_state("domcontentloaded")
    _assert_exact_search(page)
    expect(page.get_by_text("query-slack-member@test.com", exact=True)).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_users_csv_export_preserves_encoded_search_and_tag(django_server, browser):
    _ensure_tiers()
    staff_email = "query-export-staff@test.com"
    member_email = "query-export-member@test.com"
    _create_staff_user(staff_email)
    _clear_users_except(staff_email)
    _create_user(member_email, first_name=SEARCH)
    _set_tags(member_email, ["r-d-alumni"])

    context, page = _open_staff_page(
        django_server,
        browser,
        staff_email,
        "/studio/users/",
        {"q": SEARCH, "tag": "r-d-alumni", "sort": "joined", "page": "5"},
    )
    export_link = page.get_by_role("link", name="Export CSV")
    export_query = parse_qs(urlsplit(export_link.get_attribute("href")).query)
    assert export_query["q"] == [SEARCH]
    assert export_query["tag"] == ["r-d-alumni"]
    assert export_query["sort"] == ["joined"]
    assert "page" not in export_query

    with page.expect_download() as download_info:
        export_link.click()
    rows = list(csv.DictReader(download_info.value.path().read_text().splitlines()))
    assert [row["email"] for row in rows] == [member_email]
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_users_tag_clear_preserves_encoded_search_and_drops_page(
    django_server,
    browser,
):
    _ensure_tiers()
    staff_email = "query-tag-staff@test.com"
    member_email = "query-tag-member@test.com"
    _create_staff_user(staff_email)
    _clear_users_except(staff_email)
    _create_user(member_email, first_name=SEARCH)
    _set_tags(member_email, ["priority"])

    context, page = _open_staff_page(
        django_server,
        browser,
        staff_email,
        "/studio/users/",
        {"q": SEARCH, "tag": "priority", "page": "9"},
    )
    page.get_by_test_id("active-tag-clear").click()
    page.wait_for_load_state("domcontentloaded")

    _assert_exact_search(page)
    assert "tag" not in _query(page)
    assert "page" not in _query(page)
    expect(page.get_by_text(member_email, exact=True)).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_crm_ampersand_search_survives_status_chip(django_server, browser):
    _ensure_tiers()
    staff_email = "query-crm-status-staff@test.com"
    member_email = "query-crm-status-member@test.com"
    _create_staff_user(staff_email)
    _clear_users_except(staff_email)
    _create_user(member_email)
    _create_crm(member_email, persona=SEARCH, status="archived")

    context, page = _open_staff_page(
        django_server,
        browser,
        staff_email,
        "/studio/crm/",
        {"filter": "all", "q": SEARCH, "page": "4"},
    )
    page.get_by_test_id("crm-filter-archived").click()
    page.wait_for_load_state("domcontentloaded")

    _assert_exact_search(page)
    assert _query(page)["filter"] == ["archived"]
    assert "page" not in _query(page)
    expect(page.get_by_text(member_email, exact=True)).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_crm_spaced_search_survives_pager(django_server, browser):
    _ensure_tiers()
    staff_email = "query-crm-pager-staff@test.com"
    _create_staff_user(staff_email)
    _clear_users_except(staff_email)
    for index in range(3):
        email = f"query-crm-pager-{index}@test.com"
        _create_user(email)
        _create_crm(email, persona=f"{SEARCH} cohort {index}")

    with mock.patch("studio.views.crm.CRM_LIST_PAGE_SIZE", 2):
        context, page = _open_staff_page(
            django_server,
            browser,
            staff_email,
            "/studio/crm/",
            {"filter": "active", "q": SEARCH},
        )
        page.get_by_role("link", name="Next", exact=True).click()
        page.wait_for_load_state("domcontentloaded")

        _assert_exact_search(page)
        assert _query(page)["page"] == ["2"]
        _shot(page, "crm-special-character-pager")
        context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_lifecycle_chips_preserve_encoded_search_on_users_and_crm(
    django_server,
    browser,
):
    _ensure_tiers()
    staff_email = "query-lifecycle-staff@test.com"
    member_email = "query-lifecycle-member@test.com"
    _create_staff_user(staff_email)
    _clear_users_except(staff_email)
    _create_user(member_email, first_name=SEARCH)
    _update_user(member_email, signup_source="signup", account_activated=True)
    _create_crm(member_email, persona=SEARCH)

    context, page = _open_staff_page(
        django_server,
        browser,
        staff_email,
        "/studio/users/",
        {"q": SEARCH, "page": "6"},
    )
    page.locator('[data-lifecycle-filter="full_account"]').click()
    page.wait_for_load_state("domcontentloaded")
    _assert_exact_search(page)
    assert "page" not in _query(page)
    expect(page.get_by_text(member_email, exact=True)).to_be_visible()

    page.goto(
        f"{django_server}/studio/crm/?{urlencode({'q': SEARCH, 'page': '8'})}",
        wait_until="domcontentloaded",
    )
    page.locator('[data-lifecycle-filter="full_account"]').click()
    page.wait_for_load_state("domcontentloaded")
    _assert_exact_search(page)
    assert "page" not in _query(page)
    expect(page.get_by_text(member_email, exact=True)).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_filtered_lists_keep_anonymous_redirect_and_nonstaff_forbidden(
    django_server,
    browser,
):
    _ensure_tiers()
    member_email = "query-auth-member@test.com"
    _create_user(member_email)
    query = urlencode({"q": SEARCH, "tag": "R&D + members", "page": "2"})

    anonymous = browser.new_context()
    anonymous_page = anonymous.new_page()
    for path in ("/studio/users/", "/studio/crm/"):
        response = anonymous_page.goto(
            f"{django_server}{path}?{query}",
            wait_until="domcontentloaded",
        )
        assert response.status in (200, 302)
        assert "/accounts/login/" in anonymous_page.url
        next_value = parse_qs(urlsplit(anonymous_page.url).query)["next"][0]
        assert urlsplit(next_value).path == path

    member = _auth_context(browser, member_email)
    member_page = member.new_page()
    for path in ("/studio/users/", "/studio/crm/"):
        response = member_page.goto(
            f"{django_server}{path}?{query}",
            wait_until="domcontentloaded",
        )
        assert response.status == 403

    anonymous.close()
    member.close()
