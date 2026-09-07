"""Browser acceptance for Maven welcome -> Slack delivery (issue #1565).

Three journeys:

1. A brand-new Maven enrollee follows the Slack link in their welcome email
   through the login gate and lands on the workspace invite.
2. Staff previews ``maven_welcome`` in Studio before the cohort goes out.
3. Support opens a Maven occurrence and reads why the enrollee never reached
   Slack.
"""

import os
import re
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_staff_user,
    ensure_tiers,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]

COURSE = "AI Engineering Buildcamp: From RAG to Agents"
COHORT = "Cohort 1"


def _enrol(email):
    """Onboard a cold enrollee through the real Maven handler."""
    from django.db import connection

    from accounts.models import User
    from integrations.services.maven import handle_maven_event

    ensure_tiers()
    # Not in the Slack workspace: the real invite path runs and produces the
    # real ledger note, so the Studio journey asserts production copy.
    with patch(
        "community.services.staff_notifications.notify_maven_enrollment",
        return_value=True,
    ), patch(
        "community.services.slack.SlackCommunityService.lookup_user_by_email",
        return_value=None,
    ), patch(
        "email_app.services.email_service.EmailService._send_ses",
        return_value="ses-message-id",
    ):
        result = handle_maven_event({
            "event": "user_cohort.enrolled",
            "email": email,
            "first_name": "Sam",
            "last_name": "Rivera",
            "course": COURSE,
            "cohort": COHORT,
        })
    user = User.objects.get(pk=result.user_id)
    connection.close()
    return user


def _render_welcome(user):
    """Render the maven_welcome body exactly as a real send would."""
    from django.db import connection

    from email_app.services.email_service import EmailService
    from integrations.services.maven import _welcome_context

    _subject, body_html = EmailService()._render_template(
        "maven_welcome", user, _welcome_context(user, COURSE, COHORT),
    )
    connection.close()
    return body_html


def _slack_href(body_html):
    match = re.search(r'href="([^"]*/community/slack)"', body_html)
    assert match, "maven_welcome renders no /community/slack link"
    return match.group(1)


@browser_journey
def test_enrollee_follows_the_welcome_email_into_slack(
    django_server, browser, settings,
):
    user = _enrol("maven-journey-1565@example.com")
    # The workspace invite target is served locally: the journey must stay
    # hermetic, and a real join.slack.com hop would make this test depend on
    # outbound network access from CI.
    invite_target = f"{django_server}/?slack-workspace-invite=1"
    settings.SLACK_INVITE_URL = invite_target

    body_html = _render_welcome(user)
    join_path = urlparse(_slack_href(body_html)).path
    assert join_path == "/community/slack"

    # Step 1 of the email: the enrollee sets a password.
    from django.db import connection
    user.set_password(DEFAULT_PASSWORD)
    user.save(update_fields=["password"])
    connection.close()

    context = browser.new_context()
    page = context.new_page()

    # Signed out, the emailed link lands on sign-in with the join as next.
    page.goto(f"{django_server}{join_path}", wait_until="domcontentloaded")
    assert "/accounts/login/" in page.url, page.url
    assert "next=/community/slack" in page.url, page.url

    # Step 2 of the email: sign in. The journey then completes on its own.
    page.fill("#login-email", user.email)
    page.fill("#login-password", DEFAULT_PASSWORD)
    page.click("#login-submit")

    # No further clicks: signing in delivers the member onward to the
    # workspace invite the email promised.
    page.wait_for_url(invite_target, timeout=15000)
    expect(page.get_by_test_id("account-menu-trigger")).to_be_visible()
    context.close()


@browser_journey
def test_staff_preview_shows_the_ordered_steps_and_the_slack_link(
    django_server, browser,
):
    ensure_tiers()
    create_staff_user("staff-1565@example.com")

    context = auth_context(browser, "staff-1565@example.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/email-templates/maven_welcome/edit/",
        wait_until="domcontentloaded",
    )
    status = page.locator('[data-testid="preview-status"]')
    status.wait_for(state="visible")
    page.wait_for_function(
        "() => {"
        "  const el = document.querySelector('[data-testid=\"preview-status\"]');"
        "  return el && el.textContent && el.textContent.trim() === 'Up to date';"
        "}",
        timeout=15000,
    )
    srcdoc = page.locator('[data-testid="email-template-preview"]').get_attribute(
        "srcdoc"
    )
    assert srcdoc, "maven_welcome preview iframe is empty"

    assert "You're enrolled in" in srcdoc
    assert "Set your password" in srcdoc
    assert "Sign in to AI Shipping Labs" in srcdoc
    assert "/community/slack" in srcdoc
    assert "your course" not in srcdoc
    context.close()


@browser_journey
def test_support_reads_why_an_enrollee_never_reached_slack(
    django_server, browser,
):
    from django.db import connection

    from integrations.models import MavenEnrollmentEvent

    user = _enrol("maven-skipped-1565@example.com")
    event = MavenEnrollmentEvent.objects.get(user=user)
    assert event.slack_status == MavenEnrollmentEvent.STEP_SKIPPED
    create_staff_user("staff-support-1565@example.com")
    connection.close()

    context = auth_context(browser, "staff-support-1565@example.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/maven-events/", wait_until="domcontentloaded")
    page.get_by_role("link", name=re.compile(rf"^#?{event.pk}\b")).first.click()
    page.wait_for_url(re.compile(rf".*/studio/maven-events/{event.pk}/"))

    note = page.get_by_test_id("maven-step-note-slack")
    expect(note).to_contain_text("not in the Slack workspace")
    expect(note).to_contain_text("delivered in the welcome email")
    expect(note).not_to_contain_text("Last error")
    context.close()
