"""
Playwright E2E tests for the Studio campaign detail page (issue #292).

Only the ``window.confirm()``-gated Send and Delete flows are exercised
here — every other behaviour is covered by Django TestCases in
``studio/tests/test_campaigns.py`` per the testing guidelines.

Scenarios:
1. Staff author aborts a Send (confirm Cancel) — no navigation, still draft.
2. Staff author confirms a Send (confirm OK)  — stays on the campaign page; Relay owns sending.
3. Staff author aborts a Delete (confirm Cancel) — no navigation, record kept.
4. Staff author confirms a Delete (confirm OK)   — lands on /studio/campaigns/,
   campaign no longer listed.
"""

import os
from unittest import mock

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_campaigns():
    """Delete all campaigns and email logs to ensure clean state."""
    from email_app.models import EmailCampaign, EmailLog

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    connection.close()


def _create_campaign(subject, body="Body content", status="draft", target_min_level=0):
    """Create an EmailCampaign via the ORM."""
    from email_app.models import EmailCampaign

    campaign = EmailCampaign.objects.create(
        subject=subject,
        body=body,
        status=status,
        target_min_level=target_min_level,
    )
    connection.close()
    return campaign


def _seed_one_eligible_recipient(email="recipient@test.com"):
    """Ensure at least one eligible recipient exists for campaigns."""
    _create_user(
        email,
        tier_slug="free",
        email_verified=True,
        unsubscribed=False,
        is_staff=False,
    )


def _create_ambiguous_delivery(subject):
    from django.utils import timezone

    from email_app.models import CampaignDelivery, EmailCampaign

    recipient = _create_user(
        f"{subject.lower().replace(' ', '-')}@test.com",
        tier_slug="free",
        email_verified=True,
        unsubscribed=False,
        is_staff=False,
    )
    campaign = EmailCampaign.objects.create(
        subject=subject,
        body="Body content",
        status="needs_attention",
        audience_snapshotted_at=timezone.now(),
    )
    delivery = CampaignDelivery.objects.create(
        campaign=campaign,
        user=recipient,
        recipient_user_pk=recipient.pk,
        recipient_email=recipient.email,
        state=CampaignDelivery.State.AMBIGUOUS,
        attempt_count=1,
        last_error="Transport outcome indeterminate.",
        completed_at=timezone.now(),
    )
    connection.close()
    return campaign, delivery


# ---------------------------------------------------------------
# Scenario 1: Staff author aborts a risky send — campaign stays draft
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStaffAbortsSend:
    """Clicking Cancel on the Send confirm dialog keeps the campaign as draft."""

    def test_cancel_send_keeps_draft(self, django_server, browser):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        _seed_one_eligible_recipient("send-cancel@test.com")
        campaign = _create_campaign("Abort Send", status="draft")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # Dismiss the confirm() dialog as it pops up.
        page.on("dialog", lambda dialog: dialog.dismiss())

        detail_url = f"{django_server}/studio/campaigns/{campaign.pk}/"
        page.goto(detail_url, wait_until="domcontentloaded")

        preview = page.get_by_title("Campaign email preview", exact=True)
        assert preview.count() == 1
        assert preview.is_visible()
        assert preview.get_attribute("data-testid") == "campaign-preview-iframe"
        assert page.get_by_role("heading", name="Test Send", exact=True).is_visible()
        assert page.get_by_text("Status", exact=True).is_visible()

        # Click the Send button. Because the dialog is dismissed, the
        # onsubmit handler returns false and the POST never fires.
        page.locator('[data-testid="send-campaign-btn"]').click()

        # No navigation happened — still on the detail URL.
        assert page.url.rstrip("/") == detail_url.rstrip("/")

        # Campaign is still in draft status.
        from email_app.models import EmailCampaign
        campaign.refresh_from_db()
        assert campaign.status == "draft"
        EmailCampaign.objects.filter(pk=campaign.pk).update()
        connection.close()


