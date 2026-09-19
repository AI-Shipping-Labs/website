"""Browser coverage for the Studio email-template single-line construct guard.

Operator copy stored in ``EmailTemplateOverride`` is compiled by the same
``django.template.Template`` call as the shipped ``.md`` files, so a note whose
closing marker lands on a later line is never lexed and its raw source ships to
the recipient. A file lint cannot see database rows, so the edit view rejects
the save instead. These journeys drive the real editor: the rejection, the
recovery, the fact that a rejected save never partially writes, and that the
pre-existing reset control still works afterwards.
"""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

STAFF_EMAIL = "email-template-guard@test.com"

SPLIT_NOTE_BODY = (
    "Hi {{ user_name }}\n"
    "{# note about the call to action\n"
    "   continued on a second line #}\n"
    "Thanks for joining us."
)

BLOCK_NOTE_BODY = (
    "Hi {{ user_name }}\n"
    "{% comment %}\n"
    "note about the call to action\n"
    "continued on a second line\n"
    "{% endcomment %}\n"
    "Thanks for joining us."
)

LINE_ERROR = "Line 2: a "
REMEDY_ERROR = "use {% comment %} ... {% endcomment %}"


def _seed_override(template_name, subject, body_markdown):
    from django.db import connection

    from email_app.models import EmailTemplateOverride

    override, _ = EmailTemplateOverride.objects.update_or_create(
        template_name=template_name,
        defaults={"subject": subject, "body_markdown": body_markdown},
    )
    connection.close()
    return override


def _stored_body(template_name):
    from django.db import connection

    from email_app.models import EmailTemplateOverride

    body = EmailTemplateOverride.objects.get(template_name=template_name).body_markdown
    connection.close()
    return body


def _open_editor(page, django_server, template_name):
    page.goto(
        f"{django_server}/studio/email-templates/{template_name}/edit/",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("email-template-edit-form")).to_be_visible()


def _save(page):
    page.get_by_role("button", name="Save override", exact=True).click()


class TestStudioEmailTemplateCommentGuard:
    @browser_journey
    def test_split_note_is_rejected_then_accepted_once_rewritten(
        self, django_server, browser
    ):
        create_staff_user(STAFF_EMAIL)
        context = auth_context(browser, STAFF_EMAIL)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/email-templates/", wait_until="domcontentloaded"
        )
        row = page.locator('tr[data-template-name="welcome"]')
        row.get_by_role("link", name="Edit", exact=True).click()
        expect(page.get_by_test_id("email-template-edit-form")).to_be_visible()

        page.fill("#tpl-body", SPLIT_NOTE_BODY)
        _save(page)

        # Still on the edit page, with the operator's full text intact.
        expect(page.get_by_test_id("email-template-edit-form")).to_be_visible()
        assert page.input_value("#tpl-body") == SPLIT_NOTE_BODY
        error = page.get_by_test_id("messages-region")
        expect(error).to_contain_text(LINE_ERROR)
        expect(error).to_contain_text(REMEDY_ERROR)

        page.fill("#tpl-body", BLOCK_NOTE_BODY)
        _save(page)

        expect(page).to_have_url(f"{django_server}/studio/email-templates/")
        expect(page.get_by_test_id("messages-region")).to_contain_text("welcome")

        _open_editor(page, django_server, "welcome")
        saved_body = page.input_value("#tpl-body")
        assert "{% comment %}" in saved_body
        assert "{# note about" not in saved_body

        context.close()

    @browser_journey
    def test_single_line_note_saves(self, django_server, browser):
        create_staff_user(STAFF_EMAIL)
        context = auth_context(browser, STAFF_EMAIL)
        page = context.new_page()

        _open_editor(page, django_server, "event_reminder")
        page.fill(
            "#tpl-body",
            "Hi {{ user_name }}\n"
            "{# internal note: keep this under two sentences #}\n"
            "See you there.",
        )
        _save(page)

        expect(page).to_have_url(f"{django_server}/studio/email-templates/")
        expect(page.get_by_test_id("messages-region")).to_contain_text("event_reminder")
        assert "internal note" in _stored_body("event_reminder")

        context.close()

    @browser_journey
    def test_rejected_save_leaves_the_stored_copy_untouched(
        self, django_server, browser
    ):
        create_staff_user(STAFF_EMAIL)
        _seed_override("welcome", "Welcome aboard", "Hi there, glad you joined.")
        context = auth_context(browser, STAFF_EMAIL)
        page = context.new_page()

        _open_editor(page, django_server, "welcome")
        page.fill("#tpl-body", SPLIT_NOTE_BODY)
        _save(page)

        expect(page.get_by_test_id("messages-region")).to_contain_text(LINE_ERROR)

        page.goto(
            f"{django_server}/studio/email-templates/", wait_until="domcontentloaded"
        )
        _open_editor(page, django_server, "welcome")

        assert page.input_value("#tpl-body") == "Hi there, glad you joined."
        assert _stored_body("welcome") == "Hi there, glad you joined."

        context.close()

    @browser_journey
    def test_reset_to_default_still_works_after_a_rejected_save(
        self, django_server, browser
    ):
        create_staff_user(STAFF_EMAIL)
        _seed_override("welcome", "Welcome aboard", "Hi there, glad you joined.")
        context = auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())

        _open_editor(page, django_server, "welcome")
        page.fill("#tpl-body", SPLIT_NOTE_BODY)
        _save(page)
        expect(page.get_by_test_id("messages-region")).to_contain_text(LINE_ERROR)

        page.goto(
            f"{django_server}/studio/email-templates/", wait_until="domcontentloaded"
        )
        _open_editor(page, django_server, "welcome")
        page.get_by_role("button", name="Reset to default", exact=True).click()

        expect(page).to_have_url(f"{django_server}/studio/email-templates/")
        expect(page.get_by_test_id("messages-region")).to_contain_text("Reverted")

        from django.db import connection

        from email_app.models import EmailTemplateOverride

        assert not EmailTemplateOverride.objects.filter(
            template_name="welcome"
        ).exists()
        connection.close()

        _open_editor(page, django_server, "welcome")
        shipped_body = page.input_value("#tpl-body")
        assert shipped_body
        assert shipped_body != SPLIT_NOTE_BODY

        context.close()
