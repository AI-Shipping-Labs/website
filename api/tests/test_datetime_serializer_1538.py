"""Ownership and behavior tests for nullable datetime serialization."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from django.test import SimpleTestCase

from api.serializers.datetime import isoformat_or_none

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_HELPER = REPOSITORY_ROOT / "api" / "serializers" / "datetime.py"
DISALLOWED_LOCAL_HELPERS = {"_isoformat_or_none", "_iso", "isoformat_or_none"}
CANONICAL_CALLERS = {
    "api/serializers/books.py",
    "api/serializers/crm.py",
    "api/serializers/onboarding.py",
    "api/serializers/plans.py",
    "api/serializers/users.py",
    "api/serializers/worker.py",
    "api/views/campaigns.py",
    "api/views/contacts.py",
    "api/views/crm_export.py",
    "api/views/event_series.py",
    "api/views/events.py",
    "api/views/hosts.py",
    "api/views/marketing_pages.py",
    "api/views/redirects.py",
    "api/views/sprints.py",
    "api/views/sync_sources.py",
    "api/views/triggers.py",
    "api/views/utm_campaigns.py",
    "member_api/serializers/plans.py",
}


def _production_python_files():
    for package in ("api", "member_api"):
        for path in (REPOSITORY_ROOT / package).rglob("*.py"):
            if path == CANONICAL_HELPER:
                continue
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            yield path


class IsoformatOrNoneBehaviorTest(SimpleTestCase):
    def test_none_remains_none(self):
        self.assertIsNone(isoformat_or_none(None))

    def test_aware_datetime_uses_native_isoformat(self):
        value = datetime(2026, 9, 9, 12, 34, 56, 789012, tzinfo=UTC)

        self.assertEqual(isoformat_or_none(value), value.isoformat())

    def test_falsy_non_null_value_is_serialized(self):
        class FalsyDatetime:
            def __bool__(self):
                return False

            def isoformat(self):
                return "serialized-falsy-value"

        self.assertEqual(
            isoformat_or_none(FalsyDatetime()),
            "serialized-falsy-value",
        )


class NullableDatetimeOwnershipTest(SimpleTestCase):
    def test_production_packages_have_no_local_named_copies(self):
        copies = []
        for path in _production_python_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in DISALLOWED_LOCAL_HELPERS
                ):
                    copies.append(
                        f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}:{node.name}"
                    )

        self.assertEqual(copies, [])

    def test_named_callers_import_canonical_helper_without_alias(self):
        missing_imports = []
        for relative_path in sorted(CANONICAL_CALLERS):
            path = REPOSITORY_ROOT / relative_path
            tree = ast.parse(path.read_text(), filename=str(path))
            imports_canonical = any(
                isinstance(node, ast.ImportFrom)
                and node.module == "api.serializers.datetime"
                and any(
                    alias.name == "isoformat_or_none" and alias.asname is None
                    for alias in node.names
                )
                for node in tree.body
            )
            if not imports_canonical:
                missing_imports.append(relative_path)

        self.assertEqual(missing_imports, [])