# ---------------------------------------------------------------
# Scenario 2: Staff author confirms a send — navigates to worker
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStaffConfirmsSend:
    """Clicking OK on the Send confirm dialog submits the campaign to Relay."""

    def test_confirm_send_navigates_to_worker(self, django_server, browser):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        _seed_one_eligible_recipient("send-confirm@test.com")
        campaign = _create_campaign("Confirm Send", status="draft")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.on("dialog", lambda dialog: dialog.accept())

        # The low-level model form remains reachable but has no delivery
        # actions; sending is owned exclusively by Studio.
        page.goto(
            f"{django_server}/admin/email_app/emailcampaign/{campaign.pk}/change/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text("Send Test Email", exact=True).count() == 0
        assert page.get_by_text("Send Campaign", exact=True).count() == 0
        assert page.locator('[href*="send-test"], [href*="send-campaign"]').count() == 0

        detail_url = f"{django_server}/studio/campaigns/{campaign.pk}/"
        page.goto(detail_url, wait_until="domcontentloaded")

        # Stub the single Relay submit so the E2E test never calls Relay.
        with mock.patch(
            "email_app.services.campaign_submit.submit_native_campaign",
            return_value={"queued": True},
        ) as mock_submit:
            page.locator('[data-testid="send-campaign-btn"]').click()
            page.wait_for_load_state("domcontentloaded")

        # Relay owns sending: no worker page, no queue line.
        assert f"/studio/campaigns/{campaign.pk}/" in page.url
        assert "/studio/worker/" not in page.url
        assert "Queue:" not in page.content()
        campaign.refresh_from_db()
        assert campaign.status == "sending"
        assert mock_submit.call_count == 1


# ---------------------------------------------------------------
# Scenario 3: Staff author aborts a draft delete
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStaffAbortsDelete:
    """Clicking Cancel on the Delete confirm dialog keeps the campaign."""

    def test_cancel_delete_keeps_campaign(self, django_server, browser):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        campaign = _create_campaign("Keep Me", status="draft")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.on("dialog", lambda dialog: dialog.dismiss())

        detail_url = f"{django_server}/studio/campaigns/{campaign.pk}/"
        page.goto(detail_url, wait_until="domcontentloaded")

        page.locator('[data-testid="studio-header-overflow"] summary').click()
        page.locator('[data-testid="delete-campaign-btn"]').click()

        # Dialog dismissed → no navigation.
        assert page.url.rstrip("/") == detail_url.rstrip("/")

        # Campaign still exists.
        from email_app.models import EmailCampaign
        assert EmailCampaign.objects.filter(pk=campaign.pk).exists()
        connection.close()


# ---------------------------------------------------------------
# Scenario 4: Staff author confirms a draft delete
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStaffConfirmsDelete:
    """Clicking OK on the Delete confirm dialog removes the campaign and
    lands the operator on the list view with a success message mentioning
    the deleted subject."""

    def test_confirm_delete_redirects_to_list_with_success(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        campaign = _create_campaign(
            "Distinctive Delete Target", status="draft",
        )

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.on("dialog", lambda dialog: dialog.accept())

        detail_url = f"{django_server}/studio/campaigns/{campaign.pk}/"
        page.goto(detail_url, wait_until="domcontentloaded")

        page.locator('[data-testid="studio-header-overflow"] summary').click()
        page.locator('[data-testid="delete-campaign-btn"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Landed on the list.
        assert page.url.rstrip("/").endswith("/studio/campaigns")

        body = page.content()
        # Success flash mentions the subject.
        assert "Deleted draft campaign" in body
        assert "Distinctive Delete Target" in body

        # The canonical fresh-zero empty-state block shows up because the
        # list is now empty (#756 — studio_empty_state partial).
        assert "No campaigns yet" in body
        assert "New campaign" in body

        # And the record is really gone.
        from email_app.models import EmailCampaign
        assert not EmailCampaign.objects.filter(pk=campaign.pk).exists()
        connection.close()
