"""Unlimited self-service and scannable study-group journeys (#1984)."""

import datetime
import importlib
import os
import re

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.study_groups_1984_helpers import (
    groups_url,
    imported_groups,
    imported_members,
    membership_count,
)
from playwright_tests.test_pods_1918 import (
    _page as cohort_page,
)
from playwright_tests.test_pods_1918 import (
    _pod as cohort_group,
)
from playwright_tests.test_pods_1918 import (
    _pod_url as cohort_group_url,
)
from playwright_tests.test_pods_1918 import (
    _seed as seed_cohort,
)
from playwright_tests.test_pods_1918 import (
    _set_config,
)
from playwright_tests.test_sprint_pods_1921 import (
    _page as sprint_page,
)
from playwright_tests.test_sprint_pods_1921 import (
    _pod as sprint_group,
)
from playwright_tests.test_sprint_pods_1921 import (
    _seed as seed_sprint,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


@pytest.mark.core
@browser_journey
def test_cohort_participant_joins_two_groups_for_broader_peer_support(django_server, browser):
    _course, cohort, users = seed_cohort()
    first = cohort_group(cohort, "Evidence group", [users["raj"]])
    second = cohort_group(cohort, "Agent group", [users["mike"]])
    context, page = cohort_page(browser, "anna@test.com")
    page.goto(groups_url(django_server), wait_until="domcontentloaded")
    for pod in (first, second):
        row = page.get_by_test_id("pod-row").filter(has_text=pod.name)
        row.get_by_test_id("pod-row-join").click()
        expect(page.get_by_test_id("messages-region")).to_contain_text(f"You joined {pod.name}.")
        expect(page.get_by_test_id("pod-row").filter(has_text=pod.name).get_by_test_id("pod-row-leave")).to_be_visible()
    assert membership_count(first, users["anna"]) == 1
    assert membership_count(second, users["anna"]) == 1
    for pod in (first, second):
        page.get_by_test_id("pod-row").filter(has_text=pod.name).get_by_test_id("pod-row-title-link").click()
        expect(page.get_by_test_id("pod-name")).to_have_text(pod.name)
        page.get_by_test_id("pod-back").click()
    context.close()


@pytest.mark.core
@browser_journey
def test_sprint_participant_joins_without_an_approval_queue(django_server, browser):
    sprint, _autumn, users = seed_sprint()
    pod = sprint_group(sprint, "Sprint agents", [users["anna"]], owner=users["anna"])
    context, page = sprint_page(browser, "main2@test.com")
    page.goto(f"{django_server}/sprints/{sprint.slug}/pods", wait_until="domcontentloaded")
    row = page.get_by_test_id("pod-row").filter(has_text="Sprint agents")
    row.get_by_test_id("pod-row-join").click()
    expect(page.get_by_test_id("messages-region")).to_contain_text("You joined Sprint agents.")
    expect(
        page.get_by_test_id("pod-row").filter(has_text="Sprint agents").get_by_test_id("pod-row-leave")
    ).to_be_visible()
    page.get_by_test_id("pod-row").filter(has_text="Sprint agents").get_by_test_id("pod-row-title-link").click()
    expect(page.get_by_test_id("pod-suggested-times")).to_be_visible()
    expect(page.get_by_text(re.compile(r"request|waiting list", re.I))).to_have_count(0)
    assert membership_count(pod, users["mike"]) == 1
    context.close()


@pytest.mark.core
@browser_journey
def test_member_leaves_one_group_while_keeping_another(django_server, browser):
    _course, cohort, users = seed_cohort()
    first = cohort_group(cohort, "Leave this group", [users["anna"], users["raj"]], owner=users["raj"])
    second = cohort_group(cohort, "Keep this group", [users["anna"], users["mike"]], owner=users["mike"])
    context, page = cohort_page(browser, "anna@test.com")
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(groups_url(django_server), wait_until="domcontentloaded")
    page.get_by_test_id("pod-row").filter(has_text=first.name).get_by_test_id("pod-row-leave").click()
    expect(page.get_by_test_id("messages-region")).to_contain_text("You left Leave this group.")
    expect(page.get_by_test_id("pod-row").filter(has_text=first.name).get_by_test_id("pod-row-join")).to_be_visible()
    expect(page.get_by_test_id("pod-row").filter(has_text=second.name).get_by_test_id("pod-row-leave")).to_be_visible()
    assert membership_count(first, users["anna"]) == 0
    assert membership_count(second, users["anna"]) == 1
    context.close()


@pytest.mark.core
@browser_journey
def test_participant_understands_a_closed_groups_boundary(django_server, browser):
    _course, cohort, users = seed_cohort()
    pod = cohort_group(cohort, "Closed research group", [users["raj"]], owner=users["raj"])
    from pods.models import Pod

    Pod.objects.filter(pk=pod.pk).update(status="closed")
    connection.close()
    context, page = cohort_page(browser, "anna@test.com")
    page.goto(cohort_group_url(django_server, pod), wait_until="domcontentloaded")
    expect(page.get_by_test_id("pod-purpose")).to_be_visible()
    expect(page.get_by_test_id("pod-join")).to_have_count(0)
    context.close()
    context, page = cohort_page(browser, "raj@test.com")
    page.goto(cohort_group_url(django_server, pod), wait_until="domcontentloaded")
    expect(page.get_by_test_id("pod-leave-toggle")).to_be_visible()
    page.get_by_test_id("pod-leave-toggle").click()
    expect(page.get_by_test_id("pod-leave")).to_contain_text(
        "Leave Closed research group? You can rejoin while the group is open.",
    )
    context.close()


@pytest.mark.core
@browser_journey
def test_ineligible_viewer_cannot_mutate_membership(django_server, browser):
    _course, cohort, users = seed_cohort()
    pod = cohort_group(cohort, "Cohort-only group", [users["raj"]])
    context, page = cohort_page(browser, "main@test.com")
    for url in (groups_url(django_server), cohort_group_url(django_server, pod)):
        response = page.goto(url, wait_until="domcontentloaded")
        assert response.status == 404
    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    result = page.evaluate(
        "url => fetch(url, {method: 'POST', headers: {'X-CSRFToken': "
        "document.cookie.split('; ').find(v => v.startsWith('csrftoken='))?.split('=')[1] || ''}})"
        ".then(r => ({status: r.status, body: r.text()})).then(async x => ({status: x.status, body: await x.body}))",
        f"{cohort_group_url(django_server, pod)}/join",
    )
    assert result["status"] == 404
    assert membership_count(pod, users["main"]) == 0
    context.close()


@pytest.mark.core
@browser_journey
def test_participant_distinguishes_a_meeting_plan_from_a_scheduled_meeting(django_server, browser):
    _course, cohort, users = seed_cohort()
    pod = cohort_group(cohort, "Planning group", [users["anna"], users["raj"]], owner=users["anna"])
    context, page = cohort_page(browser, "anna@test.com")
    page.goto(cohort_group_url(django_server, pod), wait_until="domcontentloaded")
    expect(page.get_by_test_id("pod-meta")).to_contain_text("Plan: 1 meeting × 60 min")
    expect(page.get_by_test_id("pod-meeting")).to_have_count(0)
    from pods.tests.fixtures import make_meeting

    make_meeting(pod, timezone.now() + datetime.timedelta(days=2))
    connection.close()
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("pod-meta")).to_contain_text("Plan: 1 meeting × 60 min")
    expect(page.get_by_test_id("pod-meeting")).to_have_count(1)
    context.close()


