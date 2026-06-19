"""Tests for event join redirect with click tracking - issue #186."""

import datetime
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from freezegun import freeze_time

from content.models import Workshop
from events.models import Event, EventJoinClick, EventRegistration
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _move_event_to(event, *, start_offset, end_offset=None):
    """Move an event so ``start_datetime = now + start_offset``.

    Used by tests that need to land inside one of the join-redirect time
    windows (issue #704). ``end_offset`` is optional; when omitted, the
    event keeps its existing ``end_datetime``.
    """
    now = timezone.now()
    event.start_datetime = now + start_offset
    if end_offset is not None:
        event.end_datetime = now + end_offset
    event.save(update_fields=['start_datetime', 'end_datetime'])


class EventJoinRedirectTest(TierSetupMixin, TestCase):
    """Tests for GET /events/<slug>/join endpoint."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@example.com',
            password='testpass123',
        )
        cls.staff_user = User.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            is_staff=True,
        )
        cls.upcoming_event = Event.objects.create(
            title='Upcoming Event',
            slug='upcoming-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/123456',
        )
        cls.no_url_event = Event.objects.create(
            title='No URL Event',
            slug='no-url-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
            zoom_join_url='',
        )
        cls.completed_event = Event.objects.create(
            title='Past Event',
            slug='past-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
        )
        cls.completed_event_with_recording = Event.objects.create(
            title='Past Event With Recording',
            slug='past-event-recording',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=abc',
        )
        # Issue #426: the "Watch the recording" CTA on the join-unavailable
        # page only appears when the event has a linked Workshop, since
        # recording playback lives on the workshop video page.
        cls.workshop_for_recording = Workshop.objects.create(
            slug='past-workshop',
            title='Past Workshop',
            date=datetime.date(2025, 1, 1),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            event=cls.completed_event_with_recording,
        )
        cls.completed_event_no_workshop = Event.objects.create(
            title='Past Event No Workshop',
            slug='past-event-no-workshop',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=orphan',
        )
        cls.draft_event = Event.objects.create(
            title='Draft Event',
            slug='draft-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
            zoom_join_url='https://zoom.us/j/999',
        )
        # Register user for relevant events
        for event in [
            cls.upcoming_event,
            cls.no_url_event,
            cls.completed_event,
            cls.completed_event_with_recording,
            cls.completed_event_no_workshop,
        ]:
            EventRegistration.objects.create(event=event, user=cls.user)

    def test_join_redirect_records_click_and_redirects(self):
        """Registered user for upcoming event with join URL gets 302 to Zoom.

        Issue #704: the redirect only fires inside the join window, so
        we move the event to ``now + 1 min`` (well inside the
        ``delta <= 5 min`` branch).
        """
        _move_event_to(self.upcoming_event, start_offset=timedelta(minutes=1))
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/upcoming-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://zoom.us/j/123456')
        self.assertEqual(
            EventJoinClick.objects.filter(
                event=self.upcoming_event, user=self.user,
            ).count(),
            1,
        )

    def test_join_redirect_requires_login(self):
        """Anonymous user gets redirected to login with next parameter."""
        response = self.client.get('/events/upcoming-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertIn('next=/events/upcoming-event/join', response['Location'])

    def test_join_redirect_no_url_shows_unavailable(self):
        """Event without zoom_join_url shows unavailable page."""
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/no-url-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'The join link is not available yet')
        # No click should be recorded
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_join_redirect_past_event_shows_ended(self):
        """Completed event shows ended page."""
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/past-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')
        # No click should be recorded
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_join_redirect_past_event_with_workshop_links_to_workshop(self):
        """Completed event with linked Workshop sends users to the workshop.

        Issue #426: recording playback lives on the workshop video page,
        so the "Watch the recording" CTA on the join-unavailable page
        points at ``/workshops/<slug>``.
        """
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/past-event-recording/join')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Watch the recording')
        self.assertContains(
            response, 'href="/workshops/past-workshop"',
        )
        # Must not point at the announcement-only event page or the
        # retired standalone recording surface.
        self.assertNotContains(response, '/event-recordings/')

    def test_join_redirect_past_event_without_workshop_omits_recording_cta(self):
        """Completed event with recording_url but no linked Workshop has no CTA.

        Issue #426: there is no canonical recording surface to point at
        when no Workshop has been linked, so the CTA is suppressed.
        """
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/past-event-no-workshop/join')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This event has ended')
        self.assertNotContains(response, 'Watch the recording')

    def test_join_redirect_draft_event_404(self):
        """Draft event returns 404 for non-staff user."""
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/draft-event/join')
        self.assertEqual(response.status_code, 404)

    def test_join_redirect_unregistered_user_redirected_to_detail(self):
        """Authenticated but unregistered user is redirected to event detail.

        Issue #673: redirects through the canonical ``/events/<id>/<slug>``
        URL via ``Event.get_absolute_url``.
        """
        User.objects.create_user(
            email='unregistered@example.com',
            password='testpass123',
        )
        self.client.login(email='unregistered@example.com', password='testpass123')
        response = self.client.get('/events/upcoming-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            self.upcoming_event.get_absolute_url(),
        )

    def test_multiple_clicks_tracked(self):
        """Each visit creates a new click record.

        Issue #704: the event must be inside the join window for any
        click to be recorded.
        """
        _move_event_to(self.upcoming_event, start_offset=timedelta(minutes=1))
        self.client.login(email='member@example.com', password='testpass123')
        self.client.get('/events/upcoming-event/join')
        self.client.get('/events/upcoming-event/join')
        self.client.get('/events/upcoming-event/join')
        self.assertEqual(
            EventJoinClick.objects.filter(
                event=self.upcoming_event, user=self.user,
            ).count(),
            3,
        )


class EventJoinTimeWindowTest(TierSetupMixin, TestCase):
    """Time-gate the /events/<slug>/join redirect (issue #704).

    The view branches on ``timezone.now()`` vs ``event.start_datetime``
    and ``event.end_datetime``:

    - ``delta > 10 min``                       -> too-early page
    - ``5 min < delta <= 10 min``              -> countdown page
    - ``delta <= 5 min`` AND inside live window -> 302 + EventJoinClick
    - ``now > end_or_grace_cutoff``            -> 'past' unavailable page
    """

    EVENT_START = '2026-03-01T15:00:00Z'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='time@example.com',
            password='testpass123',
        )
        cls.event = Event.objects.create(
            title='Window Event',
            slug='window-event',
            start_datetime=datetime.datetime(
                2026, 3, 1, 15, 0, 0, tzinfo=datetime.UTC,
            ),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/stub',
        )
        EventRegistration.objects.create(event=cls.event, user=cls.user)

    def _login(self):
        self.client.login(email='time@example.com', password='testpass123')

    @freeze_time('2026-03-01T14:30:00Z')  # 30 min before start
    def test_too_early_page_when_more_than_10_min_before(self):
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_too_early.html')
        self.assertNotIn('Location', response)
        self.assertEqual(EventJoinClick.objects.count(), 0)
        self.assertContains(response, 'data-testid="event-join-too-early"')
        self.assertContains(response, 'Window Event')
        # The "Back to event details" link points at the canonical
        # id+slug URL via Event.get_absolute_url().
        self.assertContains(
            response, f'href="{self.event.get_absolute_url()}"',
        )

    @freeze_time('2026-03-01T14:52:00Z')  # 8 min before start
    def test_countdown_page_in_5_to_10_min_window(self):
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_countdown.html')
        self.assertNotIn('Location', response)
        self.assertEqual(EventJoinClick.objects.count(), 0)
        self.assertContains(response, 'data-testid="event-join-countdown"')
        self.assertContains(
            response, 'data-testid="event-join-countdown-timer"',
        )

    @freeze_time('2026-03-01T14:52:00Z')  # 8 min before start
    def test_countdown_page_contains_exactly_one_meta_refresh(self):
        self._login()
        response = self.client.get('/events/window-event/join')
        body = response.content.decode()
        # Exact-match the tag the AC names; assertContains with count=1
        # would also catch this but we want to assert against the raw
        # attribute pair to avoid matching unrelated meta tags.
        self.assertEqual(
            body.count('<meta http-equiv="refresh" content="30">'),
            1,
        )

    @freeze_time('2026-03-01T14:52:00Z')  # 8 min before start -> 3 min to open
    def test_countdown_timer_initial_text_matches_remaining(self):
        self._login()
        response = self.client.get('/events/window-event/join')
        # delta_to_open = 8 min - 5 min = 3 min 0 sec.
        self.assertContains(response, '>3 min 0 sec</span>')

    @freeze_time('2026-03-01T14:54:00Z')  # 6 min before start
    def test_six_minutes_before_start_does_not_record_or_redirect(self):
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_countdown.html')
        self.assertNotIn('Location', response)
        self.assertEqual(EventJoinClick.objects.count(), 0)
        registration = EventRegistration.objects.get(
            event=self.event, user=self.user,
        )
        self.assertIsNone(registration.joined_at)

    @freeze_time('2026-03-01T14:56:00Z')  # 4 min before start
    def test_redirect_to_zoom_within_5_min(self):
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://zoom.us/j/stub')
        self.assertEqual(
            EventJoinClick.objects.filter(
                event=self.event, user=self.user,
            ).count(),
            1,
        )
        registration = EventRegistration.objects.get(
            event=self.event, user=self.user,
        )
        self.assertIsNotNone(registration.joined_at)

    @freeze_time('2026-03-01T15:30:00Z')  # 30 min after start, no end_datetime
    def test_redirect_during_live_event_with_no_end_datetime(self):
        """Event is live under the 1h fallback (issue #712)."""
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://zoom.us/j/stub')

    @freeze_time('2026-03-01T16:01:00Z')  # 1h 1min after start, no end_datetime
    def test_past_grace_cutoff_renders_unavailable_when_no_end_datetime(self):
        """Past the 1h fallback: 'past' unavailable page renders.

        Issue #712: the fallback for events with null ``end_datetime``
        is ``start + 1h`` (matches ``complete_finished_events``, the
        .ics export, and Studio's default duration). At ``start + 61
        min`` we are just past the cutoff.
        """
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')
        self.assertEqual(EventJoinClick.objects.count(), 0)

    @freeze_time('2026-03-01T17:00:00Z')  # 2h after start, no end_datetime
    def test_past_two_hour_mark_renders_unavailable_when_no_end_datetime(self):
        """Regression guard vs the pre-issue-#712 3h grace.

        Under the old fallback (``start + 3h``) this request would have
        302'd to Zoom. Under the new 1h fallback (issue #712) it must
        render the 'past' unavailable page.
        """
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_stale_upcoming_status_blocked_past_explicit_end_datetime(self):
        """``status='upcoming'`` past ``end_datetime`` still blocks.

        Issue #712: pin the invariant that the time-gate is independent
        of ``event.status``. A cron-delayed row with stale
        ``status='upcoming'`` AND ``now > end_datetime`` falls through
        the status branch (which only catches ``completed``/
        ``cancelled``) and is caught by the timestamp branch.
        """
        # Set explicit end_datetime in the past while keeping status='upcoming'.
        _move_event_to(
            self.event,
            start_offset=timedelta(hours=-2),
            end_offset=timedelta(minutes=-30),
        )
        # Sanity check: status is still 'upcoming' (cron has not run).
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'upcoming')
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_stale_upcoming_status_blocked_past_one_hour_fallback(self):
        """``status='upcoming'`` past the 1h fallback (null end) still blocks.

        Issue #712: same invariant as
        ``test_stale_upcoming_status_blocked_past_explicit_end_datetime``
        but for events with null ``end_datetime``. ``start + 2h``
        without an explicit end is past the 1h fallback cutoff, so the
        request must NOT 302 and no ``EventJoinClick`` must be written.
        """
        _move_event_to(self.event, start_offset=timedelta(hours=-2))
        # No end_datetime, still 'upcoming'.
        self.event.refresh_from_db()
        self.assertIsNone(self.event.end_datetime)
        self.assertEqual(self.event.status, 'upcoming')
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_cancelled_event_blocked_inside_live_window(self):
        """``status='cancelled'`` short-circuits before the time branch.

        Issue #712: the status-based gate already catches cancelled
        events; this test pins the regression so a future refactor that
        re-orders the branches still blocks Zoom redirects on cancelled
        rows whose timestamps would otherwise be inside the live
        window.
        """
        # Move the event so it is live (start 30 min ago), then cancel it.
        _move_event_to(self.event, start_offset=timedelta(minutes=-30))
        self.event.status = 'cancelled'
        self.event.save(update_fields=['status'])
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_redirect_at_one_minute_before_end_when_end_is_set(self):
        """When ``end_datetime`` is set it is the cutoff."""
        # Move the event so it starts 90 min ago and ends in 1 minute,
        # i.e. the live window is still open.
        _move_event_to(
            self.event,
            start_offset=timedelta(minutes=-90),
            end_offset=timedelta(minutes=1),
        )
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://zoom.us/j/stub')

    def test_past_cutoff_when_end_datetime_in_past(self):
        """After ``end_datetime`` the 'past' page renders."""
        _move_event_to(
            self.event,
            start_offset=timedelta(hours=-2),
            end_offset=timedelta(minutes=-31),
        )
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')
        self.assertEqual(EventJoinClick.objects.count(), 0)

    @freeze_time('2026-03-01T14:56:00Z')  # 4 min before start
    def test_non_registered_user_still_redirected_to_detail_inside_window(self):
        """Unregistered branch wins even inside the redirect window."""
        User.objects.create_user(
            email='outsider@example.com',
            password='testpass123',
        )
        self.client.login(email='outsider@example.com', password='testpass123')
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'], self.event.get_absolute_url(),
        )
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_cancelled_event_still_unavailable_inside_window(self):
        """``status='cancelled'`` overrides the time-window logic.

        Issue #713: legacy ``status='completed'`` alone is no longer
        enough — past detection is time-driven. Cancellation still
        wins over time so the join redirect still bails on a cancelled
        event whose start window is open.
        """
        _move_event_to(
            self.event,
            start_offset=timedelta(minutes=-4),
            end_offset=timedelta(minutes=56),
        )
        self.event.status = 'cancelled'
        self.event.save(update_fields=['status'])
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')

    @freeze_time('2026-03-01T14:56:00Z')
    def test_empty_zoom_url_still_unavailable_inside_window(self):
        """An empty ``zoom_join_url`` overrides the time-window logic."""
        self.event.zoom_join_url = ''
        self.event.save(update_fields=['zoom_join_url'])
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'The join link is not available yet')

    @freeze_time('2026-03-01T14:30:00Z')
    def test_too_early_page_uses_user_preferred_timezone(self):
        """``format_user_datetime`` renders in the registered user's TZ."""
        self.user.preferred_timezone = 'Europe/Berlin'
        self.user.save(update_fields=['preferred_timezone'])
        self._login()
        response = self.client.get('/events/window-event/join')
        # 15:00 UTC == 16:00 Berlin (CET, +01:00) on 2026-03-01.
        self.assertContains(response, '16:00 Europe/Berlin')

    @freeze_time('2026-03-01T14:30:00Z')
    def test_too_early_page_falls_back_to_utc_for_user_without_tz(self):
        """No ``preferred_timezone`` -> the suffix is the literal 'UTC'."""
        self._login()
        response = self.client.get('/events/window-event/join')
        self.assertContains(response, '15:00 UTC')


