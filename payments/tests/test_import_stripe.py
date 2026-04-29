from datetime import datetime
from datetime import timezone as datetime_timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import ImportBatch, TierOverride
from accounts.services.import_users import get_import_adapter, run_import_batch
from payments.models import Tier
from payments.services import handle_subscription_updated
from payments.services.import_stripe import (
    register_stripe_import_adapter,
    stripe_customer_import_adapter,
)

User = get_user_model()


class StripePager:
    def __init__(self, items):
        self.items = items

    def auto_paging_iter(self):
        return iter(self.items)


def customer(customer_id, email="", name="", created=1, livemode=False):
    return {
        "id": customer_id,
        "email": email,
        "name": name,
        "created": created,
        "livemode": livemode,
    }


def subscription(
    subscription_id,
    *,
    status="active",
    price_id="",
    current_period_end=1_800_000_000,
):
    return {
        "id": subscription_id,
        "status": status,
        "current_period_end": current_period_end,
        "items": {"data": [{"price": {"id": price_id}}]},
    }


@override_settings(STRIPE_SECRET_KEY="sk_test_import")
class StripeImportAdapterTest(TestCase):
    def setUp(self):
        self.basic = Tier.objects.get(slug="basic")
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.stripe_price_id_yearly = "price_basic_yearly"
        self.basic.save(update_fields=["stripe_price_id_monthly", "stripe_price_id_yearly"])

        self.main = Tier.objects.get(slug="main")
        self.main.stripe_price_id_monthly = "price_main_monthly"
        self.main.stripe_price_id_yearly = "price_main_yearly"
        self.main.save(update_fields=["stripe_price_id_monthly", "stripe_price_id_yearly"])

        self.premium = Tier.objects.get(slug="premium")
        self.premium.stripe_price_id_monthly = "price_premium_monthly"
        self.premium.save(update_fields=["stripe_price_id_monthly"])

    def _patch_stripe(self, customers, subscriptions_by_customer=None):
        subscriptions_by_customer = subscriptions_by_customer or {}

        def list_subscriptions(**kwargs):
            return StripePager(subscriptions_by_customer.get(kwargs["customer"], []))

        customer_patch = patch(
            "payments.services.import_stripe.stripe.Customer.list",
            return_value=StripePager(customers),
        )
        subscription_patch = patch(
            "payments.services.import_stripe.stripe.Subscription.list",
            side_effect=list_subscriptions,
        )
        return customer_patch, subscription_patch

    def test_adapter_uses_auto_pagination_and_yields_customers_with_email(self):
        customers = [
            customer("cus_1", "one@example.com"),
            customer("cus_2", "two@example.com"),
        ]
        customer_patch, subscription_patch = self._patch_stripe(customers)

        with customer_patch as customer_list, subscription_patch as subscription_list:
            rows = list(stripe_customer_import_adapter())

        self.assertEqual([row.email for row in rows], ["one@example.com", "two@example.com"])
        customer_list.assert_called_once_with(api_key="sk_test_import", limit=100)
        self.assertEqual(subscription_list.call_count, 2)
        self.assertFalse(User.objects.exists())

    def test_no_email_customer_is_skipped_in_batch_diagnostics(self):
        customers = [customer("cus_no_email")]
        customer_patch, subscription_patch = self._patch_stripe(customers)

        with customer_patch, subscription_patch, self.assertLogs(
            "payments.services.import_stripe", level="INFO"
        ) as logs:
            batch = run_import_batch(
                "stripe",
                stripe_customer_import_adapter,
                dry_run=True,
                send_welcome=False,
            )

        self.assertEqual(batch.users_skipped, 1)
        self.assertEqual(batch.users_created, 0)
        self.assertIn("Skipping Stripe customer without email", "\n".join(logs.output))
        self.assertFalse(User.objects.exists())

    def test_no_subscription_customer_imports_link_metadata_and_imported_tag(self):
        customers = [
            customer("cus_plain", "plain@example.com", name="Plain Customer", created=123)
        ]
        customer_patch, subscription_patch = self._patch_stripe(customers)

        with customer_patch, subscription_patch:
            batch = run_import_batch(
                "stripe",
                stripe_customer_import_adapter,
                send_welcome=False,
            )

        self.assertEqual(batch.users_created, 1)
        user = User.objects.get(email="plain@example.com")
        self.assertEqual(user.stripe_customer_id, "cus_plain")
        self.assertEqual(user.first_name, "Plain")
        self.assertEqual(user.last_name, "Customer")
        self.assertEqual(user.tags, ["stripe:imported"])
        self.assertEqual(
            user.import_metadata["stripe"],
            {
                "stripe_customer_id": "cus_plain",
                "created": 123,
                "livemode": False,
            },
        )

    def test_active_subscription_imports_subscription_fields_tier_and_tags(self):
        period_end = 1_800_001_000
        customers = [customer("cus_active", "active@example.com")]
        subscriptions = {
            "cus_active": [
                subscription(
                    "sub_active",
                    status="active",
                    price_id="price_basic_monthly",
                    current_period_end=period_end,
                )
            ]
        }
        customer_patch, subscription_patch = self._patch_stripe(customers, subscriptions)

        with customer_patch, subscription_patch:
            run_import_batch("stripe", stripe_customer_import_adapter, send_welcome=False)

        user = User.objects.get(email="active@example.com")
        self.assertEqual(user.stripe_customer_id, "cus_active")
        self.assertEqual(user.subscription_id, "sub_active")
        self.assertEqual(
            user.billing_period_end,
            datetime.fromtimestamp(period_end, tz=datetime_timezone.utc),
        )
        self.assertEqual(user.tags, ["stripe:imported", "stripe:active", "stripe:plan-basic"])
        self.assertEqual(TierOverride.objects.get(user=user).override_tier, self.basic)

    def test_trialing_subscription_grants_active_import_access(self):
        customers = [customer("cus_trial", "trial@example.com")]
        subscriptions = {
            "cus_trial": [
                subscription("sub_trial", status="trialing", price_id="price_main_yearly")
            ]
        }
        customer_patch, subscription_patch = self._patch_stripe(customers, subscriptions)

        with customer_patch, subscription_patch:
            run_import_batch("stripe", stripe_customer_import_adapter, send_welcome=False)

        user = User.objects.get(email="trial@example.com")
        self.assertEqual(user.subscription_id, "sub_trial")
        self.assertIn("stripe:active", user.tags)
        self.assertIn("stripe:plan-main", user.tags)
        self.assertEqual(TierOverride.objects.get(user=user).override_tier, self.main)

    def test_churned_customer_imports_without_paid_access(self):
        customers = [customer("cus_churned", "churned@example.com")]
        subscriptions = {
            "cus_churned": [
                subscription("sub_old", status="canceled", price_id="price_basic_monthly")
            ]
        }
        customer_patch, subscription_patch = self._patch_stripe(customers, subscriptions)

        with customer_patch, subscription_patch:
            run_import_batch("stripe", stripe_customer_import_adapter, send_welcome=False)

        user = User.objects.get(email="churned@example.com")
        self.assertEqual(user.stripe_customer_id, "cus_churned")
        self.assertEqual(user.subscription_id, "")
        self.assertIsNone(user.billing_period_end)
        self.assertEqual(user.tags, ["stripe:imported", "stripe:churned"])
        self.assertFalse(TierOverride.objects.filter(user=user).exists())

    def test_unknown_active_price_logs_warning_and_does_not_grant_tier(self):
        customers = [customer("cus_unknown", "unknown@example.com")]
        subscriptions = {
            "cus_unknown": [
                subscription("sub_unknown", price_id="price_not_configured")
            ]
        }
        customer_patch, subscription_patch = self._patch_stripe(customers, subscriptions)

        with customer_patch, subscription_patch, self.assertLogs(
            "payments.services.import_stripe", level="WARNING"
        ) as logs:
            run_import_batch("stripe", stripe_customer_import_adapter, send_welcome=False)

        user = User.objects.get(email="unknown@example.com")
        self.assertEqual(user.stripe_customer_id, "cus_unknown")
        self.assertEqual(user.subscription_id, "sub_unknown")
        self.assertEqual(user.tags, ["stripe:imported", "stripe:active"])
        self.assertFalse(TierOverride.objects.filter(user=user).exists())
        self.assertIn("price_not_configured", user.import_metadata["stripe"]["subscription_price_id"])
        self.assertIn("unknown price price_not_configured", "\n".join(logs.output))

    def test_multiple_active_subscriptions_choose_highest_mapped_tier_then_latest_period(self):
        customers = [customer("cus_multi", "multi@example.com")]
        subscriptions = {
            "cus_multi": [
                subscription(
                    "sub_basic_late",
                    price_id="price_basic_monthly",
                    current_period_end=1_900_000_000,
                ),
                subscription(
                    "sub_main_early",
                    price_id="price_main_monthly",
                    current_period_end=1_700_000_000,
                ),
                subscription(
                    "sub_main_late",
                    price_id="price_main_yearly",
                    current_period_end=1_800_000_000,
                ),
            ]
        }
        customer_patch, subscription_patch = self._patch_stripe(customers, subscriptions)

        with customer_patch, subscription_patch:
            run_import_batch("stripe", stripe_customer_import_adapter, send_welcome=False)

        user = User.objects.get(email="multi@example.com")
        self.assertEqual(user.subscription_id, "sub_main_late")
        self.assertIn("stripe:plan-main", user.tags)
        self.assertEqual(TierOverride.objects.get(user=user).override_tier, self.main)

    def test_existing_customer_id_is_not_overwritten(self):
        existing = User.objects.create_user(
            email="existing@example.com",
            stripe_customer_id="cus_existing",
        )
        customers = [customer("cus_new", "existing@example.com")]
        customer_patch, subscription_patch = self._patch_stripe(customers)

        with customer_patch, subscription_patch:
            batch = run_import_batch(
                "stripe",
                stripe_customer_import_adapter,
                send_welcome=False,
            )

        self.assertEqual(batch.users_updated, 1)
        existing.refresh_from_db()
        self.assertEqual(existing.stripe_customer_id, "cus_existing")
        self.assertEqual(existing.import_metadata["stripe"]["stripe_customer_id"], "cus_new")
        self.assertEqual(User.objects.filter(email="existing@example.com").count(), 1)

    def test_command_registration_supports_stripe_dry_run(self):
        register_stripe_import_adapter()
        customers = [customer("cus_cmd", "cmd@example.com")]
        customer_patch, subscription_patch = self._patch_stripe(customers)
        out = StringIO()

        with customer_patch, subscription_patch:
            call_command(
                "import_users",
                "stripe",
                "--dry-run",
                "--no-send-welcome",
                stdout=out,
            )

        self.assertIn("1 created", out.getvalue())
        self.assertIs(get_import_adapter("stripe"), stripe_customer_import_adapter)
        self.assertFalse(User.objects.filter(email="cmd@example.com").exists())
        batch = ImportBatch.objects.get(source="stripe")
        self.assertTrue(batch.dry_run)

    def test_webhook_handler_finds_imported_user_by_customer_id(self):
        customers = [customer("cus_webhook", "webhook-linked@example.com")]
        subscriptions = {
            "cus_webhook": [
                subscription("sub_webhook", price_id="price_basic_monthly")
            ]
        }
        customer_patch, subscription_patch = self._patch_stripe(customers, subscriptions)

        with customer_patch, subscription_patch:
            run_import_batch("stripe", stripe_customer_import_adapter, send_welcome=False)

        handle_subscription_updated(
            {
                "id": "sub_new_webhook",
                "customer": "cus_webhook",
                "status": "active",
                "cancel_at_period_end": False,
                "current_period_end": 1_900_000_000,
                "items": {"data": [{"price": {"id": "price_premium_monthly"}}]},
            }
        )

        user = User.objects.get(email="webhook-linked@example.com")
        self.assertEqual(user.tier, self.premium)
        self.assertEqual(user.subscription_id, "sub_new_webhook")
        self.assertEqual(User.objects.filter(stripe_customer_id="cus_webhook").count(), 1)
