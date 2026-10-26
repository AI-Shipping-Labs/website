"""Browser coverage for the nameless password-reset boundary (#1591)."""

import os

import pytest

from playwright_tests.conftest import create_user, ensure_tiers, goto_with_retry
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


@pytest.mark.core
@browser_journey
def test_nameless_member_password_reset_uses_neutral_relay_context(
    django_server,
    browser,
):
    from community_base.mail.models import EmailDelivery
    from django.db import connection

    from email_app.hooks import resolve_auth_mail_context

    ensure_tiers()
    user = create_user("nameless-reset@test.com")
    assert not user.first_name and not user.last_name

    context = browser.new_context()
    page = context.new_page()
    goto_with_retry(
        page,
        f"{django_server}/accounts/login/",
        wait_until="domcontentloaded",
    )
    page.locator("#forgot-password-link").click()
    page.wait_for_selector("#password-reset-email", timeout=20000)
    page.fill("#password-reset-email", user.email)
    page.locator("#password-reset-request-submit").click()

    success = page.locator("#password-reset-request-success")
    success.wait_for(state="visible", timeout=20000)
    assert "password reset instructions" in success.inner_text()

    delivery = EmailDelivery.objects.filter(
        purpose="password_reset",
        recipient_email=user.email,
    ).latest("created_at")
    resolved = resolve_auth_mail_context(
        delivery=delivery,
        context=dict(delivery.context_data),
    )
    assert delivery.template_version == 2
    assert resolved["user_name"] == "there"
    assert "nameless-reset" not in resolved["user_name"]
    assert "/api/password-reset" in resolved["reset_url"]
    connection.close()
    context.close()
