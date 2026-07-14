"""Browser acceptance for scoped Maven email opt-out and re-enable (#960)."""

import os
import re
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _user(email):
    from accounts.models import User
    from integrations.models import MavenEnrollmentEvent
    from integrations.services.maven import handle_maven_event

    # Transactional Playwright tests flush tables between cases; restore the
    # tier fixture before exercising the real Maven import path.
    ensure_tiers()
    with patch("integrations.services.maven._invite_to_slack"), patch(
        "integrations.services.maven._send_welcome"
    ):
        result = handle_maven_event(
            {
                "event": "user_cohort.enrolled",
                "email": email,
                "course_id": "pw-course-960",
                "cohort_id": "pw-cohort-960",
                "course": {"name": "Playwright Maven course"},
                "cohort": {"name": "Playwright Maven cohort"},
            }
        )
    user = User.objects.get(pk=result.user_id)
    assert user.signup_source == "imported"
    assert user.tier.level == 0
    assert MavenEnrollmentEvent.objects.filter(user=user, lifecycle="active").count() == 1
    return user


def test_account_maven_toggle_persists_without_changing_access(browser, django_server):
    user = _user("maven-toggle@example.com")
    from content.access import get_user_level

    access_level = get_user_level(user)
    context = auth_context(browser, user.email)
    page = context.new_page()
    page.goto(f"{django_server}/account/")
    toggle = page.get_by_test_id("maven-emails-toggle")
    expect(toggle).to_be_visible()
    toggle.click()
    expect(page.get_by_test_id("maven-emails-status")).to_contain_text("Access is unchanged")
    page.reload()
    expect(page.locator("#maven-emails-toggle-dot")).to_have_class(re.compile("translate-x-0"))
    from accounts.models import User
    user = User.objects.get(pk=user.pk)
    assert user.email_preferences["maven_emails"] is False
    assert get_user_level(user) == access_level
    context.close()


def test_signed_opt_out_confirmation_and_account_reenable(browser, django_server):
    user = _user("maven-link@example.com")
    from integrations.services.maven import _welcome_context
    path = urlparse(_welcome_context(user, "Agents Course")["opt_out_url"]).path
    query = urlparse(_welcome_context(user, "Agents Course")["opt_out_url"]).query
    page = browser.new_page()
    page.goto(f"{django_server}{path}?{query}")
    expect(page.get_by_text("Your course and community access are unchanged", exact=False)).to_be_visible()
    page.close()

    context = auth_context(browser, user.email)
    account = context.new_page()
    account.goto(f"{django_server}/account/")
    expect(account.locator("#maven-emails-toggle-dot")).to_have_class(re.compile("translate-x-0"))
    account.get_by_test_id("maven-emails-toggle").click()
    expect(account.get_by_test_id("maven-emails-status")).to_contain_text("turned on")
    account.reload()
    expect(account.locator("#maven-emails-toggle-dot")).to_have_class(re.compile("translate-x-5"))
    context.close()
