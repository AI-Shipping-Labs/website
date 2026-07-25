"""Operator journeys for the subscription reconciliation report (#1308, #1347).

Reconciliation state is pre-seeded via the ORM (persisted run + findings) so
the report renders deterministically and never contacts live Stripe.

Coverage map for the twelve #1308 Playwright scenarios
------------------------------------------------------
Every #1308 scenario has a home — a scripted operator journey here, or a named
Django/API test where a pure-UI journey would be brittle or has no Studio
surface (e.g. run-history comparison, the confirmed-apply API flow):

 1. Check all paid tiers without changing access
    -> Playwright ``test_read_only_check_queues_without_changing_access``
       + ``test_operator_reviews_scheduled_ended_and_dunning``;
       Django ``studio.tests.test_subscription_reconciliation
       .ReportViewTest.test_check_action_enqueues_diagnostic_run``.
 2. Recognize a scheduled cancellation that keeps access
    -> Playwright ``test_operator_reviews_scheduled_ended_and_dunning``
       (Scheduled filter, no Revert to Free);
       Django ``payments.tests.test_subscription_reconciliation`` scheduled tests.
 3. Immediate cancellation missed by webhooks
    -> Playwright ``test_actionable_member_link_and_revert_label``.
 4. Cancellation drift across Basic, Main, and Premium (tier filter)
    -> Playwright ``test_operator_filters_report_by_paid_tier``.
 5. Scheduled becomes ended on a later run (run-history comparison; no Studio
    run-history-browse UI exists)
    -> Django ``api.tests.test_reconciliation_1308
       .RunEndpointsTest.test_scheduled_becomes_ended_across_run_history``.
 6. Safely preview and explicitly apply canceled members through the API
    -> Django ``api.tests.test_reconciliation_1308.ApplyGuardTest``
       (omitted dry_run preview, confirm gate, confirmed apply + override).
 7. Distinguish payment failure from cancellation
    -> Playwright ``test_operator_reviews_scheduled_ended_and_dunning``
       (Warnings filter shows past_due, never Revert to Free);
       Django ``payments.tests.test_subscription_reconciliation`` dunning tests.
 8. Distinguish reconciliation from checkout identity mismatches
    -> Playwright ``test_distinct_from_payment_mismatches_nav``.
 9. Useful clean no-findings report
    -> Playwright ``test_read_only_check_queues_without_changing_access``
       (empty state).
10. Duplicate Stripe ownership before any account is changed
    -> Playwright ``test_duplicate_ownership_shows_conflicts_and_no_apply``;
       Django ``api.tests.test_reconciliation_1308
       .ApplyGuardTest.test_duplicate_ownership_targets_are_skipped_on_apply``.
11. Missed deletion webhook vs a processing failure
    -> Playwright ``test_missed_vs_failed_deletion_webhook_evidence``.
12. Unauthorized member cannot inspect or run reconciliation
    -> Playwright ``test_non_staff_cannot_open_report``;
       Django ``studio.tests.test_subscription_reconciliation
       .ReportViewTest.test_non_staff_forbidden`` +
       ``api.tests.test_reconciliation_1308
       .RunEndpointsTest.test_runs_list_requires_staff_token``.
"""

