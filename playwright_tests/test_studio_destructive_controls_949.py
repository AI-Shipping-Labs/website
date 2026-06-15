"""Playwright E2E for Studio destructive controls (issue #949).

Covers the eight scenarios from the issue across the three destructive
surfaces — sprint cancel/delete, sprint enrollment unenroll, and course
certificate revoke/un-revoke — exercising the confirm() dialog, the
data-effect (work preserved / refused / deleted), the public revoked
certificate page, and the staff-gating contract.

All scenarios seed local DB fixtures and inject session cookies, so this
module is local-only (see _docs/testing-guidelines.md).
"""

import os
from datetime import date

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

pytestmark = pytest.mark.local_only


# --- Fixture helpers -------------------------------------------------------


def _reset():
    """Clear the rows this module creates so each test starts clean."""
    from content.models import (
        Course,
        CourseCertificate,
        Module,
        ProjectSubmission,
        Unit,
    )
    from plans.models import Plan, Sprint, SprintEnrollment

    CourseCertificate.objects.all().delete()
    ProjectSubmission.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _make_sprint(*, slug, name, status="active"):
    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=date(2026, 5, 1),
        duration_weeks=6,
        status=status,
    )
    connection.close()
    return sprint


def _enroll(sprint_id, email):
    from accounts.models import User
    from plans.models import Sprint, SprintEnrollment

    sprint = Sprint.objects.get(pk=sprint_id)
    user = User.objects.get(email=email)
    enrollment = SprintEnrollment.objects.create(sprint=sprint, user=user)
    connection.close()
    return enrollment


def _make_plan(sprint_id, email):
    from accounts.models import User
    from plans.models import Plan, Sprint

    sprint = Sprint.objects.get(pk=sprint_id)
    user = User.objects.get(email=email)
    plan = Plan.objects.create(sprint=sprint, member=user)
    connection.close()
    return plan


def _make_certificate(*, course_slug, email):
    from accounts.models import User
    from content.models import (
        Course,
        CourseCertificate,
        Module,
        ProjectSubmission,
        Unit,
    )

    course = Course.objects.create(
        title=f"Course {course_slug}", slug=course_slug, status="published",
    )
    module = Module.objects.create(
        course=course, title="M", slug=f"{course_slug}-m", sort_order=0,
    )
    Unit.objects.create(
        module=module, title="U", slug=f"{course_slug}-u", sort_order=0,
    )
    user = User.objects.get(email=email)
    submission = ProjectSubmission.objects.create(
        user=user, course=course,
        project_url="https://example.com/p", status="certified",
    )
    cert = CourseCertificate.objects.create(
        user=user, course=course, submission=submission,
    )
    course_id, cert_id = course.pk, cert.id
    connection.close()
    return course_id, cert_id


def _sprint_status(sprint_id):
    from plans.models import Sprint

    status = Sprint.objects.get(pk=sprint_id).status
    connection.close()
    return status


def _sprint_exists(sprint_id):
    from plans.models import Sprint

    exists = Sprint.objects.filter(pk=sprint_id).exists()
    connection.close()
    return exists


def _enrollment_exists(enrollment_id):
    from plans.models import SprintEnrollment

    exists = SprintEnrollment.objects.filter(pk=enrollment_id).exists()
    connection.close()
    return exists


def _plan_exists(plan_id):
    from plans.models import Plan

    exists = Plan.objects.filter(pk=plan_id).exists()
    connection.close()
    return exists


def _cert_is_revoked(cert_id):
    from content.models import CourseCertificate

    revoked = CourseCertificate.objects.get(pk=cert_id).is_revoked
    connection.close()
    return revoked


