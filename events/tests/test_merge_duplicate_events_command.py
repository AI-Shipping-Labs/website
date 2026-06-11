"""Tests for the ``merge_duplicate_events`` management command (issue #881)."""

import datetime as dt
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from events.models import Event, EventRegistration

User = get_user_model()

MAY19 = dt.datetime(2026, 5, 19, 0, 0, tzinfo=dt.timezone.utc)
MAY19_STUDIO = dt.datetime(2026, 5, 19, 15, 0, tzinfo=dt.timezone.utc)


def _pair():
    canonical = Event.objects.create(
        slug="may19-studio", title="May 19 Workshop",
        start_datetime=MAY19_STUDIO, origin="studio", source_repo="",
        status="upcoming", published=True)
    duplicate = Event.objects.create(
        slug="may19-github", title="May 19 Workshop",
        start_datetime=MAY19, origin="github", source_repo="workshops-content",
        kind="workshop", status="completed", published=True)
    return canonical, duplicate


class MergeCommandArgValidationTest(TestCase):
    def test_requires_all_or_explicit_pair(self):
        with self.assertRaises(CommandError):
            call_command("merge_duplicate_events")

    def test_rejects_all_with_explicit_pair(self):
        with self.assertRaises(CommandError):
            call_command(
                "merge_duplicate_events", "--all",
                "--canonical", "1", "--duplicate", "2")


class MergeCommandDryRunTest(TestCase):
    def test_dry_run_writes_nothing(self):
        canonical, duplicate = _pair()
        member = User.objects.create_user(email="m@test.com", password="x")
        EventRegistration.objects.create(event=duplicate, user=member)

        out = StringIO()
        call_command("merge_duplicate_events", "--all", stdout=out)

        # Plan printed.
        self.assertIn("DRY-RUN", out.getvalue())
        self.assertIn("would merge", out.getvalue())
        # Nothing written.
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "completed")
        self.assertTrue(duplicate.published)
        self.assertTrue(
            EventRegistration.objects.filter(event=duplicate).exists())


class MergeCommandCommitTest(TestCase):
    def test_commit_merges_pair(self):
        canonical, duplicate = _pair()
        member = User.objects.create_user(email="m@test.com", password="x")
        EventRegistration.objects.create(event=duplicate, user=member)

        out = StringIO()
        call_command("merge_duplicate_events", "--all", "--commit", stdout=out)

        self.assertIn("merged", out.getvalue())
        self.assertIn("merged=1", out.getvalue())
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "cancelled")
        self.assertFalse(duplicate.published)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=canonical, user=member).exists())

    def test_explicit_pair_commit(self):
        canonical, duplicate = _pair()
        out = StringIO()
        call_command(
            "merge_duplicate_events",
            "--canonical", str(canonical.pk),
            "--duplicate", str(duplicate.pk),
            "--commit", stdout=out)
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "cancelled")

    def test_unknown_explicit_pk_errors(self):
        with self.assertRaises(CommandError):
            call_command(
                "merge_duplicate_events",
                "--canonical", "999999", "--duplicate", "888888",
                "--commit")
