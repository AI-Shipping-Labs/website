"""Tests for the Studio event-series create/detail/edit/delete views.

Issue #564 (renamed from event-group in #575).
"""

import re
import zoneinfo
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event, EventSeries

User = get_user_model()


class StaffMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pass')


class StudioEventSeriesAccessTest(StaffMixin, TestCase):
    """Access control on the studio event-series endpoints."""

    def test_anonymous_redirected_from_new(self):
        client = Client()
        response = client.get('/studio/event-series/new')
        self.assertEqual(response.status_code, 302)

    def test_non_staff_forbidden(self):
        User.objects.create_user(email='plain@test.com', password='pass')
        client = Client()
        client.login(email='plain@test.com', password='pass')
        response = client.get('/studio/event-series/new')
        self.assertEqual(response.status_code, 403)

    def test_staff_get_new_returns_200(self):
        response = self.client.get('/studio/event-series/new')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sticky-save-action')

    def test_required_level_is_named_dropdown(self):
        """The Required Level field is a named-tier dropdown, not a bare number box."""
        response = self.client.get('/studio/event-series/new')
        self.assertContains(response, '<option value="0"')
        self.assertContains(response, 'Free (0)')
        self.assertContains(response, 'Basic (10)')
        self.assertContains(response, 'Main (20)')
        self.assertContains(response, 'Premium (30)')
        # No bare numeric input for the access level remains.
        self.assertNotContains(response, 'type="number" name="required_level"')

    def test_required_level_default_is_free(self):
        """Free (level 0) is the selected default on a fresh series form."""
        response = self.client.get('/studio/event-series/new')
        self.assertContains(
            response, '<option value="0" selected>Free (0)</option>', html=False,
        )


