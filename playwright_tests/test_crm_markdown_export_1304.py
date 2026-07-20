"""Staff CRM Markdown download journeys for issue #1304."""

import os

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _seed_record(member_email, staff_email, *, with_note):
    from django.db import connection

    from accounts.models import User
    from crm.models import CRMRecord
    from plans.models import InterviewNote

    member = User.objects.get(email=member_email)
    staff = User.objects.get(email=staff_email)
    record, _ = CRMRecord.objects.update_or_create(
        user=member,
        defaults={
            "created_by": staff,
            "summary": "Playwright relationship summary" if with_note else "",
            "next_steps": "Ship the hand-off" if with_note else "",
        },
    )
    if with_note:
        InterviewNote.objects.update_or_create(
            member=member,
            body="PLAYWRIGHT_INTERNAL_CRM_NOTE",
            defaults={
                "visibility": "internal",
                "created_by": staff,
            },
        )
    record_id = record.pk
    connection.close()
    return record_id


@pytest.mark.django_db(transaction=True)
class TestCRMMarkdownExport:
    @pytest.mark.core
    def test_staff_downloads_complete_private_hand_off(self, django_server, browser):
        staff_email = "crm-export-staff@test.com"
        member_email = "crm-export-member@test.com"
        create_staff_user(staff_email)
        create_user(member_email, first_name="CRM Export")
        record_id = _seed_record(member_email, staff_email, with_note=True)

        context = auth_context(browser, staff_email)
        page = context.new_page()
        detail_url = f"{django_server}/studio/crm/{record_id}/"
        page.goto(detail_url, wait_until="domcontentloaded")
        action = page.get_by_role("link", name="Download Markdown")
        action.wait_for(state="visible")

        with page.expect_download() as download_info:
            action.click()
        download = download_info.value
        assert download.suggested_filename == f"crm-record-{record_id}.md"
        assert page.url == detail_url
        with open(download.path(), encoding="utf-8") as downloaded:
            markdown = downloaded.read()
        assert "## Profile & account" in markdown
        assert "Playwright relationship summary" in markdown
        assert r"PLAYWRIGHT\_INTERNAL\_CRM\_NOTE" in markdown
        assert markdown.endswith("\n") and not markdown.endswith("\n\n")
        context.close()

    @pytest.mark.core
    def test_mobile_action_is_visible_and_does_not_overflow(self, django_server, browser):
        staff_email = "crm-mobile-staff@test.com"
        member_email = "crm-mobile-member@test.com"
        create_staff_user(staff_email)
        create_user(member_email, first_name="Mobile")
        record_id = _seed_record(member_email, staff_email, with_note=False)

        context = auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        detail_url = f"{django_server}/studio/crm/{record_id}/"
        page.goto(detail_url, wait_until="domcontentloaded")
        action = page.get_by_role("link", name="Download Markdown")
        action.wait_for(state="visible")
        assert page.get_by_label("More actions").count() == 1
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )

        with page.expect_download() as download_info:
            action.click()
        assert download_info.value.suggested_filename == f"crm-record-{record_id}.md"
        assert page.url == detail_url
        context.close()
