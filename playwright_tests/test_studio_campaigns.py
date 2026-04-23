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

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


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

        detail_url = f"{django_server}/studio/campaigns/{campaign.pk}/"
        page.goto(detail_url, wait_until="domcontentloaded")

        # Intercept async_task so the E2E test does not actually enqueue
        # work into the real django-q cluster. The view just needs the
        # call to return a task id.
        with mock.patch(
            "jobs.tasks.async_task", return_value="task-e2e",
        ):
            page.locator('[data-testid="send-campaign-btn"]').click()
            page.wait_for_load_state("domcontentloaded")

        # After the send flow, the worker page is the destination.
        assert "/studio/worker/" in page.url


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

        page.locator('[data-testid="delete-campaign-btn"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Landed on the list.
        assert page.url.rstrip("/").endswith("/studio/campaigns")

        body = page.content()
        # Success flash mentions the subject.
        assert "Deleted draft campaign" in body
        assert "Distinctive Delete Target" in body

        # The empty-state block shows up because the list is now empty.
        assert "Create your first campaign" in body

        # And the record is really gone.
        from email_app.models import EmailCampaign
        assert not EmailCampaign.objects.filter(pk=campaign.pk).exists()
        connection.close()
