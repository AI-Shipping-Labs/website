"""Browser-timezone default + explicit resolved line (issue #855).

Extends the #665 picker behavior:

- When the admin has no saved ``preferred_timezone``, the picker no longer
  forces UTC: the shared partial emits ``data-tz-autodetect="true"`` so JS
  pre-selects the browser zone. (The actual JS pre-selection is covered by a
  Playwright scenario; here we assert the server opt-in marker.)
- A saved preference (or an existing event/series timezone) suppresses the
  marker so it is never overridden.
- A "Set your default timezone" link appears next to the timezone field on
  editable forms and points to the account page.
- The event edit form's "Resolved" line states the timezone of the displayed
  values (UTC) and also shows the equivalent in the event's selected zone.
- UTC stays selectable.
"""

import re
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from events.models import Event, EventSeries

User = get_user_model()


def _tz_select_attrs(html, instance_id):
    """Return the opening ``<select>`` tag text for ``dtp-<instance_id>-tz``."""
    match = re.search(
        rf'(<select[^>]*id="dtp-{instance_id}-tz"[^>]*>)',
        html,
        re.DOTALL,
    )
    assert match, f'TZ <select> for instance {instance_id!r} not found'
    return match.group(1)


class _AdminMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.berlin_admin = User.objects.create_user(
            email='berlin-admin-855@test.com',
            password='pw',
            is_staff=True,
        )
        cls.berlin_admin.preferred_timezone = 'Europe/Berlin'
        cls.berlin_admin.save(update_fields=['preferred_timezone'])

        cls.unset_admin = User.objects.create_user(
            email='no-tz-admin-855@test.com',
            password='pw',
            is_staff=True,
        )
        # preferred_timezone defaults to '' on the User model.


