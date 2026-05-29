"""Playwright coverage for the Observability (Logfire) settings group (issue #813).

Covers the staff-facing config flow on the Studio settings dashboard: the
group is discoverable with its three keys, the token persists masked after
a save + reload, and the per-key (?) docs link points at the observability
integration doc anchor on GitHub.
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

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

FAKE_TOKEN = "pylf_fake_playwright_token"


def _clear_settings():
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestStudioSettingsObservability:
    def test_staff_finds_observability_group_with_all_three_keys(
        self, django_server, browser,
    ):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#observability",
            wait_until="domcontentloaded",
        )

        card = page.locator("#integration-observability")
        assert card.is_visible()
        assert card.locator('[data-field-key="LOGFIRE_TOKEN"]').count() == 1
        assert card.locator('[data-field-key="LOGFIRE_ENABLED"]').count() == 1
        assert card.locator('[data-field-key="LOGFIRE_ENVIRONMENT"]').count() == 1
        # With nothing configured the group is not fully configured: the
        # required token is empty, so the badge is not the green "Configured".
        badge_text = card.locator("span").first.inner_text()
        assert "Configured" != badge_text.strip()

    def test_staff_configures_token_and_it_is_stored_masked(
        self, django_server, browser,
    ):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#observability",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-observability")
        token_input = card.locator('input[name="LOGFIRE_TOKEN"]')
        # Secret fields render as password inputs (masked).
        assert token_input.get_attribute("type") == "password"
        token_input.fill(FAKE_TOKEN)
        card.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert "Observability settings saved" in page.locator("body").inner_text()

        # Reload — the value is still masked (a password input), never shown
        # as plaintext text, exactly like other secret keys.
        page.goto(
            f"{django_server}/studio/settings/#observability",
            wait_until="domcontentloaded",
        )
        token_input = page.locator(
            "#integration-observability input[name='LOGFIRE_TOKEN']"
        )
        assert token_input.get_attribute("type") == "password"

    def test_staff_reads_inline_docs_link_for_the_token(
        self, django_server, browser,
    ):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#observability",
            wait_until="domcontentloaded",
        )
        docs_link = page.locator(
            "#integration-observability [data-docs-link='LOGFIRE_TOKEN']"
        )
        assert docs_link.count() == 1
        href = docs_link.get_attribute("href")
        assert href == (
            "https://github.com/AI-Shipping-Labs/website/blob/main/"
            "_docs/integrations/observability.md#logfire_token"
        )
