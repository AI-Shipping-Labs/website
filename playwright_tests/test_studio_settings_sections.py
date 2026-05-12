"""Playwright coverage for sectioned Studio settings navigation (issue #407)."""

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


def _seed_settings():
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    IntegrationSetting.objects.create(
        key="SLACK_BOT_TOKEN",
        value="xoxb-existing",
        is_secret=True,
        group="slack",
    )
    connection.close()


def _read_integration_values():
    from integrations.models import IntegrationSetting

    values = dict(IntegrationSetting.objects.values_list("key", "value"))
    connection.close()
    return values


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestStudioSettingsSections:
    def test_staff_jumps_directly_to_payment_settings(self, django_server, browser):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/#payments", wait_until="domcontentloaded")

        assert page.locator('[data-settings-section="payments"]').is_visible()
        assert page.locator("#integration-stripe").is_visible()
        assert page.locator("#auth-google").is_visible()
        assert page.locator('[data-section-nav-item="payments"]').get_attribute("aria-current") == "true"

    def test_staff_uses_section_nav_to_review_messaging(self, django_server, browser):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
        page.locator('[data-section-nav-item="messaging"]').click()

        page.wait_for_function("window.location.hash === '#messaging'")
        assert page.locator("#integration-ses").is_visible()
        assert page.locator("#integration-slack").is_visible()
        assert page.locator('[data-section-nav-item="messaging"]').get_attribute("aria-current") == "true"

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
        values = _read_integration_values()
        assert values["STRIPE_SECRET_KEY"] == "sk_section_test"
        assert values["SLACK_BOT_TOKEN"] == "xoxb-existing"
        assert "GITHUB_APP_ID" not in values
