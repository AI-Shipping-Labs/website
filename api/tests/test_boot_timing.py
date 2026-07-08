"""Tests for the boot-timing diagnostics endpoint (issue #1142).

Covers ``GET /api/diagnostics/boot-timing``:

- Auth: anon 401 (JSON, not a login redirect), non-staff token 401,
  unknown token 401 -- mirroring the ``cleanup-gates`` auth tests exactly.
- Method: non-GET 405; unauthenticated non-GET 401 (auth is outermost).
- Payload: returns the captured web + worker payloads (``tag``,
  ``recorded_at``, ``role``, ``phases``) plus a top-level ``generated_at``
  when both cache keys are present.
- Missing data: a tier whose key is absent is ``null``; an empty store
  returns both ``null`` with a clean 200 and ``generated_at`` (no 404/500).
- The endpoint is documented in the generated OpenAPI spec under the
  ``Diagnostics`` tag.

Token fixtures mirror ``api/tests/test_cleanup_gates.py``: a staff token
via the manager, and a non-staff token constructed directly.
"""

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase

from accounts.models import Token
from api.openapi import OPENAPI_SPEC_ATTR, build_spec
from api.urls import urlpatterns

User = get_user_model()

URL = "/api/diagnostics/boot-timing"

WEB_PAYLOAD = {
    "tag": "20260708-ab12cd3",
    "recorded_at": "2026-07-08T10:00:00+00:00",
    "role": "web",
    "phases": {
        "django_setup": 4.2,
        "migrate": 8.1,
        "check": 1.3,
        "setup_schedules": 0.4,
        "total": 14.0,
    },
}
WORKER_PAYLOAD = {
    "tag": "20260708-ab12cd3",
    "recorded_at": "2026-07-08T10:00:05+00:00",
    "role": "worker",
    "phases": {
        "django_setup": 4.1,
        "check": 1.2,
        "setup_schedules": 0.4,
        "total": 5.9,
    },
}


class BootTimingAuthTest(TestCase):
    """Auth + method boundary (no cache data needed)."""

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

    def test_unauthenticated_non_get_returns_401_not_405(self):
        # ``token_required`` is outermost, so auth is checked before the
        # method gate: an anonymous POST must 401, not 405.
        response = self.client.post(URL)
        self.assertEqual(response.status_code, 401)


class BootTimingPayloadTest(TestCase):
    """The endpoint returns the persisted per-tier payloads verbatim."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="staff-tok")

    def setUp(self):
        # The ``django_q`` cache is a per-process ``LocMemCache`` under
        # TESTING; clear it so payloads written by one test do not leak.
        caches["django_q"].clear()

    def _get(self):
        response = self.client.get(
            URL, HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_returns_both_tiers_and_generated_at_when_present(self):
        caches["django_q"].set("boot_timing:web", WEB_PAYLOAD, timeout=None)
        caches["django_q"].set(
            "boot_timing:worker", WORKER_PAYLOAD, timeout=None,
        )

        body = self._get()

        self.assertEqual(set(body.keys()), {"web", "worker", "generated_at"})
        self.assertEqual(body["web"], WEB_PAYLOAD)
        self.assertEqual(body["worker"], WORKER_PAYLOAD)

    def test_web_payload_carries_tag_recorded_at_role_and_phases(self):
        caches["django_q"].set("boot_timing:web", WEB_PAYLOAD, timeout=None)

        web = self._get()["web"]

        self.assertEqual(web["tag"], "20260708-ab12cd3")
        self.assertEqual(web["recorded_at"], "2026-07-08T10:00:00+00:00")
        self.assertEqual(web["role"], "web")
        self.assertEqual(web["phases"]["migrate"], 8.1)
        self.assertEqual(web["phases"]["total"], 14.0)

    def test_generated_at_is_tz_aware_iso8601(self):
        from django.utils.dateparse import parse_datetime

        body = self._get()
        parsed = parse_datetime(body["generated_at"])
        self.assertIsNotNone(parsed, body["generated_at"])
        self.assertIsNotNone(parsed.utcoffset())

    def test_absent_tier_is_null_other_tier_returned(self):
        caches["django_q"].set("boot_timing:web", WEB_PAYLOAD, timeout=None)

        body = self._get()

        self.assertEqual(body["web"], WEB_PAYLOAD)
        self.assertIsNone(body["worker"])

    def test_empty_store_returns_both_null_still_200(self):
        body = self._get()

        self.assertIsNone(body["web"])
        self.assertIsNone(body["worker"])
        self.assertIn("generated_at", body)


class BootTimingOpenApiTest(TestCase):
    """The endpoint is documented under the ``Diagnostics`` tag."""

    @classmethod
    def setUpTestData(cls):
        cls.document = build_spec(urlpatterns)

    def test_path_present_with_only_get_operation(self):
        self.assertIn("/api/diagnostics/boot-timing", self.document["paths"])
        operations = self.document["paths"]["/api/diagnostics/boot-timing"]
        self.assertEqual(set(operations.keys()), {"get"})

    def test_operation_tagged_diagnostics(self):
        get_op = (
            self.document["paths"]["/api/diagnostics/boot-timing"]["get"]
        )
        self.assertEqual(get_op["tags"], ["Diagnostics"])

    def test_200_example_documents_web_worker_and_generated_at(self):
        example = (
            self.document["paths"]["/api/diagnostics/boot-timing"]["get"]
            ["responses"]["200"]["content"]["application/json"]["example"]
        )
        self.assertEqual(
            set(example.keys()), {"web", "worker", "generated_at"},
        )
        self.assertEqual(example["web"]["role"], "web")
        self.assertEqual(example["worker"]["role"], "worker")

    def test_view_carries_openapi_spec_attribute(self):
        from api.views.boot_timing import boot_timing_diagnostics

        spec = getattr(boot_timing_diagnostics, OPENAPI_SPEC_ATTR, None)
        self.assertIsNotNone(spec)
        self.assertEqual(spec["tag"], "Diagnostics")
        self.assertEqual(set(spec["methods"].keys()), {"GET"})
