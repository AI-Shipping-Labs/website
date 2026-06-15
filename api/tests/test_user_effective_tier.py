"""Effective-tier reporting in the user API serializer (issue #965).

``serialize_user_state`` now reports the EFFECTIVE tier (override applied)
in the primary ``tier`` field, with ``tier.source`` provenance and an
additive ``base_tier`` object (full payload only). These tests pin that
shape, the ``max(base, override)`` rule, and the end-to-end GET response.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride, Token, User
from api.serializers.users import serialize_user_state
from payments.models import Tier


class SerializeUserEffectiveTierTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.basic = Tier.objects.get(slug="basic")
        cls.main = Tier.objects.get(slug="main")

    def _override(self, user, *, override_tier, original_tier, expires_at=None):
        return TierOverride.objects.create(
            user=user,
            original_tier=original_tier,
            override_tier=override_tier,
            expires_at=expires_at or (timezone.now() + timedelta(days=30)),
        )

    def test_free_base_with_main_override_reports_effective_main(self):
        user = User.objects.create_user(email="ov@test.com", password="x")
        self._override(user, override_tier=self.main, original_tier=self.free)

        payload = serialize_user_state(user)

        self.assertEqual(payload["tier"]["slug"], "main")
        self.assertEqual(payload["tier"]["level"], 20)
        self.assertEqual(payload["tier"]["source"], "override")
        self.assertEqual(payload["base_tier"], {"slug": "free", "level": 0})
        self.assertTrue(payload["tier_override_active"])
        self.assertEqual(payload["tier_override"]["tier_slug"], "main")

    def test_main_base_no_override_reports_subscription(self):
        user = User.objects.create_user(
            email="paid@test.com", password="x", tier=self.main
        )

        payload = serialize_user_state(user)

        self.assertEqual(payload["tier"]["slug"], "main")
        self.assertEqual(payload["tier"]["level"], 20)
        self.assertEqual(payload["tier"]["source"], "subscription")
        self.assertEqual(payload["base_tier"], {"slug": "main", "level": 20})
        self.assertFalse(payload["tier_override_active"])
        self.assertIsNone(payload["tier_override"])

    def test_free_base_no_override_reports_free_source(self):
        user = User.objects.create_user(email="free@test.com", password="x")

        payload = serialize_user_state(user)

        self.assertEqual(payload["tier"]["slug"], "free")
        self.assertEqual(payload["tier"]["level"], 0)
        self.assertEqual(payload["tier"]["source"], "free")
        self.assertEqual(payload["base_tier"], {"slug": "free", "level": 0})

    def test_expired_override_treated_as_no_override(self):
        user = User.objects.create_user(email="exp@test.com", password="x")
        self._override(
            user,
            override_tier=self.main,
            original_tier=self.free,
            expires_at=timezone.now() - timedelta(days=1),
        )

        payload = serialize_user_state(user)

        self.assertEqual(payload["tier"]["slug"], "free")
        self.assertEqual(payload["tier"]["source"], "free")
        self.assertNotEqual(payload["tier"]["source"], "override")
        self.assertFalse(payload["tier_override_active"])

    def test_override_below_or_equal_base_does_not_downgrade(self):
        # Main base with a Basic override: the override grants nothing extra,
        # so the reported tier stays Main and the source stays subscription.
        user = User.objects.create_user(
            email="hi@test.com", password="x", tier=self.main
        )
        self._override(user, override_tier=self.basic, original_tier=self.main)

        payload = serialize_user_state(user)

        self.assertEqual(payload["tier"]["slug"], "main")
        self.assertEqual(payload["tier"]["level"], 20)
        self.assertEqual(payload["tier"]["source"], "subscription")

    def test_compact_row_reports_effective_tier_and_omits_base_tier(self):
        user = User.objects.create_user(email="cmp@test.com", password="x")
        self._override(user, override_tier=self.main, original_tier=self.free)

        payload = serialize_user_state(user, compact=True)

        self.assertEqual(payload["tier"]["slug"], "main")
        self.assertEqual(payload["tier"]["source"], "override")
        self.assertNotIn("base_tier", payload)
        self.assertNotIn("tier_override", payload)
        self.assertTrue(payload["tier_override_active"])

    def test_staff_user_not_escalated_to_premium(self):
        # Regression guard: the serializer must NOT call get_user_level,
        # which short-circuits staff/superuser to Premium. A staff member
        # with a Main base reports Main, not Premium.
        user = User.objects.create_user(
            email="staff@test.com",
            password="x",
            tier=self.main,
            is_staff=True,
            is_superuser=True,
        )

        payload = serialize_user_state(user)

        self.assertEqual(payload["tier"]["slug"], "main")
        self.assertEqual(payload["tier"]["level"], 20)


class UserDetailEffectiveTierApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.main = Tier.objects.get(slug="main")
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="x",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="effective-tier")

    def test_get_user_returns_effective_tier_shape(self):
        user = User.objects.create_user(email="member@test.com", password="x")
        TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
        )

        url = reverse("api_user_detail", kwargs={"email": user.email})
        response = self.client.get(
            url, HTTP_AUTHORIZATION=f"Token {self.token.key}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tier"]["slug"], "main")
        self.assertEqual(body["tier"]["level"], 20)
        self.assertEqual(body["tier"]["source"], "override")
        self.assertEqual(body["base_tier"], {"slug": "free", "level": 0})
        self.assertTrue(body["tier_override_active"])
