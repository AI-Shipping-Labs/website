"""Playwright coverage for the configurable Google Analytics loader (issue #771).

Scenarios mirror the spec on the GitHub issue:

1. Anonymous visitor on a vanilla install does not get tracked (no GA
   markup on / or /blog).
2. Operator configures GA via Studio and the loader appears site-wide
   on /, /pricing.
3. Operator clears the measurement ID and tracking stops on the next
   request.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

pytestmark = pytest.mark.local_only


def _clear_analytics_setting():
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.filter(key='GOOGLE_ANALYTICS_ID').delete()
    clear_config_cache()
    connection.close()


def _set_analytics_setting(value):
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key='GOOGLE_ANALYTICS_ID',
        defaults={
            'value': value,
            'group': 'analytics',
            'is_secret': False,
        },
    )
    clear_config_cache()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestGoogleAnalyticsLoader:
    def test_anonymous_visit_emits_no_ga_when_unset(self, django_server, browser):
        """Scenario 1: fresh install, no setting => no GA loader on / or /blog."""
        _clear_analytics_setting()
        context = browser.new_context()
        page = context.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        html_home = page.content()
        assert 'googletagmanager.com' not in html_home, (
            "Expected no GA loader on / when GOOGLE_ANALYTICS_ID is unset."
        )
        assert 'googletagmanager' not in html_home

        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        html_blog = page.content()
        assert 'googletagmanager.com' not in html_blog, (
            "Expected no GA loader on /blog when GOOGLE_ANALYTICS_ID is unset."
        )
        assert 'googletagmanager' not in html_blog

    def test_operator_configures_ga_and_loader_appears_sitewide(
        self, django_server, browser,
    ):
        """Scenario 2: operator saves via Studio, loader appears for anon users."""
        _clear_analytics_setting()
        _create_staff_user("admin@test.com")
        admin_ctx = _auth_context(browser, "admin@test.com")
        admin_page = admin_ctx.new_page()

        # Open Settings, jump to the Analytics anchor.
        admin_page.goto(
            f"{django_server}/studio/settings/#analytics",
            wait_until="domcontentloaded",
        )
        assert admin_page.locator("#integration-analytics").is_visible()
        # Status badge starts as `not_configured` (all keys optional, but
        # there is no DB value yet).
        # The dashboard shows a status badge somewhere inside the card.

        # Fill the field and submit.
        analytics_card = admin_page.locator("#integration-analytics")
        analytics_card.locator(
            'input[name="GOOGLE_ANALYTICS_ID"]'
        ).fill("G-TEST123456")
        analytics_card.locator('button[type="submit"]').click()
        admin_page.wait_for_load_state("domcontentloaded")

        # Anonymous visitor sees the loader on /.
        anon_ctx = browser.new_context()
        anon_page = anon_ctx.new_page()
        anon_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        html_home = anon_page.content()
        assert "googletagmanager.com/gtag/js?id=G-TEST123456" in html_home
        # The ID also appears in the `gtag('config', ...)` call.
        assert html_home.count("G-TEST123456") >= 2

        # And on another public page (/pricing).
        anon_page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        html_pricing = anon_page.content()
        assert "G-TEST123456" in html_pricing

    def test_operator_clears_id_and_tracking_stops(self, django_server, browser):
        """Scenario 3: operator clears the field; next request has no GA."""
        _set_analytics_setting("G-TEST123456")
        _create_staff_user("admin@test.com")
        admin_ctx = _auth_context(browser, "admin@test.com")
        admin_page = admin_ctx.new_page()

        admin_page.goto(
            f"{django_server}/studio/settings/#analytics",
            wait_until="domcontentloaded",
        )
        analytics_card = admin_page.locator("#integration-analytics")
        analytics_card.locator(
            'input[name="GOOGLE_ANALYTICS_ID"]'
        ).fill("")
        analytics_card.locator('button[type="submit"]').click()
        admin_page.wait_for_load_state("domcontentloaded")

        # New anon context => no GA markup.
        anon_ctx = browser.new_context()
        anon_page = anon_ctx.new_page()
        anon_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        html_home = anon_page.content()
        assert 'googletagmanager.com' not in html_home
        assert 'googletagmanager' not in html_home
