"""Tests for the ``process_missed_bounces`` management command (issue #765).

Covers the documented flag matrix:

- ``--email`` (single + repeatable) marks the matching user.
- ``--emails-from`` parses a file, skips blanks / # comments.
- ``--since`` (with optional ``--until``) selects users in a date range
  who have no existing bounce ``SesEvent``.
- ``--dry-run`` prints planned actions without writing.
- Idempotency: re-running over an already-processed user is a no-op.

Since issue #766 landed, the live ``User`` model carries a structured
``bounce_state`` field (replacing the legacy ``"bounced"`` contact
tag), so the command's structured-path branch is the live code path
exercised by these tests.
"""

import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from email_app.models import SesEvent

User = get_user_model()


def _call(*args):
    """Invoke the command and return (stdout, stderr) strings."""
    out = io.StringIO()
    err = io.StringIO()
    call_command(
        "process_missed_bounces",
        *args,
        stdout=out,
        stderr=err,
    )
    return out.getvalue(), err.getvalue()


class ProcessMissedBouncesArgsTest(TestCase):
    """Bad invocations reject early with CommandError."""

    def test_empty_invocation_raises(self):
        with self.assertRaises(CommandError):
            call_command("process_missed_bounces")

    def test_unparseable_since_raises(self):
        with self.assertRaises(CommandError):
            call_command("process_missed_bounces", "--since", "not-a-date")

    def test_unparseable_until_raises(self):
        with self.assertRaises(CommandError):
            call_command(
                "process_missed_bounces",
                "--since", "2026-05-01",
                "--until", "tomorrow",
            )

    def test_emails_from_missing_file_raises(self):
        with self.assertRaises(CommandError):
            call_command(
                "process_missed_bounces",
                "--emails-from", "/nonexistent/path/no-such-file.txt",
            )


class ProcessMissedBouncesSingleEmailTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="taylor@example.com")
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.bounce_state = User.BounceState.NONE
        self.user.bounce_recorded_at = None
        self.user.last_bounce_diagnostic = ""
        self.user.save(
            update_fields=[
                "unsubscribed",
                "tags",
                "bounce_state",
                "bounce_recorded_at",
                "last_bounce_diagnostic",
            ]
        )

    def test_marks_matching_user_permanently_bounced(self):
        out, _err = _call("--email", "taylor@example.com")

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        # Issue #766: structured bounce_state replaces the legacy
        # "bounced" tag. The command's hasattr(user, "bounce_state")
        # branch is now the live code path.
        self.assertEqual(
            self.user.bounce_state, User.BounceState.PERMANENT,
        )
        self.assertIsNotNone(self.user.bounce_recorded_at)
        self.assertEqual(
            self.user.last_bounce_diagnostic,
            "backfilled via process_missed_bounces",
        )

        event = SesEvent.objects.get(recipient_email="taylor@example.com")
        self.assertEqual(
            event.event_type, SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
        )
        self.assertEqual(event.bounce_type, "Permanent")
        self.assertEqual(
            event.diagnostic_code, "backfilled via process_missed_bounces",
        )
        self.assertTrue(event.message_id.startswith("backfill-"))
        self.assertEqual(event.user, self.user)
        # Audit payload identifies the synthetic origin.
        self.assertEqual(event.raw_payload.get("backfill"), True)
        self.assertEqual(
            event.raw_payload.get("source"), "process_missed_bounces",
        )
        self.assertIn("ran_at", event.raw_payload)

        self.assertIn("marked=1", out)
        self.assertIn("skipped_no_user=0", out)
        self.assertIn("skipped_existing_event=0", out)

    def test_no_matching_user_is_skipped(self):
        out, _err = _call("--email", "ghost@nowhere.example")

        self.assertFalse(
            SesEvent.objects.filter(
                recipient_email="ghost@nowhere.example",
            ).exists()
        )
        self.assertIn("no matching user", out)
        self.assertIn("marked=0", out)
        self.assertIn("skipped_no_user=1", out)

    def test_repeated_email_flag_marks_each(self):
        other = User.objects.create_user(email="other@example.com")
        out, _err = _call(
            "--email", "taylor@example.com",
            "--email", "other@example.com",
        )

        self.user.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        self.assertTrue(other.unsubscribed)
        self.assertEqual(SesEvent.objects.count(), 2)
        self.assertIn("marked=2", out)

    def test_idempotent_on_rerun(self):
        # First run marks the user.
        _call("--email", "taylor@example.com")
        self.assertEqual(SesEvent.objects.count(), 1)
        first_event = SesEvent.objects.get()

        # Second run is a no-op: no second SesEvent, the existing one
        # is untouched.
        out, _err = _call("--email", "taylor@example.com")
        self.assertEqual(SesEvent.objects.count(), 1)
        unchanged = SesEvent.objects.get()
        self.assertEqual(unchanged.pk, first_event.pk)
        self.assertIn("already processed", out)
        self.assertIn("skipped_existing_event=1", out)


