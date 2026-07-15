"""Focused browser coverage for intrinsic homepage tier-card heights (#1235)."""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import ensure_site_config_tiers

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
]

DESKTOP = {"width": 1440, "height": 1000}
MOBILE = {"width": 390, "height": 844}
SCREENSHOT_DIR = Path('.tmp/screenshots/issue-1235')


def _seed_tiers(django_db_blocker):
    with django_db_blocker.unblock():
        ensure_site_config_tiers()


def _card_metrics(page):
    return page.locator('[data-testid="home-tier-card"]').evaluate_all(
        """cards => cards.map(card => {
          const cta = card.querySelector(
            '[data-testid="home-free-tier-cta"], .tier-cta-link'
          );
          const cardRect = card.getBoundingClientRect();
          const ctaRect = cta.getBoundingClientRect();
          return {
            slug: card.dataset.tierCard,
            offsetHeight: card.offsetHeight,
            blankBelowCta: cardRect.bottom - ctaRect.bottom,
          };
        })"""
    )


def _wait_for_main_centered(page):
    page.wait_for_function(
        """() => {
          const carousel = document.querySelector(
            '[data-testid="home-tier-carousel"]'
          );
          const main = carousel && carousel.querySelector(
            '[data-tier-card="main"]'
          );
          if (!main || carousel.scrollLeft <= 0) return false;
          const outer = carousel.getBoundingClientRect();
          const inner = main.getBoundingClientRect();
          return Math.abs(
            (inner.left + inner.width / 2) -
            (outer.left + outer.width / 2)
          ) < 60;
        }"""
    )


@pytest.mark.visual_regression
def test_desktop_cards_use_intrinsic_heights_and_preserve_billing(
    django_server, page, django_db_blocker
):
    _seed_tiers(django_db_blocker)
    page.set_viewport_size(DESKTOP)
    page.goto(f'{django_server}/', wait_until='domcontentloaded')

    carousel = page.locator('[data-testid="home-tier-carousel"]')
    carousel.scroll_into_view_if_needed()
    expect(carousel.locator('[data-testid="home-tier-card"]')).to_have_count(4)
    assert carousel.evaluate('el => getComputedStyle(el).display') == 'grid'
    assert carousel.evaluate('el => getComputedStyle(el).alignItems') == 'flex-start'

    metrics = _card_metrics(page)
    assert len({item['offsetHeight'] for item in metrics}) > 1, metrics
    for item in metrics:
        # The card padding is 32px; allow subpixel rounding from Main's
        # desktop scale transform while rejecting any stretch-created band.
        assert item['blankBelowCta'] <= 36, item

    free_cta = carousel.locator('[data-testid="home-free-tier-cta"]')
    basic_cta = carousel.locator('[data-tier-card="basic"] .tier-cta-link')
    expect(free_cta).to_have_attribute('href', '/#join-free')
    expect(carousel.locator('[data-tier-card="main"]')).to_contain_text(
        'Most Popular'
    )

    monthly_link = basic_cta.get_attribute('data-link-monthly')
    annual_link = basic_cta.get_attribute('data-link-annual')
    expect(basic_cta).to_have_attribute('href', monthly_link)
    page.locator('#billing-toggle').click()
    expect(basic_cta).to_have_attribute('href', annual_link)
    expect(free_cta).to_have_attribute('href', '/#join-free')
    page.locator('#billing-toggle').click()
    expect(basic_cta).to_have_attribute('href', monthly_link)


def test_mobile_carousel_still_centers_main_and_reaches_every_tier(
    django_server, page, django_db_blocker
):
    _seed_tiers(django_db_blocker)
    page.set_viewport_size(MOBILE)
    page.goto(f'{django_server}/', wait_until='domcontentloaded')
    page.wait_for_load_state('load')

    carousel = page.locator('[data-testid="home-tier-carousel"]')
    _wait_for_main_centered(page)
    carousel.scroll_into_view_if_needed()
    assert carousel.evaluate('el => getComputedStyle(el).display') == 'flex'
    assert page.evaluate(
        'document.documentElement.scrollWidth <= window.innerWidth + 1'
    )

    main_delta = carousel.evaluate(
        """el => {
          const card = el.querySelector('[data-tier-card="main"]');
          const outer = el.getBoundingClientRect();
          const inner = card.getBoundingClientRect();
          return Math.abs(
            (inner.left + inner.width / 2) -
            (outer.left + outer.width / 2)
          );
        }"""
    )
    assert main_delta < 60
    expect(carousel.locator('[data-tier-card="main"]')).to_contain_text(
        'Most Popular'
    )

    for slug in ('free', 'basic', 'main', 'premium'):
        card = carousel.locator(f'[data-tier-card="{slug}"]')
        card.scroll_into_view_if_needed()
        expect(card).to_be_visible()
        expect(card.locator('a').last).to_be_visible()

    expect(
        carousel.locator('[data-tier-card="free"]')
        .get_by_role('link', name='Join free', exact=True)
    ).to_have_attribute('href', '/#join-free')


@pytest.mark.manual_visual
def test_homepage_tiers_light_dark_responsive_evidence(
    django_server, browser, django_db_blocker
):
    """Generate tester-reviewable desktop/mobile evidence in both themes."""
    _seed_tiers(django_db_blocker)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    for theme in ('light', 'dark'):
        for viewport_name, viewport in (('desktop', DESKTOP), ('mobile', MOBILE)):
            context = browser.new_context(viewport=viewport)
            context.add_init_script(
                f"""
                localStorage.setItem('theme', '{theme}');
                document.documentElement.classList.toggle(
                  'dark', '{theme}' === 'dark'
                );
                """
            )
            page = context.new_page()
            try:
                page.goto(f'{django_server}/', wait_until='domcontentloaded')
                tiers = page.locator('#tiers')
                if viewport_name == 'mobile':
                    page.wait_for_load_state('load')
                    _wait_for_main_centered(page)
                tiers.scroll_into_view_if_needed()
                expect(tiers.locator('[data-testid="home-tier-card"]')).to_have_count(4)
                assert page.evaluate(
                    "document.documentElement.classList.contains('dark')"
                ) is (theme == 'dark')
                page.add_style_tag(
                    content='header, #section-nav { visibility: hidden !important; }'
                )
                tiers.screenshot(
                    path=SCREENSHOT_DIR / f'{viewport_name}-{theme}.png'
                )
            finally:
                context.close()
