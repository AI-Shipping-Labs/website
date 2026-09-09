"""Playwright coverage for the Observability (Logfire) settings group (issue #813).

Covers the staff-facing config flow on the Studio settings dashboard: the
group is discoverable with its three keys, the token persists masked after
a save + reload, and the per-key (?) docs link points at the observability
integration doc anchor on GitHub.
"""

import os
import re
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import create_user as _create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

FAKE_TOKEN = "pylf_fake_playwright_token"
RESTART_HINT = (
    "Takes effect on the next web and worker process start. "
    "Saving does not reconfigure this process."
)
RESTART_MESSAGE = (
    "Observability changes apply after you restart the web and worker processes."
)
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1539")


def _clear_settings():
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    connection.close()


def _seed_observability(*, enabled=True):
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key="LOGFIRE_ENABLED",
        defaults={
            "value": "true" if enabled else "false",
            "group": "observability",
        },
    )
    IntegrationSetting.objects.update_or_create(
        key="LOGFIRE_TOKEN",
        defaults={
            "value": FAKE_TOKEN,
            "is_secret": True,
            "group": "observability",
        },
    )
    IntegrationSetting.objects.update_or_create(
        key="LOGFIRE_ENVIRONMENT",
        defaults={"value": "production", "group": "observability"},
    )
    connection.close()


def _disable_analytics_consent_prompt(context, django_server):
    context.add_cookies([{
        "name": "aslab_analytics_consent",
        "value": "denied",
        "url": django_server,
    }])


@pytest.mark.django_db(transaction=True)
class TestStudioSettingsObservability:
    def test_staff_finds_observability_group_with_all_three_keys(
        self, django_server, browser,
    ):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        _disable_analytics_consent_prompt(context, django_server)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#observability",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-observability")
        assert card.is_visible()
        for key in (
            "LOGFIRE_ENABLED",
            "LOGFIRE_TOKEN",
            "LOGFIRE_ENVIRONMENT",
        ):
            field = card.locator(f'[data-field-key="{key}"]')
            assert field.count() == 1
            assert field.locator(
                f'[data-requires-restart-badge="{key}"]'
            ).inner_text() == "Restart required"
            assert field.locator(
                f'[data-requires-restart-hint="{key}"]'
            ).inner_text() == RESTART_HINT

        site_field = page.locator(
            '#integration-site [data-field-key="SITE_BASE_URL"]'
        )
        assert site_field.count() == 1
        assert site_field.locator('[data-requires-restart-badge]').count() == 0

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        card.screenshot(path=SCREENSHOT_DIR / "restart-badges-before-save.png")

    def test_staff_configures_token_and_it_is_stored_masked(
        self, django_server, browser,
    ):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        _disable_analytics_consent_prompt(context, django_server)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#observability",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-observability")
        token_input = card.locator('input[name="LOGFIRE_TOKEN"]')
        # Secret fields render as password inputs (masked).
        assert token_input.get_attribute("type") == "password"
        card.locator('input[name="LOGFIRE_ENABLED"]').check()
        token_input.fill(FAKE_TOKEN)
        card.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert re.search(
            r"Saved \d+ settings in Observability\.",
            page.locator("body").inner_text(),
        )
        assert RESTART_MESSAGE in page.locator("body").inner_text()
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=SCREENSHOT_DIR / "restart-message-after-save.png",
            full_page=True,
        )

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
        assert page.locator(
            "#integration-observability [data-requires-restart-badge]"
        ).count() == 3

    @browser_journey
    def test_staff_disables_logfire_and_is_still_told_to_restart(
        self, django_server, browser,
    ):
        _clear_settings()
        _seed_observability(enabled=True)
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#observability",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-observability")
        enabled = card.locator('input[name="LOGFIRE_ENABLED"]')
        assert enabled.is_checked()
        enabled.uncheck()
        card.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        body_text = page.locator("body").inner_text()
        assert "Saved 3 settings in Observability." in body_text
        assert RESTART_MESSAGE in body_text
        assert "already stopped" not in body_text.lower()
        assert not page.locator(
            '#integration-observability input[name="LOGFIRE_ENABLED"]'
        ).is_checked()

    @browser_journey
    def test_saving_site_settings_does_not_show_logfire_restart_message(
        self, django_server, browser,
    ):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#site",
            wait_until="domcontentloaded",
        )
        site_card = page.locator("#integration-site")
        site_card.locator('input[name="SITE_BASE_URL"]').fill(
            "https://settings.example.test"
        )
        site_card.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        body_text = page.locator("body").inner_text()
        assert re.search(r"Saved \d+ settings in Site\.", body_text)
        assert RESTART_MESSAGE not in body_text

    @browser_journey
    def test_non_staff_member_cannot_open_observability_settings(
        self, django_server, browser,
    ):
        _clear_settings()
        _create_user("free@test.com", tier_slug="free", is_staff=False)
        context = _auth_context(browser, "free@test.com")
        page = context.new_page()

        response = page.goto(
            f"{django_server}/studio/settings/#observability",
            wait_until="domcontentloaded",
        )

        assert response.status == 403
        assert page.locator("#integration-observability").count() == 0
        assert page.locator('[data-field-key="LOGFIRE_TOKEN"]').count() == 0

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
