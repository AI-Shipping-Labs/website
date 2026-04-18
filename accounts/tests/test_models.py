from django.db import IntegrityError
from django.test import TestCase, tag

from accounts.models import User
from payments.models import Tier


@tag('core')
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

    def test_str_returns_email(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertEqual(str(user), "test@example.com")

class UserPaymentFieldsTest(TestCase):
    """Tests for user payment-related fields."""

    def test_tier_defaults_to_free(self):
        """New users should be assigned the 'free' tier on creation."""
        user = User.objects.create_user(email="test@example.com")
        self.assertIsNotNone(user.tier)
        self.assertEqual(user.tier.slug, "free")

    def test_tier_fk_resolves_to_tier_model(self):
        user = User.objects.create_user(email="test@example.com")
        self.assertIsInstance(user.tier, Tier)

