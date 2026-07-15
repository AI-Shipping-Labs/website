"""Studio email-setting guardrails for paid welcome routing (#1119)."""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.core]


@pytest.mark.django_db(transaction=True)
def test_paid_welcome_addresses_use_browser_email_validation(
    django_server, browser,
):
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.all().delete()
    _create_staff_user("admin-1119@test.com")
    context = _auth_context(browser, "admin-1119@test.com")
    page = context.new_page()

    page.goto(
        f"{django_server}/studio/settings/#site",
        wait_until="domcontentloaded",
    )

    staff_email = page.locator(
        "#integration-site input[name='STAFF_SIGNUP_NOTIFY_EMAIL']"
    )
    reply_to = page.locator(
        "#integration-ses input[name='SES_WELCOME_REPLY_TO_EMAIL']"
    )
    assert staff_email.get_attribute("type") == "email"
    assert reply_to.get_attribute("type") == "email"

    staff_email.fill("not-an-email")
    page.locator("#integration-site button[type='submit']").click()

    # Native browser validation blocks the request before any group setting
    # can be partially saved. Server-side validation covers non-browser POSTs.
    assert staff_email.evaluate("element => element.validationMessage")
    assert not IntegrationSetting.objects.filter(
        key="STAFF_SIGNUP_NOTIFY_EMAIL"
    ).exists()

    context.close()
