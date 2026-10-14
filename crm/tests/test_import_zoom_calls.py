"""Tests for the historical 1:1 call import (issue #1951 AC1-AC7).

All API access goes through a stub client -- no network. The service
under test is ``crm.services.zoom_call_import`` (the command is a thin
wrapper over ``run_import``).
"""

import datetime
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from crm.models.call import Call
from crm.services import zoom_call_import
from crm.services.zoom_call_import import (
    CrmApiClient,
    parse_summary_file,
    resolve_email,
    run_import,
)


def _summary_file(root, date, slug, name, *, duration=45):
    directory = Path(root) / "1x1"
    directory.mkdir(parents=True, exist_ok=True)
    text = (
        f"# 1x1: {name} and Alexey Grigorev\n\n"
        f"Date: {date}\n"
        f"Duration: {duration} minutes\n\n"
        "## Background\n\n- Talked about the portfolio project.\n"
    )
    path = directory / f"{date}-{slug}-summary.md"
    path.write_text(text, encoding="utf-8")
    return path


class StubClient:
    """Stub operator API: static user directory + recorded payloads."""

    def __init__(self, users=None, existing_refs=()):
        self.users = users or {}
        self.existing_refs = set(existing_refs)
        self.searches = []
        self.payloads = []

    def search_users(self, query, limit=100):
        self.searches.append(query)
        rows = []
        for email, (first, last) in self.users.items():
            blob = f"{first} {last}".casefold()
            if all(token.casefold() in blob for token in query.split()):
                rows.append(
                    {"email": email, "first_name": first, "last_name": last},
                )
        return rows

    def member_calls(self, email):
        calls = [
            {"source": "zoom", "source_ref": ref}
            for ref in sorted(self.existing_refs)
        ]
        return [{"email": email, "calls": calls}]

    def post_call(self, payload):
        self.payloads.append(payload)
        return {"id": len(self.payloads)}


