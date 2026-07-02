"""Usage-doc and downloadable-skill checks for issue #1112."""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

User = get_user_model()


DOCS_PATH = Path("docs/member-api/plans.md")
SKILL_PATH = Path("skills/ai-shipping-labs-plans-api/SKILL.md")
SKILL_README_PATH = Path("skills/ai-shipping-labs-plans-api/README.md")


@tag("core")
class MemberApiUsageDocsArtifactTest(TestCase):
    def test_usage_docs_cover_member_plans_api(self):
        self.assertTrue(DOCS_PATH.exists())
        text = DOCS_PATH.read_text(encoding="utf-8")

        required = [
            "https://aishippinglabs.com/member-api/v1",
            "Authorization: Token <asl_member_...>",
            "/account/#api-keys",
            "/member-api/openapi.json",
            "/member-api/v1/plans",
            "/member-api/v1/plans/12",
            "/member-api/v1/plans/12/markdown",
            "/member-api/v1/plans/12/progress",
            "internal notes",
            "CRM notes",
            "onboarding answers",
            "staff context",
            "other members' data",
            "cannot create plans",
            "delete plans",
            "share plans",
            "edit narrative fields",
            "cohort teammates' plans",
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

        self.assertNotIn("Bearer", text)
        self.assertNotIn("/api/plans", text)
        self.assertNotIn("/studio/", text)

    def test_downloadable_skill_is_present_and_safe(self):
        self.assertTrue(SKILL_PATH.exists())
        self.assertTrue(SKILL_README_PATH.exists())
        text = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("name: ai-shipping-labs-plans-api", text)
        self.assertIn("AI_SHIPPING_LABS_MEMBER_API_KEY", text)
        self.assertIn("Authorization: Token <asl_member_...>", text)
        self.assertIn("GET /member-api/v1/plans", text)
        self.assertIn("GET /member-api/v1/plans/{plan_id}", text)
        self.assertIn("GET /member-api/v1/plans/{plan_id}/markdown", text)
        self.assertIn("PATCH /member-api/v1/plans/{plan_id}/progress", text)
        self.assertIn("Do not call `/api/`, `/studio/`, Django admin", text)
        self.assertIn("CRM notes", text)
        self.assertIn("onboarding answers", text)
        self.assertIn("staff context", text)
        self.assertIn("other members' data", text)
        self.assertIn("PRs against `skills/ai-shipping-labs-plans-api/`", text)
        self.assertNotIn("Bearer", text)
        self.assertIsNone(re.search(r"asl_member_[A-Za-z0-9]{16,}", text))


@tag("core")
class MemberApiUsageDocsLinkTest(TestCase):
    def test_account_links_to_github_docs_and_skill_directory(self):
        user = User.objects.create_user(email="member-api-doc-links@test.com")
        self.client.force_login(user)

        response = self.client.get("/account/#api-keys")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API usage guide")
        self.assertContains(response, "Download agent skill")
        self.assertContains(
            response,
            "https://github.com/AI-Shipping-Labs/website/blob/main/"
            "docs/member-api/plans.md",
        )
        self.assertContains(
            response,
            "https://github.com/AI-Shipping-Labs/website/tree/main/"
            "skills/ai-shipping-labs-plans-api",
        )

    def test_member_api_docs_links_to_github_usage_guide(self):
        user = User.objects.create_user(email="member-api-doc-page-link@test.com")
        self.client.force_login(user)

        response = self.client.get("/member-api/docs")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="member-api-docs"')
        self.assertContains(response, "API usage guide")
        self.assertContains(
            response,
            "https://github.com/AI-Shipping-Labs/website/blob/main/"
            "docs/member-api/plans.md",
        )
