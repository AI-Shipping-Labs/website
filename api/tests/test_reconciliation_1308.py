"""API tests for subscription reconciliation apply guard + run endpoints (#1308).

Stripe is mocked at the ``payments.services.subscription_reconciliation``
helper boundary — never live.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import TierOverride, Token
from payments.models import (
    SubscriptionReconciliationFinding as Finding,
)
from payments.models import (
    SubscriptionReconciliationRun as Run,
)
from payments.models import Tier
from payments.services import subscription_reconciliation as recon
from payments.tests.test_subscription_reconciliation import make_sub

User = get_user_model()

APPLY_URL = "/api/payments/tier-reconcile"
RUNS_URL = "/api/payments/tier-reconcile/runs"


@override_settings(STRIPE_SECRET_KEY="sk_test_1308")
class ApplyGuardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main = Tier.objects.get(slug="main")
        cls.main.stripe_price_id_monthly = "price_main_monthly"
        cls.main.save(update_fields=["stripe_price_id_monthly"])
        cls.admin = User.objects.create_user(
            email="admin@t.com", password="x", is_staff=True, is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="t")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _post(self, body):
        return self.client.post(
            APPLY_URL, data=json.dumps(body),
            content_type="application/json", **self._auth(),
        )

    def _canceled_user(self, email="cx@t.com"):
        return User.objects.create_user(
            email=email, password="x", tier=self.main,
            stripe_customer_id=f"cus_{email}", subscription_id="sub_1",
        )

    def _patch_stripe(self, sub):
        return patch.object(
            recon, "_retrieve_subscription", return_value=sub,
        ), patch.object(
            recon, "_list_subscriptions_for_customer", return_value=[],
        )

    def test_omitted_dry_run_is_read_only_preview(self):
        user = self._canceled_user()
        p1, p2 = self._patch_stripe(make_sub(status="canceled"))
        with p1, p2:
            resp = self._post({"emails": [user.email]})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["dry_run"])
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")

    def test_dry_run_false_without_confirm_is_rejected_no_writes(self):
        user = self._canceled_user()
        p1, p2 = self._patch_stripe(make_sub(status="canceled"))
        with p1, p2:
            resp = self._post({"emails": [user.email], "dry_run": False})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "confirmation_required")
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")

    def test_confirmed_apply_reverts_canceled_and_preserves_override(self):
        user = self._canceled_user()
        override = TierOverride.objects.create(
            user=user, override_tier=self.main, is_active=True,
            expires_at=timezone.now() + timedelta(days=30),
        )
        p1, p2 = self._patch_stripe(make_sub(status="canceled"))
        with p1, p2, patch(
            "payments.services.subscription_transition._services._community_remove"
        ):
            resp = self._post({
                "emails": [user.email],
                "dry_run": False,
                "confirm": "apply_stripe_truth",
            })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["changed"], 1)
        row = body["results"][0]
        self.assertEqual(row["status"], "changed")
        self.assertEqual(row["to"], "free")
        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertTrue(override.is_active)

    def test_duplicate_ownership_targets_are_skipped_on_apply(self):
        u1 = User.objects.create_user(
            email="d1@t.com", password="x", tier=self.main,
            stripe_customer_id="cus_shared", subscription_id="sub_1",
        )
        u2 = User.objects.create_user(
            email="d2@t.com", password="x", tier=self.main,
            stripe_customer_id="cus_shared", subscription_id="sub_1",
        )
        p1, p2 = self._patch_stripe(make_sub(status="canceled"))
        with p1, p2:
            resp = self._post({
                "emails": [u1.email, u2.email],
                "dry_run": False,
                "confirm": "apply_stripe_truth",
            })
        body = resp.json()
        self.assertEqual(body["changed"], 0)
        for row in body["results"]:
            self.assertEqual(row["status"], "warning")
            self.assertIn("Duplicate Stripe ownership", row["message"])
        u1.refresh_from_db()
        self.assertEqual(u1.tier.slug, "main")


@override_settings(STRIPE_SECRET_KEY="sk_test_1308")
class RunEndpointsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin2@t.com", password="x", is_staff=True, is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="t2")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _seed_run(self):
        run = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            source=Run.SOURCE_SCHEDULED, cohort_count=2, actionable_count=1,
        )
        Finding.objects.create(
            run=run, email="ended@t.com", classification=recon.CLASSIFICATION_ENDED,
            action=recon.ACTION_REVERT_TO_FREE, outcome=Finding.OUTCOME_WOULD_CHANGE,
            current_tier="main", stripe_status="canceled",
        )
        Finding.objects.create(
            run=run, email="dun@t.com", classification=recon.CLASSIFICATION_DUNNING,
            action=recon.ACTION_REVIEW, outcome=Finding.OUTCOME_WARNING,
            current_tier="main", stripe_status="past_due",
        )
        return run

    def test_runs_list_requires_staff_token(self):
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 401)

    def test_runs_list_returns_history(self):
        self._seed_run()
        resp = self.client.get(RUNS_URL, **self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)

    def test_run_detail_filters_by_classification(self):
        run = self._seed_run()
        resp = self.client.get(
            f"{RUNS_URL}/{run.id}",
            {"classification": recon.CLASSIFICATION_ENDED},
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(
            body["findings"][0]["classification"], recon.CLASSIFICATION_ENDED
        )

    def test_run_detail_invalid_classification_is_422(self):
        run = self._seed_run()
        resp = self.client.get(
            f"{RUNS_URL}/{run.id}", {"classification": "nope"}, **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(
            resp.json()["details"]["field"], "classification"
        )

    def test_run_detail_filters_by_tier(self):
        run = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            source=Run.SOURCE_SCHEDULED, cohort_count=2,
        )
        Finding.objects.create(
            run=run, email="main-ended@t.com",
            classification=recon.CLASSIFICATION_ENDED,
            action=recon.ACTION_REVERT_TO_FREE,
            outcome=Finding.OUTCOME_WOULD_CHANGE,
            current_tier="main", stripe_status="canceled",
        )
        Finding.objects.create(
            run=run, email="premium-ended@t.com",
            classification=recon.CLASSIFICATION_ENDED,
            action=recon.ACTION_REVERT_TO_FREE,
            outcome=Finding.OUTCOME_WOULD_CHANGE,
            current_tier="premium", stripe_status="canceled",
        )
        resp = self.client.get(
            f"{RUNS_URL}/{run.id}", {"tier": "main"}, **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["findings"][0]["email"], "main-ended@t.com")
        self.assertEqual(body["findings"][0]["current_tier"], "main")

    def test_run_detail_invalid_tier_is_422_field_tier(self):
        run = self._seed_run()
        resp = self.client.get(
            f"{RUNS_URL}/{run.id}", {"tier": "gold"}, **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["details"]["field"], "tier")

    def test_run_detail_returns_next_cursor_when_more_rows(self):
        run = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            source=Run.SOURCE_SCHEDULED, cohort_count=3,
        )
        for i in range(3):
            Finding.objects.create(
                run=run, email=f"row{i}@t.com",
                classification=recon.CLASSIFICATION_ENDED,
                action=recon.ACTION_REVERT_TO_FREE,
                outcome=Finding.OUTCOME_WOULD_CHANGE,
                current_tier="main", stripe_status="canceled",
            )
        resp = self.client.get(
            f"{RUNS_URL}/{run.id}", {"page_size": "2"}, **self._auth(),
        )
        body = resp.json()
        self.assertEqual(body["count"], 3)
        self.assertEqual(len(body["findings"]), 2)
        self.assertEqual(body["next_cursor"], "2")
        # Last page has no further cursor.
        resp2 = self.client.get(
            f"{RUNS_URL}/{run.id}", {"page_size": "2", "page": "2"},
            **self._auth(),
        )
        self.assertIsNone(resp2.json()["next_cursor"])

    def test_runs_list_returns_next_cursor_when_more_rows(self):
        for _ in range(3):
            Run.objects.create(
                status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
                source=Run.SOURCE_SCHEDULED,
            )
        resp = self.client.get(
            RUNS_URL, {"page_size": "2"}, **self._auth(),
        )
        body = resp.json()
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["next_cursor"], "2")

    def test_run_detail_warnings_filter(self):
        run = self._seed_run()
        resp = self.client.get(
            f"{RUNS_URL}/{run.id}", {"filter": "warnings"}, **self._auth(),
        )
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["findings"][0]["stripe_status"], "past_due")

    def test_enqueue_run_returns_202_and_creates_queued_run(self):
        with patch("api.views.subscription_reconciliation.async_task") as aq:
            resp = self.client.post(RUNS_URL, **self._auth())
        self.assertEqual(resp.status_code, 202)
        run_id = resp.json()["run_id"]
        self.assertTrue(Run.objects.filter(pk=run_id).exists())
        aq.assert_called_once()

    def test_enqueue_run_without_token_is_401_no_run_created(self):
        with patch("api.views.subscription_reconciliation.async_task") as aq:
            resp = self.client.post(RUNS_URL)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(Run.objects.count(), 0)
        aq.assert_not_called()

    def test_run_detail_without_token_is_401(self):
        run = self._seed_run()
        resp = self.client.get(f"{RUNS_URL}/{run.id}")
        self.assertEqual(resp.status_code, 401)

    def test_scheduled_becomes_ended_across_run_history(self):
        """#1308 scenario 5: the same member is scheduled on an earlier run and
        ended on a later run; run history exposes both so an operator (or
        automation) can compare them without a Studio run-history-browse UI."""
        earlier = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            source=Run.SOURCE_SCHEDULED, started_at=timezone.now() - timedelta(days=1),
            scheduled_cancellation_count=1,
        )
        Finding.objects.create(
            run=earlier, email="drift@t.com",
            classification=recon.CLASSIFICATION_SCHEDULED,
            action=recon.ACTION_REPAIR_SCHEDULED_CANCELLATION,
            outcome=Finding.OUTCOME_REPORTED, current_tier="premium",
            stripe_status="active", cancel_at_period_end=True,
        )
        later = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            source=Run.SOURCE_SCHEDULED, started_at=timezone.now(),
            actionable_count=1,
        )
        Finding.objects.create(
            run=later, email="drift@t.com",
            classification=recon.CLASSIFICATION_ENDED,
            action=recon.ACTION_REVERT_TO_FREE,
            outcome=Finding.OUTCOME_WOULD_CHANGE, current_tier="premium",
            stripe_status="canceled",
        )
        history = self.client.get(RUNS_URL, **self._auth()).json()
        self.assertEqual(history["count"], 2)
        # Newest-first ordering.
        self.assertEqual(history["runs"][0]["id"], str(later.id))

        earlier_detail = self.client.get(
            f"{RUNS_URL}/{earlier.id}", **self._auth(),
        ).json()
        later_detail = self.client.get(
            f"{RUNS_URL}/{later.id}", **self._auth(),
        ).json()
        self.assertEqual(
            earlier_detail["findings"][0]["classification"],
            recon.CLASSIFICATION_SCHEDULED,
        )
        self.assertEqual(
            later_detail["findings"][0]["classification"],
            recon.CLASSIFICATION_ENDED,
        )
        self.assertEqual(later_detail["run"]["counts"]["actionable"], 1)


DIAGNOSTICS_URL = "/api/payments/tier-reconcile/diagnostics"


@override_settings(STRIPE_SECRET_KEY="sk_test_1308")
class DiagnosticsBackwardCompatTest(TestCase):
    """`/diagnostics` stays backward-compatible: `email` + `include=ok` only.

    The rescoped `tier`/`classification`/cursor filters live on the `runs`
    endpoints (issue #1347), so `/diagnostics` must NOT 422 on those params —
    it ignores anything it does not understand. With no Stripe-linked users in
    the DB the eligible queryset is empty, so this exercises the query-param
    handling without any live Stripe call.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="diag@t.com", password="x", is_staff=True, is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="diag")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_unknown_tier_param_does_not_422(self):
        resp = self.client.get(
            DIAGNOSTICS_URL, {"tier": "gold"}, **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)

    def test_unknown_classification_param_does_not_422(self):
        resp = self.client.get(
            DIAGNOSTICS_URL, {"classification": "nope"}, **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)


class RunsOpenApiSchemaTest(TestCase):
    """The rescoped filters must be discoverable in the generated OpenAPI."""

    @classmethod
    def setUpTestData(cls):
        from api.openapi.builder import build_spec
        from api.urls import urlpatterns

        cls.spec = build_spec(urlpatterns)

    def _op(self, path, method):
        return self.spec["paths"][path][method]

    def test_runs_list_documents_pagination(self):
        op = self._op("/api/payments/tier-reconcile/runs", "get")
        names = {p["name"] for p in op.get("parameters", [])}
        self.assertIn("page", names)
        self.assertIn("page_size", names)

    def test_enqueue_documents_202(self):
        op = self._op("/api/payments/tier-reconcile/runs", "post")
        self.assertIn("202", op["responses"])

    def test_run_detail_documents_filters(self):
        op = self._op(
            "/api/payments/tier-reconcile/runs/{run_id}", "get",
        )
        names = {p["name"] for p in op.get("parameters", [])}
        for expected in ("classification", "tier", "filter", "page", "page_size"):
            self.assertIn(expected, names)
        # 422 and 401 error shapes are declared.
        self.assertIn("422", op["responses"])
        self.assertIn("401", op["responses"])