import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
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

    def test_operator_filters_report_by_paid_tier(self, django_server, browser):
        """#1308 scenario 4: one stale canceled member per paid tier; the tier
        filter round-trips through the report."""
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        self._seed_one_ended_per_tier()
        page = self._staff_page_local(browser)

        page.goto(f"{django_server}{REPORT_URL}", wait_until="domcontentloaded")
        # All three paid tiers are present before filtering.
        expect(page.get_by_text("basic-1347@example.com")).to_be_visible()
        expect(page.get_by_text("main-1347@example.com")).to_be_visible()
        expect(page.get_by_text("premium-1347@example.com")).to_be_visible()

        page.goto(
            f"{django_server}{REPORT_URL}?tier=main",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("main-1347@example.com")).to_be_visible()
        expect(page.get_by_text("basic-1347@example.com")).to_have_count(0)
        expect(page.get_by_text("premium-1347@example.com")).to_have_count(0)

        page.goto(
            f"{django_server}{REPORT_URL}?tier=premium",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("premium-1347@example.com")).to_be_visible()
        expect(page.get_by_text("main-1347@example.com")).to_have_count(0)

        page.goto(
            f"{django_server}{REPORT_URL}?tier=basic",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("basic-1347@example.com")).to_be_visible()
        expect(page.get_by_text("premium-1347@example.com")).to_have_count(0)

    def test_actionable_member_link_and_revert_label(self, django_server, browser):
        """#1308 scenario 3: an ended-but-still-paid member is Actionable with a
        Revert to Free proposal, a missing-deletion-webhook badge, and a link to
        the Studio member detail."""
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        member = _create_user("linked-1347@example.com", tier_slug="main")
        self._seed_finding(
            email=member.email, tier="main", user=member,
            classification="ended_subscription_still_entitled",
            action="revert_to_free", stripe_status="canceled",
            outcome="would_change", webhook_evidence="missing",
        )
        page = self._staff_page_local(browser)
        page.goto(
            f"{django_server}{REPORT_URL}?filter=actionable",
            wait_until="domcontentloaded",
        )
        link = page.get_by_test_id("reconciliation-member-link")
        expect(link).to_be_visible()
        expect(link).to_have_text("linked-1347@example.com")
        expect(page.get_by_text("Revert to Free").first).to_be_visible()
        expect(
            page.get_by_test_id("reconciliation-webhook-evidence")
        ).to_have_text("Deletion webhook missing")

    def test_duplicate_ownership_shows_conflicts_and_no_apply(
        self, django_server, browser
    ):
        """#1308 scenario 10: two accounts share a Stripe customer; both show
        Duplicate Stripe ownership with conflicting-account links and no
        Revert to Free apply."""
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        a = _create_user("dup-a-1347@example.com", tier_slug="main")
        b = _create_user("dup-b-1347@example.com", tier_slug="main")
        run = self._new_run(actionable_count=2)
        self._seed_finding(
            email=a.email, tier="main", user=a, run=run,
            classification="duplicate_stripe_ownership", action="review",
            stripe_status="active", outcome="warning",
            stripe_customer_id="cus_shared", conflicting_user_ids=[b.pk],
        )
        self._seed_finding(
            email=b.email, tier="main", user=b, run=run,
            classification="duplicate_stripe_ownership", action="review",
            stripe_status="active", outcome="warning",
            stripe_customer_id="cus_shared", conflicting_user_ids=[a.pk],
        )
        page = self._staff_page_local(browser)
        page.goto(
            f"{django_server}{REPORT_URL}?filter=actionable",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("dup-a-1347@example.com")).to_be_visible()
        expect(page.get_by_text("dup-b-1347@example.com")).to_be_visible()
        expect(
            page.get_by_text("Duplicate Stripe ownership").first
        ).to_be_visible()
        expect(page.get_by_text("Conflicting account", exact=False).first).to_be_visible()
        expect(page.get_by_text("Revert to Free")).to_have_count(0)

    def test_missed_vs_failed_deletion_webhook_evidence(
        self, django_server, browser
    ):
        """#1308 scenario 11: a canceled account with no local deletion event
        reads 'missing'; one with a failed-permanent event reads 'failed'."""
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        run = self._new_run(actionable_count=2)
        self._seed_finding(
            email="missing-1347@example.com", tier="main", run=run,
            classification="suspected_missed_subscription_deleted",
            action="revert_to_free", stripe_status="canceled",
            outcome="would_change", webhook_evidence="missing",
        )
        self._seed_finding(
            email="failed-1347@example.com", tier="main", run=run,
            classification="suspected_missed_subscription_deleted",
            action="revert_to_free", stripe_status="canceled",
            outcome="would_change", webhook_evidence="failed_permanent",
        )
        page = self._staff_page_local(browser)
        page.goto(
            f"{django_server}{REPORT_URL}?filter=actionable",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("Deletion webhook missing")).to_be_visible()
        expect(page.get_by_text("Deletion webhook failed")).to_be_visible()

    def test_distinct_from_payment_mismatches_nav(self, django_server, browser):
        """#1308 scenario 8: the report copy and its Payment mismatches link make
        the two operator tools visibly distinct."""
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        page = self._staff_page_local(browser)
        page.goto(f"{django_server}{REPORT_URL}", wait_until="domcontentloaded")
        expect(
            page.get_by_role("heading", name="Subscription reconciliation")
        ).to_be_visible()
        # The report explains it is different from checkout Payment mismatches.
        expect(
            page.get_by_text("Different from checkout Payment mismatches")
        ).to_be_visible()
        # A distinct link to the Payment mismatches tool is present.
        expect(
            page.get_by_role("link", name="Payment mismatches").first
        ).to_be_visible()

    def test_non_staff_cannot_open_report(self, django_server, browser):
        """#1308 scenario 12: a logged-in non-staff member is denied the report
        and cannot trigger the read-only check."""
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        _create_user("member-1347@example.com", tier_slug="main")
        page = _auth_context(browser, "member-1347@example.com").new_page()
        response = page.goto(
            f"{django_server}{REPORT_URL}", wait_until="domcontentloaded"
        )
        assert response is not None and response.status == 403
        expect(
            page.get_by_role("button", name="Check all Stripe subscriptions")
        ).to_have_count(0)

    # --- seeding helpers -------------------------------------------------

    def _staff_page_local(self, browser):
        return _auth_context(browser, "admin@test.com").new_page()

    def _new_run(self, **counts):
        from payments.models import SubscriptionReconciliationRun as Run

        run = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC, **counts,
        )
        connection.close()
        return run

    def _seed_finding(self, *, email, tier, run=None, user=None, **fields):
        from payments.models import (
            SubscriptionReconciliationFinding as Finding,
        )
        from payments.models import SubscriptionReconciliationRun as Run

        if run is None:
            run = Run.objects.create(
                status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
                actionable_count=1,
            )
        Finding.objects.create(
            run=run, email=email, current_tier=tier, user=user, **fields,
        )
        connection.close()
        return run

    def _seed_one_ended_per_tier(self):
        run = self._new_run(actionable_count=3)
        for tier in ("basic", "main", "premium"):
            self._seed_finding(
                email=f"{tier}-1347@example.com", tier=tier, run=run,
                classification="ended_subscription_still_entitled",
                action="revert_to_free", stripe_status="canceled",
                outcome="would_change",
            )
        return run
