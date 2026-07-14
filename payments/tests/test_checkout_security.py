import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from io import StringIO
from threading import Barrier, Lock
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import stripe
from allauth.socialaccount.models import SocialAccount
from django.core.management import call_command
from django.db import OperationalError, close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import EmailAlias, User
from accounts.services.privacy import delete_account_for_privacy
from community.models import CommunityAuditLog
from integrations.models import IntegrationSetting
from payments.models import (
    CHECKOUT_BINDING_PREFIX,
    CheckoutAccountBinding,
    CheckoutFulfillment,
    PaymentAccountMismatch,
    Tier,
)
from payments.services import (
    handle_checkout_completed,
    handle_subscription_deleted,
    handle_subscription_updated,
)
from tests.fixtures import TierSetupMixin


@override_settings(STRIPE_PAYMENT_LINKS={
    "basic": {
        "monthly": "https://buy.stripe.test/basic-monthly",
        "annual": "https://buy.stripe.test/basic-annual",
    },
})
class CheckoutBindingViewTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(email="bound@test.com", password="pass1234")

    def setUp(self):
        from integrations.config import clear_config_cache
        clear_config_cache()
        self.client.login(email=self.user.email, password="pass1234")

    def tearDown(self):
        from integrations.config import clear_config_cache
        clear_config_cache()
        super().tearDown()

    def test_authenticated_pricing_contains_post_forms_not_stripe_identity_urls(self):
        response = self.client.get("/pricing")
        self.assertContains(response, 'method="post"')
        self.assertContains(response, "/payments/checkout/basic/annual")
        self.assertNotContains(response, f"client_reference_id={self.user.pk}")
        self.assertNotContains(response, "locked_prefilled_email=bound")

    def test_post_issues_hashed_expiring_binding_and_redirects(self):
        response = self.client.post("/payments/checkout/basic/monthly")
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        reference = query["client_reference_id"][0]
        self.assertTrue(reference.startswith(CHECKOUT_BINDING_PREFIX))
        self.assertNotEqual(reference, str(self.user.pk))
        self.assertEqual(query["locked_prefilled_email"], [self.user.email])
        binding = CheckoutAccountBinding.objects.get()
        self.assertNotIn(reference.removeprefix(CHECKOUT_BINDING_PREFIX), binding.token_hash)
        self.assertEqual(binding.user, self.user)
        self.assertEqual(binding.tier, self.basic_tier)
        self.assertGreater(binding.expires_at, timezone.now())

    def test_endpoint_is_post_only_and_requires_authentication(self):
        self.assertEqual(
            self.client.get("/payments/checkout/basic/monthly").status_code,
            405,
        )
        self.client.logout()
        response = self.client.post("/payments/checkout/basic/monthly")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response["Location"])

    def test_endpoint_rejects_missing_csrf_token_when_enforcement_is_enabled(self):
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertTrue(csrf_client.login(email=self.user.email, password="pass1234"))
        response = csrf_client.post("/payments/checkout/basic/monthly")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CheckoutAccountBinding.objects.exists())

    def test_kill_switch_prevents_binding_issuance(self):
        IntegrationSetting.objects.create(
            key="AUTHENTICATED_CHECKOUT_BINDING_ENABLED",
            value="false",
            group="stripe",
        )
        from integrations.config import clear_config_cache
        clear_config_cache()
        response = self.client.post("/payments/checkout/basic/monthly")
        self.assertRedirects(
            response,
            "/pricing?checkout_error=temporarily_unavailable#pricing-section",
        )
        self.assertFalse(CheckoutAccountBinding.objects.exists())
        recovery = self.client.get(response["Location"])
        self.assertContains(recovery, 'data-testid="checkout-recovery-banner"')
        self.assertContains(recovery, "Checkout is temporarily unavailable")
        self.assertContains(recovery, "View membership tiers")
        self.assertContains(recovery, "Contact support")
        self.assertContains(recovery, "mailto:contact@aishippinglabs.com")
        self.assertNotContains(recovery, "not configured for this plan")

    def test_invalid_interval_returns_to_tier_recovery_ui_without_issuing_binding(self):
        response = self.client.post("/payments/checkout/basic/weekly")

        self.assertRedirects(
            response,
            "/pricing?checkout_error=invalid_interval#pricing-section",
        )
        self.assertFalse(CheckoutAccountBinding.objects.exists())
        recovery = self.client.get(response["Location"])
        self.assertContains(recovery, "That billing interval is unavailable")
        self.assertContains(recovery, "membership tier")
        self.assertContains(recovery, "Contact support")

    @override_settings(STRIPE_PAYMENT_LINKS={})
    def test_missing_tier_link_returns_to_recovery_ui_without_issuing_binding(self):
        response = self.client.post("/payments/checkout/basic/monthly")

        self.assertRedirects(
            response,
            "/pricing?checkout_error=tier_unavailable#pricing-section",
        )
        self.assertFalse(CheckoutAccountBinding.objects.exists())
        recovery = self.client.get(response["Location"])
        self.assertContains(recovery, "not configured for that membership tier")
        self.assertContains(recovery, "choose another tier")
        self.assertContains(recovery, "Contact support")


