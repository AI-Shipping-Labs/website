"""Tests for Stripe webhook resolution-by-alias (issue #840a).

The relay case: a checkout / payment-failed whose billing email is a known
``EmailAlias`` (e.g. an Apple Pay relay address) must resolve to the
canonical account instead of creating a duplicate user. Primary
``User.email`` always wins; an unknown email with no alias still creates a
new user (today's behaviour preserved).

Follows the existing ``payments`` webhook-handler test pattern: construct a
``session_data`` / ``invoice_data`` dict and call the handler directly,
reusing ``QuietSubscriptionLookupMixin`` so the 3-step resolver fallback
never makes a real Stripe call.
"""

from django.core import mail
from django.test import TestCase, override_settings

from accounts.models import EmailAlias, User
from payments.services import (
    handle_checkout_completed,
    handle_invoice_payment_failed,
)

from .test_webhooks import QuietSubscriptionLookupMixin


class CheckoutAliasResolutionTest(QuietSubscriptionLookupMixin, TestCase):
    """``handle_checkout_completed`` alias-routing scenarios."""

    def test_relay_checkout_routes_to_canonical_account(self):
        """A relay-email checkout upgrades the alias owner; no duplicate user."""
        canonical = User.objects.create_user(email="stefano@test.com")
        self.assertEqual(canonical.tier.slug, "free")
        EmailAlias.objects.create(user=canonical, email="relay@icloud.test")

        session_data = {
            "id": "cs_relay",
            "customer": "cus_relay",
            "customer_details": {"email": "relay@icloud.test"},
            "subscription": "sub_relay",
            "client_reference_id": None,
            "metadata": {"tier_slug": "main"},
        }

        handle_checkout_completed(session_data)

        canonical.refresh_from_db()
        self.assertEqual(canonical.tier.slug, "main")
        self.assertEqual(canonical.stripe_customer_id, "cus_relay")
        self.assertEqual(canonical.subscription_id, "sub_relay")
        # No new user spawned for the relay address.
        self.assertFalse(
            User.objects.filter(email="relay@icloud.test").exists()
        )

    def test_primary_email_wins_over_alias(self):
        """The primary-email match takes precedence; the alias is never used."""
        user_a = User.objects.create_user(email="a@test.com")
        user_b = User.objects.create_user(email="b@test.com")
        # An alias of an unrelated address routes to B.
        EmailAlias.objects.create(user=user_b, email="old@test.com")

        session_data = {
            "id": "cs_primary",
            "customer": "cus_primary",
            "customer_details": {"email": "a@test.com"},
            "subscription": "sub_primary",
            "client_reference_id": None,
            "metadata": {"tier_slug": "main"},
        }

        handle_checkout_completed(session_data)

        user_a.refresh_from_db()
        user_b.refresh_from_db()
        # A (primary) is upgraded; B is untouched.
        self.assertEqual(user_a.tier.slug, "main")
        self.assertEqual(user_a.stripe_customer_id, "cus_primary")
        self.assertEqual(user_b.tier.slug, "free")
        self.assertEqual(user_b.stripe_customer_id, "")

    def test_unknown_email_with_no_alias_still_creates_user(self):
        """No primary, no alias -> a brand-new user is created (unchanged)."""
        session_data = {
            "id": "cs_new",
            "customer": "cus_new",
            "customer_details": {"email": "brand-new@test.com"},
            "subscription": "sub_new",
            "client_reference_id": None,
            "metadata": {"tier_slug": "main"},
        }

        handle_checkout_completed(session_data)

        new_user = User.objects.get(email="brand-new@test.com")
        self.assertEqual(new_user.tier.slug, "main")
        self.assertEqual(new_user.stripe_customer_id, "cus_new")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@test.com",
)
class InvoicePaymentFailedAliasResolutionTest(
    QuietSubscriptionLookupMixin, TestCase
):
    """``handle_invoice_payment_failed`` alias-routing scenario."""

    def test_payment_failed_for_relay_alias_notifies_canonical_user(self):
        canonical = User.objects.create_user(email="stefano@test.com")
        EmailAlias.objects.create(user=canonical, email="relay@icloud.test")

        invoice_data = {
            # No matching stripe_customer_id on any user; the exact-email
            # lookup also misses, so the alias fallback must fire.
            "customer": "cus_unknown",
            "customer_email": "relay@icloud.test",
        }

        mail.outbox = []
        handle_invoice_payment_failed(invoice_data)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["stefano@test.com"])

    def test_payment_failed_unknown_email_no_alias_sends_nothing(self):
        invoice_data = {
            "customer": "cus_unknown",
            "customer_email": "nobody@test.com",
        }
        mail.outbox = []
        handle_invoice_payment_failed(invoice_data)
        self.assertEqual(len(mail.outbox), 0)
