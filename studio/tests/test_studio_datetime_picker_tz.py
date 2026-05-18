"""Studio datetime picker TZ defaulting and round-trip (issue #665).

Covers the acceptance criteria for the New/Edit event forms and the
event-series forms:

- New form defaults the TZ chooser to ``request.user.preferred_timezone``.
- New form falls back to ``settings.TIME_ZONE`` (UTC) when the admin
  has no preferred TZ.
- Edit form pre-selects the event's stored TZ regardless of who is
  signed in.
- Submitting (date, time, tz) persists ``start_datetime`` as the
  UTC instant equivalent.
- Editing an event preserves the stored TZ during a no-op round trip.
- Invalid TZ POST values are rejected with a field-level error.
- The hardcoded ``Europe/Berlin`` default is gone from the view and
  template defaults.
"""

import re
from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from events.models import Event, EventSeries

User = get_user_model()


class _AdminMixin:
    """Create staff users with controlled ``preferred_timezone`` values."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ny_admin = User.objects.create_user(
            email='ny-admin-665@test.com',
            password='pw',
            is_staff=True,
        )
        cls.ny_admin.preferred_timezone = 'America/New_York'
        cls.ny_admin.save(update_fields=['preferred_timezone'])

        cls.berlin_admin = User.objects.create_user(
            email='berlin-admin-665@test.com',
            password='pw',
            is_staff=True,
        )
        cls.berlin_admin.preferred_timezone = 'Europe/Berlin'
        cls.berlin_admin.save(update_fields=['preferred_timezone'])

        cls.unset_admin = User.objects.create_user(
            email='no-tz-admin-665@test.com',
            password='pw',
            is_staff=True,
        )
        # preferred_timezone defaults to '' on the User model.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_python_noncode(source):
    """Remove comments and docstrings so literal-search tests ignore prose.

    Uses ``tokenize`` so triple-quoted docstrings, module docstrings,
    and ``#`` comments are all stripped; the remaining text is only the
    runtime literals/identifiers we care about for the "no hardcode"
    invariant in issue #665.
    """
    import io
    import token
    import tokenize

    pieces = []
    last_token_type = None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING:
            # Treat docstring-shaped strings (those that begin a logical
            # line at module / function / class scope) as documentation.
            if last_token_type in (None, token.NEWLINE, token.INDENT,
                                   token.DEDENT, token.NL):
                last_token_type = tok.type
                continue
        pieces.append(tok.string)
        last_token_type = tok.type
    return ' '.join(pieces)


def _selected_tz_value(html):
    """Return the value of the ``<option … selected>`` inside the TZ select.

    The partial renders the TZ select with id ``dtp-<instance_id>-tz``;
    we accept any instance suffix (event, series, add, …).
    """
    tz_select_match = re.search(
        r'<select[^>]*id="dtp-[\w-]+-tz"[^>]*>(.*?)</select>',
        html,
        re.DOTALL,
    )
    assert tz_select_match, 'TZ <select> not found in form HTML'
    block = tz_select_match.group(1)
    selected = re.search(
        r'<option[^>]+value="([^"]+)"[^>]*\bselected\b', block,
    )
    assert selected, 'No <option … selected> inside the TZ <select>'
    return selected.group(1)


# ---------------------------------------------------------------------------
# New event form — TZ default depends on the signed-in admin
# ---------------------------------------------------------------------------


class NewEventTzDefaultTest(_AdminMixin, TestCase):
    """The New event form preselects the admin's ``preferred_timezone``."""

    def test_ny_admin_sees_new_york_selected(self):
        self.client.login(email='ny-admin-665@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _selected_tz_value(response.content.decode()),
            'America/New_York',
        )

    def test_admin_without_preference_falls_back_to_utc(self):
        self.client.login(email='no-tz-admin-665@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _selected_tz_value(response.content.decode()),
            'UTC',
        )

    def test_form_default_value_is_not_europe_berlin(self):
        """Issue #665: 'Europe/Berlin' must not be the default fallback."""
        self.client.login(email='no-tz-admin-665@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        # If the page selects Europe/Berlin by default this regression
        # check will fail. We explicitly assert UTC is the active label
        # for an admin without a preference.
        self.assertNotEqual(
            _selected_tz_value(response.content.decode()),
            'Europe/Berlin',
        )

    def test_view_module_no_longer_hardcodes_europe_berlin(self):
        """``studio/views/events.py`` no longer uses 'Europe/Berlin' as a default.

        Comments and docstrings that reference the historical hardcode
        (for traceability) are allowed; code literals are not.
        """
        from pathlib import Path

        from studio.views import events as events_view

        source = Path(events_view.__file__).read_text()
        # Strip comments and docstrings before scanning for literals.
        stripped = _strip_python_noncode(source)
        self.assertNotIn(
            "'Europe/Berlin'",
            stripped,
            "'Europe/Berlin' literal must be removed from view code (issue #665)",
        )
        self.assertNotIn(
            '"Europe/Berlin"',
            stripped,
            '"Europe/Berlin" literal must be removed from view code (issue #665)',
        )

    def test_event_form_template_no_longer_hardcodes_europe_berlin(self):
        """The event form template no longer contains 'Europe/Berlin'."""
        from pathlib import Path

        from django.conf import settings

        for relative in ('studio/events/form.html',):
            for base in settings.TEMPLATES[0]['DIRS']:
                path = Path(base) / relative
                if path.exists():
                    self.assertNotIn(
                        'Europe/Berlin',
                        path.read_text(),
                        f"'Europe/Berlin' must not appear in {relative}",
                    )
                    break


# ---------------------------------------------------------------------------
# Edit event form — TZ comes from the event row, not the admin
# ---------------------------------------------------------------------------


class EditEventTzPreservationTest(_AdminMixin, TestCase):
    """The Edit form preselects ``event.timezone`` even for a different admin."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ny_event = Event.objects.create(
            title='Edit NY Event', slug='edit-ny-event-665',
            # 2027-06-15T18:30Z = 2027-06-15T14:30 in America/New_York (DST).
            start_datetime=datetime(2027, 6, 15, 18, 30, tzinfo=UTC),
            end_datetime=datetime(2027, 6, 15, 19, 30, tzinfo=UTC),
            timezone='America/New_York',
            origin='studio',
        )

    def test_berlin_admin_sees_event_tz_not_their_own(self):
        self.client.login(email='berlin-admin-665@test.com', password='pw')
        response = self.client.get(f'/studio/events/{self.ny_event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _selected_tz_value(response.content.decode()),
            'America/New_York',
        )

    def test_edit_form_renders_time_in_event_tz(self):
        """The HH:MM input shows 14:30 (NY) not 18:30 (UTC) or 20:30 (Berlin)."""
        self.client.login(email='berlin-admin-665@test.com', password='pw')
        response = self.client.get(f'/studio/events/{self.ny_event.pk}/edit')
        html = response.content.decode()
        match = re.search(
            r'name="event_time"[^>]*value="([^"]+)"', html,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '14:30')

    def test_no_op_round_trip_preserves_tz_and_instant(self):
        """Re-submitting the same form leaves start_datetime + timezone alone."""
        self.client.login(email='berlin-admin-665@test.com', password='pw')
        original_start = self.ny_event.start_datetime
        original_tz = self.ny_event.timezone
        response = self.client.post(
            f'/studio/events/{self.ny_event.pk}/edit',
            {
                'title': self.ny_event.title,
                'slug': self.ny_event.slug,
                'description': '',
                'event_date': '15/06/2027',
                'event_time': '14:30',
                'duration_hours': '1',
                'timezone': 'America/New_York',
                'platform': 'zoom',
                'status': 'draft',
                'required_level': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.ny_event.refresh_from_db()
        self.assertEqual(self.ny_event.start_datetime, original_start)
        self.assertEqual(self.ny_event.timezone, original_tz)


# ---------------------------------------------------------------------------
# Submit (date, time, tz) round-trip persists UTC instant
# ---------------------------------------------------------------------------


class CreateEventRoundTripUtcTest(_AdminMixin, TestCase):
    """date=2027-06-15, time=14:30, tz=America/New_York → start=18:30 UTC."""

    def test_post_persists_utc_instant(self):
        self.client.login(email='ny-admin-665@test.com', password='pw')
        response = self.client.post('/studio/events/new', {
            'title': 'NY Office Hours',
            'slug': 'ny-office-hours-665',
            'event_date': '15/06/2027',
            'event_time': '14:30',
            'duration_hours': '1',
            'timezone': 'America/New_York',
            'status': 'draft',
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(slug='ny-office-hours-665')
        self.assertEqual(event.timezone, 'America/New_York')
        self.assertEqual(
            event.start_datetime,
            datetime(2027, 6, 15, 18, 30, tzinfo=UTC),
        )

    def test_invalid_tz_post_is_rejected_with_field_error(self):
        self.client.login(email='ny-admin-665@test.com', password='pw')
        response = self.client.post('/studio/events/new', {
            'title': 'Bogus TZ Event',
            'slug': 'bogus-tz-665',
            'event_date': '15/06/2027',
            'event_time': '14:30',
            'duration_hours': '1',
            'timezone': 'Mars/Olympus_Mons',
            'status': 'draft',
        })
        # The form re-renders (200) with a TZ field error and no row.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="error-timezone"')
        self.assertFalse(Event.objects.filter(slug='bogus-tz-665').exists())


# ---------------------------------------------------------------------------
# Event series form mirrors the event form
# ---------------------------------------------------------------------------


class NewEventSeriesTzDefaultTest(_AdminMixin, TestCase):
    def test_ny_admin_sees_new_york_selected_on_series_form(self):
        self.client.login(email='ny-admin-665@test.com', password='pw')
        response = self.client.get('/studio/event-series/new')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _selected_tz_value(response.content.decode()),
            'America/New_York',
        )

    def test_admin_without_preference_falls_back_to_utc_on_series(self):
        self.client.login(email='no-tz-admin-665@test.com', password='pw')
        response = self.client.get('/studio/event-series/new')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _selected_tz_value(response.content.decode()),
            'UTC',
        )

    def test_series_view_module_no_longer_hardcodes_europe_berlin(self):
        from pathlib import Path

        from studio.views import event_series as series_view

        source = Path(series_view.__file__).read_text()
        stripped = _strip_python_noncode(source)
        self.assertNotIn("'Europe/Berlin'", stripped)
        self.assertNotIn('"Europe/Berlin"', stripped)


class CreateSeriesRoundTripUtcTest(_AdminMixin, TestCase):
    """Creating a series in NY stores each occurrence as a UTC instant."""

    def test_first_occurrence_persists_in_utc(self):
        self.client.login(email='ny-admin-665@test.com', password='pw')
        response = self.client.post('/studio/event-series/new', {
            'name': 'NY Workshops',
            'slug': 'ny-workshops-665',
            'description': '',
            'start_date': '15/06/2027',
            'start_time': '14:30',
            'duration_hours': '1',
            'occurrences': '2',
            'timezone': 'America/New_York',
            'required_level': '0',
            'kind': 'standard',
            'platform': 'zoom',
        })
        self.assertEqual(response.status_code, 302)
        series = EventSeries.objects.get(slug='ny-workshops-665')
        self.assertEqual(series.timezone, 'America/New_York')
        events = list(series.events.order_by('series_position'))
        self.assertEqual(len(events), 2)
        self.assertEqual(
            events[0].start_datetime,
            datetime(2027, 6, 15, 18, 30, tzinfo=UTC),
        )
        # Seven days later in the same local TZ. Since no DST flip
        # happens between 15 June and 22 June in NY, the UTC delta is
        # exactly 7 days.
        self.assertEqual(
            events[1].start_datetime - events[0].start_datetime,
            timedelta(days=7),
        )


# ---------------------------------------------------------------------------
# Add-occurrence form inherits the series TZ
# ---------------------------------------------------------------------------


class AddOccurrenceTzInheritanceTest(_AdminMixin, TestCase):
    """The detail page's add-occurrence form is anchored to the series TZ."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Series NY',
            slug='series-ny-665',
            cadence='weekly',
            cadence_weeks=1,
            day_of_week=1,
            start_time=datetime(2000, 1, 1, 14, 30).time(),
            timezone='America/New_York',
        )

    def test_detail_page_picker_defaults_to_series_tz(self):
        """Even a Berlin admin sees the series TZ in the add-occurrence form."""
        self.client.login(email='berlin-admin-665@test.com', password='pw')
        response = self.client.get(
            reverse(
                'studio_event_series_detail',
                kwargs={'series_id': self.series.pk},
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _selected_tz_value(response.content.decode()),
            'America/New_York',
        )

    def test_add_occurrence_persists_utc_using_series_tz(self):
        self.client.login(email='berlin-admin-665@test.com', password='pw')
        response = self.client.post(
            reverse(
                'studio_event_series_add_occurrence',
                kwargs={'series_id': self.series.pk},
            ),
            {
                'start_date': '15/06/2027',
                'duration_hours': '1',
                # Leave 'timezone' off — the view defaults to the
                # series TZ (America/New_York) under issue #665.
            },
        )
        self.assertEqual(response.status_code, 302)
        events = list(self.series.events.order_by('series_position'))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].timezone, 'America/New_York')
        self.assertEqual(
            events[0].start_datetime,
            datetime(2027, 6, 15, 18, 30, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------
# Shared partial wiring
# ---------------------------------------------------------------------------


class SharedPartialIsIncludedTest(_AdminMixin, TestCase):
    """All three forms render the shared ``_partials/datetime_picker.html``."""

    @override_settings(DEBUG=True)
    def test_event_form_uses_shared_partial(self):
        self.client.login(email='ny-admin-665@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        self.assertContains(response, 'data-testid="studio-datetime-picker"')

    @override_settings(DEBUG=True)
    def test_event_series_form_uses_shared_partial(self):
        self.client.login(email='ny-admin-665@test.com', password='pw')
        response = self.client.get('/studio/event-series/new')
        self.assertContains(response, 'data-testid="studio-datetime-picker"')

    @override_settings(DEBUG=True)
    def test_event_series_detail_uses_shared_partial(self):
        series = EventSeries.objects.create(
            name='Series SP',
            slug='series-sp-665',
            cadence='weekly',
            cadence_weeks=1,
            day_of_week=1,
            start_time=datetime(2000, 1, 1, 14, 30).time(),
            timezone='America/New_York',
        )
        self.client.login(email='ny-admin-665@test.com', password='pw')
        response = self.client.get(
            reverse(
                'studio_event_series_detail',
                kwargs={'series_id': series.pk},
            ),
        )
        self.assertContains(response, 'data-testid="studio-datetime-picker"')