@override_settings(STRIPE_SECRET_KEY="sk_test_example")
class CheckoutSecurityHandlerTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Tier.objects.filter(pk=cls.basic_tier.pk).update(
            stripe_price_id_monthly="price_basic_m",
            stripe_price_id_yearly="price_basic_y",
        )
        Tier.objects.filter(pk=cls.main_tier.pk).update(
            stripe_price_id_monthly="price_main_m",
            stripe_price_id_yearly="price_main_y",
        )
        cls.basic_tier.refresh_from_db()
        cls.main_tier.refresh_from_db()

    def setUp(self):
        super().setUp()
        patchers = [
            patch("payments.services.webhook_handlers._bound_checkout_price_id", return_value="price_basic_m"),
            patch("payments.services._get_subscription_price_id", return_value="price_basic_m"),
            patch("payments.services._get_subscription_period_end", return_value=None),
            patch("payments.services._record_conversion_attribution"),
            patch("payments.services._community_invite"),
            patch("payments.services.send_mail"),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def issue(self, user, *, tier=None, period="monthly", expired=False):
        return CheckoutAccountBinding.issue(
            user=user,
            tier=tier or self.basic_tier,
            billing_period=period,
            expires_at=timezone.now() + timedelta(minutes=-1 if expired else 30),
        )

    def session(
        self,
        reference,
        *,
        session_id="cs_secure",
        email="bound@test.com",
        tier="basic",
        subscription="sub_secure",
        payment_status="paid",
        status="complete",
        livemode=False,
    ):
        return {
            "id": session_id,
            "customer": "cus_secure",
            "customer_details": {"email": email},
            "subscription": subscription,
            "client_reference_id": reference,
            "metadata": {"tier_slug": tier} if tier is not None else {},
            "payment_status": payment_status,
            "status": status,
            "livemode": livemode,
        }

    def test_unpaid_incomplete_and_wrong_mode_sessions_are_quarantined(self):
        user = User.objects.create_user(email="bound@test.com")
        cases = [
            (
                "cs_unpaid",
                {"payment_status": "unpaid"},
                PaymentAccountMismatch.REASON_UNPAID_CHECKOUT,
            ),
            (
                "cs_incomplete",
                {"status": "open"},
                PaymentAccountMismatch.REASON_INCOMPLETE_CHECKOUT,
            ),
            (
                "cs_wrong_mode",
                {"livemode": True},
                PaymentAccountMismatch.REASON_STRIPE_MODE_MISMATCH,
            ),
        ]
        for session_id, overrides, reason in cases:
            _binding, reference = self.issue(user)
            handle_checkout_completed(
                self.session(reference, session_id=session_id, **overrides)
            )
            row = CheckoutFulfillment.objects.get(stripe_session_id=session_id)
            self.assertEqual(row.status, CheckoutFulfillment.STATUS_QUARANTINED)
            self.assertEqual(row.reason, reason)
        user.refresh_from_db()
        self.assertEqual(user.tier, self.free_tier)

    @override_settings(STRIPE_SECRET_KEY="sk_live_example")
    def test_live_key_accepts_only_live_session(self):
        user = User.objects.create_user(email="bound@test.com")
        _binding, reference = self.issue(user)
        handle_checkout_completed(self.session(reference, livemode=True))
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_secure").status,
            CheckoutFulfillment.STATUS_FULFILLED,
        )

    def test_valid_binding_fulfills_once_and_never_creates_auth_alias(self):
        user = User.objects.create_user(email="bound@test.com")
        binding, reference = self.issue(user)
        payload = self.session(reference, email="billing-new@test.com")

        handle_checkout_completed(payload)
        handle_checkout_completed(payload)

        user.refresh_from_db()
        self.assertEqual(user.tier, self.basic_tier)
        self.assertEqual(user.subscription_id, "sub_secure")
        self.assertFalse(EmailAlias.objects.filter(email="billing-new@test.com").exists())
        fulfillment = CheckoutFulfillment.objects.get(stripe_session_id="cs_secure")
        self.assertEqual(fulfillment.status, CheckoutFulfillment.STATUS_FULFILLED)
        self.assertEqual(fulfillment.binding, binding)
        mismatch = PaymentAccountMismatch.objects.get(stripe_session_id="cs_secure")
        self.assertEqual(mismatch.reason, PaymentAccountMismatch.REASON_BILLING_EMAIL_MISMATCH)

    def test_tampered_and_expired_bindings_are_quarantined(self):
        user = User.objects.create_user(email="bound@test.com")
        _binding, reference = self.issue(user, expired=True)
        for session_id, candidate, reason in [
            ("cs_expired", reference, PaymentAccountMismatch.REASON_EXPIRED_BINDING),
            ("cs_tampered", f"{CHECKOUT_BINDING_PREFIX}tampered", PaymentAccountMismatch.REASON_INVALID_BINDING),
        ]:
            with self.subTest(reason=reason):
                handle_checkout_completed(self.session(candidate, session_id=session_id))
                row = CheckoutFulfillment.objects.get(stripe_session_id=session_id)
                self.assertEqual(row.status, CheckoutFulfillment.STATUS_QUARANTINED)
                self.assertEqual(row.reason, reason)
        user.refresh_from_db()
        self.assertEqual(user.tier, self.free_tier)

    def test_revoked_and_wrong_purpose_bindings_are_quarantined(self):
        user = User.objects.create_user(email="bound@test.com")
        revoked, revoked_reference = self.issue(user)
        revoked.revoked_at = timezone.now()
        revoked.save(update_fields=["revoked_at"])
        wrong, wrong_reference = self.issue(user)
        CheckoutAccountBinding.objects.filter(pk=wrong.pk).update(
            source="unknown_source",
            purpose="course_purchase",
        )
        cases = [
            (
                "cs_revoked",
                revoked_reference,
                PaymentAccountMismatch.REASON_EXPIRED_BINDING,
            ),
            (
                "cs_wrong_purpose",
                wrong_reference,
                PaymentAccountMismatch.REASON_BINDING_PURPOSE_MISMATCH,
            ),
        ]
        for session_id, reference, reason in cases:
            handle_checkout_completed(self.session(reference, session_id=session_id))
            self.assertEqual(
                CheckoutFulfillment.objects.get(stripe_session_id=session_id).reason,
                reason,
            )

    def test_binding_cannot_fulfill_two_sessions(self):
        user = User.objects.create_user(email="bound@test.com")
        _binding, reference = self.issue(user)
        handle_checkout_completed(self.session(reference, session_id="cs_first"))
        handle_checkout_completed(self.session(reference, session_id="cs_second"))
        second = CheckoutFulfillment.objects.get(stripe_session_id="cs_second")
        self.assertEqual(second.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(second.reason, PaymentAccountMismatch.REASON_BINDING_REUSED)

    def test_wrong_price_is_quarantined(self):
        user = User.objects.create_user(email="bound@test.com")
        _binding, reference = self.issue(user)
        with patch("payments.services.webhook_handlers._bound_checkout_price_id", return_value="price_main_m"):
            handle_checkout_completed(self.session(reference, session_id="cs_wrong_price"))
        user.refresh_from_db()
        self.assertEqual(user.tier, self.free_tier)
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_wrong_price").reason,
            PaymentAccountMismatch.REASON_TIER_MISMATCH,
        )

    def test_missing_price_is_quarantined(self):
        user = User.objects.create_user(email="bound@test.com")
        _binding, reference = self.issue(user)
        with patch(
            "payments.services.webhook_handlers._bound_checkout_price_id",
            return_value="",
        ):
            handle_checkout_completed(self.session(reference, session_id="cs_no_price"))
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_no_price").reason,
            PaymentAccountMismatch.REASON_MISSING_PRICE,
        )

    def test_transient_price_failure_leaves_no_email_mismatch_or_audit(self):
        user = User.objects.create_user(email="bound@test.com")
        _binding, reference = self.issue(user)
        payload = self.session(
            reference,
            session_id="cs_price_retry",
            email="different-billing@test.com",
        )
        with patch(
            "payments.services.webhook_handlers._bound_checkout_price_id",
            side_effect=RuntimeError("Stripe temporarily unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                handle_checkout_completed(payload)
        self.assertFalse(
            PaymentAccountMismatch.objects.filter(
                stripe_session_id="cs_price_retry"
            ).exists()
        )
        self.assertFalse(
            CommunityAuditLog.objects.filter(
                user=user,
                action="payment_mismatch_recorded",
            ).exists()
        )
        self.assertEqual(
            CheckoutFulfillment.objects.get(
                stripe_session_id="cs_price_retry"
            ).status,
            CheckoutFulfillment.STATUS_PROCESSING,
        )

    def test_payment_link_first_tier_lookup_failure_remains_retryable(self):
        """Metadata-free Payment Links must not quarantine during outage."""
        user = User.objects.create_user(email="bound@test.com")
        _binding, reference = self.issue(user)
        payload = self.session(
            reference,
            session_id="cs_first_lookup_retry",
            email="different-billing@test.com",
            tier=None,
        )
        client = MagicMock()
        client.subscriptions.retrieve.side_effect = (
            stripe.APIConnectionError("Stripe temporarily unavailable")
        )
        with patch(
            "payments.services._get_stripe_client", return_value=client,
        ):
            with self.assertRaises(stripe.APIConnectionError):
                handle_checkout_completed(payload)

        user.refresh_from_db()
        self.assertEqual(user.tier, self.free_tier)
        self.assertEqual(user.subscription_id, "")
        self.assertEqual(user.stripe_customer_id, "")
        self.assertFalse(
            PaymentAccountMismatch.objects.filter(
                stripe_session_id="cs_first_lookup_retry",
            ).exists()
        )
        self.assertFalse(
            CommunityAuditLog.objects.filter(
                user=user,
                action="payment_mismatch_recorded",
            ).exists()
        )
        fulfillment = CheckoutFulfillment.objects.get(
            stripe_session_id="cs_first_lookup_retry",
        )
        self.assertEqual(
            fulfillment.status, CheckoutFulfillment.STATUS_PROCESSING,
        )
        self.assertEqual(fulfillment.reason, "")
        self.assertIsNone(fulfillment.binding_id)
        self.assertIsNone(fulfillment.user_id)
        self.assertIsNone(fulfillment.tier_id)

    def test_legacy_numeric_requires_same_canonical_email(self):
        user = User.objects.create_user(email="legacy@test.com")
        handle_checkout_completed(self.session(str(user.pk), session_id="cs_legacy", email=user.email))
        user.refresh_from_db()
        self.assertEqual(user.tier, self.basic_tier)

        other = User.objects.create_user(email="other@test.com")
        handle_checkout_completed(self.session(str(user.pk), session_id="cs_legacy_bad", email=other.email, subscription="sub_other"))
        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_legacy_bad")
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(row.reason, PaymentAccountMismatch.REASON_LEGACY_REFERENCE_MISMATCH)

    def test_legacy_numeric_kill_switch_quarantines_even_matching_email(self):
        user = User.objects.create_user(email="legacy-off@test.com")
        with patch("payments.services.get_config", return_value="false"):
            handle_checkout_completed(
                self.session(str(user.pk), session_id="cs_legacy_off", email=user.email)
            )
        user.refresh_from_db()
        self.assertEqual(user.tier, self.free_tier)
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_legacy_off").status,
            CheckoutFulfillment.STATUS_QUARANTINED,
        )

    def test_legacy_numeric_cutoff_is_enforced_even_when_switch_stays_enabled(self):
        user = User.objects.create_user(email="legacy-cutoff@test.com")
        cutoff = datetime(2026, 8, 1, tzinfo=datetime_timezone.utc)

        def config(key, default=""):
            return {
                "LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED": "true",
                "LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF": cutoff.isoformat(),
            }.get(key, default)

        with (
            patch("payments.services.get_config", side_effect=config),
            patch(
                "payments.services.webhook_handlers.django_timezone.now",
                return_value=cutoff,
            ),
        ):
            handle_checkout_completed(
                self.session(
                    str(user.pk),
                    session_id="cs_legacy_cutoff",
                    email=user.email,
                )
            )

        user.refresh_from_db()
        fulfillment = CheckoutFulfillment.objects.get(
            stripe_session_id="cs_legacy_cutoff"
        )
        self.assertEqual(user.tier, self.free_tier)
        self.assertEqual(fulfillment.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(
            fulfillment.reason,
            PaymentAccountMismatch.REASON_LEGACY_REFERENCE_MISMATCH,
        )
        self.assertTrue(fulfillment.details["legacy_reference_disabled"])

    def test_legacy_numeric_malformed_cutoff_fails_closed(self):
        user = User.objects.create_user(email="legacy-malformed@test.com")

        def config(key, default=""):
            return {
                "LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED": "true",
                "LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF": "not-a-date",
            }.get(key, default)

        with patch("payments.services.get_config", side_effect=config):
            handle_checkout_completed(
                self.session(
                    str(user.pk),
                    session_id="cs_legacy_malformed",
                    email=user.email,
                )
            )

        self.assertEqual(
            CheckoutFulfillment.objects.get(
                stripe_session_id="cs_legacy_malformed"
            ).status,
            CheckoutFulfillment.STATUS_QUARANTINED,
        )

    def test_transient_core_failure_retries_atomically(self):
        user = User.objects.create_user(email="bound@test.com")
        _binding, reference = self.issue(user)
        payload = self.session(reference, session_id="cs_retry")
        with patch("payments.services._get_subscription_period_end", side_effect=RuntimeError("temporary")):
            with self.assertRaises(RuntimeError):
                handle_checkout_completed(payload)
        user.refresh_from_db()
        self.assertEqual(user.tier, self.free_tier)
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_retry").status,
            CheckoutFulfillment.STATUS_PROCESSING,
        )
        handle_checkout_completed(payload)
        user.refresh_from_db()
        self.assertEqual(user.tier, self.basic_tier)

    def test_existing_distinct_subscription_is_quarantined(self):
        user = User.objects.create_user(email="bound@test.com")
        user.subscription_id = "sub_authoritative"
        user.save(update_fields=["subscription_id"])
        _binding, reference = self.issue(user)
        handle_checkout_completed(self.session(reference, session_id="cs_conflict"))
        user.refresh_from_db()
        self.assertEqual(user.subscription_id, "sub_authoritative")
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_conflict").reason,
            PaymentAccountMismatch.REASON_SUBSCRIPTION_CONFLICT,
        )

    def test_existing_distinct_customer_is_quarantined(self):
        user = User.objects.create_user(email="customer-conflict@test.com")
        user.stripe_customer_id = "cus_authoritative"
        user.save(update_fields=["stripe_customer_id"])
        _binding, reference = self.issue(user)
        handle_checkout_completed(self.session(
            reference,
            session_id="cs_customer_conflict",
            email=user.email,
        ))
        user.refresh_from_db()
        self.assertEqual(user.stripe_customer_id, "cus_authoritative")
        self.assertEqual(
            CheckoutFulfillment.objects.get(
                stripe_session_id="cs_customer_conflict"
            ).reason,
            PaymentAccountMismatch.REASON_CUSTOMER_CONFLICT,
        )


