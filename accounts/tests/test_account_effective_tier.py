"""Effective-tier headline + override provenance on /account/ (issue #965).

The "Current Plan" headline reads the EFFECTIVE tier (override applied)
when an active override raises the tier above base, and a provenance line
("Main plan — tier override from Free until <date>") explains where it
comes from. Paid/free members with no override see the base tier unchanged.
"""

import datetime

from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride, User
from payments.models import Tier


class AccountEffectiveTierContextTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.main = Tier.objects.get(slug="main")

    def _get_account(self, user):
        self.client.force_login(user)
        return self.client.get("/account/")

    def test_override_user_headline_and_provenance_in_context(self):
        user = User.objects.create_user(email="ov@example.com")
        user.tier = self.free
        user.save(update_fields=["tier"])
        TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + datetime.timedelta(days=30),
            is_active=True,
        )

        response = self._get_account(user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["effective_tier"], self.main)
        provenance = response.context["override_provenance"]
        self.assertIn("Main plan", provenance)
        self.assertIn("tier override from Free", provenance)
        self.assertIn("until", provenance)

    def test_override_headline_renders_effective_tier_name(self):
        user = User.objects.create_user(email="ov2@example.com")
        user.tier = self.free
        user.save(update_fields=["tier"])
        TierOverride.objects.create(
            user=user,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + datetime.timedelta(days=30),
            is_active=True,
        )

        response = self._get_account(user)
        content = response.content.decode()

        # Headline element shows the effective tier, and the provenance line
        # is present as a sibling element.
        name_idx = content.find('id="tier-name"')
        self.assertNotEqual(name_idx, -1, "tier-name element must render")
        headline = content[name_idx:content.find("</span>", name_idx)]
        self.assertIn("Main", headline)
        self.assertNotIn("Free", headline)
        self.assertIn('id="tier-override-provenance"', content)

    def test_paid_member_no_override_shows_base_tier_no_provenance(self):
        user = User.objects.create_user(email="paid@example.com")
        user.tier = self.main
        user.save(update_fields=["tier"])

        response = self._get_account(user)

        self.assertEqual(response.context["effective_tier"], self.main)
        self.assertEqual(response.context["override_provenance"], "")
        self.assertNotIn(
            'id="tier-override-provenance"', response.content.decode()
        )

    def test_free_member_no_override_shows_free(self):
        user = User.objects.create_user(email="free@example.com")
        user.tier = self.free
        user.save(update_fields=["tier"])

        response = self._get_account(user)
        content = response.content.decode()

        self.assertEqual(response.context["effective_tier"], self.free)
        self.assertEqual(response.context["override_provenance"], "")
        name_idx = content.find('id="tier-name"')
        headline = content[name_idx:content.find("</span>", name_idx)]
        self.assertIn("Free", headline)
