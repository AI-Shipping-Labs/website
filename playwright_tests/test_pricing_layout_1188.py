"""Issue #1188 pricing layout and mobile carousel coverage."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    SETTLE_TIMEOUT_MS,
    auth_context,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
MAX_CAROUSEL_FEATURED_HEIGHT_DELTA = 180

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
          const cta = card.querySelector('.tier-cta-link, [data-action], [data-testid="pricing-free-signup-cta"]');
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
def test_pricing_desktop_cards_share_baseline_with_free_join_cta(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)

    page.set_viewport_size(DESKTOP)
    page.goto(f"{django_server}/membership", wait_until="networkidle")
    free_card = page.locator('[data-tier-card="free"]')
    # The Free tier is a single Join button that links to the register
    # page (the inline register form / email toggle were removed).
    signup_cta = free_card.locator('[data-testid="pricing-free-signup-cta"]')
    expect(signup_cta).to_be_visible()
    assert signup_cta.get_attribute("href") == "/accounts/register/?next=/membership"
    assert free_card.locator("#register-email").count() == 0

    metrics = {slug: _card_metrics(page, slug) for slug in ("free", "basic", "main", "premium")}
    # All four cards share a top/bottom baseline on the desktop grid row.
    for slug in ("basic", "main", "premium"):
        assert abs(metrics[slug]["top"] - metrics["free"]["top"]) <= 2
        assert abs(metrics[slug]["bottom"] - metrics["free"]["bottom"]) <= 2

    paid_cta_tops = [metrics[slug]["ctaTop"] for slug in ("basic", "main", "premium")]
    assert max(paid_cta_tops) - min(paid_cta_tops) <= 2


@pytest.mark.django_db(transaction=True)
def test_pricing_mobile_indicator_controls_scroll_without_overflow(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)

    page.set_viewport_size(MOBILE)
    page.goto(f"{django_server}/membership", wait_until="networkidle")
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"pricing-tier-carousel\"]').scrollLeft > 0",
        timeout=SETTLE_TIMEOUT_MS,
    )

    # The recommended (Main) tier auto-centers in the mobile carousel.
    # CSS scroll-snap resolves the JS-set scrollLeft to the nearest snap
    # point, which lands Main a small, deterministic offset from the exact
    # geometric center for these fixed card widths. The bound stays tight
    # enough to catch a genuinely mis-scrolled carousel (hundreds of px).
    assert _main_center_delta(page) < 80
    main = _card_metrics(page, "main")
    carousel_height = page.locator(
        '[data-testid="pricing-tier-carousel"]'
    ).evaluate("el => el.getBoundingClientRect().height")
    assert carousel_height <= main["height"] + MAX_CAROUSEL_FEATURED_HEIGHT_DELTA
    assert _body_overflow(page) <= 1

    indicators = page.locator('[data-testid="pricing-tier-indicator"]')
    dots = page.locator('[data-testid="pricing-tier-indicator-dot"]')
    assert indicators.count() == 4
    assert dots.count() == 4
    indicator_boxes = []
    for tier in ("Free", "Basic", "Main", "Premium"):
        indicator = page.get_by_role("button", name=f"Show {tier} tier")
        expect(indicator).to_be_visible()
        box = indicator.bounding_box()
        assert box is not None
        assert box["width"] >= 44
        assert box["height"] >= 44
        indicator_boxes.append(box)
    for dot_index in range(dots.count()):
        dot_box = dots.nth(dot_index).bounding_box()
        assert dot_box is not None
        assert 10 <= dot_box["width"] <= 14
        assert 10 <= dot_box["height"] <= 14
    assert max(box["y"] for box in indicator_boxes) - min(
        box["y"] for box in indicator_boxes
    ) <= 1
    group_box = page.get_by_role("group", name="Pricing tiers").bounding_box()
    assert group_box is not None
    assert abs((group_box["x"] + group_box["width"] / 2) - MOBILE["width"] / 2) <= 1
    assert (
        page.get_by_role("button", name="Show Main tier").get_attribute("aria-current")
        == "true"
    )

    most_popular = page.get_by_test_id("pricing-most-popular-badge")
    expect(most_popular).to_have_count(1)
    expect(most_popular).to_have_attribute("data-component", "member-badge")
    expect(most_popular.locator('[data-lucide="star"]')).to_have_count(1)
    most_popular_classes = most_popular.get_attribute("class").split()
    assert "bg-accent" in most_popular_classes
    assert "text-accent-foreground" in most_popular_classes
    assert "bg-accent/10" not in most_popular_classes
    for slug in ("free", "basic", "premium"):
        expect(
            page.locator(f'[data-tier-card="{slug}"]')
            .get_by_test_id("pricing-most-popular-badge")
        ).to_have_count(0)

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
    expect(page.locator('[data-tier-card="main"]')).to_contain_text("Most popular")
    assert _body_overflow(page) <= 1


@pytest.mark.django_db(transaction=True)
def test_pricing_free_join_cta_routes_to_register_with_pricing_return_url(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)

    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")
    free_card = page.locator('[data-tier-card="free"]')
    signup_cta = free_card.locator('[data-testid="pricing-free-signup-cta"]')
    expect(signup_cta).to_be_visible()
    # Clicking Join sends the visitor to the register page with next=/membership
    # so they return here after creating a free account.
    signup_cta.click()
    page.wait_for_url(
        f"{django_server}/accounts/register/?next=/membership", timeout=10000
    )


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
        page.goto(f"{django_server}/membership", wait_until="networkidle")
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
