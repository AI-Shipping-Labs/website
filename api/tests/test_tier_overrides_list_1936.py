"""``GET /api/tier-overrides`` and ``ineffective_only`` revoke (issue #1936).

A grant raises access only when its tier is above the member's current
stored tier. The list endpoint reports that per row; the revoke flag limits
a revoke to the grants that raise nothing.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Token
from payments.models import Tier, TierOverride
from tests.fixtures import set_membership

User = get_user_model()

LIST_URL = "/api/tier-overrides"
REVOKE_URL = "/api/tier-overrides/revoke"

ROW_KEYS = {
    "id", "email", "tier", "stored_tier", "tier_at_grant", "raises_access",
    "source", "created_at", "expires_at", "granted_by",
}


def _grant(user, tier, *, original_tier, days, granted_by=None, source=""):
    return TierOverride.objects.create(
        user=user,
        original_tier=original_tier,
        override_tier=tier,
        expires_at=timezone.now() + timedelta(days=days),
        granted_by=granted_by,
        source=source,
        is_active=True,
    )


class TierOverrideApiBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.basic = Tier.objects.get(slug="basic")
        cls.main = Tier.objects.get(slug="main")
        cls.premium = Tier.objects.get(slug="premium")
        cls.admin = User.objects.create_user(
            email="admin-1936@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="cleanup-bot")

    def _get(self, params=None, *, token=None):
        headers = {}
        if token is not False:
            key = token.key if token is not None else self.token.key
            headers["HTTP_AUTHORIZATION"] = f"Token {key}"
        return self.client.get(LIST_URL, params or {}, **headers)

    def _revoke(self, payload):
        return self.client.post(
            REVOKE_URL,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )


class FourMemberFixture(TierOverrideApiBase):
    """A stored Main + Basic grant, B Free + Premium and Basic, C Basic +
    Basic, D granted Main while Free but now stored Premium."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a = User.objects.create_user(email="a-1936@test.com")
        set_membership(cls.a, tier=cls.main)
        cls.b = User.objects.create_user(email="b-1936@test.com")
        set_membership(cls.b, tier=cls.free)
        cls.c = User.objects.create_user(email="c-1936@test.com")
        set_membership(cls.c, tier=cls.basic)
        cls.d = User.objects.create_user(email="d-1936@test.com")
        set_membership(cls.d, tier=cls.premium)

        cls.a_basic = _grant(
            cls.a, cls.basic, original_tier=cls.main, days=5,
            granted_by=cls.admin,
        )
        cls.b_premium = _grant(
            cls.b, cls.premium, original_tier=cls.free, days=10,
            source="maven:abc",
        )
        cls.b_basic = _grant(cls.b, cls.basic, original_tier=cls.free, days=300)
        cls.c_basic = _grant(cls.c, cls.basic, original_tier=cls.basic, days=20)
        cls.d_main = _grant(cls.d, cls.main, original_tier=cls.free, days=30)


