"""Surface-level tests for issue #713.

Verify that every user-facing surface that previously gated on
``status == 'upcoming'`` or ``status == 'completed'`` now responds to
the time-derived ``Event.is_upcoming`` / ``Event.is_past`` properties.

The shared fixture creates a "stale" event: stored ``status='upcoming'``
but ``end_datetime`` already in the past. Under the new properties this
must render as past everywhere without any cron run.
"""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event, EventRegistration

User = get_user_model()


def _stale_upcoming(slug='stale-evt', **overrides):
    """Event with stored ``status='upcoming'`` and end 1 minute ago."""
    now = timezone.now()
    defaults = {
        'title': overrides.pop('title', 'Stale Event'),
        'start_datetime': now - timedelta(hours=2),
        'end_datetime': now - timedelta(minutes=1),
        'status': 'upcoming',
        'required_level': 0,
    }
    defaults.update(overrides)
    return Event.objects.create(slug=slug, **defaults)


def _legacy_completed_future(slug='legacy-comp', **overrides):
    """Event with stored ``status='completed'`` but a FUTURE end."""
    now = timezone.now()
    defaults = {
        'title': overrides.pop('title', 'Legacy Completed Future'),
        'start_datetime': now + timedelta(hours=1),
        'end_datetime': now + timedelta(hours=2),
        'status': 'completed',
        'required_level': 0,
    }
    defaults.update(overrides)
    return Event.objects.create(slug=slug, **defaults)


class EventDetailHeaderTest(TestCase):
    """Stale upcoming row renders the Past pill on the detail page."""

    def test_stale_event_renders_past_pill(self):
        event = _stale_upcoming()
        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="event-status-pill"')
        self.assertContains(response, 'Past')
        self.assertNotContains(response, 'Upcoming')

    def test_legacy_completed_with_future_end_renders_upcoming_pill(self):
        event = _legacy_completed_future()
        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="event-status-pill"')
        self.assertContains(response, 'Upcoming')
        self.assertNotContains(response, 'Past')


class EventDetailRegistrationCardTest(TestCase):
    """Registration card disappears once effective end has passed."""

    def test_registration_card_hidden_for_stale_upcoming(self):
        event = _stale_upcoming()
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(
            response, 'data-testid="event-registration-card"',
        )
        self.assertNotContains(
            response, 'data-testid="event-external-join-card"',
        )

    def test_registration_card_visible_for_legacy_completed_future(self):
        """Time wins over legacy ``status='completed'``."""
        event = _legacy_completed_future()
        response = self.client.get(event.get_absolute_url())
        self.assertContains(
            response, 'data-testid="event-anonymous-email-form"',
        )


class EventDetailAttendeeChipTest(TestCase):
    """Attendee chip switches to past-tense for stale upcoming row."""

    def test_stale_event_shows_past_tense_attendee_chip(self):
        event = _stale_upcoming()
        user = User.objects.create_user(email='att1@test.com')
        EventRegistration.objects.create(event=event, user=user)
        response = self.client.get(event.get_absolute_url())
        # Past-tense copy on the chip.
        self.assertContains(response, 'attended')
        self.assertNotContains(response, 'is going')
        self.assertNotContains(response, 'are going')


class EventsListSectionsTest(TestCase):
    """Public ``/events`` list sorts events by time, not stored status."""

    def setUp(self):
        now = timezone.now()
        self.stale = Event.objects.create(
            slug='listed-stale',
            title='Listed Stale',
            start_datetime=now - timedelta(hours=2),
            end_datetime=now - timedelta(minutes=1),
            status='upcoming',
        )
        self.future = Event.objects.create(
            slug='listed-future',
            title='Listed Future',
            start_datetime=now + timedelta(hours=1),
            end_datetime=now + timedelta(hours=2),
            status='upcoming',
        )
        self.legacy_future = Event.objects.create(
            slug='listed-legacy-completed',
            title='Listed Legacy Future',
            start_datetime=now + timedelta(hours=3),
            end_datetime=now + timedelta(hours=4),
            status='completed',
        )

    def test_upcoming_section_excludes_stale_row(self):
        response = self.client.get('/events?filter=upcoming')
        body = response.content.decode()
        self.assertIn('Listed Future', body)
        self.assertIn('Listed Legacy Future', body)
        # Stale event has ``end_datetime`` in the past -> NOT upcoming.
        upcoming_section_start = body.find(
            'data-testid="events-upcoming-section"',
        )
        # Look for "Listed Stale" only after the upcoming section opens.
        self.assertGreater(upcoming_section_start, -1)
        # Should not appear anywhere on the upcoming-only page.
        self.assertNotIn('Listed Stale', body)


class RegistrationApi409Test(TestCase):
    """API returns 409 for a stale-upcoming event."""

    def test_register_returns_409_for_stale_upcoming(self):
        user = User.objects.create_user(email='rg@test.com', password='x')
        event = _stale_upcoming(slug='api-stale')
        client = Client()
        client.force_login(user)
        response = client.post(
            f'/api/events/{event.slug}/register',
            data='',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 409)
        body = json.loads(response.content)
        self.assertEqual(
            body.get('error'), 'Event is not open for registration',
        )

    def test_unregister_returns_409_for_stale_upcoming(self):
        user = User.objects.create_user(email='ug@test.com', password='x')
        event = _stale_upcoming(slug='api-stale-unreg')
        EventRegistration.objects.create(event=event, user=user)
        client = Client()
        client.force_login(user)
        response = client.delete(
            f'/api/events/{event.slug}/unregister',
        )
        self.assertEqual(response.status_code, 409)


