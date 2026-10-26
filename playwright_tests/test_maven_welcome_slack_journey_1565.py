"""Browser acceptance for Maven welcome -> Slack delivery (issue #1565).

Two journeys:

1. A brand-new Maven enrollee follows the resolved Slack join route
   through the login gate and lands on the workspace invite.
2. Support opens a Maven occurrence and reads that the join link went out
   through the welcome email rather than a direct Slack invite (#1665).
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
    # Issue #1665 retired the direct Slack lookup/invite call for Maven
    # enrollees: the ``slack`` ledger step mirrors ``welcome_status`` instead,
    # so this patch is a no-op guard against an accidental real API call
    # rather than a fixture for a "not in the workspace" code path. The
    # welcome mail is queued as a durable delivery and the session's sync
    # jobs backend drains it against the installed offline mail stub (A1.2).
    with patch(
        "community.services.staff_notifications.notify_maven_enrollment",
        return_value=True,
    ), patch(
        "community.services.slack.SlackCommunityService.lookup_user_by_email",
        return_value=None,
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


def _resolve_welcome_context(user):
    """Resolve the durable Maven delivery context handed to Relay."""
    from community_base.mail.models import EmailDelivery
    from django.db import connection

    from email_app.hooks import resolve_auth_mail_context

    delivery = EmailDelivery.objects.filter(
        purpose="maven_welcome", recipient_user=user,
    ).latest("created_at")
    context = resolve_auth_mail_context(
        delivery=delivery, context=dict(delivery.context_data),
    )
    connection.close()
    return delivery, context


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

    delivery, resolved = _resolve_welcome_context(user)
    assert delivery.purpose == "maven_welcome"
    assert delivery.template_version == 2
    join_path = urlparse(resolved["slack_join_url"]).path
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
def test_support_reads_the_join_link_note_on_the_slack_step(
    django_server, browser,
):
    """Issue #1665: the ``slack`` step mirrors ``welcome_status`` and never
    calls the Slack API, so a successful welcome makes it succeed too — with
    a note explaining the join link travelled by email, not a direct invite.
    """
    from django.db import connection

    from integrations.models import MavenEnrollmentEvent

    user = _enrol("maven-slack-note-1565@example.com")
    event = MavenEnrollmentEvent.objects.get(user=user)
    assert event.welcome_status == MavenEnrollmentEvent.STEP_SUCCEEDED
    assert event.slack_status == MavenEnrollmentEvent.STEP_SUCCEEDED
    create_staff_user("staff-support-1565@example.com")
    connection.close()

    context = auth_context(browser, "staff-support-1565@example.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/maven-events/", wait_until="domcontentloaded")
    page.get_by_role("link", name=re.compile(rf"^#?{event.pk}\b")).first.click()
    page.wait_for_url(re.compile(rf".*/studio/maven-events/{event.pk}/"))

    note = page.get_by_test_id("maven-step-note-slack")
    expect(note).to_contain_text("Join link delivered via the maven_welcome email")
    expect(note).to_contain_text("direct Slack invite is not attempted")
    expect(note).not_to_contain_text("Last error")
    context.close()
