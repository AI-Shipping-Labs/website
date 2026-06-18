"""Playwright scenarios for issue #728 — drop Plan.status entirely.

Four scenarios:

1. Operator creates a plan from /studio/plans/new and sees no Status
   select.
2. Operator views the plan editor header and only sees the share
   indicator — no status pill.
3. Operator filters the plans list without a Status filter.
4. Member views their own plan with no status chip.

These exercise the rendering of multiple Studio + member surfaces in a
real browser. They are local-only (database seeding + session cookie
injection), per ``_docs/testing-guidelines.md``.
"""

import datetime
import os
import re

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


def _clear_plans_data():
    from plans.models import InterviewNote, Plan, Sprint, SprintEnrollment

    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestDropPlanStatus728:

    def test_create_form_has_no_status_select(self, django_server, browser):
        from plans.models import Sprint

        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )
        Sprint.objects.create(
            name="May 2026 sprint", slug="may-2026",
            start_date=datetime.date(2026, 5, 1), duration_weeks=6,
        )
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/new",
            wait_until="domcontentloaded",
        )
        # No <select name="status"> on the page.
        assert page.locator('select[name="status"]').count() == 0
        # No "Status" <label> on the page either.
        assert page.locator('label:has-text("Status")').count() == 0

    def test_editor_header_has_no_status_pill(self, django_server, browser):
        from plans.models import Plan, Sprint

        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        member = _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )
        sprint = Sprint.objects.create(
            name="May 2026 sprint", slug="may-2026",
            start_date=datetime.date(2026, 5, 1), duration_weeks=6,
        )
        plan = Plan.objects.create(member=member, sprint=sprint)
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="domcontentloaded",
        )
        # The status pill is gone.
        assert page.locator(
            '[data-testid="plan-status-pill"]'
        ).count() == 0
        # The share indicator stays.
        page.locator(
            '[data-testid="plan-share-indicator"]'
        ).wait_for(state="visible")
        assert "Not yet shared" in page.locator(
            '[data-testid="plan-share-indicator"]'
        ).inner_text()

    def test_plans_list_has_no_status_filter_or_column(
        self, django_server, browser,
    ):
        from plans.models import Plan, Sprint

        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        member = _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )
        sprint = Sprint.objects.create(
            name="May 2026 sprint", slug="may-2026",
            start_date=datetime.date(2026, 5, 1), duration_weeks=6,
        )
        Plan.objects.create(member=member, sprint=sprint)
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/",
            wait_until="domcontentloaded",
        )
        # No status <select> in the filter form.
        assert page.locator('select[name="status"]').count() == 0
        # The table has no Status column. The Title column is expected after
        # #1047 added first-class plan titles.
        # Headers render via Tailwind's ``uppercase`` class, so Playwright's
        # ``inner_text()`` returns the visually-uppercased form. Compare
        # case-insensitively so the test is robust to future CSS changes.
        headers = page.locator("table thead th").all_inner_texts()
        headers = [h.strip() for h in headers]
        expected = ["Member", "Title", "Sprint", "Shared", "Actions"]
        assert len(headers) == len(expected)
        for actual, want in zip(headers, expected):
            assert re.fullmatch(want, actual, re.IGNORECASE), (
                f"header {actual!r} does not match {want!r}"
            )
        assert not any(
            re.fullmatch("Status", header, re.IGNORECASE)
            for header in headers
        )

    def test_my_plan_view_has_no_status_chip(self, django_server, browser):
        from django.utils import timezone

        from plans.models import Plan, Sprint, SprintEnrollment

        _ensure_tiers()
        _clear_plans_data()
        member = _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )
        sprint = Sprint.objects.create(
            name="May 2026 sprint", slug="may-2026",
            start_date=datetime.date(2026, 5, 1), duration_weeks=6,
        )
        plan = Plan.objects.create(
            member=member, sprint=sprint, shared_at=timezone.now(),
        )
        # SprintEnrollment is back-created by the post_save signal on Plan.
        assert SprintEnrollment.objects.filter(
            sprint=sprint, user=member,
        ).exists()
        connection.close()

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{sprint.slug}/plan/{plan.pk}",
            wait_until="domcontentloaded",
        )
        # The member-side status chip is gone.
        assert page.locator(
            '[data-testid="my-plan-status-badge"]'
        ).count() == 0
        # Progress chip stays.
        page.locator(
            '[data-testid="my-plan-progress"]'
        ).wait_for(state="visible")
