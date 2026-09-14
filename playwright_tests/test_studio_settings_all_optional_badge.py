"""Playwright coverage for the Studio settings source badges (#938, #1584, #1627).

Issue #938 stopped all-optional groups from rendering a green "Configured"
badge by vacuous truth. The #1584 cutover then moved the dashboard onto the
community-base package views, which dropped group-level status badges
entirely in favour of per-key source badges (``data-source-badge`` with
``db`` / ``env`` / ``default``). These scenarios pin the per-key contract
that replaced the group badge: unset keys read ``default`` and the card
never claims "Configured", a saved override flips the key to ``db``, and
package-store values win over environment values.
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

# Issue #656: this module seeds the DB and injects a session cookie, so it
# is local-only and cannot run against the deployed dev environment.
pytestmark = pytest.mark.local_only


def _clear_settings():
    """Drop every stored override in both stores for a deterministic start."""
    from community_base.config.models import Setting

    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    Setting.objects.all().delete()
    connection.close()


def _field_badge(card, key):
    """Per-key source badge inside one group card."""
    return card.locator(f'[data-field-key="{key}"] [data-source-badge]')


def _claims_configured(card):
    """True when the card renders a standalone "Configured" badge."""
    return card.locator("span:text-is('Configured')").count() > 0


def _open_settings(page, django_server, section):
    page.goto(
        f"{django_server}/studio/settings/#{section}",
        wait_until="domcontentloaded",
    )


@pytest.mark.django_db(transaction=True)
class TestStudioSettingsAllOptionalBadge:
    def test_analytics_unset_shows_not_configured(self, django_server, browser):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        _open_settings(page, django_server, "analytics")
        card = page.locator("#integration-analytics")
        assert card.is_visible()
        # Nothing stored: the GA key reads its registry default and no
        # "Configured" claim renders anywhere in the card (#938).
        assert (
            _field_badge(card, "GOOGLE_ANALYTICS_ID").get_attribute(
                "data-source-badge"
            )
            == "default"
        )
        assert not _claims_configured(card)

    def test_saving_ga_id_flips_badge_to_configured(self, django_server, browser):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        _open_settings(page, django_server, "analytics")
        card = page.locator("#integration-analytics")
        card.locator('input[name="GOOGLE_ANALYTICS_ID"]').fill("G-ABC123XYZ")
        card.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # The package save view reports the group in lowercase.
        assert (
            "Saved analytics settings."
            in page.locator("body").inner_text()
        )
        card = page.locator("#integration-analytics")
        # The stored override flips the GA key's badge to the db source.
        assert (
            _field_badge(card, "GOOGLE_ANALYTICS_ID").get_attribute(
                "data-source-badge"
            )
            == "db"
        )

    def test_default_only_retention_does_not_look_configured(self, django_server, browser):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        _open_settings(page, django_server, "analytics")
        card = page.locator("#integration-analytics")
        retention_row = card.locator(
            '[data-field-key="USER_ACTIVITY_RETENTION_DAYS"]'
        )
        # The retention value comes from the registry default (365) and its
        # badge says so; a default alone must not look configured.
        assert (
            retention_row.locator('[data-source-badge="default"]').count() == 1
        )
        retention_input = retention_row.locator(
            'input[name="USER_ACTIVITY_RETENTION_DAYS"]'
        )
        assert retention_input.input_value() == "365"
        assert not _claims_configured(card)

    def test_calendly_unset_shows_not_configured(self, django_server, browser):
        _clear_settings()
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # The calendly group lives in the "content" section of the
        # sectioned dashboard; the section id is the anchor that works.
        _open_settings(page, django_server, "content")
        card = page.locator("#integration-calendly")
        assert card.is_visible()
        assert not _claims_configured(card)

    def test_required_key_group_zoom_unaffected(self, django_server, browser):
        # Keys stored through the package service (the post-cutover
        # override store) must surface as the db source even when an
        # environment variable for the same key exists.
        from community_base.config import service

        _clear_settings()
        for key in (
            "ZOOM_CLIENT_ID",
            "ZOOM_CLIENT_SECRET",
            "ZOOM_ACCOUNT_ID",
            "ZOOM_WEBHOOK_SECRET_TOKEN",
        ):
            service.set(key, "val", actor_ref="test", reason="playwright seed")
        connection.close()

        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        _open_settings(page, django_server, "content")
        card = page.locator("#integration-zoom")
        assert card.is_visible()
        for key in (
            "ZOOM_CLIENT_ID",
            "ZOOM_CLIENT_SECRET",
            "ZOOM_ACCOUNT_ID",
            "ZOOM_WEBHOOK_SECRET_TOKEN",
        ):
            assert (
                _field_badge(card, key).get_attribute("data-source-badge")
                == "db"
            )
