"""Tests for the cleanup-gate diagnostics endpoint (issue #1087).

Covers ``GET /api/diagnostics/cleanup-gates``:

- Auth: anon 401 (JSON, not a login redirect), non-staff token 401.
- Method: non-GET 405.
- Each of the three counts equals its exact queryset against seeded data,
  with rows that must be EXCLUDED present in the fixture so the assertions
  fail if a filter is dropped.
- ``completed_future_events`` uses timezone-aware ``timezone.now()`` (a
  one-minute-future completed event is counted; a one-minute-past one is
  not).
- The endpoint is read-only (no rows written).
- The endpoint is documented in the generated OpenAPI spec under the
  ``Diagnostics`` tag, and ``generate_openapi --check`` exits 0.

Token fixtures mirror ``api/tests/test_openapi.py``: a staff token via the
manager, and a non-staff token constructed directly (the manager's
staff-only validator would otherwise reject it).
"""

import io
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from api.openapi import OPENAPI_SPEC_ATTR, build_spec
from api.urls import urlpatterns
from content.models import (
    Course,
    Module,
    Unit,
    UserCourseProgress,
    Workshop,
)
from events.models import Event

User = get_user_model()

URL = "/api/diagnostics/cleanup-gates"


def _make_unit(course, sort_order):
    module = Module.objects.create(
        course=course,
        title=f"Module {sort_order}",
        slug=f"module-{sort_order}",
        sort_order=sort_order,
    )
    return Unit.objects.create(
        module=module,
        title=f"Unit {sort_order}",
        slug=f"unit-{sort_order}",
        sort_order=sort_order,
    )


