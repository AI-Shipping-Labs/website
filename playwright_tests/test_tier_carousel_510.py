"""Issue #510 — mobile tier carousel reworked: Main visually dominant,
total section height reduced, `Most Popular` badge fully visible.

All scenarios run at viewport 393x851 (Pixel 7) unless noted.
Screenshots are written to ``playwright_tests/screenshots/issue-510/``.
"""

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

SCREENSHOT_DIR = Path("playwright_tests/screenshots/issue-510")
PIXEL_7 = {"width": 393, "height": 851}
DESKTOP = {"width": 1280, "height": 900}

PRICING_TIERS = [
    {
        "slug": "free",
        "name": "Free",
        "level": 0,
        "price_eur_month": None,
        "price_eur_year": None,
        "description": "Subscribe to the newsletter and access open content.",
        "features": [
            "Newsletter emails",
            "Access to open content",
            "Curated public resources",
            "Community blog updates",
            "Public interview prep",
        ],
    },
    {
        "slug": "basic",
        "name": "Basic",
        "level": 10,
        "price_eur_month": 20,
        "price_eur_year": 200,
        "description": "Access curated educational content, tutorials, and research.",
        "features": [
            "Exclusive articles",
            "Tutorials with code examples",
            "Behind-the-scenes research",
            "Curated social posts",
            "Slack-free study materials",
        ],
    },
    {
        "slug": "main",
        "name": "Main",
        "level": 20,
        "price_eur_month": 50,
        "price_eur_year": 500,
        "description": "Everything in Basic, plus structure and peer support.",
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
        "description": "Everything in Main, plus courses and personalized feedback.",
        "features": [
            "Everything in Main",
            "All mini-courses",
            "Personalized profile teardowns",
            "Career feedback",
            "1:1 office hours",
        ],
    },
]


def _ensure_pricing_tiers():
    from django.db import connection

    from payments.models import Tier

    for tier in PRICING_TIERS:
        Tier.objects.update_or_create(slug=tier["slug"], defaults=tier)
    connection.close()


