"""Tests for ``accounts.tasks.purge_unverified_users`` (issue #452).

The purge job hard-deletes expired unverified email-signup accounts
that have done nothing else. Each test pins a single safety gate so a
regression in any of them surfaces a clear failure.
"""

import datetime
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.utils import DatabaseError
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from accounts.tasks import purge_unverified_users
from accounts.tasks.purge_unverified_users import (
    DEFAULT_PURGE_UNVERIFIED_BATCH_SIZE,
    DEFAULT_PURGE_UNVERIFIED_MAX_BATCHES,
    _candidate_queryset,
    _positive_int_config,
    _related_user_ids,
    _standard_base_queryset,
)
from email_app.models import EmailLog, SesEvent


def _make_unverified(email, *, expires_offset_hours, **extra):
    """Fixture: an unverified user with a verification window relative to now."""
    return User.objects.create_user(
        email=email,
        password="secure1234",
        email_verified=False,
        verification_expires_at=timezone.now()
        + datetime.timedelta(hours=expires_offset_hours),
        **extra,
    )


class PurgeUnverifiedUsersTest(TestCase):
    """Hard-delete only when the user is genuinely abandoned."""

    def test_purge_deletes_expired_unverified_user(self):
        user = _make_unverified("expired@example.com", expires_offset_hours=-24)
        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 1)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_purge_keeps_unexpired_unverified_user(self):
        user = _make_unverified("future@example.com", expires_offset_hours=24)
        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_keeps_verified_user(self):
        user = User.objects.create_user(
            email="verified@example.com",
            password="secure1234",
            email_verified=True,
            verification_expires_at=timezone.now() - datetime.timedelta(days=1),
        )
        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_keeps_user_with_last_login(self):
        user = _make_unverified(
            "loggedin@example.com",
            expires_offset_hours=-24,
            last_login=timezone.now() - datetime.timedelta(days=30),
        )
        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="WARNING",
        ) as logs:
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(
            any("blocked by last_login" in msg for msg in logs.output),
            f"expected blocked-by-last_login warning, got {logs.output}",
        )

    def test_purge_keeps_user_with_stripe_customer_id(self):
        user = _make_unverified(
            "stripey@example.com",
            expires_offset_hours=-1,
            stripe_customer_id="cus_X",
        )
        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="WARNING",
        ) as logs:
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(
            any("blocked by stripe_customer_id" in msg for msg in logs.output),
            f"expected blocked-by-stripe warning, got {logs.output}",
        )

    def test_purge_keeps_user_with_subscription_id(self):
        user = _make_unverified(
            "subbed@example.com",
            expires_offset_hours=-1,
            subscription_id="sub_X",
        )
        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="WARNING",
        ):
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_keeps_user_with_email_log(self):
        user = _make_unverified("hadmail@example.com", expires_offset_hours=-2)
        EmailLog.objects.create(
            user=user,
            email_type="welcome",
            ses_message_id="abc",
        )

        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="WARNING",
        ) as logs:
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        # Should name the blocking reverse relation explicitly so an
        # operator can grep the log and find which downstream rows
        # exist.
        self.assertTrue(
            any("email_logs" in msg for msg in logs.output),
            f"expected blocker name in warning, got {logs.output}",
        )

    def test_purge_safe_for_users_with_null_verification_expires_at(self):
        """Legacy rows (pre-#452 migration) are exempt from purge."""
        user = User.objects.create_user(
            email="legacy@example.com",
            password="secure1234",
            email_verified=False,
            verification_expires_at=None,
        )
        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_ignores_signup_bookkeeping_relations(self):
        user = _make_unverified(
            "bookkeeping@example.com",
            expires_offset_hours=-24,
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            verified=False,
            primary=True,
        )
        SocialAccount.objects.create(
            user=user,
            provider="test-provider",
            uid="bookkeeping-user",
        )
        LogEntry.objects.create(
            user_id=user.pk,
            content_type=ContentType.objects.get_for_model(User),
            object_id=str(user.pk),
            object_repr=user.email,
            action_flag=ADDITION,
            change_message="",
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_standard"], 1)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_purge_processes_multiple_candidates_in_one_pass(self):
        """Purge does not stop at the first candidate."""
        a = _make_unverified("a@example.com", expires_offset_hours=-48)
        b = _make_unverified("b@example.com", expires_offset_hours=-12)
        keep = _make_unverified("keep@example.com", expires_offset_hours=12)

        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 2)
        self.assertFalse(User.objects.filter(pk=a.pk).exists())
        self.assertFalse(User.objects.filter(pk=b.pk).exists())
        self.assertTrue(User.objects.filter(pk=keep.pk).exists())


