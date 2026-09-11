"""Operator journeys for monitored re-permission campaigns."""

import os
import re
from datetime import timedelta
from unittest import mock
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only


def _reset_campaign_data():
    from django_q.models import Schedule

    from email_app.models import EmailCampaign, EmailLog

    Schedule.objects.all().delete()
    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    connection.close()


def _update_user(email, **fields):
    from accounts.models import User

    User.objects.filter(email=email).update(**fields)
    connection.close()


def _relative(url):
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_targets_unverified_users_and_reviews_the_safety_policy(
    django_server,
    browser,
):
    _ensure_tiers()
    _reset_campaign_data()
    _create_staff_user("admin@test.com")
    _create_user("unverified-one@test.com", email_verified=False)
    _create_user("unverified-two@test.com", email_verified=False)
    _create_user("verified@test.com", email_verified=True)
    _create_user("unsubscribed@test.com", email_verified=False, unsubscribed=True)
    _create_user("bounced@test.com", email_verified=False)
    _update_user("bounced@test.com", bounce_state="permanent")
    _create_user("inactive@test.com", email_verified=False)
    _update_user("inactive@test.com", is_active=False)

    context = _auth_context(browser, "admin@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/campaigns/new", wait_until="domcontentloaded")
    page.locator('input[name="subject"]').fill("Re-permission E2E")
    page.locator('textarea[name="body"]').fill("A short introduction")
    page.locator('[data-testid="campaign-audience-verification"]').select_option(
        "unverified_only",
    )
    page.locator('[data-testid="recipient-count-helper"]').get_by_text(
        "Will reach 2 eligible recipients",
    ).wait_for()
    page.get_by_role("button", name="Save as Draft", exact=True).click()
    page.wait_for_load_state("domcontentloaded")

    monitoring = page.locator('[data-testid="campaign-repermission-monitoring"]')
    assert monitoring.is_visible()
    assert "pilot of at most 100 recipients" in monitoring.inner_text()
    assert "waves contain at most 250" in monitoring.inner_text()
    assert "24 hours" in monitoring.inner_text()
    assert "bounce rate below 2%" in monitoring.inner_text()
    assert "zero complaints" in monitoring.inner_text()
    assert page.locator('[data-testid="eligible-recipients"]').inner_text().strip() == "2"
    page.locator('[data-testid="campaign-recipients-link"]').click()
    page.wait_for_load_state("domcontentloaded")
    for email in ("unverified-one@test.com", "unverified-two@test.com"):
        expect(page.get_by_text(email, exact=True)).to_be_visible()
    for email in (
        "verified@test.com",
        "unsubscribed@test.com",
        "bounced@test.com",
        "inactive@test.com",
    ):
        expect(page.get_by_text(email, exact=True)).to_have_count(0)
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_proves_the_controlled_consent_action_before_release(
    django_server,
    browser,
):
    from email_app.models import EmailCampaign
    from email_app.services.campaign_repermission import (
        REPERMISSION_COPY,
        REPERMISSION_CTA,
    )

    _ensure_tiers()
    _reset_campaign_data()
    _create_staff_user("consent-admin@test.com")
    controlled = _create_user(
        "controlled-consent@test.com",
        email_verified=False,
    )
    campaign = EmailCampaign.objects.create(
        subject="Controlled consent proof",
        body="A controlled introduction",
        audience_verification="unverified_only",
    )
    campaign_id = campaign.pk
    connection.close()

    prepared_messages = []

    def capture(prepared):
        prepared_messages.append(prepared)
        return "controlled-test-message"

    context = _auth_context(browser, "consent-admin@test.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/campaigns/{campaign_id}/",
        wait_until="domcontentloaded",
    )
    preview = page.frame_locator('[data-testid="campaign-preview-iframe"]')
    expect(preview.get_by_text(REPERMISSION_COPY, exact=False)).to_be_visible()
    expect(preview.get_by_role("link", name=REPERMISSION_CTA)).to_be_visible()
    assert "preview-only" in preview.get_by_role(
        "link",
        name=REPERMISSION_CTA,
    ).get_attribute("href")

    page.locator("#test-recipients").fill(controlled.email)
    with mock.patch(
        "studio.views.campaigns.EmailService.send_prepared",
        side_effect=capture,
    ):
        page.get_by_role("button", name="Send Test", exact=True).click()
        page.wait_for_load_state("domcontentloaded")

    assert len(prepared_messages) == 1
    prepared = prepared_messages[0]
    for rendered in (prepared.full_html, prepared.plain_text):
        assert REPERMISSION_COPY in rendered
        assert REPERMISSION_CTA in rendered
        assert rendered.count("/api/verify-and-subscribe") == 1
        assert "/api/unsubscribe" in rendered
        assert "/api/verify-email" not in rendered
    match = re.search(
        r"https?://[^\s)]+/api/verify-and-subscribe\?token=[^\s)]+",
        prepared.plain_text,
    )
    assert match is not None

    page.goto(
        f"{django_server}{_relative(match.group(0))}",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_role("heading", name="You're subscribed")).to_be_visible()

    from accounts.models import User

    controlled = User.objects.get(pk=controlled.pk)
    assert controlled.email_verified is True
    assert controlled.unsubscribed is False
    assert controlled.email_preferences["newsletter"] is True
    connection.close()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_starts_only_the_pilot_and_repeat_submission_is_idempotent(
    django_server,
    browser,
):
    from django_q.models import Schedule

    from email_app.models import CampaignWave, EmailCampaign
    from email_app.tasks.send_campaign import send_campaign

    _ensure_tiers()
    _reset_campaign_data()
    _create_staff_user("pilot-admin@test.com")
    for index in range(351):
        _create_user(f"pilot-{index:03d}@test.com", email_verified=False)
    campaign = EmailCampaign.objects.create(
        subject="Pilot-only release",
        body="Introduction",
        audience_verification="unverified_only",
    )
    campaign_id = campaign.pk
    connection.close()

    def run_parent(func, **kwargs):
        assert func == "email_app.tasks.send_campaign.send_campaign"
        send_campaign(
            kwargs["campaign_id"],
            released_by_id=kwargs.get("released_by_id"),
        )
        return "inline-parent-task"

    context = _auth_context(browser, "pilot-admin@test.com")
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    detail_url = f"{django_server}/studio/campaigns/{campaign_id}/"
    page.goto(detail_url, wait_until="domcontentloaded")
    send_form = page.locator('[data-testid="send-campaign-btn"]').locator("xpath=..")
    send_path = send_form.get_attribute("action")
    csrf_token = send_form.locator('input[name="csrfmiddlewaretoken"]').input_value()

    with mock.patch("jobs.tasks.async_task", side_effect=run_parent) as enqueue:
        page.locator('[data-testid="send-campaign-btn"]').click()
        page.wait_for_load_state("domcontentloaded")
        duplicate = context.request.post(
            f"{django_server}{send_path}",
            form={"csrfmiddlewaretoken": csrf_token},
            headers={"Referer": detail_url},
        )

    assert duplicate.ok
    assert enqueue.call_count == 1
    waves = list(CampaignWave.objects.filter(campaign_id=campaign_id).order_by("number"))
    assert [wave.deliveries.count() for wave in waves] == [100, 250, 1]
    assert waves[0].state == CampaignWave.State.SENDING
    assert waves[0].released_at is not None
    assert all(wave.state == CampaignWave.State.PENDING for wave in waves[1:])
    assert all(wave.released_at is None for wave in waves[1:])
    assert Schedule.objects.count() == 1
    connection.close()

    page.goto(detail_url, wait_until="domcontentloaded")
    assert page.locator('[data-testid="campaign-wave-row"]').count() == 3
    expect(page.locator('[data-testid="release-next-wave"]')).to_have_count(0)
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_releases_one_safe_followup_wave(django_server, browser):
    _ensure_tiers()
    _reset_campaign_data()
    staff = _create_staff_user("admin@test.com")
    sent_user = _create_user("sent@test.com", email_verified=False)
    future_user = _create_user("future@test.com", email_verified=False)

    from email_app.models import (
        CampaignDelivery,
        CampaignWave,
        EmailCampaign,
        EmailLog,
    )

    campaign = EmailCampaign.objects.create(
        subject="Release wave E2E",
        body="Introduction",
        audience_verification="unverified_only",
        status="sending",
        audience_snapshotted_at=timezone.now(),
    )
    first = CampaignWave.objects.create(
        campaign=campaign,
        number=1,
        state=CampaignWave.State.MONITORING,
        released_at=timezone.now() - timedelta(hours=25),
        released_by=staff,
        monitoring_started_at=timezone.now() - timedelta(hours=24),
    )
    second = CampaignWave.objects.create(campaign=campaign, number=2)
    log = EmailLog.objects.create(
        campaign=campaign,
        user=sent_user,
        recipient_email=sent_user.email,
        email_type="campaign",
    )
    CampaignDelivery.objects.create(
        campaign=campaign,
        wave=first,
        user=sent_user,
        recipient_user_pk=sent_user.pk,
        recipient_email=sent_user.email,
        state=CampaignDelivery.State.SENT,
        email_log=log,
    )
    CampaignDelivery.objects.create(
        campaign=campaign,
        wave=second,
        user=future_user,
        recipient_user_pk=future_user.pk,
        recipient_email=future_user.email,
    )
    campaign_id = campaign.pk
    connection.close()

    context = _auth_context(browser, "admin@test.com")
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(
        f"{django_server}/studio/campaigns/{campaign_id}/",
        wait_until="domcontentloaded",
    )
    page.locator('[data-testid="release-next-wave"]').click()
    page.wait_for_load_state("domcontentloaded")

    assert page.locator('[data-testid="campaign-wave-row"]').count() == 2
    assert "Wave 2 queued for monitored sending" in page.locator("main").inner_text()
    second.refresh_from_db()
    assert second.state == CampaignWave.State.SENDING
    assert second.released_by_id == staff.pk
    connection.close()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_is_stopped_by_unsafe_feedback_and_can_open_evidence(
    django_server,
    browser,
):
    from email_app.models import (
        CampaignDelivery,
        CampaignWave,
        EmailCampaign,
        EmailLog,
    )

    _ensure_tiers()
    _reset_campaign_data()
    _create_staff_user("pause-admin@test.com")
    complained = _create_user("complained@test.com", email_verified=False)
    future = _create_user("paused-future@test.com", email_verified=False)
    campaign = EmailCampaign.objects.create(
        subject="Unsafe feedback",
        body="Introduction",
        audience_verification="unverified_only",
        status="sending",
        audience_snapshotted_at=timezone.now(),
    )
    first = CampaignWave.objects.create(
        campaign=campaign,
        number=1,
        state=CampaignWave.State.MONITORING,
        released_at=timezone.now() - timedelta(hours=25),
        monitoring_started_at=timezone.now() - timedelta(hours=24),
    )
    second = CampaignWave.objects.create(campaign=campaign, number=2)
    log = EmailLog.objects.create(
        campaign=campaign,
        user=complained,
        recipient_email=complained.email,
        email_type="campaign_repermission",
        complained_at=timezone.now(),
    )
    CampaignDelivery.objects.create(
        campaign=campaign,
        wave=first,
        user=complained,
        recipient_user_pk=complained.pk,
        recipient_email=complained.email,
        state=CampaignDelivery.State.SENT,
        email_log=log,
    )
    CampaignDelivery.objects.create(
        campaign=campaign,
        wave=second,
        user=future,
        recipient_user_pk=future.pk,
        recipient_email=future.email,
    )
    campaign_id = campaign.pk
    connection.close()

    context = _auth_context(browser, "pause-admin@test.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/campaigns/{campaign_id}/",
        wait_until="domcontentloaded",
    )

    gate = page.locator('[data-testid="campaign-wave-gate"]')
    expect(gate).to_contain_text("complaint triggered the hard stop")
    expect(page.locator('[data-testid="release-next-wave"]')).to_have_count(0)
    recipients_link = page.get_by_role("link", name="Recipient ledger").last
    ses_link = page.get_by_role("link", name="SES events").last
    assert f"/studio/campaigns/{campaign_id}/recipients/" in recipients_link.get_attribute("href")
    assert f"campaign={campaign_id}" in ses_link.get_attribute("href")
    recipients_link.click()
    page.wait_for_load_state("domcontentloaded")
    assert f"/studio/campaigns/{campaign_id}/recipients/" in page.url
    page.goto(
        f"{django_server}/studio/campaigns/{campaign_id}/",
        wait_until="domcontentloaded",
    )
    page.get_by_role("link", name="SES events").last.click()
    page.wait_for_load_state("domcontentloaded")
    assert f"campaign={campaign_id}" in page.url

    campaign.refresh_from_db()
    second.refresh_from_db()
    assert campaign.status == "paused"
    assert second.state == CampaignWave.State.PENDING
    from django_q.models import Schedule

    assert Schedule.objects.count() == 0
    connection.close()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_late_consent_skips_the_future_wave_delivery(
    django_server,
    browser,
):
    from email_app.models import (
        CampaignDelivery,
        CampaignWave,
        EmailCampaign,
        EmailLog,
    )
    from email_app.services.campaign_repermission import confirmation_url_for_user
    from email_app.tasks.send_campaign import VERIFIED_AT_SEND, send_campaign_batch

    _ensure_tiers()
    _reset_campaign_data()
    staff = _create_staff_user("late-admin@test.com")
    sent_user = _create_user("late-sent@test.com", email_verified=False)
    future_user = _create_user("late-future@test.com", email_verified=False)
    campaign = EmailCampaign.objects.create(
        subject="Late consent",
        body="Introduction",
        audience_verification="unverified_only",
        status="sending",
        audience_snapshotted_at=timezone.now(),
    )
    first = CampaignWave.objects.create(
        campaign=campaign,
        number=1,
        state=CampaignWave.State.MONITORING,
        released_at=timezone.now() - timedelta(hours=25),
        released_by=staff,
        monitoring_started_at=timezone.now() - timedelta(hours=24),
    )
    second = CampaignWave.objects.create(campaign=campaign, number=2)
    log = EmailLog.objects.create(
        campaign=campaign,
        user=sent_user,
        recipient_email=sent_user.email,
        email_type="campaign_repermission",
    )
    CampaignDelivery.objects.create(
        campaign=campaign,
        wave=first,
        user=sent_user,
        recipient_user_pk=sent_user.pk,
        recipient_email=sent_user.email,
        state=CampaignDelivery.State.SENT,
        email_log=log,
    )
    future_delivery = CampaignDelivery.objects.create(
        campaign=campaign,
        wave=second,
        user=future_user,
        recipient_user_pk=future_user.pk,
        recipient_email=future_user.email,
    )
    confirmation_path = _relative(confirmation_url_for_user(future_user))
    campaign_id = campaign.pk
    delivery_id = future_delivery.pk
    connection.close()

    consent_page = browser.new_page()
    consent_page.goto(
        f"{django_server}{confirmation_path}",
        wait_until="domcontentloaded",
    )
    expect(consent_page.get_by_role("heading", name="You're subscribed")).to_be_visible()
    consent_page.close()

    context = _auth_context(browser, "late-admin@test.com")
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(
        f"{django_server}/studio/campaigns/{campaign_id}/",
        wait_until="domcontentloaded",
    )
    page.locator('[data-testid="release-next-wave"]').click()
    page.wait_for_load_state("domcontentloaded")

    with mock.patch(
        "email_app.tasks.send_campaign.EmailService.send_prepared",
    ) as send:
        send_campaign_batch(campaign_id, [delivery_id], send_delay=0)

    future_delivery.refresh_from_db()
    second.refresh_from_db()
    assert future_delivery.state == CampaignDelivery.State.SKIPPED
    assert future_delivery.skip_reason == VERIFIED_AT_SEND
    assert second.state == CampaignWave.State.MONITORING
    send.assert_not_called()
    connection.close()

    page.goto(
        f"{django_server}/studio/campaigns/{campaign_id}/",
        wait_until="domcontentloaded",
    )
    second_row = page.locator('[data-testid="campaign-wave-row"]').nth(1)
    expect(second_row).to_contain_text("Monitoring")
    context.close()
