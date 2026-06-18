"""Studio user activity links point to public/member pages (issue #1052).

Usage:
    uv run pytest playwright_tests/test_studio_user_activity_public_links_1052.py -v
"""

import datetime
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
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only


def _seed_member_activity():
    from analytics.models import UserActivity
    from content.models import Article, Course, Module, Unit
    from events.models import Event

    staff_email = "activity-links-1052-staff@test.com"
    member_email = "activity-links-1052-member@test.com"
    _ensure_tiers()
    _create_staff_user(staff_email)
    member = _create_user(member_email)

    course, _ = Course.objects.update_or_create(
        slug="activity-links-1052-course",
        defaults={
            "title": "Activity Links Course",
            "status": "published",
        },
    )
    module, _ = Module.objects.update_or_create(
        course=course,
        slug="module-1052",
        defaults={"title": "Module 1052", "sort_order": 1},
    )
    unit, _ = Unit.objects.update_or_create(
        module=module,
        slug="lesson-1052",
        defaults={"title": "Lesson 1052", "sort_order": 1},
    )
    event, _ = Event.objects.update_or_create(
        slug="activity-links-1052-event",
        defaults={
            "title": "Activity Links Event",
            "start_datetime": timezone.now() + timezone.timedelta(days=1),
            "status": "upcoming",
        },
    )
    Article.objects.update_or_create(
        slug="activity-links-1052-article",
        defaults={
            "title": "Activity Links Article",
            "date": datetime.date(2026, 1, 1),
            "published": True,
            "status": "published",
            "content_markdown": "Readable public article.",
        },
    )

    UserActivity.objects.filter(user=member).delete()
    now = timezone.now()
    rows = [
        {
            "event_type": UserActivity.EVENT_COURSE_ENROLL,
            "label": "Enrolled in course: Activity Links Course",
            "object_type": "course",
            "object_id": course.slug,
            "target_url": f"/studio/courses/{course.pk}/edit",
            "occurred_at": now - timezone.timedelta(minutes=6),
        },
        {
            "event_type": UserActivity.EVENT_LESSON_OPEN,
            "label": "Opened lesson: Module 1052 / Lesson 1052",
            "object_type": "unit",
            "object_id": str(unit.pk),
            "target_url": f"/studio/units/{unit.pk}/edit",
            "occurred_at": now - timezone.timedelta(minutes=5),
        },
        {
            "event_type": UserActivity.EVENT_EVENT_REGISTER,
            "label": "Registered for event: Activity Links Event",
            "object_type": "event",
            "object_id": event.slug,
            "target_url": f"/studio/events/{event.pk}/edit",
            "occurred_at": now - timezone.timedelta(minutes=4),
        },
        {
            "event_type": UserActivity.EVENT_EVENT_JOIN,
            "label": "Joined event: Activity Links Event",
            "object_type": "event",
            "object_id": event.slug,
            "target_url": f"/studio/events/{event.pk}/edit",
            "occurred_at": now - timezone.timedelta(minutes=3),
        },
        {
            "event_type": UserActivity.EVENT_RESOURCE_VIEW,
            "label": "Viewed article: Activity Links Article",
            "object_type": "article",
            "object_id": "activity-links-1052-article",
            "target_url": "/blog/activity-links-1052-article",
            "occurred_at": now - timezone.timedelta(minutes=2),
        },
        {
            "event_type": UserActivity.EVENT_COURSE_ENROLL,
            "label": "Enrolled in course: Deleted 1052",
            "object_type": "course",
            "object_id": "deleted-course-1052",
            "target_url": "/studio/courses/999999/edit",
            "occurred_at": now - timezone.timedelta(minutes=1),
        },
    ]
    UserActivity.objects.bulk_create(
        UserActivity(user=member, **row) for row in rows
    )

    result = {
        "staff_email": staff_email,
        "member_pk": member.pk,
        "course_url": course.get_absolute_url(),
        "unit_url": unit.get_absolute_url(),
        "event_url": event.get_absolute_url(),
    }
    connection.close()
    return result


@pytest.mark.django_db(transaction=True)
class TestStudioUserActivityPublicLinks1052:
    @pytest.mark.core
    def test_staff_clicks_public_activity_links(self, django_server, browser):
        data = _seed_member_activity()
        context = _auth_context(browser, data["staff_email"])
        page = context.new_page()
        studio_url = f"{django_server}/studio/users/{data['member_pk']}/"
        page.goto(studio_url, wait_until="domcontentloaded")

        section = page.locator('[data-testid="user-activity-section"]')
        assert section.count() == 1
        hrefs = section.locator(
            'a[data-testid="user-activity-label"]',
        ).evaluate_all("(links) => links.map((link) => link.getAttribute('href'))")
        assert not any((href or "").startswith("/studio/") for href in hrefs)
        assert not any((href or "").startswith("/admin/") for href in hrefs)
        assert (
            section.get_by_role("link", name="Enrolled in course: Deleted 1052")
            .count()
            == 0
        )

        page.get_by_role(
            "link", name="Enrolled in course: Activity Links Course",
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith(data["course_url"])

        page.goto(studio_url, wait_until="domcontentloaded")
        page.get_by_role(
            "link", name="Opened lesson: Module 1052 / Lesson 1052",
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith(data["unit_url"])

        page.goto(studio_url, wait_until="domcontentloaded")
        page.get_by_role(
            "link", name="Registered for event: Activity Links Event",
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith(data["event_url"])

        page.goto(studio_url, wait_until="domcontentloaded")
        page.get_by_role("link", name="Joined event: Activity Links Event").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith(data["event_url"])

        page.goto(studio_url, wait_until="domcontentloaded")
        page.get_by_role(
            "link", name="Viewed article: Activity Links Article",
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith("/blog/activity-links-1052-article")

        context.close()

    def test_non_staff_cannot_open_activity_timeline(self, django_server, browser):
        data = _seed_member_activity()
        other_email = "activity-links-1052-other@test.com"
        _create_user(other_email)

        context = _auth_context(browser, other_email)
        page = context.new_page()
        response = page.goto(
            f"{django_server}/studio/users/{data['member_pk']}/",
            wait_until="domcontentloaded",
        )

        assert response.status in {302, 403}
        assert page.locator('[data-testid="user-activity-section"]').count() == 0
        context.close()
