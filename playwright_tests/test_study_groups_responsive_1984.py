"""Responsive list journeys for study groups (#1984)."""

import os

import pytest
from django.db import connection
from playwright.sync_api import expect

from playwright_tests.conftest import create_session_for_user
from playwright_tests.study_groups_1984_helpers import groups_url, set_theme
from playwright_tests.test_pods_1918 import _page as cohort_page
from playwright_tests.test_pods_1918 import _pod as cohort_group
from playwright_tests.test_pods_1918 import _seed as seed_cohort
from playwright_tests.test_pods_1918 import _windows
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


def _assert_scannable_rows(rows):
    expect(rows).to_have_count(3)
    for index in range(3):
        row = rows.nth(index)
        expect(row.get_by_test_id("pod-row-title-link")).to_be_visible()
        expect(row.get_by_test_id("pod-row-purpose")).to_be_visible()
        expect(row.get_by_test_id("pod-row-members")).to_be_visible()
        expect(row.get_by_test_id("pod-row-timezones")).to_be_visible()
        actions = row.locator(
            '[data-testid="pod-row-join"], [data-testid="pod-row-leave"], [data-testid="pod-row-closed"]'
        )
        assert actions.count() == 1


def _mobile_context(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies(
        [
            {"name": "aslab_analytics_consent", "value": "denied", "domain": "127.0.0.1", "path": "/"},
            {
                "name": "sessionid",
                "value": create_session_for_user("anna@test.com"),
                "domain": "127.0.0.1",
                "path": "/",
            },
            {"name": "csrftoken", "value": "e2e-test-csrf-token-value", "domain": "127.0.0.1", "path": "/"},
        ]
    )
    return context


def _join_from_mobile_list(page, pod, theme):
    set_theme(page, theme)
    row = page.get_by_test_id("pod-row").filter(has_text=pod.name)
    expect(row.get_by_test_id("pod-row-join")).to_be_visible()
    box = row.get_by_test_id("pod-row-join").bounding_box()
    assert box["height"] >= 44
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    row.get_by_test_id("pod-row-join").click()
    expect(page.get_by_test_id("pod-row").filter(has_text=pod.name).get_by_test_id("pod-row-leave")).to_be_visible()


@pytest.mark.core
@browser_journey
def test_learner_scans_a_long_group_list_efficiently(django_server, browser):
    _course, cohort, users = seed_cohort()
    _windows(users["anna"], "Europe/Berlin", [(1, 18 * 60, 20 * 60, "preferred")])
    _windows(users["raj"], "America/New_York", [(1, 18 * 60, 20 * 60, "preferred")])
    cohort_group(
        cohort,
        "A very long research group title that wraps safely",
        [users["anna"], users["raj"]],
        purpose="Compare evidence-backed technical-agent workflows.",
    )
    cohort_group(cohort, "Open without timezones", [users["mike"]], purpose="Build useful knowledge assistants.")
    closed = cohort_group(cohort, "Closed exchange", [], purpose="Exchange project feedback.")
    from pods.models import Pod

    Pod.objects.filter(pk=closed.pk).update(status="closed")
    connection.close()
    context, page = cohort_page(browser, "anna@test.com")
    page.goto(groups_url(django_server), wait_until="domcontentloaded")
    rows = page.get_by_test_id("pod-row")
    _assert_scannable_rows(rows)
    expect(page.get_by_test_id("pods-rows")).to_contain_text("Time zones not set")
    body = page.get_by_test_id("pods-rows").inner_text().lower()
    for removed in ("waiting list", "request to join", "introduced by", "plan:", "overlap"):
        assert removed not in body
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    context.close()


@pytest.mark.core
@browser_journey
def test_member_acts_from_a_narrow_screen_group_list_in_light_and_dark(django_server, browser):
    _course, cohort, users = seed_cohort()
    groups = [
        cohort_group(cohort, f"{theme.title()} long group title that must wrap without overflow", [users["raj"]])
        for theme in ("light", "dark")
    ]
    for theme, pod in zip(("light", "dark"), groups, strict=True):
        context = _mobile_context(browser)
        page = context.new_page()
        page.goto(groups_url(django_server), wait_until="domcontentloaded")
        _join_from_mobile_list(page, pod, theme)
        context.close()
