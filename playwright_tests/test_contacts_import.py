"""Playwright E2E test for the Studio contacts CSV importer (issue #356).

Single happy-path scenario:

1. Staff user opens /studio/users/ and clicks "Import contacts".
2. They upload a CSV with a header named "Email" and a mix of new + existing
   email rows (plus one malformed row to exercise the warning path).
3. The confirm page auto-detects the email column, the operator types a tag
   and picks the Main tier.
4. The result page shows created/updated/malformed counts.
5. Back on the users list, the existing user shows the override tier.

Usage:
    uv run pytest playwright_tests/test_contacts_import.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _clear_users_except_staff(staff_email):
    """Drop every user except the named staff account so the listing is
    deterministic for the import assertions."""
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestOperatorImportsContactsCsv:
    """Operator imports a CSV, applies a tag and tier, sees per-row counts."""

    def test_happy_path(self, django_server, browser, tmp_path):
        _ensure_tiers()
        staff_email = "import-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("existing@test.com", tier_slug="free")

        # Build the CSV file the operator will upload.
        csv_path = tmp_path / "contacts.csv"
        csv_path.write_text(
            "Name,Email,Source\n"
            "Ada,existing@test.com,event-2026\n"
            "Grace,new1@test.com,event-2026\n"
            "Linus,not-an-email,event-2026\n"
        )

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # 1. Navigate to /studio/users/ and click the Import contacts link.
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="user-import-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/users/import/" in page.url
        assert page.locator('[data-testid="import-file-input"]').count() == 1

        # 2. Upload the CSV.
        page.locator('[data-testid="import-file-input"]').set_input_files(
            str(csv_path)
        )
        page.locator('[data-testid="import-upload-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Confirm page: header columns + auto-detected Email column.
        preview = page.locator('[data-testid="import-preview"]')
        assert preview.count() == 1
        # The Email column dropdown defaults to the column literally named "Email".
        selected_value = page.locator(
            '[data-testid="import-email-column"]'
        ).evaluate("el => el.value")
        assert selected_value == "Email"

        # 3. Type the tag and pick the Main tier.
        page.locator('[data-testid="import-tag-input"]').fill("event-2026-signup")
        # Pick the Main tier by visible label.
        page.locator('[data-testid="import-tier-select"]').select_option(
            label="Main",
        )

        # 4. Submit.
        page.locator('[data-testid="import-confirm-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # 5. Assert the result counts.
        assert page.locator(
            '[data-testid="import-result-created"]'
        ).inner_text().strip() == "1"
        assert page.locator(
            '[data-testid="import-result-updated"]'
        ).inner_text().strip() == "1"
        assert page.locator(
            '[data-testid="import-result-malformed"]'
        ).inner_text().strip() == "1"
        # The warnings table mentions the malformed value.
        warnings_html = page.locator(
            '[data-testid="import-warnings-table"]'
        ).inner_text()
        assert "not-an-email" in warnings_html

        # 6. Existing user now reports the override tier on the list page.
        page.goto(
            f"{django_server}/studio/users/?q=existing@test.com",
            wait_until="domcontentloaded",
        )
        user_row = page.locator("tr", has_text="existing@test.com")
        assert user_row.count() == 1
        assert "(override)" not in user_row.inner_text()
        tier_pill = user_row.locator('[data-testid="user-list-tier-pill"]')
        assert tier_pill.inner_text().strip() == "Main"
        assert tier_pill.get_attribute("data-tier") == "main"
        assert user_row.locator(
            '[data-testid="user-list-tier-override-pill"]'
        ).inner_text().strip() == "Override"

        # 7. The newly-created user is listed.
        page.goto(
            f"{django_server}/studio/users/?q=new1@test.com",
            wait_until="domcontentloaded",
        )
        assert "new1@test.com" in page.content()

        context.close()