class StudioEventSeriesCreateTest(StaffMixin, TestCase):
    """``POST /studio/event-series/new`` creates a series + N events."""

    def _post_valid(self, **overrides):
        # Use a future date so we don't bump into past-date guards.
        start = (date.today() + timedelta(days=14))
        payload = {
            'name': 'Spring Workshop Series',
            'slug': '',
            'description': '',
            'start_date': start.strftime('%d/%m/%Y'),
            'start_time': '18:00',
            'duration_hours': '1.5',
            'occurrences': '6',
            'timezone': 'Europe/Berlin',
            'required_level': '0',
            'kind': 'standard',
            'platform': 'zoom',
        }
        payload.update(overrides)
        return self.client.post('/studio/event-series/new', payload)

    def test_creates_one_series_and_six_events(self):
        response = self._post_valid()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventSeries.objects.count(), 1)
        series = EventSeries.objects.get()
        self.assertEqual(series.events.count(), 6)
        self.assertEqual(series.slug, 'spring-workshop-series')

    def test_events_are_studio_origin_and_linked_to_series(self):
        self._post_valid()
        series = EventSeries.objects.get()
        events = list(series.events.all().order_by('series_position'))
        for i, event in enumerate(events, start=1):
            self.assertEqual(event.origin, 'studio')
            self.assertIn(event.source_repo, (None, ''))
            self.assertEqual(event.event_series_id, series.pk)
            self.assertEqual(event.series_position, i)
            self.assertEqual(event.status, 'draft')

    def test_events_spaced_seven_days_apart(self):
        self._post_valid()
        series = EventSeries.objects.get()
        events = list(series.events.all().order_by('series_position'))
        for i in range(1, len(events)):
            delta = events[i].start_datetime - events[i - 1].start_datetime
            self.assertEqual(delta, timedelta(days=7))

    def test_end_datetime_equals_start_plus_duration(self):
        self._post_valid(duration_hours='1.5')
        series = EventSeries.objects.get()
        for event in series.events.all():
            self.assertEqual(
                event.end_datetime - event.start_datetime,
                timedelta(hours=1.5),
            )

    def test_occurrences_zero_re_renders_form_and_creates_nothing(self):
        response = self._post_valid(occurrences='0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EventSeries.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        self.assertContains(response, 'error-occurrences')

    def test_occurrences_too_high_re_renders_form_and_creates_nothing(self):
        response = self._post_valid(occurrences='27')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EventSeries.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        self.assertContains(response, 'error-occurrences')

    def test_main_level_applied_to_all_generated_events(self):
        """Selecting Main (20) gates every generated event at required_level 20."""
        self._post_valid(required_level='20')
        series = EventSeries.objects.get()
        self.assertEqual(series.events.count(), 6)
        for event in series.events.all():
            self.assertEqual(event.required_level, 20)

    def test_premium_level_applied_to_all_generated_events(self):
        """Selecting Premium (30) gates every generated event at required_level 30."""
        self._post_valid(required_level='30')
        series = EventSeries.objects.get()
        for event in series.events.all():
            self.assertEqual(event.required_level, 30)

    def test_default_level_is_free_on_generated_events(self):
        """Omitting required_level leaves generated events open (level 0)."""
        self._post_valid(required_level='0')
        series = EventSeries.objects.get()
        for event in series.events.all():
            self.assertEqual(event.required_level, 0)

    def test_slug_collision_appends_suffix(self):
        Event.objects.create(
            title='Pre-existing', slug='spring-workshop-series-session-1',
            start_datetime=timezone.now(), origin='studio',
        )
        self._post_valid()
        series = EventSeries.objects.get()
        first = series.events.get(series_position=1)
        # The auto-derived ``spring-workshop-series-session-1`` is taken,
        # so the generator picks ``...-1-2`` for this session.
        self.assertNotEqual(first.slug, 'spring-workshop-series-session-1')
        self.assertTrue(first.slug.startswith('spring-workshop-series-session-1'))


class StudioEventSeriesDetailTest(StaffMixin, TestCase):
    """Detail page shows member events with edit links."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Detail Series', start_time=time(18, 0),
        )
        for i in range(1, 4):
            Event.objects.create(
                title=f'Session {i}',
                slug=f'detail-session-{i}',
                start_datetime=timezone.now() + timedelta(days=7 * i),
                event_series=cls.series, series_position=i,
                origin='studio',
            )

    def test_detail_renders_member_events(self):
        response = self.client.get(f'/studio/event-series/{self.series.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Session 1')
        self.assertContains(response, 'Session 2')
        self.assertContains(response, 'Session 3')

    def test_template_comments_do_not_leak_into_rendered_page(self):
        """Multi-line {# #} comments leak every line but the last as body
        text. They must be {% comment %} blocks instead — guard against the
        sentinels that were rendering on the operator's screen."""
        response = self.client.get(f'/studio/event-series/{self.series.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Issue #859')
        self.assertNotContains(response, 'Issue #858')
        self.assertNotContains(response, 'Issue #854')
        self.assertNotContains(response, 'Issue #896')

    def test_edit_links_point_to_event_edit(self):
        response = self.client.get(f'/studio/event-series/{self.series.pk}/')
        event = self.series.events.get(series_position=1)
        self.assertContains(
            response, f'/studio/events/{event.pk}/edit',
        )

    def test_metadata_post_updates_series(self):
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': 'Renamed Series',
                'slug': self.series.slug,
                'description': 'Now with a description.',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.series.refresh_from_db()
        self.assertEqual(self.series.name, 'Renamed Series')
        self.assertIn('description', self.series.description)

    @staticmethod
    def _member_rows_text(html):
        rows = re.findall(
            r'<tr data-testid="event-series-member-row".*?</tr>',
            html,
            flags=re.DOTALL,
        )
        return [
            ' '.join(re.sub(r'<[^>]+>', ' ', row).split())
            for row in rows
        ]

    def test_scrambled_positions_render_in_date_order_without_number_column(self):
        scrambled = EventSeries.objects.create(
            name='Scrambled Detail Series',
            slug='scrambled-detail-series',
            start_time=time(18, 0),
            timezone='UTC',
        )
        plan = [
            ('Alpha kickoff', 'alpha-kickoff', 1, datetime(2026, 6, 10, 18, 0)),
            ('Bravo lab', 'bravo-lab', 7, datetime(2026, 6, 11, 18, 0)),
            ('Charlie clinic', 'charlie-clinic', 3, datetime(2026, 6, 12, 18, 0)),
            ('Delta review', 'delta-review', 4, datetime(2026, 6, 13, 18, 0)),
            ('Echo demo', 'echo-demo', 8, datetime(2026, 6, 14, 18, 0)),
            ('Foxtrot close', 'foxtrot-close', 9, datetime(2026, 6, 15, 18, 0)),
        ]
        for title, slug, position, starts_at in plan:
            Event.objects.create(
                title=title,
                slug=slug,
                start_datetime=timezone.make_aware(
                    starts_at,
                    zoneinfo.ZoneInfo('UTC'),
                ),
                event_series=scrambled,
                series_position=position,
                status='draft',
                origin='studio',
                timezone='UTC',
            )

        response = self.client.get(f'/studio/event-series/{scrambled.pk}/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('data-testid="series-position"', html)
        self.assertNotIn('> # <', ' '.join(html.split()))
        self.assertNotRegex(html, r'<th[^>]*>\s*#\s*</th>')

        headers = re.findall(r'<th[^>]*>(.*?)</th>', html, flags=re.DOTALL)
        header_text = [' '.join(re.sub(r'<[^>]+>', ' ', h).split()) for h in headers]
        self.assertEqual(
            header_text[:7],
            ['Title', 'Visibility', 'Access', 'Zoom', 'Start', 'Registrations', 'Actions'],
        )

        row_text = self._member_rows_text(html)
        self.assertEqual(len(row_text), 6)
        self.assertEqual(
            [row.split(' Not published ')[0] for row in row_text],
            [
                'Alpha kickoff',
                'Bravo lab',
                'Charlie clinic',
                'Delta review',
                'Echo demo',
                'Foxtrot close',
            ],
        )
        for text, (_title, _slug, position, _starts_at) in zip(row_text, plan):
            self.assertNotRegex(text, rf'^{position}\b')
            self.assertNotRegex(text, rf'\b{position}\s+{re.escape(_title)}\b')
            self.assertIn('Not published', text)
            self.assertIn('Free', text)
            self.assertIn('Publish', text)
            self.assertIn('Edit', text)

    def test_empty_series_uses_new_table_colspan(self):
        empty = EventSeries.objects.create(
            name='Empty Detail Series',
            slug='empty-detail-series',
            start_time=time(18, 0),
        )
        response = self.client.get(f'/studio/event-series/{empty.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No member events.')
        self.assertContains(response, 'colspan="7"')
        self.assertNotContains(response, 'colspan="8"')
        self.assertNotIn('data-testid="series-position"', response.content.decode())


class StudioEventSeriesAddOccurrenceTest(StaffMixin, TestCase):
    """``POST .../add-occurrence`` appends one more event."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Add Series', slug='add-series', start_time=time(18, 0),
        )
        for i in range(1, 4):
            Event.objects.create(
                title=f'Add Session {i}',
                slug=f'add-series-session-{i}',
                start_datetime=timezone.now() + timedelta(days=7 * i),
                event_series=cls.series, series_position=i, origin='studio',
            )

    def test_add_occurrence_creates_one_event_and_advances_position(self):
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {'start_date': start, 'duration_hours': '1'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.series.events.count(), 4)
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.series_position, 4)
        self.assertEqual(new_event.origin, 'studio')
        self.assertEqual(new_event.event_series_id, self.series.pk)

    def test_added_occurrence_position_is_stored_but_not_displayed(self):
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {'start_date': start, 'duration_hours': '1', 'timezone': 'UTC'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.series_position, 4)
        html = response.content.decode()
        self.assertNotIn('data-testid="series-position"', html)
        row_text = StudioEventSeriesDetailTest._member_rows_text(html)
        self.assertTrue(any(new_event.title in row for row in row_text))
        for row in row_text:
            if new_event.title in row:
                self.assertNotRegex(row, r'^4\b')
                break

    def test_add_occurrence_blank_time_defaults_to_series_start_time(self):
        # Series start_time is 18:00 in UTC (no series tz -> 'UTC').
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {'start_date': start, 'duration_hours': '1', 'timezone': 'UTC'},
        )
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.start_datetime.hour, 18)
        self.assertEqual(new_event.start_datetime.minute, 0)

    def test_add_occurrence_custom_time_stored_as_utc(self):
        # 20:00 in UTC must persist as 20:00 UTC, not the series 18:00.
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {
                'start_date': start,
                'start_time': '20:00',
                'duration_hours': '1',
                'timezone': 'UTC',
            },
        )
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.start_datetime.hour, 20)
        self.assertEqual(new_event.start_datetime.minute, 0)

    def test_add_occurrence_custom_time_localized_in_chosen_tz(self):
        # 20:00 Europe/Berlin (UTC+2 in summer) localizes to 18:00 UTC.
        start = '15/07/2026'  # mid-summer, CEST = UTC+2
        self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {
                'start_date': start,
                'start_time': '20:00',
                'duration_hours': '1',
                'timezone': 'Europe/Berlin',
            },
        )
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.start_datetime.hour, 18)

    def test_add_occurrence_custom_title_drives_title_and_slug(self):
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {
                'start_date': start,
                'title': 'Special Guest AMA',
                'duration_hours': '1',
                'timezone': 'UTC',
            },
        )
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.title, 'Special Guest AMA')
        self.assertEqual(new_event.slug, 'special-guest-ama')

    def test_add_occurrence_blank_title_falls_back_to_default(self):
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {'start_date': start, 'duration_hours': '1', 'timezone': 'UTC'},
        )
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.title, f'{self.series.name} — Session 4')
        self.assertEqual(new_event.slug, 'add-series-session-4')

    def test_add_occurrence_invalid_time_creates_no_row(self):
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {'start_date': start, 'start_time': 'not-a-time'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Start time must be', status_code=400)
        # No partial row written.
        self.assertEqual(self.series.events.count(), 3)


class StudioEventSeriesPropagateTest(StaffMixin, TestCase):
    """Issue #854 Part B: opt-in parent->child slug + description propagation."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Office Hours', slug='office-hours', start_time=time(18, 0),
            description='Original series description.',
        )
        for i in range(1, 4):
            Event.objects.create(
                title=f'Office Hours — Session {i}',
                slug=f'office-hours-session-{i}',
                description='',
                start_datetime=timezone.now() + timedelta(days=7 * i),
                event_series=cls.series, series_position=i, origin='studio',
            )

    def test_unchecked_does_not_touch_children(self):
        child = self.series.events.get(series_position=1)
        child.description = 'Custom note'
        child.save()
        original_slug = child.slug

        self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': 'Office Hours',
                'slug': 'office-hours',
                'description': 'Updated series description.',
                # propagate intentionally absent (unchecked)
            },
        )
        self.series.refresh_from_db()
        self.assertEqual(self.series.description, 'Updated series description.')
        child.refresh_from_db()
        self.assertEqual(child.description, 'Custom note')
        self.assertEqual(child.slug, original_slug)

    def test_checked_propagates_description_and_renders_html(self):
        self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': 'Office Hours',
                'slug': 'office-hours',
                'description': 'Bring your questions.',
                'propagate': 'on',
            },
        )
        for child in self.series.events.all():
            self.assertEqual(child.description, 'Bring your questions.')
            self.assertIn('Bring your questions.', child.description_html)

    def test_checked_regenerates_child_slugs_from_new_series_slug(self):
        self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': 'Office Hours',
                'slug': 'founder-office-hours',
                'description': 'Original series description.',
                'propagate': 'on',
            },
        )
        for i in range(1, 4):
            child = self.series.events.get(series_position=i)
            self.assertEqual(child.slug, f'founder-office-hours-session-{i}')

    def test_propagate_success_message_reports_count(self):
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': 'Office Hours',
                'slug': 'office-hours',
                'description': 'Bring your questions.',
                'propagate': 'on',
            },
            follow=True,
        )
        self.assertContains(response, 'Updated 3 events')

    def test_propagation_rolls_back_on_external_slug_collision(self):
        # An unrelated standalone event already owns the slug a propagated
        # child would take. Propagation must fail atomically.
        Event.objects.create(
            title='Unrelated', slug='founder-office-hours-session-1',
            start_datetime=timezone.now(), origin='studio',
        )
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': 'Office Hours',
                'slug': 'founder-office-hours',
                'description': 'Original series description.',
                'propagate': 'on',
            },
        )
        self.assertEqual(response.status_code, 400)
        # Series slug unchanged.
        self.series.refresh_from_db()
        self.assertEqual(self.series.slug, 'office-hours')
        # No child slug changed.
        for i in range(1, 4):
            child = self.series.events.get(series_position=i)
            self.assertEqual(child.slug, f'office-hours-session-{i}')


