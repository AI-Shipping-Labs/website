"""
Playwright E2E tests for the Studio campaign detail page (issue #292).

Only the ``window.confirm()``-gated Send and Delete flows are exercised
here — every other behaviour is covered by Django TestCases in
``studio/tests/test_campaigns.py`` per the testing guidelines.

Scenarios:
1. Staff author aborts a Send (confirm Cancel) — no navigation, still draft.
2. Staff author confirms a Send (confirm OK)  — navigates to /studio/worker/.
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
from scripts.browser_journey_policy import browser_journey

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
    """Clicking OK on the Send confirm dialog queues the send."""

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

        # Intercept async_task so the E2E test does not actually enqueue
        # work into the real django-q cluster. The view just needs the
        # call to return a task id.
        with mock.patch(
            "jobs.tasks.async_task", return_value="task-e2e",
        ) as mock_async_task:
            page.locator('[data-testid="send-campaign-btn"]').click()
            page.wait_for_load_state("domcontentloaded")

        # After the send flow, the worker page is the destination.
        assert "/studio/worker/" in page.url
        campaign.refresh_from_db()
        assert campaign.status == "sending"
        assert mock_async_task.call_count == 1


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


@pytest.mark.django_db(transaction=True)
class TestStaffReconcilesAmbiguousDelivery:
    @pytest.mark.core
    @browser_journey
    def test_duplicate_risk_confirmations_gate_retry_and_assume_sent(
        self,
        django_server,
        browser,
    ):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        campaign, delivery = _create_ambiguous_delivery("Retry Ambiguous")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        recipients_url = (
            f"{django_server}/studio/campaigns/{campaign.pk}/recipients?attention=1"
        )
        page.goto(recipients_url, wait_until="domcontentloaded")

        dismissed = []

        def dismiss_retry(dialog):
            dismissed.append(dialog.message)
            dialog.dismiss()

        page.once("dialog", dismiss_retry)
        page.locator('[data-testid="campaign-delivery-retry"]').click()
        assert dismissed == [
            "SES may already have accepted this email. Retrying can send a duplicate. Retry anyway?"
        ]
        assert page.get_by_test_id("campaign-delivery-state").get_by_text(
            "Ambiguous",
            exact=True,
        ).is_visible()

        accepted = []

        def accept_retry(dialog):
            accepted.append(dialog.message)
            dialog.accept()

        page.once("dialog", accept_retry)
        page.locator('[data-testid="campaign-delivery-retry"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert accepted == dismissed
        assert page.get_by_text(
            f"Queued retry for {delivery.recipient_email}.",
            exact=True,
        ).is_visible()
        assert page.get_by_test_id("campaign-delivery-state").get_by_text(
            "Pending",
            exact=True,
        ).is_visible()

        assume_campaign, assume_delivery = _create_ambiguous_delivery(
            "Assume Ambiguous",
        )
        page.goto(
            f"{django_server}/studio/campaigns/{assume_campaign.pk}/recipients?attention=1",
            wait_until="domcontentloaded",
        )
        assume_messages = []

        def accept_assume(dialog):
            assume_messages.append(dialog.message)
            dialog.accept()

        page.once("dialog", accept_assume)
        page.locator('[data-testid="campaign-delivery-assume"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert assume_messages == [
            "Mark this recipient as assumed delivered without sending again?"
        ]
        assert page.get_by_text(
            f"Marked {assume_delivery.recipient_email} as assumed sent without resending.",
            exact=True,
        ).is_visible()
        assert page.get_by_test_id("campaign-delivery-state").get_by_text(
            "Assumed sent",
            exact=True,
        ).is_visible()
        connection.close()


def _create_snapshotted_delivery(email, *, state, subject, unsubscribed=False):
    from django.utils import timezone

    from email_app.models import CampaignDelivery, EmailCampaign

    recipient = _create_user(
        email,
        tier_slug="free",
        email_verified=True,
        unsubscribed=unsubscribed,
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
        state=state,
        attempt_count=1,
        last_error="Needs operator review.",
        completed_at=timezone.now(),
    )
    connection.close()
    return campaign, delivery, recipient


@pytest.mark.django_db(transaction=True)
class TestStaffSeesNeedsAttentionAfterHardRejection:
    @pytest.mark.core
    @browser_journey
    def test_hard_ses_rejection_leaves_needs_attention_not_sending(
        self,
        django_server,
        browser,
    ):
        from botocore.exceptions import ClientError

        from email_app.models import CampaignDelivery
        from email_app.services.email_service import EmailServiceError
        from email_app.tasks.send_campaign import send_campaign, send_campaign_batch

        _ensure_tiers()
        _clear_campaigns()
        staff = _create_staff_user("admin@test.com")
        staff.unsubscribed = True
        staff.save(update_fields=["unsubscribed"])
        _seed_one_eligible_recipient("hard-fail@test.com")
        campaign = _create_campaign("Hard Rejection", status="draft")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())

        with mock.patch("jobs.tasks.async_task", return_value="task-e2e"):
            page.goto(
                f"{django_server}/studio/campaigns/{campaign.pk}/",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="send-campaign-btn"]').click()
            page.wait_for_load_state("domcontentloaded")

        def reject(_prepared):
            try:
                raise ClientError(
                    {"Error": {"Code": "MessageRejected", "Message": "rejected"}},
                    "SendEmail",
                )
            except ClientError as exc:
                raise EmailServiceError("send failed") from exc

        send_campaign(campaign.pk)
        delivery_ids = list(
            CampaignDelivery.objects.filter(campaign=campaign)
            .values_list("pk", flat=True)
        )
        with mock.patch(
            "email_app.tasks.send_campaign.EmailService.send_prepared",
            side_effect=reject,
        ):
            send_campaign_batch(campaign.pk, delivery_ids, send_delay=0)
        connection.close()

        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text("Needs attention", exact=True).is_visible()
        assert page.get_by_text("Sending", exact=True).count() == 0
        assert page.get_by_text("Sent", exact=True).count() == 0
        counts = page.get_by_test_id("campaign-delivery-counts")
        assert counts.get_by_text("Failed: 1", exact=True).is_visible()
        assert page.get_by_test_id("campaign-attention-link").get_by_text(
            "Review 1 recipient needing attention",
            exact=True,
        ).is_visible()

        page.get_by_test_id("campaign-attention-link").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.get_by_text("hard-fail@test.com").is_visible()
        assert page.get_by_test_id("campaign-delivery-state").get_by_text(
            "Failed",
            exact=True,
        ).is_visible()
        assert page.get_by_test_id("campaign-delivery-retry").is_visible()
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestStaffRetriesFailedRecipient:
    @pytest.mark.core
    @browser_journey
    def test_retry_failed_recipient_can_finish_the_campaign(
        self,
        django_server,
        browser,
    ):
        from email_app.models import CampaignDelivery
        from email_app.tasks.send_campaign import send_campaign_batch

        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        campaign, delivery, _recipient = _create_snapshotted_delivery(
            "retry-me@test.com",
            state=CampaignDelivery.State.FAILED,
            subject="Retry Failed",
        )
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        with mock.patch("jobs.tasks.async_task", return_value="retry-task"):
            page.goto(
                f"{django_server}/studio/campaigns/{campaign.pk}/recipients?attention=1",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="campaign-delivery-retry"]').click()
            page.wait_for_load_state("domcontentloaded")

        assert page.get_by_text(
            "Queued retry for retry-me@test.com.",
            exact=True,
        ).is_visible()

        with mock.patch(
            "email_app.tasks.send_campaign.EmailService.send_prepared",
            return_value="retried-ok",
        ):
            send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)
        connection.close()

        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text("Sent", exact=True).is_visible()
        assert page.get_by_test_id("campaign-delivery-counts").get_by_text(
            "Failed: 0",
            exact=True,
        ).is_visible()
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestStaffAssumesAmbiguousWithoutResend:
    @pytest.mark.core
    @browser_journey
    def test_assume_delivered_does_not_create_email_log(
        self,
        django_server,
        browser,
    ):
        from email_app.models import CampaignDelivery, EmailLog

        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        campaign, delivery, _recipient = _create_snapshotted_delivery(
            "assume-me@test.com",
            state=CampaignDelivery.State.AMBIGUOUS,
            subject="Assume Ambiguous 1506",
        )
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())

        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/recipients?attention=1",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="campaign-delivery-assume"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert page.get_by_text(
            "Marked assume-me@test.com as assumed sent without resending.",
            exact=True,
        ).is_visible()

        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )
        campaign.refresh_from_db()
        assert campaign.status == "sent"
        assert campaign.sent_count == 0
        assert not EmailLog.objects.filter(
            campaign=campaign,
            recipient_email=delivery.recipient_email,
        ).exists()
        assert page.get_by_text("Sent", exact=True).is_visible()
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestStaffSeesFrozenAudience:
    @pytest.mark.core
    @browser_journey
    def test_late_joiner_is_not_listed_on_recipients(
        self,
        django_server,
        browser,
    ):
        from email_app.models import CampaignDelivery

        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        campaign, _delivery, _recipient = _create_snapshotted_delivery(
            "first@test.com",
            state=CampaignDelivery.State.PENDING,
            subject="Frozen Audience",
        )
        _create_user(
            "late-joiner@test.com",
            tier_slug="free",
            email_verified=True,
            unsubscribed=False,
            is_staff=False,
        )
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/recipients",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text("first@test.com").is_visible()
        assert page.get_by_text("late-joiner@test.com").count() == 0
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_test_id("eligible-recipients").get_by_text(
            "1",
            exact=True,
        ).is_visible()
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestStaffSeesSkippedSnapshotAsSent:
    @pytest.mark.core
    @browser_journey
    def test_unsubscribed_snapshot_completes_as_sent(
        self,
        django_server,
        browser,
    ):
        from email_app.models import CampaignDelivery
        from email_app.tasks.send_campaign import send_campaign_batch

        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")
        campaign, delivery, recipient = _create_snapshotted_delivery(
            "skipped@test.com",
            state=CampaignDelivery.State.PENDING,
            subject="Skipped Snapshot",
        )
        recipient.unsubscribed = True
        recipient.save(update_fields=["unsubscribed"])
        EmailCampaign = campaign.__class__
        EmailCampaign.objects.filter(pk=campaign.pk).update(status="sending")
        send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text("Sent", exact=True).is_visible()
        counts = page.get_by_test_id("campaign-delivery-counts")
        assert counts.get_by_text("Skipped: 1", exact=True).is_visible()
        assert counts.get_by_text("Sent: 0", exact=True).is_visible()
        assert counts.get_by_text("Pending: 0", exact=True).is_visible()
        assert page.get_by_test_id("campaign-attention-link").count() == 0
        connection.close()