class ParseTest(TestCase):
    def test_parses_name_date_duration_and_body(self):
        with TemporaryDirectory() as tmp:
            path = _summary_file(tmp, "2026-10-12", "mariano-beccaria", "Mariano Beccaria")
            parsed, reason = parse_summary_file(path)
            self.assertIsNone(reason)
            self.assertEqual(parsed.stem, "2026-10-12-mariano-beccaria-summary")
            self.assertEqual(parsed.name, "Mariano Beccaria")
            self.assertEqual(parsed.date, datetime.date(2026, 10, 12))
            self.assertEqual(parsed.duration_minutes, 45)
            self.assertIn("portfolio project", parsed.body)

    def test_occurred_at_is_noon_utc(self):
        with TemporaryDirectory() as tmp:
            path = _summary_file(tmp, "2026-10-12", "x", "X Y")
            parsed, _ = parse_summary_file(path)
            self.assertEqual(
                parsed.occurred_at().isoformat(), "2026-10-12T12:00:00+00:00",
            )

    def test_strips_parenthetical_alias_from_name(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "1x1"
            path.mkdir(parents=True)
            file_path = path / "2026-10-02-kalavati-singh-summary.md"
            file_path.write_text(
                "# 1x1: Kalavati Singh (Kala) and Alexey Grigorev\n\n"
                "Date: 2026-10-02\n",
                encoding="utf-8",
            )
            parsed, reason = parse_summary_file(file_path)
            self.assertIsNone(reason)
            self.assertEqual(parsed.name, "Kalavati Singh")

    def test_missing_date_line_is_skipped_never_guessed(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "1x1"
            directory.mkdir(parents=True)
            file_path = directory / "2026-10-12-nodate-summary.md"
            file_path.write_text(
                "# 1x1: No Date and Alexey Grigorev\n\nNo date here.\n",
                encoding="utf-8",
            )
            parsed, reason = parse_summary_file(file_path)
            self.assertIsNone(parsed)
            self.assertIn("Date:", reason)

    def test_bad_filename_is_skipped(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "1x1"
            directory.mkdir(parents=True)
            file_path = directory / "not-a-date-summary.md"
            file_path.write_text("# 1x1: X and Alexey Grigorev\n\nDate: 2026-10-12\n", encoding="utf-8")
            parsed, reason = parse_summary_file(file_path)
            self.assertIsNone(parsed)
            self.assertIn("filename", reason)


class _Parsed:
    """Minimal parsed-summary stand-in for resolution tests."""

    def __init__(self, name, stem="2026-10-12-x"):
        self.name = name
        self.stem = stem


class ResolveEmailTest(TestCase):
    def test_alias_by_name_wins_over_search(self):
        client = StubClient(users={"k@example.com": ("Kalavati", "Singh")})
        resolution = resolve_email(client, {"kala": "kala@resolved.com"}, _Parsed("Kala"))
        self.assertEqual(resolution.status, zoom_call_import.MATCHED)
        self.assertEqual(resolution.email, "kala@resolved.com")
        self.assertEqual(client.searches, [])

    def test_alias_by_file_stem_resolves(self):
        client = StubClient()
        resolution = resolve_email(
            client,
            {"2026-10-02-kalavati-singh": "kala@resolved.com"},
            _Parsed("Kalavati Singh", stem="2026-10-02-kalavati-singh"),
        )
        self.assertEqual(resolution.email, "kala@resolved.com")

    def test_unique_search_hit_resolves(self):
        client = StubClient(users={"m@example.com": ("Mariano", "Beccaria")})
        resolution = resolve_email(client, {}, _Parsed("Mariano Beccaria"))
        self.assertEqual(resolution.email, "m@example.com")

    def test_no_match_stays_unresolved(self):
        client = StubClient(users={"sam@example.com": ("Sam", "Else")})
        resolution = resolve_email(client, {}, _Parsed("Mariano Beccaria"))
        self.assertEqual(resolution.status, zoom_call_import.UNRESOLVED)
        self.assertEqual(resolution.reason, zoom_call_import.UNRESOLVED_NO_MATCH)

    def test_ambiguous_hits_narrow_by_exact_full_name(self):
        client = StubClient(users={
            "a@example.com": ("Mariano", "Beccaria"),
            "b@example.com": ("Mariano", "Beccaria-Later"),
        })
        resolution = resolve_email(client, {}, _Parsed("Mariano Beccaria"))
        self.assertEqual(resolution.email, "a@example.com")

    def test_still_ambiguous_after_narrowing_is_skipped(self):
        client = StubClient(users={
            "a@example.com": ("Alex", "Smith"),
            "b@example.com": ("Alex", "Smith"),
        })
        resolution = resolve_email(client, {}, _Parsed("Alex Smith"))
        self.assertEqual(resolution.status, zoom_call_import.UNRESOLVED)
        self.assertIn(zoom_call_import.UNRESOLVED_AMBIGUOUS, resolution.reason)


class RunImportTest(TestCase):
    def _root(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _summary_file(tmp.name, "2026-10-12", "mariano-beccaria", "Mariano Beccaria")
        _summary_file(tmp.name, "2026-10-09", "unknown-person", "Unknown Person")
        return tmp.name

    def test_dry_run_writes_nothing_and_classifies(self):
        client = StubClient(users={"m@example.com": ("Mariano", "Beccaria")})
        result = run_import(root=self._root(), client=client, apply=False)
        self.assertEqual(client.payloads, [])
        self.assertFalse(Call.objects.exists())
        self.assertEqual(result.would_create, 1)
        self.assertEqual(result.skipped_unresolved, 1)
        self.assertEqual(len(result.skipped_files), 0)

    def test_dry_run_reports_would_update_for_existing_refs(self):
        client = StubClient(
            users={"m@example.com": ("Mariano", "Beccaria")},
            existing_refs={"2026-10-12-mariano-beccaria-summary"},
        )
        result = run_import(root=self._root(), client=client, apply=False)
        self.assertEqual(result.would_update, 1)
        self.assertEqual(result.would_create, 0)

    def test_apply_records_zoom_source_with_stem_ref_at_noon(self):
        client = StubClient(users={"m@example.com": ("Mariano", "Beccaria")})
        result = run_import(root=self._root(), client=client, apply=True)
        self.assertEqual(len(client.payloads), 1)
        payload = client.payloads[0]
        self.assertEqual(payload["source"], "zoom")
        self.assertEqual(payload["source_ref"], "2026-10-12-mariano-beccaria-summary")
        self.assertEqual(payload["email"], "m@example.com")
        self.assertEqual(payload["occurred_at"], "2026-10-12T12:00:00+00:00")
        self.assertIn("portfolio project", payload["summary"])
        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.skipped_unresolved, 1)

    def test_rerun_classifies_update_not_create(self):
        client = StubClient(
            users={"m@example.com": ("Mariano", "Beccaria")},
            existing_refs={"2026-10-12-mariano-beccaria-summary"},
        )
        result = run_import(root=self._root(), client=client, apply=True)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.created, 0)


class CommandTest(TestCase):
    def _run(self, root, stub, *, apply=False, aliases=None):
        client_factory = patch.object(
            zoom_call_import, "CrmApiClient", return_value=stub,
        )
        with client_factory:
            out = io.StringIO()
            args = [f"--path={root}"]
            if apply:
                args.append("--apply")
            if aliases:
                args.extend(["--aliases", aliases])
            call_command("import_zoom_calls", *args, stdout=out)
            return out.getvalue()

    def test_dry_run_is_default_and_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            _summary_file(tmp, "2026-10-12", "mariano-beccaria", "Mariano Beccaria")
            stub = StubClient(users={"m@example.com": ("Mariano", "Beccaria")})
            output = self._run(tmp, stub)
            self.assertEqual(stub.payloads, [])
            self.assertFalse(Call.objects.exists())
            self.assertIn("Dry run (wrote nothing)", output)
            self.assertIn("would_create=1", output)

    def test_apply_creates_call_and_reports(self):
        with TemporaryDirectory() as tmp:
            _summary_file(tmp, "2026-10-12", "mariano-beccaria", "Mariano Beccaria")
            stub = StubClient(users={"m@example.com": ("Mariano", "Beccaria")})
            output = self._run(tmp, stub, apply=True)
            self.assertEqual(len(stub.payloads), 1)
            self.assertIn("created=1", output)

    def test_aliases_file_is_loaded(self):
        with TemporaryDirectory() as tmp:
            _summary_file(tmp, "2026-10-02", "kalavati-singh", "Kalavati Singh")
            aliases_path = Path(tmp) / "aliases.json"
            aliases_path.write_text(
                json.dumps({"kalavati singh": "kala@resolved.com"}),
                encoding="utf-8",
            )
            stub = StubClient()
            output = self._run(tmp, stub, apply=True, aliases=str(aliases_path))
            self.assertEqual(stub.payloads[0]["email"], "kala@resolved.com")
            self.assertIn("created=1", output)

    def test_missing_1x1_dir_fails_with_command_error(self):
        with TemporaryDirectory() as tmp:
            stub = StubClient()
            with self.assertRaises(CommandError):
                self._run(tmp, stub)


class CrmApiClientAuthHeaderTest(TestCase):
    def test_client_uses_token_scheme(self):
        client = CrmApiClient(base_url="https://example.com", token="abc")
        self.assertEqual(client._headers()["Authorization"], "Token abc")

    def test_token_resolution_requires_token(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(
            zoom_call_import, "_read_env_file_value", return_value=None,
        ):
            with self.assertRaises(RuntimeError):
                zoom_call_import.resolve_staff_token(env={})
