"""Playwright E2E tests for Studio event rescheduling (issue #670).

Covers the happy path (date changed -> flash + queued job), the no-op
path (description-only change -> no flash, no job), and the past-event
guardrail (typo correction on a past row -> no flash, no job).

The "queued job" assertion inspects the django-q OrmQ table: each
``enqueue_reschedule_notice`` call writes one row whose payload
function matches the dotted path. The Playwright runner does NOT have a
worker process attached, so the rows accumulate without executing —
mirroring how ``test_content_source_first_sync`` and friends assert
queueing.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_events():
    from django_q.models import OrmQ

    from email_app.models import EmailLog
    from events.models import Event, EventRegistration

    EmailLog.objects.filter(email_type='event_rescheduled').delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    OrmQ.objects.all().delete()
    connection.close()


def _create_event(title, slug, start_datetime, **kwargs):
    from events.models import Event

    defaults = {
        'status': 'upcoming',
        'timezone': 'UTC',
        'origin': 'studio',
    }
    defaults.update(kwargs)
    event = Event(
        title=title,
        slug=slug,
        start_datetime=start_datetime,
        end_datetime=start_datetime + timedelta(hours=1),
        **defaults,
    )
    event.save()
    connection.close()
    return event


def _register_user(email, event, preferred_timezone=''):
    from accounts.models import User
    from events.models import EventRegistration

    user, _ = User.objects.get_or_create(
        email=email,
        defaults={
            'email_verified': True,
            'preferred_timezone': preferred_timezone,
        },
    )
    user.preferred_timezone = preferred_timezone
    user.save()
    EventRegistration.objects.get_or_create(event=event, user=user)
    connection.close()
    return user


def _count_queued_reschedule_jobs():
    """Return the number of OrmQ rows targeting the reschedule fan-out.

    Each call to ``enqueue_reschedule_notice`` writes one stage-1 row
    pointing at ``send_reschedule_notice_fanout`` — no worker is
    running under Playwright, so the row stays queued.
    """
    from django_q.models import OrmQ

    total = 0
    for row in OrmQ.objects.all():
        # ``OrmQ.task`` is a cached_property (decoded signed payload), not
        # a method — accessing it as an attribute returns the dict.
        payload = row.task
        if (
            payload.get('func')
            == 'events.tasks.notify_reschedule.send_reschedule_notice_fanout'
        ):
            total += 1
    connection.close()
    return total


@pytest.mark.django_db(transaction=True)
class TestStudioEventReschedule:
    """Studio admin reschedules an event and sees the dispatch confirmation."""

    def test_reschedule_flashes_count_and_enqueues_job(
        self, django_server, browser,
    ):
        _clear_events()
        _create_staff_user("admin-reschedule@test.com")

        # Pin the event well in the future so timezone.now() guards do
        # not silently skip the trigger.
        future_start = datetime.now(timezone.utc) + timedelta(days=30)
        # Zero seconds so the rendered form value round-trips cleanly.
        future_start = future_start.replace(second=0, microsecond=0)
        event = _create_event(
            title='Live Q&A',
            slug='live-qa-pw',
            start_datetime=future_start,
        )

        # Three registered users with mixed timezone preferences (the
        # per-user template rendering itself is covered in the Django
        # tests; here we only assert the queueing).
        _register_user('eu-pw@test.com', event, preferred_timezone='Europe/Berlin')
        _register_user(
            'us-pw@test.com', event, preferred_timezone='America/New_York',
        )
        _register_user('nopref-pw@test.com', event, preferred_timezone='')

        before = _count_queued_reschedule_jobs()

        context = _auth_context(browser, "admin-reschedule@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Move the date 7 days later, keep everything else the same.
        new_date = (future_start + timedelta(days=7)).strftime('%d/%m/%Y')
        date_field = page.locator('input[name="event_date"]')
        date_field.fill('')
        date_field.fill(new_date)
        # Issue #860: link-less event — accept the "no meeting link" confirm.
        page.on("dialog", lambda d: d.accept())
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        # Flash message visible on the redirected edit page.
        body_text = page.locator('body').inner_text()
        assert 'Rescheduling notice sent to 3 registered attendees.' in body_text

        # Exactly one stage-1 fan-out job was enqueued (the fan-out
        # itself runs on the worker and would enqueue the three
        # stage-2 jobs — but there is no worker running here).
        after = _count_queued_reschedule_jobs()
        assert after - before == 1

    def test_description_only_save_does_not_flash_or_enqueue(
        self, django_server, browser,
    ):
        _clear_events()
        _create_staff_user("admin-noflash@test.com")

        future_start = (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).replace(second=0, microsecond=0)
        event = _create_event(
            title='Live Q&A NoFlash',
            slug='live-qa-pw-noflash',
            start_datetime=future_start,
        )
        _register_user('attendee-noflash@test.com', event)

        context = _auth_context(browser, "admin-noflash@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Change the description only — no date/time mutation.
        description = page.locator('textarea[name="description"]')
        description.fill('Bring questions!')
        # Issue #860: link-less event — accept the "no meeting link" confirm.
        page.on("dialog", lambda d: d.accept())
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        body_text = page.locator('body').inner_text()
        assert 'Rescheduling notice' not in body_text
        assert _count_queued_reschedule_jobs() == 0

    def test_past_event_correction_does_not_flash_or_enqueue(
        self, django_server, browser,
    ):
        """Admin fixes a typo on a past event; no spurious notification."""
        _clear_events()
        _create_staff_user("admin-pastfix@test.com")

        # Past event — start_datetime well in the past.
        past_start = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).replace(second=0, microsecond=0)
        event = _create_event(
            title='Past Workshop',
            slug='past-workshop-pw',
            start_datetime=past_start,
            status='complete',
        )
        _register_user('attendee-past@test.com', event)

        context = _auth_context(browser, "admin-pastfix@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Move the past event one day later (still in the past) — a
        # content-repo typo correction.
        new_date = (past_start + timedelta(days=1)).strftime('%d/%m/%Y')
        date_field = page.locator('input[name="event_date"]')
        date_field.fill('')
        date_field.fill(new_date)
        # Issue #860: link-less event — accept the "no meeting link" confirm.
        page.on("dialog", lambda d: d.accept())
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        body_text = page.locator('body').inner_text()
        assert 'Rescheduling notice' not in body_text
        assert _count_queued_reschedule_jobs() == 0
