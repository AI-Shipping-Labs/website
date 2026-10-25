"""Campaign preview and test-send coverage at the Relay boundary (#1567)."""

import os
from unittest import mock

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _campaign_fixture():
    from django.db import connection

    from email_app.models import EmailCampaign

    ensure_tiers()
    EmailCampaign.objects.all().delete()
    staff = create_staff_user("plain-campaign-1567@example.com")
    campaign = EmailCampaign.objects.create(
        subject="Plain alternative campaign",
        body=(
            "# Campaign heading\n\n- First item\n- Second item\n\n"
            "[Open the guide](https://example.test/guide)"
        ),
        status="draft",
    )
    connection.close()
    return staff, campaign


def _assert_preview_contract(page, relay_templates, campaign):
    preview = page.frame_locator('[data-testid="campaign-preview-iframe"]')
    expect(preview.locator("body")).to_contain_text("Relay preview")
    preview_call = None
    for call in relay_templates.calls:
        if isinstance(call, dict) and call.get("operation") == "preview":
            preview_call = call
            break
    assert preview_call is not None
    assert preview_call["template_key"] == "campaign-broadcast"
    assert preview_call["version"] == 2
    assert preview_call["draft"] is False
    assert preview_call["context"]["subject"] == campaign.subject
    assert preview_call["context"]["body_markdown"] == campaign.body


def _send_campaign_test(page, recipient):
    page.locator("#test-recipients").fill(recipient)
    with mock.patch(
        "studio.views.campaigns.send_campaign_delivery_to_relay",
        return_value="playwright-campaign-relay",
    ) as relay_send:
        page.get_by_role("button", name="Send Test").click()
        page.wait_for_load_state("domcontentloaded")
    return relay_send.call_args.kwargs


def _assert_delivery(delivered, campaign, recipient):
    assert delivered["recipient_email"] == recipient
    assert delivered["subject"] == f"[TEST] {campaign.subject}"
    assert delivered["body_markdown"] == campaign.body
    assert delivered["metadata"]["is_test_send"] is True
    assert delivered["idempotency_key"].startswith(
        f"campaign-test-{campaign.pk}-{recipient}-"
    )


@pytest.mark.core
@browser_journey
def test_staff_previews_and_test_sends_markdown_campaign(django_server, browser):
    from django.db import connection

    from email_app.testing import StubRelayTemplateClient

    staff, campaign = _campaign_fixture()
    relay_templates = StubRelayTemplateClient()
    context = auth_context(browser, staff.email)
    page = context.new_page()
    with mock.patch(
        "studio.views.campaigns.configured_template_client",
        return_value=relay_templates,
    ):
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )
        _assert_preview_contract(page, relay_templates, campaign)
        delivered = _send_campaign_test(page, staff.email)

    expect(page.get_by_text(
        f"Test email sent to 1 address(es): {staff.email}.",
        exact=True,
    )).to_be_visible()
    _assert_delivery(delivered, campaign, staff.email)
    campaign.refresh_from_db()
    assert campaign.status == "draft"
    connection.close()
    context.close()
