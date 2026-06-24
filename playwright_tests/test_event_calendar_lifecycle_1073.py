"""Playwright trigger coverage for event calendar email lifecycle (#1073)."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _reset_event_state():
    from django_q.models import OrmQ

    from email_app.models import EmailLog
    from events.models import Event, EventRegistration

    EmailLog.objects.filter(
        email_type__in=("event_registration", "event_rescheduled", "event_cancelled"),
    ).delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    OrmQ.objects.all().delete()
    connection.close()


def _create_event(slug, *, status="upcoming"):
    from events.models import Event

    start = (datetime.now(timezone.utc) + timedelta(days=30)).replace(
        second=0,
        microsecond=0,
    )
    event = Event.objects.create(
        title=f"Calendar Lifecycle {slug}",
        slug=slug,
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        timezone="UTC",
        origin="studio",
    )
    connection.close()
    return event


def _register(email, event):
    from accounts.models import User
    from events.models import EventRegistration

    user = User.objects.create_user(email=email, email_verified=True)
    EventRegistration.objects.create(event=event, user=user)
    connection.close()
    return user


def _create_api_token(email):
    from accounts.models import Token, User

    user = User.objects.create_user(email=email, is_staff=True)
    token = Token.objects.create(user=user, name="calendar-lifecycle")
    connection.close()
    return token.key


def _count_queued(func_path):
    from django_q.models import OrmQ

    count = 0
    for row in OrmQ.objects.all():
        if row.task.get("func") == func_path:
            count += 1
    connection.close()
    return count


@pytest.mark.django_db(transaction=True)
class TestStudioCalendarLifecycle:
    def test_studio_cancellation_enqueues_calendar_cancel(
        self, django_server, browser,
    ):
        _reset_event_state()
        _create_staff_user("admin-1073@test.com")
        event = _create_event("studio-cancel-1073")
        _register("attendee-cancel-1073@test.com", event)

        before = _count_queued(
            "events.tasks.notify_cancellation.send_cancellation_notice_fanout",
        )

        context = _auth_context(browser, "admin-1073@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator('select[name="status"]').select_option("cancelled")
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        after = _count_queued(
            "events.tasks.notify_cancellation.send_cancellation_notice_fanout",
        )
        assert after - before == 1
        context.close()


@pytest.mark.django_db(transaction=True)
class TestApiCalendarLifecycle:
    def test_api_patch_reschedule_enqueues_calendar_update(
        self, django_server, page,
    ):
        _reset_event_state()
        token = _create_api_token("api-reschedule-1073@test.com")
        event = _create_event("api-reschedule-1073")
        _register("attendee-api-reschedule-1073@test.com", event)
        new_start = event.start_datetime + timedelta(days=3)
        new_end = new_start + timedelta(hours=2)

        before = _count_queued(
            "events.tasks.notify_reschedule.send_reschedule_notice_fanout",
        )

        response = page.request.patch(
            f"{django_server}/api/events/{event.slug}",
            headers={"Authorization": f"Token {token}"},
            data={
                "start_datetime": new_start.isoformat(),
                "end_datetime": new_end.isoformat(),
            },
        )

        assert response.status == 200
        after = _count_queued(
            "events.tasks.notify_reschedule.send_reschedule_notice_fanout",
        )
        assert after - before == 1

    def test_api_patch_cancel_enqueues_calendar_cancel(self, django_server, page):
        _reset_event_state()
        token = _create_api_token("api-cancel-1073@test.com")
        event = _create_event("api-cancel-1073")
        _register("attendee-api-cancel-1073@test.com", event)

        before = _count_queued(
            "events.tasks.notify_cancellation.send_cancellation_notice_fanout",
        )

        response = page.request.patch(
            f"{django_server}/api/events/{event.slug}",
            headers={"Authorization": f"Token {token}"},
            data={"status": "cancelled"},
        )

        assert response.status == 200
        after = _count_queued(
            "events.tasks.notify_cancellation.send_cancellation_notice_fanout",
        )
        assert after - before == 1
