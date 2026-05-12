"""Tests for the Studio event-group create/detail/edit/delete views.

Issue #564.
"""

from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event, EventGroup

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


class StudioEventGroupAccessTest(StaffMixin, TestCase):
    """Access control on the studio event-group endpoints."""

    def test_anonymous_redirected_from_new(self):
        client = Client()
        response = client.get('/studio/event-groups/new')
        self.assertEqual(response.status_code, 302)

    def test_non_staff_forbidden(self):
        User.objects.create_user(email='plain@test.com', password='pass')
        client = Client()
        client.login(email='plain@test.com', password='pass')
        response = client.get('/studio/event-groups/new')
        self.assertEqual(response.status_code, 403)

    def test_staff_get_new_returns_200(self):
        response = self.client.get('/studio/event-groups/new')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'event-group-submit')


class StudioEventGroupCreateTest(StaffMixin, TestCase):
    """``POST /studio/event-groups/new`` creates a group + N events."""

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
        return self.client.post('/studio/event-groups/new', payload)

    def test_creates_one_group_and_six_events(self):
        response = self._post_valid()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventGroup.objects.count(), 1)
        group = EventGroup.objects.get()
        self.assertEqual(group.events.count(), 6)
        self.assertEqual(group.slug, 'spring-workshop-series')

    def test_events_are_studio_origin_and_linked_to_group(self):
        self._post_valid()
        group = EventGroup.objects.get()
        events = list(group.events.all().order_by('series_position'))
        for i, event in enumerate(events, start=1):
            self.assertEqual(event.origin, 'studio')
            self.assertIn(event.source_repo, (None, ''))
            self.assertEqual(event.event_group_id, group.pk)
            self.assertEqual(event.series_position, i)
            self.assertEqual(event.status, 'draft')

    def test_events_spaced_seven_days_apart(self):
        self._post_valid()
        group = EventGroup.objects.get()
        events = list(group.events.all().order_by('series_position'))
        for i in range(1, len(events)):
            delta = events[i].start_datetime - events[i - 1].start_datetime
            self.assertEqual(delta, timedelta(days=7))

    def test_end_datetime_equals_start_plus_duration(self):
        self._post_valid(duration_hours='1.5')
        group = EventGroup.objects.get()
        for event in group.events.all():
            self.assertEqual(
                event.end_datetime - event.start_datetime,
                timedelta(hours=1.5),
            )

    def test_occurrences_zero_re_renders_form_and_creates_nothing(self):
        response = self._post_valid(occurrences='0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EventGroup.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        self.assertContains(response, 'error-occurrences')

    def test_occurrences_too_high_re_renders_form_and_creates_nothing(self):
        response = self._post_valid(occurrences='27')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EventGroup.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        self.assertContains(response, 'error-occurrences')

    def test_slug_collision_appends_suffix(self):
        Event.objects.create(
            title='Pre-existing', slug='spring-workshop-series-session-1',
            start_datetime=timezone.now(), origin='studio',
        )
        self._post_valid()
        group = EventGroup.objects.get()
        first = group.events.get(series_position=1)
        # The auto-derived ``spring-workshop-series-session-1`` is taken,
        # so the generator picks ``...-1-2`` for this session.
        self.assertNotEqual(first.slug, 'spring-workshop-series-session-1')
        self.assertTrue(first.slug.startswith('spring-workshop-series-session-1'))


class StudioEventGroupDetailTest(StaffMixin, TestCase):
    """Detail page shows member events with edit links."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.group = EventGroup.objects.create(
            name='Detail Series', start_time=time(18, 0),
        )
        for i in range(1, 4):
            Event.objects.create(
                title=f'Session {i}',
                slug=f'detail-session-{i}',
                start_datetime=timezone.now() + timedelta(days=7 * i),
                event_group=cls.group, series_position=i,
                origin='studio',
            )

    def test_detail_renders_member_events(self):
        response = self.client.get(f'/studio/event-groups/{self.group.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Session 1')
        self.assertContains(response, 'Session 2')
        self.assertContains(response, 'Session 3')

    def test_edit_links_point_to_event_edit(self):
        response = self.client.get(f'/studio/event-groups/{self.group.pk}/')
        event = self.group.events.get(series_position=1)
        self.assertContains(
            response, f'/studio/events/{event.pk}/edit',
        )

    def test_metadata_post_updates_group(self):
        response = self.client.post(
            f'/studio/event-groups/{self.group.pk}/',
            {
                'name': 'Renamed Series',
                'slug': self.group.slug,
                'description': 'Now with a description.',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'Renamed Series')
        self.assertIn('description', self.group.description)


class StudioEventGroupAddOccurrenceTest(StaffMixin, TestCase):
    """``POST .../add-occurrence`` appends one more event."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.group = EventGroup.objects.create(
            name='Add Series', slug='add-series', start_time=time(18, 0),
        )
        for i in range(1, 4):
            Event.objects.create(
                title=f'Add Session {i}',
                slug=f'add-series-session-{i}',
                start_datetime=timezone.now() + timedelta(days=7 * i),
                event_group=cls.group, series_position=i, origin='studio',
            )

    def test_add_occurrence_creates_one_event_and_advances_position(self):
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        response = self.client.post(
            f'/studio/event-groups/{self.group.pk}/add-occurrence',
            {'start_date': start, 'duration_hours': '1'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.group.events.count(), 4)
        new_event = self.group.events.order_by('-series_position').first()
        self.assertEqual(new_event.series_position, 4)
        self.assertEqual(new_event.origin, 'studio')
        self.assertEqual(new_event.event_group_id, self.group.pk)


class StudioEventGroupDeleteTest(StaffMixin, TestCase):
    """Deleting the group preserves the events and unlinks them."""

    def test_delete_unlinks_events(self):
        group = EventGroup.objects.create(
            name='To Delete', start_time=time(18, 0),
        )
        Event.objects.create(
            title='Sticky', slug='sticky-event',
            start_datetime=timezone.now(),
            event_group=group, series_position=1, origin='studio',
        )
        response = self.client.post(
            f'/studio/event-groups/{group.pk}/delete',
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EventGroup.objects.filter(pk=group.pk).exists())
        # Event still exists, just unlinked.
        event = Event.objects.get(slug='sticky-event')
        self.assertIsNone(event.event_group_id)


class StudioEventListSurfacesGroupsTest(StaffMixin, TestCase):
    """``/studio/events/`` shows origin badges, group column, new-series button."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.group = EventGroup.objects.create(
            name='Listed Series', start_time=time(18, 0),
        )
        cls.studio_event = Event.objects.create(
            title='Studio Member', slug='studio-member',
            start_datetime=timezone.now(),
            event_group=cls.group, series_position=1, origin='studio',
        )
        cls.github_event = Event.objects.create(
            title='GitHub Event', slug='github-event',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
        )

    def test_origin_badge_studio(self):
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-origin="studio"')

    def test_origin_badge_github(self):
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-origin="github"')

    def test_series_column_links_to_group(self):
        response = self.client.get('/studio/events/')
        self.assertContains(
            response, f'/studio/event-groups/{self.group.pk}/',
        )
        self.assertContains(response, 'data-testid="event-series-link"')

    def test_new_event_series_button_present(self):
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-series-new-button"')
        self.assertContains(response, '/studio/event-groups/new')

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
                'timezone': 'Europe/Berlin',
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
                'max_participants': '',
                'platform': 'zoom',
            },
        )
        event.refresh_from_db()
        # Title MUST be untouched by the synced branch.
        self.assertEqual(event.title, 'Synced Title')
        # Operational fields (status) still update.
        self.assertEqual(event.status, 'upcoming')

    def test_event_with_parent_group_renders_group_link(self):
        group = EventGroup.objects.create(
            name='Parent Group', start_time=time(18, 0),
        )
        event = Event.objects.create(
            title='Has Parent', slug='has-parent',
            start_datetime=timezone.now(),
            origin='studio',
            event_group=group, series_position=1,
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="event-parent-group"')
        self.assertContains(
            response, f'/studio/event-groups/{group.pk}/',
        )


class StudioEventGroupSidebarTest(StaffMixin, TestCase):
    """Studio sidebar surfaces the new Event groups link."""

    def test_dashboard_sidebar_includes_event_groups_link(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'data-testid="sidebar-event-groups-link"')
        self.assertContains(response, '/studio/event-groups/')
