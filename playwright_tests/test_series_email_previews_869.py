"""Local Studio preview evidence for series calendar email copy (#869)."""

from pathlib import Path
from unittest import mock

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from scripts.browser_journey_policy import browser_journey

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "issue-869-email-previews"

PREVIEW_CASES = (
    ("series_registration", "registration"),
    ("series_update", "update"),
    ("series_cancellation", "cancellation"),
)

EXPECTED_GUIDANCE = {
    "series_registration": "registers for an event series",
    "series_update": "session details change",
    "series_cancellation": "one session in an event series is cancelled",
}


@pytest.mark.parametrize(
    "template_name,screenshot_name", PREVIEW_CASES
)
@browser_journey
def test_series_email_editor_previews_through_relay(
    django_server,
    browser,
    template_name,
    screenshot_name,
):
    from email_app.testing import StubRelayTemplateClient

    _create_staff_user("staff-series-preview@test.com")
    relay = StubRelayTemplateClient()
    context = _auth_context(browser, "staff-series-preview@test.com")
    page = context.new_page()
    _open_and_preview(page, django_server, template_name, relay)

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOT_DIR / f"series-{screenshot_name}-preview.png",
        full_page=True,
    )
    context.close()


def _open_and_preview(page, django_server, template_name, relay):
    with mock.patch(
        "studio.views.email_templates.configured_template_client",
        return_value=relay,
    ):
        response = page.goto(
            f"{django_server}/studio/email-templates/{template_name}/edit/",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        sent_when = page.get_by_test_id("template-sent-when")
        expect(sent_when).to_be_visible()
        expect(sent_when).to_contain_text("Sent when:")
        expect(sent_when).to_contain_text(EXPECTED_GUIDANCE[template_name])
        page.get_by_role("button", name="Preview through Relay").click()
        preview = page.frame_locator('[data-testid="email-template-preview"]')
        expect(preview.locator("body")).to_contain_text("Relay preview")
        call = _preview_call(relay.calls)
        assert call["template_key"] == template_name
        assert call["draft"] is True


def _preview_call(calls):
    for call in calls:
        if isinstance(call, dict) and call.get("operation") == "preview":
            return call
    raise AssertionError("Relay preview was not requested")
