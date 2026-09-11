"""Tests for the ``StripePaymentLink`` model.

The previous ``test_different_period_allowed`` round-tripped
``StripePaymentLink`` rows with different ``billing_period``
values to confirm Django can save two different rows — pure
ORM behaviour, not project logic. Removed per
``_docs/testing-guidelines.md`` Rule 3. Stripe-specific
behaviour for these links is exercised by the checkout/webhook
integration tests in ``payments/tests/`` proper.
"""

from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase

from payments.models import Membership, Tier
from tests.fixtures import TierSetupMixin

User = get_user_model()


class MembershipFieldParityTest(TierSetupMixin, TestCase):
    """Membership carries the five fields moved off User (issue #1579)."""

    def test_field_definitions_match_spec(self):
        tier_field = Membership._meta.get_field("tier")
        self.assertIsInstance(tier_field, models.ForeignKey)
        self.assertEqual(tier_field.remote_field.on_delete, models.PROTECT)
        self.assertEqual(tier_field.remote_field.related_name, "memberships")
        self.assertTrue(tier_field.null)

        pending_field = Membership._meta.get_field("pending_tier")
        self.assertIsInstance(pending_field, models.ForeignKey)
        self.assertEqual(
            pending_field.remote_field.on_delete, models.SET_NULL
        )
        self.assertEqual(
            pending_field.remote_field.related_name, "pending_memberships"
        )
        self.assertTrue(pending_field.null)

        user_field = Membership._meta.get_field("user")
        self.assertIsInstance(user_field, models.OneToOneField)
        self.assertEqual(user_field.remote_field.on_delete, models.CASCADE)
        self.assertEqual(user_field.remote_field.related_name, "membership")

        self.assertTrue(
            Membership._meta.get_field("billing_period_end").null
        )

        stripe_field = Membership._meta.get_field("stripe_customer_id")
        self.assertEqual(stripe_field.max_length, 255)
        self.assertFalse(stripe_field.null)

        sub_field = Membership._meta.get_field("subscription_id")
        self.assertEqual(sub_field.max_length, 255)
        self.assertFalse(sub_field.null)

    def test_membership_cascade_deletes_with_user(self):
        user = User.objects.create_user(email="cascade@test.com")
        membership = Membership.for_user(user)
        user.delete()
        self.assertFalse(Membership.objects.filter(pk=membership.pk).exists())


class ForUserTest(TierSetupMixin, TestCase):
    """Membership.for_user lazy create and idempotence (issue #1579)."""

    def test_returns_existing_row_without_creating_duplicate(self):
        user = User.objects.create_user(email="existing@test.com")
        first = Membership.for_user(user)
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)
        second = Membership.for_user(user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)

    def test_lazy_create_uses_free_tier(self):
        user = User.objects.create_user(email="lazy@test.com")
        Membership.objects.filter(user=user).delete()
        membership = Membership.for_user(user)
        self.assertEqual(membership.tier, self.free_tier)
        self.assertEqual(membership.stripe_customer_id, "")
        self.assertEqual(membership.subscription_id, "")
        self.assertIsNone(membership.billing_period_end)

    def test_lazy_create_falls_back_to_null_tier_without_free_row(self):
        user = User.objects.create_user(email="notier@test.com")
        Membership.objects.filter(user=user).delete()
        Tier.objects.filter(slug="free").delete()
        # Re-fetch so the reverse relation cache does not hide the deletion.
        user = User.objects.get(pk=user.pk)
        membership = Membership.for_user(user)
        self.assertIsNone(membership.tier)
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)


class MembershipCreationInvariantTest(TierSetupMixin, TestCase):
    """Every user creation entry point yields exactly one row (issue #1579)."""

    def test_create_user_creates_membership(self):
        user = User.objects.create_user(email="plain@test.com")
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)
        self.assertEqual(Membership.for_user(user).tier, self.free_tier)

    def test_objects_create_creates_membership(self):
        user = User.objects.create(email="objects-create@test.com")
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)

    def test_save_based_creation_creates_membership(self):
        user = User(email="save-based@test.com")
        user.set_unusable_password()
        user.save()
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)

    def test_create_superuser_creates_membership(self):
        admin = User.objects.create_superuser(
            email="admin@test.com", password="pw12345!"
        )
        self.assertEqual(Membership.objects.filter(user=admin).count(), 1)
        self.assertEqual(Membership.for_user(admin).tier, self.free_tier)

    def test_user_save_no_longer_assigns_tier_column(self):
        # Issue #1579: the free-tier default moved to Membership creation;
        # User.save() leaves the abandoned column alone.
        user = User.objects.create_user(email="nosave-default@test.com")
        user.refresh_from_db()
        self.assertIsNone(user.tier_id)
        self.assertEqual(Membership.for_user(user).tier, self.free_tier)
