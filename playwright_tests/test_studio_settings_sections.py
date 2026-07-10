"""Playwright coverage for Studio settings section navigation and filtering."""

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


def _seed_settings():
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    IntegrationSetting.objects.create(
        key="SLACK_BOT_TOKEN",
        value="xoxb-existing",
        is_secret=True,
        group="slack",
    )
    IntegrationSetting.objects.create(
        key="SLACK_ENVIRONMENT",
        value="production",
        is_secret=False,
        group="slack",
    )
    connection.close()


def _read_integration_values():
    from integrations.models import IntegrationSetting

    values = dict(IntegrationSetting.objects.values_list("key", "value"))
    connection.close()
    return values


@pytest.mark.django_db(transaction=True)
class TestStudioSettingsSections:
    def test_staff_focuses_on_auth_section_by_default(self, django_server, browser):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")

        assert page.locator('[data-settings-section="auth"]').is_visible()
        assert page.locator("#auth-google").is_visible()
        assert page.locator('[data-settings-section="payments"]').is_hidden()
        assert page.locator("#integration-stripe").is_hidden()
        assert page.locator('[data-settings-section="messaging"]').is_hidden()
        assert page.locator("#integration-slack").is_hidden()
        assert (
            page.locator('[data-section-nav-item="auth"]').get_attribute("aria-current")
            == "page"
        )
        assert (
            page.locator('[data-section-nav-item="auth"]').get_attribute("aria-selected")
            == "true"
        )

    def test_staff_jumps_directly_to_payment_settings(self, django_server, browser):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/#payments", wait_until="domcontentloaded")

        assert page.locator('[data-settings-section="payments"]').is_visible()
        assert page.locator("#integration-stripe").is_visible()
        assert page.locator("#auth-google").is_hidden()
        assert page.locator("#integration-slack").is_hidden()
        assert (
            page.locator('[data-section-nav-item="payments"]').get_attribute("aria-current")
            == "page"
        )

    def test_staff_opens_card_level_hash(self, django_server, browser):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/#auth-google", wait_until="domcontentloaded")

        assert page.locator('[data-settings-section="auth"]').is_visible()
        assert page.locator("#auth-google").is_visible()
        assert page.locator('[data-settings-section="payments"]').is_hidden()
        assert page.locator("#integration-stripe").is_hidden()

    def test_staff_uses_section_nav_to_review_messaging(self, django_server, browser):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
        page.locator('[data-section-nav-item="messaging"]').click()

        page.wait_for_function("window.location.hash === '#messaging'")
        assert page.locator("#integration-ses").is_visible()
        assert page.locator("#integration-slack").is_visible()
        assert page.locator("#integration-stripe").is_hidden()
        assert (
            page.locator('[data-section-nav-item="messaging"]').get_attribute("aria-current")
            == "page"
        )
        assert (
            page.locator('[data-section-nav-item="messaging"]').get_attribute("aria-selected")
            == "true"
        )

    def test_staff_saves_payments_without_touching_messaging(self, django_server, browser):
        _seed_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/#payments", wait_until="domcontentloaded")
        stripe_card = page.locator("#integration-stripe")
        stripe_card.locator('input[name="STRIPE_SECRET_KEY"]').fill("sk_section_test")
        stripe_card.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        body_text = page.locator("body").inner_text()
        assert "Stripe settings saved" in body_text
        assert page.locator('[data-settings-section="payments"]').is_visible()
        assert page.locator("#integration-stripe").is_visible()
        assert page.locator("#integration-slack").is_hidden()
        values = _read_integration_values()
        assert values["STRIPE_SECRET_KEY"] == "sk_section_test"
        assert values["SLACK_BOT_TOKEN"] == "xoxb-existing"
        assert values["SLACK_ENVIRONMENT"] == "production"
        assert "GITHUB_APP_ID" not in values

    def test_staff_filters_known_key_across_sections(self, django_server, browser):
        _seed_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
        page.locator('[data-settings-filter]').fill("slack_bot_token")

        assert page.locator('[data-settings-section="messaging"]').is_visible()
        assert page.locator("#integration-slack").is_visible()
        assert page.locator('[data-field-key="SLACK_BOT_TOKEN"]').is_visible()
        assert page.locator('[data-field-key="SLACK_ENVIRONMENT"]').is_hidden()
        assert page.locator("#integration-stripe").is_hidden()
        assert page.locator('[data-settings-filter-empty]').is_hidden()

    def test_staff_filters_by_description_and_keeps_source_badges(
        self, django_server, browser,
    ):
        _seed_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
        page.locator('[data-settings-filter]').fill("webhook")

        assert page.locator('[data-settings-section="payments"]').is_visible()
        assert page.locator("#integration-stripe").is_visible()
        assert page.locator('[data-field-key="STRIPE_WEBHOOK_SECRET"]').is_visible()
        assert page.locator(
            '[data-field-key="STRIPE_WEBHOOK_SECRET"] [data-source-badge]',
        ).is_visible()

    def test_staff_filters_oauth_provider(self, django_server, browser):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
        page.locator('[data-settings-filter]').fill("Google")

        assert page.locator('[data-settings-section="auth"]').is_visible()
        assert page.locator("#auth-google").is_visible()
        assert page.locator("#auth-github").is_hidden()
        assert page.locator("#integration-stripe").is_hidden()

    def test_staff_sees_empty_state_for_unknown_filter(self, django_server, browser):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/#messaging", wait_until="domcontentloaded")
        page.locator('[data-settings-filter]').fill("NO_SUCH_SETTING_KEY_123")

        assert page.locator('[data-settings-filter-empty]').is_visible()
        assert page.locator("#integration-slack").is_hidden()
        assert page.locator("#integration-stripe").is_hidden()

        page.locator('[data-settings-filter-clear]').click()

        assert page.locator('[data-settings-section="messaging"]').is_visible()
        assert page.locator("#integration-slack").is_visible()
        assert page.locator("#integration-stripe").is_hidden()

    def test_staff_saves_filtered_group_without_losing_hidden_sibling_values(
        self, django_server, browser,
    ):
        _seed_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
        page.locator('[data-settings-filter]').fill("SLACK_BOT_TOKEN")
        slack_card = page.locator("#integration-slack")
        slack_card.locator('input[name="SLACK_BOT_TOKEN"]').fill("xoxb-updated")
        slack_card.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        values = _read_integration_values()
        assert values["SLACK_BOT_TOKEN"] == "xoxb-updated"
        assert values["SLACK_ENVIRONMENT"] == "production"
        assert page.locator('[data-settings-section="messaging"]').is_visible()
        assert page.locator('[data-field-key="SLACK_BOT_TOKEN"] [data-source-badge="db"]').is_visible()
