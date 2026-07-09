"""Issue #1188 pricing layout and mobile carousel coverage."""

import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    SETTLE_TIMEOUT_MS,
    auth_context,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}

PRICING_TIERS = [
    {
        "slug": "free",
        "name": "Free",
        "level": 0,
        "price_eur_month": None,
        "price_eur_year": None,
        "description": "Newsletter and open resources.",
        "features": [
            "Newsletter emails",
            "Access to open content",
            "Community updates",
            "Public resources",
        ],
    },
    {
        "slug": "basic",
        "name": "Basic",
        "level": 10,
        "price_eur_month": 20,
        "price_eur_year": 200,
        "description": "Curated educational content.",
        "features": [
            "Exclusive articles",
            "Tutorials with code examples",
            "Research notes",
            "Curated links",
        ],
    },
    {
        "slug": "main",
        "name": "Main",
        "level": 20,
        "price_eur_month": 50,
        "price_eur_year": 500,
        "description": "Structure and peer support.",
        "features": [
            "Everything in Basic",
            "Slack community access",
            "Group coding sessions",
            "Project-based learning",
            "Community hackathons",
        ],
    },
    {
        "slug": "premium",
        "name": "Premium",
        "level": 30,
        "price_eur_month": 100,
        "price_eur_year": 1000,
        "description": "Courses and personalized feedback.",
        "features": [
            "Everything in Main",
            "All mini-courses",
            "Profile teardowns",
            "Career feedback",
        ],
    },
]


def _new_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


def _seed_pricing(oauth=True):
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    from django.db import connection

    from payments.models import Tier

    for tier in PRICING_TIERS:
        Tier.objects.update_or_create(slug=tier["slug"], defaults=tier)

    SocialApp.objects.all().delete()
    if oauth:
        site = Site.objects.get_current()
        app = SocialApp.objects.create(
            provider="google",
            name="Google",
            client_id="google-cid",
            secret="google-secret",
        )
        app.sites.add(site)

    connection.close()


def _card_metrics(page, slug):
    return page.evaluate(
        """slug => {
          const card = document.querySelector(`[data-tier-card="${slug}"]`);
          if (!card) return null;
          const cta = card.querySelector('.tier-cta-link, [data-action], #register-submit');
          const features = card.querySelector('ul');
          const rect = card.getBoundingClientRect();
          const ctaRect = cta ? cta.getBoundingClientRect() : null;
          const featuresRect = features ? features.getBoundingClientRect() : null;
          return {
            top: rect.top,
            bottom: rect.bottom,
            height: rect.height,
            ctaTop: ctaRect ? ctaRect.top : null,
            featuresBottom: featuresRect ? featuresRect.bottom : null,
          };
        }""",
        slug,
    )


def _main_center_delta(page):
    return page.evaluate(
        """() => {
          const carousel = document.querySelector('[data-testid="pricing-tier-carousel"]');
          const main = carousel && carousel.querySelector('[data-tier-card="main"]');
          if (!carousel || !main) return null;
          const cr = carousel.getBoundingClientRect();
          const mr = main.getBoundingClientRect();
          return Math.abs((mr.left + mr.width / 2) - (cr.left + cr.width / 2));
        }"""
    )


