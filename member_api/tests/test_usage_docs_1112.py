"""Usage-doc and downloadable-skill checks for issue #1112."""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

User = get_user_model()


DOCS_PATH = Path("docs/member-api/plans.md")
SKILL_DIR = Path("skills/ai-shipping-labs-member-api")
SKILL_PATH = SKILL_DIR / "SKILL.md"
SKILL_README_PATH = SKILL_DIR / "README.md"
PLANS_SKILL_PATH = SKILL_DIR / "plans.md"
BOOKS_SKILL_PATH = SKILL_DIR / "books.md"
EVENTS_SKILL_PATH = SKILL_DIR / "events.md"


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
            "/member-api/v1/events/{event_id}",
            "Every active member key can use every deployed member endpoint",
            "attendee_count",
            "never return a roster",
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

        self.assertNotIn("Bearer", text)
        self.assertNotIn("/api/plans", text)
        self.assertNotIn("/studio/", text)
        for internal_name in (
            "plans:read",
            "plans:write",
            "books:read",
            "books:write_notes",
            "insufficient_scope",
            "under-scoped",
        ):
            self.assertNotIn(internal_name, text)

    def test_downloadable_skill_catalog_is_present_and_safe(self):
        # The top-level SKILL.md is the catalog: shared auth + key setup, the
        # safe-surface rules, and a pointer to each API family.
        self.assertTrue(SKILL_PATH.exists())
        self.assertTrue(SKILL_README_PATH.exists())
        text = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("name: ai-shipping-labs-member-api", text)
        self.assertIn("AI_SHIPPING_LABS_MEMBER_API_KEY", text)
        self.assertIn("Authorization: Token <asl_member_...>", text)
        # Catalog points at every family.
        self.assertIn("plans.md", text)
        self.assertIn("books.md", text)
        self.assertIn("events.md", text)
        self.assertIn("Every active key has the same deployed", text)
        # Shared safe-surface rules live in the catalog.
        self.assertIn("Do not call `/api/`, `/studio/`, Django admin", text)
        self.assertIn("CRM notes", text)
        self.assertIn("onboarding answers", text)
        self.assertIn("staff context", text)
        self.assertIn("other members' data", text)
        self.assertIn("PRs against `skills/ai-shipping-labs-member-api/`", text)
        self.assertNotIn("Bearer", text)
        self.assertIsNone(re.search(r"asl_member_[A-Za-z0-9]{16,}", text))

    def test_events_family_skill_is_present_and_safe(self):
        self.assertTrue(EVENTS_SKILL_PATH.exists())
        text = EVENTS_SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("GET  /member-api/v1/events/{event_id}", text)
        self.assertIn("POST /member-api/v1/events/{event_id}/register", text)
        self.assertIn("whole series", text)
        self.assertIn("never include a", text.lower())
        self.assertIn("attendee_count", text)
        self.assertNotIn("events:read", text)
        self.assertNotIn("events:register", text)
        self.assertNotIn("under-scoped", text)
        self.assertIsNone(re.search(r"asl_member_[A-Za-z0-9]{16,}", text))

    def test_plans_family_skill_is_present_and_safe(self):
        self.assertTrue(PLANS_SKILL_PATH.exists())
        text = PLANS_SKILL_PATH.read_text(encoding="utf-8")

        # Supporting reference, not a standalone skill: no frontmatter name.
        self.assertNotIn("name: ai-shipping-labs-member-api-plans", text)
        self.assertIn("GET /member-api/v1/plans", text)
        self.assertIn("GET /member-api/v1/plans/{plan_id}", text)
        self.assertIn("GET /member-api/v1/plans/{plan_id}/markdown", text)
        self.assertIn("PATCH /member-api/v1/plans/{plan_id}/progress", text)
        self.assertNotIn("Bearer", text)
        self.assertIsNone(re.search(r"asl_member_[A-Za-z0-9]{16,}", text))

    def test_books_family_skill_is_present_and_safe(self):
        self.assertTrue(BOOKS_SKILL_PATH.exists())
        text = BOOKS_SKILL_PATH.read_text(encoding="utf-8")

        # Supporting reference, not a standalone skill: no frontmatter name.
        self.assertNotIn("name: ai-shipping-labs-member-api-books", text)
        self.assertIn("GET /member-api/v1/books/{slug}/reading", text)
        self.assertIn(
            "PUT    /member-api/v1/books/{slug}/chapters/{number}/note", text,
        )
        self.assertIn("other member's note", text)
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
        # Issue #1127: the account "API usage guide" link now points at the
        # on-site docs page, not the raw GitHub blob. The skill tree link
        # stays (the directory was restored).
        self.assertContains(response, 'href="/member-api/docs"')
        self.assertNotContains(
            response,
            "https://github.com/AI-Shipping-Labs/website/blob/main/"
            "docs/member-api/plans.md",
        )
        self.assertContains(
            response,
            "https://github.com/AI-Shipping-Labs/website/tree/main/"
            "skills/ai-shipping-labs-member-api",
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