class AutodetectMarkerTest(_AdminMixin, TestCase):
    """``data-tz-autodetect`` opt-in depends on the admin + existing value."""

    def test_new_event_marks_autodetect_for_admin_without_preference(self):
        self.client.login(email='no-tz-admin-855@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)
        select = _tz_select_attrs(response.content.decode(), 'event')
        self.assertIn('data-tz-autodetect="true"', select)

    def test_new_event_no_autodetect_when_admin_has_preference(self):
        self.client.login(email='berlin-admin-855@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        select = _tz_select_attrs(response.content.decode(), 'event')
        self.assertNotIn('data-tz-autodetect', select)

    def test_new_series_marks_autodetect_for_admin_without_preference(self):
        self.client.login(email='no-tz-admin-855@test.com', password='pw')
        response = self.client.get('/studio/event-series/new')
        self.assertEqual(response.status_code, 200)
        select = _tz_select_attrs(response.content.decode(), 'series')
        self.assertIn('data-tz-autodetect="true"', select)

    def test_new_series_no_autodetect_when_admin_has_preference(self):
        self.client.login(email='berlin-admin-855@test.com', password='pw')
        response = self.client.get('/studio/event-series/new')
        select = _tz_select_attrs(response.content.decode(), 'series')
        self.assertNotIn('data-tz-autodetect', select)

    def test_existing_event_edit_never_autodetects(self):
        event = Event.objects.create(
            title='Existing Event', slug='existing-event-855',
            start_datetime=datetime(2027, 6, 15, 14, 0, tzinfo=UTC),
            end_datetime=datetime(2027, 6, 15, 15, 0, tzinfo=UTC),
            timezone='America/New_York',
            origin='studio',
        )
        self.client.login(email='no-tz-admin-855@test.com', password='pw')
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        select = _tz_select_attrs(response.content.decode(), 'event')
        self.assertNotIn('data-tz-autodetect', select)

    def test_existing_series_add_occurrence_never_autodetects(self):
        series = EventSeries.objects.create(
            name='Series With TZ', slug='series-with-tz-855',
            cadence='weekly', day_of_week=1,
            start_time=datetime(2000, 1, 1, 14, 30).time(),
            timezone='America/New_York',
        )
        self.client.login(email='no-tz-admin-855@test.com', password='pw')
        response = self.client.get(
            reverse(
                'studio_event_series_detail',
                kwargs={'series_id': series.pk},
            ),
        )
        select = _tz_select_attrs(response.content.decode(), 'add')
        self.assertNotIn('data-tz-autodetect', select)


class SettingsLinkTest(_AdminMixin, TestCase):
    """The "Set your default timezone" link shows on editable forms only."""

    def test_link_present_on_new_event_form(self):
        self.client.login(email='no-tz-admin-855@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        self.assertContains(
            response, 'data-testid="dtp-event-tz-settings-link"',
        )
        self.assertContains(response, 'href="/account/#display-preferences-section"')
        self.assertContains(response, 'Set your default timezone')

    def test_link_present_on_new_series_form(self):
        self.client.login(email='no-tz-admin-855@test.com', password='pw')
        response = self.client.get('/studio/event-series/new')
        self.assertContains(
            response, 'data-testid="dtp-series-tz-settings-link"',
        )

    def test_link_absent_on_disabled_synced_event_picker(self):
        """Synced events render a disabled picker; no settings link there."""
        event = Event.objects.create(
            title='Synced Event', slug='synced-event-855',
            start_datetime=datetime(2027, 6, 15, 14, 0, tzinfo=UTC),
            end_datetime=datetime(2027, 6, 15, 15, 0, tzinfo=UTC),
            timezone='UTC',
            origin='github', source_repo='AI-Shipping-Labs/content',
        )
        self.client.login(email='berlin-admin-855@test.com', password='pw')
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertNotContains(
            response, 'data-testid="dtp-event-tz-settings-link"',
        )


class ResolvedLineTest(_AdminMixin, TestCase):
    """The edit form's resolved line is explicit about its timezone."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # 14:00-15:00 UTC == 16:00-17:00 Europe/Berlin (summer, +02:00).
        cls.event = Event.objects.create(
            title='Resolved Event', slug='resolved-event-855',
            start_datetime=datetime(2026, 6, 15, 14, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 15, 15, 0, tzinfo=UTC),
            timezone='Europe/Berlin',
            origin='studio',
        )

    def test_resolved_line_labels_utc(self):
        self.client.login(email='berlin-admin-855@test.com', password='pw')
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        html = response.content.decode()
        self.assertContains(response, 'data-testid="event-resolved-utc"')
        self.assertIn('Resolved (UTC):', html)
        # The UTC values are 14:00 / 15:00, not the local 16:00/17:00.
        utc_block = re.search(
            r'data-testid="event-resolved-utc".*?</div>', html, re.DOTALL,
        ).group(0)
        self.assertIn('15/06/2026 14:00', utc_block)
        self.assertIn('15/06/2026 15:00', utc_block)

    def test_resolved_line_shows_local_zone_equivalent(self):
        self.client.login(email='berlin-admin-855@test.com', password='pw')
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        html = response.content.decode()
        self.assertContains(response, 'data-testid="event-resolved-local"')
        local_block = re.search(
            r'data-testid="event-resolved-local".*?</div>', html, re.DOTALL,
        ).group(0)
        self.assertIn('Europe/Berlin', local_block)
        self.assertIn('15/06/2026 16:00', local_block)
        self.assertIn('15/06/2026 17:00', local_block)

    def test_utc_event_omits_redundant_local_line(self):
        """A UTC event needs no separate local equivalent line."""
        utc_event = Event.objects.create(
            title='UTC Event', slug='utc-event-855',
            start_datetime=datetime(2026, 6, 15, 14, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 15, 15, 0, tzinfo=UTC),
            timezone='UTC',
            origin='studio',
        )
        self.client.login(email='berlin-admin-855@test.com', password='pw')
        response = self.client.get(f'/studio/events/{utc_event.pk}/edit')
        self.assertContains(response, 'Resolved (UTC):')
        self.assertNotContains(
            response, 'data-testid="event-resolved-local"',
        )


class UtcStaysSelectableTest(_AdminMixin, TestCase):
    """UTC remains an option in the picker on every form."""

    def test_utc_option_present_on_new_event(self):
        self.client.login(email='no-tz-admin-855@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        html = response.content.decode()
        select = re.search(
            r'<select[^>]*id="dtp-event-tz"[^>]*>(.*?)</select>',
            html, re.DOTALL,
        ).group(1)
        self.assertRegex(select, r'<option[^>]*value="UTC"')