def _make_eager_candidate(email, *, bounce_age_hours, **extra):
    """Fixture: unverified user with a permanent bounce at the given age.

    Mirrors the production state set by ``_mark_permanent_bounce`` so
    each eager-bucket test exercises one safety gate without leaking
    state across tests.
    """
    return User.objects.create_user(
        email=email,
        password="secure1234",
        email_verified=False,
        bounce_state=User.BounceState.PERMANENT,
        bounce_recorded_at=timezone.now() - datetime.timedelta(
            hours=bounce_age_hours,
        ),
        last_bounce_diagnostic="550 5.1.1 No such mailbox",
        **extra,
    )


class EagerBounceBucketTest(TestCase):
    """Pass B: unverified users with a permanent bounce older than 24h."""

    def test_eager_purge_deletes_old_permanent_bounce(self):
        """The ledger row blocks the standard bucket but not the eager one."""
        user = _make_eager_candidate(
            "dead@example.com",
            bounce_age_hours=48,
        )
        # The verification email IS the row that bounced -- it's why
        # the standard purge would skip the user. The eager bucket
        # ignores it.
        EmailLog.objects.create(
            user=user,
            email_type="email_verification",
            ses_message_id="ses-eager-1",
        )
        SesEvent.objects.create(
            user=user,
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            message_id="sns-eager-1",
            raw_payload={},
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 1)
        self.assertEqual(result["deleted_standard"], 0)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_eager_purge_skips_fresh_permanent_bounce(self):
        """Inside the 24h grace window the user stays put."""
        user = _make_eager_candidate(
            "wait@example.com",
            bounce_age_hours=1,
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 0)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_eager_purge_skips_verified_user(self):
        """Eager bucket only touches unverified rows."""
        user = User.objects.create_user(
            email="verified-bounced@example.com",
            password="secure1234",
            email_verified=True,
            bounce_state=User.BounceState.PERMANENT,
            bounce_recorded_at=timezone.now() - datetime.timedelta(days=30),
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_eager_purge_skips_user_with_stripe_customer_id(self):
        """Payments still block even when the email is dead."""
        user = _make_eager_candidate(
            "paid-dead@example.com",
            bounce_age_hours=48,
            stripe_customer_id="cus_X",
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 0)
        self.assertEqual(result["skipped_eager"], 1)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_eager_purge_skips_soft_bounce(self):
        """Soft state never triggers the eager bucket."""
        user = User.objects.create_user(
            email="soft@example.com",
            password="secure1234",
            email_verified=False,
            bounce_state=User.BounceState.SOFT,
            bounce_recorded_at=timezone.now() - datetime.timedelta(days=30),
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 0)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_return_dict_preserves_six_counters_and_adds_remaining(self):
        """Both buckets contribute to a shape that monitors can consume."""
        # One eager-bucket candidate, one standard-bucket candidate.
        eager_user = _make_eager_candidate(
            "eager-row@example.com", bounce_age_hours=48,
        )
        EmailLog.objects.create(
            user=eager_user,
            email_type="email_verification",
            ses_message_id="ses-eager-2",
        )
        standard_user = _make_unverified(
            "standard-row@example.com", expires_offset_hours=-48,
        )

        result = purge_unverified_users()

        expected_keys = {
            "deleted",
            "deleted_standard",
            "deleted_eager",
            "skipped",
            "skipped_standard",
            "skipped_eager",
        }
        self.assertTrue(expected_keys.issubset(result))
        self.assertIn("remaining_standard", result)
        self.assertIn("remaining_eager", result)

        self.assertEqual(result["deleted_standard"], 1)
        self.assertEqual(result["deleted_eager"], 1)
        # Legacy totals stay backwards-compatible.
        self.assertEqual(
            result["deleted"],
            result["deleted_standard"] + result["deleted_eager"],
        )
        self.assertEqual(
            result["skipped"],
            result["skipped_standard"] + result["skipped_eager"],
        )
        self.assertFalse(User.objects.filter(pk=eager_user.pk).exists())
        self.assertFalse(User.objects.filter(pk=standard_user.pk).exists())

    def test_eager_purge_emits_audit_log_with_email_and_recorded_at(self):
        """Each eager-bucket delete logs at INFO with audit fields."""
        user = _make_eager_candidate(
            "audit@example.com",
            bounce_age_hours=48,
        )
        recorded_iso = user.bounce_recorded_at.isoformat()

        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="INFO",
        ) as logs:
            purge_unverified_users()

        # Find the per-row eager-purge audit line (not the summary).
        eager_lines = [m for m in logs.output if "Eager-purged" in m]
        self.assertTrue(eager_lines, f"no eager-purge audit line in {logs.output}")
        line = eager_lines[0]
        self.assertIn("audit@example.com", line)
        self.assertIn(recorded_iso, line)
        self.assertIn("550 5.1.1 No such mailbox", line)

    @override_settings(BOUNCE_PURGE_DELAY_HOURS=1)
    def test_eager_purge_honors_settings_override(self):
        """A 90-minute-old bounce is purged when the override drops to 1h."""
        user = _make_eager_candidate(
            "tweaked@example.com",
            bounce_age_hours=1.5,
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 1)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())


