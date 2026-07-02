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
    def test_member_spec_contains_only_member_paths(self):
        document = build_spec(
            member_urlpatterns,
            title="AI Shipping Labs Member API",
            version="1.0.0",
            path_prefix="/member-api",
            docs_route_names={"member_api_openapi_json", "member_api_docs"},
            description=(
                "Member-owned keys are scoped to the owner's data."
            ),
            token_description="Authorization: Token <asl_member_...>",
        )

        self.assertEqual(document["info"]["title"], "AI Shipping Labs Member API")
        self.assertEqual(document["info"]["version"], "1.0.0")
        self.assertIn("member-owned", document["info"]["description"].lower())
        self.assertIn("/member-api/v1/plans", document["paths"])
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
        self.assertIn("scoped to the owner's data", document["info"]["description"])
        self.assertTrue(document["paths"])
        for path_name in document["paths"]:
            self.assertTrue(path_name.startswith("/member-api/v1/"), path_name)
            self.assertNotIn("/api/", path_name)
            self.assertNotIn("/studio/", path_name)
            self.assertNotIn("crm", path_name.lower())
            self.assertNotIn("email", path_name.lower())
            self.assertNotIn("event", path_name.lower())


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