class StudioEventSeriesCadenceLabelTest(StaffMixin, TestCase):
    """Issue #947: the Studio series list Cadence column and the detail
    header render the honest ``schedule_label`` (#877), not the old
    first-occurrence weekly claim. A regular series keeps the weekly
    phrasing; an irregular series shows the session summary and never the
    literal ``Weekly on``. The detail header keeps its
    ``— N occurrence(s)`` suffix. A 0-occurrence (staff-preview) series,
    whose label is empty, renders neither ``Weekly on`` nor an orphaned
    separator.
    """

    @staticmethod
    def _occurrence(series, local_dt, position, tz='Europe/Berlin'):
        aware = local_dt.replace(tzinfo=zoneinfo.ZoneInfo(tz))
        return Event.objects.create(
            title=f'{series.name} Session {position}',
            slug=f'{series.slug}-session-{position}',
            start_datetime=aware,
            timezone=tz,
            status='upcoming',
            event_series=series,
            series_position=position,
            origin='studio',
        )

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.regular = EventSeries.objects.create(
            name='Regular Series', slug='regular-series',
            day_of_week=2,  # Wednesday
            start_time=time(18, 0), timezone='Europe/Berlin',
        )
        for i in range(3):
            cls._occurrence(
                cls.regular,
                datetime(2026, 6, 17, 18, 0) + timedelta(weeks=i),
                position=i + 1,
            )

        cls.irregular = EventSeries.objects.create(
            name='Irregular Series', slug='irregular-series',
            day_of_week=2, start_time=time(18, 0), timezone='Europe/Berlin',
        )
        for i, dt in enumerate([
            datetime(2026, 6, 15, 18, 0),  # Monday
            datetime(2026, 6, 24, 18, 0),
            datetime(2026, 6, 29, 18, 0),
        ]):
            cls._occurrence(cls.irregular, dt, position=i + 1)

        # No occurrences -> schedule_label == '' (staff-preview only).
        cls.empty = EventSeries.objects.create(
            name='Empty Series', slug='empty-series',
            day_of_week=2, start_time=time(18, 0), timezone='Europe/Berlin',
        )

    @staticmethod
    def _cell(html, data_label, label_value):
        """Return collapsed text of the ``data-label`` cell whose row matches
        ``label_value`` (used to isolate one series row in the list)."""
        # Find the row containing label_value, then its target cell.
        row_start = html.index(label_value)
        # Walk back to the row open tag and forward to its close.
        tr_open = html.rindex('<tr', 0, row_start)
        tr_close = html.index('</tr>', row_start)
        row = html[tr_open:tr_close]
        match = re.search(
            rf'data-label="{data_label}"[^>]*>(.*?)</td>',
            row, re.DOTALL,
        )
        assert match, f'{data_label} cell not found in row'
        return ' '.join(match.group(1).split())

    # --- Studio list Cadence column ---------------------------------------

    def test_list_regular_row_shows_weekly_label(self):
        response = self.client.get('/studio/event-series/')
        html = response.content.decode()
        cell = self._cell(html, 'Cadence', '/regular-series')
        self.assertEqual(cell, 'Weekly on Wednesday at 18:00 Europe/Berlin')

    def test_list_irregular_row_shows_summary_not_weekly(self):
        response = self.client.get('/studio/event-series/')
        html = response.content.decode()
        cell = self._cell(html, 'Cadence', '/irregular-series')
        self.assertEqual(cell, '3 sessions · Mon, Jun 15, 2026 – Mon, Jun 29, 2026')
        self.assertNotIn('Weekly on', cell)

    def test_list_empty_series_cadence_cell_is_blank(self):
        response = self.client.get('/studio/event-series/')
        html = response.content.decode()
        cell = self._cell(html, 'Cadence', '/empty-series')
        self.assertEqual(cell, 'No occurrences scheduled')

    # --- Studio detail header ---------------------------------------------

    def _cadence_header(self, series):
        response = self.client.get(f'/studio/event-series/{series.pk}/')
        match = re.search(
            r'data-testid="event-series-cadence"[^>]*>(.*?)</p>',
            response.content.decode(), re.DOTALL,
        )
        self.assertIsNotNone(match, 'event-series-cadence paragraph not found')
        return ' '.join(match.group(1).split())

    def test_detail_regular_header_shows_weekly_and_suffix(self):
        self.assertEqual(
            self._cadence_header(self.regular),
            'Weekly on Wednesday at 18:00 Europe/Berlin — 3 occurrences',
        )

    def test_detail_irregular_header_shows_summary_not_weekly(self):
        header = self._cadence_header(self.irregular)
        self.assertEqual(
            header,
            '3 sessions · Mon, Jun 15, 2026 – Mon, Jun 29, 2026 — 3 occurrences',
        )
        self.assertNotIn('Weekly on', header)

    def test_detail_empty_header_has_no_stray_separator(self):
        # schedule_label == '' -> no leading clause, no orphaned em dash.
        header = self._cadence_header(self.empty)
        self.assertEqual(header, '0 occurrences')
        self.assertNotIn('Weekly on', header)
        self.assertNotIn('—', header)

    def test_surfaces_agree_with_schedule_label(self):
        # Each surface's cadence clause equals the series' schedule_label.
        list_html = self.client.get('/studio/event-series/').content.decode()
        for series, slug in (
            (self.regular, '/regular-series'),
            (self.irregular, '/irregular-series'),
        ):
            label = series.schedule_label
            list_cell = self._cell(list_html, 'Cadence', slug)
            self.assertEqual(list_cell, label)
            header = self._cadence_header(series)
            self.assertTrue(header.startswith(label))


