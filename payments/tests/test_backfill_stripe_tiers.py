from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from io import StringIO
from unittest.mock import patch

import stripe
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier, WebhookEvent

User = get_user_model()


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


@override_settings(STRIPE_SECRET_KEY="sk_test_backfill")
class BackfillStripeTiersCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.main = Tier.objects.get(slug="main")
        cls.main.stripe_price_id_monthly = "price_main_monthly"
        cls.main.save(update_fields=["stripe_price_id_monthly"])

    def _user(self, email, **kwargs):
        kwargs.setdefault("stripe_customer_id", f"cus_{email.split('@')[0]}")
        return User.objects.create_user(email=email, password="x", **kwargs)

    def _patch_subscriptions(self, subscriptions_by_customer):
        def list_subscriptions(**kwargs):
            return StripePager(subscriptions_by_customer.get(kwargs["customer"], []))

        return patch(
            "payments.services.backfill_tiers.stripe.Subscription.list",
            side_effect=list_subscriptions,
        )

    def test_backfill_writes_tier_directly(self):
        user = self._user("paid@test.com")
        period_end = 1_800_000_123

        with self._patch_subscriptions({
            user.stripe_customer_id: [subscription(current_period_end=period_end)]
        }):
            call_command("backfill_stripe_tiers", stdout=StringIO())

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")
        self.assertEqual(user.subscription_id, "sub_active")
        self.assertEqual(
            user.billing_period_end,
            datetime.fromtimestamp(period_end, tz=datetime_timezone.utc),
        )
        audit = WebhookEvent.objects.get(event_type="backfill_stripe_tiers")
        self.assertEqual(audit.payload["old_tier_slug"], "free")
        self.assertEqual(audit.payload["new_tier_slug"], "main")

    def test_backfill_deactivates_redundant_override(self):
        user = self._user("override@test.com")
        override = TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
        )

        with self._patch_subscriptions({
            user.stripe_customer_id: [subscription()]
        }):
            call_command("backfill_stripe_tiers", stdout=StringIO())

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")
        self.assertFalse(override.is_active)

    def test_backfill_skips_when_tier_already_matches(self):
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
        out = StringIO()

        with self._patch_subscriptions({
            user.stripe_customer_id: [subscription(current_period_end=period_end)]
        }):
            call_command("backfill_stripe_tiers", stdout=out)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")
        self.assertIn("no change: already on main", out.getvalue())
        self.assertFalse(WebhookEvent.objects.exists())

    def test_backfill_no_subscription_does_not_downgrade(self):
        user = self._user("nosub@test.com", tier=self.main)
        err = StringIO()

        with self._patch_subscriptions({user.stripe_customer_id: []}):
            call_command("backfill_stripe_tiers", stdout=StringIO(), stderr=err)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")
        self.assertIn("no active Stripe subscription", err.getvalue())

    def test_dry_run_does_not_write(self):
        user = self._user("dryrun@test.com")
        override = TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
        )

        with self._patch_subscriptions({
            user.stripe_customer_id: [subscription()]
        }):
            call_command("backfill_stripe_tiers", "--dry-run", stdout=StringIO())

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertTrue(override.is_active)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_email_filter_targets_one_user(self):
        target = self._user("target@test.com")
        other = self._user("other@test.com")
        no_customer = User.objects.create_user(email="plain@test.com", password="x")

        with self._patch_subscriptions({
            target.stripe_customer_id: [subscription("sub_target")],
            other.stripe_customer_id: [subscription("sub_other")],
        }):
            call_command(
                "backfill_stripe_tiers",
                "--email",
                target.email,
                stdout=StringIO(),
            )

        target.refresh_from_db()
        other.refresh_from_db()
        no_customer.refresh_from_db()
        self.assertEqual(target.tier.slug, "main")
        self.assertEqual(other.tier.slug, "free")
        self.assertEqual(no_customer.tier.slug, "free")

    def test_unknown_price_logs_warning(self):
        user = self._user("unknown@test.com")
        err = StringIO()

        with self._patch_subscriptions({
            user.stripe_customer_id: [
                subscription(price_id="price_unknown")
            ]
        }):
            call_command("backfill_stripe_tiers", stdout=StringIO(), stderr=err)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertIn("unknown price price_unknown", err.getvalue())

    def test_stripe_lookup_error_logs_warning_without_crashing(self):
        user = self._user("stripe-error@test.com")
        err = StringIO()

        with patch(
            "payments.services.backfill_tiers.stripe.Subscription.list",
            side_effect=stripe.InvalidRequestError(
                "No such customer",
                param="customer",
            ),
        ):
            call_command("backfill_stripe_tiers", stdout=StringIO(), stderr=err)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertIn("Stripe lookup failed", err.getvalue())
        self.assertFalse(WebhookEvent.objects.exists())
