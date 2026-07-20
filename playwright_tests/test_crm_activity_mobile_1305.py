"""Responsive CRM recent-activity browser coverage for issue #1305."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.test_crm_activity_context_1054 import (
    _seed_crm_activity,
    _seed_empty_crm,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

from analytics.models import UserActivity  # noqa: E402

pytestmark = pytest.mark.local_only

MOBILE = {"width": 393, "height": 852}
DESKTOP = {"width": 1280, "height": 720}


def _add_long_activity_rows(member_email):
    member = get_user_model().objects.get(email=member_email)
    now = timezone.now() + timezone.timedelta(seconds=5)
    linked_label = "Viewed article: " + ("a" * 239)
    token_label = "person-with-one-unbroken-token-" + ("x" * 220)
    UserActivity.objects.create(
        user=member,
        event_type=UserActivity.EVENT_RESOURCE_VIEW,
        label=linked_label[:255],
        target_url="/blog/crm-activity-1054-article",
        occurred_at=now,
    )
    UserActivity.objects.create(
        user=member,
        event_type=UserActivity.EVENT_EMAIL_CLICK,
        label=token_label[:255],
        target_url="https://dashboard.stripe.com/customer/unsafe",
        occurred_at=now - timezone.timedelta(seconds=1),
    )
    connection.close()
    return linked_label[:255], token_label[:255]


@pytest.mark.django_db(transaction=True)
class TestCRMActivityMobile1305:
    @pytest.mark.core
    def test_mobile_rows_filters_and_upgrade_marker_fit_the_page(
        self, django_server, browser,
    ):
        data = _seed_crm_activity()
        linked_label, token_label = _add_long_activity_rows(data["member_email"])
        context = _auth_context(browser, data["staff_email"])
        page = context.new_page()
        page.set_viewport_size(MOBILE)
        crm_url = f"{django_server}/studio/crm/{data['crm_id']}/?source=backlog"
        page.goto(crm_url, wait_until="domcontentloaded")

        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth",
        )
        filters = page.get_by_test_id("crm-activity-filters")
        expect(filters).to_have_attribute("role", "group")
        expect(filters).to_have_attribute(
            "aria-label", "Filter activity by category",
        )
        assert filters.evaluate("el => el.scrollWidth > el.clientWidth")
        chips = filters.get_by_role("link")
        assert chips.count() == 6
        for index in range(chips.count()):
            assert chips.nth(index).bounding_box()["height"] >= 44
        expect(page.get_by_test_id("crm-activity-filter-all")).to_have_attribute(
            "aria-current", "true",
        )

        linked = page.get_by_test_id("crm-activity-label").filter(
            has_text=linked_label,
        )
        token = page.get_by_test_id("crm-activity-label").filter(
            has_text=token_label,
        )
        expect(linked).to_be_visible()
        expect(token).to_be_visible()
        assert linked.inner_text() == linked_label
        assert token.inner_text() == token_label
        assert token.evaluate("el => el.scrollWidth <= el.clientWidth")

        row = linked.locator("xpath=ancestor::li")
        label_box = row.get_by_test_id("crm-activity-label-wrap").bounding_box()
        metadata_box = row.get_by_test_id("crm-activity-metadata").bounding_box()
        assert label_box["y"] < metadata_box["y"]
        section_width = page.get_by_test_id("crm-activity-section").bounding_box()[
            "width"
        ]
        assert row.get_by_test_id("crm-activity-type").bounding_box()["width"] < (
            section_width / 2
        )
        assert row.get_by_test_id("crm-activity-category").bounding_box()[
            "width"
        ] < (section_width / 2)
        assert row.get_by_test_id("crm-activity-time").evaluate(
            "el => getComputedStyle(el).whiteSpace",
        ) == "nowrap"

        marker = page.get_by_test_id("crm-activity-upgrade-marker")
        marker_box = marker.bounding_box()
        section_box = page.get_by_test_id("crm-activity-section").bounding_box()
        assert marker_box["x"] >= section_box["x"]
        assert marker_box["x"] + marker_box["width"] <= (
            section_box["x"] + section_box["width"]
        )

        page.get_by_test_id("crm-activity-filter-comms").click()
        page.wait_for_load_state("domcontentloaded")
        assert "activity_category=comms" in page.url
        assert "source=backlog" in page.url
        expect(page.get_by_test_id("crm-activity-filter-comms")).to_have_attribute(
            "aria-current", "true",
        )
        for testid in (
            "crm-snapshot-card",
            "crm-plans-section",
            "crm-booked-calls-section",
            "crm-onboarding-section",
            "crm-notes-section",
        ):
            expect(page.get_by_test_id(testid)).to_be_visible()
        context.close()

    @pytest.mark.core
    def test_filtered_empty_recovery_and_desktop_row_continuity(
        self, django_server, browser,
    ):
        empty_data = _seed_empty_crm()
        member = get_user_model().objects.get(email=empty_data["member_email"])
        UserActivity.objects.create(
            user=member,
            event_type=UserActivity.EVENT_COURSE_ENROLL,
            label="Learning activity only",
            occurred_at=timezone.now(),
        )
        connection.close()
        context = _auth_context(browser, empty_data["staff_email"])
        page = context.new_page()
        page.set_viewport_size(MOBILE)
        crm_url = f"{django_server}/studio/crm/{empty_data['crm_id']}/"
        page.goto(
            f"{crm_url}?activity_category=events&source=backlog",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("crm-activity-filter-empty")).to_contain_text(
            "No Events activity recorded for this member.",
        )
        page.get_by_test_id("crm-activity-view-all").click()
        page.wait_for_load_state("domcontentloaded")
        assert "activity_category=all" in page.url
        assert "source=backlog" in page.url
        expect(page.get_by_text("Learning activity only", exact=True)).to_be_visible()
        context.close()

        data = _seed_crm_activity()
        desktop_context = _auth_context(browser, data["staff_email"])
        desktop_page = desktop_context.new_page()
        desktop_page.set_viewport_size(DESKTOP)
        desktop_page.goto(
            f"{django_server}/studio/crm/{data['crm_id']}/",
            wait_until="domcontentloaded",
        )
        row = desktop_page.get_by_test_id("crm-activity-row").first
        boxes = [
            row.get_by_test_id(testid).bounding_box()
            for testid in (
                "crm-activity-type",
                "crm-activity-label-wrap",
                "crm-activity-category",
                "crm-activity-time",
            )
        ]
        assert [box["x"] for box in boxes] == sorted(box["x"] for box in boxes)
        assert max(box["height"] for box in boxes) < 50
        desktop_context.close()
