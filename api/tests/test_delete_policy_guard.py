"""Guard tests for the no-deletes-via-API policy (issue #864).

Two guards enforce ``_docs/api-delete-policy.md``:

1. ``ForbiddenDeleteRoutesTest`` — every canonical-content route returns HTTP
   405 with its ``*_delete_not_available`` code and a "use Studio" message.
2. ``DeleteHandlerClassificationGuardTest`` — greps ``api/views/`` for every
   ``require_methods(... "DELETE" ...)`` handler and asserts it is classified in
   ``api/delete_policy.py`` (forbidden -> must 405; legitimate -> allow-listed).
   A new, unclassified DELETE handler fails CI until it is classified, so a
   canonical-content delete cannot be silently reintroduced through the API.
"""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Token
from api.delete_policy import (
    FORBIDDEN_DELETE_HANDLERS,
    LEGITIMATE_DELETE_HANDLERS,
)

User = get_user_model()

API_VIEWS_DIR = Path(__file__).resolve().parent.parent / "views"


def _iter_delete_handlers():
    """Yield ``(module_stem, func_name)`` for every DELETE handler in views.

    A "DELETE handler" is a ``def`` whose nearest preceding
    ``@require_methods(...)`` decorator includes ``DELETE``. We scan each view
    module line by line so multi-line ``@openapi_spec(...)`` blocks between the
    decorator and the ``def`` do not hide the function name.
    """
    func_def_re = re.compile(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
    require_re = re.compile(r"@require_methods\(([^)]*)\)")

    for path in sorted(API_VIEWS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        pending_delete = False
        for line in lines:
            stripped = line.strip()
            match = require_re.match(stripped)
            if match:
                args = match.group(1)
                pending_delete = "DELETE" in args
                continue
            func_match = func_def_re.match(line)
            if func_match and pending_delete:
                yield (path.stem, func_match.group(1))
                pending_delete = False


class DeleteHandlerDiscoveryTest(TestCase):
    """Sanity check: the scanner finds the handlers we already know about."""

    def test_scanner_finds_known_handlers(self):
        found = set(f"{stem}.{name}" for stem, name in _iter_delete_handlers())
        # A representative forbidden handler and a representative legitimate one.
        self.assertIn("events.event_detail", found)
        self.assertIn("plan_items.resource_detail", found)
        self.assertIn("aliases.user_aliases_remove", found)


class DeleteHandlerClassificationGuardTest(TestCase):
    """Every DELETE handler must be classified; nothing may be unclassified."""

    def test_every_delete_handler_is_classified(self):
        forbidden = set(FORBIDDEN_DELETE_HANDLERS)
        legitimate = set(LEGITIMATE_DELETE_HANDLERS)
        overlap = forbidden & legitimate
        self.assertEqual(
            overlap,
            set(),
            f"Handlers classified as BOTH forbidden and legitimate: {overlap}",
        )

        classified = forbidden | legitimate
        found = {f"{stem}.{name}" for stem, name in _iter_delete_handlers()}

        unclassified = found - classified
        self.assertEqual(
            unclassified,
            set(),
            "Unclassified DELETE handler(s) found in api/views/. Add each to "
            "api/delete_policy.py: either return 405 (FORBIDDEN_DELETE_HANDLERS) "
            "or allow-list as a legitimate deleter (LEGITIMATE_DELETE_HANDLERS), "
            f"and update _docs/api-delete-policy.md. Unclassified: {unclassified}",
        )

        stale = classified - found
        self.assertEqual(
            stale,
            set(),
            "delete_policy.py lists handler(s) that no longer exist as DELETE "
            f"handlers in api/views/: {stale}",
        )


class ForbiddenDeleteRoutesTest(TestCase):
    """Each canonical-content route returns 405 with the right code."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="delete-guard-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="delete-guard")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_forbidden_routes_return_405_with_code_and_studio_message(self):
        # (path, expected *_delete_not_available code). Slugs/ids need not exist;
        # the 405 fires before any lookup.
        routes = [
            (reverse("api_events_collection"), "event_delete_not_available"),
            (
                reverse("api_event_detail", args=["any-slug"]),
                "event_delete_not_available",
            ),
            (
                reverse("api_event_series_collection"),
                "series_delete_not_available",
            ),
            (
                reverse("api_event_series_detail", args=[1]),
                "series_delete_not_available",
            ),
            (
                reverse(
                    "api_event_series_occurrence_detail",
                    args=[1, 1],
                ),
                "occurrence_delete_not_available",
            ),
            (
                reverse("api_sync_sources_collection"),
                "sync_source_delete_not_available",
            ),
            (
                reverse(
                    "api_sync_source_trigger",
                    args=["00000000-0000-0000-0000-000000000000"],
                ),
                "sync_source_delete_not_available",
            ),
        ]

        for path, expected_code in routes:
            with self.subTest(path=path):
                response = self.client.delete(path, **self._auth())
                self.assertEqual(response.status_code, 405)
                body = response.json()
                self.assertEqual(body["code"], expected_code)
                self.assertIn("Studio", body["error"])
