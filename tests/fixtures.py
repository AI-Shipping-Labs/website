from django.contrib.auth import get_user_model

from payments.models import Tier


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
