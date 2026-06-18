"""Playwright coverage for checkout feedback banners (issue #266)."""

import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _seed_user(db_blocker, email):
    with db_blocker.unblock():
        create_user(email=email, tier_slug="free")


def _seed_pricing(db_blocker):
    with db_blocker.unblock():
        ensure_tiers()


def _expect_checkout_param_cleaned(page):
    page.wait_for_function(
        "() => !new URL(window.location.href).searchParams.has('checkout')"
    )
    assert "checkout=" not in page.url


def _expect_no_banners(page):
    expect(page.locator("#checkout-success-banner")).to_be_hidden()
    expect(page.locator("#checkout-cancelled-banner")).to_be_hidden()


def test_success_banner_shows_cleans_url_and_can_be_dismissed(
    django_server, browser, django_db_blocker
):
    email = _email("checkout-success")
    _seed_user(django_db_blocker, email)
    context = auth_context(browser, email)
    page = context.new_page()

    page.goto(f"{django_server}/?checkout=success", wait_until="domcontentloaded")

    banner = page.locator("#checkout-success-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text(
        "Payment successful! Welcome to AI Shipping Labs."
    )
    _expect_checkout_param_cleaned(page)

    page.locator("#dismiss-success-banner").click()
    expect(banner).to_be_hidden()

    context.close()


def test_cancelled_banner_shows_and_cleans_url(
    django_server, page, django_db_blocker
):
    _seed_pricing(django_db_blocker)

    page.goto(
        f"{django_server}/pricing?checkout=cancelled",
        wait_until="domcontentloaded",
    )

    banner = page.locator("#checkout-cancelled-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text(
        "Checkout was cancelled. You can try again anytime."
    )
    _expect_checkout_param_cleaned(page)


def test_no_banners_without_checkout_query_params(
    django_server, browser, page, django_db_blocker
):
    email = _email("checkout-none")
    _seed_user(django_db_blocker, email)
    context = auth_context(browser, email)
    dashboard_page = context.new_page()

    dashboard_page.goto(f"{django_server}/", wait_until="domcontentloaded")
    _expect_no_banners(dashboard_page)

    _seed_pricing(django_db_blocker)
    page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
    _expect_no_banners(page)

    context.close()
