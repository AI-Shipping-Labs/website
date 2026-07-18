"""Playwright coverage for the all-optional group badge fix (issue #938).

A Studio settings group whose keys are all ``optional`` used to render a
green "Configured" badge by vacuous truth even with nothing set. These
scenarios assert the corrected behavior on the ``analytics`` and
``calendly`` groups, and that a group with required keys (``zoom``) is
unaffected — including that all-optional groups never render the
nonsensical "Partial (x/0)" badge.
"""

import os
import re

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

# Issue #656: this module seeds the DB and injects a session cookie, so it
# is local-only and cannot run against the deployed dev environment.
pytestmark = pytest.mark.local_only


def _clear_settings():
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    connection.close()


def _badge_text(card):
    """Group header badge is the first span in the card."""
    return card.locator("span").first.inner_text().strip()


@pytest.mark.django_db(transaction=True)
class TestStudioSettingsAllOptionalBadge:
    def test_analytics_unset_shows_not_configured(self, django_server, browser):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#analytics",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-analytics")
        assert card.is_visible()
        # Group badge reads "Not configured", not "Configured".
        assert _badge_text(card) == "Not configured"
        # The GA field's per-key source badge reads "Source: not set".
        ga_row = card.locator('[data-field-key="GOOGLE_ANALYTICS_ID"]')
        assert ga_row.locator('[data-source-badge="none"]').count() == 1

    def test_saving_ga_id_flips_badge_to_configured(self, django_server, browser):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#analytics",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-analytics")
        assert _badge_text(card) == "Not configured"

        card.locator('input[name="GOOGLE_ANALYTICS_ID"]').fill("G-ABC123XYZ")
        card.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert re.search(
            r"Saved \d+ settings in Analytics\.",
            page.locator("body").inner_text(),
        )

        page.goto(
            f"{django_server}/studio/settings/#analytics",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-analytics")
        assert _badge_text(card) == "Configured"
        # The GA field now sources its value from the database.
        ga_row = card.locator('[data-field-key="GOOGLE_ANALYTICS_ID"]')
        assert ga_row.locator('[data-source-badge="db"]').count() == 1

    def test_default_only_retention_does_not_look_configured(
        self, django_server, browser,
    ):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#analytics",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-analytics")
        retention_row = card.locator(
            '[data-field-key="USER_ACTIVITY_RETENTION_DAYS"]'
        )
        # The retention value comes from the registry default (365).
        assert retention_row.locator('[data-source-badge="default"]').count() == 1
        retention_input = retention_row.locator(
            'input[name="USER_ACTIVITY_RETENTION_DAYS"]'
        )
        assert retention_input.input_value() == "365"
        # A default alone must not make the group look configured.
        assert _badge_text(card) == "Not configured"

    def test_calendly_unset_shows_not_configured(self, django_server, browser):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#calendly",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-calendly")
        assert card.is_visible()
        assert _badge_text(card) == "Not configured"

    def test_required_key_group_zoom_unaffected(self, django_server, browser):
        # A required-key group (Zoom) must keep rendering green "Configured"
        # when every required key is set -- the #938 all-optional fix must
        # not regress groups that have required keys.
        #
        # We assert only the DB-backed "Configured" state here because it is
        # env-independent (DB rows win over env in `_build_group_context`).
        # The "Partial (x/y)" rendering and the "never x/0" guarantee are
        # asserted authoritatively at the unit layer, which can clear the
        # ZOOM_* env vars the live Playwright server cannot:
        #   integrations.tests.test_settings.SettingsDashboardViewTest
        #     .test_dashboard_shows_status_partial            (Partial path)
        #     .test_all_optional_group_never_partial          (never x/0)
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        from integrations.models import IntegrationSetting

        for key in (
            "ZOOM_CLIENT_ID",
            "ZOOM_CLIENT_SECRET",
            "ZOOM_ACCOUNT_ID",
            "ZOOM_WEBHOOK_SECRET_TOKEN",
        ):
            IntegrationSetting.objects.create(key=key, value="val", group="zoom")
        connection.close()

        page.goto(
            f"{django_server}/studio/settings/#zoom",
            wait_until="domcontentloaded",
        )
        card = page.locator("#integration-zoom")
        assert _badge_text(card) == "Configured"
