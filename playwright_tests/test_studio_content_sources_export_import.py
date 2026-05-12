"""Playwright E2E for Studio content sources download / upload (issue #436).

Two scenarios cover the user flows that need a real browser:

1. Download the file, wipe the DB to simulate a fresh environment, and
   upload the file back — both ContentSource rows are restored with their
   original webhook secrets.
2. Upload a file with an unsupported ``format_version`` — a red error
   flash appears and the existing row is preserved (no DB writes happened).

Server-side coverage of validation, format_version handling, runtime-state
preservation, and access-control lives in
``studio/tests/test_content_sources_export_import.py`` per Rule 15 of
``_docs/testing-guidelines.md``.
"""

import json
import os
import tempfile

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _seed_two_sources():
    """Insert two ContentSource rows. Closes DB connection on the way out."""
    from integrations.models import ContentSource

    ContentSource.objects.all().delete()
    ContentSource.objects.create(
        repo_name="AI-Shipping-Labs/content",
        webhook_secret="secret-1",
        is_private=True,
        max_files=1000,
    )
    ContentSource.objects.create(
        repo_name="AI-Shipping-Labs/courses",
        webhook_secret="secret-2",
        is_private=True,
        max_files=500,
    )
    connection.close()


def _seed_one_source():
    """Insert one ContentSource row used by the format_version scenario."""
    from integrations.models import ContentSource

    ContentSource.objects.all().delete()
    ContentSource.objects.create(
        repo_name="AI-Shipping-Labs/content",
        webhook_secret="preserved-secret",
        is_private=True,
        max_files=1000,
    )
    connection.close()


def _wipe_sources():
    """Wipe ContentSource rows to simulate a fresh environment."""
    from integrations.models import ContentSource

    ContentSource.objects.all().delete()
    connection.close()


def _read_secrets():
    """Return ``{repo_name: webhook_secret}`` for every source."""
    from integrations.models import ContentSource

    out = dict(
        ContentSource.objects.values_list("repo_name", "webhook_secret")
    )
    connection.close()
    return out


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestContentSourcesDownloadAndUpload:
    """Bootstrap a fresh environment by exporting then importing."""

    def test_download_then_upload_restores_both_repos(
        self, django_server, browser,
    ):
        _seed_two_sources()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/sync/",
            wait_until="domcontentloaded",
        )

        # The Download anchor sits in the header alongside Sync All.
        download_link = page.locator('[data-testid="content-sources-download"]')
        sync_all_btn = page.locator("#sync-all-btn")
        assert download_link.count() == 1
        assert sync_all_btn.count() == 1
        assert "Download content sources" in download_link.inner_text()

        # Click Download → JSON file with the seeded values.
        with page.expect_download() as download_info:
            download_link.click()
        download = download_info.value
        assert download.suggested_filename.startswith(
            "aishippinglabs-content-sources-"
        )
        assert download.suggested_filename.endswith(".json")
        downloaded_path = download.path()
        with open(downloaded_path, "r") as f:
            payload = json.load(f)
        assert payload["format_version"] == 1
        secrets = {
            entry["repo_name"]: entry["webhook_secret"]
            for entry in payload["content_sources"]
        }
        assert secrets["AI-Shipping-Labs/content"] == "secret-1"
        assert secrets["AI-Shipping-Labs/courses"] == "secret-2"

        # Simulate a fresh environment.
        _wipe_sources()

        # Reload — the dashboard renders fine with no rows.
        page.goto(
            f"{django_server}/studio/sync/",
            wait_until="domcontentloaded",
        )

        # Upload the file we just downloaded.
        page.locator(
            'input[name="content_sources_file"]'
        ).set_input_files(downloaded_path)
        page.locator('[data-testid="content-sources-upload"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Success flash mentions counts and webhook secrets.
        body_text = page.locator("body").inner_text()
        assert "2 created, 0 updated" in body_text
        assert "webhook secrets" in body_text

        # DB now reflects the original secrets.
        secrets = _read_secrets()
        assert secrets.get("AI-Shipping-Labs/content") == "secret-1"
        assert secrets.get("AI-Shipping-Labs/courses") == "secret-2"


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestContentSourcesFormatVersionMismatch:
    """An unsupported ``format_version`` is rejected without DB writes."""

    def test_future_format_version_blocks_with_error_flash(
        self, django_server, browser,
    ):
        _seed_one_source()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # Write a JSON file with a future format_version.
        future_payload = {
            "format_version": 99,
            "content_sources": [
                {
                    "repo_name": "AI-Shipping-Labs/content",
                    "webhook_secret": "should-not-apply",
                }
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False,
        ) as fh:
            json.dump(future_payload, fh)
            future_path = fh.name

        try:
            page.goto(
                f"{django_server}/studio/sync/",
                wait_until="domcontentloaded",
            )
            page.locator(
                'input[name="content_sources_file"]'
            ).set_input_files(future_path)
            page.locator('[data-testid="content-sources-upload"]').click()
            page.wait_for_load_state("domcontentloaded")

            # Error flash mentions format_version and the supported value.
            body_text = page.locator("body").inner_text()
            assert "format_version" in body_text
            assert "1" in body_text

            # The existing row is still here with its original secret.
            secrets = _read_secrets()
            assert (
                secrets.get("AI-Shipping-Labs/content") == "preserved-secret"
            )
        finally:
            os.unlink(future_path)
