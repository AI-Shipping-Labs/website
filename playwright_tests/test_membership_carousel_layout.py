"""Issue #482 membership/pricing card carousel layout coverage."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_site_config_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path("playwright_tests/screenshots/issue-482")
MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}

PRICING_TIERS = [
    {
        "slug": "free",
        "name": "Free",
        "level": 0,
        "price_eur_month": None,
        "price_eur_year": None,
        "description": "Subscribe to the newsletter and access open content.",
        "features": ["Newsletter emails", "Access to open content"],
    },
    {
        "slug": "basic",
        "name": "Basic",
        "level": 10,
        "price_eur_month": 20,
        "price_eur_year": 200,
        "description": "Access curated educational content, tutorials, and research.",
        "features": ["Exclusive articles", "Tutorials with code examples"],
    },
    {
        "slug": "main",
        "name": "Main",
        "level": 20,
        "price_eur_month": 50,
        "price_eur_year": 500,
        "description": "Everything in Basic, plus structure and peer support.",
        "features": ["Everything in Basic", "Slack community access"],
    },
    {
        "slug": "premium",
        "name": "Premium",
        "level": 30,
        "price_eur_month": 100,
        "price_eur_year": 1000,
        "description": "Everything in Main, plus courses and personalized feedback.",
        "features": ["Everything in Main", "All mini-courses"],
    },
]


def _ensure_pricing_tiers():
    from django.db import connection

    from payments.models import Tier

    for tier in PRICING_TIERS:
        Tier.objects.update_or_create(slug=tier["slug"], defaults=tier)
    connection.close()


def _assert_no_body_overflow(page):
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1


def _assert_mobile_carousel(page, selector):
    carousel = page.locator(selector)
    expect(carousel).to_be_visible()
    assert carousel.evaluate("el => getComputedStyle(el).display") == "flex"
    assert carousel.evaluate("el => el.scrollWidth > el.clientWidth")
    assert carousel.evaluate("el => getComputedStyle(el).scrollSnapType").startswith(
        "x"
    )
    _assert_no_body_overflow(page)


def _assert_main_centered(page, selector):
    page.wait_for_timeout(150)
    carousel = page.locator(selector)
    delta = carousel.evaluate(
        """el => {
          const main = el.querySelector('[data-tier-card="main"]');
          const elRect = el.getBoundingClientRect();
          const mainRect = main.getBoundingClientRect();
          return Math.abs(
            (mainRect.left + mainRect.width / 2) - (elRect.left + elRect.width / 2)
          );
        }"""
    )
    assert delta < 24


def _screenshot(page, selector, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.add_style_tag(content="header, #section-nav { visibility: hidden !important; }")
    page.locator(selector).screenshot(path=SCREENSHOT_DIR / f"{name}.png")


@pytest.mark.django_db(transaction=True)
def test_home_membership_mobile_carousel_and_desktop_grid(django_server, page):
    ensure_site_config_tiers()

    page.set_viewport_size(MOBILE)
    page.goto(f"{django_server}/", wait_until="networkidle")
    _assert_mobile_carousel(page, '[data-testid="home-tier-carousel"]')
    _assert_main_centered(page, '[data-testid="home-tier-carousel"]')
    _screenshot(page, '[data-testid="home-tier-carousel"]', "home-mobile-tiers")

    page.set_viewport_size(DESKTOP)
    page.goto(f"{django_server}/", wait_until="networkidle")
    carousel = page.locator('[data-testid="home-tier-carousel"]')
    expect(carousel).to_be_visible()
    assert carousel.evaluate("el => getComputedStyle(el).display") == "grid"
    assert page.locator('[data-testid="home-tier-card"]').count() == 3
    _assert_no_body_overflow(page)
    _screenshot(page, '[data-testid="home-tier-carousel"]', "home-desktop-tiers")


@pytest.mark.django_db(transaction=True)
def test_pricing_mobile_carousel_free_copy_and_desktop_grid(django_server, page):
    _ensure_pricing_tiers()

    page.set_viewport_size(MOBILE)
    page.goto(f"{django_server}/pricing", wait_until="networkidle")
    _assert_mobile_carousel(page, '[data-testid="pricing-tier-carousel"]')
    _assert_main_centered(page, '[data-testid="pricing-tier-carousel"]')
    free_card = page.locator('[data-tier-card="free"]')
    expect(free_card).to_contain_text("Newsletter and open resources")
    expect(free_card.get_by_role("link", name="Get the newsletter")).to_have_attribute(
        "href", "/#newsletter"
    )
    _screenshot(page, '[data-testid="pricing-tier-carousel"]', "pricing-mobile-anon")

    page.set_viewport_size(DESKTOP)
    page.goto(f"{django_server}/pricing", wait_until="networkidle")
    carousel = page.locator('[data-testid="pricing-tier-carousel"]')
    expect(carousel).to_be_visible()
    assert carousel.evaluate("el => getComputedStyle(el).display") == "grid"
    assert page.locator('[data-testid="pricing-tier-card"]').count() == 4
    _assert_no_body_overflow(page)
    _screenshot(page, '[data-testid="pricing-tier-carousel"]', "pricing-desktop")


@pytest.mark.django_db(transaction=True)
def test_pricing_mobile_carousel_preserves_current_plan_state(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        _ensure_pricing_tiers()
        user = create_user("issue-482-main@test.com", tier_slug="main")
        user.subscription_id = "sub_issue_482_main"
        user.save(update_fields=["subscription_id"])

    context = auth_context(browser, "issue-482-main@test.com")
    page = context.new_page()
    page.set_viewport_size(MOBILE)
    try:
        page.goto(f"{django_server}/pricing", wait_until="networkidle")
        _assert_mobile_carousel(page, '[data-testid="pricing-tier-carousel"]')
        _assert_main_centered(page, '[data-testid="pricing-tier-carousel"]')
        expect(page.locator('[data-tier-card="main"]')).to_contain_text("Current plan")
        expect(page.locator('[data-tier-card="basic"]')).to_contain_text("Downgrade")
        expect(page.locator('[data-tier-card="premium"]')).to_contain_text("Upgrade")
        _screenshot(page, '[data-testid="pricing-tier-carousel"]', "pricing-mobile-member")
    finally:
        context.close()
