"""Revoking leftover grants without removing grants that raise access."""

from django.contrib.auth import get_user_model

from api.tests.test_tier_overrides_list_1936 import (
    FourMemberFixture,
    TierOverrideApiBase,
    _grant,
)
from community.models import CommunityAuditLog
from payments.models import TierOverride
from tests.fixtures import set_membership

User = get_user_model()


class RevokeIneffectiveOnlyTest(FourMemberFixture):
    def _active(self, user):
        return set(
            TierOverride.objects.filter(user=user, is_active=True)
            .values_list("pk", flat=True)
        )

    def test_revokes_leftover_grant_and_leaves_raising_grants(self):
        response = self._revoke({
            "emails": ["b-1936@test.com", "a-1936@test.com"],
            "ineffective_only": True,
        })

        body = response.json()
        statuses = {r["email"]: r["status"] for r in body["results"]}
        self.assertEqual(
            statuses,
            {"b-1936@test.com": "no_active_override", "a-1936@test.com": "revoked"},
        )
        self.assertEqual(body["revoked"], 1)
        a_result = body["results"][1]
        self.assertEqual([o["id"] for o in a_result["overrides"]], [self.a_basic.pk])
        self.assertEqual(self._active(self.a), set())
        self.assertEqual(self._active(self.b), {self.b_premium.pk, self.b_basic.pk})
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                action="tier_override_revoked", user=self.a,
            ).count(),
            1,
        )
        self.assertFalse(
            CommunityAuditLog.objects.filter(
                action="tier_override_revoked", user=self.b,
            ).exists()
        )

    def test_dry_run_reports_without_changing_anything(self):
        body = self._revoke({
            "emails": ["b-1936@test.com", "a-1936@test.com"],
            "ineffective_only": True,
            "dry_run": True,
        }).json()

        statuses = {r["email"]: r["status"] for r in body["results"]}
        self.assertEqual(statuses["a-1936@test.com"], "would_revoke")
        self.assertEqual(statuses["b-1936@test.com"], "no_active_override")
        self.assertEqual(self._active(self.a), {self.a_basic.pk})
        self.assertFalse(
            CommunityAuditLog.objects.filter(
                action="tier_override_revoked",
            ).exists()
        )


class RevokeMixedGrantsTest(TierOverrideApiBase):
    """Stored Main member with a leftover Basic and a raising Premium grant."""

    def setUp(self):
        self.member = User.objects.create_user(email="mixed-1936@test.com")
        set_membership(self.member, tier=self.main)
        self.basic_grant = _grant(
            self.member, self.basic, original_tier=self.main, days=30,
        )
        self.premium_grant = _grant(
            self.member, self.premium, original_tier=self.main, days=60,
        )

    def _active(self):
        return set(
            TierOverride.objects.filter(user=self.member, is_active=True)
            .values_list("pk", flat=True)
        )

    def test_ineffective_only_revokes_only_the_basic_grant(self):
        body = self._revoke({
            "emails": ["mixed-1936@test.com"], "ineffective_only": True,
        }).json()

        self.assertEqual(
            [o["id"] for o in body["results"][0]["overrides"]],
            [self.basic_grant.pk],
        )
        self.assertEqual(self._active(), {self.premium_grant.pk})

    def test_default_still_revokes_every_active_grant(self):
        body = self._revoke({"emails": ["mixed-1936@test.com"]}).json()

        self.assertEqual(body["results"][0]["status"], "revoked")
        self.assertEqual(self._active(), set())