class StudioFollowupGateTest(TestCase):
    """Studio "Send follow-up now" gate uses ``is_past``."""

    def setUp(self):
        self.staff = User.objects.create_user(
            email='s@example.com', password='x', is_staff=True,
        )

    def test_button_enabled_for_stale_upcoming_with_recording(self):
        event = _stale_upcoming(
            slug='stale-fu', recording_url='https://yt.test/v',
        )
        client = Client()
        client.force_login(self.staff)
        response = client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="send-followup-button"')
        self.assertNotContains(
            response,
            'data-testid="send-followup-button-disabled"',
        )

    def test_button_disabled_for_upcoming_future_with_recording(self):
        now = timezone.now()
        event = Event.objects.create(
            slug='future-fu',
            title='Future',
            start_datetime=now + timedelta(hours=2),
            end_datetime=now + timedelta(hours=3),
            status='upcoming',
            recording_url='https://yt.test/future',
        )
        client = Client()
        client.force_login(self.staff)
        response = client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(
            response,
            'data-testid="send-followup-button-disabled"',
        )
        # Disabled copy reflects the new time-based mental model.
        self.assertContains(response, 'Available once the event has ended')

    def test_view_allows_send_for_stale_upcoming(self):
        """POST to the send endpoint succeeds when ``is_past`` is True."""
        event = _stale_upcoming(
            slug='stale-fu-post', recording_url='https://yt.test/v',
        )
        client = Client()
        client.force_login(self.staff)
        response = client.post(
            f'/studio/events/{event.pk}/send-followup',
            follow=False,
        )
        # Should redirect back to the edit page (no error flash).
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(event.pk), response['Location'])


class StudioStatusLegacyNoteTest(TestCase):
    """Studio event form surfaces the legacy-status help line."""

    def test_help_line_renders(self):
        staff = User.objects.create_user(
            email='ss@example.com', password='x', is_staff=True,
        )
        event = _stale_upcoming(slug='helpline')
        client = Client()
        client.force_login(staff)
        response = client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(
            response, 'data-testid="event-status-legacy-note"',
        )
        self.assertContains(response, 'is a legacy status')


class StudioListTimeGroupingTest(TestCase):
    """Studio events list groups rows by a single time-derived status.

    A stale ``upcoming`` row lands in the dedicated Past view; a legacy
    ``completed`` row with a future end remains on the default Upcoming
    view.
    """

    def test_stale_upcoming_grouped_into_past(self):
        staff = User.objects.create_user(
            email='admin@test.com', password='x', is_staff=True,
        )
        event = _stale_upcoming(slug='list-stale')
        client = Client()
        client.force_login(staff)
        response = client.get('/studio/events/past/')
        past_pks = [e.pk for e in response.context['past_events']]
        self.assertIn(event.pk, past_pks)
        row = next(e for e in response.context['past_events'] if e.pk == event.pk)
        self.assertEqual(row.derived_status_label, 'Past')

    def test_legacy_completed_future_grouped_into_upcoming(self):
        staff = User.objects.create_user(
            email='admin2@test.com', password='x', is_staff=True,
        )
        event = _legacy_completed_future(slug='list-legacy')
        client = Client()
        client.force_login(staff)
        response = client.get('/studio/events/')
        upcoming_pks = [e.pk for e in response.context['upcoming_events']]
        self.assertIn(event.pk, upcoming_pks)
        row = next(
            e for e in response.context['upcoming_events'] if e.pk == event.pk
        )
        self.assertEqual(row.derived_status_label, 'Upcoming')


class CancelByTokenStaleEventTest(TestCase):
    """Cancel-by-token renders the finished state for stale upcoming."""

    def test_cancel_page_for_stale_event_shows_finished_state(self):
        from events.services.cancel_token import generate_cancel_token

        user = User.objects.create_user(email='c@test.com', password='x')
        event = _stale_upcoming(slug='cancel-stale')
        registration = EventRegistration.objects.create(
            event=event, user=user,
        )
        token = generate_cancel_token(registration)
        response = self.client.get(
            f'/events/{event.slug}/cancel-registration?token={token}',
        )
        self.assertEqual(response.status_code, 200)
        # The "already started or finished" copy renders.
        self.assertContains(response, 'already started or finished')
        # Registration row is preserved.
        self.assertTrue(
            EventRegistration.objects.filter(pk=registration.pk).exists(),
        )


class DashboardExcludesPastFromUpcomingTest(TestCase):
    """Member dashboard ``_get_upcoming_events`` excludes past starts."""

    def test_legacy_completed_with_future_start_is_returned(self):
        """A legacy ``status='completed'`` row with a future start
        should still appear in the user's upcoming registrations."""
        from content.views.home import _get_upcoming_events

        user = User.objects.create_user(email='d@test.com', password='x')
        event = _legacy_completed_future(slug='dash-future')
        EventRegistration.objects.create(event=event, user=user)
        results = _get_upcoming_events(user)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].pk, event.pk)

    def test_cancelled_event_is_excluded(self):
        from content.views.home import _get_upcoming_events

        user = User.objects.create_user(email='dc@test.com', password='x')
        now = timezone.now()
        event = Event.objects.create(
            slug='dash-cancelled',
            title='Dash Cancelled',
            start_datetime=now + timedelta(hours=2),
            status='cancelled',
        )
        EventRegistration.objects.create(event=event, user=user)
        self.assertEqual(_get_upcoming_events(user), [])
