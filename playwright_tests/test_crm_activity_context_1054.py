"""CRM profile recent activity context (issue #1054).

Usage:
    uv run pytest playwright_tests/test_crm_activity_context_1054.py -v
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
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only


def _seed_crm_activity():
    from accounts.models import Token
    from analytics.models import UserActivity
    from content.models import Article, Course, Module, Unit
    from crm.models import CRMRecord
    from events.models import Event

    _ensure_tiers()
    staff_email = "crm-activity-1054-staff@test.com"
    member_email = "crm-activity-1054-member@test.com"
    other_email = "crm-activity-1054-other@test.com"
    staff = _create_staff_user(staff_email)
    member = _create_user(member_email, tier_slug="main")
    _create_user(other_email)

    token, _ = Token.objects.get_or_create(
        user=staff,
        name="crm-activity-1054",
    )
    record, _ = CRMRecord.objects.update_or_create(
        user=member,
        defaults={"status": "active", "persona": "Sam - Technical"},
    )
    course, _ = Course.objects.update_or_create(
        slug="crm-activity-1054-course",
        defaults={"title": "CRM Activity Course", "status": "published"},
    )
    module, _ = Module.objects.update_or_create(
        course=course,
        slug="module-1054",
        defaults={"title": "Module 1054", "sort_order": 1},
    )
    unit, _ = Unit.objects.update_or_create(
        module=module,
        slug="lesson-1054",
        defaults={"title": "Lesson 1054", "sort_order": 1},
    )
    event, _ = Event.objects.update_or_create(
        slug="crm-activity-1054-event",
        defaults={
            "title": "CRM Activity Event",
            "start_datetime": timezone.now() + timezone.timedelta(days=1),
            "status": "upcoming",
        },
    )
    Article.objects.update_or_create(
        slug="crm-activity-1054-article",
        defaults={
            "title": "CRM Activity Article",
            "date": date(2026, 1, 1),
            "published": True,
            "status": "published",
            "content_markdown": "Readable context article.",
        },
    )

    UserActivity.objects.filter(user=member).delete()
    now = timezone.now()
    rows = [
        {
            "event_type": UserActivity.EVENT_EVENT_REGISTER,
            "label": "Registered for event: CRM Activity Event",
            "object_type": "event",
            "object_id": event.slug,
            "target_url": f"/studio/events/{event.pk}/edit",
            "occurred_at": now,
        },
        {
            "event_type": UserActivity.EVENT_RESOURCE_VIEW,
            "label": "Viewed article: CRM Activity Article",
            "object_type": "article",
            "object_id": "crm-activity-1054-article",
            "target_url": "/blog/crm-activity-1054-article?utm_source=email",
            "occurred_at": now - timezone.timedelta(minutes=1),
        },
        {
            "event_type": UserActivity.EVENT_COURSE_ENROLL,
            "label": "Enrolled in course: CRM Activity Course",
            "object_type": "course",
            "object_id": course.slug,
            "target_url": f"/studio/courses/{course.pk}/edit",
            "occurred_at": now - timezone.timedelta(minutes=2),
        },
        {
            "event_type": UserActivity.EVENT_EMAIL_CLICK,
            "label": "Clicked external dashboard",
            "target_url": "https://dashboard.stripe.com/customers/cus_123",
            "occurred_at": now - timezone.timedelta(minutes=3),
        },
        {
            "event_type": UserActivity.EVENT_PAYMENT,
            "label": "Payment: Main",
            "occurred_at": now - timezone.timedelta(minutes=4),
        },
        {
            "event_type": UserActivity.EVENT_RESOURCE_VIEW,
            "label": "Viewed article: Pre-upgrade context",
            "target_url": "/blog/pre-upgrade-context",
            "occurred_at": now - timezone.timedelta(minutes=5),
        },
    ]
    for i in range(34):
        rows.append({
            "event_type": UserActivity.EVENT_LESSON_OPEN,
            "label": f"Opened lesson filler {i}",
            "object_type": "unit",
            "object_id": str(unit.pk),
            "target_url": f"/studio/units/{unit.pk}/edit",
            "occurred_at": now - timezone.timedelta(minutes=6 + i),
        })
    UserActivity.objects.bulk_create(
        UserActivity(user=member, **row) for row in rows
    )

    data = {
        "staff_email": staff_email,
        "member_email": member_email,
        "other_email": other_email,
        "crm_id": record.pk,
        "token": token.key,
        "event_url": event.get_absolute_url(),
        "course_url": course.get_absolute_url(),
    }
    connection.close()
    return data


def _seed_empty_crm():
    from analytics.models import UserActivity
    from crm.models import CRMRecord

    _ensure_tiers()
    staff_email = "crm-activity-1054-empty-staff@test.com"
    member_email = "crm-activity-1054-empty-member@test.com"
    _create_staff_user(staff_email)
    member = _create_user(member_email)
    record, _ = CRMRecord.objects.update_or_create(user=member)
    UserActivity.objects.filter(user=member).delete()
    data = {
        "staff_email": staff_email,
        "member_email": member_email,
        "crm_id": record.pk,
    }
    connection.close()
    return data


@pytest.mark.django_db(transaction=True)
class TestCRMActivityContext1054:
    @pytest.mark.core
    def test_staff_reads_filters_and_opens_safe_activity_links(
        self, django_server, browser,
    ):
        data = _seed_crm_activity()
        context = _auth_context(browser, data["staff_email"])
        page = context.new_page()
        crm_url = f"{django_server}/studio/crm/{data['crm_id']}/"
        page.goto(crm_url, wait_until="domcontentloaded")

        section = page.locator('[data-testid="crm-activity-section"]')
        assert section.count() == 1
        assert page.locator("text=Content context").count() == 0
        assert page.locator('[data-testid="crm-activity-row"]').count() == 30
        assert page.locator('[data-testid="crm-activity-more"]').inner_text() == (
            "Showing 30 of 40 events"
        )
        assert page.locator(
            '[data-testid="crm-activity-upgrade-marker"]',
        ).count() == 1

        hrefs = section.locator(
            'a[data-testid="crm-activity-label"]',
        ).evaluate_all("(links) => links.map((link) => link.getAttribute('href'))")
        assert data["event_url"] in hrefs
        assert data["course_url"] in hrefs
        assert "/blog/crm-activity-1054-article" in hrefs
        assert not any((href or "").startswith("/studio/") for href in hrefs)
        assert not any((href or "").startswith("/admin/") for href in hrefs)
        assert not any("dashboard.stripe.com" in (href or "") for href in hrefs)
        assert not any("utm_source" in (href or "") for href in hrefs)

        page.locator('[data-testid="crm-activity-filter-learning"]').click()
        page.wait_for_load_state("domcontentloaded")
        filtered = page.locator('[data-testid="crm-activity-section"]')
        assert "Enrolled in course: CRM Activity Course" in filtered.inner_text()
        assert "Registered for event: CRM Activity Event" not in filtered.inner_text()
        assert page.locator('[data-testid="crm-snapshot-card"]').count() == 1
        assert page.locator('[data-testid="crm-plans-section"]').count() == 1
        assert page.locator('[data-testid="crm-notes-section"]').count() == 1

        page.goto(crm_url, wait_until="domcontentloaded")
        api_response = page.request.get(
            f"{django_server}/api/users/{data['member_email']}/activity"
            "?category=events&limit=5",
            headers={"Authorization": f"Token {data['token']}"},
        )
        assert api_response.status == 200
        body = api_response.json()
        assert body["activities"][0]["label"] == (
            "Registered for event: CRM Activity Event"
        )
        assert body["activities"][0]["target_url"] == data["event_url"]
        event_link = section.get_by_role(
            "link", name="Registered for event: CRM Activity Event",
        )
        assert event_link.get_attribute("href") == data["event_url"]

        event_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith(data["event_url"])
        context.close()

    def test_empty_state_and_staff_gate(self, django_server, browser):
        data = _seed_empty_crm()
        staff_context = _auth_context(browser, data["staff_email"])
        page = staff_context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{data['crm_id']}/",
            wait_until="domcontentloaded",
        )
        empty = page.locator('[data-testid="crm-activity-empty"]')
        assert empty.count() == 1
        assert empty.inner_text() == (
            "No recorded activity yet. Activity will appear here as the "
            "member uses the site."
        )
        staff_context.close()

        member_context = _auth_context(browser, data["member_email"])
        member_page = member_context.new_page()
        response = member_page.goto(
            f"{django_server}/studio/crm/{data['crm_id']}/",
            wait_until="domcontentloaded",
        )
        assert response.status in {302, 403}
        assert member_page.locator(
            '[data-testid="crm-activity-section"]',
        ).count() == 0
        member_context.close()
