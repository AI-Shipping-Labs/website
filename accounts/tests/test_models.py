from django.db import IntegrityError
from django.test import TestCase

from accounts.models import User
from payments.models import Tier


class UserModelTest(TestCase):
    """Tests for the custom User model."""

    def test_create_user_with_email(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertEqual(user.email, "test@example.com")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="")

    def test_create_user_normalizes_email(self):
        user = User.objects.create_user(email="Test@EXAMPLE.com")
        self.assertEqual(user.email, "Test@example.com")

    def test_create_user_with_password(self):
        user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )
        self.assertTrue(user.check_password("testpass123"))

    def test_create_user_without_password_sets_unusable(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertFalse(user.has_usable_password())

    def test_create_superuser(self):
        user = User.objects.create_superuser(
            email="admin@example.com", password="adminpass123"
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_create_superuser_requires_is_staff(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="admin@example.com",
                password="adminpass123",
                is_staff=False,
            )

    def test_create_superuser_requires_is_superuser(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="admin@example.com",
                password="adminpass123",
                is_superuser=False,
            )

    def test_email_is_unique(self):
        User.objects.create_user(email="test@example.com")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="test@example.com")

    def test_username_field_is_email(self):
        self.assertEqual(User.USERNAME_FIELD, "email")

    def test_required_fields_is_empty(self):
        self.assertEqual(User.REQUIRED_FIELDS, [])

    def test_str_returns_email(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertEqual(str(user), "test@example.com")

    def test_username_is_not_a_db_field(self):
        """The username field is set to None so it is not in the database."""
        field_names = [f.name for f in User._meta.get_fields()]
        self.assertNotIn("username", field_names)


class UserProfileFieldsTest(TestCase):
    """Tests for user profile fields."""

    def test_email_verified_default_false(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertFalse(user.email_verified)

    def test_unsubscribed_default_false(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertFalse(user.unsubscribed)

    def test_email_preferences_default_empty_dict(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertEqual(user.email_preferences, {})

    def test_email_preferences_stores_json(self):
        user = User.objects.create_user(email="test@example.com")
        user.email_preferences = {"newsletter": True, "promotions": False}
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.email_preferences["newsletter"], True)
        self.assertEqual(user.email_preferences["promotions"], False)


class UserPaymentFieldsTest(TestCase):
    """Tests for user payment-related fields."""

    def test_stripe_customer_id_default_empty(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertEqual(user.stripe_customer_id, "")

    def test_subscription_id_default_empty(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertEqual(user.subscription_id, "")

    def test_tier_defaults_to_free(self):
        """New users should be assigned the 'free' tier on creation."""
        user = User.objects.create_user(email="test@example.com")
        self.assertIsNotNone(user.tier)
        self.assertEqual(user.tier.slug, "free")

    def test_tier_fk_resolves_to_tier_model(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertIsInstance(user.tier, Tier)

    def test_billing_period_end_default_null(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertIsNone(user.billing_period_end)

    def test_pending_tier_default_null(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertIsNone(user.pending_tier)

    def test_can_set_tier_to_paid(self):
        user = User.objects.create_user(email="test@example.com")
        main_tier = Tier.objects.get(slug="main")
        user.tier = main_tier
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")

    def test_can_set_pending_tier(self):
        user = User.objects.create_user(email="test@example.com")
        basic_tier = Tier.objects.get(slug="basic")
        user.pending_tier = basic_tier
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.pending_tier.slug, "basic")

    def test_can_set_stripe_customer_id(self):
        user = User.objects.create_user(email="test@example.com")
        user.stripe_customer_id = "cus_test123"
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.stripe_customer_id, "cus_test123")

    def test_can_set_subscription_id(self):
        user = User.objects.create_user(email="test@example.com")
        user.subscription_id = "sub_test123"
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.subscription_id, "sub_test123")


class UserCommunityFieldsTest(TestCase):
    """Tests for user community-related fields."""

    def test_slack_user_id_default_empty(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertEqual(user.slack_user_id, "")

    def test_can_set_slack_user_id(self):
        user = User.objects.create_user(email="test@example.com")
        user.slack_user_id = "U12345678"
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "U12345678")


class UserAllFieldsPresenceTest(TestCase):
    """Verify all required fields from the spec exist on the User model."""

    def test_user_has_all_spec_fields(self):
        """User model includes all fields from the issue spec."""
        user = User.objects.create_user(email="test@example.com")
        # Profile fields
        self.assertTrue(hasattr(user, "email"))
        self.assertTrue(hasattr(user, "email_verified"))
        self.assertTrue(hasattr(user, "unsubscribed"))
        self.assertTrue(hasattr(user, "email_preferences"))
        # Payment fields
        self.assertTrue(hasattr(user, "stripe_customer_id"))
        self.assertTrue(hasattr(user, "subscription_id"))
        self.assertTrue(hasattr(user, "tier"))
        self.assertTrue(hasattr(user, "tier_id"))
        self.assertTrue(hasattr(user, "billing_period_end"))
        self.assertTrue(hasattr(user, "pending_tier"))
        self.assertTrue(hasattr(user, "pending_tier_id"))
        # Community fields
        self.assertTrue(hasattr(user, "slack_user_id"))
