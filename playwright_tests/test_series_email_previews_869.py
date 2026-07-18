"""Local Studio preview evidence for series calendar email copy (#869)."""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "issue-869-email-previews"

PREVIEW_CASES = (
    (
        "series_registration",
        "This email includes a calendar invitation for the sessions above.",
        "registration",
    ),
    (
        "series_update",
        "this email includes an updated calendar invitation",
        "update",
    ),
    (
        "series_cancellation",
        "This email includes a calendar cancellation update",
        "cancellation",
    ),
)

EXPECTED_GUIDANCE = {
    "series_registration": "registers for an event series",
    "series_update": "session details change",
    "series_cancellation": "one session in an event series is cancelled",
}


@pytest.mark.parametrize(
    "template_name,expected_copy,screenshot_name", PREVIEW_CASES
)
def test_series_email_preview_uses_prompt_aware_calendar_copy(
    django_server,
    browser,
    template_name,
    expected_copy,
    screenshot_name,
):
    expected_guidance = EXPECTED_GUIDANCE[template_name]
    _create_staff_user("staff-series-preview@test.com")
    context = _auth_context(browser, "staff-series-preview@test.com")
    page = context.new_page()

    response = page.goto(
        f"{django_server}/studio/email-templates/{template_name}/edit/",
        wait_until="domcontentloaded",
    )
    assert response is not None and response.status == 200
    sent_when = page.get_by_test_id("template-sent-when")
    expect(sent_when).to_be_visible()
    expect(sent_when).to_contain_text("Sent when:")
    expect(sent_when).to_contain_text(expected_guidance)
    expect(page.locator('[data-testid="preview-status"]')).to_have_text(
        "Up to date"
    )

    preview = page.frame_locator('[data-testid="email-template-preview"]')
    body = preview.locator("body")
    expect(body).to_contain_text(expected_copy)
    expect(body).to_contain_text("if prompted", ignore_case=True)
    rendered = body.inner_text().lower()
    for forbidden in (
        "attached to this email",
        "download",
        "automatically",
        "rather than create duplicates",
        "will update the existing",
    ):
        assert forbidden not in rendered

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOT_DIR / f"series-{screenshot_name}-preview.png",
        full_page=True,
    )
    context.close()
