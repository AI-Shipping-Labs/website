"""The member API selects the strongest active override (issue #1933).

Several overrides can be active at once (a staff grant next to a Maven
grant). The API must pick the same one ``content.access`` grants access
from: highest tier first, ties broken by the latest expiry. List surfaces
(``GET /api/users`` and the CRM export) resolve every row's override in
one batched query instead of one query per row.
"""

from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import Token, User
from api.serializers.users import serialize_user_state
from content.access import get_active_override, get_user_level
from crm.models import CRMRecord
from payments.models import TierOverride
from tests.fixtures import TierSetupMixin, set_membership

STACKED_EMAIL = "stacked@test.com"


def _override_queries(ctx):
    return [
        query["sql"] for query in ctx.captured_queries
        if "accounts_tieroverride" in query["sql"]
    ]


class StackedOverrideFixtureMixin(TierSetupMixin):
    """Free-base member with an older staff Premium grant and a newer Maven
    Basic grant -- the state where newest-created and strongest disagree."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()
        cls.admin = User.objects.create_user(
            email="admin@test.com", password="x",
            is_staff=True, is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="override-1933")
        cls.stacked = User.objects.create_user(email=STACKED_EMAIL, password="x")
        set_membership(cls.stacked, tier=cls.free_tier)
        cls.staff_premium = TierOverride.objects.create(
            user=cls.stacked,
            original_tier=cls.free_tier,
            override_tier=cls.premium_tier,
            expires_at=now + timedelta(days=20),
            granted_by=cls.admin,
        )
        TierOverride.objects.filter(pk=cls.staff_premium.pk).update(
            created_at=now - timedelta(days=10),
        )
        cls.maven_basic = TierOverride.objects.create(
            user=cls.stacked,
            original_tier=cls.free_tier,
            override_tier=cls.basic_tier,
            expires_at=now + timedelta(days=300),
            source="maven:ai-buildcamp-3",
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}


class StrongestOverrideApiTest(StackedOverrideFixtureMixin, TestCase):
    def test_single_user_reports_strongest_override(self):
        response = self.client.get(
            reverse("api_user_detail", kwargs={"email": STACKED_EMAIL}),
            **self._auth(),
        )

        body = response.json()
        self.assertEqual(
            body["tier"], {"slug": "premium", "level": 30, "source": "override"},
        )
        self.assertEqual(body["tier_override"]["tier_slug"], "premium")
        self.assertEqual(body["tier_override"]["granted_by"], "admin@test.com")
        self.assertTrue(body["tier_override_active"])

    def test_list_row_reports_strongest_override(self):
        response = self.client.get("/api/users", {"q": "stacked"}, **self._auth())

        rows = response.json()["users"]
        self.assertEqual(
            [(row["email"], row["tier"]["slug"], row["tier"]["source"]) for row in rows],
            [(STACKED_EMAIL, "premium", "override")],
        )

    def test_activity_user_object_reports_strongest_override(self):
        response = self.client.get(
            reverse("api_user_activity", kwargs={"email": STACKED_EMAIL}),
            **self._auth(),
        )

        self.assertEqual(response.json()["user"]["tier"]["slug"], "premium")

    def test_crm_export_member_reports_strongest_override(self):
        CRMRecord.objects.create(user=self.stacked, created_by=self.admin)

        response = self.client.get("/api/crm/export", **self._auth())

        members = {
            member["email"]: member for member in response.json()["members"]
        }
        self.assertEqual(members[STACKED_EMAIL]["tier"]["slug"], "premium")
        self.assertEqual(
            members[STACKED_EMAIL]["tier_override"]["tier_slug"], "premium",
        )

    def test_api_pick_agrees_with_access_and_studio(self):
        payload = serialize_user_state(self.stacked)
        access_override = get_active_override(self.stacked)

        self.assertEqual(access_override.pk, self.staff_premium.pk)
        self.assertEqual(
            payload["tier_override"]["tier_slug"],
            access_override.override_tier.slug,
        )
        self.assertEqual(payload["tier"]["level"], get_user_level(self.stacked))

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("studio_user_detail", kwargs={"user_id": self.stacked.pk}),
        )
        self.assertEqual(response.context["tier_slug"], payload["tier"]["slug"])
        self.assertEqual(response.context["tier_source"], "override")

    def test_revoked_and_expired_overrides_are_ignored(self):
        member = User.objects.create_user(email="ignored@test.com", password="x")
        set_membership(member, tier=self.free_tier)
        now = timezone.now()
        TierOverride.objects.create(
            user=member, override_tier=self.premium_tier,
            expires_at=now + timedelta(days=30), is_active=False,
        )
        TierOverride.objects.create(
            user=member, override_tier=self.premium_tier,
            expires_at=now - timedelta(days=1),
        )
        TierOverride.objects.create(
            user=member, override_tier=self.basic_tier,
            expires_at=now + timedelta(days=30),
        )

        payload = serialize_user_state(member)

        self.assertEqual(payload["tier"]["slug"], "basic")
        self.assertEqual(payload["tier_override"]["tier_slug"], "basic")

    def test_same_tier_tie_breaks_on_latest_expiry(self):
        member = User.objects.create_user(email="tie@test.com", password="x")
        later_expiry = timezone.now() + timedelta(days=90)
        TierOverride.objects.create(
            user=member, override_tier=self.main_tier, expires_at=later_expiry,
        )
        # Created last, so the old newest-created pick would have chosen it.
        TierOverride.objects.create(
            user=member, override_tier=self.main_tier,
            expires_at=timezone.now() + timedelta(days=10),
        )

        payload = serialize_user_state(member)

        self.assertEqual(
            payload["tier_override"]["expires_at"], later_expiry.isoformat(),
        )

    def test_override_at_or_below_paid_tier_keeps_subscription_source(self):
        member = User.objects.create_user(email="paid-comp@test.com", password="x")
        set_membership(member, tier=self.main_tier, subscription_id="sub_1")
        TierOverride.objects.create(
            user=member, override_tier=self.basic_tier,
            expires_at=timezone.now() + timedelta(days=30),
        )

        payload = serialize_user_state(member)

        self.assertEqual(
            payload["tier"], {"slug": "main", "level": 20, "source": "subscription"},
        )
        self.assertTrue(payload["tier_override_active"])
        self.assertEqual(payload["tier_override"]["tier_slug"], "basic")

    def test_explicit_none_override_issues_no_override_query(self):
        with CaptureQueriesContext(connection) as ctx:
            payload = serialize_user_state(
                self.stacked, compact=True, active_override=None,
            )

        self.assertEqual(_override_queries(ctx), [])
        self.assertFalse(payload["tier_override_active"])
        self.assertEqual(payload["tier"]["source"], "free")


class BatchedOverrideQueryTest(StackedOverrideFixtureMixin, TestCase):
    """List surfaces resolve overrides once per page, not once per row."""

    @classmethod
    def _add_overridden_members(cls, prefix, count):
        for index in range(count):
            member = User.objects.create_user(
                email=f"{prefix}-{index}@test.com", password="x",
            )
            TierOverride.objects.create(
                user=member, override_tier=cls.main_tier,
                expires_at=timezone.now() + timedelta(days=30),
                granted_by=cls.admin,
            )
            CRMRecord.objects.create(user=member, created_by=cls.admin)

    def _capture(self, url, params):
        # Warm one-off caches (config stamp, redirects) before measuring.
        self.client.get(url, params, **self._auth())
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url, params, **self._auth())
        return ctx, response.json()

    def test_user_list_issues_one_override_query_for_two_or_six_rows(self):
        self._add_overridden_members("batch", 2)
        two_ctx, two_body = self._capture("/api/users", {"q": "batch-"})
        self._add_overridden_members("batch-more", 4)
        six_ctx, six_body = self._capture("/api/users", {"q": "batch-"})

        self.assertEqual(two_body["count"], 2)
        self.assertEqual(six_body["count"], 6)
        self.assertEqual(len(_override_queries(two_ctx)), 1)
        self.assertEqual(len(_override_queries(six_ctx)), 1)
        # No per-row ``granted_by`` (or any other) query either.
        self.assertEqual(
            len(six_ctx.captured_queries), len(two_ctx.captured_queries),
        )

    def test_crm_export_issues_one_override_query_per_page(self):
        self._add_overridden_members("crm", 2)
        small_ctx, small_body = self._capture("/api/crm/export", {})
        self._add_overridden_members("crm-more", 3)
        large_ctx, large_body = self._capture("/api/crm/export", {})

        self.assertEqual(small_body["count"], 2)
        self.assertEqual(large_body["count"], 5)
        self.assertEqual(len(_override_queries(small_ctx)), 1)
        self.assertEqual(len(_override_queries(large_ctx)), 1)
        granted_by = {
            member["tier_override"]["granted_by"]
            for member in large_body["members"]
        }
        self.assertEqual(granted_by, {"admin@test.com"})