@override_settings(STRIPE_SECRET_KEY="sk_test_example")
class CheckoutConcurrencySecurityTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        Tier.objects.get_or_create(slug="free", defaults={"name": "Free", "level": 0})
        self.basic_tier, _created = Tier.objects.get_or_create(
            slug="basic",
            defaults={"name": "Basic", "level": 10},
        )
        self.basic_tier.stripe_price_id_monthly = "price_basic_m"
        self.basic_tier.save(update_fields=["stripe_price_id_monthly"])
        self.patchers = [
            patch("payments.services._get_subscription_price_id", return_value="price_basic_m"),
            patch("payments.services._get_subscription_period_end", return_value=None),
            patch("payments.services._record_conversion_attribution"),
            patch("payments.services._community_invite"),
            patch("payments.services.send_mail"),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _issue(self, user):
        return CheckoutAccountBinding.issue(
            user=user,
            tier=self.basic_tier,
            billing_period=CheckoutAccountBinding.PERIOD_MONTHLY,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

    def _session(self, reference, session_id, subscription_id):
        return {
            "id": session_id,
            "customer": "cus_concurrent",
            "customer_details": {"email": "concurrent@test.com"},
            "subscription": subscription_id,
            "client_reference_id": reference,
            "metadata": {"tier_slug": "basic"},
            "payment_status": "paid",
            "status": "complete",
            "livemode": False,
        }

    def _deliver_concurrently(self, payloads):
        barrier = Barrier(len(payloads))
        call_lock = Lock()
        calls = 0

        def synchronized_price(_subscription_id):
            nonlocal calls
            with call_lock:
                calls += 1
                should_wait = calls <= len(payloads)
            if should_wait:
                barrier.wait(timeout=10)
            return "price_basic_m"

        def deliver(payload):
            for attempt in range(4):
                close_old_connections()
                try:
                    handle_checkout_completed(payload)
                    return
                except OperationalError:
                    if attempt == 3:
                        raise
                    # In-memory SQLite does not honor busy_timeout for table
                    # locks. Retry the same signed delivery, matching Stripe's
                    # production retry semantics; PostgreSQL blocks on the row.
                    time.sleep(0.05 * (attempt + 1))
                finally:
                    close_old_connections()

        with patch(
            "payments.services.webhook_handlers._bound_checkout_price_id",
            side_effect=synchronized_price,
        ):
            with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
                futures = [pool.submit(deliver, payload) for payload in payloads]
                for future in futures:
                    future.result(timeout=20)

    def test_concurrent_same_session_grants_once(self):
        user = User.objects.create_user(email="concurrent@test.com")
        _binding, reference = self._issue(user)
        payload = self._session(reference, "cs_concurrent_same", "sub_same")

        self._deliver_concurrently([payload, dict(payload)])

        user.refresh_from_db()
        self.assertEqual(user.tier, self.basic_tier)
        self.assertEqual(user.subscription_id, "sub_same")
        self.assertEqual(
            CheckoutFulfillment.objects.filter(
                stripe_session_id="cs_concurrent_same",
                status=CheckoutFulfillment.STATUS_FULFILLED,
            ).count(),
            1,
        )

    def test_concurrent_sessions_for_same_binding_grant_once(self):
        user = User.objects.create_user(email="concurrent@test.com")
        _binding, reference = self._issue(user)
        payloads = [
            self._session(reference, "cs_concurrent_a", "sub_concurrent"),
            self._session(reference, "cs_concurrent_b", "sub_concurrent"),
        ]

        self._deliver_concurrently(payloads)

        outcomes = list(
            CheckoutFulfillment.objects.filter(
                stripe_session_id__in=["cs_concurrent_a", "cs_concurrent_b"]
            ).values_list("status", "reason")
        )
        self.assertEqual(
            sum(status == CheckoutFulfillment.STATUS_FULFILLED for status, _ in outcomes),
            1,
        )
        self.assertEqual(
            sum(
                status == CheckoutFulfillment.STATUS_QUARANTINED
                and reason == PaymentAccountMismatch.REASON_BINDING_REUSED
                for status, reason in outcomes
            ),
            1,
        )


class SubscriptionOrderingSecurityTest(TierSetupMixin, TestCase):
    def test_old_update_and_delete_cannot_mutate_new_subscription(self):
        user = User.objects.create_user(email="ordered@test.com")
        user.tier = self.main_tier
        user.stripe_customer_id = "cus_ordered"
        user.subscription_id = "sub_new"
        user.save(update_fields=["tier", "stripe_customer_id", "subscription_id"])

        handle_subscription_updated({
            "id": "sub_old",
            "customer": "cus_ordered",
            "status": "active",
            "items": {"data": [{"price": {"id": "price_basic"}}]},
        })
        handle_subscription_deleted({"id": "sub_old", "customer": "cus_ordered"})

        user.refresh_from_db()
        self.assertEqual(user.subscription_id, "sub_new")
        self.assertEqual(user.tier, self.main_tier)
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=user,
                action="stale_subscription_event_ignored",
            ).count(),
            2,
        )
        mismatches = PaymentAccountMismatch.objects.filter(
            reason=PaymentAccountMismatch.REASON_OUT_OF_ORDER_SUBSCRIPTION_EVENT,
        )
        self.assertEqual(mismatches.count(), 2)
        self.assertTrue(all(row.status == PaymentAccountMismatch.STATUS_OPEN for row in mismatches))


