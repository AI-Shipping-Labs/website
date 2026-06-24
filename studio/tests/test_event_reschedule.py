"""Tests for the Studio edit-view rescheduling trigger (issue #670).

The non-synced edit branch must:
- Capture ``old_start`` before mutating the event.
- After save, enqueue the reschedule fan-out only when:
  - the start_datetime changed by >= 60 seconds, AND
  - ``old_start`` is in the future, AND
  - both old and new starts are non-null.
- End-only edits enqueue updates for registered attendees so their calendar
  entry gets the new DTEND.
- The flash message reports the pre-filter registration count.
- The synced edit branch NEVER enqueues regardless of input.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventRegistration
from tests.fixtures import StaffUserMixin

User = get_user_model()


class StudioEventRescheduleTriggerTest(StaffUserMixin, TestCase):
    """The Studio edit view fires the reschedule notice on date change."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        # Pin the event in the future relative to ``timezone.now()`` so
        # the past-event rule does not gate the happy path.
        future_start = timezone.now() + timedelta(days=30)
        self.event = Event.objects.create(
            title='Live Q&A',
            slug='live-qa-resched',
            start_datetime=future_start,
            end_datetime=future_start + timedelta(hours=1),
            status='upcoming',
            timezone='UTC',
            origin='studio',
            ics_sequence=0,
        )
        # Capture the rendered date strings for the form POST so the
        # test does not have to hardcode tomorrow's calendar date.
        self._form_date = future_start.strftime('%d/%m/%Y')
        self._form_time = future_start.strftime('%H:%M')

    def _post_edit(self, **overrides):
        data = {
            'title': 'Live Q&A',
            'slug': 'live-qa-resched',
            'event_date': self._form_date,
            'event_time': self._form_time,
            'duration_hours': '1',
            'timezone': 'UTC',
            'status': 'upcoming',
            'required_level': '0',
        }
        data.update(overrides)
        return self.client.post(
            f'/studio/events/{self.event.pk}/edit', data, follow=True,
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_date_changed_enqueues_notice(self, mock_enqueue):
        """Moving the start by a week (in the future) enqueues the task."""
        # Two registered users so the flash count is reported.
        for i in range(2):
            user = User.objects.create_user(email=f'attendee{i}@test.com')
            EventRegistration.objects.create(event=self.event, user=user)

        # Move the event 7 days later, same wall-clock time.
        new_start = self.event.start_datetime + timedelta(days=7)
        response = self._post_edit(
            event_date=new_start.strftime('%d/%m/%Y'),
            event_time=new_start.strftime('%H:%M'),
        )

        mock_enqueue.assert_called_once()
        args = mock_enqueue.call_args.args
        self.assertEqual(args[0], self.event.pk)
        # Second positional is an ISO string of the OLD start.
        self.assertIsInstance(args[1], str)

        # Flash with the registration count.
        message_strings = [m.message for m in response.context['messages']]
        self.assertIn(
            'Rescheduling notice sent to 2 registered attendees.',
            message_strings,
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_flash_count_includes_unsubscribed_users(self, mock_enqueue):
        """Pre-filter count: admins see audience size, not deliveries."""
        # Two regular users + one unsubscribed user. The flash reports
        # the full registration count of 3, NOT the 2 deliverable.
        for i in range(2):
            user = User.objects.create_user(email=f'sub{i}@test.com')
            EventRegistration.objects.create(event=self.event, user=user)
        unsub = User.objects.create_user(
            email='unsub@test.com', unsubscribed=True,
        )
        EventRegistration.objects.create(event=self.event, user=unsub)

        new_start = self.event.start_datetime + timedelta(days=7)
        response = self._post_edit(
            event_date=new_start.strftime('%d/%m/%Y'),
            event_time=new_start.strftime('%H:%M'),
        )

        message_strings = [m.message for m in response.context['messages']]
        self.assertIn(
            'Rescheduling notice sent to 3 registered attendees.',
            message_strings,
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_singular_flash_for_one_attendee(self, mock_enqueue):
        """Singular 'attendee' (no 's') when count == 1."""
        user = User.objects.create_user(email='solo@test.com')
        EventRegistration.objects.create(event=self.event, user=user)

        new_start = self.event.start_datetime + timedelta(days=7)
        response = self._post_edit(
            event_date=new_start.strftime('%d/%m/%Y'),
            event_time=new_start.strftime('%H:%M'),
        )

        message_strings = [m.message for m in response.context['messages']]
        self.assertIn(
            'Rescheduling notice sent to 1 registered attendee.',
            message_strings,
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_date_changed_bumps_ics_sequence(self, mock_enqueue):
        """SEQUENCE bump enables calendar clients to overwrite the entry."""
        before = self.event.ics_sequence
        new_start = self.event.start_datetime + timedelta(days=7)
        self._post_edit(
            event_date=new_start.strftime('%d/%m/%Y'),
            event_time=new_start.strftime('%H:%M'),
        )
        self.event.refresh_from_db()
        self.assertGreater(self.event.ics_sequence, before)

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_sub_minute_drift_does_not_enqueue(self, mock_enqueue):
        """No-op re-save (same wall-clock) MUST NOT enqueue."""
        # Same date+time means the parser produces a delta well under
        # 60 seconds (in fact exactly zero).
        before_sequence = self.event.ics_sequence
        before_start = self.event.start_datetime
        before_end = self.event.end_datetime
        response = self._post_edit()

        mock_enqueue.assert_not_called()
        self.event.refresh_from_db()
        self.assertEqual(self.event.ics_sequence, before_sequence)
        self.assertEqual(self.event.start_datetime, before_start)
        self.assertEqual(self.event.end_datetime, before_end)
        message_strings = [m.message for m in response.context['messages']]
        self.assertFalse(
            any('Rescheduling notice' in m for m in message_strings),
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_title_only_change_does_not_enqueue(self, mock_enqueue):
        response = self._post_edit(title='Live Q&A (updated)')

        mock_enqueue.assert_not_called()
        message_strings = [m.message for m in response.context['messages']]
        self.assertFalse(
            any('Rescheduling notice' in m for m in message_strings),
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_duration_only_change_enqueues_calendar_update(self, mock_enqueue):
        """End-only edits update attendee calendars with the new DTEND."""
        user = User.objects.create_user(email='duration@test.com')
        EventRegistration.objects.create(event=self.event, user=user)
        # Same date+time, longer duration. end_datetime moves; start does not.
        before_sequence = self.event.ics_sequence
        response = self._post_edit(duration_hours='3')

        mock_enqueue.assert_called_once()
        self.event.refresh_from_db()
        self.assertGreater(self.event.ics_sequence, before_sequence)
        message_strings = [m.message for m in response.context['messages']]
        self.assertIn(
            'Rescheduling notice sent to 1 registered attendee.',
            message_strings,
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_zero_registrations_still_flashes_but_no_enqueue(self, mock_enqueue):
        """Trigger fires, no fan-out, flash still informs the admin."""
        new_start = self.event.start_datetime + timedelta(days=7)
        response = self._post_edit(
            event_date=new_start.strftime('%d/%m/%Y'),
            event_time=new_start.strftime('%H:%M'),
        )

        mock_enqueue.assert_not_called()  # zero registrations
        message_strings = [m.message for m in response.context['messages']]
        self.assertIn(
            'Rescheduling notice sent to 0 registered attendees.',
            message_strings,
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_idempotent_resave_does_not_re_enqueue(self, mock_enqueue):
        """A second save with no further date change does not re-fire."""
        for i in range(2):
            user = User.objects.create_user(email=f'idem{i}@test.com')
            EventRegistration.objects.create(event=self.event, user=user)

        new_start = self.event.start_datetime + timedelta(days=7)
        # First save: trigger fires.
        self._post_edit(
            event_date=new_start.strftime('%d/%m/%Y'),
            event_time=new_start.strftime('%H:%M'),
        )
        # Second save with identical fields: nothing changed.
        self.event.refresh_from_db()
        identical_start = self.event.start_datetime
        self._post_edit(
            event_date=identical_start.strftime('%d/%m/%Y'),
            event_time=identical_start.strftime('%H:%M'),
        )

        self.assertEqual(mock_enqueue.call_count, 1)


class StudioEventReschedulePastEventRuleTest(StaffUserMixin, TestCase):
    """When ``old_start`` is in the past, the trigger MUST NOT fire."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        # Place ``old_start`` in the past relative to the mocked clock.
        self.event = Event.objects.create(
            title='Past Workshop',
            slug='past-workshop',
            start_datetime=datetime(2026, 4, 10, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 4, 10, 17, 0, tzinfo=UTC),
            status='upcoming',
            timezone='UTC',
            origin='studio',
        )
        # Register two attendees — if the rule misfires, the fan-out
        # would be visible.
        for i in range(2):
            user = User.objects.create_user(email=f'pastattn{i}@test.com')
            EventRegistration.objects.create(event=self.event, user=user)

    @patch('django.utils.timezone.now')
    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_past_event_correction_does_not_enqueue(self, mock_enqueue, mock_now):
        # Freeze "now" AFTER the event's start so old_start < now().
        mock_now.return_value = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)

        response = self.client.post(
            f'/studio/events/{self.event.pk}/edit',
            {
                'title': 'Past Workshop',
                'slug': 'past-workshop',
                # Move the date one day later (still in the past) — a
                # content-repo typo correction.
                'event_date': '11/04/2026',
                'event_time': '16:00',
                'duration_hours': '1',
                'timezone': 'UTC',
                'status': 'upcoming',
                'required_level': '0',
            },
            follow=True,
        )

        mock_enqueue.assert_not_called()
        message_strings = [m.message for m in response.context['messages']]
        self.assertFalse(
            any('Rescheduling notice' in m for m in message_strings),
        )

    @patch('django.utils.timezone.now')
    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_future_event_moved_into_past_does_not_enqueue(
        self, mock_enqueue, mock_now,
    ):
        """Future ``old_start`` but the new start is in the past: do not fire.

        Emailing "rescheduled to <past time>" is misleading — the new
        time is unreachable.
        """
        # Freeze "now" so the event start IS in the future.
        mock_now.return_value = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

        response = self.client.post(
            f'/studio/events/{self.event.pk}/edit',
            {
                'title': 'Past Workshop',
                'slug': 'past-workshop',
                # Move into the past relative to "now".
                'event_date': '15/03/2026',
                'event_time': '16:00',
                'duration_hours': '1',
                'timezone': 'UTC',
                'status': 'upcoming',
                'required_level': '0',
            },
            follow=True,
        )

        mock_enqueue.assert_not_called()
        message_strings = [m.message for m in response.context['messages']]
        self.assertFalse(
            any('Rescheduling notice' in m for m in message_strings),
        )


class StudioEventRescheduleSyncedTest(StaffUserMixin, TestCase):
    """Synced events: the rescheduling trigger MUST NOT wire to that branch.

    The synced branch only edits operational fields (status, platform,
    external_host, etc.) — start_datetime is owned by the content repo
    and is not exposed in the synced edit form. Any reviewer pushing to
    also detect there is rejected at issue level.
    """

    def setUp(self):
        self.client.login(**self.staff_credentials)
        future_start = timezone.now() + timedelta(days=30)
        self.event = Event.objects.create(
            title='Synced Event',
            slug='synced-event-resched',
            start_datetime=future_start,
            end_datetime=future_start + timedelta(hours=1),
            status='upcoming',
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='synced.md',
        )

    @patch('events.tasks.notify_reschedule.enqueue_reschedule_notice')
    def test_synced_edit_never_enqueues(self, mock_enqueue):
        self.client.post(
            f'/studio/events/{self.event.pk}/edit',
            {
                # Synced edits only post the operational fields.
                'status': 'upcoming',
                'platform': 'zoom',
            },
            follow=True,
        )
        mock_enqueue.assert_not_called()
