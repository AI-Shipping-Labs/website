"""
Playwright E2E tests for the Membership Tiers pricing page (Issue #68).

Tests cover:
- Anonymous visitor browsing the pricing page
- Tier comparison (4 tiers in order)
- Monthly/annual billing toggle
- Stripe payment link integration
- Visual distinction of the Main ("Most Popular") tier
- Cumulative feature lists
- Rapid toggle stress test

Usage:
    uv run pytest playwright_tests/test_membership_tiers.py -v --timeout=30
"""

import re

import pytest
from django.conf import settings
from playwright.sync_api import sync_playwright

from playwright_tests.conftest import DJANGO_BASE_URL


VIEWPORT = {"width": 1280, "height": 720}

# Expected tier order and data based on the seed migration (0003_seed_tiers.py)
EXPECTED_TIERS = [
    {"name": "Free", "slug": "free", "monthly": None, "annual": None},
    {"name": "Basic", "slug": "basic", "monthly": 20, "annual": 200},
    {"name": "Main", "slug": "main", "monthly": 50, "annual": 500},
    {"name": "Premium", "slug": "premium", "monthly": 100, "annual": 1000},
]

STRIPE_LINKS = settings.STRIPE_PAYMENT_LINKS


def _get_tier_cards(page):
    """Return all tier card elements in order."""
    # Each tier card is a direct child div of the grid container
    grid = page.locator(
        "div.grid.sm\\:grid-cols-2.lg\\:grid-cols-4"
    )
    return grid.locator("> div")


def _get_tier_card_by_name(page, tier_name):
    """Return the tier card element that contains the given tier name in its h2."""
    cards = _get_tier_cards(page)
    count = cards.count()
    for i in range(count):
        card = cards.nth(i)
        h2_text = card.locator("h2").inner_text()
        if h2_text.strip() == tier_name:
            return card
    raise ValueError(f"Tier card '{tier_name}' not found")