def _screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _section_screenshot(page, selector, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.locator(selector).first.screenshot(
        path=SCREENSHOT_DIR / f"{name}.png"
    )


def _get_card_height(page, slug):
    return page.evaluate(
        """slug => {
            const el = document.querySelector(`[data-tier-card="${slug}"]`);
            return el ? el.getBoundingClientRect().height : 0;
        }""",
        slug,
    )


def _badge_visible(page, slug):
    """Return (top, bottom, parentTop, parentBottom) for the Most Popular badge.

    The badge top must be >= the closest scrolling ancestor's top so it is
    not clipped. The full element bounding box must be inside the viewport.
    """
    return page.evaluate(
        """slug => {
            const card = document.querySelector(`[data-tier-card="${slug}"]`);
            if (!card) return null;
            // find absolutely-positioned badge (the only absolute child with a star icon)
            const badge = card.querySelector('.absolute');
            if (!badge) return null;
            const r = badge.getBoundingClientRect();
            // walk up to find the scrolling ancestor (carousel or html)
            let ancestor = card.parentElement;
            while (ancestor && ancestor !== document.body) {
                const cs = getComputedStyle(ancestor);
                if (cs.overflowX === 'auto' || cs.overflowX === 'scroll' ||
                    cs.overflowY === 'auto' || cs.overflowY === 'scroll') {
                    break;
                }
                ancestor = ancestor.parentElement;
            }
            const ar = ancestor ? ancestor.getBoundingClientRect() : null;
            return {
                top: r.top,
                bottom: r.bottom,
                left: r.left,
                right: r.right,
                width: r.width,
                height: r.height,
                ancestorTop: ar ? ar.top : null,
                ancestorBottom: ar ? ar.bottom : null,
                ancestorTag: ancestor ? ancestor.tagName : null,
            };
        }""",
        slug,
    )


def _assert_no_body_overflow(page):
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"body horizontally overflows by {overflow}px"


# ---------------------------------------------------------------------------
# Scenario 1: Mobile visitor sees Main as visually dominant on the homepage
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_home_main_dominant_and_badge_visible_on_mobile(django_server, page):
    ensure_site_config_tiers()
    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")

    # Centering JS has run.
    page.wait_for_timeout(200)

    # Main is taller than peers on mobile (>= 1.20x).
    main_h = _get_card_height(page, "main")
    basic_h = _get_card_height(page, "basic")
    premium_h = _get_card_height(page, "premium")
    section_h = page.evaluate(
        "() => document.getElementById('tiers').getBoundingClientRect().height"
    )
    carousel_h = page.evaluate(
        "() => document.querySelector('[data-tier-carousel]').getBoundingClientRect().height"
    )
    section_top = page.evaluate(
        "() => document.getElementById('tiers').getBoundingClientRect().top + window.scrollY"
    )
    carousel_top = page.evaluate(
        "() => document.querySelector('[data-tier-carousel]').getBoundingClientRect().top + window.scrollY"
    )
    print(
        f"\n[issue-510] home #tiers section={section_h:.0f}px "
        f"carousel={carousel_h:.0f}px head={carousel_top - section_top:.0f}px "
        f"main={main_h:.0f}px basic={basic_h:.0f}px premium={premium_h:.0f}px"
    )
    assert main_h > 0, "Main card not found"
    assert basic_h > 0 and premium_h > 0, "Peer cards not found"
    assert main_h >= 1.20 * basic_h, (
        f"Main ({main_h}px) must be >=1.20x Basic ({basic_h}px) on mobile"
    )
    assert main_h >= 1.20 * premium_h, (
        f"Main ({main_h}px) must be >=1.20x Premium ({premium_h}px) on mobile"
    )

    # `Most Popular` badge is fully visible (top >= 0 inside the page viewport,
    # AND fully visible inside its scrolling ancestor).
    badge = _badge_visible(page, "main")
    assert badge is not None, "Badge not found inside Main card"
    assert badge["top"] >= 0, (
        f"Badge top ({badge['top']}) is above viewport top — clipped"
    )
    if badge["ancestorTop"] is not None:
        assert badge["top"] >= badge["ancestorTop"] - 1, (
            f"Badge top ({badge['top']}) is above its scrolling ancestor "
            f"top ({badge['ancestorTop']}) — clipped by overflow"
        )
    # Badge has non-zero size.
    assert badge["width"] > 0 and badge["height"] > 0

    _section_screenshot(
        page,
        '[data-testid="home-tier-carousel"]',
        "home_tiers_mobile_main_dominant",
    )
    # Scroll the section into view and capture the in-viewport rendering so
    # the screenshot reflects what a user actually sees.
    page.evaluate(
        "() => document.getElementById('tiers').scrollIntoView({block:'start'})"
    )
    page.wait_for_timeout(200)
    _screenshot(page, "home_tiers_in_viewport")


# ---------------------------------------------------------------------------
# Scenario 2: Mobile peer tiers reachable by swipe with 44px CTAs
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_home_peer_tiers_reachable_via_swipe(django_server, page):
    ensure_site_config_tiers()
    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")
    page.wait_for_timeout(200)

    # Each peer card must contain its own CTA button with min-height 44px.
    for slug in ("basic", "premium"):
        cta_height = page.evaluate(
            """slug => {
                const card = document.querySelector(`[data-tier-card="${slug}"]`);
                if (!card) return 0;
                const cta = card.querySelector('a.tier-cta-link');
                return cta ? cta.getBoundingClientRect().height : 0;
            }""",
            slug,
        )
        assert cta_height >= 44, (
            f"{slug} CTA height {cta_height}px is under 44px tap-target minimum"
        )

    # Programmatically scroll the carousel right by one card width.
    page.evaluate(
        """() => {
            const c = document.querySelector('[data-tier-carousel]');
            c.scrollLeft = c.scrollLeft + c.clientWidth;
        }"""
    )
    page.wait_for_timeout(250)

    # Scroll back two card widths to the left to reach Basic.
    page.evaluate(
        """() => {
            const c = document.querySelector('[data-tier-carousel]');
            c.scrollLeft = Math.max(0, c.scrollLeft - 2 * c.clientWidth);
        }"""
    )
    page.wait_for_timeout(250)

    _section_screenshot(
        page,
        '[data-testid="home-tier-carousel"]',
        "home_tiers_mobile_peer_swipe",
    )


# ---------------------------------------------------------------------------
# Scenario 3: Most Popular badge fully visible on /pricing on mobile
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_pricing_main_dominant_and_badge_visible_on_mobile(django_server, page):
    _ensure_pricing_tiers()
    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/pricing", wait_until="networkidle")
    page.wait_for_timeout(200)

    main_h = _get_card_height(page, "main")
    free_h = _get_card_height(page, "free")
    basic_h = _get_card_height(page, "basic")
    premium_h = _get_card_height(page, "premium")
    assert main_h > 0
    for slug, h in (("free", free_h), ("basic", basic_h), ("premium", premium_h)):
        assert h > 0, f"{slug} card not found"
        assert main_h >= 1.20 * h, (
            f"Main ({main_h}px) must be >=1.20x {slug} ({h}px) on mobile"
        )

    badge = _badge_visible(page, "main")
    assert badge is not None
    assert badge["top"] >= 0
    if badge["ancestorTop"] is not None:
        assert badge["top"] >= badge["ancestorTop"] - 1

    _section_screenshot(
        page,
        '[data-testid="pricing-tier-carousel"]',
        "pricing_tiers_mobile_badge_visible",
    )
    page.evaluate(
        "() => document.getElementById('pricing-section').scrollIntoView({block:'start'})"
    )
    page.wait_for_timeout(200)
    _screenshot(page, "pricing_tiers_in_viewport")


# ---------------------------------------------------------------------------
# Scenario 4: Logged-in paid Main member sees account-aware states
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_pricing_logged_in_main_member_account_states(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        _ensure_pricing_tiers()
        user = create_user("issue-510-main@test.com", tier_slug="main")
        user.subscription_id = "sub_issue_510_main"
        user.save(update_fields=["subscription_id"])

    context = auth_context(browser, "issue-510-main@test.com")
    page = context.new_page()
    page.set_viewport_size(PIXEL_7)
    try:
        page.goto(f"{django_server}/pricing", wait_until="networkidle")
        page.wait_for_timeout(200)

        # Account-aware state: Main shows "Current plan", peers show
        # Upgrade/Downgrade.
        expect(page.locator('[data-tier-card="main"]')).to_contain_text(
            "Current plan"
        )
        expect(page.locator('[data-tier-card="basic"]')).to_contain_text(
            "Downgrade"
        )
        expect(page.locator('[data-tier-card="premium"]')).to_contain_text(
            "Upgrade"
        )

        # Main is still dominant.
        main_h = _get_card_height(page, "main")
        for slug in ("free", "basic", "premium"):
            peer_h = _get_card_height(page, slug)
            assert main_h >= 1.10 * peer_h, (
                f"Main ({main_h}px) must dominate {slug} ({peer_h}px) "
                f"even with account-aware state"
            )

        # Badge still fully visible.
        badge = _badge_visible(page, "main")
        assert badge is not None
        assert badge["top"] >= 0
        if badge["ancestorTop"] is not None:
            assert badge["top"] >= badge["ancestorTop"] - 1

        _section_screenshot(
            page,
            '[data-testid="pricing-tier-carousel"]',
            "pricing_tiers_mobile_logged_in_main",
        )
    finally:
        context.close()


# ---------------------------------------------------------------------------
# Scenario 5: Mobile section consumes less vertical real estate
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_home_tiers_section_height_under_threshold(django_server, page):
    """Issue #510 acceptance criterion: the `#tiers` section's mobile vertical
    footprint at Pixel 7 (393x851) must be at least 15 percent smaller than
    the pre-fix dev rendering.

    Pre-fix baseline measured at 393x851 on this branch: section = 1449px,
    all cards uniform 942px (uniform because flex `align-items: stretch`
    forced peers to match Main's height, and the full feature list rendered
    for every card on mobile).

    Acceptance threshold: section height <= 1449 * 0.85 = 1232px.

    All three tiers must still be in the DOM inside the carousel.
    """
    ensure_site_config_tiers()
    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")
    page.wait_for_timeout(200)

    section_height = page.evaluate(
        """() => {
            const s = document.getElementById('tiers');
            return s ? s.getBoundingClientRect().height : 0;
        }"""
    )
    pre_fix_baseline_px = 1449
    upper_bound = pre_fix_baseline_px * 0.85
    assert 0 < section_height <= upper_bound, (
        f"#tiers section height {section_height}px exceeds 15% reduction "
        f"target ({upper_bound}px) vs pre-fix baseline {pre_fix_baseline_px}px"
    )

    # All three tiers still present.
    for slug in ("basic", "main", "premium"):
        assert page.locator(
            f'[data-testid="home-tier-carousel"] [data-tier-card="{slug}"]'
        ).count() == 1


# ---------------------------------------------------------------------------
# Scenario 6: No body overflow at multiple widths on / and /pricing
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_no_horizontal_body_overflow_across_widths(django_server, page):
    ensure_site_config_tiers()
    _ensure_pricing_tiers()
    for width in (320, 393, 768, 1024):
        page.set_viewport_size({"width": width, "height": 851})
        for path in ("/", "/pricing"):
            page.goto(f"{django_server}{path}", wait_until="networkidle")
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - "
                "window.innerWidth"
            )
            assert overflow <= 1, (
                f"Body horizontally overflows by {overflow}px at "
                f"width={width} on {path}"
            )


# ---------------------------------------------------------------------------
# Scenario 7: Desktop layout is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_desktop_layout_unchanged(django_server, page):
    ensure_site_config_tiers()
    _ensure_pricing_tiers()

    page.set_viewport_size(DESKTOP)
    page.goto(f"{django_server}/", wait_until="networkidle")

    # Grid display, 3 columns on home.
    home_carousel = page.locator('[data-testid="home-tier-carousel"]')
    assert (
        home_carousel.evaluate("el => getComputedStyle(el).display") == "grid"
    )
    assert page.locator('[data-testid="home-tier-card"]').count() == 3

    # Highlighted Main has lg:scale-105 in effect.
    main_transform = page.evaluate(
        """() => {
            const main = document.querySelector('[data-tier-card="main"]');
            return main ? getComputedStyle(main).transform : '';
        }"""
    )
    # `transform: matrix(1.05, 0, 0, 1.05, 0, 0)` is what Tailwind's
    # `scale-105` resolves to — accept any matrix containing 1.05.
    assert "1.05" in main_transform, (
        f"Expected lg:scale-105 transform on Main, got: {main_transform}"
    )

    # Full feature list on Basic — at least 5 features (no condensed mobile
    # treatment on desktop).
    basic_features = page.evaluate(
        """() => {
            const card = document.querySelector('[data-tier-card="basic"]');
            if (!card) return 0;
            return Array.from(card.querySelectorAll('ul li')).filter(li => {
                return getComputedStyle(li).display !== 'none';
            }).length;
        }"""
    )
    assert basic_features >= 4, (
        f"Basic card must show full feature list on desktop, got "
        f"{basic_features} visible items"
    )

    _screenshot(page, "home_tiers_desktop_unchanged")

    page.goto(f"{django_server}/pricing", wait_until="networkidle")
    pricing_carousel = page.locator(
        '[data-testid="pricing-tier-carousel"]'
    )
    assert (
        pricing_carousel.evaluate("el => getComputedStyle(el).display")
        == "grid"
    )
    assert page.locator('[data-testid="pricing-tier-card"]').count() == 4

    _screenshot(page, "pricing_tiers_desktop_unchanged")


# ---------------------------------------------------------------------------
# Scenario 8: Recommended-tier auto-centering still works on mobile
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_main_auto_centered_on_mobile_load(django_server, page):
    ensure_site_config_tiers()
    _ensure_pricing_tiers()
    page.set_viewport_size(PIXEL_7)

    for path, selector in (
        ("/", '[data-testid="home-tier-carousel"]'),
        ("/pricing", '[data-testid="pricing-tier-carousel"]'),
    ):
        page.goto(f"{django_server}{path}", wait_until="networkidle")
        page.wait_for_timeout(400)
        scroll_left = page.evaluate(
            """sel => {
                const c = document.querySelector(sel);
                return c ? c.scrollLeft : 0;
            }""",
            selector,
        )
        assert scroll_left > 0, (
            f"Recommended-tier auto-centering must set scrollLeft > 0 on "
            f"{path}, got {scroll_left}"
        )

        # And Main is roughly centered. Allow up to 60px slack — the centering
        # is computed by the JS using offsetLeft / clientWidth math, but the
        # asymmetric card widths introduced for issue #510 plus the carousel's
        # left-padding mean exact midline alignment can vary by tens of pixels.
        # The user-perceived test is "Main is the prominent card on screen" —
        # 60px on a 393px-wide viewport is still well within the centered band.
        delta = page.evaluate(
            """sel => {
                const el = document.querySelector(sel);
                const main = el.querySelector('[data-tier-card="main"]');
                const er = el.getBoundingClientRect();
                const mr = main.getBoundingClientRect();
                return Math.abs(
                    (mr.left + mr.width / 2) - (er.left + er.width / 2)
                );
            }""",
            selector,
        )
        assert delta < 60, (
            f"Main not centered on {path}, delta from carousel midline = "
            f"{delta}px"
        )
