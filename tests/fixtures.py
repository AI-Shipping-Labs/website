from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model

from payments.models import Membership, Tier

LEGACY_NUMERIC_CHECKOUT_TEST_TIME = datetime(2026, 7, 31, 23, 59, tzinfo=UTC)

# Sentinel for set_membership: "leave this field untouched" (distinct from
# an explicit None, which clears the field).
_UNSET = object()


def set_membership(
    user,
    *,
    tier=_UNSET,
    pending_tier=_UNSET,
    billing_period_end=_UNSET,
    stripe_customer_id=_UNSET,
    subscription_id=_UNSET,
):
    """Write tier/Stripe fields onto the user's Membership row (issue #1579).

    Replaces the pre-#1579 test setups that assigned ``user.membership.tier`` /
    ``user.membership.subscription_id`` / ... directly. Only the passed fields change;
    an explicit ``None`` clears a field, an omitted field is untouched.
    Returns the saved Membership row (the same instance the reverse
    ``user.membership`` descriptor caches, so subsequent reads agree).
    """
    membership = Membership.for_user(user)
    update_fields = []
    if tier is not _UNSET:
        membership.tier = tier
        update_fields.append("tier")
    if pending_tier is not _UNSET:
        membership.pending_tier = pending_tier
        update_fields.append("pending_tier")
    if billing_period_end is not _UNSET:
        membership.billing_period_end = billing_period_end
        update_fields.append("billing_period_end")
    if stripe_customer_id is not _UNSET:
        membership.stripe_customer_id = stripe_customer_id
        update_fields.append("stripe_customer_id")
    if subscription_id is not _UNSET:
        membership.subscription_id = subscription_id
        update_fields.append("subscription_id")
    if update_fields:
        membership.save(update_fields=update_fields)
    return membership


def create_user_with_membership(
    *,
    tier=_UNSET,
    pending_tier=_UNSET,
    billing_period_end=_UNSET,
    stripe_customer_id=_UNSET,
    subscription_id=_UNSET,
    **user_fields,
):
    """Create a user and configure its Membership in one test-fixture call."""
    User = get_user_model()
    user = User.objects.create_user(**user_fields)
    set_membership(
        user,
        tier=tier,
        pending_tier=pending_tier,
        billing_period_end=billing_period_end,
        stripe_customer_id=stripe_customer_id,
        subscription_id=subscription_id,
    )
    return user


def call_checkout_in_legacy_numeric_compat_window(handler, payload):
    """Run legacy numeric-reference fixtures before their production cutoff.

    A large set of webhook tests predates opaque checkout bindings and uses a
    numeric user id as ``client_reference_id``. Production correctly stopped
    accepting that format on 2026-08-01. Keep those fixtures deterministic
    without weakening the production cutoff; dedicated security tests call the
    real handler directly and cover the cutoff/kill-switch behavior.
    """
    reference = str(payload.get("client_reference_id") or "")
    if not reference.isdigit():
        return handler(payload)
    with patch(
        "payments.services.webhook_handlers.django_timezone.now",
        return_value=LEGACY_NUMERIC_CHECKOUT_TEST_TIME,
    ):
        return handler(payload)


class TierSetupMixin:
    """Shared mixin that ensures the four standard membership tiers exist.

    Provides cls.free_tier, cls.basic_tier, cls.main_tier, cls.premium_tier.
    Uses get_or_create so it works whether tiers are seeded by migration or not.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_tier = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0})[0]
        cls.basic_tier = Tier.objects.get_or_create(
            slug="basic", defaults={"name": "Basic", "level": 10})[0]
        cls.main_tier = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20})[0]
        cls.premium_tier = Tier.objects.get_or_create(
            slug="premium", defaults={"name": "Premium", "level": 30})[0]


class StaffUserMixin:
    """Creates a staff user once per class. Tests log in via
    self.client.login(**self.staff_credentials).

    Composes with TierSetupMixin via super().setUpTestData() — both mixins
    chain through to TestCase.setUpTestData. List the mixins in any order
    before TestCase in the class hierarchy.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="testpass", is_staff=True,
        )
        cls.staff_credentials = {"email": "staff@test.com", "password": "testpass"}
