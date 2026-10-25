"""Browser journeys for Relay-owned template drafts, previews, and publishing."""

import os
from unittest import mock

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _preview_calls(client):
    return [
        call for call in client.calls
        if isinstance(call, dict) and call.get("operation") == "preview"
    ]


@pytest.mark.core
@browser_journey
def test_staff_saves_previews_and_explicitly_publishes_relay_draft(
    django_server,
    browser,
):
    from email_app.testing import StubRelayTemplateClient

    ensure_tiers()
    create_staff_user("relay-editor@test.com")
    relay = StubRelayTemplateClient()
    context = auth_context(browser, "relay-editor@test.com")
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())

    with mock.patch(
        "studio.views.email_templates.configured_template_client",
        return_value=relay,
    ):
        page.goto(
            f"{django_server}/studio/email-templates/welcome/edit/",
            wait_until="domcontentloaded",
        )
        form = page.get_by_test_id("email-template-edit-form")
        form.locator('[name="subject"]').fill("Welcome from a Relay draft")
        form.locator('[name="markdown_body"]').fill(
            "Hello {{ user_name }} from the saved Relay draft."
        )
        form.locator('[name="text_body"]').fill("Plain Relay alternative")
        form.get_by_role("button", name="Save Relay draft").click()
        page.wait_for_load_state("domcontentloaded")

        expect(page.get_by_text('Saved Relay draft for "welcome".')).to_be_visible()
        assert relay._templates["welcome"]["subject"] == "Welcome from a Relay draft"
        assert relay._templates["welcome"]["latest_version"] == 1
        assert "publish" not in relay.calls

        page.locator('[name="recipient"]').select_option("no_name")
        page.locator('[name="version"]').select_option("")
        page.get_by_role("button", name="Preview through Relay").click()
        preview = page.frame_locator('iframe[name="tpl-preview"]')
        expect(preview.locator("body")).to_contain_text("Relay preview")

        call = _preview_calls(relay)[-1]
        assert call["template_key"] == "welcome"
        assert call["draft"] is True
        assert call["version"] is None
        assert call["context"]["user_name"] == "there"
        assert call["context"]["member_name"] == "there"

        page.get_by_role("button", name="Publish current draft").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text(
            'Published "welcome" as Relay version 2. Website sends remain pinned '
            "to v1 until a release changes the pin.",
            exact=True,
        )).to_be_visible()
        assert relay._templates["welcome"]["latest_version"] == 2
        expect(page.get_by_test_id("active-send-version")).to_contain_text(
            "Website sends currently use immutable Relay v1"
        )
        expect(page.locator('[name="version"] option[value="2"]')).to_have_text(
            "Published v2"
        )

    context.close()


@pytest.mark.core
@browser_journey
def test_relay_preview_failure_is_safe_and_does_not_publish(
    django_server,
    browser,
):
    from email_app.testing import StubRelayTemplateClient

    ensure_tiers()
    create_staff_user("relay-failure@test.com")
    relay = StubRelayTemplateClient()
    context = auth_context(browser, "relay-failure@test.com")
    page = context.new_page()

    with mock.patch(
        "studio.views.email_templates.configured_template_client",
        return_value=relay,
    ):
        page.goto(
            f"{django_server}/studio/email-templates/welcome/edit/",
            wait_until="domcontentloaded",
        )
        relay.fail_operations.add("preview")
        page.get_by_role("button", name="Preview through Relay").click()

        preview = page.frame_locator('iframe[name="tpl-preview"]')
        expect(preview.locator("body")).to_contain_text("Relay preview failed.")
        expect(preview.locator("pre")).to_have_text("stub_preview_failed")
        assert page.get_by_test_id("email-template-edit-form").is_visible()
        assert relay._templates["welcome"]["latest_version"] == 1
        assert "publish" not in relay.calls

    context.close()
