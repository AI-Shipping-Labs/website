"""Focused monthly failed-payment grace coverage for issue #1413.

BDD coverage map (the browser-visible half of scenarios 6/10/11/12 also lives
in ``playwright_tests/test_monthly_payment_grace_1413.py``):

1. initial failure/copy -- ``test_scenario_1_initial_member_and_team_copy``
2. immutable retries -- ``test_webhook_timestamp_anchors_once...``
3. pre-warning recovery -- ``test_scenario_3_recovery_suppresses_later_work``
4. T-48 warning -- ``test_scenario_4_reminder_exact_body_deadline_and_link``
5. expiry/idempotency -- ``test_scenario_5_expiry_email_and_repeat_are_once``
6. courtesy access -- ``test_scenario_6_strongest_override_is_authority``
7. cancellation -- ``test_paid_scheduled_cancellation_does_not_create_grace``
8. exclusions -- ``test_scenario_8_all_unsupported_boundaries``
9. missed webhook -- ``test_missed_webhook_discovery_anchors_once...``
10. uncertainty -- ``test_uncertain_expiry_becomes_review...`` plus QA races
11. operator auth/read-only -- API/Studio tests plus Playwright
12. rollout -- ``test_observe_never...`` / ``test_first_enforcement...``
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import TierOverride, User
from community.models import CommunityAuditLog
from content.models import Course, Enrollment
from email_app.models import EmailLog
from integrations.models import IntegrationSetting
from payments.models import MonthlyPaymentGrace as Grace
from payments.models import MonthlyPaymentGraceDelivery as Delivery
from payments.models import SubscriptionReconciliationFinding as Finding
from payments.models import SubscriptionReconciliationRun as Run
from payments.models import Tier
from payments.services import monthly_payment_grace as service
from payments.services.webhook_dispatch import process_event


def subscription(*, status="past_due", price_id="price_main_monthly",
                 interval="month", interval_count=1, items=1):
    return {
        "id": "sub_grace",
        "customer": "cus_grace",
        "status": status,
        "items": {"data": [
            {"price": {
                "id": price_id,
                "recurring": {
                    "interval": interval,
                    "interval_count": interval_count,
                },
            }} for _ in range(items)
        ]},
    }


def invoice(*, paid=False, status="open", collection_method="charge_automatically",
            created=1_723_459_600):
    return {
        "id": "in_grace",
        "customer": "cus_grace",
        "subscription": "sub_grace",
        "paid": paid,
        "status": status,
        "collection_method": collection_method,
        "created": created,
    }


class GraceBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.main = Tier.objects.get(slug="main")
        cls.main.stripe_price_id_monthly = "price_main_monthly"
        cls.main.stripe_price_id_yearly = "price_main_yearly"
        cls.main.save(update_fields=["stripe_price_id_monthly", "stripe_price_id_yearly"])

    def make_user(self, **kwargs):
        kwargs.setdefault("tier", self.main)
        kwargs.setdefault("stripe_customer_id", "cus_grace")
        kwargs.setdefault("subscription_id", "sub_grace")
        return User.objects.create_user(email="member@example.com", **kwargs)

    def create_grace(self, user=None, **kwargs):
        user = user or self.make_user()
        now = kwargs.pop("grace_started_at", timezone.now())
        defaults = {
            "user": user,
            "base_tier_at_start": self.main,
            "stripe_customer_id": "cus_grace",
            "stripe_subscription_id": "sub_grace",
            "stripe_invoice_id": "in_grace",
            "livemode": False,
            "source": Grace.SOURCE_WEBHOOK,
            "interval": "month",
            "interval_count": 1,
            "grace_started_at": now,
            "grace_expires_at": now + timedelta(hours=168),
        }
        defaults.update(kwargs)
        return Grace.objects.create(**defaults)


class MonthlyPaymentGraceModelTest(GraceBase):
    def test_one_active_grace_per_user_and_invoice_mode(self):
        user = self.make_user()
        self.create_grace(user=user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_grace(
                user=user,
                stripe_invoice_id="in_other",
                livemode=True,
            )

        other = User.objects.create_user(
            email="other@example.com", tier=self.main,
            stripe_customer_id="cus_other", subscription_id="sub_other",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_grace(user=other)

    def test_unknown_mode_invoice_is_unique(self):
        first = self.create_grace(livemode=None)
        first.status = Grace.STATUS_RECOVERED
        first.save(update_fields=["status"])
        other = User.objects.create_user(email="other@example.com", tier=self.main)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_grace(user=other, livemode=None)

    def test_delivery_recipient_normalized_and_logical_key_unique(self):
        grace = self.create_grace()
        Delivery.objects.create(
            grace=grace, kind=Delivery.KIND_FAILURE_MEMBER,
            recipient=" MEMBER@EXAMPLE.COM ",
        )
        row = Delivery.objects.get()
        self.assertEqual(row.recipient, "member@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Delivery.objects.create(
                grace=grace, kind=Delivery.KIND_FAILURE_MEMBER,
                recipient="member@example.com",
            )


class QualificationTest(GraceBase):
    def test_exact_monthly_automatic_unpaid_invoice_qualifies(self):
        result = service.qualify_monthly_failure(invoice(), subscription())
        self.assertTrue(result.eligible)
        self.assertEqual(result.interval, "month")
        self.assertEqual(result.interval_count, 1)
        self.assertEqual(result.tier, self.main)

    def test_interval_collection_status_and_mixed_boundaries_do_not_qualify(self):
        cases = [
            (invoice(), subscription(interval="year"), "unsupported_interval"),
            (invoice(), subscription(interval_count=3), "unsupported_interval"),
            (invoice(collection_method="send_invoice"), subscription(), "manual_collection"),
            (invoice(), subscription(status="active"), "unsupported_status"),
            (invoice(), subscription(status="incomplete"), "unsupported_status"),
            (invoice(), subscription(items=2), "mixed_or_multiple_price"),
            (invoice(), subscription(price_id="price_unknown"), "unknown_price"),
        ]
        mismatched = subscription()
        mismatched["id"] = "sub_other"
        cases.append((invoice(), mismatched, "authority_mismatch"))
        for inv, sub, code in cases:
            with self.subTest(code=code):
                result = service.qualify_monthly_failure(inv, sub)
                self.assertFalse(result.eligible)
                self.assertEqual(result.code, code)

    def test_paid_scheduled_cancellation_does_not_create_grace(self):
        sub = subscription(status="active")
        sub.update(cancel_at_period_end=True, current_period_end=1_800_000_000)
        result = service.qualify_monthly_failure(
            invoice(paid=True, status="paid"), sub,
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.code, "invoice_paid")


class GraceLifecycleTest(GraceBase):
    @patch.object(service, "process_due_deliveries")
    def test_webhook_timestamp_anchors_once_and_repeat_does_not_extend(self, process):
        self.make_user()
        with patch.object(service, "_audit"):
            grace, result = service.start_grace_from_failure(
                invoice=invoice(), subscription=subscription(),
                event_id="evt_1", event_created=1_723_459_600,
                livemode=False,
            )
            repeated, _ = service.start_grace_from_failure(
                invoice=invoice(), subscription=subscription(status="unpaid"),
                event_id="evt_2", event_created=1_723_500_000,
                livemode=False,
            )
        expected = datetime.fromtimestamp(1_723_459_600, tz=dt_timezone.utc)
        self.assertTrue(result.eligible)
        self.assertEqual(grace.pk, repeated.pk)
        self.assertEqual(grace.grace_started_at, expected)
        self.assertEqual(grace.grace_expires_at, expected + timedelta(hours=168))
        self.assertEqual(Grace.objects.count(), 1)
        self.assertEqual(Delivery.objects.count(), 2)

    @patch.object(service, "process_due_deliveries")
    def test_reconciliation_anchors_to_invoice_created(self, process):
        self.make_user()
        with patch.object(service, "_audit"):
            grace, _ = service.start_grace_from_failure(
                invoice=invoice(created=1_723_459_600),
                subscription=subscription(), source=Grace.SOURCE_RECONCILIATION,
            )
        self.assertEqual(
            grace.grace_started_at,
            datetime.fromtimestamp(1_723_459_600, tz=dt_timezone.utc),
        )

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce")
    @patch.object(service, "process_due_deliveries")
    def test_new_failure_during_enforcement_keeps_original_exact_deadline(self, process):
        self.make_user()
        with patch.object(service, "_audit"):
            grace, _ = service.start_grace_from_failure(
                invoice=invoice(), subscription=subscription(),
                event_id="evt_enforced", event_created=1_723_459_600,
                livemode=False,
            )
        self.assertEqual(grace.policy_enforced_at, grace.grace_started_at)
        self.assertEqual(grace.effective_expires_at, grace.grace_expires_at)

    def test_recovery_is_atomic_and_late_failure_does_not_reopen(self):
        grace = self.create_grace()
        Delivery.objects.create(
            grace=grace, kind=Delivery.KIND_REMINDER_MEMBER,
            recipient=grace.user.email,
        )
        with patch.object(service, "_audit"):
            recovered = service.recover_grace(
                subscription_id="sub_grace", invoice_id="in_grace",
                event_id="evt_paid",
            )
        self.assertEqual(recovered.status, Grace.STATUS_RECOVERED)
        self.assertFalse(Delivery.objects.filter(kind=Delivery.KIND_REMINDER_MEMBER).exists())
        with patch.object(service, "process_due_deliveries"), patch.object(service, "_audit"):
            same, _ = service.start_grace_from_failure(
                invoice=invoice(), subscription=subscription(),
                event_id="evt_late", event_created=1_723_459_600,
                livemode=False,
            )
        self.assertEqual(recovered.pk, grace.pk)
        self.assertEqual(same.pk, grace.pk)
        self.assertEqual(Grace.objects.count(), 1)

        new_invoice = invoice(created=1_723_600_000)
        new_invoice["id"] = "in_grace_next"
        with patch.object(service, "process_due_deliveries"), patch.object(service, "_audit"):
            new_grace, qualification = service.start_grace_from_failure(
                invoice=new_invoice,
                subscription=subscription(status="past_due"),
                event_id="evt_new_invoice",
                event_created=1_723_600_000,
                livemode=False,
            )
        self.assertTrue(qualification.eligible)
        self.assertNotEqual(new_grace.pk, grace.pk)
        self.assertEqual(Grace.objects.count(), 2)

    def test_recovery_restores_paid_tags_and_suppresses_unsent_actions(self):
        grace = self.create_grace()
        grace.user.tags = ["stripe:lapsed", "stripe:churned"]
        grace.user.save(update_fields=["tags"])
        Delivery.objects.create(
            grace=grace, kind=Delivery.KIND_REMINDER_MEMBER,
            recipient=grace.user.email,
        )
        with patch.object(service, "_audit") as audit:
            service.recover_grace(
                subscription_id="sub_grace", invoice_id="in_grace",
            )
        grace.user.refresh_from_db()
        self.assertIn("stripe:active", grace.user.tags)
        self.assertIn("stripe:plan-main", grace.user.tags)
        self.assertNotIn("stripe:lapsed", grace.user.tags)
        self.assertNotIn("stripe:churned", grace.user.tags)
        self.assertFalse(
            Delivery.objects.filter(kind=Delivery.KIND_REMINDER_MEMBER).exists()
        )
        self.assertIn("tag_changes", audit.call_args.kwargs)

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="observe")
    def test_observe_never_stamps_or_downgrades(self):
        grace = self.create_grace(
            grace_started_at=timezone.now() - timedelta(days=10),
            grace_expires_at=timezone.now() - timedelta(days=3),
        )
        with patch.object(service, "_retrieve_subscription") as retrieve:
            service.sweep_payment_graces()
        grace.refresh_from_db()
        grace.user.refresh_from_db()
        self.assertEqual(grace.status, Grace.STATUS_ACTIVE)
        self.assertIsNone(grace.policy_enforced_at)
        self.assertEqual(grace.user.tier, self.main)
        retrieve.assert_not_called()

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce")
    def test_first_enforcement_gives_observed_grace_fresh_168_hours_once(self):
        now = timezone.now()
        grace = self.create_grace(
            grace_started_at=now - timedelta(days=10),
            grace_expires_at=now - timedelta(days=3),
        )
        service.sweep_payment_graces(now=now)
        grace.refresh_from_db()
        self.assertEqual(grace.policy_enforced_at, now)
        self.assertEqual(grace.effective_expires_at, now + timedelta(hours=168))
        later = now + timedelta(hours=1)
        service.sweep_payment_graces(now=later)
        grace.refresh_from_db()
        self.assertEqual(grace.policy_enforced_at, now)

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce")
    @patch.object(service, "process_due_deliveries")
    def test_expiry_changes_base_only_and_override_keeps_effective_access(self, process):
        user = self.make_user()
        premium = Tier.objects.get(slug="premium")
        override = TierOverride.objects.create(
            user=user, override_tier=premium,
            expires_at=timezone.now() + timedelta(days=30),
            source="staff:courtesy",
        )
        now = timezone.now()
        grace = self.create_grace(
            user=user,
            grace_started_at=now - timedelta(days=7),
            grace_expires_at=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        with patch.object(service, "_revalidate", return_value=(subscription(), invoice(), "ok", "")), patch.object(service, "_audit"):
            service.sweep_payment_graces(now=now)
        user.refresh_from_db()
        grace.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier, self.free)
        self.assertEqual(user.subscription_id, "sub_grace")
        self.assertEqual(grace.status, Grace.STATUS_EXPIRED)
        self.assertTrue(override.is_active)
        self.assertEqual(service._effective_tier(user), premium)
        self.assertTrue(Delivery.objects.filter(kind=Delivery.KIND_EXPIRED_MEMBER).exists())

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce")
    @patch.object(service, "process_due_deliveries")
    def test_uncertain_expiry_becomes_review_without_access_change(self, process):
        now = timezone.now()
        grace = self.create_grace(
            grace_started_at=now - timedelta(days=7),
            grace_expires_at=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        with patch.object(service, "_revalidate", return_value=(None, None, "stripe_lookup_error", "network down")), patch.object(service, "_audit"):
            service.sweep_payment_graces(now=now)
        grace.refresh_from_db()
        grace.user.refresh_from_db()
        self.assertEqual(grace.status, Grace.STATUS_REVIEW)
        self.assertEqual(grace.user.tier, self.main)
        self.assertEqual(grace.last_error_code, "stripe_lookup_error")

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce")
    @patch.object(service, "process_due_deliveries")
    def test_expiry_reconciles_lapsed_tags_removes_community_and_keeps_progress(self, process):
        user = self.make_user()
        user.tags = ["stripe:active", "stripe:plan-main", "keep-me"]
        user.save(update_fields=["tags"])
        course = Course.objects.create(title="Grace progress", slug="grace-progress")
        enrollment = Enrollment.objects.create(user=user, course=course)
        now = timezone.now()
        self.create_grace(
            user=user,
            grace_started_at=now - timedelta(days=7),
            grace_expires_at=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        with (
            patch.object(
                service, "_revalidate",
                return_value=(subscription(), invoice(), "ok", ""),
            ),
            patch.object(service, "_audit"),
            patch("payments.services._community_remove") as remove,
        ):
            service.sweep_payment_graces(now=now)
        user.refresh_from_db()
        self.assertEqual(user.tier, self.free)
        self.assertEqual(user.subscription_id, "sub_grace")
        self.assertEqual(user.tags, ["keep-me", "stripe:lapsed"])
        self.assertTrue(Enrollment.objects.filter(pk=enrollment.pk).exists())
        remove.assert_called_once_with(user)


class ConfigurationAndCopyTest(GraceBase):
    @override_settings(STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe")
    def test_safe_portal_accepts_only_stable_https_url(self):
        self.assertEqual(service.safe_portal_url(), "https://billing.stripe.com/p/login/safe")
        with self.settings(STRIPE_CUSTOMER_PORTAL_URL="javascript:alert(1)"):
            self.assertEqual(service.safe_portal_url(), "")
        with self.settings(STRIPE_CUSTOMER_PORTAL_URL="https://evil.example/pay"):
            self.assertEqual(service.safe_portal_url(), "")
        with self.settings(STRIPE_CUSTOMER_PORTAL_URL="https://stripe.com:bad/pay"):
            self.assertEqual(service.safe_portal_url(), "")
        with self.settings(
            STRIPE_CUSTOMER_PORTAL_URL=(
                "https://billing.stripe.com/p/session/bearer-session"
            ),
        ):
            self.assertEqual(service.safe_portal_url(), "")

    def test_initial_member_template_has_approved_subject_and_no_threat_copy(self):
        user = self.make_user()
        email_service = service.EmailService()
        subject, html = email_service._render_template(
            "payment_grace_failure_member", user,
            {"recovery_url": "https://billing.stripe.com/p/login/safe"},
        )
        self.assertEqual(subject, "Payment failed — please retry your AI Shipping Labs payment")
        lowered = html.lower()
        for forbidden in ["grace", "deadline", "losing access", "downgrade", "free membership"]:
            self.assertNotIn(forbidden, lowered)
        self.assertFalse(
            email_service._should_include_verify_footer(
                user,
                "payment_grace_failure_team",
            )
        )

    @override_settings(SES_ENABLED=False, DEBUG=False)
    def test_explicit_blank_team_recipient_records_error_and_keeps_member_path(self):
        IntegrationSetting.objects.create(
            key="PAYMENT_FAILURE_TEAM_EMAIL",
            value="",
            group="stripe",
        )
        self.make_user()
        with patch.object(service, "_audit"):
            grace, _ = service.start_grace_from_failure(
                invoice=invoice(), subscription=subscription(),
                event_id="evt_blank_team", event_created=1_723_459_600,
                livemode=False,
            )
        grace.refresh_from_db()
        self.assertEqual(grace.last_error_code, "invalid_team_email")
        self.assertEqual(
            list(grace.deliveries.values_list("kind", flat=True)),
            [Delivery.KIND_FAILURE_MEMBER],
        )

    @override_settings(
        SES_ENABLED=False,
        DEBUG=False,
        STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe",
        PAYMENT_FAILURE_TEAM_EMAIL="team@aishippinglabs.com",
    )
    def test_first_failure_sends_exactly_one_member_and_team_email(self):
        self.make_user()
        with patch.object(service, "_audit"):
            grace, _ = service.start_grace_from_failure(
                invoice=invoice(), subscription=subscription(),
                event_id="evt_delivery", event_created=1_723_459_600,
                livemode=False,
            )
        self.assertEqual(
            set(grace.deliveries.values_list("kind", "status")),
            {
                (Delivery.KIND_FAILURE_MEMBER, Delivery.STATUS_SENT),
                (Delivery.KIND_FAILURE_TEAM, Delivery.STATUS_SENT),
            },
        )
        self.assertEqual(EmailLog.objects.count(), 2)
        self.assertEqual(
            set(EmailLog.objects.values_list("subject", flat=True)),
            {
                "Payment failed — please retry your AI Shipping Labs payment",
                "[Payments] Member payment failed",
            },
        )
        service.process_due_deliveries(grace_ids=[grace.pk], initial_only=True)
        self.assertEqual(EmailLog.objects.count(), 2)

    @override_settings(
        SES_ENABLED=False,
        DEBUG=False,
        STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe",
    )
    def test_delivery_failure_retries_after_backoff_and_stale_claim(self):
        grace = self.create_grace()
        delivery = Delivery.objects.create(
            grace=grace,
            kind=Delivery.KIND_FAILURE_MEMBER,
            recipient=grace.user.email,
        )
        now = timezone.now()
        with patch.object(service, "_send_delivery", side_effect=RuntimeError("down")):
            service.process_due_deliveries(now=now)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Delivery.STATUS_FAILED)
        self.assertEqual(delivery.attempt_count, 1)
        service.process_due_deliveries(now=now + timedelta(minutes=14))
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Delivery.STATUS_FAILED)
        self.assertEqual(delivery.attempt_count, 1)
        service.process_due_deliveries(now=now + timedelta(minutes=15))
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Delivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 2)
        self.assertEqual(EmailLog.objects.count(), 1)

        stale = Delivery.objects.create(
            grace=grace,
            kind=Delivery.KIND_FAILURE_TEAM,
            recipient="team@aishippinglabs.com",
            claimed_at=now,
        )
        service.process_due_deliveries(now=now + timedelta(minutes=14))
        stale.refresh_from_db()
        self.assertEqual(stale.attempt_count, 0)
        service.process_due_deliveries(now=now + timedelta(minutes=16))
        stale.refresh_from_db()
        self.assertEqual(stale.status, Delivery.STATUS_SENT)
        self.assertEqual(stale.attempt_count, 1)

    @override_settings(
        STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce",
        SES_ENABLED=False,
        DEBUG=False,
        STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe",
    )
    def test_reminder_is_exactly_once_at_t48_with_utc_deadline(self):
        now = timezone.now()
        self.create_grace(
            grace_started_at=now - timedelta(days=5),
            grace_expires_at=now + timedelta(hours=48),
            policy_enforced_at=now - timedelta(days=6),
        )
        with patch.object(
            service, "_revalidate",
            return_value=(subscription(), invoice(), "ok", ""),
        ):
            service.sweep_payment_graces(now=now - timedelta(seconds=1))
            self.assertFalse(
                Delivery.objects.filter(kind=Delivery.KIND_REMINDER_MEMBER).exists()
            )
            service.sweep_payment_graces(now=now)
            service.sweep_payment_graces(now=now + timedelta(minutes=15))
        delivery = Delivery.objects.get(kind=Delivery.KIND_REMINDER_MEMBER)
        self.assertEqual(delivery.status, Delivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 1)
        log = EmailLog.objects.get(email_type="payment_grace_reminder_member")
        self.assertEqual(log.subject, "Payment needed to keep your paid membership")


class DiscoveryAndAuditTest(GraceBase):
    @override_settings(SES_ENABLED=False, DEBUG=False)
    def test_missed_webhook_discovery_anchors_once_to_invoice_created(self):
        user = self.make_user()
        run = Run.objects.create(
            status=Run.STATUS_COMPLETED,
            mode=Run.MODE_DIAGNOSTIC,
            source=Run.SOURCE_SCHEDULED,
            finished_at=timezone.now(),
        )
        Finding.objects.create(
            run=run,
            user=user,
            email=user.email,
            current_tier="main",
            current_subscription_id="sub_grace",
            stripe_customer_id="cus_grace",
            stripe_subscription_id="sub_grace",
            stripe_status="past_due",
            latest_invoice_id="in_grace",
            classification="monthly_payment_grace_active",
        )
        with (
            patch.object(service, "_retrieve_subscription", return_value=subscription()),
            patch.object(service, "_retrieve_invoice", return_value=invoice()),
        ):
            self.assertEqual(service.discover_from_reconciliation_run(run.pk), 1)
            self.assertEqual(service.discover_from_reconciliation_run(run.pk), 1)
        grace = Grace.objects.get()
        expected = datetime.fromtimestamp(1_723_459_600, tz=dt_timezone.utc)
        self.assertEqual(grace.source, Grace.SOURCE_RECONCILIATION)
        self.assertEqual(grace.grace_started_at, expected)
        self.assertEqual(grace.grace_expires_at, expected + timedelta(hours=168))
        self.assertEqual(Grace.objects.count(), 1)
        self.assertEqual(Delivery.objects.count(), 2)

    @override_settings(SES_ENABLED=False, DEBUG=False)
    def test_audit_is_secret_free_even_when_payload_contains_secrets(self):
        self.make_user()
        unsafe_invoice = invoice()
        unsafe_invoice.update(
            api_key="sk_test_do_not_store",
            payment_method={"card": "4242424242424242"},
            portal="https://billing.stripe.com/p/session/bearer-secret",
        )
        service.start_grace_from_failure(
            invoice=unsafe_invoice,
            subscription=subscription(),
            event_id="evt_safe_audit",
            event_created=1_723_459_600,
            livemode=False,
        )
        details = "\n".join(
            CommunityAuditLog.objects.values_list("details", flat=True)
        )
        self.assertIn("in_grace", details)
        self.assertNotIn("sk_test_do_not_store", details)
        self.assertNotIn("4242424242424242", details)
        self.assertNotIn("bearer-secret", details)


class GraceWebhookTest(GraceBase):
    @patch.object(service, "process_due_deliveries")
    @patch.object(service, "_retrieve_subscription", return_value=subscription())
    def test_failure_dispatch_passes_stable_event_context(self, retrieve, deliveries):
        self.make_user()
        with patch.object(service, "_audit"):
            outcome, status = process_event(
                event_id="evt_failure_context",
                event_type="invoice.payment_failed",
                obj=invoice(),
                livemode=False,
                event_created=1_723_459_600,
            )
        self.assertEqual((outcome, status), ("processed", 200))
        grace = Grace.objects.get()
        self.assertEqual(grace.last_failure_event_id, "evt_failure_context")
        self.assertEqual(
            grace.grace_started_at,
            datetime.fromtimestamp(1_723_459_600, tz=dt_timezone.utc),
        )

    def test_invoice_paid_is_sixth_recovery_signal(self):
        grace = self.create_grace()
        with patch.object(service, "_audit"):
            outcome, status = process_event(
                event_id="evt_invoice_paid",
                event_type="invoice.paid",
                obj={
                    "id": "in_grace", "subscription": "sub_grace",
                    "customer": "cus_grace", "paid": True,
                },
                livemode=False,
                event_created=1_723_500_000,
            )
        grace.refresh_from_db()
        self.assertEqual((outcome, status), ("processed", 200))
        self.assertEqual(grace.status, Grace.STATUS_RECOVERED)
        self.assertEqual(grace.recovery_event_id, "evt_invoice_paid")


class QaRegressionAndScenarioTest(GraceBase):
    """Regressions from the first QA review and missing BDD assertions."""

    def test_scenario_1_initial_member_and_team_copy(self):
        grace = self.create_grace(
            grace_started_at=datetime(2026, 8, 12, 10, tzinfo=dt_timezone.utc),
            grace_expires_at=datetime(2026, 8, 19, 10, tzinfo=dt_timezone.utc),
        )
        member = Delivery(
            grace=grace,
            kind=Delivery.KIND_FAILURE_MEMBER,
            recipient=grace.user.email,
        )
        team = Delivery(
            grace=grace,
            kind=Delivery.KIND_FAILURE_TEAM,
            recipient="team@aishippinglabs.com",
        )
        with self.settings(
            STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe",
            SITE_URL="https://aishippinglabs.com",
        ):
            member_name, member_context = service._delivery_template(member)
            team_name, team_context = service._delivery_template(team)
            member_subject, member_html = service.EmailService()._render_template(
                member_name, grace.user, member_context,
            )
            team_subject, team_html = service.EmailService()._render_template(
                team_name, grace.user, team_context,
            )
        self.assertEqual(
            member_subject,
            "Payment failed — please retry your AI Shipping Labs payment",
        )
        self.assertIn("recent AI Shipping Labs membership payment failed", member_html)
        self.assertIn("https://billing.stripe.com/p/login/safe", member_html)
        for forbidden in [
            "grace", "deadline", "losing access", "downgrade",
            "free membership", "free account",
        ]:
            self.assertNotIn(forbidden, member_html.lower())
        self.assertEqual(team_subject, "[Payments] Member payment failed")
        for safe_value in [
            grace.user.email, "cus_grace", "sub_grace", "in_grace",
            "2026-08-12 10:00 UTC", "month x 1",
            f"/studio/users/{grace.user_id}/",
            "/studio/payments/subscription-reconciliation/",
        ]:
            self.assertIn(safe_value, team_html)

    @override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce")
    def test_scenario_3_recovery_suppresses_later_work(self):
        now = timezone.now()
        grace = self.create_grace(
            grace_started_at=now,
            grace_expires_at=now + timedelta(days=7),
            policy_enforced_at=now,
        )
        with patch.object(service, "_audit"):
            recovered = service.recover_grace(
                subscription_id="sub_grace",
                invoice_id="in_grace",
                event_id="evt_paid_early",
                event_created=1_786_532_400,
                livemode=False,
            )
        self.assertEqual(recovered.pk, grace.pk)
        with patch.object(service, "_retrieve_subscription") as retrieve:
            service.sweep_payment_graces(now=now + timedelta(days=8))
        recovered.refresh_from_db()
        recovered.user.refresh_from_db()
        self.assertEqual(recovered.status, Grace.STATUS_RECOVERED)
        self.assertEqual(recovered.user.tier, self.main)
        self.assertFalse(
            recovered.deliveries.filter(
                kind__in=[
                    Delivery.KIND_REMINDER_MEMBER,
                    Delivery.KIND_EXPIRED_MEMBER,
                ],
            ).exists()
        )
        retrieve.assert_not_called()

    def test_scenario_4_reminder_exact_body_deadline_and_link(self):
        grace = self.create_grace(
            grace_started_at=datetime(2026, 8, 12, 10, tzinfo=dt_timezone.utc),
            grace_expires_at=datetime(2026, 8, 19, 10, tzinfo=dt_timezone.utc),
            policy_enforced_at=datetime(2026, 8, 12, 10, tzinfo=dt_timezone.utc),
        )
        delivery = Delivery(
            grace=grace,
            kind=Delivery.KIND_REMINDER_MEMBER,
            recipient=grace.user.email,
        )
        with self.settings(
            STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe",
        ):
            name, context = service._delivery_template(delivery)
            subject, html = service.EmailService()._render_template(
                name, grace.user, context,
            )
        self.assertEqual(subject, "Payment needed to keep your paid membership")
        self.assertIn("2026-08-19 10:00 UTC", html)
        self.assertIn("paid base membership will change to Free", html)
        self.assertIn("https://billing.stripe.com/p/login/safe", html)

    @override_settings(
        STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce",
        SES_ENABLED=False,
        DEBUG=False,
        STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe",
    )
    def test_scenario_5_expiry_email_and_repeat_are_once(self):
        now = timezone.now()
        grace = self.create_grace(
            grace_started_at=now - timedelta(days=7),
            grace_expires_at=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        with patch.object(
            service, "_revalidate",
            return_value=(subscription(), invoice(), "ok", ""),
        ), patch.object(service, "_audit") as audit:
            service.sweep_payment_graces(now=now)
            service.sweep_payment_graces(now=now + timedelta(minutes=15))
        grace.refresh_from_db()
        grace.user.refresh_from_db()
        self.assertEqual(grace.status, Grace.STATUS_EXPIRED)
        self.assertEqual(grace.user.tier, self.free)
        delivery = grace.deliveries.get(kind=Delivery.KIND_EXPIRED_MEMBER)
        self.assertEqual(delivery.status, Delivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(
            EmailLog.objects.filter(
                email_type="payment_grace_expired_member",
            ).count(),
            1,
        )
        self.assertEqual(audit.call_count, 1)
        name, context = service._delivery_template(delivery)
        subject, html = service.EmailService()._render_template(
            name, grace.user, context,
        )
        self.assertEqual(subject, "Your AI Shipping Labs account is now Free")
        self.assertIn("always welcome to continue with your Free account", html)
        self.assertIn("rejoin a paid tier", html)
        self.assertIn("https://billing.stripe.com/p/login/safe", html)

    def test_scenario_6_strongest_override_is_authority(self):
        user = self.make_user()
        premium = Tier.objects.get(slug="premium")
        basic = Tier.objects.get(slug="basic")
        TierOverride.objects.create(
            user=user,
            override_tier=premium,
            expires_at=timezone.now() + timedelta(days=20),
            source="staff:premium",
        )
        TierOverride.objects.create(
            user=user,
            override_tier=basic,
            expires_at=timezone.now() + timedelta(days=30),
            source="maven:newer-basic",
        )
        grace = self.create_grace(user=user)
        self.assertEqual(service._effective_tier(user), premium)
        delivery = Delivery(
            grace=grace,
            kind=Delivery.KIND_EXPIRED_MEMBER,
            recipient=user.email,
        )
        _, context = service._delivery_template(delivery)
        self.assertEqual(context["effective_tier"], premium.name)
        self.assertTrue(context["override_continues"])

    def test_scenario_8_all_unsupported_boundaries(self):
        cases = {
            "annual": (invoice(), subscription(interval="year")),
            "three_month": (invoice(), subscription(interval_count=3)),
            "weekly": (invoice(), subscription(interval="week")),
            "daily": (invoice(), subscription(interval="day")),
            "trial_only": (invoice(), subscription(status="trialing")),
            "one_time": (invoice(), subscription(interval="", interval_count=1)),
            "manual_collection": (
                invoice(collection_method="send_invoice"), subscription(),
            ),
            "unknown_price": (
                invoice(), subscription(price_id="price_unknown"),
            ),
            "mixed_or_multiple": (invoice(), subscription(items=2)),
            "missing_subscription": (
                {**invoice(), "subscription": ""}, subscription(),
            ),
            "incomplete": (invoice(), subscription(status="incomplete")),
            "incomplete_expired": (
                invoice(), subscription(status="incomplete_expired"),
            ),
            "paused": (invoice(), subscription(status="paused")),
        }
        for label, (inv, sub) in cases.items():
            with self.subTest(label=label):
                result = service.qualify_monthly_failure(inv, sub)
                self.assertFalse(result.eligible)

    def test_manual_base_or_subscription_change_never_records_recovery(self):
        base_grace = self.create_grace()
        base_grace.user.tier = self.free
        base_grace.user.save(update_fields=["tier"])
        with patch.object(service, "_audit"):
            service.recover_grace(
                subscription_id="sub_grace",
                invoice_id="in_grace",
                event_id="evt_paid_after_manual_tier",
                livemode=False,
            )
        base_grace.refresh_from_db()
        base_grace.user.refresh_from_db()
        self.assertEqual(base_grace.status, Grace.STATUS_REVIEW)
        self.assertEqual(base_grace.last_error_code, "manual_tier_change")
        self.assertEqual(base_grace.user.tier, self.free)
        self.assertIsNone(base_grace.recovered_at)

        other_user = User.objects.create_user(
            email="manual-sub@test.com",
            tier=self.main,
            stripe_customer_id="cus_manual_sub",
            subscription_id="sub_manual_old",
        )
        sub_grace = self.create_grace(
            user=other_user,
            stripe_customer_id="cus_manual_sub",
            stripe_subscription_id="sub_manual_old",
            stripe_invoice_id="in_manual_sub",
            livemode=True,
        )
        other_user.subscription_id = "sub_manual_new"
        other_user.save(update_fields=["subscription_id"])
        with patch.object(service, "_audit"):
            service.recover_grace(
                subscription_id="sub_manual_old",
                invoice_id="in_manual_sub",
                event_id="evt_paid_after_manual_sub",
                livemode=True,
            )
        sub_grace.refresh_from_db()
        other_user.refresh_from_db()
        self.assertEqual(sub_grace.status, Grace.STATUS_SUPERSEDED)
        self.assertEqual(sub_grace.last_error_code, "manual_subscription_change")
        self.assertEqual(other_user.subscription_id, "sub_manual_new")
        self.assertIsNone(sub_grace.recovered_at)

    def test_verified_recovery_mode_isolated_and_event_time_is_stable(self):
        test_grace = self.create_grace(livemode=False)
        live_user = User.objects.create_user(
            email="live-mode@test.com",
            tier=self.main,
            stripe_customer_id="cus_live",
            subscription_id="sub_live",
        )
        live_grace = self.create_grace(
            user=live_user,
            stripe_customer_id="cus_live",
            stripe_subscription_id="sub_live",
            stripe_invoice_id="in_grace",
            livemode=True,
        )
        wrong_outcome, wrong_status = process_event(
            event_id="evt_paid_wrong_mode",
            event_type="invoice.paid",
            obj={"id": "in_grace", "subscription": "sub_grace"},
            livemode=True,
            event_created=1_786_532_400,
        )
        test_grace.refresh_from_db()
        live_grace.refresh_from_db()
        self.assertEqual((wrong_outcome, wrong_status), ("failed_permanent", 200))
        self.assertEqual(test_grace.status, Grace.STATUS_ACTIVE)
        self.assertEqual(live_grace.status, Grace.STATUS_ACTIVE)

        recovered_at = datetime.fromtimestamp(1_786_532_400, tz=dt_timezone.utc)
        with patch.object(service, "_audit"):
            outcome, status = process_event(
                event_id="evt_paid_correct_mode",
                event_type="invoice.paid",
                obj={"id": "in_grace", "subscription": "sub_grace"},
                livemode=False,
                event_created=1_786_532_400,
            )
        test_grace.refresh_from_db()
        self.assertEqual((outcome, status), ("processed", 200))
        self.assertEqual(test_grace.status, Grace.STATUS_RECOVERED)
        self.assertEqual(test_grace.recovery_event_id, "evt_paid_correct_mode")
        self.assertEqual(test_grace.recovered_at, recovered_at)

    def test_expiry_reloads_locked_user_before_manual_state_check(self):
        now = timezone.now()
        grace = self.create_grace(
            grace_started_at=now - timedelta(days=7),
            grace_expires_at=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        self.assertEqual(grace.user.tier, self.main)  # populate stale relation cache
        User.objects.filter(pk=grace.user_id).update(tier=self.free)
        with transaction.atomic(), patch.object(
            service, "_retrieve_subscription", return_value=subscription(),
        ), patch.object(
            service, "_retrieve_invoice", return_value=invoice(),
        ), patch.object(service, "_audit"):
            service._expire_locked(grace)
        grace.refresh_from_db()
        grace.user.refresh_from_db()
        self.assertEqual(grace.status, Grace.STATUS_REVIEW)
        self.assertEqual(grace.last_error_code, "manual_tier_change")
        self.assertEqual(grace.user.tier, self.free)
        self.assertIsNone(grace.expired_at)

    def test_expiry_locked_row_preserves_manual_subscription_change(self):
        now = timezone.now()
        grace = self.create_grace(
            grace_started_at=now - timedelta(days=7),
            grace_expires_at=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        self.assertEqual(grace.user.subscription_id, "sub_grace")
        User.objects.filter(pk=grace.user_id).update(
            subscription_id="sub_staff_replacement",
        )
        with transaction.atomic(), patch.object(
            service, "_retrieve_subscription",
        ) as retrieve, patch.object(service, "_audit"):
            service._expire_locked(grace)
        grace.refresh_from_db()
        grace.user.refresh_from_db()
        self.assertEqual(grace.status, Grace.STATUS_REVIEW)
        self.assertEqual(grace.last_error_code, "manual_subscription_change")
        self.assertEqual(grace.user.subscription_id, "sub_staff_replacement")
        self.assertEqual(grace.user.tier, self.main)
        self.assertIsNone(grace.expired_at)
        retrieve.assert_not_called()

    def test_ambiguous_subscription_recovery_mutates_no_grace(self):
        users = [
            User.objects.create_user(
                email=f"ambiguous-{index}@test.com",
                tier=self.main,
                stripe_customer_id=f"cus_ambiguous_{index}",
                subscription_id="sub_ambiguous",
            )
            for index in range(2)
        ]
        graces = [
            self.create_grace(
                user=user,
                status=Grace.STATUS_REVIEW,
                stripe_customer_id=user.stripe_customer_id,
                stripe_subscription_id="sub_ambiguous",
                stripe_invoice_id=f"in_ambiguous_{index}",
                livemode=False,
            )
            for index, user in enumerate(users)
        ]
        with self.assertRaises(service.WebhookAmbiguousUserError):
            service.recover_grace(
                subscription_id="sub_ambiguous",
                livemode=False,
            )
        for grace in graces:
            grace.refresh_from_db()
            self.assertEqual(grace.status, Grace.STATUS_REVIEW)
            self.assertIsNone(grace.recovered_at)

    def test_stale_claim_fencing_prevents_old_and_new_worker_both_sending(self):
        grace = self.create_grace()
        delivery = Delivery.objects.create(
            grace=grace,
            kind=Delivery.KIND_FAILURE_MEMBER,
            recipient=grace.user.email,
        )
        now = timezone.now()
        _, old_token = service._claim_delivery(delivery.pk, now)
        _, new_token = service._claim_delivery(
            delivery.pk, now + service.CLAIM_TIMEOUT + timedelta(seconds=1),
        )
        self.assertIsNone(
            service._begin_delivery_transport(
                delivery.pk, old_token, now + service.CLAIM_TIMEOUT,
            )
        )
        self.assertIsNotNone(
            service._begin_delivery_transport(
                delivery.pk, new_token,
                now + service.CLAIM_TIMEOUT + timedelta(seconds=1),
            )
        )

    def test_interrupted_transport_is_not_automatically_resent(self):
        grace = self.create_grace()
        delivery = Delivery.objects.create(
            grace=grace,
            kind=Delivery.KIND_FAILURE_MEMBER,
            recipient=grace.user.email,
        )
        now = timezone.now()
        _, token = service._claim_delivery(delivery.pk, now)
        self.assertIsNotNone(
            service._begin_delivery_transport(delivery.pk, token, now)
        )
        self.assertIsNone(
            service._claim_delivery(
                delivery.pk, now + service.CLAIM_TIMEOUT + timedelta(seconds=1),
            )
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Delivery.STATUS_FAILED)
        self.assertIsNotNone(delivery.transport_started_at)
        self.assertIn("automatic retry suppressed", delivery.last_error)
        with patch.object(service, "_send_delivery") as send:
            service.process_due_deliveries(
                now=now + service.CLAIM_TIMEOUT + timedelta(hours=1),
            )
        send.assert_not_called()