# --- Scenarios -------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStudioDestructiveControls949:

    @pytest.mark.core
    def test_cancel_sprint_keeps_members_and_plan(
        self, django_server, browser,
    ):
        """Scenario: cancelling a populated sprint preserves all work."""
        _reset()
        _ensure_tiers()
        _create_staff_user(email="admin@test.com")
        _create_user("m1@test.com", tier_slug="free")
        _create_user("m2@test.com", tier_slug="free")
        sprint = _make_sprint(slug="may-cohort", name="May Cohort")
        _enroll(sprint.pk, "m1@test.com")
        _enroll(sprint.pk, "m2@test.com")
        plan = _make_plan(sprint.pk, "m1@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )

        page.once("dialog", lambda d: d.accept())
        page.locator('[data-testid="sprint-cancel-button"]').click()
        page.wait_for_url(f"**/studio/sprints/{sprint.pk}/", timeout=10000)

        region = page.locator('[data-testid="messages-region"]')
        assert "cancelled" in region.inner_text().lower()
        badge = page.locator('[data-testid="sprint-status-badge"]')
        assert "Cancelled" in badge.inner_text()

        # Both members still rostered and the plan survives in the DB.
        rows = page.locator('[data-testid="sprint-enrolled-member-row"]')
        assert rows.count() == 2
        assert _sprint_status(sprint.pk) == "cancelled"
        assert _plan_exists(plan.pk)

    @pytest.mark.core
    def test_delete_refuses_sprint_with_member_work(
        self, django_server, browser,
    ):
        """Scenario: hard-delete is refused while a sprint holds work."""
        _reset()
        _ensure_tiers()
        _create_staff_user(email="admin@test.com")
        _create_user("m1@test.com", tier_slug="free")
        sprint = _make_sprint(slug="guarded", name="Guarded Cohort")
        _enroll(sprint.pk, "m1@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )

        page.once("dialog", lambda d: d.accept())
        page.locator('[data-testid="sprint-delete-button"]').click()
        page.wait_for_url(f"**/studio/sprints/{sprint.pk}/", timeout=10000)

        region = page.locator('[data-testid="messages-region"]')
        text = region.inner_text().lower()
        assert "cannot delete" in text
        assert "cancel it instead" in text
        # The sprint is still there (operator is back on its detail page).
        assert _sprint_exists(sprint.pk)

    @pytest.mark.core
    def test_delete_empty_draft_sprint(self, django_server, browser):
        """Scenario: an empty draft sprint is deleted and the operator
        lands on the sprint list."""
        _reset()
        _ensure_tiers()
        _create_staff_user(email="admin@test.com")
        sprint = _make_sprint(slug="oops", name="Oops Draft", status="draft")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )

        page.once("dialog", lambda d: d.accept())
        page.locator('[data-testid="sprint-delete-button"]').click()
        page.wait_for_url("**/studio/sprints/", timeout=10000)

        region = page.locator('[data-testid="messages-region"]')
        assert "deleted" in region.inner_text().lower()
        assert not _sprint_exists(sprint.pk)

    @pytest.mark.core
    def test_unenroll_member_keeps_plan(self, django_server, browser):
        """Scenario: unenrolling a member removes the roster row but keeps
        their plan."""
        _reset()
        _ensure_tiers()
        _create_staff_user(email="admin@test.com")
        _create_user("main@test.com", tier_slug="free")
        sprint = _make_sprint(slug="roster", name="Roster Cohort")
        enrollment = _enroll(sprint.pk, "main@test.com")
        plan = _make_plan(sprint.pk, "main@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )

        row = page.locator(
            '[data-testid="sprint-enrolled-member-row"]'
            '[data-user-email="main@test.com"]',
        )
        assert row.count() == 1
        page.once("dialog", lambda d: d.accept())
        row.locator('[data-testid="sprint-unenroll-button"]').click()
        page.wait_for_url(f"**/studio/sprints/{sprint.pk}/", timeout=10000)

        region = page.locator('[data-testid="messages-region"]')
        text = region.inner_text().lower()
        assert "unenrolled" in text
        assert "plan was kept" in text
        # Member gone from roster; plan survives.
        gone = page.locator(
            '[data-testid="sprint-enrolled-member-row"]'
            '[data-user-email="main@test.com"]',
        )
        assert gone.count() == 0
        assert not _enrollment_exists(enrollment.pk)
        assert _plan_exists(plan.pk)

    @pytest.mark.core
    def test_revoke_certificate_reflects_on_public_page(
        self, django_server, browser,
    ):
        """Scenario: revoking a certificate shows a Studio badge and the
        public page renders the revoked state."""
        _reset()
        _ensure_tiers()
        _create_staff_user(email="admin@test.com")
        _create_user("main@test.com", tier_slug="free")
        course_id, cert_id = _make_certificate(
            course_slug="rev", email="main@test.com",
        )

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course_id}/peer-reviews",
            wait_until="domcontentloaded",
        )

        # The certificate control sits inside a collapsed <details> row;
        # open every row so the revoke button is visible.
        page.eval_on_selector_all(
            "details[data-testid='submission-row']",
            "rows => rows.forEach(r => r.open = true)",
        )
        page.once("dialog", lambda d: d.accept())
        page.locator('[data-testid="certificate-revoke-button"]').click()
        page.wait_for_url(
            f"**/studio/courses/{course_id}/peer-reviews", timeout=10000,
        )

        region = page.locator('[data-testid="messages-region"]')
        assert "revoked" in region.inner_text().lower()
        assert (
            page.locator('[data-testid="certificate-revoked-badge"]').count()
            == 1
        )
        assert _cert_is_revoked(cert_id)

        # The public certificate page (fresh anonymous context) shows the
        # revoked state and is not presented as a valid credential.
        anon = browser.new_context()
        anon_page = anon.new_page()
        anon_page.goto(
            f"{django_server}/certificates/{cert_id}",
            wait_until="domcontentloaded",
        )
        assert (
            anon_page.locator(
                '[data-testid="certificate-revoked-message"]',
            ).count()
            == 1
        )
        body = anon_page.content()
        assert "This certifies that" not in body
        anon.close()

    @pytest.mark.core
    def test_unrevoke_restores_valid_certificate(
        self, django_server, browser,
    ):
        """Scenario: un-revoking restores the valid public credential."""
        _reset()
        _ensure_tiers()
        _create_staff_user(email="admin@test.com")
        _create_user("main@test.com", tier_slug="free")
        course_id, cert_id = _make_certificate(
            course_slug="unrev", email="main@test.com",
        )

        # Pre-revoke it via the model so the un-revoke control is shown.
        from django.utils import timezone

        from content.models import CourseCertificate
        cert = CourseCertificate.objects.get(pk=cert_id)
        cert.revoked_at = timezone.now()
        cert.save(update_fields=["revoked_at"])
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course_id}/peer-reviews",
            wait_until="domcontentloaded",
        )

        page.eval_on_selector_all(
            "details[data-testid='submission-row']",
            "rows => rows.forEach(r => r.open = true)",
        )
        page.once("dialog", lambda d: d.accept())
        page.locator('[data-testid="certificate-unrevoke-button"]').click()
        page.wait_for_url(
            f"**/studio/courses/{course_id}/peer-reviews", timeout=10000,
        )

        region = page.locator('[data-testid="messages-region"]')
        assert "restored" in region.inner_text().lower()
        assert (
            page.locator('[data-testid="certificate-revoked-badge"]').count()
            == 0
        )
        assert not _cert_is_revoked(cert_id)

        # Public page renders the valid credential again.
        anon = browser.new_context()
        anon_page = anon.new_page()
        anon_page.goto(
            f"{django_server}/certificates/{cert_id}",
            wait_until="domcontentloaded",
        )
        body = anon_page.content()
        assert "This certifies that" in body
        assert (
            anon_page.locator(
                '[data-testid="certificate-revoked-message"]',
            ).count()
            == 0
        )
        anon.close()

    @pytest.mark.core
    def test_non_staff_and_anonymous_cannot_reach_controls(
        self, django_server, browser,
    ):
        """Scenario: non-staff get 403 and anonymous redirect to login on
        the destructive endpoints; nothing changes."""
        _reset()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="free")
        sprint = _make_sprint(slug="acl", name="ACL Cohort")
        enrollment = _enroll(sprint.pk, "main@test.com")
        course_id, cert_id = _make_certificate(
            course_slug="acl", email="main@test.com",
        )

        cancel_url = f"/studio/sprints/{sprint.pk}/cancel"
        unenroll_url = (
            f"/studio/sprints/{sprint.pk}/enrollments/"
            f"{enrollment.pk}/unenroll"
        )
        revoke_url = f"/studio/certificates/{cert_id}/revoke"

        # Non-staff member: each destructive POST is blocked with 403
        # (the staff_required gate; the Django view tests assert the gate
        # specifically). The browser-level guarantee here is that a logged
        # in non-staff member cannot fire any of the three actions.
        member_ctx = _auth_context(browser, "main@test.com")
        member_page = member_ctx.new_page()
        for url in (cancel_url, unenroll_url, revoke_url):
            resp = member_page.request.post(f"{django_server}{url}")
            assert resp.status == 403, f"{url} should 403 for non-staff"
        member_ctx.close()

        # Anonymous: hitting a Studio page redirects to login (the
        # staff_required gate). A raw cross-context POST without a CSRF
        # cookie is rejected by CSRF middleware (403) before the gate even
        # runs, so the login-redirect contract for the destructive POSTs
        # is asserted authoritatively in the Django view tests. Here we
        # confirm the browser-level gate: an anonymous GET of the sprint
        # detail page (where the controls live) lands on the login page.
        anon = browser.new_context()
        anon_page = anon.new_page()
        anon_page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )
        assert "/accounts/login/" in anon_page.url, (
            "anonymous access to the sprint detail page should redirect "
            "to login"
        )
        # And a raw destructive POST is blocked (never 2xx/3xx-to-success).
        for url in (cancel_url, unenroll_url, revoke_url):
            resp = anon_page.request.post(f"{django_server}{url}")
            assert resp.status in (302, 403), (
                f"{url} must be blocked for anonymous, got {resp.status}"
            )
        anon.close()

        # Nothing changed.
        assert _sprint_status(sprint.pk) == "active"
        assert _enrollment_exists(enrollment.pk)
        assert not _cert_is_revoked(cert_id)

    @pytest.mark.core
    def test_dismissing_confirm_does_not_fire(self, django_server, browser):
        """Scenario: dismissing the delete confirm dialog sends no request
        and leaves the sprint unchanged."""
        _reset()
        _ensure_tiers()
        _create_staff_user(email="admin@test.com")
        sprint = _make_sprint(
            slug="dismiss", name="Dismiss Draft", status="draft",
        )

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )

        page.once("dialog", lambda d: d.dismiss())
        page.locator('[data-testid="sprint-delete-button"]').click()
        # No navigation expected; give the page a beat then assert we are
        # still on the detail page and the sprint survives.
        page.wait_for_timeout(500)
        assert page.url.endswith(f"/studio/sprints/{sprint.pk}/")
        assert _sprint_exists(sprint.pk)
