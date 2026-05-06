"""Playwright E2E coverage for member-level notes (issue #459)."""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_member_note_data():
    from accounts.models import Token
    from plans.models import InterviewNote, Plan, Sprint

    Token.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _seed_member_with_two_plans():
    from accounts.models import User
    from plans.models import Plan, Sprint

    spring = Sprint.objects.create(
        name="Spring 2026",
        slug="spring-2026",
        start_date="2026-03-01",
    )
    summer = Sprint.objects.create(
        name="Summer 2026",
        slug="summer-2026",
        start_date="2026-06-01",
    )
    member = User.objects.get(email="member@test.com")
    spring_plan = Plan.objects.create(member=member, sprint=spring)
    summer_plan = Plan.objects.create(member=member, sprint=summer)
    connection.close()
    return member.pk, spring_plan.pk, summer_plan.pk


@pytest.mark.django_db(transaction=True)
class TestStaffCapturesMemberContextAcrossSprints:
    def test_member_level_note_created_once_is_visible_in_next_sprint(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_member_note_data()
        _create_staff_user("staff@test.com")
        _create_user("member@test.com", tier_slug="main", email_verified=True)
        member_pk, _spring_plan_pk, summer_plan_pk = _seed_member_with_two_plans()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_role("heading", name="Member notes").wait_for(
            state="visible",
        )
        page.locator("text=No member notes yet.").wait_for(state="visible")
        page.get_by_role("link", name="Add member note").click()
        page.wait_for_url(f"{django_server}/studio/users/{member_pk}/notes/new")

        assert page.locator('select[name="visibility"]').evaluate("el => el.value") == "internal"
        assert page.locator('select[name="kind"]').evaluate("el => el.value") == "intake"
        assert page.locator('select[name="plan_id"]').evaluate("el => el.value") == ""

        page.locator('textarea[name="body"]').fill(
            "Wants to ship a RAG side project; previously a backend engineer",
        )
        page.locator('button[type="submit"]').click()
        page.wait_for_url(f"{django_server}/studio/users/{member_pk}/#member-notes")
        page.locator("text=Member note added.").wait_for(state="visible")
        page.locator(
            '[data-testid="internal-notes"] >> '
            'text=Wants to ship a RAG side project; previously a backend engineer'
        ).wait_for(state="visible")

        page.goto(
            f"{django_server}/studio/plans/{summer_plan_pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_role("heading", name="Member notes").wait_for(
            state="visible",
        )
        page.locator(
            'text=Wants to ship a RAG side project; previously a backend engineer'
        ).wait_for(state="visible")
        assert page.locator("text=This sprint").count() == 0
        context.close()


@pytest.mark.django_db(transaction=True)
class TestStaffRecordsSprintSpecificMemberNote:
    def test_sprint_specific_note_links_to_origin_plan_from_other_sprint(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_member_note_data()
        _create_staff_user("staff@test.com")
        _create_user("member@test.com", tier_slug="main", email_verified=True)
        member_pk, spring_plan_pk, summer_plan_pk = _seed_member_with_two_plans()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/plans/{spring_plan_pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_role("link", name="Add member note").click()
        page.wait_for_url(
            f"{django_server}/studio/users/{member_pk}/notes/new?plan_id={spring_plan_pk}",
        )
        assert page.locator('select[name="plan_id"]').evaluate("el => el.value") == str(spring_plan_pk)

        page.locator('select[name="kind"]').select_option("meeting")
        page.locator('textarea[name="body"]').fill(
            "Discussed pivot from RAG to agents on 2026-05-10",
        )
        page.locator('button[type="submit"]').click()
        page.wait_for_url(f"{django_server}/studio/users/{member_pk}/#member-notes")

        row = page.locator(
            "li",
            has_text="Discussed pivot from RAG to agents on 2026-05-10",
        )
        row.locator("text=From sprint: Spring 2026").wait_for(state="visible")
        assert row.get_by_role("link", name="From sprint: Spring 2026").get_attribute(
            "href",
        ) == f"/studio/plans/{spring_plan_pk}/"

        page.goto(
            f"{django_server}/studio/plans/{summer_plan_pk}/",
            wait_until="domcontentloaded",
        )
        row = page.locator(
            "li",
            has_text="Discussed pivot from RAG to agents on 2026-05-10",
        )
        row.locator("text=Spring 2026").wait_for(state="visible")
        assert row.get_by_role("link", name="Spring 2026").get_attribute(
            "href",
        ) == f"/studio/plans/{spring_plan_pk}/"
        assert row.locator("text=This sprint").count() == 0
        context.close()


@pytest.mark.django_db(transaction=True)
class TestMemberNotesApiPrivacy:
    def test_member_notes_alias_filters_external_and_blocks_other_member(
        self, django_server, browser,
    ):
        from accounts.models import Token, User
        from plans.models import InterviewNote, Plan

        _ensure_tiers()
        _clear_member_note_data()
        _create_staff_user("staff@test.com")
        _create_user("member@test.com", tier_slug="main", email_verified=True)
        _create_user("other@test.com", tier_slug="main", email_verified=True)
        _member_pk, spring_plan_pk, _summer_plan_pk = _seed_member_with_two_plans()

        staff = User.objects.get(email="staff@test.com")
        member = User.objects.get(email="member@test.com")
        other = User.objects.get(email="other@test.com")
        spring_plan = Plan.objects.get(pk=spring_plan_pk)
        InterviewNote.objects.create(
            plan=spring_plan,
            member=member,
            visibility="internal",
            kind="general",
            body="Member is shy in cohort",
            created_by=staff,
        )
        InterviewNote.objects.create(
            plan=spring_plan,
            member=member,
            visibility="external",
            kind="general",
            body="Share weekly progress in #show-and-tell",
            created_by=staff,
        )
        member_token = Token.objects.create(user=member, name="member")
        other_token = Token.objects.create(user=other, name="other")
        connection.close()

        context = browser.new_context()
        response = context.request.get(
            f"{django_server}/api/users/member@test.com/notes",
            headers={"Authorization": f"Token {member_token.key}"},
        )
        assert response.status == 200
        body = response.text()
        assert "Share weekly progress in #show-and-tell" in body
        assert "Member is shy in cohort" not in body

        response = context.request.get(
            f"{django_server}/api/users/member@test.com/notes?plan=null",
            headers={"Authorization": f"Token {member_token.key}"},
        )
        assert response.status == 200
        assert response.json()["interview_notes"] == []

        response = context.request.get(
            f"{django_server}/api/users/member@test.com/notes",
            headers={"Authorization": f"Token {other_token.key}"},
        )
        assert response.status == 403
        assert response.json()["code"] == "forbidden_other_user_plan"

        response = context.request.get(
            f"{django_server}/api/users/member@test.com/notes",
        )
        assert response.status == 401
        context.close()
