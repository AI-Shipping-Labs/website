"""Tests for the Studio event registrations panel and CSV export (issue #701).

Covers:
- ``event_edit`` adds ``registrations`` and ``registration_count`` to context.
- ``event_create`` does NOT add a roster (no pk, no rows to list).
- The panel renders a count chip, one row per registration, and is gated
  on ``form_action == 'edit'``.
- Empty state copy renders when ``registration_count == 0`` and the
  table is omitted (the Download CSV link still renders).
- Rows arrive in ``-registered_at`` order.
- The CSV endpoint returns ``200 text/csv`` with the locked header
  ``email,name,registered_at,tier`` and one data row per registration.
- The CSV filename includes the event slug and a UTC timestamp.
- Anonymous and non-staff users are rejected by ``@staff_required``.
- The filter input ships with the testid Playwright targets.
"""

import csv
import io
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventRegistration
from tests.fixtures import StaffUserMixin, TierSetupMixin

User = get_user_model()


def _parse_csv(response):
    """Return ``response`` body parsed as a list of dicts."""
    return list(csv.DictReader(io.StringIO(response.content.decode())))


class EventEditRegistrationsContextTest(TierSetupMixin, StaffUserMixin, TestCase):
    """``event_edit`` exposes the roster to the template."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Roster Event',
            slug='roster-event',
            start_datetime=timezone.now() + timedelta(days=5),
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Anders',
            tier=cls.main_tier,
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
            tier=cls.basic_tier,
        )
        cls.carol = User.objects.create_user(
            email='carol@test.com', password='pw',
        )
        # Three registrations, controlled timestamps so we can assert
        # the "-registered_at" ordering reliably.
        now = timezone.now()
        cls.reg_oldest = EventRegistration.objects.create(
            event=cls.event, user=cls.carol,
        )
        EventRegistration.objects.filter(pk=cls.reg_oldest.pk).update(
            registered_at=now - timedelta(days=2),
        )
        cls.reg_mid = EventRegistration.objects.create(
            event=cls.event, user=cls.bob,
        )
        EventRegistration.objects.filter(pk=cls.reg_mid.pk).update(
            registered_at=now - timedelta(days=1),
        )
        cls.reg_newest = EventRegistration.objects.create(
            event=cls.event, user=cls.alice,
        )
        EventRegistration.objects.filter(pk=cls.reg_newest.pk).update(
            registered_at=now,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_context_has_registration_count(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.context['registration_count'], 3)

    def test_context_registrations_ordered_newest_first(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        emails = [r.user.email for r in response.context['registrations']]
        self.assertEqual(emails, ['alice@test.com', 'bob@test.com', 'carol@test.com'])

    def test_panel_renders_count_chip(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="event-registrations-panel"')
        self.assertContains(response, 'data-testid="registrations-count-chip"')
        self.assertContains(response, '3 registered')

    def test_panel_renders_one_desktop_row_per_registration(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        # The responsive panel (issue #1144) renders BOTH a desktop table
        # row and a mobile card per attendee, each carrying data-email.
        # Assert on the desktop-row testid to count table rows only.
        self.assertContains(
            response, 'data-testid="registration-row"', count=3,
        )

    def test_panel_renders_one_mobile_card_per_registration(self):
        """Issue #1144: below md the roster renders one stacked card per
        attendee so full names/emails wrap instead of clipping."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(
            response, 'data-testid="registration-card"', count=3,
        )
        # Each attendee appears in exactly two places (row + card).
        html = response.content.decode()
        for email in ('alice@test.com', 'bob@test.com', 'carol@test.com'):
            self.assertEqual(html.count(f'data-email="{email}"'), 2)

    def test_rows_show_email_name_and_tier_labels(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        html = response.content.decode()
        # Alice has full name; her row should render the full name.
        self.assertIn('Alice Anders', html)
        # Bob and Carol have no full name set; their rows must fall
        # back to the email address.
        self.assertIn('bob@test.com', html)
        self.assertIn('carol@test.com', html)
        # Tier labels resolve via FK.
        self.assertIn('Main', html)
        self.assertIn('Basic', html)
        # Carol has no tier override; the default-applied free tier
        # renders the "Free" label.
        self.assertIn('Free', html)

    def test_filter_input_has_expected_testid(self):
        """The filter input ships with a testid so Playwright tests
        (and the inline client-side script) can target it."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="registrations-filter"')
        self.assertContains(response, 'id="registrations-filter"')

    def test_download_csv_link_points_to_csv_endpoint(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="registrations-download-csv"')
        self.assertContains(
            response,
            f'/studio/events/{self.event.pk}/registrations.csv',
        )


class EventEditEmptyRosterTest(TierSetupMixin, StaffUserMixin, TestCase):
    """The panel renders an empty state when there are no registrations."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Lonely Event',
            slug='lonely-event',
            start_datetime=timezone.now() + timedelta(days=3),
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_empty_count_in_context(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.context['registration_count'], 0)

    def test_empty_state_copy_renders(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="registrations-empty"')
        self.assertContains(response, 'No registrations yet.')

    def test_no_table_rendered_when_empty(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertNotContains(response, 'data-testid="registrations-table"')
        # The filter input is part of the populated branch only.
        self.assertNotContains(response, 'data-testid="registrations-filter"')

    def test_download_csv_link_still_present_when_empty(self):
        """A roster with zero rows still exposes the CSV link (the file
        downloads with just the header row)."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="registrations-download-csv"')


class EventCreateRosterPanelHiddenTest(StaffUserMixin, TestCase):
    """The roster panel is only rendered on the edit form."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_create_form_has_no_registrations_context(self):
        response = self.client.get('/studio/events/new')
        # ``event_create`` must not add ``registrations`` to the context.
        # A KeyError on missing keys is a regression — assert by
        # explicit membership.
        self.assertNotIn('registrations', response.context)
        self.assertNotIn('registration_count', response.context)

    def test_create_form_does_not_render_registrations_panel(self):
        response = self.client.get('/studio/events/new')
        self.assertNotContains(response, 'data-testid="event-registrations-panel"')


class EventRegistrationsCsvTest(TierSetupMixin, StaffUserMixin, TestCase):
    """CSV export for the event roster."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='CSV Roster Event',
            slug='csv-roster-event',
            start_datetime=timezone.now() + timedelta(days=7),
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Anders',
            tier=cls.main_tier,
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
            tier=cls.premium_tier,
        )
        cls.reg_alice = EventRegistration.objects.create(
            event=cls.event, user=cls.alice,
        )
        cls.reg_bob = EventRegistration.objects.create(
            event=cls.event, user=cls.bob,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.url = f'/studio/events/{self.event.pk}/registrations.csv'

    def test_csv_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_csv_content_type_is_text_csv(self):
        response = self.client.get(self.url)
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_csv_filename_includes_slug_and_utc_timestamp(self):
        response = self.client.get(self.url)
        disposition = response['Content-Disposition']
        match = re.search(
            r'filename="event-csv-roster-event-registrations-(\d{8}-\d{6})\.csv"',
            disposition,
        )
        self.assertIsNotNone(
            match,
            f'Expected slug+timestamp filename, got {disposition!r}',
        )

    def test_csv_header_row_matches_locked_columns(self):
        response = self.client.get(self.url)
        reader = csv.reader(io.StringIO(response.content.decode()))
        header = next(reader)
        # Issue #936: ``joined_at`` is appended after the original four
        # columns, which keep their order.
        self.assertEqual(
            header, ['email', 'name', 'registered_at', 'tier', 'joined_at'],
        )

    def test_csv_has_one_data_row_per_registration(self):
        response = self.client.get(self.url)
        rows = _parse_csv(response)
        self.assertEqual(len(rows), 2)
        emails = {row['email'] for row in rows}
        self.assertEqual(emails, {'alice@test.com', 'bob@test.com'})

    def test_csv_row_includes_name_and_tier_label(self):
        response = self.client.get(self.url)
        rows = {row['email']: row for row in _parse_csv(response)}
        # Alice has a first/last name -> the joined full name lands in
        # the ``name`` column. Tier comes from the FK.
        self.assertEqual(rows['alice@test.com']['name'], 'Alice Anders')
        self.assertEqual(rows['alice@test.com']['tier'], 'Main')
        # Bob has no first/last name -> empty string, not the email.
        self.assertEqual(rows['bob@test.com']['name'], '')
        self.assertEqual(rows['bob@test.com']['tier'], 'Premium')

    def test_csv_user_without_tier_defaults_to_free(self):
        """``user.tier`` is FK-defaulted to ``free`` on create, but if a
        future code path nulls it the export still renders ``Free`` so
        the column never goes blank."""
        carol = User.objects.create_user(
            email='carol@test.com', password='pw',
        )
        # Explicitly null the FK to exercise the fallback branch.
        User.objects.filter(pk=carol.pk).update(tier=None)
        EventRegistration.objects.create(event=self.event, user=carol)

        response = self.client.get(self.url)
        rows = {row['email']: row for row in _parse_csv(response)}
        self.assertEqual(rows['carol@test.com']['tier'], 'Free')

    def test_csv_registered_at_is_iso_8601(self):
        response = self.client.get(self.url)
        rows = _parse_csv(response)
        for row in rows:
            value = row['registered_at']
            # ISO 8601 with T separator (datetime.isoformat() output).
            self.assertRegex(
                value,
                r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}',
                f'Expected ISO 8601, got {value!r}',
            )

    def test_csv_empty_roster_returns_only_header(self):
        empty_event = Event.objects.create(
            title='Empty Event', slug='empty-event',
            start_datetime=timezone.now() + timedelta(days=4),
        )
        response = self.client.get(
            f'/studio/events/{empty_event.pk}/registrations.csv',
        )
        self.assertEqual(response.status_code, 200)
        rows = _parse_csv(response)
        self.assertEqual(rows, [])
        # The header row is still present (DictReader consumes it).
        header_line = response.content.decode().splitlines()[0]
        self.assertEqual(
            header_line, 'email,name,registered_at,tier,joined_at',
        )

    def test_csv_404_for_unknown_event(self):
        response = self.client.get('/studio/events/999999/registrations.csv')
        self.assertEqual(response.status_code, 404)

    def test_csv_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_csv_non_staff_forbidden(self):
        self.client.logout()
        member = User.objects.create_user(
            email='member-701@test.com', password='pw',
        )
        member.email_verified = True
        member.save()
        self.client.login(email='member-701@test.com', password='pw')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)


class EventEditJoinedAttendanceTest(TierSetupMixin, StaffUserMixin, TestCase):
    """Issue #936: the Studio roster surfaces per-registration attendance.

    Covers the ``joined_count`` context value, the ``joined-count`` stat
    rendering as ``joined / registrations``, and the per-row Joined badge
    vs. ``Not joined`` indicator.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Attendance Event',
            slug='attendance-event',
            start_datetime=timezone.now() + timedelta(days=5),
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Anders',
        )
        cls.bob = User.objects.create_user(email='bob@test.com', password='pw')
        cls.carol = User.objects.create_user(email='carol@test.com', password='pw')
        cls.reg_alice = EventRegistration.objects.create(
            event=cls.event, user=cls.alice,
        )
        # Alice joined; Bob and Carol registered but never joined.
        joined_ts = timezone.now() - timedelta(hours=1)
        EventRegistration.objects.filter(pk=cls.reg_alice.pk).update(
            joined_at=joined_ts,
        )
        EventRegistration.objects.create(event=cls.event, user=cls.bob)
        EventRegistration.objects.create(event=cls.event, user=cls.carol)

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.url = f'/studio/events/{self.event.pk}/edit'

    def test_joined_count_in_context(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['joined_count'], 1)
        self.assertEqual(response.context['registration_count'], 3)

    def test_joined_stat_renders_ratio(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="joined-count"')
        # The stat reads "1 / 3".
        html = response.content.decode()
        stat = re.search(
            r'data-testid="joined-count"[^>]*>([^<]*)<', html,
        )
        self.assertIsNotNone(stat)
        self.assertEqual(stat.group(1).strip(), '1 / 3')

    def test_joined_row_shows_badge_and_timestamp(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="registration-joined-badge"')
        # Exactly one Joined badge (only Alice joined).
        self.assertContains(
            response, 'data-testid="registration-joined-badge"', count=1,
        )

    def test_not_joined_rows_show_muted_indicator(self):
        response = self.client.get(self.url)
        # Bob and Carol did not join -> two "Not joined" indicators.
        self.assertContains(
            response, 'data-testid="registration-not-joined"', count=2,
        )


class EventEditAllZeroJoinedTest(TierSetupMixin, StaffUserMixin, TestCase):
    """Issue #936: an event nobody has joined shows a clean ``0 / N`` state."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Nobody Joined Event',
            slug='nobody-joined-event',
            start_datetime=timezone.now() + timedelta(days=2),
        )
        cls.u1 = User.objects.create_user(email='u1@test.com', password='pw')
        cls.u2 = User.objects.create_user(email='u2@test.com', password='pw')
        EventRegistration.objects.create(event=cls.event, user=cls.u1)
        EventRegistration.objects.create(event=cls.event, user=cls.u2)

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.url = f'/studio/events/{self.event.pk}/edit'

    def test_joined_count_zero(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['joined_count'], 0)

    def test_stat_reads_zero_over_total(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        stat = re.search(r'data-testid="joined-count"[^>]*>([^<]*)<', html)
        self.assertIsNotNone(stat)
        self.assertEqual(stat.group(1).strip(), '0 / 2')

    def test_all_rows_not_joined_no_badge(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'data-testid="registration-not-joined"', count=2,
        )
        self.assertNotContains(
            response, 'data-testid="registration-joined-badge"',
        )


class EventRegistrationsCsvJoinedColumnTest(
    TierSetupMixin, StaffUserMixin, TestCase,
):
    """Issue #936: the CSV's trailing ``joined_at`` column."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='CSV Joined Event',
            slug='csv-joined-event',
            start_datetime=timezone.now() + timedelta(days=7),
        )
        cls.joined_user = User.objects.create_user(
            email='joined@test.com', password='pw',
        )
        cls.not_joined_user = User.objects.create_user(
            email='notjoined@test.com', password='pw',
        )
        cls.reg_joined = EventRegistration.objects.create(
            event=cls.event, user=cls.joined_user,
        )
        cls.joined_ts = timezone.now() - timedelta(hours=2)
        EventRegistration.objects.filter(pk=cls.reg_joined.pk).update(
            joined_at=cls.joined_ts,
        )
        EventRegistration.objects.create(
            event=cls.event, user=cls.not_joined_user,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.url = f'/studio/events/{self.event.pk}/registrations.csv'

    def test_joined_row_has_iso_utc_timestamp(self):
        response = self.client.get(self.url)
        rows = {row['email']: row for row in _parse_csv(response)}
        value = rows['joined@test.com']['joined_at']
        # ISO 8601 with a UTC offset (+00:00).
        self.assertRegex(
            value,
            r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*\+00:00$',
            f'Expected ISO 8601 UTC, got {value!r}',
        )

    def test_not_joined_row_has_empty_cell(self):
        response = self.client.get(self.url)
        rows = {row['email']: row for row in _parse_csv(response)}
        self.assertEqual(rows['notjoined@test.com']['joined_at'], '')
