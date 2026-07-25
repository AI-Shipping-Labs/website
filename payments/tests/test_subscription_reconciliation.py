"""Tests for cohort-wide Stripe subscription reconciliation (issue #1308).

Stripe is always mocked — these tests never contact live Stripe. They patch
``payments.services.subscription_reconciliation`` module-level helpers
``_retrieve_subscription`` / ``_list_subscriptions_for_customer`` so the state
contract is exercised deterministically.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import (
    StripeWebhookDeliveryAttempt,
    Tier,
    WebhookEvent,
)
from payments.models import (
    SubscriptionReconciliationFinding as Finding,
)
from payments.models import (
    SubscriptionReconciliationRun as Run,
)
from payments.services import subscription_reconciliation as recon

User = get_user_model()

FUTURE_TS = 1_900_000_000  # far-future unix timestamp


class _Pager:
    def __init__(self, items):
        self._items = items

    def auto_paging_iter(self):
        return iter(self._items)


def make_sub(
    *,
    sub_id="sub_1",
    status="active",
    cancel_at_period_end=False,
    price_id="price_main_monthly",
    current_period_end=FUTURE_TS,
    tier_slug="main",
):
    price = {"id": price_id, "metadata": {}}
    if tier_slug:
        price["metadata"] = {"tier_slug": tier_slug}
    return {
        "id": sub_id,
        "status": status,
        "cancel_at_period_end": cancel_at_period_end,
        "current_period_end": current_period_end,
        "items": {"data": [{"price": price}]},
    }


@override_settings(STRIPE_SECRET_KEY="sk_test_recon")
class ReconBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.main = Tier.objects.get(slug="main")
        cls.main.stripe_price_id_monthly = "price_main_monthly"
        cls.main.save(update_fields=["stripe_price_id_monthly"])
        cls.premium = Tier.objects.get(slug="premium")

    def _user(self, email, **kwargs):
        kwargs.setdefault("stripe_customer_id", f"cus_{email.split('@')[0]}")
        return User.objects.create_user(email=email, password="x", **kwargs)

    def _classify(self, user, sub=None, subs=None):
        with patch.object(
            recon, "_retrieve_subscription", return_value=sub
        ), patch.object(
            recon, "_list_subscriptions_for_customer",
            return_value=subs if subs is not None else ([sub] if sub else []),
        ):
            return recon.classify_user(user)


class CohortSelectionTest(ReconBase):
    def test_cohort_includes_all_stripe_indicators(self):
        paid = self._user("paid@t.com", tier=self.main, stripe_customer_id="")
        with_cus = self._user("cus@t.com", stripe_customer_id="cus_x")
        with_sub = self._user(
            "sub@t.com", stripe_customer_id="", subscription_id="sub_z",
        )
        tagged_active = self._user(
            "active@t.com", stripe_customer_id="", tags=["stripe:active"],
        )
        tagged_churn = self._user(
            "churn@t.com", stripe_customer_id="", tags=["stripe:churned"],
        )
        tagged_plan = self._user(
            "plan@t.com", stripe_customer_id="", tags=["stripe:plan-main"],
        )
        # Not in cohort: free, no stripe id/sub/tags.
        outsider = self._user("free@t.com", stripe_customer_id="", tier=self.free)

        cohort_ids = {u.pk for u in recon.cohort_queryset()}
        for u in (paid, with_cus, with_sub, tagged_active, tagged_churn, tagged_plan):
            self.assertIn(u.pk, cohort_ids, msg=u.email)
        self.assertNotIn(outsider.pk, cohort_ids)


class ClassificationMatrixTest(ReconBase):
    def test_active_in_sync_is_ok(self):
        user = self._user(
            "a@t.com", tier=self.main, subscription_id="sub_1",
            billing_period_end=datetime.fromtimestamp(FUTURE_TS, tz=dt_timezone.utc),
        )
        result = self._classify(user, sub=make_sub(status="active"))
        self.assertEqual(result.classification, recon.CLASSIFICATION_OK)

    def test_active_metadata_drift_flagged(self):
        user = self._user("b@t.com", tier=self.free, subscription_id="")
        result = self._classify(user, sub=make_sub(status="active"))
        self.assertEqual(
            result.classification, recon.CLASSIFICATION_ACTIVE_METADATA
        )
        self.assertEqual(result.action, recon.ACTION_REPAIR_ACTIVE_METADATA)
        self.assertEqual(result.stripe_status, "active")

    def test_scheduled_cancellation(self):
        user = self._user("c@t.com", tier=self.main, subscription_id="sub_1")
        result = self._classify(
            user, sub=make_sub(status="active", cancel_at_period_end=True),
        )
        self.assertEqual(result.classification, recon.CLASSIFICATION_SCHEDULED)
        self.assertTrue(result.cancel_at_period_end)
        self.assertIsNotNone(result.stripe_period_end)
        self.assertEqual(
            result.action, recon.ACTION_REPAIR_SCHEDULED_CANCELLATION
        )

    def test_ended_still_entitled_with_processed_webhook(self):
        user = self._user("d@t.com", tier=self.main, subscription_id="sub_1")
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_del",
            event_type="customer.subscription.deleted",
            stripe_subscription_id="sub_1",
            stripe_customer_id=user.stripe_customer_id,
            outcome=StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED,
            finished_at=timezone.now(),
        )
        result = self._classify(user, sub=make_sub(status="canceled"))
        self.assertEqual(result.classification, recon.CLASSIFICATION_ENDED)
        self.assertEqual(result.action, recon.ACTION_REVERT_TO_FREE)
        self.assertEqual(result.webhook_evidence, Finding.WEBHOOK_PROCESSED)

    def test_ended_with_missing_webhook_is_suspected_missed(self):
        user = self._user("e@t.com", tier=self.main, subscription_id="sub_1")
        result = self._classify(user, sub=make_sub(status="canceled"))
        self.assertEqual(
            result.classification,
            recon.CLASSIFICATION_SUSPECTED_MISSED_DELETE,
        )
        self.assertEqual(result.action, recon.ACTION_REVERT_TO_FREE)
        self.assertEqual(result.webhook_evidence, Finding.WEBHOOK_MISSING)

    def test_ended_with_failed_webhook_shown_distinctly(self):
        user = self._user("f@t.com", tier=self.main, subscription_id="sub_1")
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_fail",
            event_type="customer.subscription.deleted",
            stripe_subscription_id="sub_1",
            outcome=StripeWebhookDeliveryAttempt.OUTCOME_FAILED_PERMANENT,
            finished_at=timezone.now(),
        )
        result = self._classify(user, sub=make_sub(status="canceled"))
        self.assertEqual(
            result.webhook_evidence, Finding.WEBHOOK_FAILED_PERMANENT
        )
        self.assertEqual(result.action, recon.ACTION_REVERT_TO_FREE)

    def test_past_due_is_dunning_not_canceled(self):
        user = self._user("g@t.com", tier=self.main, subscription_id="sub_1")
        result = self._classify(user, sub=make_sub(status="past_due"))
        self.assertEqual(result.classification, recon.CLASSIFICATION_DUNNING)
        self.assertEqual(result.stripe_status, "past_due")
        self.assertNotEqual(result.action, recon.ACTION_REVERT_TO_FREE)

    def test_unpaid_is_dunning(self):
        user = self._user("h@t.com", tier=self.main, subscription_id="sub_1")
        result = self._classify(user, sub=make_sub(status="unpaid"))
        self.assertEqual(result.classification, recon.CLASSIFICATION_DUNNING)

    def test_incomplete_is_non_entitled_review(self):
        user = self._user("i@t.com", tier=self.main, subscription_id="sub_1")
        result = self._classify(user, sub=make_sub(status="incomplete"))
        self.assertEqual(
            result.classification, recon.CLASSIFICATION_NON_ENTITLED_REVIEW
        )
        self.assertEqual(result.stripe_status, "incomplete")

    def test_no_subscription_for_paid_user_is_missing_not_downgrade(self):
        user = self._user("j@t.com", tier=self.main, subscription_id="")
        result = self._classify(user, sub=None, subs=[])
        self.assertEqual(
            result.classification, recon.CLASSIFICATION_MISSING_SUBSCRIPTION
        )
        self.assertNotEqual(result.action, recon.ACTION_REVERT_TO_FREE)

    def test_inconsistent_status_tags_reported(self):
        user = self._user(
            "k@t.com", tier=self.free, subscription_id="",
            tags=["stripe:active", "stripe:churned"],
        )
        result = self._classify(user, sub=make_sub(status="active"))
        self.assertEqual(
            result.classification, recon.CLASSIFICATION_INCONSISTENT_TAGS
        )

    def test_unknown_price_is_warning(self):
        user = self._user("l@t.com", tier=self.free, subscription_id="")
        sub = make_sub(status="active", price_id="price_unknown", tier_slug=None)
        result = self._classify(user, sub=sub)
        self.assertEqual(
            result.classification, recon.CLASSIFICATION_UNKNOWN_PRICE
        )

    def test_ambiguous_multiple_entitled_subscriptions(self):
        user = self._user("m@t.com", tier=self.main, subscription_id="")
        subs = [
            make_sub(sub_id="sub_a", status="active"),
            make_sub(sub_id="sub_b", status="active"),
        ]
        result = self._classify(user, subs=subs)
        self.assertEqual(
            result.classification, recon.CLASSIFICATION_AMBIGUOUS_SUBSCRIPTION
        )

    def test_lookup_error_recorded_as_review(self):
        import stripe as stripe_lib
        user = self._user("n@t.com", tier=self.main, subscription_id="sub_1")
        with patch.object(
            recon, "_retrieve_subscription",
            side_effect=stripe_lib.AuthenticationError("bad key"),
        ):
            result = recon.classify_user(user)
        self.assertEqual(
            result.classification, recon.CLASSIFICATION_LOOKUP_ERROR
        )


class DuplicateOwnershipTest(ReconBase):
    def test_shared_customer_id_flags_both(self):
        u1 = self._user("dup1@t.com", stripe_customer_id="cus_shared", tier=self.main)
        u2 = self._user("dup2@t.com", stripe_customer_id="cus_shared", tier=self.main)
        dup = recon._duplicate_ownership_map([u1, u2])
        self.assertIn(u1.pk, dup)
        self.assertIn(u2.pk, dup)
        result = recon.classify_user(u1, duplicate_ids=dup)
        self.assertEqual(
            result.classification, recon.CLASSIFICATION_DUPLICATE_OWNERSHIP
        )
        self.assertIn(u2.pk, result.conflicting_user_ids)


class ApplyTransitionTest(ReconBase):
    def test_apply_canceled_reverts_and_preserves_override_and_community(self):
        user = self._user(
            "o@t.com", tier=self.main, subscription_id="sub_1",
            billing_period_end=timezone.now() + timedelta(days=10),
        )
        override = TierOverride.objects.create(
            user=user,
            override_tier=self.main,
            is_active=True,
            expires_at=timezone.now() + timedelta(days=30),
        )
        result = self._classify(user, sub=make_sub(status="canceled"))
        with patch(
            "payments.services.subscription_transition._services._community_remove"
        ) as remove:
            recon.apply_deterministic(result)
        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertEqual(user.subscription_id, "")
        self.assertIsNone(user.pending_tier_id)
        # Override survives; community NOT removed (effective access is Main).
        self.assertTrue(override.is_active)
        remove.assert_not_called()
        # Secret-free audit row written.
        self.assertTrue(
            WebhookEvent.objects.filter(
                event_type="subscription_reconciliation_apply",
                payload__user_id=user.pk,
            ).exists()
        )

    def test_apply_is_idempotent(self):
        user = self._user("p@t.com", tier=self.main, subscription_id="sub_1")
        result = self._classify(user, sub=make_sub(status="canceled"))
        recon.apply_deterministic(result)
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        # Re-classify: now free locally, canceled Stripe -> in sync, no action.
        result2 = self._classify(user, sub=make_sub(status="canceled"))
        self.assertEqual(result2.classification, recon.CLASSIFICATION_OK)


class RunOrchestrationTest(ReconBase):
    def test_diagnostic_run_is_read_only_and_persists_findings(self):
        paid_ended = self._user(
            "q@t.com", tier=self.main, subscription_id="sub_1",
        )
        with patch.object(
            recon, "_retrieve_subscription",
            return_value=make_sub(status="canceled"),
        ), patch.object(
            recon, "_list_subscriptions_for_customer", return_value=[],
        ):
            run = recon.run_reconciliation(
                mode=Run.MODE_DIAGNOSTIC, users=[paid_ended],
            )
        run.refresh_from_db()
        paid_ended.refresh_from_db()
        # Read-only: no membership change.
        self.assertEqual(paid_ended.tier.slug, "main")
        self.assertEqual(run.status, Run.STATUS_COMPLETED)
        self.assertEqual(run.cohort_count, 1)
        self.assertEqual(run.actionable_count, 1)
        self.assertEqual(run.changed_count, 0)
        finding = run.findings.get()
        self.assertEqual(finding.outcome, Finding.OUTCOME_WOULD_CHANGE)

    def test_in_sync_active_row_has_no_finding_but_counts(self):
        user = self._user(
            "r@t.com", tier=self.main, subscription_id="sub_1",
            billing_period_end=datetime.fromtimestamp(FUTURE_TS, tz=dt_timezone.utc),
        )
        with patch.object(
            recon, "_retrieve_subscription",
            return_value=make_sub(status="active"),
        ):
            run = recon.run_reconciliation(
                mode=Run.MODE_DIAGNOSTIC, users=[user],
            )
        self.assertEqual(run.ok_count, 1)
        self.assertEqual(run.findings.count(), 0)

    def test_apply_run_writes_only_when_confirmed(self):
        user = self._user("s@t.com", tier=self.main, subscription_id="sub_1")
        with patch.object(
            recon, "_retrieve_subscription",
            return_value=make_sub(status="canceled"),
        ), patch.object(
            recon, "_list_subscriptions_for_customer", return_value=[],
        ):
            run = recon.run_reconciliation(
                mode=Run.MODE_APPLY, users=[user], confirm=True,
            )
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertEqual(run.changed_count, 1)
        self.assertEqual(run.findings.get().outcome, Finding.OUTCOME_CHANGED)

    def test_apply_run_without_confirm_is_dry_run(self):
        user = self._user("t@t.com", tier=self.main, subscription_id="sub_1")
        with patch.object(
            recon, "_retrieve_subscription",
            return_value=make_sub(status="canceled"),
        ), patch.object(
            recon, "_list_subscriptions_for_customer", return_value=[],
        ):
            run = recon.run_reconciliation(
                mode=Run.MODE_APPLY, users=[user], confirm=False,
            )
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")
        self.assertEqual(run.changed_count, 0)