class EventEffectiveEndDatetimePropertyTest(TestCase):
    """Test ``Event.effective_end_datetime`` (issue #712).

    Single source of truth for "when did this event end?" consumed by
    the join-redirect view, the ``complete_finished_events`` cron, the
    ``.ics`` export, and the calendar deep-link builders.
    """

    def test_returns_explicit_end_datetime_when_set(self):
        start = datetime.datetime(2026, 3, 1, 15, 0, 0, tzinfo=datetime.UTC)
        end = datetime.datetime(2026, 3, 1, 17, 30, 0, tzinfo=datetime.UTC)
        event = Event.objects.create(
            title='Explicit End',
            slug='explicit-end',
            start_datetime=start,
            end_datetime=end,
            status='upcoming',
        )
        self.assertEqual(event.effective_end_datetime, end)

    def test_falls_back_to_start_plus_one_hour_when_null(self):
        start = datetime.datetime(2026, 3, 1, 15, 0, 0, tzinfo=datetime.UTC)
        event = Event.objects.create(
            title='Implicit End',
            slug='implicit-end',
            start_datetime=start,
            end_datetime=None,
            status='upcoming',
        )
        expected = start + timedelta(hours=1)
        self.assertEqual(event.effective_end_datetime, expected)


class EventJoinedAtTest(TierSetupMixin, TestCase):
    """Issue #936: ``EventRegistration.joined_at`` is set on the first
    live-window join click and left null in every other branch.

    The branch selection is time-driven, so we drive each branch with
    ``freeze_time`` against a fixed event start, mirroring
    ``EventJoinTimeWindowTest``.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='attendee@example.com',
            password='testpass123',
        )
        cls.event = Event.objects.create(
            title='Joined Window Event',
            slug='joined-window-event',
            start_datetime=datetime.datetime(
                2026, 3, 1, 15, 0, 0, tzinfo=datetime.UTC,
            ),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/joined',
        )
        EventRegistration.objects.create(event=cls.event, user=cls.user)

    def _login(self):
        self.client.login(email='attendee@example.com', password='testpass123')

    def _reg(self):
        return EventRegistration.objects.get(event=self.event, user=self.user)

    @freeze_time('2026-03-01T14:56:00Z')  # 4 min before start -> live window
    def test_live_window_click_sets_joined_at(self):
        self._login()
        response = self.client.get('/events/joined-window-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://zoom.us/j/joined')
        reg = self._reg()
        self.assertIsNotNone(reg.joined_at)
        self.assertEqual(
            reg.joined_at,
            datetime.datetime(2026, 3, 1, 14, 56, 0, tzinfo=datetime.UTC),
        )
        # The per-click log is still written.
        self.assertEqual(
            EventJoinClick.objects.filter(
                event=self.event, user=self.user,
            ).count(),
            1,
        )

    @freeze_time('2026-03-01T14:30:00Z')  # 30 min before -> too-early branch
    def test_too_early_branch_leaves_joined_at_null(self):
        self._login()
        response = self.client.get('/events/joined-window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_too_early.html')
        self.assertIsNone(self._reg().joined_at)
        self.assertEqual(EventJoinClick.objects.count(), 0)

    @freeze_time('2026-03-01T14:52:00Z')  # 8 min before -> countdown branch
    def test_countdown_branch_leaves_joined_at_null(self):
        self._login()
        response = self.client.get('/events/joined-window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_countdown.html')
        self.assertIsNone(self._reg().joined_at)

    @freeze_time('2026-03-01T16:01:00Z')  # past the 1h fallback -> past branch
    def test_past_branch_leaves_joined_at_null(self):
        self._login()
        response = self.client.get('/events/joined-window-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertIsNone(self._reg().joined_at)

    def test_second_live_window_click_does_not_overwrite_joined_at(self):
        """First-join-wins: a second click keeps the original timestamp."""
        self._login()
        with freeze_time('2026-03-01T14:56:00Z'):
            self.client.get('/events/joined-window-event/join')
        first_joined_at = self._reg().joined_at
        self.assertEqual(
            first_joined_at,
            datetime.datetime(2026, 3, 1, 14, 56, 0, tzinfo=datetime.UTC),
        )
        # A second click a minute later, still inside the live window.
        with freeze_time('2026-03-01T14:57:00Z'):
            self.client.get('/events/joined-window-event/join')
        reg = self._reg()
        # joined_at unchanged; a second click row exists.
        self.assertEqual(reg.joined_at, first_joined_at)
        self.assertEqual(
            EventJoinClick.objects.filter(
                event=self.event, user=self.user,
            ).count(),
            2,
        )

    @freeze_time('2026-03-01T14:56:00Z')  # live window
    def test_unregistered_user_writes_nothing(self):
        """An unregistered user is redirected away; no registration row
        is created and no click/joined_at is written."""
        User.objects.create_user(
            email='nobody@example.com',
            password='testpass123',
        )
        self.client.login(email='nobody@example.com', password='testpass123')
        response = self.client.get('/events/joined-window-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.event.get_absolute_url())
        self.assertEqual(EventJoinClick.objects.count(), 0)
        # Only the one pre-existing registration (the setUpTestData user).
        self.assertEqual(
            EventRegistration.objects.filter(event=self.event).count(), 1,
        )
        # The pre-existing registration is untouched.
        self.assertIsNone(self._reg().joined_at)


class EventJoinClickCountPropertyTest(TestCase):
    """Test the join_click_count property on Event."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='counter@example.com',
            password='testpass123',
        )
        cls.event = Event.objects.create(
            title='Count Event',
            slug='count-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )

    def test_join_click_count_returns_total(self):
        """join_click_count property returns total number of clicks."""
        self.assertEqual(self.event.join_click_count, 0)
        EventJoinClick.objects.create(event=self.event, user=self.user)
        EventJoinClick.objects.create(event=self.event, user=self.user)
        self.assertEqual(self.event.join_click_count, 2)


class StudioJoinClickCountTest(TierSetupMixin, TestCase):
    """Test that Studio event edit page shows join click count."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff_user = User.objects.create_user(
            email='studio@example.com',
            password='testpass123',
            is_staff=True,
        )
        cls.user = User.objects.create_user(
            email='clicker@example.com',
            password='testpass123',
        )
        cls.event = Event.objects.create(
            title='Studio Event',
            slug='studio-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )

    def test_join_click_count_in_studio(self):
        """Studio event edit page displays join click count."""
        # Create 5 clicks
        for _ in range(5):
            EventJoinClick.objects.create(event=self.event, user=self.user)

        self.client.login(email='studio@example.com', password='testpass123')
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Join clicks')
        # Check the count is shown via the data-testid element
        content = response.content.decode()
        self.assertIn('data-testid="join-click-count"', content)
        self.assertIn('>5<', content)