class CheckoutSecurityOperationsTest(TierSetupMixin, TestCase):
    def test_privacy_delete_detaches_and_redacts_checkout_records(self):
        user = User.objects.create_user(email="erase-checkout@test.com")
        binding, _reference = CheckoutAccountBinding.issue(
            user=user,
            tier=self.basic_tier,
            billing_period="monthly",
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        fulfillment = CheckoutFulfillment.objects.create(
            stripe_session_id="cs_privacy",
            binding=binding,
            user=user,
            tier=self.basic_tier,
            status=CheckoutFulfillment.STATUS_QUARANTINED,
        )

        result = delete_account_for_privacy(user)

        self.assertTrue(result.success)
        binding.refresh_from_db()
        fulfillment.refresh_from_db()
        self.assertIsNone(binding.user)
        self.assertTrue(binding.email_snapshot.endswith("@privacy.invalid"))
        self.assertIsNone(fulfillment.user)

    def test_privacy_delete_redacts_distinct_stripe_billing_email_and_details(self):
        user = User.objects.create_user(email="canonical-delete@test.com")
        mismatch = PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_distinct_privacy",
            stripe_email="different-billing@test.com",
            paid_user=user,
            reason=PaymentAccountMismatch.REASON_BILLING_EMAIL_MISMATCH,
            details={
                "paid_user_email": user.email,
                "stripe_email": "different-billing@test.com",
            },
        )

        result = delete_account_for_privacy(user)

        self.assertTrue(result.success)
        mismatch.refresh_from_db()
        self.assertTrue(mismatch.stripe_email.endswith("@privacy.invalid"))
        self.assertEqual(mismatch.details["paid_user_email"], "[privacy-redacted]")
        self.assertEqual(mismatch.details["stripe_email"], "[privacy-redacted]")

    def test_legacy_alias_audit_command_is_read_only(self):
        user = User.objects.create_user(email="alias-owner@test.com")
        alias = EmailAlias.objects.create(
            user=user,
            email="legacy-relay@test.com",
            source=EmailAlias.SOURCE_STRIPE_RELAY,
        )
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google-legacy-owner",
            extra_data={"email": alias.email},
        )
        CommunityAuditLog.objects.create(
            user=user,
            action="email_alias_added",
            details=(
                "added alias legacy-relay@test.com from Stripe checkout; "
                "session=cs_legacy_alias_audit"
            ),
        )
        CheckoutFulfillment.objects.create(
            stripe_session_id="cs_legacy_alias_audit",
            user=user,
            status=CheckoutFulfillment.STATUS_FULFILLED,
        )
        output = StringIO()

        call_command("audit_stripe_checkout_aliases", stdout=output)

        self.assertIn("legacy-relay@test.com", output.getvalue())
        self.assertIn("google-legacy-owner", output.getvalue())
        self.assertIn("cs_legacy_alias_audit", output.getvalue())
        self.assertIn("email_alias_added", output.getvalue())
        self.assertIn("fulfilled", output.getvalue())
        self.assertIn("Total legacy Stripe aliases: 1", output.getvalue())
        self.assertTrue(EmailAlias.objects.filter(pk=alias.pk).exists())
