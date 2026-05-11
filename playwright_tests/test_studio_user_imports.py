import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _seed_import_batches():
    from accounts.models import ImportBatch

    ImportBatch.objects.all().delete()
    ImportBatch.objects.create(
        source="stripe",
        dry_run=False,
        status=ImportBatch.STATUS_COMPLETED,
        users_created=1,
        emails_queued=1,
        summary="Stripe import complete",
    )
    ImportBatch.objects.create(
        source="course_db",
        dry_run=True,
        status=ImportBatch.STATUS_COMPLETED,
        users_created=3,
        users_skipped=1,
        errors=[{"kind": "missing_email", "row": 4, "message": "Missing email"}],
        summary="Course-db dry-run complete",
    )
    connection.close()


def _seed_import_schedules():
    from django.core.management import call_command

    call_command("setup_schedules")
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestStudioUserImports:
    def test_staff_reviews_imports_and_opens_new_form(self, django_server, browser):
        _ensure_tiers()
        staff_email = "imports-admin@test.com"
        _create_staff_user(staff_email)
        _seed_import_batches()
        _seed_import_schedules()

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        # Issue #570 nested Imports inside the People > Users sub-group
        # (the old label was ``User imports``; the new label is just
        # ``Imports``). People is collapsed by default, so expand it and
        # then expand the Users sub-group chevron before clicking.
        page.locator(
            'aside#studio-sidebar [aria-controls="studio-section-people"]'
        ).click()
        page.locator(
            'aside#studio-sidebar [data-studio-users-toggle]'
        ).click()
        page.locator(
            '#studio-users-children a[href="/studio/imports/"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith("/studio/imports/")
        assert "Scheduled imports" in page.content()
        assert "Slack workspace" in page.content()
        assert "03:00 UTC" in page.content()
        assert "Stripe customers" in page.content()
        assert "03:30 UTC" in page.content()
        assert "Course database · 03:" not in page.content()

        page.locator("#source").select_option("course_db")
        page.locator("#dry_run").select_option("yes")
        page.get_by_role("button", name="Filter").click()
        page.wait_for_load_state("domcontentloaded")

        assert "Course database" in page.content()
        assert "dry-run" in page.content()
        assert "Missing email" not in page.content()
        assert "Stripe import complete" not in page.content()

        page.get_by_role("link", name="New import").click()
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("#id_dry_run").is_checked()
        assert page.locator("#id_send_welcome").is_disabled()
        page.locator("#id_source").select_option("course_db")
        assert page.locator("#csv-field").is_visible()
        assert "This will create real user accounts and send welcome emails. Continue?" in page.content()

        context.close()
