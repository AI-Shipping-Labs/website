"""Tests for ``POST /api/tier-overrides`` (issue #833).

Covers every "Django API test scenario" from the issue: the 10-year main grant,
user creation for the cohort case, deactivation of a prior override, idempotent
re-grant, staff auth (401 unauth + non-staff), wrong method (405), batch with a
malformed row, per-grant audit rows, invalid-tier rejection before any write,
and over-cap batch rejection.
"""

import json

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride, Token
from community.models import CommunityAuditLog
from payments.models import Tier
from studio.services.contacts_import import OVERRIDE_DURATION

User = get_user_model()


class TierOverridesGrantTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Tiers are seeded by payments data migrations; resolve them defensively.
        cls.free, _ = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0}
        )
        cls.basic, _ = Tier.objects.get_or_create(
            slug="basic", defaults={"name": "Basic", "level": 10}
        )
        cls.main, _ = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20}
        )
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="cohort-bot")

    def _post(self, payload, *, token=None, raw_body=None):
        """POST to the endpoint.

        ``token`` semantics: ``None`` -> use the default staff token; ``False``
        -> send no ``Authorization`` header at all; a ``Token`` instance ->
        authenticate with that token.
        """
        body = raw_body if raw_body is not None else json.dumps(payload)
        headers = {}
        if token is False:
            pass  # no Authorization header
        else:
            key = token.key if token is not None else self.token.key
            headers["HTTP_AUTHORIZATION"] = f"Token {key}"
        return self.client.post(
            "/api/tier-overrides",
            data=body,
            content_type="application/json",
            **headers,
        )

    # ---- Scenario: 10-year main override for a non-paying member ----------

    def test_grants_ten_year_main_override(self):
        member = User.objects.create_user(email="vinayak@example.com", password=None)
        self.assertEqual(member.tier.slug, "free")

        before = timezone.now()
        response = self._post({"emails": ["vinayak@example.com"]})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tier"], "main")
        self.assertEqual(body["granted"], 1)
        self.assertEqual(body["results"][0]["status"], "granted")

        overrides = TierOverride.objects.filter(user=member, is_active=True)
        self.assertEqual(overrides.count(), 1)
        override = overrides.get()
        self.assertEqual(override.override_tier.slug, "main")
        self.assertEqual(override.granted_by, self.admin)
        self.assertEqual(override.original_tier, self.free)
        # Expiry is ~10 years out (OVERRIDE_DURATION): assert > 9 years ahead.
        self.assertGreater(override.expires_at, before + relativedelta(years=9))

    # ---- Scenario: granting creates a user that does not exist (cohort) ---

    def test_grant_creates_missing_user(self):
        self.assertFalse(
            User.objects.filter(email="newcohort@example.com").exists()
        )
        response = self._post({"emails": ["newcohort@example.com"]})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["results"][0]["created_user"])

        user = User.objects.get(email="newcohort@example.com")
        self.assertEqual(user.signup_source, "imported")
        self.assertFalse(user.email_verified)
        self.assertTrue(
            TierOverride.objects.filter(
                user=user, override_tier=self.main, is_active=True
            ).exists()
        )

    # ---- Scenario: a prior active override is deactivated, not stacked ----

    def test_prior_override_deactivated(self):
        member = User.objects.create_user(email="member@example.com", password=None)
        prior = TierOverride.objects.create(
            user=member,
            original_tier=self.free,
            override_tier=self.basic,
            expires_at=timezone.now() + OVERRIDE_DURATION,
            granted_by=self.admin,
            is_active=True,
        )

        response = self._post({"emails": ["member@example.com"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["status"], "granted")

        prior.refresh_from_db()
        self.assertFalse(prior.is_active)

        active = TierOverride.objects.filter(user=member, is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.get().override_tier.slug, "main")

    # ---- Scenario: idempotent re-grant does not stack rows ---------------

    def test_idempotent_regrant(self):
        member = User.objects.create_user(email="member@example.com", password=None)
        existing = TierOverride.objects.create(
            user=member,
            original_tier=self.free,
            override_tier=self.main,
            expires_at=timezone.now() + OVERRIDE_DURATION,
            granted_by=self.admin,
            is_active=True,
        )
        rows_before = TierOverride.objects.filter(user=member).count()
        audit_before = CommunityAuditLog.objects.filter(user=member).count()

        response = self._post({"emails": ["member@example.com"]})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["results"][0]["status"], "skipped_idempotent")
        # The skipped row reports the existing override's expiry (#834).
        self.assertEqual(
            body["results"][0]["expires_at"], existing.expires_at.isoformat()
        )
        self.assertEqual(body["skipped"], 1)
        self.assertEqual(body["granted"], 0)

        self.assertEqual(
            TierOverride.objects.filter(user=member).count(), rows_before
        )
        # No new audit row for an idempotent skip.
        self.assertEqual(
            CommunityAuditLog.objects.filter(user=member).count(), audit_before
        )

    def test_granted_row_has_no_expires_at_key(self):
        """``expires_at`` is scoped to skipped rows; granted rows omit it (#834)."""
        response = self._post({"emails": ["freshgrant@example.com"]})

        self.assertEqual(response.status_code, 200)
        row = response.json()["results"][0]
        self.assertEqual(row["status"], "granted")
        self.assertNotIn("expires_at", row)

    # ---- Scenario: staff auth is required (401 unauth + non-staff) -------

    def test_missing_auth_header_rejected(self):
        response = self._post({"emails": ["x@y.com"]}, token=False)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(TierOverride.objects.count(), 0)
        self.assertFalse(User.objects.filter(email="x@y.com").exists())

    def test_non_staff_token_rejected(self):
        member = User.objects.create_user(email="plain@test.com", password="pw")
        # Construct directly to bypass the manager's staff-only validator
        # (models a legacy-demoted user), matching test_token_auth.py.
        non_staff = Token(key="non-staff-key-833", user=member, name="legacy")
        Token.objects.bulk_create([non_staff])

        response = self._post({"emails": ["x@y.com"]}, token=non_staff)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(TierOverride.objects.count(), 0)
        self.assertFalse(User.objects.filter(email="x@y.com").exists())

    # ---- Scenario: wrong method is rejected ------------------------------

    def test_get_method_rejected(self):
        response = self.client.get(
            "/api/tier-overrides",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 405)

    # ---- Scenario: batch grants many and reports per-email results -------

    def test_batch_with_malformed_row(self):
        User.objects.create_user(email="b@x.com", password=None)
        response = self._post(
            {"emails": ["a@x.com", "b@x.com", "bad-email"]}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["granted"], 2)
        self.assertEqual(body["malformed"], 1)

        statuses = {r["email"]: r["status"] for r in body["results"]}
        self.assertEqual(statuses["a@x.com"], "granted")
        self.assertEqual(statuses["b@x.com"], "granted")
        self.assertEqual(statuses["bad-email"], "malformed")

        # The malformed row did not abort the batch: both valid emails got
        # active main overrides.
        for email in ("a@x.com", "b@x.com"):
            user = User.objects.get(email=email)
            self.assertTrue(
                TierOverride.objects.filter(
                    user=user, override_tier=self.main, is_active=True
                ).exists()
            )

    # ---- Scenario: audit row is written for each granted email -----------

    def test_audit_row_written_per_grant(self):
        response = self._post({"emails": ["audited@example.com"]})
        self.assertEqual(response.status_code, 200)

        user = User.objects.get(email="audited@example.com")
        logs = CommunityAuditLog.objects.filter(
            user=user, action="api_tier_override"
        )
        self.assertEqual(logs.count(), 1)
        details = logs.get().details
        self.assertIn("actor_token=cohort-bot", details)
        self.assertIn("main", details)

    # ---- Scenario: invalid tier is rejected before any write -------------

    def test_unknown_tier_rejected(self):
        response = self._post({"emails": ["x@y.com"], "tier": "nope"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "unknown_tier")
        self.assertEqual(TierOverride.objects.count(), 0)
        self.assertFalse(User.objects.filter(email="x@y.com").exists())

    def test_free_tier_rejected(self):
        response = self._post({"emails": ["x@y.com"], "tier": "free"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_tier")
        self.assertEqual(TierOverride.objects.count(), 0)
        self.assertFalse(User.objects.filter(email="x@y.com").exists())

    # ---- Scenario: explicit valid higher tier is honored -----------------

    def test_explicit_higher_tier_granted(self):
        response = self._post({"emails": ["x@y.com"], "tier": "basic"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tier"], "basic")
        user = User.objects.get(email="x@y.com")
        self.assertTrue(
            TierOverride.objects.filter(
                user=user, override_tier=self.basic, is_active=True
            ).exists()
        )

    # ---- Scenario: duplicate emails in one request collapse to one grant -

    def test_duplicate_emails_in_request_grant_once(self):
        # Same address twice -- once verbatim, once in differing case/whitespace
        # -- must collapse to a single grant, audit row, and results entry. The
        # importer already collapses to one TierOverride; the endpoint must keep
        # its counts, audit rows, and results array consistent with that.
        response = self._post(
            {"emails": ["dupe@example.com", " DUPE@example.com "]}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["granted"], 1)
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["status"], "granted")
        # First-seen raw value is the one echoed back.
        self.assertEqual(body["results"][0]["email"], "dupe@example.com")

        user = User.objects.get(email="dupe@example.com")
        self.assertEqual(
            TierOverride.objects.filter(
                user=user, override_tier=self.main, is_active=True
            ).count(),
            1,
        )
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=user, action="api_tier_override"
            ).count(),
            1,
        )

    # ---- Scenario: over-cap batch is rejected ----------------------------

    def test_over_cap_batch_rejected(self):
        emails = [f"user{i}@example.com" for i in range(1001)]
        response = self._post({"emails": emails})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "batch_too_large")
        self.assertEqual(TierOverride.objects.count(), 0)
        self.assertEqual(
            User.objects.filter(email__endswith="@example.com").count(), 0
        )