class CleanupGatesAuthTest(TestCase):
    """Auth + method boundary (no fixture data needed)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="staff-tok")
        cls.non_staff_token = Token(
            key="non-staff-token-key",
            user=cls.member,
            name="legacy-non-staff",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def test_anonymous_gets_json_401_not_login_redirect(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(), {"error": "Authentication token required"},
        )

    def test_non_staff_token_gets_401(self):
        response = self.client.get(
            URL, HTTP_AUTHORIZATION=f"Token {self.non_staff_token.key}",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_unknown_token_gets_401(self):
        response = self.client.get(
            URL, HTTP_AUTHORIZATION="Token does-not-exist",
        )
        self.assertEqual(response.status_code, 401)

    def test_staff_token_gets_200(self):
        response = self.client.get(
            URL, HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self):
        response = self.client.post(
            URL, HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "Method not allowed"})

    def test_put_returns_405(self):
        response = self.client.put(
            URL, HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )
        self.assertEqual(response.status_code, 405)

    def test_unauthenticated_non_get_returns_401_not_405(self):
        # ``token_required`` is outermost, so auth is checked before the
        # method gate: an anonymous POST must 401, not 405.
        response = self.client.post(URL)
        self.assertEqual(response.status_code, 401)


class CleanupGatesCountsTest(TestCase):
    """Each count isolates only its target rows against a mixed fixture."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="staff-tok")

        learner = User.objects.create_user(
            email="learner@test.com", password="pw",
        )
        course = Course.objects.create(title="Course", slug="course")

        # --- null_completed_unit_progress: 2 null-completed, 1 completed ---
        unit_a = _make_unit(course, 1)
        unit_b = _make_unit(course, 2)
        unit_c = _make_unit(course, 3)
        UserCourseProgress.objects.create(
            user=learner, unit=unit_a, completed_at=None,
        )
        UserCourseProgress.objects.create(
            user=learner, unit=unit_b, completed_at=None,
        )
        # Completed row MUST be excluded by ``completed_at__isnull=True``.
        UserCourseProgress.objects.create(
            user=learner, unit=unit_c, completed_at=timezone.now(),
        )

        # --- workshops_missing_content_id: 3 missing, 2 present ---
        for i in range(3):
            Workshop.objects.create(
                slug=f"ws-missing-{i}",
                title=f"Missing {i}",
                date=date(2026, 1, 1),
                content_id=None,
            )
        for i in range(2):
            # Present content_id MUST be excluded by content_id__isnull=True.
            Workshop.objects.create(
                slug=f"ws-has-{i}",
                title=f"Has {i}",
                date=date(2026, 1, 1),
                content_id=uuid.uuid4(),
            )

        # --- completed_future_events: only completed + future counts ---
        now = timezone.now()
        # Counted: completed + future.
        Event.objects.create(
            slug="completed-future-1",
            title="Completed future 1",
            start_datetime=now + timedelta(days=3),
            status="completed",
        )
        Event.objects.create(
            slug="completed-future-2",
            title="Completed future 2",
            start_datetime=now + timedelta(days=10),
            status="completed",
        )
        # Excluded: completed but in the past.
        Event.objects.create(
            slug="completed-past",
            title="Completed past",
            start_datetime=now - timedelta(days=3),
            status="completed",
        )
        # Excluded: future but not completed.
        Event.objects.create(
            slug="upcoming-future",
            title="Upcoming future",
            start_datetime=now + timedelta(days=3),
            status="upcoming",
        )
        # Excluded: cancelled (even if future).
        Event.objects.create(
            slug="cancelled-future",
            title="Cancelled future",
            start_datetime=now + timedelta(days=3),
            status="cancelled",
        )

    def _get(self):
        response = self.client.get(
            URL, HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_body_has_exactly_the_documented_keys(self):
        body = self._get()
        self.assertEqual(
            set(body.keys()),
            {
                "null_completed_unit_progress",
                "workshops_missing_content_id",
                "completed_future_events",
                "generated_at",
            },
        )

    def test_counts_are_non_negative_integers(self):
        body = self._get()
        for key in (
            "null_completed_unit_progress",
            "workshops_missing_content_id",
            "completed_future_events",
        ):
            self.assertIsInstance(body[key], int)
            self.assertGreaterEqual(body[key], 0)

    def test_null_completed_unit_progress_excludes_completed_rows(self):
        body = self._get()
        expected = UserCourseProgress.objects.filter(
            unit__isnull=False, completed_at__isnull=True,
        ).count()
        self.assertEqual(expected, 2)
        self.assertEqual(body["null_completed_unit_progress"], expected)

    def test_workshops_missing_content_id_excludes_workshops_with_id(self):
        body = self._get()
        expected = Workshop.objects.filter(content_id__isnull=True).count()
        self.assertEqual(expected, 3)
        self.assertEqual(body["workshops_missing_content_id"], expected)

    def test_completed_future_events_excludes_past_upcoming_cancelled(self):
        body = self._get()
        expected = Event.objects.filter(
            status="completed", start_datetime__gt=timezone.now(),
        ).count()
        self.assertEqual(expected, 2)
        self.assertEqual(body["completed_future_events"], expected)

    def test_generated_at_is_tz_aware_iso8601(self):
        from django.utils.dateparse import parse_datetime

        body = self._get()
        parsed = parse_datetime(body["generated_at"])
        self.assertIsNotNone(parsed, body["generated_at"])
        # tz-aware -> utcoffset is not None.
        self.assertIsNotNone(parsed.utcoffset())

    def test_endpoint_is_read_only(self):
        counts_before = (
            UserCourseProgress.objects.count(),
            Workshop.objects.count(),
            Event.objects.count(),
        )
        self._get()
        counts_after = (
            UserCourseProgress.objects.count(),
            Workshop.objects.count(),
            Event.objects.count(),
        )
        self.assertEqual(counts_before, counts_after)


class CleanupGatesCleanStateTest(TestCase):
    """When every gate is satisfied, all three counts are 0 (proceed signal)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="staff-tok")

        learner = User.objects.create_user(
            email="learner@test.com", password="pw",
        )
        course = Course.objects.create(title="Course", slug="course")
        unit = _make_unit(course, 1)
        # Completed progress only.
        UserCourseProgress.objects.create(
            user=learner, unit=unit, completed_at=timezone.now(),
        )
        # Workshop with a content_id only.
        Workshop.objects.create(
            slug="ws-clean", title="Clean", date=date(2026, 1, 1),
            content_id=uuid.uuid4(),
        )
        # No completed-future events: a completed-past and an upcoming-future.
        now = timezone.now()
        Event.objects.create(
            slug="done", title="Done",
            start_datetime=now - timedelta(days=1), status="completed",
        )
        Event.objects.create(
            slug="soon", title="Soon",
            start_datetime=now + timedelta(days=1), status="upcoming",
        )

    def test_all_counts_zero(self):
        response = self.client.get(
            URL, HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["null_completed_unit_progress"], 0)
        self.assertEqual(body["workshops_missing_content_id"], 0)
        self.assertEqual(body["completed_future_events"], 0)


class CleanupGatesTimezoneBoundaryTest(TestCase):
    """``completed_future_events`` uses tz-aware now at request time."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="staff-tok")
        now = timezone.now()
        Event.objects.create(
            slug="one-min-future", title="One min future",
            start_datetime=now + timedelta(minutes=1), status="completed",
        )
        Event.objects.create(
            slug="one-min-past", title="One min past",
            start_datetime=now - timedelta(minutes=1), status="completed",
        )

    def test_only_future_completed_event_counted(self):
        response = self.client.get(
            URL, HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["completed_future_events"], 1)


class CleanupGatesOpenApiTest(TestCase):
    """The endpoint is documented under the ``Diagnostics`` tag."""

    @classmethod
    def setUpTestData(cls):
        cls.document = build_spec(urlpatterns)

    def test_path_present_with_get_operation(self):
        self.assertIn("/api/diagnostics/cleanup-gates", self.document["paths"])
        operations = self.document["paths"]["/api/diagnostics/cleanup-gates"]
        self.assertIn("get", operations)
        self.assertEqual(set(operations.keys()), {"get"})

    def test_operation_tagged_diagnostics(self):
        get_op = (
            self.document["paths"]["/api/diagnostics/cleanup-gates"]["get"]
        )
        self.assertEqual(get_op["tags"], ["Diagnostics"])

    def test_200_example_documents_all_keys(self):
        example = (
            self.document["paths"]["/api/diagnostics/cleanup-gates"]["get"]
            ["responses"]["200"]["content"]["application/json"]["example"]
        )
        self.assertEqual(
            set(example.keys()),
            {
                "null_completed_unit_progress",
                "workshops_missing_content_id",
                "completed_future_events",
                "generated_at",
            },
        )

    def test_view_carries_openapi_spec_attribute(self):
        from api.views.cleanup_gates import cleanup_gates_diagnostics

        spec = getattr(cleanup_gates_diagnostics, OPENAPI_SPEC_ATTR, None)
        self.assertIsNotNone(spec)
        self.assertEqual(spec["tag"], "Diagnostics")
        self.assertEqual(set(spec["methods"].keys()), {"GET"})

    def test_generate_openapi_check_passes_on_clean_tree(self):
        out = io.StringIO()
        call_command("generate_openapi", "--check", stdout=out)
        self.assertIn("up to date", out.getvalue())
