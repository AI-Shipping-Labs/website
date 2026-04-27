"""Playwright E2E for Studio settings download / upload (issue #323).

Covers the bootstrap workflow from the issue Acceptance Criteria:

1. Staff lands on /studio/settings/ and sees both buttons.
2. Clicking Download triggers a JSON file download with format_version: 1
   and the populated values in plaintext.
3. After wiping the rows (simulating a fresh environment), uploading the
   downloaded file repopulates IntegrationSetting + SocialApp with the
   original values.
"""

import json
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
    """Insert a couple of integration settings + a Google SocialApp.

    Closes the DB connection afterwards so the running server thread can
    read fresh state.
    """
    from allauth.socialaccount.models import SocialApp

    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    SocialApp.objects.all().delete()

    IntegrationSetting.objects.create(
        key="STRIPE_SECRET_KEY", value="sk_live_e2e",
        is_secret=True, group="stripe",
    )
    IntegrationSetting.objects.create(
        key="STRIPE_PUBLISHABLE_KEY", value="pk_live_e2e",
        is_secret=False, group="stripe",
    )
    SocialApp.objects.create(
        provider="google", name="Google",
        client_id="goog-e2e-id", secret="goog-e2e-secret",
    )
    connection.close()


def _wipe_settings():
    """Wipe IntegrationSetting + SocialApp to simulate a fresh environment."""
    from allauth.socialaccount.models import SocialApp

    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    SocialApp.objects.all().delete()
    connection.close()


def _read_settings():
    """Return a dict of the seeded keys + the google SocialApp values."""
    from allauth.socialaccount.models import SocialApp

    from integrations.models import IntegrationSetting

    integration_values = dict(
        IntegrationSetting.objects.values_list("key", "value")
    )
    google = SocialApp.objects.filter(provider="google").first()
    google_creds = (
        (google.client_id, google.secret) if google else (None, None)
    )
    connection.close()
    return integration_values, google_creds


@pytest.mark.django_db(transaction=True)
class TestSettingsDownloadAndUpload:
    """Operator copies Studio settings from one environment to another."""

    def test_download_then_upload_round_trip(self, django_server, browser):
        _seed_settings()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # 1. Both buttons visible in the page header.
        page.goto(
            f"{django_server}/studio/settings/",
            wait_until="domcontentloaded",
        )
        download_link = page.locator('[data-testid="settings-download"]')
        upload_button = page.locator('[data-testid="settings-upload"]')
        assert download_link.count() == 1
        assert upload_button.count() == 1
        assert "Download settings" in download_link.inner_text()
        assert "Upload settings" in upload_button.inner_text()

        # 2. Click Download → JSON file with the seeded values.
        with page.expect_download() as download_info:
            download_link.click()
        download = download_info.value
        assert download.suggested_filename.startswith("aishippinglabs-settings-")
        assert download.suggested_filename.endswith(".json")
        downloaded_path = download.path()
        with open(downloaded_path, "r") as f:
            payload = json.load(f)
        assert payload["format_version"] == 1
        keys = {entry["key"]: entry["value"] for entry in payload["integration_settings"]}
        assert keys["STRIPE_SECRET_KEY"] == "sk_live_e2e"
        assert keys["STRIPE_PUBLISHABLE_KEY"] == "pk_live_e2e"
        providers = {p["provider"]: p for p in payload["auth_providers"]}
        assert providers["google"]["client_id"] == "goog-e2e-id"
        assert providers["google"]["secret"] == "goog-e2e-secret"

        # 3. Simulate a fresh environment by wiping the DB.
        _wipe_settings()

        # 4. Back on the dashboard, upload the downloaded file.
        page.goto(
            f"{django_server}/studio/settings/",
            wait_until="domcontentloaded",
        )
        page.locator('input[name="settings_file"]').set_input_files(downloaded_path)
        page.locator('[data-testid="settings-upload"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Success flash visible somewhere on the dashboard after redirect.
        body_text = page.locator("body").inner_text()
        assert "Settings imported" in body_text

        # DB now reflects the original values.
        integration_values, google_creds = _read_settings()
        assert integration_values.get("STRIPE_SECRET_KEY") == "sk_live_e2e"
        assert integration_values.get("STRIPE_PUBLISHABLE_KEY") == "pk_live_e2e"
        assert google_creds == ("goog-e2e-id", "goog-e2e-secret")