class StudioEventSeriesDeleteTest(StaffMixin, TestCase):
    """Deleting the series preserves the events and unlinks them."""

    def test_delete_unlinks_events(self):
        series = EventSeries.objects.create(
            name='To Delete', start_time=time(18, 0),
        )
        Event.objects.create(
            title='Sticky', slug='sticky-event',
            start_datetime=timezone.now(),
            event_series=series, series_position=1, origin='studio',
        )
        response = self.client.post(
            f'/studio/event-series/{series.pk}/delete',
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EventSeries.objects.filter(pk=series.pk).exists())
        # Event still exists, just unlinked.
        event = Event.objects.get(slug='sticky-event')
        self.assertIsNone(event.event_series_id)


class StudioEventListSurfacesSeriesTest(StaffMixin, TestCase):
    """``/studio/events/`` shows compact origin/series controls."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Listed Series', start_time=time(18, 0),
        )
        cls.studio_event = Event.objects.create(
            title='Studio Member', slug='studio-member',
            start_datetime=timezone.now(),
            event_series=cls.series, series_position=1, origin='studio',
        )
        cls.github_event = Event.objects.create(
            title='GitHub Event', slug='github-event',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
        )

    def test_studio_origin_has_no_github_icon(self):
        response = self.client.get('/studio/events/')
        body = response.content.decode()
        studio_row = body[
            body.index('Studio Member'):body.index('GitHub Event')
        ]
        self.assertNotIn('data-testid="origin-github-icon"', studio_row)

    def test_github_origin_icon(self):
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="origin-github-icon"')
        self.assertContains(response, 'aria-label="Synced from GitHub"')

    def test_series_column_links_to_series(self):
        response = self.client.get('/studio/events/')
        self.assertContains(
            response, f'/studio/event-series/{self.series.pk}/',
        )
        self.assertContains(response, 'data-testid="event-series-link"')
        self.assertContains(response, 'data-lucide="layers"')
        self.assertContains(response, 'title="Listed Series"')
        self.assertNotContains(response, '>Listed Series<')

    def test_new_event_series_button_present(self):
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-series-new-button"')
        self.assertContains(response, '/studio/event-series/new')

    def test_new_event_button_present(self):
        """Issue #574 added the ``New event`` button next to the series one."""
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-new-button"')
        self.assertContains(response, '>New event<')

    def test_event_create_url_returns_200(self):
        """Issue #574: ``/studio/events/new`` renders the create form."""
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)


