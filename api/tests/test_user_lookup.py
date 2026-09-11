"""Tests for the shared operator API user lookup."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from api.user_lookup import find_user_by_primary_email
from tests.fixtures import TierSetupMixin, set_membership

User = get_user_model()


class UserLookupTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(email="lookup-member@example.com")
        set_membership(
            cls.user,
            tier=cls.main_tier,
            pending_tier=cls.basic_tier,
        )

    def test_lookup_loads_membership_tiers_in_the_lookup_query(self):
        with self.assertNumQueries(1):
            user = find_user_by_primary_email("LOOKUP-MEMBER@example.com")
            self.assertEqual(user.membership.tier, self.main_tier)
            self.assertEqual(user.membership.pending_tier, self.basic_tier)