@pytest.mark.django_db
class TestScenario1AnonymousBrowsesFreeSubscribe:
    """
    Scenario 1: Anonymous visitor browses pricing to understand what they get
    for free.
    """

    def test_pricing_page_loads_without_login(self, django_server):
        """Navigate to /pricing without being logged in. Verify HTTP 200."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                response = page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                assert response.status == 200
            finally:
                browser.close()

    def test_free_tier_shows_zero_price_and_subscribe_button(self, django_server):
        """
        Read the Free tier card -- verify it shows currency 0 with /forever
        and a Subscribe button (not Join).
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")

                # Check price shows 0
                price_text = free_card.locator(
                    "span.text-4xl"
                ).inner_text()
                assert "0" in price_text

                # Check /forever label
                period_text = free_card.locator(
                    "span.text-muted-foreground",
                ).filter(has_text="/forever").inner_text()
                assert "/forever" in period_text

                # Check Subscribe button exists (not Join)
                cta = free_card.locator("a")
                cta_text = cta.inner_text()
                assert cta_text.strip() == "Subscribe"
            finally:
                browser.close()

    def test_free_tier_features_include_newsletter_and_open_content(
        self, django_server
    ):
        """Verify the Free tier's feature list includes expected items."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")
                features_text = free_card.locator("ul").inner_text()
                assert "Newsletter emails" in features_text
                assert "Access to open content" in features_text
            finally:
                browser.close()

    def test_free_subscribe_button_navigates_to_newsletter(self, django_server):
        """Click the Subscribe button on the Free tier and verify navigation."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")
                subscribe_link = free_card.locator("a")

                # Verify href before clicking
                href = subscribe_link.get_attribute("href")
                assert href == "/#newsletter"

                # Click and verify navigation
                subscribe_link.click()
                page.wait_for_timeout(1000)
                assert "/#newsletter" in page.url or page.url.endswith(
                    "/#newsletter"
                )
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario2CompareAllFourTiers:
    """
    Scenario 2: Prospective member compares all four tiers to decide which
    to join.
    """

    def test_four_tiers_in_ascending_order(self, django_server):
        """Verify all four tiers appear in ascending order: Free, Basic, Main,
        Premium."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                cards = _get_tier_cards(page)
                assert cards.count() == 4

                names = []
                for i in range(4):
                    name = cards.nth(i).locator("h2").inner_text().strip()
                    names.append(name)

                assert names == ["Free", "Basic", "Main", "Premium"]
            finally:
                browser.close()

    def test_each_tier_has_name_price_description_and_features(
        self, django_server
    ):
        """Verify each tier card has a name, a price, a description, and at
        least one feature."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                cards = _get_tier_cards(page)
                for i in range(4):
                    card = cards.nth(i)
                    # Name
                    name = card.locator("h2").inner_text().strip()
                    assert len(name) > 0

                    # Price (all cards have a span with text-4xl class)
                    price_el = card.locator("span.text-4xl")
                    assert price_el.count() == 1

                    # Description (paragraph)
                    desc = card.locator("p").first.inner_text().strip()
                    assert len(desc) > 0

                    # Features (at least one li)
                    features = card.locator("ul li")
                    assert features.count() >= 1
            finally:
                browser.close()

    def test_cumulative_value_communicated(self, django_server):
        """Verify cumulative value: Basic lists its features, Main lists
        Everything in Basic, Premium lists Everything in Main."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                # Basic card
                basic_card = _get_tier_card_by_name(page, "Basic")
                basic_features = basic_card.locator("ul").inner_text()
                assert "Exclusive articles" in basic_features
                assert "Tutorials with code examples" in basic_features

                # Main card
                main_card = _get_tier_card_by_name(page, "Main")
                main_features = main_card.locator("ul").inner_text()
                assert "Everything in Basic" in main_features
                assert "Slack community access" in main_features
                assert "Group coding sessions" in main_features

                # Premium card
                premium_card = _get_tier_card_by_name(page, "Premium")
                premium_features = premium_card.locator("ul").inner_text()
                assert "Everything in Main" in premium_features
                assert "All mini-courses" in premium_features
                assert "Resume/LinkedIn/GitHub teardowns" in premium_features
            finally:
                browser.close()

    def test_only_main_tier_has_most_popular_badge(self, django_server):
        """Verify only the Main tier card displays the Most Popular badge."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                # The "Most Popular" text should appear exactly once on the page
                badges = page.locator("text=Most Popular")
                assert badges.count() == 1

                # And it should be inside the Main tier card
                main_card = _get_tier_card_by_name(page, "Main")
                main_badge = main_card.locator("text=Most Popular")
                assert main_badge.count() == 1

                # Verify other tiers do NOT have it
                for tier_name in ["Free", "Basic", "Premium"]:
                    card = _get_tier_card_by_name(page, tier_name)
                    badge = card.locator("text=Most Popular")
                    assert badge.count() == 0
            finally:
                browser.close()

    def test_paid_tiers_show_join_free_shows_subscribe(self, django_server):
        """Verify paid tiers show Join, Free shows Subscribe."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                # Free -> Subscribe
                free_card = _get_tier_card_by_name(page, "Free")
                free_cta = free_card.locator("a").last
                assert free_cta.inner_text().strip() == "Subscribe"

                # Paid tiers -> Join
                for tier_name in ["Basic", "Main", "Premium"]:
                    card = _get_tier_card_by_name(page, tier_name)
                    cta = card.locator("a.tier-cta-link")
                    assert cta.inner_text().strip() == "Join"
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario3BillingToggle:
    """
    Scenario 3: Cost-conscious visitor toggles to annual billing to see
    the savings.
    """

    def test_default_shows_monthly_prices(self, django_server):
        """Verify the default state shows monthly prices."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                # Check Basic price
                basic_card = _get_tier_card_by_name(page, "Basic")
                basic_price = basic_card.locator(".tier-price").inner_text()
                assert "20" in basic_price

                basic_period = basic_card.locator(".tier-period").inner_text()
                assert "/month" in basic_period

                # Check Main price
                main_card = _get_tier_card_by_name(page, "Main")
                main_price = main_card.locator(".tier-price").inner_text()
                assert "50" in main_price

                # Check Premium price
                premium_card = _get_tier_card_by_name(page, "Premium")
                premium_price = premium_card.locator(".tier-price").inner_text()
                assert "100" in premium_price
            finally:
                browser.close()

    def test_save_indicator_visible(self, django_server):
        """Verify the Save ~17% indicator is visible near the Annual label."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                annual_label = page.locator("#annual-label")
                label_text = annual_label.inner_text()
                assert "Save ~17%" in label_text
            finally:
                browser.close()

    def test_toggle_to_annual_shows_annual_prices(self, django_server):
        """Click the toggle to switch to Annual and verify annual prices."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                # Click the toggle
                page.locator("#billing-toggle").click()
                page.wait_for_timeout(300)

                # Verify Annual label is highlighted (text-foreground) and
                # Monthly is muted
                annual_label = page.locator("#annual-label")
                assert "text-foreground" in annual_label.get_attribute("class")
                monthly_label = page.locator("#monthly-label")
                assert "text-muted-foreground" in monthly_label.get_attribute(
                    "class"
                )

                # Check annual prices
                basic_card = _get_tier_card_by_name(page, "Basic")
                assert "200" in basic_card.locator(".tier-price").inner_text()
                assert "/year" in basic_card.locator(
                    ".tier-period"
                ).inner_text()

                main_card = _get_tier_card_by_name(page, "Main")
                assert "500" in main_card.locator(".tier-price").inner_text()

                premium_card = _get_tier_card_by_name(page, "Premium")
                assert "1000" in premium_card.locator(
                    ".tier-price"
                ).inner_text()
            finally:
                browser.close()

    def test_free_tier_unaffected_by_toggle(self, django_server):
        """Verify the Free tier price remains 0 when toggling to annual."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")
                price_before = free_card.locator(
                    "span.text-4xl"
                ).inner_text()
                assert "0" in price_before

                # Toggle to annual
                page.locator("#billing-toggle").click()
                page.wait_for_timeout(300)

                price_after = free_card.locator(
                    "span.text-4xl"
                ).inner_text()
                assert "0" in price_after

                # Period should still say /forever
                period = free_card.locator(
                    "span.text-muted-foreground"
                ).filter(has_text="/forever")
                assert period.count() == 1
            finally:
                browser.close()

    def test_toggle_back_to_monthly_restores_prices(self, django_server):
        """Toggle to annual and back to monthly, verify prices revert."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                toggle = page.locator("#billing-toggle")

                # Toggle to annual
                toggle.click()
                page.wait_for_timeout(300)

                # Toggle back to monthly
                toggle.click()
                page.wait_for_timeout(300)

                # Verify monthly prices
                basic_card = _get_tier_card_by_name(page, "Basic")
                assert "20" in basic_card.locator(".tier-price").inner_text()
                assert "/month" in basic_card.locator(
                    ".tier-period"
                ).inner_text()

                main_card = _get_tier_card_by_name(page, "Main")
                assert "50" in main_card.locator(".tier-price").inner_text()

                premium_card = _get_tier_card_by_name(page, "Premium")
                assert "100" in premium_card.locator(
                    ".tier-price"
                ).inner_text()
                assert "/month" in premium_card.locator(
                    ".tier-period"
                ).inner_text()
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario4MainMonthlyStripeLink:
    """
    Scenario 4: Visitor selects Main (monthly) and is sent to the correct
    Stripe payment link.
    """

    def test_main_monthly_join_button_has_correct_stripe_link(
        self, django_server
    ):
        """Verify the Main Join button href is a valid Stripe link matching
        the configured monthly payment link."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                main_card = _get_tier_card_by_name(page, "Main")
                join_button = main_card.locator("a.tier-cta-link")
                monthly_link = join_button.get_attribute("href")

                # Verify it starts with https://buy.stripe.com/
                assert monthly_link.startswith("https://buy.stripe.com/")

                # Verify it matches the configured payment link
                expected = STRIPE_LINKS["main"]["monthly"]
                assert monthly_link == expected
            finally:
                browser.close()

    def test_main_join_button_has_target_blank(self, django_server):
        """Verify the Join button has target=_blank to open Stripe in a new
        tab."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                main_card = _get_tier_card_by_name(page, "Main")
                join_button = main_card.locator("a.tier-cta-link")
                target = join_button.get_attribute("target")
                assert target == "_blank"
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario5AnnualStripeLinksSwap:
    """
    Scenario 5: Visitor switches to annual billing and verifies Join buttons
    update to annual Stripe links.
    """

    def test_paid_tiers_have_distinct_monthly_and_annual_data_attributes(
        self, django_server
    ):
        """Verify each paid tier's Join button has distinct values in
        data-link-monthly and data-link-annual."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                for tier_name in ["Basic", "Main", "Premium"]:
                    card = _get_tier_card_by_name(page, tier_name)
                    cta = card.locator("a.tier-cta-link")
                    monthly_attr = cta.get_attribute("data-link-monthly")
                    annual_attr = cta.get_attribute("data-link-annual")
                    assert monthly_attr is not None, (
                        f"{tier_name} missing data-link-monthly"
                    )
                    assert annual_attr is not None, (
                        f"{tier_name} missing data-link-annual"
                    )
                    assert monthly_attr != annual_attr, (
                        f"{tier_name} monthly and annual links are the same"
                    )
            finally:
                browser.close()

    def test_toggle_to_annual_updates_href_to_annual_links(
        self, django_server
    ):
        """After toggling to annual, each paid tier Join button href matches
        its data-link-annual value."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                # Toggle to annual
                page.locator("#billing-toggle").click()
                page.wait_for_timeout(300)

                for tier_name in ["Basic", "Main", "Premium"]:
                    card = _get_tier_card_by_name(page, tier_name)
                    cta = card.locator("a.tier-cta-link")
                    href = cta.get_attribute("href")
                    annual_attr = cta.get_attribute("data-link-annual")
                    assert href == annual_attr, (
                        f"{tier_name} href {href} does not match "
                        f"data-link-annual {annual_attr}"
                    )
            finally:
                browser.close()

    def test_toggle_back_to_monthly_reverts_href_to_monthly_links(
        self, django_server
    ):
        """After toggling back to monthly, each paid tier Join button href
        reverts to data-link-monthly."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                toggle = page.locator("#billing-toggle")

                # Toggle to annual then back to monthly
                toggle.click()
                page.wait_for_timeout(300)
                toggle.click()
                page.wait_for_timeout(300)

                for tier_name in ["Basic", "Main", "Premium"]:
                    card = _get_tier_card_by_name(page, tier_name)
                    cta = card.locator("a.tier-cta-link")
                    href = cta.get_attribute("href")
                    monthly_attr = cta.get_attribute("data-link-monthly")
                    assert href == monthly_attr, (
                        f"{tier_name} href {href} does not match "
                        f"data-link-monthly {monthly_attr}"
                    )
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario6PremiumAnnualStripeLink:
    """
    Scenario 6: Visitor picks Premium (annual) and confirms it leads to
    the most expensive Stripe link.
    """

    def test_premium_annual_shows_correct_price_and_stripe_link(
        self, django_server
    ):
        """Toggle to annual, verify Premium shows 1000/year and the correct
        Stripe link."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                # Toggle to annual
                page.locator("#billing-toggle").click()
                page.wait_for_timeout(300)

                premium_card = _get_tier_card_by_name(page, "Premium")

                # Verify price
                price = premium_card.locator(".tier-price").inner_text()
                assert "1000" in price

                period = premium_card.locator(".tier-period").inner_text()
                assert "/year" in period

                # Verify Stripe link
                cta = premium_card.locator("a.tier-cta-link")
                href = cta.get_attribute("href")
                assert href.startswith("https://buy.stripe.com/")

                expected = STRIPE_LINKS["premium"]["annual"]
                assert href == expected
            finally:
                browser.close()

    def test_premium_annual_link_differs_from_monthly(self, django_server):
        """Verify Premium annual link is different from its monthly link."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                premium_card = _get_tier_card_by_name(page, "Premium")
                cta = premium_card.locator("a.tier-cta-link")
                monthly_link = cta.get_attribute("data-link-monthly")
                annual_link = cta.get_attribute("data-link-annual")
                assert monthly_link != annual_link
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario7FreeSubscribeFlow:
    """
    Scenario 7: Free-tier subscriber clicks Subscribe and starts the
    newsletter signup flow.
    """

    def test_free_tier_has_no_join_button(self, django_server):
        """Verify the Free tier card does NOT have a Join button."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")
                join_buttons = free_card.locator("a.tier-cta-link")
                assert join_buttons.count() == 0
            finally:
                browser.close()

    def test_free_subscribe_links_to_newsletter(self, django_server):
        """Verify the Subscribe button links to /#newsletter."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")
                subscribe_link = free_card.locator("a")
                href = subscribe_link.get_attribute("href")
                assert href == "/#newsletter"
            finally:
                browser.close()

    def test_free_tier_shows_zero_forever(self, django_server):
        """Verify the Free tier card shows 0 with /forever."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")
                price = free_card.locator("span.text-4xl").inner_text()
                assert "0" in price

                forever = free_card.locator(
                    "span.text-muted-foreground"
                ).filter(has_text="/forever")
                assert forever.count() == 1
            finally:
                browser.close()

    def test_subscribe_click_navigates_to_newsletter_section(
        self, django_server
    ):
        """Click Subscribe and verify navigation to /#newsletter."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")
                subscribe_link = free_card.locator("a")
                subscribe_link.click()
                page.wait_for_timeout(1000)
                assert "newsletter" in page.url
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario8MainTierVisualDistinction:
    """
    Scenario 8: Visitor confirms the Main tier is visually distinguished as
    the recommended plan.
    """

    def test_main_tier_has_most_popular_badge(self, django_server):
        """Verify Most Popular badge appears on Main and no other card."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                main_card = _get_tier_card_by_name(page, "Main")
                badge = main_card.locator("text=Most Popular")
                assert badge.count() == 1

                # No other card has it
                for name in ["Free", "Basic", "Premium"]:
                    card = _get_tier_card_by_name(page, name)
                    assert card.locator("text=Most Popular").count() == 0
            finally:
                browser.close()

    def test_main_tier_has_accent_border_and_ring(self, django_server):
        """Verify the Main tier card has border-accent and ring-2 ring-accent/20
        CSS classes."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                main_card = _get_tier_card_by_name(page, "Main")
                classes = main_card.get_attribute("class")
                assert "border-accent" in classes
                assert "ring-2" in classes
                assert "ring-accent/20" in classes

                # Other cards should NOT have these accent classes
                for name in ["Free", "Basic", "Premium"]:
                    card = _get_tier_card_by_name(page, name)
                    card_classes = card.get_attribute("class")
                    assert "border-accent" not in card_classes
            finally:
                browser.close()

    def test_main_join_button_styled_differently(self, django_server):
        """Verify the Main Join button uses bg-accent while others use
        bg-secondary."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                # Main Join button should have bg-accent
                main_card = _get_tier_card_by_name(page, "Main")
                main_cta = main_card.locator("a.tier-cta-link")
                main_classes = main_cta.get_attribute("class")
                assert "bg-accent" in main_classes

                # Basic and Premium should have bg-secondary
                for name in ["Basic", "Premium"]:
                    card = _get_tier_card_by_name(page, name)
                    cta = card.locator("a.tier-cta-link")
                    cta_classes = cta.get_attribute("class")
                    assert "bg-secondary" in cta_classes
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario9CumulativeFeatureLists:
    """
    Scenario 9: All tier feature lists accurately reflect the cumulative
    value proposition.
    """

    def test_free_tier_features(self, django_server):
        """Verify Free tier lists exactly Newsletter emails and Access to
        open content."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                free_card = _get_tier_card_by_name(page, "Free")
                features = free_card.locator("ul li")
                assert features.count() == 2

                texts = [
                    features.nth(i).inner_text().strip()
                    for i in range(features.count())
                ]
                assert "Newsletter emails" in texts
                assert "Access to open content" in texts
            finally:
                browser.close()

    def test_basic_tier_features(self, django_server):
        """Verify Basic tier features include expected items."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                basic_card = _get_tier_card_by_name(page, "Basic")
                features_text = basic_card.locator("ul").inner_text()
                for expected in [
                    "Exclusive articles",
                    "Tutorials with code examples",
                    "AI tool breakdowns",
                    "Research notes",
                    "Curated social posts",
                ]:
                    assert expected in features_text, (
                        f"Basic tier missing feature: {expected}"
                    )
            finally:
                browser.close()

    def test_main_tier_features(self, django_server):
        """Verify Main tier starts with Everything in Basic and includes
        expected features."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                main_card = _get_tier_card_by_name(page, "Main")
                features_text = main_card.locator("ul").inner_text()

                for expected in [
                    "Everything in Basic",
                    "Slack community access",
                    "Group coding sessions",
                    "Project-based learning",
                    "Community hackathons",
                    "Career discussions",
                    "Personal brand guidance",
                    "Topic voting",
                ]:
                    assert expected in features_text, (
                        f"Main tier missing feature: {expected}"
                    )
            finally:
                browser.close()

    def test_premium_tier_features(self, django_server):
        """Verify Premium tier starts with Everything in Main and includes
        expected features."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )
                premium_card = _get_tier_card_by_name(page, "Premium")
                features_text = premium_card.locator("ul").inner_text()

                for expected in [
                    "Everything in Main",
                    "All mini-courses",
                    "Mini-course topic voting",
                    "Resume/LinkedIn/GitHub teardowns",
                ]:
                    assert expected in features_text, (
                        f"Premium tier missing feature: {expected}"
                    )
            finally:
                browser.close()


