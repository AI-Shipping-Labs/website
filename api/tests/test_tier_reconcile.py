"""Tests for tier-reconciliation API endpoints (issue #621).

Covers:

- ``GET /api/payments/tier-reconcile/diagnostics``
- ``POST /api/payments/tier-reconcile``

Stripe is patched at ``payments.services.backfill_tiers.stripe.Subscription.list``
exactly as in ``payments/tests/test_backfill_stripe_tiers.py``. The
``StripePager`` / ``subscription()`` helpers are copied locally on purpose;
importing from a sibling test module would create cross-package coupling.
"""

import json
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from unittest.mock import patch

import stripe
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import TierOverride, Token
from api.views import tier_reconcile as tier_reconcile_module
from payments.models import Tier, WebhookEvent

User = get_user_model()

DIAGNOSTICS_URL = "/api/payments/tier-reconcile/diagnostics"
APPLY_URL = "/api/payments/tier-reconcile"


class StripePager:
    def __init__(self, items):
        self.items = items

    def auto_paging_iter(self):
        return iter(self.items)


def subscription(
    subscription_id="sub_active",
    *,
    price_id="price_main_monthly",
    current_period_end=1_800_000_000,
):
    return {
        "id": subscription_id,
        "status": "active",
        "current_period_end": current_period_end,
        "items": {"data": [{"price": {"id": price_id}}]},
    }


def patch_subscriptions(subscriptions_by_customer):
    def list_subscriptions(**kwargs):
        return StripePager(
            subscriptions_by_customer.get(kwargs["customer"], [])
        )

    return patch(
        "payments.services.backfill_tiers.stripe.Subscription.list",
        side_effect=list_subscriptions,
    )


