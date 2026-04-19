"""Playwright E2E tests for the Stripe checkout success/cancelled banners (Issue #266).

These tests cover the user-visible JS behavior that was previously asserted via
template-string-matching unit tests in
``payments/tests/test_checkout_feedback.py`` and removed under
``_docs/testing-guidelines.md`` Rule 4 (issue #258).

Backend redirect-URL behavior is still covered by
``payments.tests.test_checkout_feedback.CheckoutSuccessRedirectTest``.

Scenarios covered:
1. Authenticated user lands on ``/?checkout=success`` -> success banner shows,
   URL is cleaned via ``history.replaceState``, dismiss button hides the banner.
2. Anonymous user lands on ``/pricing?checkout=cancelled`` -> cancelled banner
   shows and the URL is cleaned.
3. Visiting ``/`` and ``/pricing`` without the query parameter renders neither
   banner.
"""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    VIEWPORT,
)
from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


SUCCESS_BANNER = "#checkout-success-banner"
SUCCESS_DISMISS = "#dismiss-success-banner"
SUCCESS_TEXT = "Payment successful! Welcome to AI Shipping Labs."

CANCELLED_BANNER = "#checkout-cancelled-banner"
CANCELLED_TEXT = "Checkout was cancelled. You can try again anytime."


# ---------------------------------------------------------------------------
# Scenario 1: Authenticated user lands on /?checkout=success
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSuccessBannerOnDashboard:
    EMAIL = "checkout-success@test.com"

    def test_banner_visible_url_cleaned_then_dismissable(
        self, django_server, browser
    ):
        _create_user(self.EMAIL, tier_slug="basic")

        context = _auth_context(browser, self.EMAIL)
        page = context.new_page()
        try:
            page.goto(
                f"{django_server}/?checkout=success",
                wait_until="domcontentloaded",
            )

            banner = page.locator(SUCCESS_BANNER)
            expect(banner).to_be_visible(timeout=5000)
            expect(banner).to_contain_text(SUCCESS_TEXT)

            # JS should remove ?checkout=success from the URL via
            # history.replaceState so a refresh / share does not re-trigger
            # the banner.
            page.wait_for_function(
                "() => !new URLSearchParams(window.location.search).has('checkout')",
                timeout=5000,
            )
            assert "checkout=success" not in page.url
            assert "checkout" not in page.url

            # Click the dismiss button -> banner becomes hidden.
            page.locator(SUCCESS_DISMISS).click()
            expect(banner).to_be_hidden(timeout=5000)
        finally:
            context.close()


# ---------------------------------------------------------------------------
# Scenario 2: Anonymous user lands on /pricing?checkout=cancelled
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCancelledBannerOnPricing:
    def test_banner_visible_and_url_cleaned(self, django_server, page):
        page.goto(
            f"{django_server}/pricing?checkout=cancelled",
            wait_until="domcontentloaded",
        )

        banner = page.locator(CANCELLED_BANNER)
        expect(banner).to_be_visible(timeout=5000)
        expect(banner).to_contain_text(CANCELLED_TEXT)

        # URL should be cleaned of the cancelled query param.
        page.wait_for_function(
            "() => !new URLSearchParams(window.location.search).has('checkout')",
            timeout=5000,
        )
        assert "checkout=cancelled" not in page.url
        assert "/pricing" in page.url


# ---------------------------------------------------------------------------
# Scenario 3: No banner without the query parameter
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNoBannerWithoutQueryParam:
    EMAIL = "no-banner@test.com"

    def test_dashboard_without_query_param_hides_banner(
        self, django_server, browser
    ):
        _create_user(self.EMAIL, tier_slug="free")

        context = _auth_context(browser, self.EMAIL)
        page = context.new_page()
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")

            # The success banner element is rendered server-side but kept
            # hidden by the ``hidden`` Tailwind class until the JS toggles
            # it. ``to_be_hidden`` passes when the element is absent OR when
            # it is present but not visible.
            expect(page.locator(SUCCESS_BANNER)).to_be_hidden()
        finally:
            context.close()

    def test_pricing_without_query_param_hides_banner(
        self, django_server, browser
    ):
        # Anonymous browsing context -- no auth required for /pricing.
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        try:
            page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
            expect(page.locator(CANCELLED_BANNER)).to_be_hidden()
        finally:
            context.close()
