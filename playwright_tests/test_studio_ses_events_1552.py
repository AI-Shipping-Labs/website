"""Operator journeys for actionable SES summaries and campaign context (#1552)."""

import os
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only

STAFF_EMAIL = "ses-1552-staff@example.com"


def _reset_feedback_data():
    from email_app.models import EmailCampaign, EmailLog, SesEvent

    SesEvent.objects.all().delete()
    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    connection.close()


def _login(browser):
    _ensure_tiers()
    _create_staff_user(STAFF_EMAIL)
    return _auth_context(browser, STAFF_EMAIL)


def _campaign(subject):
    from email_app.models import EmailCampaign

    campaign = EmailCampaign.objects.create(
        subject=subject,
        body="Campaign body",
        status="sent",
    )
    connection.close()
    return campaign


def _feedback_event(*, campaign=None, event_type="bounce_permanent", email="bounce@example.com"):
    from accounts.models import User
    from email_app.models import EmailLog, SesEvent

    user = User.objects.create_user(email=email)
    log = None
    if campaign is not None:
        log = EmailLog.objects.create(
            campaign=campaign,
            user=user,
            recipient_email=email,
            email_type="campaign",
            ses_message_id=f"ses-{campaign.pk}-{user.pk}",
            bounced_at=timezone.now(),
            bounce_type="Permanent",
            bounce_subtype="General",
            bounce_diagnostic="smtp; 550 mailbox unavailable",
        )
    event = SesEvent.objects.create(
        event_type=event_type,
        message_id=f"sns-{campaign.pk if campaign else 'global'}-{user.pk}",
        raw_payload={"recipient": email},
        recipient_email=email,
        user=user,
        email_log=log,
        match_status=SesEvent.MATCH_STATUS_PRIMARY_EMAIL,
        bounce_type="Permanent" if event_type == "bounce_permanent" else "",
        bounce_subtype="General" if event_type == "bounce_permanent" else "",
        action_taken="Unsubscribed after permanent bounce",
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
@pytest.mark.visual_regression
@browser_journey
def test_keyboard_summary_card_opens_matching_global_queue(django_server, browser):
    _reset_feedback_data()
    empty_campaign = _campaign("Campaign with no linked events")
    _feedback_event(email="global-bounce@example.com")
    context = _login(browser)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/ses-events/?campaign={empty_campaign.pk}&q=hidden",
        wait_until="domcontentloaded",
    )

    card = page.get_by_test_id("ses-event-stat-bounce-link")
    expect(card).to_have_accessible_name(
        "View 1 permanent bounces from the last 30 UTC calendar days",
    )
    card.focus()
    expect(card).to_be_focused()
    focus_style = card.evaluate(
        "el => ({outline: getComputedStyle(el).outlineStyle, shadow: getComputedStyle(el).boxShadow})",
    )
    assert focus_style["outline"] != "none" or focus_style["shadow"] != "none"
    page.keyboard.press("Enter")
    page.wait_for_url("**/studio/ses-events/?type=bounce_permanent&since=*")

    query = parse_qs(urlsplit(page.url).query)
    assert set(query) == {"type", "since"}
    assert query["type"] == ["bounce_permanent"]
    expect(page.get_by_test_id("ses-event-current-results")).to_contain_text(
        "Current results: 1",
    )
    expect(page.get_by_text("global-bounce@example.com", exact=True)).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_campaign_without_linked_events_leads_to_recipient_diagnostics(
    django_server, browser,
):
    _reset_feedback_data()
    campaign = _campaign("Campaign recipient investigation")
    from accounts.models import User
    from email_app.models import EmailLog

    recipient = User.objects.create_user(email="campaign-bounce@example.com")
    EmailLog.objects.create(
        campaign=campaign,
        user=recipient,
        recipient_email=recipient.email,
        email_type="campaign",
        bounced_at=timezone.now(),
        bounce_type="Transient",
        bounce_subtype="General",
        bounce_diagnostic="smtp; 452 mailbox full",
    )
    connection.close()

    context = _login(browser)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/ses-events/?campaign={campaign.pk}",
        wait_until="domcontentloaded",
    )

    expect(page.get_by_test_id("studio-empty-state-filter")).to_contain_text(
        "No SES events are linked",
    )
    expect(page.get_by_test_id("ses-event-campaign-filter")).to_contain_text(
        "correlated through this campaign's EmailLog records",
    )
    page.get_by_test_id("ses-event-campaign-recipients-link").click()
    page.wait_for_url(f"**/studio/campaigns/{campaign.pk}/recipients/")
    expect(page.get_by_text("campaign-bounce@example.com", exact=True)).to_be_visible()
    expect(page.get_by_text("smtp; 452 mailbox full", exact=True)).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_clear_event_filters_keeps_campaign_and_recovers_linked_event(
    django_server, browser,
):
    _reset_feedback_data()
    campaign = _campaign("Campaign with linked feedback")
    _feedback_event(campaign=campaign, email="linked-bounce@example.com")
    context = _login(browser)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/ses-events/?campaign={campaign.pk}&type=complaint",
        wait_until="domcontentloaded",
    )

    empty = page.get_by_test_id("studio-empty-state-filter")
    expect(empty).to_contain_text("has SES events, but none match")
    empty.get_by_role("link", name="Clear event filters").click()
    page.wait_for_url(f"**/studio/ses-events/?campaign={campaign.pk}")
    expect(page.get_by_text("linked-bounce@example.com", exact=True)).to_be_visible()
    expect(page.get_by_test_id("ses-event-current-results")).to_contain_text(
        "Current results: 1",
    )
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_matched_identity_links_from_campaign_event_to_member(
    django_server, browser,
):
    _reset_feedback_data()
    campaign = _campaign("Campaign identity trace")
    event = _feedback_event(campaign=campaign, email="matched-member@example.com")
    context = _login(browser)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/ses-events/?campaign={campaign.pk}",
        wait_until="domcontentloaded",
    )

    row = page.get_by_test_id(f"ses-event-row-{event.pk}")
    expect(row.get_by_test_id("ses-event-match-status")).to_have_text(
        "Matched by primary email",
    )
    row.get_by_test_id("ses-event-user-link").click()
    page.wait_for_url("**/studio/users/*/")
    expect(page.get_by_text("matched-member@example.com", exact=True).first).to_be_visible()
    context.close()