@pytest.mark.core
@browser_journey
def test_staff_publishes_copy_without_exposing_formation_history(django_server, browser):
    _course, cohort, users = seed_cohort()
    secret = "Introduced by Alex in a private Slack thread."
    pod = cohort_group(cohort, "Privacy-safe group", [users["raj"]], owner=users["raj"])
    context, page = cohort_page(browser, "admin@test.com")
    page.goto(f"{django_server}/studio/pods/{pod.pk}/", wait_until="domcontentloaded")
    page.get_by_test_id("studio-pod-purpose-input").fill("Compare grounded technical workflows.")
    page.get_by_test_id("studio-pod-scheduling-note").fill("Choose a weekly time after joining.")
    page.get_by_test_id("studio-pod-internal-notes").fill(secret)
    page.get_by_test_id("sticky-save-action").click()
    expect(page.get_by_test_id("messages-region")).to_contain_text("Pod saved.")
    expect(page.get_by_test_id("studio-pod-internal-notes")).to_have_value(secret)
    context.close()
    context, page = cohort_page(browser, "anna@test.com")
    page.goto(cohort_group_url(django_server, pod), wait_until="domcontentloaded")
    expect(page.get_by_test_id("pod-purpose")).to_have_text("Compare grounded technical workflows.")
    expect(page.get_by_test_id("pod-scheduling-note")).to_have_text("Choose a weekly time after joining.")
    expect(page.get_by_text(secret, exact=True)).to_have_count(0)
    assert secret not in page.content()
    context.close()


@pytest.mark.core
@browser_journey
def test_imported_groups_survive_the_transition_intact(django_server, browser):
    course, cohort, users = seed_cohort()
    from pods.models import PodMembership

    course.slug = "ai-buildcamp"
    course.save(update_fields=["slug"])
    _set_config("PODS_COURSE_SLUGS", "ai-buildcamp")
    pods = imported_groups(cohort, imported_members(cohort, users))
    before = list(
        PodMembership.objects.filter(pod__in=pods).values_list("pk", "pod_id", "user_id", "joined_at", "source")
    )
    migration = importlib.import_module("pods.migrations.0010_unlimited_self_service_groups")
    migration.preserve_notes_and_retire_requests(apps, None)
    after = list(
        PodMembership.objects.filter(pod__in=pods).values_list("pk", "pod_id", "user_id", "joined_at", "source")
    )
    assert after == before
    assert len(after) == 52
    connection.close()

    context, page = cohort_page(browser, "anna@test.com")
    page.goto(f"{django_server}/courses/ai-buildcamp/home/pods?cohort=4", wait_until="domcontentloaded")
    expect(page.get_by_test_id("pod-row")).to_have_count(23)
    expect(page.get_by_text("Share progress on finance projects grounded in traceable evidence.")).to_be_visible()
    expect(page.get_by_text("Raw private formation note 1", exact=True)).to_have_count(0)
    context.close()
    context, page = cohort_page(browser, "admin@test.com")
    page.goto(f"{django_server}/studio/pods/{pods[0].pk}/", wait_until="domcontentloaded")
    expect(page.get_by_test_id("studio-pod-internal-notes")).to_have_value("Raw private formation note 1")
    context.close()
