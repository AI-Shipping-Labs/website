"""Tests for ``POST /api/contacts/import`` (issue #431)."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import TierOverride, Token
from payments.models import Tier

User = get_user_model()


class ContactsImportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # The free tier comes from data migrations; ensure main also exists
        # (used by default_tier tests).
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20},
        )
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def _post(self, payload, *, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload)
        return self.client.post(
            "/api/contacts/import",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    def test_import_creates_new_users(self):
        response = self._post({
            "contacts": [
                {"email": "alice@test.com"},
                {"email": "bob@test.com"},
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 2)
        self.assertTrue(User.objects.filter(email="alice@test.com").exists())
        self.assertTrue(User.objects.filter(email="bob@test.com").exists())

    def test_import_updates_existing_users_with_per_row_tags(self):
        existing = User.objects.create_user(
            email="existing@test.com",
            password=None,
        )
        existing.tags = ["existing"]
        existing.save(update_fields=["tags"])

        response = self._post({
            "contacts": [
                {"email": "existing@test.com", "tags": ["new-tag"]},
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)

        existing.refresh_from_db()
        # MERGE, not REPLACE: the original tag is still there.
        self.assertEqual(existing.tags, ["existing", "new-tag"])

    def test_import_with_default_tag_applies_to_every_row(self):
        response = self._post({
            "contacts": [
                {"email": "row1@test.com"},
                {"email": "row2@test.com"},
                {"email": "row3@test.com"},
            ],
            "default_tag": "campaign-q1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 3)

        for email in ("row1@test.com", "row2@test.com", "row3@test.com"):
            user = User.objects.get(email=email)
            self.assertIn("campaign-q1", user.tags)

    def test_import_with_default_tier_requires_stripe_customer_id(self):
        response = self._post({
            "contacts": [{"email": "tiered@test.com"}],
            "default_tier": "main",
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="tiered@test.com")
        self.assertFalse(
            TierOverride.objects.filter(user=user, is_active=True).exists()
        )
        self.assertEqual(user.tier.slug, "free")
        warnings = response.json()["warnings"]
        self.assertEqual(
            warnings[0]["reason"],
            "stripe_customer_id_required_for_tier_assignment",
        )

    def test_import_with_matching_stripe_tier_sets_direct_tier_not_override(self):
        main = Tier.objects.get(slug="main")

        def sync_from_stripe(user, **kwargs):
            user.tier = main
            user.subscription_id = "sub_MAIN"
            user.save(update_fields=["tier", "subscription_id"])

            class Record:
                status = "changed"
                new_tier_slug = "main"
                message = "changed: free -> main"

            return Record()

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ):
            response = self._post({
                "contacts": [{
                    "email": "tiered-stripe@test.com",
                    "stripe_customer_id": "cus_MAIN",
                }],
                "default_tier": "main",
            })

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="tiered-stripe@test.com")
        self.assertEqual(user.tier.slug, "main")
        self.assertEqual(user.subscription_id, "sub_MAIN")
        self.assertFalse(
            TierOverride.objects.filter(user=user, is_active=True).exists()
        )
        self.assertEqual(response.json()["warnings"], [])

    def test_import_refuses_tier_assignment_when_stripe_tier_differs(self):
        main = Tier.objects.get(slug="main")

        def sync_from_stripe(user, **kwargs):
            user.tier = main
            user.save(update_fields=["tier"])

            class Record:
                status = "changed"
                new_tier_slug = "main"
                message = "changed: free -> main"

            return Record()

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ):
            response = self._post({
                "contacts": [{
                    "email": "tier-mismatch@test.com",
                    "stripe_customer_id": "cus_MAIN",
                    "tier": "premium",
                }],
            })

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="tier-mismatch@test.com")
        self.assertEqual(user.tier.slug, "main")
        self.assertFalse(
            TierOverride.objects.filter(user=user, is_active=True).exists()
        )
        warnings = response.json()["warnings"]
        self.assertEqual(warnings[0]["reason"], "stripe_tier_mismatch")

    def test_import_malformed_email_is_warned_not_raised(self):
        response = self._post({
            "contacts": [
                {"email": "not-an-email"},
                {"email": "good@example.com"},
            ],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["malformed"], 1)
        self.assertEqual(body["created"], 1)
        self.assertTrue(
            any("not-an-email" == w["value"] for w in body["warnings"]),
            f"Expected a malformed-email warning in {body['warnings']}",
        )

    def test_import_rolls_back_on_internal_error(self):
        """A failure mid-batch must not leave half-imported users behind."""
        users_before = User.objects.count()

        # Patch _apply_tag (used inside the per-row loop) to raise after the
        # decorator has already created the first user. The whole batch is
        # wrapped in transaction.atomic, so the first row's INSERT must roll
        # back together with the rest of the batch.
        with mock.patch(
            "studio.services.contacts_import._apply_tag",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self._post({
                    "contacts": [
                        {"email": "first@test.com"},
                        {"email": "second@test.com"},
                    ],
                })

        # Atomic block rolled back: no new users.
        self.assertEqual(User.objects.count(), users_before)

    def test_import_unknown_default_tier_returns_400(self):
        users_before = User.objects.count()
        response = self._post({
            "contacts": [{"email": "x@test.com"}],
            "default_tier": "nonexistent",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "unknown_tier")
        # The error fires before the import runs; no users created.
        self.assertEqual(User.objects.count(), users_before)

    def test_import_missing_contacts_key_returns_400(self):
        response = self._post({})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "missing_contacts")

    def test_import_invalid_json_returns_400(self):
        response = self._post(None, raw_body="not-json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid JSON"})

    def test_import_rejects_get(self):
        response = self.client.get(
            "/api/contacts/import",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "Method not allowed"})


class ContactsImportAuthTest(TestCase):
    """Auth gates on ``POST /api/contacts/import`` (issue #636 AC1)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="test")

    def test_import_rejects_unauthenticated_request(self):
        """Issue #636: no Authorization header -> 401 and no users written."""
        users_before = User.objects.count()
        response = self.client.post(
            "/api/contacts/import",
            data=json.dumps({"contacts": [{"email": "anon@test.com"}]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), users_before)
        self.assertFalse(
            User.objects.filter(email="anon@test.com").exists()
        )

    def test_import_rejects_non_staff_token(self):
        """Issue #636: token whose owner lost is_staff -> 401.

        ``Token.clean()`` blocks creating a token for a non-staff user, so we
        create the token while staff and then flip ``is_staff`` to False --
        which is exactly the post-revocation drift the decorator must catch.
        """
        self.staff.is_staff = False
        self.staff.save(update_fields=["is_staff"])

        users_before = User.objects.count()
        response = self.client.post(
            "/api/contacts/import",
            data=json.dumps({"contacts": [{"email": "demoted@test.com"}]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), users_before)


class ContactsImportStripeValidatedTierTest(TestCase):
    """Stripe-validated tier assignment branches (issue #636 ACs 2-13)."""

    @classmethod
    def setUpTestData(cls):
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20},
        )
        cls.premium_tier, _ = Tier.objects.get_or_create(
            slug="premium", defaults={"name": "Premium", "level": 30},
        )
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def _post(self, payload):
        return self.client.post(
            "/api/contacts/import",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    # ------------------------------------------------------------------
    # AC3 (matching tier via default_tier writes direct tier).
    # ------------------------------------------------------------------

    def test_import_default_tier_match_writes_direct_tier(self):
        """default_tier (not per-row tier) + matching Stripe -> direct tier."""
        main = Tier.objects.get(slug="main")

        def sync_from_stripe(user, **kwargs):
            user.tier = main
            user.save(update_fields=["tier"])

            class Record:
                status = "changed"
                new_tier_slug = "main"
                message = "changed: free -> main"

            return Record()

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ):
            response = self._post({
                "contacts": [{
                    "email": "default-match@test.com",
                    "stripe_customer_id": "cus_DEFAULT",
                }],
                "default_tier": "main",
            })

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="default-match@test.com")
        self.assertEqual(user.tier.slug, "main")
        self.assertFalse(
            TierOverride.objects.filter(user=user, is_active=True).exists()
        )
        self.assertEqual(response.json()["warnings"], [])

    # ------------------------------------------------------------------
    # AC4 (warning value shape on mismatch).
    # ------------------------------------------------------------------

    def test_import_tier_mismatch_warning_value_includes_both_slugs(self):
        """Mismatch warning value spells out requested and Stripe slugs."""
        main = Tier.objects.get(slug="main")

        def sync_from_stripe(user, **kwargs):
            user.tier = main
            user.save(update_fields=["tier"])

            class Record:
                status = "changed"
                new_tier_slug = "main"
                message = "changed: free -> main"

            return Record()

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ):
            response = self._post({
                "contacts": [{
                    "email": "mismatch-value@test.com",
                    "stripe_customer_id": "cus_MAIN",
                    "tier": "premium",
                }],
            })

        self.assertEqual(response.status_code, 200)
        warnings = response.json()["warnings"]
        mismatch = next(
            (w for w in warnings if w["reason"] == "stripe_tier_mismatch"),
            None,
        )
        self.assertIsNotNone(mismatch)
        self.assertIn("requested=premium", mismatch["value"])
        self.assertIn("stripe=main", mismatch["value"])

    # ------------------------------------------------------------------
    # AC5 (Stripe lookup warning short-circuits).
    # ------------------------------------------------------------------

    def test_import_refuses_tier_when_stripe_returns_warning(self):
        """Stripe lookup returning status='warning' -> tier unchanged + warning."""

        class Record:
            status = "warning"
            new_tier_slug = ""
            message = (
                "warning: active Stripe subscription sub_X uses unknown price "
                "price_abc; tier unchanged"
            )

        # User already exists with a customer id so the validation branch
        # reaches the dry-run lookup (no customer-id sync to short-circuit on).
        user = User.objects.create_user(
            email="unknown-price@test.com",
            password=None,
            stripe_customer_id="cus_WARN",
        )

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            return_value=Record(),
        ):
            response = self._post({
                "contacts": [{
                    "email": "unknown-price@test.com",
                    "tier": "main",
                }],
            })

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertFalse(
            TierOverride.objects.filter(user=user, is_active=True).exists()
        )
        warnings = response.json()["warnings"]
        self.assertTrue(
            any(w["reason"] == "stripe_tier_validation_failed" for w in warnings),
            f"Expected stripe_tier_validation_failed warning in {warnings}",
        )

    def test_import_refuses_tier_when_stripe_has_no_active_subscription(self):
        """No active Stripe subscription -> validation failure, tier unchanged."""

        class Record:
            status = "warning"
            new_tier_slug = ""
            message = (
                "warning: no active Stripe subscription for paid user "
                "no-sub@test.com; leaving tier free unchanged"
            )

        user = User.objects.create_user(
            email="no-sub@test.com",
            password=None,
            stripe_customer_id="cus_NOSUB",
        )

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            return_value=Record(),
        ):
            response = self._post({
                "contacts": [{
                    "email": "no-sub@test.com",
                    "tier": "main",
                }],
            })

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        warnings = response.json()["warnings"]
        self.assertTrue(
            any(w["reason"] == "stripe_tier_validation_failed" for w in warnings),
        )

    # ------------------------------------------------------------------
    # AC6 (never creates a TierOverride row, regardless of branch).
    # ------------------------------------------------------------------

    def test_import_never_creates_tier_override_on_match(self):
        main = Tier.objects.get(slug="main")

        def sync_from_stripe(user, **kwargs):
            user.tier = main
            user.save(update_fields=["tier"])

            class Record:
                status = "changed"
                new_tier_slug = "main"
                message = "changed"

            return Record()

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ):
            response = self._post({
                "contacts": [{
                    "email": "no-override-match@test.com",
                    "stripe_customer_id": "cus_MATCH",
                    "tier": "main",
                }],
            })

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="no-override-match@test.com")
        self.assertEqual(
            TierOverride.objects.filter(user=user, is_active=True).count(),
            0,
        )

    def test_import_never_creates_tier_override_on_mismatch(self):
        main = Tier.objects.get(slug="main")

        def sync_from_stripe(user, **kwargs):
            user.tier = main
            user.save(update_fields=["tier"])

            class Record:
                status = "changed"
                new_tier_slug = "main"
                message = "changed"

            return Record()

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ):
            response = self._post({
                "contacts": [{
                    "email": "no-override-mismatch@test.com",
                    "stripe_customer_id": "cus_MAIN",
                    "tier": "premium",
                }],
            })

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="no-override-mismatch@test.com")
        self.assertEqual(
            TierOverride.objects.filter(user=user, is_active=True).count(),
            0,
        )

    def test_import_never_creates_tier_override_when_stripe_missing(self):
        """No stripe_customer_id + paid tier request -> no override row."""
        response = self._post({
            "contacts": [{
                "email": "no-override-missing@test.com",
                "tier": "main",
            }],
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="no-override-missing@test.com")
        self.assertEqual(
            TierOverride.objects.filter(user=user, is_active=True).count(),
            0,
        )

    # ------------------------------------------------------------------
    # AC8 (idempotency of a matching payload).
    # ------------------------------------------------------------------

    def test_import_matching_payload_is_idempotent(self):
        """Second identical import: no warnings, no new overrides, no commit re-run.

        After the first call, the user is already on ``main`` and the
        customer_id is set. The second call must:

        - leave the user on ``main``,
        - emit no warnings,
        - not create any ``TierOverride`` rows,
        - and not re-issue a non-dry-run backfill (validation path's dry-run
          finds nothing to commit and short-circuits).
        """
        main = Tier.objects.get(slug="main")

        # Stateful mock: the very first call (customer-id sync) mutates the
        # user to match Stripe. Subsequent calls return "skipped"/"dry_run"
        # because the tier already matches.
        def sync_from_stripe(user, **kwargs):
            dry_run = kwargs.get("dry_run", False)
            already_on_main = (user.tier_id == main.pk)

            if not already_on_main and not dry_run:
                user.tier = main
                user.subscription_id = "sub_MAIN"
                user.save(update_fields=["tier", "subscription_id"])

                class ChangedRecord:
                    status = "changed"
                    new_tier_slug = "main"
                    message = "changed: free -> main"

                return ChangedRecord()

            # Already on the right tier: dry_run and non-dry-run both report
            # "skipped" (the live backfill returns status="skipped" when
            # nothing would change).
            class SkippedRecord:
                status = "skipped"
                new_tier_slug = "main"
                message = "no change: already on main"

            return SkippedRecord()

        payload = {
            "contacts": [{
                "email": "idempotent@test.com",
                "stripe_customer_id": "cus_IDEM",
                "tier": "main",
            }],
        }

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ) as backfill:
            r1 = self._post(payload)
            calls_after_first = backfill.call_count

            r2 = self._post(payload)
            calls_after_second = backfill.call_count

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["warnings"], [])

        user = User.objects.get(email="idempotent@test.com")
        self.assertEqual(user.tier.slug, "main")
        self.assertEqual(
            TierOverride.objects.filter(user=user, is_active=True).count(),
            0,
        )

        # Second call did the dry-run lookup once, but did NOT re-issue a
        # non-dry-run backfill once it saw the tier already matched.
        delta = calls_after_second - calls_after_first
        self.assertEqual(
            delta,
            1,
            f"Expected exactly 1 backfill call on the idempotent second "
            f"import (the dry_run validation), got {delta}",
        )

    # ------------------------------------------------------------------
    # AC7 (single backfill call when customer_id is newly written).
    # ------------------------------------------------------------------

    def test_import_calls_backfill_once_when_stripe_customer_id_newly_set(self):
        """First row sets customer_id AND requests matching tier -> 1 backfill call."""
        main = Tier.objects.get(slug="main")

        def sync_from_stripe(user, **kwargs):
            user.tier = main
            user.save(update_fields=["tier"])

            class Record:
                status = "changed"
                new_tier_slug = "main"
                message = "changed"

            return Record()

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ) as backfill:
            response = self._post({
                "contacts": [{
                    "email": "single-call@test.com",
                    "stripe_customer_id": "cus_SINGLE",
                    "tier": "main",
                }],
            })

        self.assertEqual(response.status_code, 200)
        # Exactly one call: the customer-id sync's record is reused by the
        # validation path, so no second dry_run / commit call fires.
        self.assertEqual(
            backfill.call_count,
            1,
            f"Expected exactly 1 backfill call, got {backfill.call_count}",
        )

    def test_import_calls_backfill_with_dry_run_when_customer_id_unchanged(self):
        """User already has customer_id; tier-only row -> dry_run + commit."""
        main = Tier.objects.get(slug="main")
        user = User.objects.create_user(
            email="existing-cid@test.com",
            password=None,
            stripe_customer_id="cus_EXISTING",
        )

        call_log = []

        def sync_from_stripe(user, **kwargs):
            dry_run = kwargs.get("dry_run", False)
            call_log.append({"dry_run": dry_run})
            if not dry_run:
                user.tier = main
                user.save(update_fields=["tier"])

                class ChangedRecord:
                    status = "changed"
                    new_tier_slug = "main"
                    message = "changed: free -> main"

                return ChangedRecord()

            class DryRunRecord:
                status = "dry_run"
                new_tier_slug = "main"
                message = "would change: free -> main"

            return DryRunRecord()

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ):
            response = self._post({
                "contacts": [{
                    "email": "existing-cid@test.com",
                    "tier": "main",
                }],
            })

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main")
        # Exactly two calls: dry_run first (validation), then a real commit
        # (because the dry_run reported changes pending).
        self.assertEqual(
            len(call_log),
            2,
            f"Expected 2 backfill calls (dry_run + commit), got {call_log}",
        )
        self.assertEqual(call_log[0]["dry_run"], True)
        self.assertEqual(call_log[1]["dry_run"], False)

    # ------------------------------------------------------------------
    # AC9 (level-0 short-circuit).
    # ------------------------------------------------------------------

    def test_import_free_default_tier_does_not_call_stripe(self):
        """default_tier='free' (level 0) -> no Stripe call, no warning."""
        free = Tier.objects.get(slug="free")
        self.assertEqual(free.level, 0)

        with mock.patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
        ) as backfill:
            response = self._post({
                "contacts": [{"email": "level-zero@test.com"}],
                "default_tier": "free",
            })

        self.assertEqual(response.status_code, 200)
        backfill.assert_not_called()
        self.assertEqual(response.json()["warnings"], [])