class ListTierOverridesTest(FourMemberFixture):
    def test_all_rows_with_counts_and_status_per_row(self):
        body = self._get().json()

        self.assertEqual(body["total"], 5)
        self.assertEqual(body["effective_count"], 2)
        self.assertEqual(body["ineffective_count"], 3)
        self.assertEqual(body["count"], 5)
        rows = body["overrides"]
        self.assertEqual(
            [r["id"] for r in rows],
            [
                self.a_basic.pk, self.b_premium.pk, self.c_basic.pk,
                self.d_main.pk, self.b_basic.pk,
            ],
        )
        for row in rows:
            self.assertEqual(set(row), ROW_KEYS)
        by_id = {r["id"]: r for r in rows}

        a_row = by_id[self.a_basic.pk]
        self.assertEqual(a_row["email"], "a-1936@test.com")
        self.assertEqual(a_row["tier"], "basic")
        self.assertEqual(a_row["stored_tier"], "main")
        self.assertEqual(a_row["tier_at_grant"], "main")
        self.assertIs(a_row["raises_access"], False)
        self.assertEqual(a_row["source"], "manual")
        self.assertEqual(a_row["granted_by"], "admin-1936@test.com")
        self.assertEqual(
            a_row["expires_at"], self.a_basic.expires_at.isoformat(),
        )
        self.assertEqual(
            a_row["created_at"], self.a_basic.created_at.isoformat(),
        )

        b_row = by_id[self.b_premium.pk]
        self.assertIs(b_row["raises_access"], True)
        self.assertEqual(b_row["stored_tier"], "free")
        self.assertEqual(b_row["source"], "maven:abc")
        self.assertIsNone(b_row["granted_by"])
        self.assertIs(by_id[self.b_basic.pk]["raises_access"], True)
        self.assertIs(by_id[self.c_basic.pk]["raises_access"], False)

        d_row = by_id[self.d_main.pk]
        self.assertIs(d_row["raises_access"], False)
        self.assertEqual(d_row["stored_tier"], "premium")
        self.assertEqual(d_row["tier_at_grant"], "free")

    def test_ineffective_status_returns_only_leftover_grants(self):
        body = self._get({"status": "ineffective"}).json()

        self.assertEqual(
            [r["id"] for r in body["overrides"]],
            [self.a_basic.pk, self.c_basic.pk, self.d_main.pk],
        )
        self.assertEqual(body["count"], 3)
        # Counts stay the unfiltered totals.
        self.assertEqual(
            (body["total"], body["effective_count"], body["ineffective_count"]),
            (5, 2, 3),
        )

    def test_effective_status_returns_only_raising_grants(self):
        body = self._get({"status": "effective"}).json()
        self.assertEqual(
            [r["id"] for r in body["overrides"]],
            [self.b_premium.pk, self.b_basic.pk],
        )

    def test_unknown_status_is_rejected(self):
        response = self._get({"status": "nope"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_status")

    def test_limit_caps_rows_but_not_counts(self):
        body = self._get({"limit": "2"}).json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(
            [r["id"] for r in body["overrides"]],
            [self.a_basic.pk, self.b_premium.pk],
        )
        self.assertEqual(body["total"], 5)

    def test_invalid_limit_is_rejected(self):
        response = self._get({"limit": "0"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_inactive_and_expired_grants_are_excluded(self):
        TierOverride.objects.create(
            user=self.b, original_tier=self.free, override_tier=self.main,
            expires_at=timezone.now() - timedelta(minutes=1), is_active=True,
        )
        TierOverride.objects.create(
            user=self.b, original_tier=self.free, override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=9), is_active=False,
        )
        self.assertEqual(self._get().json()["total"], 5)


class ListTierOverridesAuthTest(TierOverrideApiBase):
    def test_missing_token_is_unauthorized(self):
        self.assertEqual(self._get(token=False).status_code, 401)

    def test_non_staff_token_gets_the_same_rejection_as_grant(self):
        member = User.objects.create_user(email="plain-1936@test.com")
        non_staff = Token(key="non-staff-key-1936", user=member, name="legacy")
        Token.objects.bulk_create([non_staff])

        list_response = self._get(token=non_staff)
        grant_response = self.client.post(
            LIST_URL, data=json.dumps({"emails": ["x@y.com"]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {non_staff.key}",
        )
        self.assertEqual(list_response.status_code, 401)
        self.assertEqual(list_response.status_code, grant_response.status_code)
        self.assertEqual(list_response.json(), grant_response.json())


class ListTierOverridesQueryBudgetTest(TierOverrideApiBase):
    def _seed(self, count, offset):
        for index in range(count):
            member = User.objects.create_user(
                email=f"budget-{offset + index}-1936@test.com",
            )
            set_membership(member, tier=self.main if index % 2 else self.free)
            _grant(
                member, self.basic, original_tier=self.free,
                days=offset + index + 1, granted_by=self.admin,
            )

    def _queries(self, params):
        with CaptureQueriesContext(connection) as ctx:
            response = self._get(params)
        self.assertTrue(response.json()["overrides"])
        return len(ctx.captured_queries)

    @patch("integrations.middleware.get_active_redirects", return_value={})
    @patch("integrations.config._read_stamp", return_value=None)
    def test_query_count_is_constant_for_two_and_eight_overrides(self, *_caches):
        self._seed(2, 0)
        # Pin ambient cache reads: redirect expiry (300s) or a config stamp
        # change must not add a query to only one measurement (see #1103).
        self._get()  # Warm the config cache before measuring.
        small = [self._queries({}), self._queries({"status": "ineffective"})]
        self._seed(6, 2)
        large = [self._queries({}), self._queries({"status": "ineffective"})]
        self.assertEqual(small, large)
