"""Tests for the Stripe / Slack / name fields on POST /api/contacts/import.

Issue #437. The original /api/contacts/import (issue #431) only wrote ``email``,
``tags``, and (optionally) a ``TierOverride``. This file exercises the five
additional optional per-row keys: ``first_name``, ``last_name``,
``stripe_customer_id``, ``subscription_id``, ``slack_member``.

Each test creates one row at a time so the assertion targets the exact write
rule under test (last-write-wins for names, write-once for Stripe IDs,
authoritative-with-timestamp for Slack).
"""

import csv
import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from payments.models import Tier

User = get_user_model()


class ContactsImportExtendedFieldsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
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

    # -- name fields -------------------------------------------------------

    def test_import_sets_first_and_last_name(self):
        response = self._post({
            "contacts": [{
                "email": "ada@test.com",
                "first_name": "Ada",
                "last_name": "Lovelace",
            }],
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="ada@test.com")
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")

    def test_import_blank_name_does_not_clear_existing(self):
        existing = User.objects.create_user(
            email="keep@test.com",
            password=None,
            first_name="Grace",
            last_name="Hopper",
        )

        response = self._post({
            "contacts": [{
                "email": "keep@test.com",
                "first_name": "",
                "last_name": "   ",
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "Grace")
        self.assertEqual(existing.last_name, "Hopper")

    def test_import_name_last_write_wins_on_non_empty(self):
        existing = User.objects.create_user(
            email="rewrite@test.com",
            password=None,
            first_name="Old",
            last_name="Name",
        )

        response = self._post({
            "contacts": [{
                "email": "rewrite@test.com",
                "first_name": "New",
                "last_name": "Name2",
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "New")
        self.assertEqual(existing.last_name, "Name2")

    def test_import_trims_whitespace_around_name(self):
        response = self._post({
            "contacts": [{
                "email": "trim@test.com",
                "first_name": "  Trimmed  ",
                "last_name": "\tName\n",
            }],
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="trim@test.com")
        self.assertEqual(user.first_name, "Trimmed")
        self.assertEqual(user.last_name, "Name")

    # -- stripe_customer_id ------------------------------------------------

    def test_import_sets_stripe_customer_id_when_empty(self):
        existing = User.objects.create_user(
            email="stripe@test.com",
            password=None,
        )
        # Sanity: blank by default.
        self.assertEqual(existing.stripe_customer_id, "")

        with patch("studio.services.contacts_import.backfill_user_from_stripe"):
            response = self._post({
                "contacts": [{
                    "email": "stripe@test.com",
                    "stripe_customer_id": "cus_ABC",
                }],
            })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.stripe_customer_id, "cus_ABC")
        # Identical-write should never produce a conflict warning.
        self.assertEqual(response.json()["warnings"], [])

    def test_import_syncs_tier_after_setting_stripe_customer_id(self):
        existing = User.objects.create_user(
            email="stripe-sync@test.com",
            password=None,
        )
        main = Tier.objects.get(slug="main")

        def sync_from_stripe(user):
            user.tier = main
            user.subscription_id = "sub_SYNCED"
            user.save(update_fields=["tier", "subscription_id"])

            class Record:
                status = "changed"
                message = "changed: free -> main"

            return Record()

        with patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            side_effect=sync_from_stripe,
        ) as mock_backfill:
            response = self._post({
                "contacts": [{
                    "email": "stripe-sync@test.com",
                    "stripe_customer_id": "cus_SYNC",
                }],
            })

        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.stripe_customer_id, "cus_SYNC")
        self.assertEqual(existing.tier.slug, "main")
        self.assertEqual(existing.subscription_id, "sub_SYNCED")
        mock_backfill.assert_called_once()
        self.assertEqual(response.json()["warnings"], [])

    def test_import_reports_stripe_sync_warning_after_customer_id(self):
        existing = User.objects.create_user(
            email="stripe-warning@test.com",
            password=None,
        )

        class Record:
            status = "warning"
            message = "warning: active Stripe subscription uses unknown price"

        with patch(
            "studio.services.contacts_import.backfill_user_from_stripe",
            return_value=Record(),
        ):
            response = self._post({
                "contacts": [{
                    "email": "stripe-warning@test.com",
                    "stripe_customer_id": "cus_WARN",
                }],
            })

        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.stripe_customer_id, "cus_WARN")
        warnings = response.json()["warnings"]
        self.assertEqual(warnings[0]["reason"], "stripe_sync_warning")

    def test_import_does_not_overwrite_existing_stripe_customer_id(self):
        existing = User.objects.create_user(
            email="webhook@test.com",
            password=None,
            stripe_customer_id="cus_OLD",
        )

        response = self._post({
            "contacts": [{
                "email": "webhook@test.com",
                "stripe_customer_id": "cus_NEW",
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.stripe_customer_id, "cus_OLD")

        warnings = response.json()["warnings"]
        conflict = next(
            (w for w in warnings if w["reason"] == "stripe_customer_id_conflict"),
            None,
        )
        self.assertIsNotNone(
            conflict,
            f"Expected stripe_customer_id_conflict warning in {warnings}",
        )
        self.assertEqual(conflict["value"], "cus_NEW")

    def test_import_identical_stripe_customer_id_is_silent_noop(self):
        existing = User.objects.create_user(
            email="same@test.com",
            password=None,
            stripe_customer_id="cus_SAME",
        )

        with patch("studio.services.contacts_import.backfill_user_from_stripe"):
            response = self._post({
                "contacts": [{
                    "email": "same@test.com",
                    "stripe_customer_id": "cus_SAME",
                }],
            })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.stripe_customer_id, "cus_SAME")
        self.assertEqual(response.json()["warnings"], [])

    def test_import_first_row_wins_for_stripe_customer_id(self):
        # Two rows for the same email both carry an ID; the first should land,
        # the second should produce a conflict warning.
        response = self._post({
            "contacts": [
                {"email": "race@test.com", "stripe_customer_id": "cus_FIRST"},
                {"email": "race@test.com", "stripe_customer_id": "cus_SECOND"},
            ],
        })
        self.assertEqual(response.status_code, 200)
        # Second row is a duplicate-within-file; it does not re-run upsert.
        # So the only stripe write is from row 1; row 2 is "skipped".
        body = response.json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["skipped"], 1)
        user = User.objects.get(email="race@test.com")
        self.assertEqual(user.stripe_customer_id, "cus_FIRST")

    def test_import_subscription_id_same_overwrite_rule(self):
        existing = User.objects.create_user(
            email="sub@test.com",
            password=None,
            subscription_id="sub_OLD",
        )

        response = self._post({
            "contacts": [{
                "email": "sub@test.com",
                "subscription_id": "sub_NEW",
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.subscription_id, "sub_OLD")
        warnings = response.json()["warnings"]
        conflict = next(
            (w for w in warnings if w["reason"] == "subscription_id_conflict"),
            None,
        )
        self.assertIsNotNone(
            conflict,
            f"Expected subscription_id_conflict warning in {warnings}",
        )
        self.assertEqual(conflict["value"], "sub_NEW")

    def test_import_subscription_id_writes_when_empty(self):
        existing = User.objects.create_user(
            email="newsub@test.com",
            password=None,
        )
        self.assertEqual(existing.subscription_id, "")

        response = self._post({
            "contacts": [{
                "email": "newsub@test.com",
                "subscription_id": "sub_FRESH",
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.subscription_id, "sub_FRESH")

    def test_import_blank_stripe_customer_id_is_noop(self):
        existing = User.objects.create_user(
            email="blank@test.com",
            password=None,
            stripe_customer_id="cus_KEEP",
        )

        response = self._post({
            "contacts": [{
                "email": "blank@test.com",
                "stripe_customer_id": "",
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.stripe_customer_id, "cus_KEEP")
        self.assertEqual(response.json()["warnings"], [])

    # -- slack_member -------------------------------------------------------

    def test_import_slack_member_true_sets_flag_and_timestamp(self):
        existing = User.objects.create_user(
            email="slack-on@test.com",
            password=None,
        )
        self.assertFalse(existing.slack_member)
        self.assertIsNone(existing.slack_checked_at)

        before = timezone.now()
        response = self._post({
            "contacts": [{
                "email": "slack-on@test.com",
                "slack_member": True,
            }],
        })
        after = timezone.now()
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertTrue(existing.slack_member)
        self.assertIsNotNone(existing.slack_checked_at)
        # Within the request window (5s wall-clock margin).
        self.assertGreaterEqual(
            existing.slack_checked_at, before - timedelta(seconds=5),
        )
        self.assertLessEqual(
            existing.slack_checked_at, after + timedelta(seconds=5),
        )

    def test_import_slack_member_false_sets_flag_and_timestamp(self):
        old_time = timezone.now() - timedelta(days=30)
        existing = User.objects.create_user(
            email="slack-off@test.com",
            password=None,
        )
        existing.slack_member = True
        existing.slack_checked_at = old_time
        existing.save(update_fields=["slack_member", "slack_checked_at"])

        response = self._post({
            "contacts": [{
                "email": "slack-off@test.com",
                "slack_member": False,
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertFalse(existing.slack_member)
        # Stamp was refreshed to "now", not left at the 30-day-old value.
        self.assertGreater(existing.slack_checked_at, old_time)

    def test_import_slack_member_omitted_leaves_fields_alone(self):
        old_time = timezone.now() - timedelta(days=7)
        existing = User.objects.create_user(
            email="slack-skip@test.com",
            password=None,
        )
        existing.slack_member = True
        existing.slack_checked_at = old_time
        existing.save(update_fields=["slack_member", "slack_checked_at"])

        response = self._post({
            "contacts": [{
                "email": "slack-skip@test.com",
                # No slack_member key at all.
                "tags": ["unrelated"],
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertTrue(existing.slack_member)
        # Stamp untouched -- background-job state preserved.
        self.assertEqual(existing.slack_checked_at, old_time)

    def test_import_slack_member_invalid_string_emits_warning(self):
        existing = User.objects.create_user(
            email="bad-slack@test.com",
            password=None,
        )
        self.assertFalse(existing.slack_member)
        self.assertIsNone(existing.slack_checked_at)

        response = self._post({
            "contacts": [{
                "email": "bad-slack@test.com",
                "slack_member": "yes",
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        # No DB write -- still default.
        self.assertFalse(existing.slack_member)
        self.assertIsNone(existing.slack_checked_at)

        warnings = response.json()["warnings"]
        self.assertTrue(
            any(w["reason"] == "invalid_slack_member" for w in warnings),
            f"Expected invalid_slack_member warning in {warnings}",
        )

    def test_import_slack_member_int_one_is_invalid(self):
        # JSON ``true`` round-trips to Python ``True``; integer 1 is NOT a
        # bool from ``isinstance(x, bool)``'s point of view, so it's rejected
        # to avoid silently coercing CSV-style "1" into True.
        # (Note: ``isinstance(True, int)`` is True, but ``isinstance(1, bool)``
        # is False -- we use the latter so int 1 / 0 falls through to the
        # warning branch.)
        existing = User.objects.create_user(
            email="int-slack@test.com",
            password=None,
        )

        response = self._post({
            "contacts": [{
                "email": "int-slack@test.com",
                "slack_member": 1,
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertFalse(existing.slack_member)
        self.assertIsNone(existing.slack_checked_at)

        warnings = response.json()["warnings"]
        self.assertTrue(
            any(w["reason"] == "invalid_slack_member" for w in warnings),
        )

    def test_import_slack_member_null_is_invalid(self):
        existing = User.objects.create_user(
            email="null-slack@test.com",
            password=None,
        )
        existing.slack_member = True
        existing.save(update_fields=["slack_member"])

        response = self._post({
            "contacts": [{
                "email": "null-slack@test.com",
                "slack_member": None,
            }],
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        # null is NOT treated as "omit"; it's a type error and the existing
        # flag is left alone.
        self.assertTrue(existing.slack_member)
        warnings = response.json()["warnings"]
        self.assertTrue(
            any(w["reason"] == "invalid_slack_member" for w in warnings),
        )

    # -- backwards compatibility -------------------------------------------

    def test_import_payload_without_new_keys_creates_clean_user(self):
        """A row with only ``email`` (the v1 shape from #431) still works."""
        response = self._post({
            "contacts": [{"email": "compat@test.com"}],
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="compat@test.com")
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")
        self.assertEqual(user.stripe_customer_id, "")
        self.assertEqual(user.subscription_id, "")
        self.assertFalse(user.slack_member)
        self.assertIsNone(user.slack_checked_at)
        self.assertEqual(response.json()["warnings"], [])


class ContactsExportNewColumnsTest(TestCase):
    """Issue #437: export must round-trip the four new identity fields."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def _get(self, path):
        return self.client.get(
            path,
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    def test_export_includes_new_columns_json(self):
        stamp = timezone.now()
        u = User.objects.create_user(
            email="full@test.com",
            password=None,
            stripe_customer_id="cus_ABC",
            subscription_id="sub_XYZ",
        )
        u.slack_member = True
        u.slack_checked_at = stamp
        u.save(update_fields=["slack_member", "slack_checked_at"])

        response = self._get("/api/contacts/export")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        match = next(c for c in body["contacts"] if c["email"] == "full@test.com")
        self.assertEqual(match["stripe_customer_id"], "cus_ABC")
        self.assertEqual(match["subscription_id"], "sub_XYZ")
        self.assertIs(match["slack_member"], True)
        self.assertEqual(match["slack_checked_at"], stamp.isoformat())

    def test_export_serializes_null_slack_checked_at_as_null(self):
        User.objects.create_user(
            email="never-checked@test.com",
            password=None,
        )
        response = self._get("/api/contacts/export")
        body = response.json()
        match = next(
            c for c in body["contacts"] if c["email"] == "never-checked@test.com"
        )
        self.assertIsNone(match["slack_checked_at"])
        self.assertIs(match["slack_member"], False)
        self.assertEqual(match["stripe_customer_id"], "")
        self.assertEqual(match["subscription_id"], "")

    def test_export_csv_header_includes_new_columns(self):
        User.objects.create_user(email="csv@test.com", password=None)
        response = self._get("/api/contacts/export?format=csv")
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        header = next(reader)
        self.assertEqual(
            header,
            [
                "email",
                "first_name",
                "last_name",
                "tags",
                "tier",
                "email_verified",
                "unsubscribed",
                "date_joined",
                "last_login",
                "stripe_customer_id",
                "subscription_id",
                "slack_member",
                "slack_checked_at",
            ],
        )

    def test_export_csv_row_carries_new_columns(self):
        stamp = timezone.now()
        u = User.objects.create_user(
            email="csvrow@test.com",
            password=None,
            stripe_customer_id="cus_CSV",
            subscription_id="sub_CSV",
        )
        u.slack_member = True
        u.slack_checked_at = stamp
        u.save(update_fields=["slack_member", "slack_checked_at"])

        response = self._get("/api/contacts/export?format=csv")
        text = response.content.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        header = rows[0]
        data_rows = rows[1:]
        match = next(r for r in data_rows if r[header.index("email")] == "csvrow@test.com")
        self.assertEqual(match[header.index("stripe_customer_id")], "cus_CSV")
        self.assertEqual(match[header.index("subscription_id")], "sub_CSV")
        self.assertEqual(match[header.index("slack_member")], "true")
        self.assertEqual(match[header.index("slack_checked_at")], stamp.isoformat())
