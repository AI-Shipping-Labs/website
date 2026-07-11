"""Focused Playwright coverage for account-page polish (issue #1206)."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT, create_user
from playwright_tests.conftest import create_session_for_user as _create_session

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _auth_context(browser, email, *, timezone_id=None):
    session_key = _create_session(email)
    kwargs = {"viewport": VIEWPORT}
    if timezone_id:
        kwargs["timezone_id"] = timezone_id
    context = browser.new_context(**kwargs)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _create_newsletter_only_user(email):
    from django.db import connection

    from accounts.models import SIGNUP_SOURCE_NEWSLETTER, User
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    user, _ = User.objects.get_or_create(email=email)
    user.set_password(DEFAULT_PASSWORD)
    user.email_verified = True
    user.signup_source = SIGNUP_SOURCE_NEWSLETTER
    user.account_activated = False
    user.save()
    connection.close()


@pytest.mark.django_db(transaction=True)
def test_main_member_account_sections_and_api_empty_state(
    django_server, browser, settings,
):
    create_user("account-order-1206@test.com", tier_slug="main")
    settings.SLACK_INVITE_URL = "https://join.slack.com/t/test/shared_invite/1206"

    context = _auth_context(browser, "account-order-1206@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

    selectors = [
        '[data-lucide="crown"]',
        "#email-preferences-section",
        '[data-testid="slack-account-card"]',
        "#api-keys",
        "#display-preferences-section",
        "#change-password-section",
        "#profile-section",
        "#account-info-section",
    ]
    y_positions = [
        page.locator(selector).first.bounding_box()["y"] for selector in selectors
    ]
    assert y_positions == sorted(y_positions)

    empty = page.get_by_test_id("member-api-keys-empty")
    expect(empty).to_be_visible()
    assert empty.locator("a", has_text="API usage guide").count() == 0
    guide_link = page.get_by_test_id("member-api-usage-guide-link")
    expect(guide_link).to_have_count(1)
    expect(guide_link).to_have_attribute("href", "/member-api/docs")
    expect(page.get_by_test_id("member-api-skill-link")).to_be_visible()

    submit = page.get_by_test_id("member-api-key-create-submit")
    assert "whitespace-nowrap" in submit.get_attribute("class")
    page.get_by_test_id("member-api-key-name-input").fill("local codex")
    submit.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("member-api-key-plaintext")).to_be_visible()

    context.close()


@pytest.mark.django_db(transaction=True)
def test_newsletter_only_account_stays_trimmed_and_toggle_confirms(
    django_server, browser,
):
    _create_newsletter_only_user("newsletter-only-1206@test.com")

    context = _auth_context(browser, "newsletter-only-1206@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

    expect(page.get_by_test_id("newsletter-only-cta")).to_be_visible()
    expect(page.locator("#email-preferences-section")).to_be_visible()
    for selector in [
        "#tier-name",
        '[data-testid="slack-account-card"]',
        "#api-keys",
        "#display-preferences-section",
        "#change-password-section",
        "#profile-section",
        "#account-info-section",
    ]:
        expect(page.locator(selector)).to_have_count(0)

    status = page.locator("#newsletter-status")
    expect(status).to_be_hidden()
    page.locator("#newsletter-toggle").click()
    expect(status).to_have_text("Newsletter updates turned off.")

    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#newsletter-status")).to_be_hidden()
    assert "translate-x-5" not in (
        page.locator("#newsletter-toggle-dot").get_attribute("class") or ""
    )

    context.close()


@pytest.mark.django_db(transaction=True)
def test_email_preference_toggles_confirm_without_initial_duplicate_copy(
    django_server, browser,
):
    create_user("email-toggle-1206@test.com", tier_slug="free")

    context = _auth_context(browser, "email-toggle-1206@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

    body = page.content()
    assert "You are subscribed to newsletters." not in body
    assert "You will receive workshop announcement emails." not in body

    newsletter_status = page.locator("#newsletter-status")
    workshop_status = page.locator("#workshop-emails-status")
    expect(newsletter_status).to_be_hidden()
    expect(workshop_status).to_be_hidden()

    page.locator("#newsletter-toggle").click()
    expect(newsletter_status).to_have_text("Newsletter updates turned off.")

    page.locator("#workshop-emails-toggle").click()
    expect(workshop_status).to_have_text("Workshop announcements turned off.")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#workshop-emails-status")).to_be_hidden()
    assert "translate-x-5" not in (
        page.locator("#workshop-emails-toggle-dot").get_attribute("class") or ""
    )

    context.close()


@pytest.mark.django_db(transaction=True)
def test_timezone_save_and_clear_use_one_save_button(django_server, browser):
    create_user("timezone-1206@test.com", tier_slug="free")

    context = _auth_context(
        browser, "timezone-1206@test.com", timezone_id="Europe/Berlin"
    )
    page = context.new_page()
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

    expect(page.get_by_test_id("save-timezone-btn")).to_have_count(1)
    expect(page.get_by_test_id("clear-timezone-btn")).to_have_count(0)
    expect(page.get_by_test_id("account-timezone-input")).to_have_value(
        "Europe/Berlin"
    )

    page.get_by_test_id("account-timezone-input").select_option("America/New_York")
    page.get_by_test_id("save-timezone-btn").click()
    status = page.get_by_test_id("timezone-preference-status")
    expect(status).to_have_text("Timezone preference saved.")
    expect(status).not_to_contain_text("Saved timezone:")
    expect(status).not_to_contain_text("America/New_York")
    expect(status).not_to_contain_text("GMT-04:00")

    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("account-timezone-input")).to_have_value(
        "America/New_York"
    )
    status = page.get_by_test_id("timezone-preference-status")
    expect(status).to_have_text("Using saved timezone for event times.")
    expect(status).not_to_contain_text("Saved timezone:")
    expect(status).not_to_contain_text("America/New_York")
    expect(status).not_to_contain_text("GMT-04:00")

    page.get_by_test_id("account-timezone-input").select_option("")
    page.get_by_test_id("save-timezone-btn").click()
    expect(page.get_by_test_id("timezone-preference-status")).to_contain_text(
        "Using browser timezone."
    )
    expect(page.get_by_test_id("account-timezone-input")).to_have_value("")

    context.close()


@pytest.mark.django_db(transaction=True)
def test_account_info_shows_support_id_after_profile(django_server, browser):
    create_user("support-id-1206@test.com", tier_slug="free")

    context = _auth_context(browser, "support-id-1206@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

    account_info = page.locator("#account-info-section")
    expect(account_info).to_contain_text("Support ID")
    expect(account_info).to_contain_text("Quote this in support requests.")
    assert "User ID:" not in account_info.inner_text()
    assert (
        page.locator("#profile-section").bounding_box()["y"]
        < account_info.bounding_box()["y"]
    )

    context.close()