def _body_overflow(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


@pytest.mark.django_db(transaction=True)
def test_pricing_desktop_cards_keep_intrinsic_heights_after_email_expand(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)

    page.set_viewport_size(DESKTOP)
    page.goto(f"{django_server}/pricing", wait_until="networkidle")
    free_card = page.locator('[data-tier-card="free"]')
    expect(free_card.get_by_role("link", name="Sign up with Google")).to_be_visible()
    assert free_card.locator("#register-email").is_visible() is False

    before = {slug: _card_metrics(page, slug) for slug in ("free", "basic", "main", "premium")}
    free_card.locator('[data-testid="inline-register-email-toggle"]').click()
    free_card.locator("#register-email").wait_for(state="visible")
    after = {slug: _card_metrics(page, slug) for slug in ("free", "basic", "main", "premium")}

    assert after["free"]["height"] > before["free"]["height"]
    for slug in ("basic", "main", "premium"):
        assert abs(after[slug]["height"] - before[slug]["height"]) <= 2
        assert abs(after[slug]["top"] - after["free"]["top"]) <= 2
        gap = after[slug]["ctaTop"] - after[slug]["featuresBottom"]
        assert gap < 80, f"{slug} CTA has a large blank gap: {gap}px"


@pytest.mark.django_db(transaction=True)
def test_pricing_mobile_indicator_controls_scroll_without_overflow(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)

    page.set_viewport_size(MOBILE)
    page.goto(f"{django_server}/pricing", wait_until="networkidle")
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"pricing-tier-carousel\"]').scrollLeft > 0",
        timeout=SETTLE_TIMEOUT_MS,
    )

    assert _main_center_delta(page) < 60
    main = _card_metrics(page, "main")
    carousel_height = page.locator(
        '[data-testid="pricing-tier-carousel"]'
    ).evaluate("el => el.getBoundingClientRect().height")
    assert carousel_height <= main["height"] + 140
    assert _body_overflow(page) <= 1

    indicators = page.locator('[data-testid="pricing-tier-indicator"]')
    assert indicators.count() == 4
    for tier in ("Free", "Basic", "Main", "Premium"):
        expect(
            page.get_by_role("button", name=f"Show {tier} tier")
        ).to_be_visible()
    assert (
        page.get_by_role("button", name="Show Main tier").get_attribute("aria-current")
        == "true"
    )

    page.get_by_role("button", name="Show Free tier").click()
    page.wait_for_function(
        "() => document.querySelector('[data-tier-card=\"free\"]').getBoundingClientRect().left >= 0",
        timeout=SETTLE_TIMEOUT_MS,
    )
    assert (
        page.get_by_role("button", name="Show Free tier").get_attribute("aria-current")
        == "true"
    )

    premium_indicator = page.get_by_role("button", name="Show Premium tier")
    premium_indicator.focus()
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.querySelector('[data-tier-card=\"premium\"]').getBoundingClientRect().right <= window.innerWidth",
        timeout=SETTLE_TIMEOUT_MS,
    )
    assert premium_indicator.get_attribute("aria-current") == "true"
    expect(page.locator('[data-tier-card="main"]')).to_contain_text("Most Popular")
    assert _body_overflow(page) <= 1


@pytest.mark.django_db(transaction=True)
def test_pricing_email_disclosure_submits_with_pricing_return_url(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)
    email = _new_email("pricing-1188")

    page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
    free_card = page.locator('[data-tier-card="free"]')
    toggle = free_card.locator('[data-testid="inline-register-email-toggle"]')
    toggle.click()
    free_card.locator("#register-email").wait_for(state="visible")
    assert toggle.get_attribute("aria-expanded") == "true"
    assert page.evaluate("document.activeElement.id") == "register-email"

    free_card.locator("#register-email").fill(email)
    free_card.locator("#register-password").fill(DEFAULT_PASSWORD)
    free_card.locator("#register-password-confirm").fill(DEFAULT_PASSWORD)
    free_card.locator("#register-submit").click()

    success = free_card.locator("#register-success")
    success.wait_for(state="visible")
    assert success.locator("a").get_attribute("href") == "/pricing"
    assert page.url.endswith("/pricing")


@pytest.mark.django_db(transaction=True)
def test_authenticated_pricing_keeps_account_state_and_no_inline_register(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)
        user = create_user("pricing-1188-main@test.com", tier_slug="main")
        user.subscription_id = "sub_pricing_1188_main"
        user.save(update_fields=["subscription_id"])

    context = auth_context(browser, "pricing-1188-main@test.com")
    page = context.new_page()
    try:
        page.set_viewport_size(MOBILE)
        page.goto(f"{django_server}/pricing", wait_until="networkidle")
        assert page.locator('[data-testid="inline-register-card"]').count() == 0
        expect(page.locator('[data-tier-card="main"]')).to_contain_text("Current plan")
        expect(page.locator('[data-tier-card="basic"]')).to_contain_text("Downgrade")
        expect(page.locator('[data-tier-card="premium"]')).to_contain_text(
            "Manage Subscription"
        )
        expect(page.locator('[data-testid="pricing-tier-indicators"]')).to_be_visible()
        assert _body_overflow(page) <= 1
    finally:
        context.close()
