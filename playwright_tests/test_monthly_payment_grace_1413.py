"""Twelve deterministic BDD journeys for monthly payment grace (#1413).

Each groomed scenario has a named browser test below.  Service-only truth is
also asserted in focused Django tests; these journeys seed or invoke that same
mocked policy layer, then verify the operator-facing result in a real browser.
No test calls Stripe or real email transport.
"""

import os
from datetime import timedelta
from unittest.mock import patch

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.local_only
REPORT = "/studio/payments/subscription-reconciliation/?filter=payment_grace"


def _reset():
    from accounts.models import User
    from email_app.models import EmailLog
    from payments.models import MonthlyPaymentGrace as Grace
    from payments.models import SubscriptionReconciliationRun

    Grace.objects.all().delete()
    EmailLog.objects.filter(email_type__startswith="payment_grace_").delete()
    SubscriptionReconciliationRun.objects.all().delete()
    User.objects.exclude(email="admin@test.com").delete()


def _member(email="grace-1413@test.com"):
    from accounts.models import User
    from payments.models import Tier

    main = Tier.objects.get(slug="main")
    if main.stripe_price_id_monthly != "price_main_monthly":
        main.stripe_price_id_monthly = "price_main_monthly"
        main.save(update_fields=["stripe_price_id_monthly"])
    return User.objects.create_user(
        email=email, password="x", tier=main,
        stripe_customer_id=f"cus_{email.split('@')[0]}",
        subscription_id=f"sub_{email.split('@')[0]}",
    )


def _grace(user, *, status="active", source="webhook", started=None,
           expires=None, policy_enforced_at=None, invoice_id="in_grace_1413"):
    from payments.models import MonthlyPaymentGrace as Grace
    from payments.models import Tier

    started = started or timezone.now()
    expires = expires or started + timedelta(hours=168)
    main = Tier.objects.get(slug="main")
    return Grace.objects.create(
        user=user, base_tier_at_start=main,
        stripe_customer_id=user.stripe_customer_id,
        stripe_subscription_id=user.subscription_id,
        stripe_invoice_id=invoice_id, livemode=False,
        source=source, status=status, interval="month", interval_count=1,
        grace_started_at=started, grace_expires_at=expires,
        policy_enforced_at=policy_enforced_at,
    )


def _reset_and_seed():
    from payments.models import MonthlyPaymentGraceDelivery as Delivery

    _reset()
    user = _member()
    grace = _grace(user)
    now = timezone.now()
    Delivery.objects.create(
        grace=grace, kind=Delivery.KIND_FAILURE_MEMBER,
        recipient=user.email, status=Delivery.STATUS_SENT,
        attempt_count=1, sent_at=now,
    )
    connection.close()
    return user.pk


def _invoice(user, *, invoice_id="in_grace_1413", created=1_786_528_800,
             paid=False, collection_method="charge_automatically"):
    return {
        "id": invoice_id,
        "customer": user.stripe_customer_id,
        "subscription": user.subscription_id,
        "paid": paid,
        "status": "paid" if paid else "open",
        "collection_method": collection_method,
        "created": created,
    }


def _subscription(user, *, status="past_due", interval="month",
                  interval_count=1, price_id="price_main_monthly", items=1):
    return {
        "id": user.subscription_id,
        "customer": user.stripe_customer_id,
        "status": status,
        "items": {"data": [{"price": {
            "id": price_id,
            "recurring": {
                "interval": interval,
                "interval_count": interval_count,
            },
        }} for _ in range(items)]},
    }


def _staff_page(django_server, browser, path=REPORT):
    page = _auth_context(browser, "admin@test.com").new_page()
    page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
    return page


