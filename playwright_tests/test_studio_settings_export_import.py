"""Playwright E2E for Studio settings export / import (issue #323).

Covers the bootstrap workflow from the issue Acceptance Criteria, adapted
to the package settings contract introduced by the #1584 cutover (and
re-pinned here after #1627):

1. Staff lands on /studio/settings/ and sees both controls.
2. Exporting downloads a JSON file shaped ``{"settings": {key: value}}``
   with the stored values in plaintext and secret values replaced by the
   package redaction sentinel.
3. After wiping the rows (simulating a fresh environment), importing the
   downloaded file repopulates the package setting store for every
   non-redacted entry; redacted secrets are skipped by design so an
   export never carries secrets into another environment.

The pre-cutover flow also round-tripped SocialApp auth providers; the
package view no longer exports them (auth providers are managed through
their per-provider forms on the same dashboard).
"""

import json
import os

import pytest
from community_base.kernel.redaction import REDACTED

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

ROUND_TRIP_KEYS = [
    "STRIPE_SECRET_KEY",
    "STRIPE_CUSTOMER_PORTAL_URL",
    "EVENT_DISPLAY_TIMEZONE",
]
SECRET_KEY = "STRIPE_SECRET_KEY"
PLAIN_KEYS = ["STRIPE_CUSTOMER_PORTAL_URL", "EVENT_DISPLAY_TIMEZONE"]


def _seed_settings():
    """Store round-trip values through the package override store.

    Closes the DB connection afterwards so the running server thread can
    read fresh state.
    """
    from community_base.config import service

    service.set(
        SECRET_KEY,
        "sk_live_e2e",
        actor_ref="test",
        reason="playwright seed",
    )
    service.set(
        "STRIPE_CUSTOMER_PORTAL_URL",
        "https://billing.example.test/portal-e2e",
        actor_ref="test",
        reason="playwright seed",
    )
    service.set(
        "EVENT_DISPLAY_TIMEZONE",
        "Europe/Berlin",
        actor_ref="test",
        reason="playwright seed",
    )
    connection.close()


def _wipe_settings():
    """Wipe the round-trip rows to simulate a fresh environment."""
    from community_base.config.models import Setting

    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    Setting.objects.filter(key__in=ROUND_TRIP_KEYS).delete()
    connection.close()


def _read_settings():
    """Return the stored package values for the non-secret round-trip keys."""
    from community_base.config.models import Setting

    values = dict(
        Setting.objects.filter(key__in=PLAIN_KEYS).values_list("key", "value")
    )
    connection.close()
    return values


@pytest.mark.django_db(transaction=True)
class TestSettingsDownloadAndUpload:
    """Operator copies Studio settings from one environment to another."""

    def test_download_then_upload_round_trip(self, django_server, browser):
        _seed_settings()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # 1. Both controls visible: the header download link and the
        #    import card's submit button.
        page.goto(
            f"{django_server}/studio/settings/",
            wait_until="domcontentloaded",
        )
        download_link = page.locator('[data-testid="settings-download"]')
        upload_button = page.locator('[data-testid="settings-upload"]')
        assert download_link.count() == 1
        assert upload_button.count() == 1
        assert "Download settings" in download_link.inner_text()
        assert "Import JSON" in upload_button.inner_text()

        # 2. Export → JSON payload in the package shape; the secret is
        #    redacted, the plaintext values come through.
        with page.expect_download() as download_info:
            download_link.click()
        download = download_info.value
        assert download.suggested_filename == "community-base-settings.json"
        downloaded_path = download.path()
        with open(downloaded_path, "r") as f:
            payload = json.load(f)
        assert set(payload.keys()) == {"settings"}
        settings_payload = payload["settings"]
        assert settings_payload[SECRET_KEY] == REDACTED
        assert (
            settings_payload["STRIPE_CUSTOMER_PORTAL_URL"]
            == "https://billing.example.test/portal-e2e"
        )
        assert settings_payload["EVENT_DISPLAY_TIMEZONE"] == "Europe/Berlin"

        # 3. Simulate a fresh environment by wiping the rows.
        _wipe_settings()

        # 4. Back on the dashboard, upload the downloaded file. The page
        #    script reads the chosen file into the payload textarea and
        #    enables the submit button once it lands.
        page.goto(
            f"{django_server}/studio/settings/",
            wait_until="domcontentloaded",
        )
        page.locator('input[name="settings_file"]').set_input_files(
            downloaded_path
        )
        page.wait_for_function(
            """() => {
                const el = document.querySelector('textarea[name="payload"]');
                return el && el.value.includes('"settings"');
            }"""
        )
        upload_button.click()
        page.wait_for_load_state("domcontentloaded")

        # Success flash visible somewhere on the dashboard after redirect.
        body_text = page.locator("body").inner_text()
        assert "Settings imported" in body_text

        # The package store reflects the exported values again; the
        # redacted secret is deliberately not restored.
        restored = _read_settings()
        assert (
            restored.get("STRIPE_CUSTOMER_PORTAL_URL")
            == "https://billing.example.test/portal-e2e"
        )
        assert restored.get("EVENT_DISPLAY_TIMEZONE") == "Europe/Berlin"
        from community_base.config.models import Setting

        assert not Setting.objects.filter(key=SECRET_KEY).exists()
        connection.close()
