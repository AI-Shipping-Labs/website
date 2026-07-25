"""Operator journeys for the subscription reconciliation report (#1308).

Reconciliation state is pre-seeded via the ORM (persisted run + findings) so
the report renders deterministically and never contacts live Stripe.
"""

import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.local_only

REPORT_URL = "/studio/payments/subscription-reconciliation/"


def _reset():
    from accounts.models import User
    from payments.models import SubscriptionReconciliationRun

    SubscriptionReconciliationRun.objects.all().delete()
    User.objects.exclude(email="admin@test.com").delete()
    connection.close()


def _seed():
    from payments.models import (
        SubscriptionReconciliationFinding as Finding,
    )
    from payments.models import (
        SubscriptionReconciliationRun as Run,
    )
    from payments.services import subscription_reconciliation as recon

    run = Run.objects.create(
        status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
        cohort_count=3, actionable_count=1, scheduled_cancellation_count=1,
        warning_count=1,
    )
    Finding.objects.create(
        run=run, email="ended-1308@example.com",
        classification=recon.CLASSIFICATION_ENDED,
        action=recon.ACTION_REVERT_TO_FREE, current_tier="main",
        stripe_status="canceled", outcome=Finding.OUTCOME_WOULD_CHANGE,
        webhook_evidence=Finding.WEBHOOK_MISSING,
    )
    Finding.objects.create(
        run=run, email="scheduled-1308@example.com",
        classification=recon.CLASSIFICATION_SCHEDULED,
        action=recon.ACTION_REPAIR_SCHEDULED_CANCELLATION,
        current_tier="premium", stripe_status="active",
        cancel_at_period_end=True, outcome=Finding.OUTCOME_REPORTED,
    )
    Finding.objects.create(
        run=run, email="dunning-1308@example.com",
        classification=recon.CLASSIFICATION_DUNNING,
        action=recon.ACTION_REVIEW, current_tier="main",
        stripe_status="past_due", outcome=Finding.OUTCOME_WARNING,
    )
    connection.close()


def _staff_page(browser):
    return _auth_context(browser, "admin@test.com").new_page()


@pytest.mark.django_db(transaction=True)
class TestSubscriptionReconciliation1308:
    @pytest.mark.core
    def test_operator_reviews_scheduled_ended_and_dunning(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        _seed()
        page = _staff_page(browser)
        page.goto(f"{django_server}{REPORT_URL}", wait_until="domcontentloaded")

        expect(
            page.get_by_role("heading", name="Subscription reconciliation")
        ).to_be_visible()
        expect(
            page.get_by_role("button", name="Check all Stripe subscriptions")
        ).to_be_visible()
        expect(page.get_by_test_id("count-actionable")).to_have_text("1")

        # Actionable filter: the ended Main member is offered Revert to Free.
        page.goto(
            f"{django_server}{REPORT_URL}?filter=actionable",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("ended-1308@example.com")).to_be_visible()
        expect(page.get_by_text("Revert to Free").first).to_be_visible()
        expect(page.get_by_text("scheduled-1308@example.com")).to_have_count(0)

        # Scheduled filter: the Premium member keeps access; no Revert to Free.
        page.goto(
            f"{django_server}{REPORT_URL}?filter=scheduled",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("scheduled-1308@example.com")).to_be_visible()
        expect(page.get_by_text("Scheduled cancellation").first).to_be_visible()
        expect(page.get_by_text("Revert to Free")).to_have_count(0)

        # Warnings filter: dunning shows the exact live status, not "no active".
        page.goto(
            f"{django_server}{REPORT_URL}?filter=warnings",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("dunning-1308@example.com")).to_be_visible()
        expect(page.get_by_text("past_due").first).to_be_visible()
        expect(page.get_by_text("Revert to Free")).to_have_count(0)

    def test_read_only_check_queues_without_changing_access(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        page = _staff_page(browser)
        page.goto(f"{django_server}{REPORT_URL}", wait_until="domcontentloaded")

        # Empty state before any run.
        expect(page.get_by_text("No subscription drift found")).to_be_visible()

        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_role(
            "button", name="Check all Stripe subscriptions"
        ).click()
        expect(
            page.get_by_text("Read-only check queued.")
        ).to_be_visible()

        from payments.models import SubscriptionReconciliationRun
        run = SubscriptionReconciliationRun.objects.get()
        assert run.mode == SubscriptionReconciliationRun.MODE_DIAGNOSTIC
        assert run.source == SubscriptionReconciliationRun.SOURCE_STUDIO
        connection.close()