@pytest.mark.django_db
class TestScenario10RapidToggleStressTest:
    """
    Scenario 10: Visitor rapidly toggles billing multiple times and prices
    stay consistent.
    """

    def test_rapid_toggle_returns_to_monthly_after_even_clicks(
        self, django_server
    ):
        """Click toggle rapidly multiple times. An even number of clicks
        (starting from the monthly default) returns to monthly. Verify that
        rapid toggling does not corrupt state."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )

                # Use JavaScript to fire exactly 6 click events rapidly
                # (even number -> back to monthly default)
                # monthly->annual->monthly->annual->monthly->annual->monthly
                # Wait, 6 clicks from false: T F T F T F => false = monthly
                page.evaluate("""
                    const toggle = document.getElementById('billing-toggle');
                    for (let i = 0; i < 6; i++) {
                        toggle.click();
                    }
                """)

                page.wait_for_timeout(500)

                # Verify monthly prices (even clicks = back to default)
                basic_card = _get_tier_card_by_name(page, "Basic")
                assert "20" in basic_card.locator(".tier-price").inner_text()
                assert "/month" in basic_card.locator(
                    ".tier-period"
                ).inner_text()

                main_card = _get_tier_card_by_name(page, "Main")
                assert "50" in main_card.locator(".tier-price").inner_text()
                assert "/month" in main_card.locator(
                    ".tier-period"
                ).inner_text()

                premium_card = _get_tier_card_by_name(page, "Premium")
                assert "100" in premium_card.locator(
                    ".tier-price"
                ).inner_text()
                assert "/month" in premium_card.locator(
                    ".tier-period"
                ).inner_text()

                # Verify links match monthly
                for tier_name in ["Basic", "Main", "Premium"]:
                    card = _get_tier_card_by_name(page, tier_name)
                    cta = card.locator("a.tier-cta-link")
                    href = cta.get_attribute("href")
                    monthly_attr = cta.get_attribute("data-link-monthly")
                    assert href == monthly_attr, (
                        f"{tier_name} href after rapid toggle doesn't match "
                        f"monthly link"
                    )
            finally:
                browser.close()

    def test_one_more_toggle_after_rapid_switches_to_annual(
        self, django_server
    ):
        """After rapid even-number toggles (back to monthly), one more click
        switches to annual. Confirms state is not corrupted by rapid
        toggling."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/pricing", wait_until="networkidle"
                )

                # Fire exactly 6 click events via JavaScript (back to monthly)
                page.evaluate("""
                    const toggle = document.getElementById('billing-toggle');
                    for (let i = 0; i < 6; i++) {
                        toggle.click();
                    }
                """)

                page.wait_for_timeout(500)

                # One more click -> annual (7 total = odd = annual)
                page.locator("#billing-toggle").click()
                page.wait_for_timeout(300)

                # Verify annual state
                basic_card = _get_tier_card_by_name(page, "Basic")
                assert "200" in basic_card.locator(".tier-price").inner_text()
                assert "/year" in basic_card.locator(
                    ".tier-period"
                ).inner_text()

                main_card = _get_tier_card_by_name(page, "Main")
                assert "500" in main_card.locator(".tier-price").inner_text()

                premium_card = _get_tier_card_by_name(page, "Premium")
                assert "1000" in premium_card.locator(
                    ".tier-price"
                ).inner_text()
                assert "/year" in premium_card.locator(
                    ".tier-period"
                ).inner_text()

                # Verify links match annual
                for tier_name in ["Basic", "Main", "Premium"]:
                    card = _get_tier_card_by_name(page, tier_name)
                    cta = card.locator("a.tier-cta-link")
                    href = cta.get_attribute("href")
                    annual_attr = cta.get_attribute("data-link-annual")
                    assert href == annual_attr, (
                        f"{tier_name} href after toggle doesn't match "
                        f"annual link"
                    )
            finally:
                browser.close()
