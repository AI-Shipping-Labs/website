"""Playwright coverage for the CRM-style Studio user overview (issue #494).

Covers the staff-facing flows the issue's product intent calls out:

- Identity-area click on the user list navigates to the detail page.
- The detail page surfaces ``Login as user``, ``View as user``, and
  ``Django Admin`` from the header without scrolling to a sub-section.
- The detail page renders without horizontal overflow at desktop and
  mobile widths and the note cards do not collide with neighbors.
- The plan-detail page (which reuses ``_member_notes.html``) still
  renders cleanly so the partial polish does not regress that surface.
"""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
MOBILE_VIEWPORT = {"width": 390, "height": 844}
SCREENSHOT_DIR = Path("/tmp/aisl-issue-494-screenshots")


def _reset_state(staff_email):
    from accounts.models import User
    from plans.models import InterviewNote, Plan, Sprint

    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _seed_member_with_plan_and_notes(staff_email):
    from accounts.models import User
    from plans.models import InterviewNote, Plan, Sprint

    member = User.objects.create_user(
        email="crm-target@test.com",
        password="pw",
        first_name="Crm",
        last_name="Target",
        email_verified=True,
    )
    member.tags = ["early-adopter"]
    member.save(update_fields=["tags"])

    sprint = Sprint.objects.create(
        name="Spring 2026 (CRM)",
        slug="spring-2026-crm",
        start_date=datetime.date(2026, 3, 1),
    )
    plan = Plan.objects.create(member=member, sprint=sprint)

    staff = User.objects.get(email=staff_email)
    InterviewNote.objects.create(
        plan=None,
        member=member,
        visibility="internal",
        kind="intake",
        body="CRM internal note body",
        created_by=staff,
    )
    InterviewNote.objects.create(
        plan=plan,
        member=member,
        visibility="external",
        kind="general",
        body="CRM external note body",
        created_by=staff,
    )
    member_pk = member.pk
    plan_pk = plan.pk
    connection.close()
    return member_pk, plan_pk


def _capture_screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _assert_no_horizontal_overflow(page):
    overflow = page.evaluate(
        """() => {
            const root = document.scrollingElement || document.documentElement;
            return root.scrollWidth - root.clientWidth;
        }"""
    )
    assert overflow <= 2, f"Page has horizontal overflow of {overflow}px"


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestStudioUserCrmOverview:
    def test_user_list_identity_area_navigates_to_detail(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "crm-overview-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk, _plan_pk = _seed_member_with_plan_and_notes(staff_email)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=crm-target",
            wait_until="domcontentloaded",
        )

        row = page.locator(f'[data-testid="user-row-{member_pk}"]')
        assert row.is_visible()

        identity_link = row.locator('[data-testid="user-row-link"]')
        assert identity_link.is_visible()
        assert identity_link.get_attribute("href") == f"/studio/users/{member_pk}/"

        # The explicit View action stays alongside the identity link.
        assert row.locator('[data-testid="user-view-link"]').is_visible()
        # The login-as control is still a POST button (no GET regression).
        login_as = row.get_by_role("button", name="Login as")
        assert login_as.is_visible()

        _capture_screenshot(page, "users-list-1280px")
        identity_link.click()
        page.wait_for_url(f"**/studio/users/{member_pk}/")
        assert page.locator('[data-testid="user-detail-header"]').is_visible()
        context.close()

    def test_user_detail_header_actions_visible_and_keyboard_reachable(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "crm-detail-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk, _plan_pk = _seed_member_with_plan_and_notes(staff_email)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        impersonate = page.locator('[data-testid="user-detail-impersonate"]')
        view_as = page.locator('[data-testid="user-detail-view-as"]')
        admin_link = page.locator('[data-testid="user-detail-django-admin"]')
        assert impersonate.is_visible()
        assert view_as.is_visible()
        assert admin_link.is_visible()
        assert (
            admin_link.get_attribute("href")
            == f"/admin/accounts/user/{member_pk}/change/"
        )

        # Keyboard reachability: each action receives focus.
        impersonate.focus()
        assert page.evaluate(
            "document.activeElement.getAttribute('data-testid')"
        ) == "user-detail-impersonate"
        view_as.focus()
        assert page.evaluate(
            "document.activeElement.getAttribute('data-testid')"
        ) == "user-detail-view-as"
        admin_link.focus()
        assert page.evaluate(
            "document.activeElement.getAttribute('data-testid')"
        ) == "user-detail-django-admin"

        # Account-data sections present (issue #560): profile,
        # membership, tags, and the new CRM card. Plans and notes are
        # no longer rendered inline on the profile.
        for testid in [
            "user-detail-profile-section",
            "user-detail-membership-section",
            "user-tags-section",
            "user-crm-section",
        ]:
            assert page.locator(f'[data-testid="{testid}"]').is_visible()
        assert page.locator(
            '[data-testid="user-detail-plans-section"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="member-notes-section"]'
        ).count() == 0

        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "user-detail-1280px")

        # Submitting the impersonation form lands the staff user on '/'
        # as the target user.
        impersonate.click()
        page.wait_for_url(f"{django_server}/")
        context.close()

    def test_user_detail_is_usable_at_mobile_390x844(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "crm-mobile-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk, _plan_pk = _seed_member_with_plan_and_notes(staff_email)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=crm-target",
            wait_until="domcontentloaded",
        )
        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "users-list-390px")

        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        # Header actions, tags, and the new CRM card render cleanly at
        # narrow viewport. Plans and notes sections are no longer
        # rendered inline (issue #560).
        assert page.locator('[data-testid="user-detail-header"]').is_visible()
        assert page.locator('[data-testid="user-detail-impersonate"]').is_visible()
        assert page.locator('[data-testid="user-detail-django-admin"]').is_visible()
        assert page.locator('[data-testid="user-tags-section"]').is_visible()
        assert page.locator('[data-testid="user-crm-section"]').is_visible()
        assert page.locator(
            '[data-testid="user-detail-plans-section"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="member-notes-section"]'
        ).count() == 0
        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "user-detail-390px")

        # Note create form also renders cleanly on mobile.
        page.goto(
            f"{django_server}/studio/users/{member_pk}/notes/new",
            wait_until="domcontentloaded",
        )
        assert page.locator("textarea[name='body']").is_visible()
        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "user-detail-note-form-390px")
        context.close()

    def test_plan_detail_still_renders_member_notes_partial(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "crm-plan-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk, plan_pk = _seed_member_with_plan_and_notes(staff_email)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/plans/{plan_pk}/",
            wait_until="domcontentloaded",
        )

        # The shared partial renders both note sections and the add link
        # picks up plan_id from the plan-detail context.
        assert page.locator('[data-testid="internal-notes"]').is_visible()
        assert page.locator('[data-testid="external-notes"]').is_visible()
        add_note = page.locator('[data-testid="member-notes-add"]')
        assert add_note.is_visible()
        assert (
            add_note.get_attribute("href")
            == f"/studio/users/{member_pk}/notes/new?plan_id={plan_pk}"
        )

        # Plan detail's link back to the member detail still works.
        member_link = page.locator('[data-testid="plan-detail-member-link"]')
        assert member_link.is_visible()
        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "plan-detail-1280px")
        context.close()
