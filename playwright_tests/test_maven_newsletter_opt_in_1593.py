"""Browser acceptance for the Maven newsletter opt-in (#1593).

The welcome email says "if you want to hear from us, verify your email".
These tests exist to prove the sentence is true in a real browser: the link
subscribes as well as verifies, the landing page says so, and nothing else in
the enrollment flow subscribes anybody who did not click it.
"""

import os
import re
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _enrollee(email):
    """Create a Maven enrollee through the real webhook handler."""
    from accounts.models import User
    from integrations.services.maven import handle_maven_event

    ensure_tiers()
    with patch(
        "integrations.services.maven._invite_to_slack",
        return_value=("succeeded", ""),
    ), patch("integrations.services.maven._send_welcome"):
        result = handle_maven_event(
            {
                "event": "user_cohort.enrolled",
                "email": email,
                "course_id": "pw-course-1593",
                "cohort_id": "pw-cohort-1593",
                "course": {"name": "Playwright Maven course"},
            }
        )
    user = User.objects.get(pk=result.user_id)
    # Enrolling alone must never subscribe anyone.
    assert user.unsubscribed is True
    assert user.email_verified is False
    return user


def _relative(url):
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path


def _opt_in_path(user):
    from integrations.services.maven import _welcome_context

    return _relative(_welcome_context(user, "Agents Course")["newsletter_opt_in_url"])


@browser_journey
def test_enrollee_clicks_verify_and_is_both_verified_and_subscribed(
    browser, django_server,
):
    user = _enrollee("maven-opt-in-1593@example.com")
    path = _opt_in_path(user)

    page = browser.new_page()
    page.goto(f"{django_server}{path}", wait_until="domcontentloaded")

    # The page states plainly what just happened — both halves of it.
    expect(page.get_by_role("heading", name="You're subscribed")).to_be_visible()
    expect(page.get_by_text("verified", exact=False)).to_be_visible()
    # And the way out is right there, without needing a password they may not
    # have set yet.
    unsubscribe_link = page.get_by_role("link", name="Unsubscribe")
    expect(unsubscribe_link).to_be_visible()
    assert "/api/unsubscribe?token=" in unsubscribe_link.get_attribute("href")
    page.close()

    from accounts.models import User

    user = User.objects.get(pk=user.pk)
    assert user.email_verified is True
    assert user.unsubscribed is False
    assert user.email_preferences["newsletter"] is True
    # The scoped course-email preference is a separate decision.
    assert user.email_preferences["maven_emails"] is True

    context = auth_context(browser, user.email)
    account = context.new_page()
    account.goto(f"{django_server}/account/")
    expect(account.locator("#newsletter-toggle-dot")).to_have_class(
        re.compile("translate-x-5")
    )
    expect(
        account.get_by_role("switch", name="Toggle Maven course emails")
    ).to_have_attribute("aria-checked", "true")
    context.close()


@browser_journey
def test_enrollee_who_never_clicks_stays_off_the_newsletter(browser, django_server):
    """Silence is not consent: the toggle they never touched reads off."""
    user = _enrollee("maven-no-opt-in-1593@example.com")

    context = auth_context(browser, user.email)
    page = context.new_page()
    page.goto(f"{django_server}/account/")

    expect(page.locator("#newsletter-toggle-dot")).to_have_class(
        re.compile("translate-x-0")
    )
    expect(
        page.get_by_role("switch", name="Toggle Maven course emails")
    ).to_have_attribute("aria-checked", "true")
    context.close()


@browser_journey
def test_opting_in_then_leaving_takes_one_click_each_way(browser, django_server):
    user = _enrollee("maven-in-then-out-1593@example.com")

    page = browser.new_page()
    page.goto(f"{django_server}{_opt_in_path(user)}", wait_until="domcontentloaded")
    page.get_by_role("link", name="Unsubscribe").click()
    page.wait_for_load_state("domcontentloaded")

    expect(page.get_by_role("heading", name="Unsubscribed")).to_be_visible()
    # The corrected copy: the flag gates marketing, not everything.
    assert "unsubscribed from all emails" not in page.content()
    expect(page.get_by_text("newsletter", exact=False).first).to_be_visible()
    page.close()

    from accounts.models import User

    user = User.objects.get(pk=user.pk)
    assert user.unsubscribed is True
    assert user.email_preferences["newsletter"] is False
    # Leaving the newsletter does not un-verify them or cost them access.
    assert user.email_verified is True
    assert user.email_preferences["maven_emails"] is True


@browser_journey
def test_a_tampered_opt_in_link_fails_safely(browser, django_server):
    user = _enrollee("maven-tampered-1593@example.com")

    page = browser.new_page()
    page.goto(
        f"{django_server}/api/verify-and-subscribe?token=not-a-real-token",
        wait_until="domcontentloaded",
    )

    expect(page.get_by_role("heading", name="Subscribe failed")).to_be_visible()
    page.close()

    from accounts.models import User

    user = User.objects.get(pk=user.pk)
    assert user.unsubscribed is True
    assert user.email_verified is False