class StudioEventEditOriginGatingTest(StaffMixin, TestCase):
    """``/studio/events/<id>/edit`` branches on ``event.origin``."""

    def test_studio_origin_event_renders_full_form(self):
        event = Event.objects.create(
            title='Editable', slug='editable-event',
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            origin='studio',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'This content is synced from GitHub')
        self.assertContains(response, 'Save Changes')

    def test_studio_origin_event_post_updates_fields(self):
        event = Event.objects.create(
            title='Original', slug='editable-post',
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            origin='studio',
        )
        future = date.today() + timedelta(days=10)
        response = self.client.post(
            f'/studio/events/{event.pk}/edit',
            {
                'title': 'Updated Title',
                'slug': 'editable-post',
                'description': 'New description body.',
                'event_date': future.strftime('%d/%m/%Y'),
                'event_time': '19:00',
                'duration_hours': '2',
                'platform': 'zoom',
                'status': 'upcoming',
                # Issue #665: keep storage in UTC to match the typed
                # wall-clock value; the picker round-trip is covered in
                # studio.tests.test_events.
                'timezone': 'UTC',
                'required_level': '0',
                'tags': '',
                'location': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.title, 'Updated Title')
        self.assertEqual(event.description, 'New description body.')
        self.assertEqual(event.start_datetime.hour, 19)
        self.assertEqual(event.status, 'upcoming')

    def test_github_origin_event_shows_synced_banner(self):
        event = Event.objects.create(
            title='Synced Event', slug='synced-event-edit',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/synced-event-edit.yaml',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This content is synced from GitHub')

    def test_github_origin_event_title_post_is_silently_ignored(self):
        """The synced branch only persists operational fields."""
        event = Event.objects.create(
            title='Synced Title', slug='synced-title-test',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/synced-title-test.yaml',
        )
        self.client.post(
            f'/studio/events/{event.pk}/edit',
            {
                'title': 'Hacked Title',
                'status': 'upcoming',
                'platform': 'zoom',
            },
        )
        event.refresh_from_db()
        # Title MUST be untouched by the synced branch.
        self.assertEqual(event.title, 'Synced Title')
        # Operational fields (status) still update.
        self.assertEqual(event.status, 'upcoming')

    def test_event_with_parent_series_renders_series_link(self):
        series = EventSeries.objects.create(
            name='Parent Series', start_time=time(18, 0),
        )
        event = Event.objects.create(
            title='Has Parent', slug='has-parent',
            start_datetime=timezone.now(),
            origin='studio',
            event_series=series, series_position=1,
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="event-parent-series"')
        self.assertContains(
            response, f'/studio/event-series/{series.pk}/',
        )


class StudioEventSeriesSidebarTest(StaffMixin, TestCase):
    """Studio sidebar surfaces the new Event series link."""

    def test_dashboard_sidebar_includes_event_series_link(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'data-testid="sidebar-event-series-link"')
        self.assertContains(response, '/studio/event-series/')


class StudioEventSeriesPublishTest(StaffMixin, TestCase):
    """Issue #858: per-event Publish / Unpublish on the series detail page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Publish Series', slug='publish-series', start_time=time(18, 0),
        )
        cls.draft = Event.objects.create(
            title='Draft Session', slug='publish-series-session-1',
            start_datetime=timezone.now() + timedelta(days=7),
            event_series=cls.series, series_position=1,
            status='draft', origin='studio',
        )
        cls.live = Event.objects.create(
            title='Live Session', slug='publish-series-session-2',
            start_datetime=timezone.now() + timedelta(days=14),
            event_series=cls.series, series_position=2,
            status='upcoming', origin='studio',
        )

    def _publish_url(self, event):
        return (
            f'/studio/event-series/{self.series.pk}'
            f'/events/{event.pk}/publish'
        )

    def _unpublish_url(self, event):
        return (
            f'/studio/event-series/{self.series.pk}'
            f'/events/{event.pk}/unpublish'
        )

    def test_publish_flips_draft_to_upcoming(self):
        response = self.client.post(self._publish_url(self.draft))
        self.assertEqual(response.status_code, 302)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, 'upcoming')

    def test_unpublish_flips_upcoming_to_draft(self):
        response = self.client.post(self._unpublish_url(self.live))
        self.assertEqual(response.status_code, 302)
        self.live.refresh_from_db()
        self.assertEqual(self.live.status, 'draft')

    def test_publish_does_not_resurrect_cancelled(self):
        cancelled = Event.objects.create(
            title='Cancelled Session', slug='publish-series-session-3',
            start_datetime=timezone.now() + timedelta(days=21),
            event_series=self.series, series_position=3,
            status='cancelled', origin='studio',
        )
        self.client.post(self._publish_url(cancelled))
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, 'cancelled')

    def test_publish_requires_post(self):
        response = self.client.get(self._publish_url(self.draft))
        self.assertEqual(response.status_code, 405)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, 'draft')

    def test_publish_is_staff_only(self):
        User.objects.create_user(email='plain858@test.com', password='pass')
        client = Client()
        client.login(email='plain858@test.com', password='pass')
        response = client.post(self._publish_url(self.draft))
        self.assertEqual(response.status_code, 403)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, 'draft')

    def test_publish_rejects_event_from_other_series(self):
        other = EventSeries.objects.create(
            name='Other', slug='other-pub', start_time=time(18, 0),
        )
        stray = Event.objects.create(
            title='Stray', slug='stray-858',
            start_datetime=timezone.now() + timedelta(days=2),
            event_series=other, series_position=1,
            status='draft', origin='studio',
        )
        response = self.client.post(self._publish_url(stray))
        self.assertEqual(response.status_code, 404)

    def test_detail_renders_not_published_wording_not_draft(self):
        response = self.client.get(f'/studio/event-series/{self.series.pk}/')
        self.assertContains(response, 'Not published')
        self.assertContains(response, 'data-testid="member-event-publish"')
        self.assertContains(response, 'data-testid="member-event-unpublish"')
        # The bare "Draft" badge wording is gone from the member table.
        self.assertNotContains(response, '>Draft<')


class StudioEventSeriesVisibilityToggleTest(StaffMixin, TestCase):
    """Issue #858: the metadata form drives ``EventSeries.is_active``."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Toggle Series', slug='toggle-series', start_time=time(18, 0),
        )

    def test_metadata_save_without_checkbox_hides_series(self):
        # An unchecked "Visible to the public" box is absent from POST.
        self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {'name': self.series.name, 'slug': self.series.slug,
             'description': ''},
        )
        self.series.refresh_from_db()
        self.assertFalse(self.series.is_active)

    def test_metadata_save_with_checkbox_keeps_series_visible(self):
        self.series.is_active = False
        self.series.save()
        self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {'name': self.series.name, 'slug': self.series.slug,
             'description': '', 'is_active': 'on'},
        )
        self.series.refresh_from_db()
        self.assertTrue(self.series.is_active)

    def test_detail_renders_visibility_checkbox(self):
        response = self.client.get(f'/studio/event-series/{self.series.pk}/')
        self.assertContains(response, 'data-testid="event-series-is-active"')
        self.assertContains(response, 'Visible to the public')
