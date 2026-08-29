"""OpenAPI separation tests for the member API."""

import io
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings, tag

from accounts.models import MemberAPIKey, Token
from api.openapi import build_spec
from api.urls import urlpatterns as operator_urlpatterns
from member_api.urls import urlpatterns as member_urlpatterns

User = get_user_model()


@tag("core")
class MemberOpenApiSpecTest(TestCase):
    def _build_member_spec(self):
        return build_spec(
            member_urlpatterns,
            title="AI Shipping Labs Member API",
            version="1.0.0",
            path_prefix="/member-api",
            docs_route_names={"member_api_openapi_json", "member_api_docs"},
            description="Member-owned keys act only on the owner's data.",
            token_description="Authorization: Token <asl_member_...>",
        )

    def test_member_spec_contains_only_member_paths(self):
        document = self._build_member_spec()

        self.assertEqual(document["info"]["title"], "AI Shipping Labs Member API")
        self.assertEqual(document["info"]["version"], "1.0.0")
        self.assertIn("member-owned", document["info"]["description"].lower())
        self.assertIn("/member-api/v1/plans", document["paths"])
        self.assertIn("/member-api/v1/events", document["paths"])
        self.assertIn("/member-api/v1/events/{event_id}", document["paths"])
        self.assertIn(
            "/member-api/v1/events/{event_id}/register",
            document["paths"],
        )
        self.assertIn("/member-api/v1/plans/{plan_id}", document["paths"])
        self.assertIn(
            "/member-api/v1/plans/{plan_id}/markdown",
            document["paths"],
        )
        self.assertIn(
            "/member-api/v1/plans/{plan_id}/progress",
            document["paths"],
        )
        self.assertNotIn("/member-api/openapi.json", document["paths"])
        self.assertNotIn("/member-api/docs", document["paths"])
        for path in document["paths"]:
            self.assertTrue(path.startswith("/member-api/v1/"), path)
            self.assertNotIn("/api/", path)
            self.assertNotIn("/studio/", path)

    def test_events_operations_have_complete_member_safe_contracts(self):
        document = self._build_member_spec()
        paths = document["paths"]
        list_operation = paths["/member-api/v1/events"]["get"]
        query = {
            parameter["name"]: parameter
            for parameter in list_operation["parameters"]
        }
        self.assertEqual(set(query), {"filter", "page"})
        self.assertEqual(query["filter"]["schema"]["enum"], ["upcoming", "past"])
        self.assertEqual(query["filter"]["schema"]["default"], "upcoming")
        self.assertEqual(query["page"]["schema"]["minimum"], 1)

        list_schema = list_operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(set(list_schema["properties"]), {"events", "pagination"})
        summary_properties = list_schema["properties"]["events"]["items"][
            "properties"
        ]
        self.assertIn("id", summary_properties)
        self.assertIn("registration_source", summary_properties)
        self.assertIn("attendee_count", summary_properties)

        detail_operation = paths["/member-api/v1/events/{event_id}"]["get"]
        detail_schema = detail_operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertIn("description_html", detail_schema["properties"])
        self.assertIn("instructors", detail_schema["properties"])
        self.assertIn("hosts", detail_schema["properties"])
        self.assertIn("join_url", detail_schema["properties"])

        register_operation = paths[
            "/member-api/v1/events/{event_id}/register"
        ]["post"]
        self.assertFalse(register_operation["requestBody"]["required"])
        request_properties = register_operation["requestBody"]["content"][
            "application/json"
        ]["schema"]["properties"]
        self.assertEqual(set(request_properties), {"scope"})
        self.assertEqual(request_properties["scope"]["enum"], ["series", "event"])
        register_schema = register_operation["responses"]["201"]["content"][
            "application/json"
        ]["schema"]
        self.assertIn("registration_status", register_schema["properties"])
        self.assertIn("registered_at", register_schema["properties"])
        self.assertIn("summary", register_schema["properties"])

        for operation, statuses in (
            (list_operation, ("401", "422")),
            (detail_operation, ("401", "403", "404")),
            (register_operation, ("401", "403", "404", "409", "422")),
        ):
            for status in statuses:
                with self.subTest(path=operation["summary"], status=status):
                    schema = operation["responses"][status]["content"][
                        "application/json"
                    ]["schema"]
                    self.assertEqual(
                        schema,
                        {"$ref": "#/components/schemas/ErrorResponse"},
                    )

        serialized = json.dumps({
            "list": list_schema,
            "detail": detail_schema,
            "register": register_schema,
        })
        for private_name in (
            "attendees",
            "registrations",
            "email",
            "zoom_meeting_id",
            "zoom_join_url",
            "source_repo",
            "crm",
        ):
            with self.subTest(private_name=private_name):
                self.assertNotIn(f'"{private_name}"', serialized)

    def test_operator_spec_does_not_include_member_paths(self):
        document = build_spec(operator_urlpatterns)

        self.assertTrue(document["paths"])
        for path in document["paths"]:
            self.assertFalse(path.startswith("/member-api/"), path)

    def test_committed_member_spec_is_separate_and_member_only(self):
        path = Path("_docs/member-openapi.json")
        self.assertTrue(path.exists())
        document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["info"]["title"], "AI Shipping Labs Member API")
        self.assertEqual(document["info"]["version"], "1.0.0")
        self.assertIn("same deployed member API capabilities", document["info"]["description"])
        self.assertEqual(
            document["externalDocs"]["description"],
            "Member API usage guide",
        )
        self.assertTrue(document["paths"])
        for path_name in document["paths"]:
            self.assertTrue(path_name.startswith("/member-api/v1/"), path_name)
            self.assertNotIn("/api/", path_name)
            self.assertNotIn("/studio/", path_name)
            self.assertNotIn("crm", path_name.lower())
            self.assertNotIn("email", path_name.lower())
        self.assertIn("/member-api/v1/events", document["paths"])
        serialized = json.dumps(document)
        for internal_name in (
            "plans:read",
            "plans:write",
            "books:read",
            "books:write_notes",
            "insufficient_scope",
            "under-scoped",
        ):
            self.assertNotIn(internal_name, serialized)