class ProcessMissedBouncesDryRunTest(TestCase):
    def test_dry_run_writes_no_user_changes_and_no_ses_event(self):
        user = User.objects.create_user(email="taylor@example.com")
        user.unsubscribed = False
        user.tags = []
        user.bounce_state = User.BounceState.NONE
        user.save(
            update_fields=["unsubscribed", "tags", "bounce_state"],
        )
        events_before = SesEvent.objects.count()

        out, _err = _call("--email", "taylor@example.com", "--dry-run")

        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)
        # Issue #766: dry-run must not flip bounce_state either.
        self.assertEqual(user.bounce_state, User.BounceState.NONE)
        self.assertEqual(SesEvent.objects.count(), events_before)
        self.assertIn("would mark permanent bounce", out)
        self.assertIn("dry_run=True", out)


class ProcessMissedBouncesEmailsFromFileTest(TestCase):
    def test_reads_addresses_skipping_blank_and_comments(self, *_args):
        a = User.objects.create_user(email="a@example.com")
        b = User.objects.create_user(email="b@example.com")
        # Third address has no matching user -> "no matching user" path.

        import tempfile
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False,
        ) as fh:
            fh.write("# header comment\n")
            fh.write("a@example.com\n")
            fh.write("\n")  # blank line skipped
            fh.write("# another comment\n")
            fh.write("b@example.com\n")
            fh.write("ghost@nowhere.example\n")
            tmp_path = fh.name

        out, _err = _call("--emails-from", tmp_path)

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertTrue(a.unsubscribed)
        self.assertTrue(b.unsubscribed)
        self.assertEqual(SesEvent.objects.count(), 2)
        self.assertIn("marked=2", out)
        self.assertIn("skipped_no_user=1", out)


class ProcessMissedBouncesSinceTest(TestCase):
    """``--since`` and ``--until`` select users by signup window."""

    def setUp(self):
        now = timezone.now()
        # In-range: joined two days ago.
        self.in_range = User.objects.create_user(email="recent@example.com")
        self.in_range.date_joined = now - timedelta(days=2)
        self.in_range.save(update_fields=["date_joined"])

        # Out of range: joined a long time ago.
        self.old = User.objects.create_user(email="old@example.com")
        self.old.date_joined = now - timedelta(days=400)
        self.old.save(update_fields=["date_joined"])

        # In-range but already has a bounce SesEvent -- must be skipped.
        self.already_done = User.objects.create_user(
            email="done@example.com",
        )
        self.already_done.date_joined = now - timedelta(days=1)
        self.already_done.save(update_fields=["date_joined"])
        SesEvent.objects.create(
            message_id="pre-existing-bounce",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            recipient_email="done@example.com",
            user=self.already_done,
            raw_payload={"existing": True},
        )

    def test_since_marks_in_range_users_only(self):
        since_date = (
            timezone.localdate(timezone.now()) - timedelta(days=5)
        ).isoformat()
        out, _err = _call("--since", since_date)

        self.in_range.refresh_from_db()
        self.old.refresh_from_db()

        self.assertTrue(self.in_range.unsubscribed)
        self.assertFalse(self.old.unsubscribed)
        # Synthetic SesEvent for the in-range user only; the old user has
        # none and the already-done user keeps its existing one.
        self.assertEqual(
            SesEvent.objects.filter(
                recipient_email="recent@example.com",
            ).count(),
            1,
        )
        self.assertEqual(
            SesEvent.objects.filter(
                recipient_email="old@example.com",
            ).count(),
            0,
        )
        # Pre-existing row not duplicated.
        self.assertEqual(
            SesEvent.objects.filter(
                recipient_email="done@example.com",
            ).count(),
            1,
        )
        self.assertIn("marked=1", out)

    def test_until_excludes_users_after_upper_bound(self):
        # --until set to yesterday so users that joined "today/-2 days ago"
        # (recent / done) all sit BEFORE the upper bound and are still
        # eligible, but a future-joined user would not be.
        today = timezone.localdate(timezone.now())
        since_date = (today - timedelta(days=5)).isoformat()
        future_user = User.objects.create_user(email="future@example.com")
        future_user.date_joined = timezone.now() + timedelta(days=5)
        future_user.save(update_fields=["date_joined"])

        until_date = today.isoformat()  # excludes future user
        out, _err = _call(
            "--since", since_date,
            "--until", until_date,
        )

        future_user.refresh_from_db()
        self.assertFalse(future_user.unsubscribed)
        self.assertFalse(
            SesEvent.objects.filter(
                recipient_email="future@example.com",
            ).exists()
        )
        self.assertIn("marked=", out)  # in-range user still gets marked


# The structured-bounce-state branch is now the live path and is
# already covered by ``ProcessMissedBouncesSingleEmailTest`` above
# (which asserts ``bounce_state``, ``bounce_recorded_at`` and
# ``last_bounce_diagnostic`` on the real ``User`` row after #766
# landed). The transitional mocked-branch test from #765 has been
# removed -- one authoritative test per behavior.