@override_settings(STRIPE_SECRET_KEY="sk_test_reconcile")
class TierReconcileTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.main = Tier.objects.get(slug="main")
        cls.main.stripe_price_id_monthly = "price_main_monthly"
        cls.main.save(update_fields=["stripe_price_id_monthly"])
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="reconcile-test")

    def _user(self, email, **kwargs):
        kwargs.setdefault(
            "stripe_customer_id",
            f"cus_{email.split('@')[0]}",
        )
        return User.objects.create_user(email=email, password="x", **kwargs)

    def _auth_header(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _get_diagnostics(self, **params):
        return self.client.get(DIAGNOSTICS_URL, params, **self._auth_header())

    def _post_apply(self, body=None, *, raw_body=None):
        data = raw_body if raw_body is not None else json.dumps(body or {})
        return self.client.post(
            APPLY_URL,
            data=data,
            content_type="application/json",
            **self._auth_header(),
        )


class TierReconcileDiagnosticsTest(TierReconcileTestBase):
    def test_diagnostics_lists_users_whose_stripe_tier_differs_from_direct_tier(self):
        user = self._user("paid@test.com")

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self._get_diagnostics()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        entry = body["users"][0]
        self.assertEqual(entry["email"], "paid@test.com")
        self.assertEqual(entry["current_tier"], "free")
        self.assertEqual(entry["stripe_active_tier"], "main")
        self.assertEqual(entry["action_needed"], "set_direct_tier")
        self.assertEqual(entry["current_tier_source"], "none")
        self.assertEqual(entry["subscription_id"], "sub_active")
        self.assertEqual(entry["stripe_customer_id"], user.stripe_customer_id)

    def test_diagnostics_flags_users_whose_paid_access_comes_from_a_redundant_override(self):
        user = self._user("override@test.com")
        TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
        )

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self._get_diagnostics()

        body = response.json()
        self.assertEqual(body["count"], 1)
        entry = body["users"][0]
        self.assertEqual(entry["current_tier"], "free")
        self.assertEqual(entry["stripe_active_tier"], "main")
        self.assertEqual(entry["action_needed"], "set_direct_tier")

    def test_diagnostics_flags_user_with_redundant_override_matching_direct_tier(self):
        """User on `main` (direct) with an active matching override.

        ``backfill_user_from_stripe`` reports ``status: changed`` with
        ``override_deactivated=True`` because the override is redundant.
        The diagnostic must surface this as ``deactivate_override``.
        """
        user = self._user("redundant@test.com", tier=self.main)
        TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
        )

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self._get_diagnostics()

        body = response.json()
        self.assertEqual(body["count"], 1)
        entry = body["users"][0]
        self.assertEqual(entry["current_tier"], "main")
        self.assertEqual(entry["current_tier_source"], "override")
        self.assertEqual(entry["action_needed"], "deactivate_override")

    def test_diagnostics_flags_paid_users_with_no_active_stripe_subscription(self):
        user = self._user("nosub@test.com", tier=self.main)

        with patch_subscriptions({user.stripe_customer_id: []}):
            response = self._get_diagnostics()

        body = response.json()
        self.assertEqual(body["count"], 1)
        entry = body["users"][0]
        self.assertEqual(entry["email"], "nosub@test.com")
        self.assertEqual(entry["action_needed"], "warning_no_active_subscription")
        self.assertIsNone(entry["stripe_active_tier"])

    def test_diagnostics_flags_unknown_price_as_warning(self):
        user = self._user("weirdprice@test.com")

        with patch_subscriptions({
            user.stripe_customer_id: [subscription(price_id="price_unknown")]
        }):
            response = self._get_diagnostics()

        body = response.json()
        self.assertEqual(body["count"], 1)
        entry = body["users"][0]
        self.assertEqual(entry["action_needed"], "warning_unknown_price")
        self.assertEqual(entry["stripe_active_tier"], "unknown")

    def test_diagnostics_excludes_users_already_in_sync_by_default(self):
        period_end = 1_800_000_000
        user = self._user(
            "current@test.com",
            tier=self.main,
            subscription_id="sub_active",
            billing_period_end=datetime.fromtimestamp(
                period_end,
                tz=datetime_timezone.utc,
            ),
        )

        with patch_subscriptions({
            user.stripe_customer_id: [subscription(current_period_end=period_end)]
        }):
            response = self._get_diagnostics()

        body = response.json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["users"], [])

    def test_diagnostics_include_ok_param_returns_in_sync_users_with_noop_action(self):
        period_end = 1_800_000_000
        user = self._user(
            "current@test.com",
            tier=self.main,
            subscription_id="sub_active",
            billing_period_end=datetime.fromtimestamp(
                period_end,
                tz=datetime_timezone.utc,
            ),
        )

        with patch_subscriptions({
            user.stripe_customer_id: [subscription(current_period_end=period_end)]
        }):
            response = self._get_diagnostics(include="ok")

        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["users"][0]["email"], user.email)
        self.assertEqual(body["users"][0]["action_needed"], "noop")

    def test_diagnostics_email_filter_returns_only_the_matching_user(self):
        alice = self._user("alice@test.com")
        bob = self._user("bob@test.com")
        carol = self._user("carol@test.com")

        with patch_subscriptions({
            alice.stripe_customer_id: [subscription("sub_alice")],
            bob.stripe_customer_id: [subscription("sub_bob")],
            carol.stripe_customer_id: [subscription("sub_carol")],
        }):
            response = self._get_diagnostics(email="ALICE@test.com")

        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["users"][0]["email"], "alice@test.com")

    def test_diagnostics_email_filter_for_unknown_email_returns_empty_count_zero_not_404(self):
        with patch_subscriptions({}):
            response = self._get_diagnostics(email="nobody@example.com")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["users"], [])

    def test_diagnostics_users_without_stripe_customer_id_are_skipped(self):
        # Eligible user (gets one Stripe call).
        eligible = self._user("eligible@test.com")
        # Two ineligible users with no Stripe customer ID.
        User.objects.create_user(email="plain1@test.com", password="x")
        User.objects.create_user(email="plain2@test.com", password="x")

        with patch_subscriptions({
            eligible.stripe_customer_id: [subscription("sub_e")],
        }) as stripe_list:
            response = self._get_diagnostics()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        emails = {row["email"] for row in body["users"]}
        self.assertEqual(emails, {"eligible@test.com"})
        # Only the eligible user triggers a Stripe call.
        self.assertEqual(stripe_list.call_count, 1)

    def test_diagnostics_returns_users_in_ascending_email_order(self):
        carol = self._user("carol@test.com")
        alice = self._user("alice@test.com")
        bob = self._user("bob@test.com")

        with patch_subscriptions({
            alice.stripe_customer_id: [subscription("sub_a")],
            bob.stripe_customer_id: [subscription("sub_b")],
            carol.stripe_customer_id: [subscription("sub_c")],
        }):
            response = self._get_diagnostics()

        body = response.json()
        self.assertEqual(
            [row["email"] for row in body["users"]],
            ["alice@test.com", "bob@test.com", "carol@test.com"],
        )

    def test_diagnostics_caps_at_max_users(self):
        for index in range(3):
            self._user(f"user{index}@test.com")

        with patch.object(tier_reconcile_module, "MAX_USERS_PER_REQUEST", 2):
            with patch_subscriptions({}):
                response = self._get_diagnostics()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "too_many_users")

    def test_diagnostics_unauthenticated_returns_401_with_no_stripe_call(self):
        self._user("paid@test.com")

        with patch_subscriptions({}) as stripe_list:
            response = self.client.get(DIAGNOSTICS_URL)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )
        self.assertEqual(stripe_list.call_count, 0)

    def test_diagnostics_non_staff_token_returns_401(self):
        member = User.objects.create_user(
            email="member-tr@test.com",
            password="x",
        )
        stale_token = Token(
            key="stale-member-tr-key",
            user=member,
            name="legacy-member-tr-token",
        )
        Token.objects.bulk_create([stale_token])

        with patch_subscriptions({}):
            response = self.client.get(
                DIAGNOSTICS_URL,
                HTTP_AUTHORIZATION=f"Token {stale_token.key}",
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_diagnostics_post_returns_405(self):
        response = self.client.post(
            DIAGNOSTICS_URL,
            data="{}",
            content_type="application/json",
            **self._auth_header(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "Method not allowed"})


class TierReconcileApplyTest(TierReconcileTestBase):
    def test_apply_with_dry_run_does_not_write_user_or_override_or_audit_row(self):
        user = self._user("paid@test.com")
        override = TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
        )

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self._post_apply({
                "emails": ["paid@test.com"],
                "dry_run": True,
            })

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["processed"], 1)
        self.assertEqual(body["changed"], 1)
        self.assertEqual(body["results"][0]["status"], "would_change")

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertTrue(override.is_active)
        self.assertFalse(
            WebhookEvent.objects.filter(
                event_type="backfill_stripe_tiers",
            ).exists()
        )

    def test_apply_without_dry_run_writes_user_tier_and_audit_row(self):
        user = self._user("paid@test.com")
        period_end = 1_800_000_123

        with patch_subscriptions({
            user.stripe_customer_id: [subscription(current_period_end=period_end)]
        }):
            response = self._post_apply({"emails": ["paid@test.com"]})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["dry_run"])
        self.assertEqual(body["processed"], 1)
        self.assertEqual(body["changed"], 1)
        self.assertEqual(body["results"][0]["status"], "changed")

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")
        self.assertEqual(user.subscription_id, "sub_active")
        self.assertEqual(
            user.billing_period_end,
            datetime.fromtimestamp(period_end, tz=datetime_timezone.utc),
        )
        self.assertEqual(
            WebhookEvent.objects.filter(
                event_type="backfill_stripe_tiers",
            ).count(),
            1,
        )

    def test_apply_deactivates_redundant_override_and_reports_in_response(self):
        user = self._user("override@test.com")
        override = TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
        )

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self._post_apply({"emails": ["override@test.com"]})

        body = response.json()
        self.assertEqual(body["results"][0]["deactivated_override"], True)

        override.refresh_from_db()
        self.assertFalse(override.is_active)

    def test_apply_with_no_emails_processes_all_users_with_stripe_customer_id(self):
        alice = self._user("alice@test.com")
        bob = self._user("bob@test.com")
        carol = self._user("carol@test.com")
        User.objects.create_user(email="plain1@test.com", password="x")
        User.objects.create_user(email="plain2@test.com", password="x")

        with patch_subscriptions({
            alice.stripe_customer_id: [subscription("sub_a")],
            bob.stripe_customer_id: [subscription("sub_b")],
            carol.stripe_customer_id: [subscription("sub_c")],
        }) as stripe_list:
            response = self._post_apply({})

        body = response.json()
        self.assertEqual(body["processed"], 3)
        self.assertEqual(stripe_list.call_count, 3)

    def test_apply_with_unknown_email_returns_per_row_not_found_not_404(self):
        with patch_subscriptions({}):
            response = self._post_apply({"emails": ["nobody@example.com"]})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["processed"], 0)
        self.assertEqual(body["changed"], 0)
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["status"], "not_found")
        self.assertEqual(body["results"][0]["email"], "nobody@example.com")

    def test_apply_email_match_is_case_insensitive(self):
        user = self._user("alice@test.com")

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self._post_apply({"emails": ["ALICE@TEST.COM"]})

        body = response.json()
        self.assertEqual(body["processed"], 1)
        self.assertEqual(body["results"][0]["status"], "changed")
        self.assertEqual(body["results"][0]["email"], "alice@test.com")

    def test_apply_emails_that_dont_match_keep_processed_count_accurate(self):
        period_end = 1_800_000_000
        mismatched = self._user("paid@test.com")
        in_sync = self._user(
            "current@test.com",
            tier=self.main,
            subscription_id="sub_active",
            billing_period_end=datetime.fromtimestamp(
                period_end,
                tz=datetime_timezone.utc,
            ),
        )

        with patch_subscriptions({
            mismatched.stripe_customer_id: [subscription()],
            in_sync.stripe_customer_id: [
                subscription(current_period_end=period_end),
            ],
        }):
            response = self._post_apply({
                "emails": [
                    "paid@test.com",
                    "current@test.com",
                    "nobody@example.com",
                ],
            })

        body = response.json()
        self.assertEqual(body["processed"], 2)
        self.assertEqual(body["changed"], 1)
        self.assertEqual(body["skipped"], 1)
        statuses = {row["email"]: row["status"] for row in body["results"]}
        self.assertEqual(statuses["paid@test.com"], "changed")
        self.assertEqual(statuses["current@test.com"], "skipped")
        self.assertEqual(statuses["nobody@example.com"], "not_found")

    def test_apply_with_warning_status_records_warning_in_response_and_does_not_write(self):
        user = self._user("nosub@test.com", tier=self.main)

        with patch_subscriptions({user.stripe_customer_id: []}):
            response = self._post_apply({"emails": ["nosub@test.com"]})

        body = response.json()
        self.assertEqual(body["processed"], 1)
        self.assertEqual(body["warnings"], 1)
        self.assertEqual(body["results"][0]["status"], "warning")
        self.assertEqual(body["results"][0]["from"], "main")
        self.assertIsNone(body["results"][0]["to"])

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")

    def test_apply_with_stripe_lookup_error_returns_warning_not_500(self):
        user = self._user("stripe-error@test.com")

        with patch(
            "payments.services.backfill_tiers.stripe.Subscription.list",
            side_effect=stripe.InvalidRequestError(
                "No such customer",
                param="customer",
            ),
        ):
            response = self._post_apply({
                "emails": ["stripe-error@test.com"],
                "dry_run": True,
            })

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["processed"], 1)
        self.assertEqual(body["warnings"], 1)
        self.assertEqual(body["results"][0]["status"], "warning")
        self.assertIn("Stripe lookup failed", body["results"][0]["message"])

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")

    def test_apply_caps_at_max_users_when_emails_omitted(self):
        for index in range(3):
            self._user(f"user{index}@test.com")

        with patch.object(tier_reconcile_module, "MAX_USERS_PER_REQUEST", 2):
            with patch_subscriptions({}):
                response = self._post_apply({})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "too_many_users")

    def test_apply_with_invalid_json_body_returns_400_invalid_json_no_stripe_call(self):
        self._user("paid@test.com")

        with patch_subscriptions({}) as stripe_list:
            response = self._post_apply(raw_body="not json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_json")
        self.assertEqual(stripe_list.call_count, 0)

    def test_apply_with_non_object_body_returns_400_invalid_type(self):
        with patch_subscriptions({}):
            response = self._post_apply(raw_body="[]")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")

    def test_apply_with_emails_not_a_list_returns_400_invalid_type(self):
        with patch_subscriptions({}):
            response = self._post_apply({"emails": "alice@test.com"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")

    def test_apply_with_dry_run_not_a_bool_returns_400_invalid_type(self):
        with patch_subscriptions({}):
            response = self._post_apply({"dry_run": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")

    def test_apply_with_empty_string_email_returns_422_validation_error(self):
        with patch_subscriptions({}):
            response = self._post_apply({"emails": [""]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_apply_unauthenticated_returns_401_with_no_writes(self):
        user = self._user("paid@test.com")

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self.client.post(
                APPLY_URL,
                data=json.dumps({"emails": ["paid@test.com"]}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertFalse(
            WebhookEvent.objects.filter(
                event_type="backfill_stripe_tiers",
            ).exists()
        )

    def test_apply_non_staff_token_returns_401(self):
        member = User.objects.create_user(
            email="member-tr2@test.com",
            password="x",
        )
        stale_token = Token(
            key="stale-member-tr2-key",
            user=member,
            name="legacy-member-tr2-token",
        )
        Token.objects.bulk_create([stale_token])

        with patch_subscriptions({}):
            response = self.client.post(
                APPLY_URL,
                data=json.dumps({"emails": ["paid@test.com"]}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Token {stale_token.key}",
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_apply_get_returns_405(self):
        response = self.client.get(APPLY_URL, **self._auth_header())
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "Method not allowed"})

    def test_apply_response_field_names_match_documented_shape(self):
        user = self._user("paid@test.com")

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self._post_apply({"emails": ["paid@test.com"]})

        body = response.json()
        self.assertEqual(
            set(body.keys()),
            {"processed", "changed", "skipped", "warnings", "dry_run", "results"},
        )
        self.assertEqual(
            set(body["results"][0].keys()),
            {
                "email",
                "status",
                "from",
                "to",
                "subscription_id",
                "deactivated_override",
                "saved_metadata",
                "audit_event_id",
                "message",
            },
        )

    def test_diagnostics_response_field_names_match_documented_shape(self):
        user = self._user("paid@test.com")

        with patch_subscriptions({user.stripe_customer_id: [subscription()]}):
            response = self._get_diagnostics()

        body = response.json()
        self.assertEqual(set(body.keys()), {"count", "users"})
        self.assertEqual(
            set(body["users"][0].keys()),
            {
                "email",
                "stripe_customer_id",
                "current_tier",
                "current_tier_source",
                "stripe_active_tier",
                "subscription_id",
                "billing_period_end",
                "action_needed",
            },
        )
        # Confirm we exercised the user we care about.
        self.assertEqual(body["users"][0]["email"], user.email)