@tag("core")
class MemberOpenApiViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email="member-openapi@test.com",
            password="pw",
        )
        cls.staff = User.objects.create_user(
            email="member-openapi-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member_key, cls.plaintext = MemberAPIKey.create_for_user(
            user=cls.member,
            name="docs",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="operator")

    def test_member_session_gets_docs_and_spec(self):
        self.client.force_login(self.member)

        docs = self.client.get("/member-api/docs")
        spec = self.client.get("/member-api/openapi.json")

        self.assertEqual(docs.status_code, 200)
        self.assertContains(docs, "/member-api/openapi.json")
        self.assertContains(docs, "API usage guide")
        self.assertContains(docs, "docs/member-api/plans.md")
        self.assertEqual(spec.status_code, 200)
        document = spec.json()
        self.assertEqual(document["info"]["title"], "AI Shipping Labs Member API")
        self.assertEqual(
            document["externalDocs"]["url"],
            "https://github.com/AI-Shipping-Labs/website/blob/main/"
            "docs/member-api/plans.md",
        )

    def test_member_key_gets_spec_but_operator_token_does_not(self):
        member_response = self.client.get(
            "/member-api/openapi.json",
            HTTP_AUTHORIZATION=f"Token {self.plaintext}",
        )
        operator_response = self.client.get(
            "/member-api/openapi.json",
            HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )

        self.assertEqual(member_response.status_code, 200)
        self.assertEqual(operator_response.status_code, 401)
        self.assertEqual(operator_response.json()["code"], "invalid_member_api_key")


@tag("core")
class GenerateMemberOpenApiCommandTest(TestCase):
    def test_check_passes_on_clean_tree(self):
        out = io.StringIO()
        call_command("generate_member_openapi", "--check", stdout=out)
        self.assertIn("up to date", out.getvalue())

    def test_write_mode_produces_member_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(BASE_DIR=tmp):
                call_command("generate_member_openapi", stdout=io.StringIO())
            written = Path(tmp) / "_docs" / "member-openapi.json"
            self.assertTrue(written.exists())
            document = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(document["info"]["title"], "AI Shipping Labs Member API")
            self.assertIn("/member-api/v1/plans", document["paths"])
            self.assertIn("/member-api/v1/events", document["paths"])
