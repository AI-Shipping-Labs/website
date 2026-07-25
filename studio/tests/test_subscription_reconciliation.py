"""Studio report + read-only check for subscription reconciliation (#1308)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from payments.models import (
    SubscriptionReconciliationFinding as Finding,
)
from payments.models import (
    SubscriptionReconciliationRun as Run,
)
from payments.services import subscription_reconciliation as recon

User = get_user_model()


class ReportViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@t.com", password="x", is_staff=True,
        )
        cls.member = User.objects.create_user(email="member@t.com", password="x")
        cls.url = reverse("studio_subscription_reconciliation")

    def _seed(self):
        run = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            cohort_count=3, actionable_count=1, scheduled_cancellation_count=1,
        )
        Finding.objects.create(
            run=run, email="ended@t.com",
            classification=recon.CLASSIFICATION_ENDED,
            action=recon.ACTION_REVERT_TO_FREE, current_tier="main",
            stripe_status="canceled", outcome=Finding.OUTCOME_WOULD_CHANGE,
        )
        Finding.objects.create(
            run=run, email="sched@t.com",
            classification=recon.CLASSIFICATION_SCHEDULED,
            action=recon.ACTION_REPAIR_SCHEDULED_CANCELLATION,
            current_tier="premium", stripe_status="active",
            cancel_at_period_end=True, outcome=Finding.OUTCOME_REPORTED,
        )
        return run

    def test_non_staff_forbidden(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_staff_sees_summary_and_findings(self):
        self._seed()
        self.client.force_login(self.staff)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ended subscription still entitled")
        self.assertContains(resp, "Revert to Free")
        self.assertContains(resp, "Check all Stripe subscriptions")

    def test_actionable_filter_excludes_scheduled(self):
        self._seed()
        self.client.force_login(self.staff)
        resp = self.client.get(self.url, {"filter": "actionable"})
        self.assertContains(resp, "ended@t.com")
        self.assertNotContains(resp, "sched@t.com")

    def test_scheduled_filter_shows_only_scheduled(self):
        self._seed()
        self.client.force_login(self.staff)
        resp = self.client.get(self.url, {"filter": "scheduled"})
        self.assertContains(resp, "sched@t.com")
        self.assertNotContains(resp, "ended@t.com")

    def test_tier_filter_shows_only_that_tier(self):
        self._seed()
        self.client.force_login(self.staff)
        resp = self.client.get(self.url, {"tier": "main"})
        self.assertContains(resp, "ended@t.com")  # main
        self.assertNotContains(resp, "sched@t.com")  # premium
        resp2 = self.client.get(self.url, {"tier": "premium"})
        self.assertContains(resp2, "sched@t.com")
        self.assertNotContains(resp2, "ended@t.com")

    def test_tier_and_view_filters_combine(self):
        self._seed()
        self.client.force_login(self.staff)
        # Actionable view + Main tier -> only the ended Main row.
        resp = self.client.get(self.url, {"filter": "actionable", "tier": "main"})
        self.assertContains(resp, "ended@t.com")
        self.assertNotContains(resp, "sched@t.com")
        # Actionable view + Premium tier -> nothing (the premium row is scheduled).
        resp2 = self.client.get(
            self.url, {"filter": "actionable", "tier": "premium"}
        )
        self.assertNotContains(resp2, "ended@t.com")
        self.assertNotContains(resp2, "sched@t.com")

    def test_invalid_tier_is_ignored(self):
        self._seed()
        self.client.force_login(self.staff)
        resp = self.client.get(self.url, {"tier": "gold"})
        self.assertEqual(resp.status_code, 200)
        # Unknown tier is ignored, so all rows still render.
        self.assertContains(resp, "ended@t.com")
        self.assertContains(resp, "sched@t.com")

    def test_empty_state_when_no_findings(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.url)
        self.assertContains(resp, "No subscription drift found")

    def test_check_action_enqueues_diagnostic_run(self):
        self.client.force_login(self.staff)
        with patch(
            "studio.views.subscription_reconciliation.async_task"
        ) as aq:
            resp = self.client.post(
                reverse("studio_subscription_reconciliation_check")
            )
        self.assertEqual(resp.status_code, 302)
        run = Run.objects.get()
        self.assertEqual(run.mode, Run.MODE_DIAGNOSTIC)
        self.assertEqual(run.source, Run.SOURCE_STUDIO)
        self.assertEqual(run.status, Run.STATUS_QUEUED)
        aq.assert_called_once()
