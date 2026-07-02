"""Playwright coverage for member API docs and plan-list execution (#1113)."""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _create_member_plan_and_key(email):
    from django.db import connection

    from accounts.models import MemberAPIKey, User
    from plans.models import Plan, Sprint

    user = User.objects.get(email=email)
    sprint, _ = Sprint.objects.get_or_create(
        slug="member-api-docs-2026",
        defaults={
            "name": "Member API Docs 2026",
            "start_date": datetime.date(2026, 7, 1),
            "duration_weeks": 6,
            "status": "active",
        },
    )
    plan, _ = Plan.objects.get_or_create(
        member=user,
        sprint=sprint,
        defaults={
            "title": "Docs-visible plan",
            "goal": "Use the member API from local tools",
        },
    )
    _, plaintext = MemberAPIKey.create_for_user(user=user, name="docs e2e")
    connection.close()
    return plan.id, plaintext


@pytest.mark.django_db(transaction=True)
class TestMemberApiDocs:
    @pytest.mark.core
    def test_member_opens_interactive_docs(self, django_server, browser):
        email = "member-api-docs@test.com"
        create_user(email, tier_slug="free")
        _create_member_plan_and_key(email)
        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/member-api/docs", wait_until="domcontentloaded")

        expect(page.locator('[data-testid="member-api-docs"]')).to_be_visible()
        expect(page.locator('[data-testid="member-api-usage-guide-link"]')).to_have_attribute(
            "href",
            "https://github.com/AI-Shipping-Labs/website/blob/main/docs/member-api/plans.md",
        )
        spec_response = context.request.get(f"{django_server}/member-api/openapi.json")
        assert spec_response.status == 200
        spec = spec_response.json()
        assert spec["info"]["title"] == "AI Shipping Labs Member API"
        assert spec["externalDocs"]["url"].endswith("docs/member-api/plans.md")
        assert "/member-api/v1/plans" in spec["paths"]
        assert all(path.startswith("/member-api/v1/") for path in spec["paths"])
        assert not any(path.startswith("/api/") for path in spec["paths"])

        context.close()

    @pytest.mark.core
    def test_member_key_lists_own_plan_from_docs_context(
        self, django_server, browser
    ):
        email = "member-api-docs-execute@test.com"
        other_email = "member-api-docs-other@test.com"
        create_user(email, tier_slug="free")
        create_user(other_email, tier_slug="free")
        plan_id, plaintext = _create_member_plan_and_key(email)
        other_plan_id, _ = _create_member_plan_and_key(other_email)
        context = auth_context(browser, email)
        page = context.new_page()
        page.goto(f"{django_server}/member-api/docs", wait_until="domcontentloaded")

        response = context.request.get(
            f"{django_server}/member-api/v1/plans",
            headers={"Authorization": f"Token {plaintext}"},
        )

        assert response.status == 200
        body = response.json()
        ids = {plan["id"] for plan in body["plans"]}
        assert plan_id in ids
        assert other_plan_id not in ids
        serialized = response.text()
        assert other_email not in serialized
        assert "user_email" not in serialized

        context.close()