class BoundedPurgeQueryPlanTest(TestCase):
    def test_candidate_queryset_contains_all_field_gates(self):
        queryset = _candidate_queryset(_standard_base_queryset(timezone.now()))
        sql = str(queryset.query)

        self.assertIn('"last_login" IS NULL', sql)
        self.assertIn('"stripe_customer_id" =', sql)
        self.assertIn('"subscription_id" =', sql)
        self.assertIn('"email_verified"', sql)
        self.assertIn('"verification_expires_at" IS NOT NULL', sql)

    def test_relation_queries_do_not_scale_with_candidate_count(self):
        def run(candidate_count):
            users = [
                _make_unverified(
                    f"query-{candidate_count}-{index}@example.com",
                    expires_offset_hours=-48,
                )
                for index in range(candidate_count)
            ]
            for user in users[: candidate_count // 2]:
                EmailLog.objects.create(
                    user=user,
                    email_type="welcome",
                    ses_message_id=f"query-{user.pk}",
                )
            with patch(
                "accounts.tasks.purge_unverified_users.get_config",
                side_effect=lambda key, default: default,
            ):
                with CaptureQueriesContext(connection) as queries:
                    purge_unverified_users()
            sql = [query["sql"] for query in queries.captured_queries]
            User.objects.all().delete()
            return len(sql), sql

        small_count, small_sql = run(8)
        large_count, large_sql = run(32)

        self.assertLessEqual(abs(large_count - small_count), 5)
        self.assertFalse(
            any('SELECT 1 AS "a"' in sql for sql in small_sql + large_sql),
            "relation gates must not issue per-user exists queries",
        )

    def test_relation_query_failure_preserves_whole_batch_and_returns(self):
        users = [
            _make_unverified(
                f"failure-{index}@example.com",
                expires_offset_hours=-48,
            )
            for index in range(3)
        ]

        with patch(
            "accounts.tasks.purge_unverified_users._related_user_ids",
            side_effect=DatabaseError("relation unavailable"),
        ):
            with self.assertLogs(
                "accounts.tasks.purge_unverified_users",
                level="WARNING",
            ) as logs:
                result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["skipped_standard"], 3)
        self.assertEqual(
            User.objects.filter(pk__in=[user.pk for user in users]).count(),
            3,
        )
        error_line = next(
            message
            for message in logs.output
            if "Failed to query reverse relation" in message
        )
        failed_accessor = error_line.split("reverse relation ", 1)[1].split()[0]
        self.assertTrue(
            any(
                f"blocked by {failed_accessor}" in message
                for message in logs.output
            ),
            f"expected failed accessor in warning, got {logs.output}",
        )

    def test_relation_failure_skips_failed_batch_and_continues(self):
        users = [
            _make_unverified(
                f"continue-{index}@example.com",
                expires_offset_hours=-48,
            )
            for index in range(3)
        ]
        failed_batch_ids = {users[0].pk, users[1].pk}

        def fail_first_batch(relation, batch_ids):
            if set(batch_ids) == failed_batch_ids:
                raise DatabaseError("first batch unavailable")
            return _related_user_ids(relation, batch_ids)

        def config(key, default):
            return {
                "PURGE_UNVERIFIED_BATCH_SIZE": 2,
                "PURGE_UNVERIFIED_MAX_BATCHES": 2,
            }.get(key, default)

        with patch(
            "accounts.tasks.purge_unverified_users.get_config",
            side_effect=config,
        ), patch(
            "accounts.tasks.purge_unverified_users._related_user_ids",
            side_effect=fail_first_batch,
        ):
            result = purge_unverified_users()

        self.assertEqual(result["deleted_standard"], 1)
        self.assertEqual(result["skipped_standard"], 2)
        self.assertTrue(User.objects.filter(pk__in=failed_batch_ids).exists())
        self.assertFalse(User.objects.filter(pk=users[2].pk).exists())

    def test_max_batches_leaves_backlog_and_next_run_continues(self):
        users = [
            _make_unverified(
                f"backlog-{index}@example.com",
                expires_offset_hours=-48,
            )
            for index in range(5)
        ]

        def config(key, default):
            return {
                "PURGE_UNVERIFIED_BATCH_SIZE": 2,
                "PURGE_UNVERIFIED_MAX_BATCHES": 1,
            }.get(key, default)

        with patch(
            "accounts.tasks.purge_unverified_users.get_config",
            side_effect=config,
        ):
            first = purge_unverified_users()
            second = purge_unverified_users()

        self.assertEqual(first["deleted_standard"], 2)
        self.assertEqual(first["remaining_standard"], 3)
        self.assertEqual(second["deleted_standard"], 2)
        self.assertEqual(second["remaining_standard"], 1)
        self.assertFalse(
            User.objects.filter(pk__in=[u.pk for u in users[:4]]).exists()
        )
        self.assertTrue(User.objects.filter(pk=users[4].pk).exists())

    def test_wall_clock_budget_returns_normally_without_deleting_batch(self):
        users = [
            _make_unverified(
                f"budget-{index}@example.com",
                expires_offset_hours=-48,
            )
            for index in range(2)
        ]
        ticks = iter([0, 0, 0, 241])

        with patch(
            "accounts.tasks.purge_unverified_users.time.monotonic",
            side_effect=lambda: next(ticks, 241),
        ):
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["remaining_standard"], 2)
        self.assertEqual(
            User.objects.filter(pk__in=[user.pk for user in users]).count(),
            2,
        )


class PurgeConfigTest(TestCase):
    def test_invalid_batch_overrides_fall_back_to_safe_defaults(self):
        invalid_values = ("", "not-an-int", "0", "-3", None)
        for invalid in invalid_values:
            with self.subTest(value=invalid):
                with patch(
                    "accounts.tasks.purge_unverified_users.get_config",
                    return_value=invalid,
                ):
                    self.assertEqual(
                        _positive_int_config(
                            "PURGE_UNVERIFIED_BATCH_SIZE",
                            DEFAULT_PURGE_UNVERIFIED_BATCH_SIZE,
                        ),
                        DEFAULT_PURGE_UNVERIFIED_BATCH_SIZE,
                    )
                    self.assertEqual(
                        _positive_int_config(
                            "PURGE_UNVERIFIED_MAX_BATCHES",
                            DEFAULT_PURGE_UNVERIFIED_MAX_BATCHES,
                        ),
                        DEFAULT_PURGE_UNVERIFIED_MAX_BATCHES,
                    )
