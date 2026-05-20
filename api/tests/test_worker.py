"""Tests for the read-only worker task API (issue #714).

Covers all three endpoints:

* ``GET /api/worker/tasks/<task_id>``
* ``GET /api/worker/tasks/failed``
* ``GET /api/worker/tasks``

Plus the cross-cutting requirements:

* ``@token_required`` -- 401 without/with bad/non-staff tokens.
* ``args``/``kwargs``/``result`` come back as pprint strings, not
  pickled bytes.
* ``error_summary`` matches the Studio collapsed-row heuristic.
* The whole surface is read-only -- a sweep of every endpoint leaves
  ``Task.objects.count()`` unchanged.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django_q.models import Task

from accounts.models import Token

User = get_user_model()


def _create_task(**kwargs):
    """Create a django-q ``Task`` with a unique 32-char hex id."""
    if "id" not in kwargs:
        kwargs["id"] = uuid.uuid4().hex
    return Task.objects.create(**kwargs)


class WorkerApiAuthTest(TestCase):
    """Every endpoint requires a staff token.

    We mirror the same matrix the other ``api/tests/test_*`` files use:
    no header, malformed header, unknown token, non-staff token.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-worker@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-worker@test.com", password="pw", is_staff=False,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="staff")
        # Non-staff token: a token that USED to belong to a staff member
        # and is now owned by a regular user (rotation scenario).
        cls.non_staff_token = Token(
            key="non-staff-worker-token",
            user=cls.member,
            name="member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])
        cls.task = _create_task(
            name="seed-task",
            func="f",
            started=timezone.now(),
            stopped=timezone.now(),
            success=True,
        )

    def _urls(self):
        return [
            "/api/worker/tasks",
            "/api/worker/tasks/failed",
            f"/api/worker/tasks/{self.task.id}",
        ]

    def test_missing_header_returns_401(self):
        for url in self._urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json(),
                    {"error": "Authentication token required"},
                )

    def test_unknown_token_returns_401(self):
        for url in self._urls():
            with self.subTest(url=url):
                response = self.client.get(
                    url,
                    HTTP_AUTHORIZATION="Token does-not-exist",
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_non_staff_token_returns_401(self):
        for url in self._urls():
            with self.subTest(url=url):
                response = self.client.get(
                    url,
                    HTTP_AUTHORIZATION=f"Token {self.non_staff_token.key}",
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_non_get_methods_return_405(self):
        # The surface is read-only; every other method must 405.
        for url in self._urls():
            for method in ("post", "patch", "delete", "put"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(
                        url,
                        HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
                    )
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(
                        response.json(), {"error": "Method not allowed"},
                    )


class WorkerTaskDetailTest(TestCase):
    """``GET /api/worker/tasks/<task_id>``."""

    TRACEBACK_RESULT = (
        "Traceback (most recent call last):\n"
        '  File "/app/integrations/services/github.py", line 42, in sync\n'
        "    response = client.get(url)\n"
        "RuntimeError: GitHub API rate limit exceeded"
    )

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-detail@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="detail")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_unknown_task_returns_404(self):
        response = self.client.get(
            "/api/worker/tasks/" + ("0" * 32),
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Task not found"})

    def test_successful_task_serializes_full_detail(self):
        started = timezone.now() - timedelta(seconds=30)
        stopped = started + timedelta(seconds=5)
        # Use a hook string that won't trigger django-q's post-save signal
        # to actually try to import + call it (would log an error). The
        # signal swallows ImportError silently but logs malformed entries.
        # We leave ``hook=None`` here and assert that path; another test
        # could exercise a string hook but we don't need to for this AC.
        task = _create_task(
            name="sync-content-2026-05-20",
            func="integrations.services.github.sync",
            hook=None,
            args=("content-repo",),
            kwargs={"force": True},
            result={"files_updated": 7},
            group="content-sync",
            cluster="default",
            started=started,
            stopped=stopped,
            success=True,
            attempt_count=1,
        )
        response = self.client.get(
            f"/api/worker/tasks/{task.id}",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["task_id"], task.id)
        self.assertEqual(data["name"], "sync-content-2026-05-20")
        self.assertEqual(data["function"], "integrations.services.github.sync")
        self.assertIsNone(data["hook"])
        self.assertEqual(data["group"], "content-sync")
        self.assertEqual(data["cluster"], "default")
        self.assertEqual(data["attempt_count"], 1)
        self.assertTrue(data["success"])
        self.assertEqual(data["started_at"], started.isoformat())
        self.assertEqual(data["stopped_at"], stopped.isoformat())
        self.assertEqual(data["duration_seconds"], 5.0)
        # args/kwargs/result must come back as pprint strings, NEVER
        # the raw Python value (would fail JSON encoding) and NEVER
        # the pickled bytes.
        self.assertIsInstance(data["args"], str)
        self.assertIn("content-repo", data["args"])
        self.assertIsInstance(data["kwargs"], str)
        self.assertIn("force", data["kwargs"])
        self.assertIn("True", data["kwargs"])
        self.assertIsInstance(data["result"], str)
        self.assertIn("files_updated", data["result"])
        # No failure payload on a success row.
        self.assertIsNone(data["error"])
        self.assertIsNone(data["traceback"])
        self.assertFalse(data["is_traceback"])

    def test_failed_task_with_traceback_populates_error_and_traceback(self):
        started = timezone.now() - timedelta(seconds=60)
        stopped = started + timedelta(seconds=10)
        task = _create_task(
            name="broken-sync",
            func="integrations.services.github.sync",
            started=started,
            stopped=stopped,
            success=False,
            result=self.TRACEBACK_RESULT,
        )
        response = self.client.get(
            f"/api/worker/tasks/{task.id}",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["success"])
        # ``error`` is the last non-blank line of the traceback.
        self.assertEqual(
            data["error"],
            "RuntimeError: GitHub API rate limit exceeded",
        )
        # ``traceback`` is the full result text, with the banner intact.
        self.assertEqual(data["traceback"], self.TRACEBACK_RESULT)
        self.assertTrue(data["is_traceback"])
        # On failure we surface the payload through error/traceback and
        # leave ``result`` null so clients only have to look in one place.
        self.assertIsNone(data["result"])

    def test_failed_task_with_plain_string_has_no_traceback(self):
        task = _create_task(
            name="plain-error",
            func="f",
            started=timezone.now(),
            stopped=timezone.now(),
            success=False,
            result="ValueError: something went wrong",
        )
        response = self.client.get(
            f"/api/worker/tasks/{task.id}",
            **self._auth(),
        )
        data = response.json()
        self.assertEqual(data["error"], "ValueError: something went wrong")
        self.assertIsNone(data["traceback"])
        self.assertFalse(data["is_traceback"])

    def test_args_and_kwargs_empty_string_when_none(self):
        # django-q stores None for tasks called with no args/kwargs; the
        # serializer must collapse that to '' so JSON consumers don't
        # have to None-check before rendering.
        task = _create_task(
            name="no-args",
            func="f",
            started=timezone.now(),
            stopped=timezone.now(),
            success=True,
            args=None,
            kwargs=None,
        )
        response = self.client.get(
            f"/api/worker/tasks/{task.id}",
            **self._auth(),
        )
        data = response.json()
        self.assertEqual(data["args"], "")
        self.assertEqual(data["kwargs"], "")

    def test_duration_seconds_is_positive_float(self):
        # ``Task.started`` and ``Task.stopped`` are NOT NULL in the
        # django-q schema, so duration is always derivable in practice.
        # The serializer's ``None`` branch is purely defensive (in case
        # django-q changes the schema) and is unit-tested below.
        started = timezone.now() - timedelta(seconds=12)
        stopped = started + timedelta(seconds=7)
        task = _create_task(
            name="t",
            func="f",
            started=started,
            stopped=stopped,
            success=True,
        )
        response = self.client.get(
            f"/api/worker/tasks/{task.id}",
            **self._auth(),
        )
        self.assertAlmostEqual(response.json()["duration_seconds"], 7.0, places=1)

    def test_serializer_duration_is_none_when_started_or_stopped_missing(self):
        # Direct serializer call -- the model layer rejects NULL on
        # started/stopped so we can't go through the HTTP layer for
        # this branch.
        from types import SimpleNamespace

        from api.serializers.worker import serialize_task_detail

        fake = SimpleNamespace(
            id="x" * 32,
            name="n",
            func="f",
            hook=None,
            args=None,
            kwargs=None,
            result=None,
            group=None,
            cluster=None,
            started=None,
            stopped=timezone.now(),
            success=True,
            attempt_count=0,
        )
        self.assertIsNone(serialize_task_detail(fake)["duration_seconds"])


class WorkerTasksFailedListTest(TestCase):
    """``GET /api/worker/tasks/failed``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-failed@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="failed")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_returns_only_failed_tasks_newest_first(self):
        now = timezone.now()
        # Two failures + one success. Failed list must skip the success
        # row and order the two failures by ``started DESC``.
        _create_task(
            name="ok", func="f", started=now, stopped=now, success=True,
        )
        older = _create_task(
            name="older-failure",
            func="f",
            started=now - timedelta(minutes=10),
            stopped=now - timedelta(minutes=9),
            success=False,
            result="RuntimeError: stale",
        )
        newer = _create_task(
            name="newer-failure",
            func="f",
            started=now - timedelta(minutes=1),
            stopped=now - timedelta(seconds=30),
            success=False,
            result="RuntimeError: fresh",
        )
        response = self.client.get("/api/worker/tasks/failed", **self._auth())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        ids = [row["task_id"] for row in data["tasks"]]
        self.assertEqual(ids, [newer.id, older.id])
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["limit"], 20)
        # Every row carries a one-line error_summary.
        self.assertEqual(data["tasks"][0]["error_summary"], "RuntimeError: fresh")
        self.assertEqual(data["tasks"][1]["error_summary"], "RuntimeError: stale")

    def test_error_summary_uses_last_line_for_tracebacks(self):
        now = timezone.now()
        _create_task(
            name="t",
            func="f",
            started=now,
            stopped=now,
            success=False,
            result=(
                "Traceback (most recent call last):\n"
                '  File "x.py", line 1, in <module>\n'
                "TimeoutError: deadline exceeded"
            ),
        )
        response = self.client.get("/api/worker/tasks/failed", **self._auth())
        self.assertEqual(
            response.json()["tasks"][0]["error_summary"],
            "TimeoutError: deadline exceeded",
        )

    def test_limit_clamped_to_200(self):
        # Asking for more than the cap silently clamps to LIMIT_MAX so
        # an agent passing ``limit=1000`` gets the cap, not 422.
        response = self.client.get(
            "/api/worker/tasks/failed?limit=201",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["limit"], 200)

    def test_limit_honored_when_below_cap(self):
        now = timezone.now()
        for i in range(5):
            _create_task(
                name=f"f{i}",
                func="f",
                started=now - timedelta(seconds=i),
                stopped=now,
                success=False,
                result=f"E{i}",
            )
        response = self.client.get(
            "/api/worker/tasks/failed?limit=2",
            **self._auth(),
        )
        data = response.json()
        self.assertEqual(data["limit"], 2)
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["tasks"]), 2)

    def test_default_limit_is_20(self):
        response = self.client.get("/api/worker/tasks/failed", **self._auth())
        self.assertEqual(response.json()["limit"], 20)

    def test_since_filters_to_recent_failures(self):
        now = timezone.now()
        _create_task(
            name="old",
            func="f",
            started=now - timedelta(hours=2),
            stopped=now - timedelta(hours=2),
            success=False,
            result="old",
        )
        recent = _create_task(
            name="recent",
            func="f",
            started=now - timedelta(minutes=5),
            stopped=now - timedelta(minutes=4),
            success=False,
            result="recent",
        )
        cutoff = quote((now - timedelta(hours=1)).isoformat())
        response = self.client.get(
            f"/api/worker/tasks/failed?since={cutoff}",
            **self._auth(),
        )
        ids = [row["task_id"] for row in response.json()["tasks"]]
        self.assertEqual(ids, [recent.id])

    def test_invalid_since_returns_422(self):
        response = self.client.get(
            "/api/worker/tasks/failed?since=not-a-date",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["field"], "since")

    def test_invalid_limit_returns_422(self):
        for bad in ("abc", "-1", "0"):
            with self.subTest(limit=bad):
                response = self.client.get(
                    f"/api/worker/tasks/failed?limit={bad}",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "validation_error")


class WorkerTasksCollectionTest(TestCase):
    """``GET /api/worker/tasks``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-collection@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="collection")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _seed_mixed(self):
        now = timezone.now()
        rows = [
            _create_task(
                name="ok-a",
                func="f",
                group="alpha",
                started=now - timedelta(minutes=1),
                stopped=now,
                success=True,
            ),
            _create_task(
                name="ok-b",
                func="f",
                group="beta",
                started=now - timedelta(minutes=2),
                stopped=now,
                success=True,
            ),
            _create_task(
                name="bad-a",
                func="f",
                group="alpha",
                started=now - timedelta(minutes=3),
                stopped=now,
                success=False,
                result="E1",
            ),
            _create_task(
                name="bad-b",
                func="f",
                group="beta",
                started=now - timedelta(minutes=4),
                stopped=now,
                success=False,
                result="E2",
            ),
        ]
        return rows

    def test_default_returns_all_statuses_newest_first(self):
        rows = self._seed_mixed()
        response = self.client.get("/api/worker/tasks", **self._auth())
        self.assertEqual(response.status_code, 200)
        ids = [r["task_id"] for r in response.json()["tasks"]]
        # The seed was inserted in ``started DESC`` order, so the response
        # should echo that ordering exactly.
        self.assertEqual(ids, [r.id for r in rows])
        self.assertEqual(response.json()["limit"], 50)

    def test_status_success_filters_to_successful(self):
        self._seed_mixed()
        response = self.client.get(
            "/api/worker/tasks?status=success", **self._auth(),
        )
        data = response.json()
        self.assertTrue(all(row["success"] for row in data["tasks"]))
        self.assertEqual(data["count"], 2)

    def test_status_failed_filters_to_failed(self):
        self._seed_mixed()
        response = self.client.get(
            "/api/worker/tasks?status=failed", **self._auth(),
        )
        data = response.json()
        self.assertTrue(all(not row["success"] for row in data["tasks"]))
        self.assertEqual(data["count"], 2)

    def test_status_all_equivalent_to_default(self):
        self._seed_mixed()
        default = self.client.get("/api/worker/tasks", **self._auth()).json()
        all_ = self.client.get(
            "/api/worker/tasks?status=all", **self._auth(),
        ).json()
        self.assertEqual(default["tasks"], all_["tasks"])

    def test_unknown_status_returns_422(self):
        response = self.client.get(
            "/api/worker/tasks?status=other", **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["field"], "status")
        self.assertEqual(
            sorted(body["details"]["allowed"]),
            ["all", "failed", "success"],
        )

    def test_group_filters_to_exact_match(self):
        self._seed_mixed()
        response = self.client.get(
            "/api/worker/tasks?group=alpha", **self._auth(),
        )
        groups = {row["group"] for row in response.json()["tasks"]}
        self.assertEqual(groups, {"alpha"})

    def test_since_filters_to_recent(self):
        now = timezone.now()
        _create_task(
            name="old",
            func="f",
            started=now - timedelta(hours=2),
            stopped=now - timedelta(hours=2),
            success=True,
        )
        recent = _create_task(
            name="recent",
            func="f",
            started=now - timedelta(minutes=5),
            stopped=now - timedelta(minutes=4),
            success=True,
        )
        cutoff = quote((now - timedelta(hours=1)).isoformat())
        response = self.client.get(
            f"/api/worker/tasks?since={cutoff}", **self._auth(),
        )
        ids = [row["task_id"] for row in response.json()["tasks"]]
        self.assertEqual(ids, [recent.id])

    def test_limit_clamped_to_200(self):
        response = self.client.get(
            "/api/worker/tasks?limit=999", **self._auth(),
        )
        self.assertEqual(response.json()["limit"], 200)

    def test_since_with_z_suffix(self):
        # ISO-8601 commonly uses ``Z`` for UTC; ``fromisoformat`` accepts
        # it on Python 3.11+ and we substitute ``+00:00`` defensively.
        now = timezone.now()
        _create_task(
            name="t",
            func="f",
            started=now,
            stopped=now,
            success=True,
        )
        response = self.client.get(
            "/api/worker/tasks?since=2020-01-01T00:00:00Z",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)


class WorkerApiReadOnlyTest(TestCase):
    """The whole API surface is read-only.

    Sweeping every endpoint must leave the ``Task`` table untouched.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-readonly@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="readonly")
        now = timezone.now()
        cls.success = _create_task(
            name="ok",
            func="f",
            started=now,
            stopped=now,
            success=True,
            result="done",
        )
        cls.failure = _create_task(
            name="bad",
            func="f",
            started=now,
            stopped=now,
            success=False,
            result="boom",
        )

    def test_endpoints_never_mutate_tasks(self):
        auth = {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}
        before = Task.objects.count()
        self.client.get("/api/worker/tasks", **auth)
        self.client.get("/api/worker/tasks?status=failed", **auth)
        self.client.get("/api/worker/tasks/failed", **auth)
        self.client.get(f"/api/worker/tasks/{self.success.id}", **auth)
        self.client.get(f"/api/worker/tasks/{self.failure.id}", **auth)
        self.assertEqual(Task.objects.count(), before)


class WorkerSerializerSharedHelperTest(TestCase):
    """The ``extract_error_summary`` helper is the single source of truth.

    The Studio view (``studio/views/worker.py``) and the API serializer
    must both call the helper defined in ``api/serializers/worker.py`` so
    the collapsed-row summary stays in lock-step between the HTML page
    and the JSON API.
    """

    def test_studio_imports_helper_from_api_serializer(self):
        # If a future refactor inlines the heuristic back into the Studio
        # view, this import check fails -- forcing the author to pick a
        # side instead of letting the two definitions drift.
        from api.serializers.worker import extract_error_summary
        from studio.views import worker as studio_worker

        self.assertIs(studio_worker.extract_error_summary, extract_error_summary)

    def test_helper_returns_last_line_for_traceback(self):
        from api.serializers.worker import extract_error_summary

        text = (
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            "RuntimeError: nope"
        )
        self.assertEqual(extract_error_summary(text), "RuntimeError: nope")

    def test_helper_returns_first_line_for_non_traceback(self):
        from api.serializers.worker import extract_error_summary

        self.assertEqual(
            extract_error_summary("first line\nsecond line"),
            "first line",
        )

    def test_helper_truncates_long_lines_to_160_chars(self):
        from api.serializers.worker import extract_error_summary

        summary = extract_error_summary("X" * 500)
        self.assertLessEqual(len(summary), 160)
        self.assertTrue(summary.endswith("..."))

    def test_helper_handles_blank_input(self):
        from api.serializers.worker import (
            NO_ERROR_DETAILS_PLACEHOLDER,
            extract_error_summary,
        )

        self.assertEqual(
            extract_error_summary("   \n  \n"),
            NO_ERROR_DETAILS_PLACEHOLDER,
        )
        self.assertEqual(
            extract_error_summary(""),
            NO_ERROR_DETAILS_PLACEHOLDER,
        )
        self.assertEqual(
            extract_error_summary(None),
            NO_ERROR_DETAILS_PLACEHOLDER,
        )
