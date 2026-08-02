from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model

from payments.models import Tier

LEGACY_NUMERIC_CHECKOUT_TEST_TIME = datetime(2026, 7, 31, 23, 59, tzinfo=UTC)


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