@pytest.mark.django_db(transaction=True)
class TestMonthlyPaymentGrace1413:
    @pytest.mark.core
    def test_scenario_1_member_and_team_initial_failure_visible_once(self, django_server, browser):
        from email_app.models import EmailLog
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        with override_settings(
            SES_ENABLED=False,
            DEBUG=False,
            PAYMENT_FAILURE_TEAM_EMAIL="team@aishippinglabs.com",
            STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe",
        ), patch.object(service, "_audit"):
            grace, _ = service.start_grace_from_failure(
                invoice=_invoice(user),
                subscription=_subscription(user),
                event_id="evt_browser_initial",
                event_created=1_786_528_800,
                livemode=False,
            )
        assert grace.deliveries.filter(status="sent").count() == 2
        assert set(EmailLog.objects.values_list("subject", flat=True)) == {
            "Payment failed — please retry your AI Shipping Labs payment",
            "[Payments] Member payment failed",
        }
        connection.close()
        page = _staff_page(django_server, browser)
        row = page.get_by_test_id("payment-grace-row")
        expect(row).to_contain_text("Active")
        expect(row).to_contain_text("Initial member failure")
        expect(row).to_contain_text("Initial team failure")

    def test_scenario_2_retries_keep_one_deadline_and_one_delivery_set(self, django_server, browser):
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        with override_settings(SES_ENABLED=False, DEBUG=False), patch.object(
            service, "_audit",
        ):
            first, _ = service.start_grace_from_failure(
                invoice=_invoice(user), subscription=_subscription(user),
                event_id="evt_retry_first", event_created=1_786_528_800,
                livemode=False,
            )
            repeated, _ = service.start_grace_from_failure(
                invoice=_invoice(user),
                subscription=_subscription(user, status="unpaid"),
                event_id="evt_retry_second", event_created=1_786_618_800,
                livemode=False,
            )
        assert repeated.pk == first.pk
        assert repeated.grace_started_at.isoformat() == "2026-08-12T10:00:00+00:00"
        assert repeated.grace_expires_at - repeated.grace_started_at == timedelta(hours=168)
        assert repeated.deliveries.count() == 2
        connection.close()
        page = _staff_page(django_server, browser)
        expect(page.get_by_test_id("payment-grace-row")).to_have_count(1)

    def test_scenario_3_paid_before_warning_shows_recovered_without_later_mail(self, django_server, browser):
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        grace = _grace(user)
        with patch.object(service, "_audit"):
            service.recover_grace(
                subscription_id=user.subscription_id,
                invoice_id=grace.stripe_invoice_id,
                event_id="evt_browser_paid",
                event_created=1_786_536_000,
                livemode=False,
            )
        assert not grace.deliveries.filter(
            kind__in=["reminder_member", "expired_member"],
        ).exists()
        connection.close()
        page = _staff_page(django_server, browser)
        row = page.get_by_test_id("payment-grace-row")
        expect(row).to_contain_text("Recovered")
        expect(row).not_to_contain_text("Member reminder")

    def test_scenario_4_t48_warning_appears_once_with_deadline(self, django_server, browser):
        from email_app.models import EmailLog
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        now = timezone.now()
        grace = _grace(
            user,
            started=now - timedelta(days=5),
            expires=now + timedelta(hours=48),
            policy_enforced_at=now - timedelta(days=6),
        )
        with override_settings(
            STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce",
            SES_ENABLED=False,
            DEBUG=False,
        ), patch.object(
            service, "_revalidate",
            return_value=(_subscription(user), _invoice(user), "ok", ""),
        ):
            service.sweep_payment_graces(now=now)
            service.sweep_payment_graces(now=now + timedelta(minutes=15))
        assert grace.deliveries.filter(kind="reminder_member").count() == 1
        assert EmailLog.objects.filter(
            email_type="payment_grace_reminder_member",
        ).count() == 1
        connection.close()
        page = _staff_page(django_server, browser)
        row = page.get_by_test_id("payment-grace-row")
        expect(row).to_contain_text("Member reminder")
        expect(row).to_contain_text("Sent")

    def test_scenario_5_expiry_returns_base_to_free_once(self, django_server, browser):
        from email_app.models import EmailLog
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        now = timezone.now()
        _grace(
            user,
            started=now - timedelta(days=7),
            expires=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        with override_settings(
            STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce",
            SES_ENABLED=False,
            DEBUG=False,
        ), patch.object(
            service, "_revalidate",
            return_value=(_subscription(user), _invoice(user), "ok", ""),
        ), patch.object(service, "_audit"):
            service.sweep_payment_graces(now=now)
            service.sweep_payment_graces(now=now + timedelta(minutes=15))
        user.refresh_from_db()
        assert user.tier.slug == "free"
        assert EmailLog.objects.filter(
            email_type="payment_grace_expired_member",
        ).count() == 1
        connection.close()
        page = _staff_page(
            django_server, browser, f"/studio/users/{user.pk}/",
        )
        summary = page.get_by_test_id("user-payment-grace-section")
        expect(summary).to_contain_text("Expired")
        expect(summary).to_contain_text("Base tierfree")
        expect(summary).to_contain_text("Effective tierfree")

    def test_scenario_6_strongest_courtesy_access_survives_expiry(self, django_server, browser):
        from accounts.models import TierOverride
        from payments.models import Tier
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        premium = Tier.objects.get(slug="premium")
        basic = Tier.objects.get(slug="basic")
        TierOverride.objects.create(
            user=user, override_tier=premium,
            expires_at=timezone.now() + timedelta(days=20),
            source="staff:premium",
        )
        TierOverride.objects.create(
            user=user, override_tier=basic,
            expires_at=timezone.now() + timedelta(days=30),
            source="maven:newer-basic",
        )
        now = timezone.now()
        _grace(
            user, started=now - timedelta(days=7), expires=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        with override_settings(
            STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce",
            SES_ENABLED=False,
            DEBUG=False,
        ), patch.object(
            service, "_revalidate",
            return_value=(_subscription(user), _invoice(user), "ok", ""),
        ), patch.object(service, "_audit"):
            service.sweep_payment_graces(now=now)
        user.refresh_from_db()
        assert user.tier.slug == "free"
        assert TierOverride.objects.filter(user=user, is_active=True).count() == 2
        connection.close()
        page = _staff_page(
            django_server, browser, f"/studio/users/{user.pk}/",
        )
        summary = page.get_by_test_id("user-payment-grace-section")
        expect(summary).to_contain_text("Base tierfree")
        expect(summary).to_contain_text("Effective tierpremium")

    def test_scenario_7_scheduled_cancellation_has_no_payment_grace(self, django_server, browser):
        from payments.services.webhook_handlers import handle_subscription_updated

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        data = _subscription(user, status="active")
        data.update({
            "cancel_at_period_end": True,
            "current_period_end": 1_800_000_000,
        })
        with patch("payments.services._community_schedule_removal"):
            handle_subscription_updated(
                data,
                event_context={
                    "event_id": "evt_cancel_browser",
                    "created": 1_786_528_800,
                    "livemode": False,
                },
            )
        user.refresh_from_db()
        assert user.tier.slug == "main"
        assert user.pending_tier.slug == "free"
        assert not user.monthly_payment_graces.exists()
        connection.close()
        page = _staff_page(django_server, browser)
        expect(page.get_by_test_id("payment-grace-list")).to_have_count(0)

    def test_scenario_8_unsupported_billing_states_create_no_grace(self, django_server, browser):
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        fixtures = [
            (_invoice(user), _subscription(user, interval="year")),
            (_invoice(user), _subscription(user, interval_count=3)),
            (_invoice(user, collection_method="send_invoice"), _subscription(user)),
            (_invoice(user), _subscription(user, items=2)),
            (_invoice(user), _subscription(user, price_id="unknown")),
        ]
        assert all(
            not service.qualify_monthly_failure(inv, sub).eligible
            for inv, sub in fixtures
        )
        assert not user.monthly_payment_graces.exists()
        connection.close()
        page = _staff_page(django_server, browser)
        expect(page.get_by_test_id("payment-grace-list")).to_have_count(0)

    def test_scenario_9_reconciliation_discovers_missed_webhook(self, django_server, browser):
        from payments.models import SubscriptionReconciliationFinding as Finding
        from payments.models import SubscriptionReconciliationRun as Run
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        run = Run.objects.create(
            status=Run.STATUS_COMPLETED,
            mode=Run.MODE_DIAGNOSTIC,
            source=Run.SOURCE_SCHEDULED,
            finished_at=timezone.now(),
        )
        Finding.objects.create(
            run=run, user=user, email=user.email, current_tier="main",
            current_subscription_id=user.subscription_id,
            stripe_customer_id=user.stripe_customer_id,
            stripe_subscription_id=user.subscription_id,
            stripe_status="past_due", latest_invoice_id="in_grace_1413",
            classification="monthly_payment_grace_active",
        )
        with override_settings(SES_ENABLED=False, DEBUG=False), patch.object(
            service, "_retrieve_subscription",
            return_value=_subscription(user),
        ), patch.object(
            service, "_retrieve_invoice", return_value=_invoice(user),
        ):
            assert service.discover_from_reconciliation_run(run.pk) == 1
        connection.close()
        page = _staff_page(django_server, browser)
        row = page.get_by_test_id("payment-grace-row")
        expect(row).to_contain_text("Source: reconciliation")

    def test_scenario_10_stripe_uncertainty_is_review_without_access_change(self, django_server, browser):
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        now = timezone.now()
        _grace(
            user, started=now - timedelta(days=7), expires=now,
            policy_enforced_at=now - timedelta(days=8),
        )
        with override_settings(
            STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce",
        ), patch.object(
            service, "_revalidate",
            return_value=(None, None, "stripe_lookup_error", "Stripe unavailable"),
        ), patch.object(service, "_audit"):
            service.sweep_payment_graces(now=now)
        user.refresh_from_db()
        assert user.tier.slug == "main"
        connection.close()
        page = _staff_page(django_server, browser)
        row = page.get_by_test_id("payment-grace-row")
        expect(row).to_contain_text("Review")
        expect(row).to_contain_text("stripe_lookup_error")

    def test_scenario_11_staff_investigates_grace_from_report_to_member(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        user_id = _reset_and_seed()
        page = _auth_context(browser, "admin@test.com").new_page()

        page.goto(f"{django_server}{REPORT}", wait_until="domcontentloaded")
        expect(page.get_by_test_id("payment-grace-list")).to_be_visible()
        row = page.get_by_test_id("payment-grace-row")
        expect(row).to_contain_text("in_grace_1413")
        expect(row).to_contain_text("Base: main")
        expect(row).to_contain_text("Effective: main")
        expect(row).to_contain_text("Initial member failure")
        expect(row).to_contain_text("Sent")
        expect(page.get_by_text("Expire now")).to_have_count(0)
        expect(page.get_by_text("Extend grace")).to_have_count(0)

        row.get_by_text("grace-1413@test.com").click()
        page.wait_for_url(f"**/studio/users/{user_id}/")
        summary = page.get_by_test_id("user-payment-grace-section")
        expect(summary).to_be_visible()
        expect(summary).to_contain_text("Base tier")
        expect(summary).to_contain_text("Effective tier")
        expect(summary).to_contain_text("Audit activity")

    def test_scenario_12_observe_to_enforce_stamps_one_fresh_window(self, django_server, browser):
        from payments.services import monthly_payment_grace as service

        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _reset()
        user = _member()
        now = timezone.now()
        grace = _grace(
            user,
            started=now - timedelta(days=10),
            expires=now - timedelta(days=3),
        )
        with override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="observe"):
            service.sweep_payment_graces(now=now)
        grace.refresh_from_db()
        assert grace.policy_enforced_at is None
        with override_settings(STRIPE_MONTHLY_PAYMENT_GRACE_MODE="enforce"):
            service.sweep_payment_graces(now=now)
            service.sweep_payment_graces(now=now + timedelta(hours=1))
        grace.refresh_from_db()
        assert grace.policy_enforced_at == now
        assert grace.effective_expires_at == now + timedelta(hours=168)
        connection.close()
        page = _staff_page(django_server, browser)
        expect(page.get_by_test_id("payment-grace-row")).to_contain_text("Active")

    def test_nonstaff_cannot_inspect_payment_grace(self, django_server, browser):
        _ensure_tiers()
        _reset_and_seed()
        _create_user("plain-1413@test.com")
        page = _auth_context(browser, "plain-1413@test.com").new_page()
        response = page.goto(
            f"{django_server}{REPORT}",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 403
        expect(page.get_by_test_id("payment-grace-list")).to_have_count(0)
