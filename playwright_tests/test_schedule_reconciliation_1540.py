"""Browser coverage for recurring schedule health on Studio Worker."""

import os

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _set_state(status):
    from django.core.cache import caches

    from jobs.schedule_reconciliation import SCHEDULE_RECONCILIATION_CACHE_KEY

    caches["django_q"].set(
        SCHEDULE_RECONCILIATION_CACHE_KEY,
        {
            "status": status,
            "recorded_at": "2026-09-03T10:00:00+00:00",
            "last_success_at": None,
            "error": "RuntimeError: apply failed" if status == "degraded" else None,
            "expected_names": ["health-check"],
            "missing_names": [],
        },
        timeout=None,
    )


@pytest.mark.django_db(transaction=True)
class TestScheduleReconciliationWorkerBanner:
    @browser_journey
    def test_staff_sees_degraded_banner_and_worker_content(
        self,
        django_server,
        browser,
    ):
        create_staff_user("admin@test.com")
        _set_state("degraded")
        context = auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")

        banner = page.get_by_test_id("schedule-reconciliation-degraded")
        assert banner.is_visible()
        text = banner.inner_text()
        assert "Recurring schedules are degraded" in text
        assert "Existing crons were left unchanged" in text
        assert "python manage.py setup_schedules" in text
        assert "reconcile-schedules" in text
        assert page.get_by_text("Queue Depth").is_visible()

    @browser_journey
    def test_ok_state_has_no_degraded_banner(self, django_server, browser):
        create_staff_user("admin@test.com")
        _set_state("ok")
        context = auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")

        assert page.get_by_test_id("schedule-reconciliation-degraded").count() == 0
        assert page.get_by_text("Queue Depth").is_visible()
        assert page.get_by_text("Recent Tasks").is_visible()

    @browser_journey
    def test_successful_apply_clears_existing_banner(self, django_server, browser):
        from jobs.schedule_reconciliation import (
            ScheduleDefinition,
            apply_schedule_definitions,
        )

        create_staff_user("admin@test.com")
        _set_state("degraded")
        context = auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")
        assert page.get_by_test_id("schedule-reconciliation-degraded").is_visible()

        apply_schedule_definitions(
            (
                ScheduleDefinition(
                    "health-check",
                    "jobs.tasks.healthcheck.health_check",
                    "*/15 * * * *",
                ),
            )
        )
        page.reload(wait_until="domcontentloaded")

        assert page.get_by_test_id("schedule-reconciliation-degraded").count() == 0

    @browser_journey
    def test_non_staff_cannot_open_worker_diagnostics(self, django_server, browser):
        create_user("free@test.com")
        _set_state("degraded")
        context = auth_context(browser, "free@test.com")
        page = context.new_page()

        response = page.goto(
            f"{django_server}/studio/worker/",
            wait_until="domcontentloaded",
        )

        assert response.status == 403
        assert page.get_by_test_id("schedule-reconciliation-degraded").count() == 0
