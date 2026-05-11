"""Studio surfaces for linking event groups to sprints (issue #565).

Covers the form (event-group select renders, pre-selects, persists,
unlinks, rejects invalid ids with HTTP 400) and the detail page
("Event group" section: linked group + occurrence table, or
empty-state + "Link an event group" CTA).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from events.models import Event, EventGroup
from plans.models import Sprint

User = get_user_model()


def _make_event_group(name='Wednesday office hours', slug='weekly-oh'):
    return EventGroup.objects.create(
        name=name,
        slug=slug,
        cadence='weekly',
        cadence_weeks=1,
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone='Europe/Berlin',
    )


def _make_event(group, *, position, status='upcoming'):
    base = datetime.datetime(2026, 5, 6, 18, 0, tzinfo=datetime.timezone.utc)
    start = base + datetime.timedelta(days=7 * (position - 1))
    return Event.objects.create(
        title=f'{group.name} — Session {position}',
        slug=f'{group.slug}-session-{position}',
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=start,
        timezone='Europe/Berlin',
        status=status,
        origin='studio',
        event_group=group,
        series_position=position,
        published=True,
    )


class SprintFormEventGroupSelectTest(TestCase):
    """The Studio sprint form renders the Event group dropdown."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.group_a = _make_event_group(name='Alpha series', slug='alpha')
        cls.group_b = _make_event_group(name='Beta series', slug='beta')

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_create_form_lists_all_event_groups_with_none_default(self):
        response = self.client.get('/studio/sprints/new')
        self.assertEqual(response.status_code, 200)
        # The select is named ``event_group`` and exposes a stable test id.
        self.assertContains(
            response, 'data-testid="sprint-event-group"',
        )
        # Both groups appear as options.
        self.assertContains(
            response, f'<option value="{self.group_a.pk}"',
        )
        self.assertContains(
            response, f'<option value="{self.group_b.pk}"',
        )
        self.assertContains(response, 'Alpha series')
        self.assertContains(response, 'Beta series')
        # The "— None —" placeholder is selected by default on a new sprint.
        self.assertContains(
            response,
            '<option value="" selected>— None —</option>',
            html=True,
        )

    def test_edit_form_pre_selects_currently_linked_group(self):
        sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
            event_group=self.group_a,
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<option value="{self.group_a.pk}" selected>Alpha series</option>',
            html=True,
        )
        # The other group should be present but NOT selected.
        self.assertContains(
            response,
            f'<option value="{self.group_b.pk}">Beta series</option>',
            html=True,
        )

    def test_edit_form_shows_unlink_hint(self):
        sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
            event_group=self.group_a,
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/edit')
        self.assertContains(
            response,
            'data-testid="sprint-event-group-unlink-hint"',
        )

    def test_create_form_does_not_show_unlink_hint(self):
        response = self.client.get('/studio/sprints/new')
        self.assertNotContains(
            response,
            'data-testid="sprint-event-group-unlink-hint"',
        )

    def test_create_form_shows_create_group_hint_link(self):
        response = self.client.get('/studio/sprints/new')
        self.assertContains(
            response,
            'data-testid="sprint-create-event-group-hint"',
        )
        # Link must point at the event-group new flow.
        self.assertContains(response, '/studio/event-groups/new')


class SprintFormEventGroupPersistenceTest(TestCase):
    """Submitting the form persists / clears the FK correctly."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.group = _make_event_group()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_create_with_event_group_selected_persists_fk(self):
        response = self.client.post('/studio/sprints/new', {
            'name': 'Linked sprint',
            'slug': 'linked-sprint',
            'start_date': '2026-05-01',
            'duration_weeks': '6',
            'status': 'draft',
            'event_group': str(self.group.pk),
        })
        self.assertEqual(response.status_code, 302)
        sprint = Sprint.objects.get(slug='linked-sprint')
        self.assertEqual(sprint.event_group_id, self.group.pk)

    def test_create_without_event_group_persists_null(self):
        response = self.client.post('/studio/sprints/new', {
            'name': 'Solo sprint',
            'slug': 'solo-sprint',
            'start_date': '2026-05-01',
            'duration_weeks': '6',
            'status': 'draft',
            'event_group': '',
        })
        self.assertEqual(response.status_code, 302)
        sprint = Sprint.objects.get(slug='solo-sprint')
        self.assertIsNone(sprint.event_group)

    def test_edit_selecting_none_clears_fk(self):
        sprint = Sprint.objects.create(
            name='Linked', slug='linked',
            start_date=datetime.date(2026, 5, 1),
            event_group=self.group,
        )
        # Add one event so we can verify the unlink does not delete it.
        event = _make_event(self.group, position=1)

        response = self.client.post(
            f'/studio/sprints/{sprint.pk}/edit',
            {
                'name': sprint.name,
                'slug': sprint.slug,
                'start_date': sprint.start_date.isoformat(),
                'duration_weeks': str(sprint.duration_weeks),
                'status': sprint.status,
                'event_group': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        sprint.refresh_from_db()
        self.assertIsNone(sprint.event_group)
        # Group and events survive the unlink.
        self.assertTrue(EventGroup.objects.filter(pk=self.group.pk).exists())
        event.refresh_from_db()
        self.assertEqual(event.event_group_id, self.group.pk)

    def test_edit_invalid_event_group_id_returns_400_and_no_write(self):
        sprint = Sprint.objects.create(
            name='Linked', slug='linked',
            start_date=datetime.date(2026, 5, 1),
            event_group=self.group,
        )
        original_group_id = sprint.event_group_id

        response = self.client.post(
            f'/studio/sprints/{sprint.pk}/edit',
            {
                'name': sprint.name,
                'slug': sprint.slug,
                'start_date': sprint.start_date.isoformat(),
                'duration_weeks': str(sprint.duration_weeks),
                'status': sprint.status,
                'event_group': '99999',  # non-existent
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            'Selected event group does not exist.',
            status_code=400,
        )
        sprint.refresh_from_db()
        # The FK did NOT change.
        self.assertEqual(sprint.event_group_id, original_group_id)

    def test_create_with_invalid_event_group_id_returns_400_and_no_write(self):
        before = Sprint.objects.count()
        response = self.client.post('/studio/sprints/new', {
            'name': 'Broken',
            'slug': 'broken',
            'start_date': '2026-05-01',
            'duration_weeks': '6',
            'status': 'draft',
            'event_group': '99999',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            'Selected event group does not exist.',
            status_code=400,
        )
        self.assertEqual(Sprint.objects.count(), before)


class SprintDetailEventGroupSectionTest(TestCase):
    """Studio sprint detail renders linked group or empty-state CTA."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.group = _make_event_group()
        cls.event_1 = _make_event(cls.group, position=1)
        cls.event_2 = _make_event(cls.group, position=2)
        cls.linked_sprint = Sprint.objects.create(
            name='L', slug='l',
            start_date=datetime.date(2026, 5, 1),
            event_group=cls.group,
        )
        cls.unlinked_sprint = Sprint.objects.create(
            name='U', slug='u',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_linked_sprint_shows_group_name_and_event_rows(self):
        response = self.client.get(
            f'/studio/sprints/{self.linked_sprint.pk}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="sprint-event-group-link"',
        )
        # Group name links to the studio event-group detail.
        self.assertContains(
            response,
            f'/studio/event-groups/{self.group.pk}/',
        )
        self.assertContains(
            response, 'data-testid="sprint-event-group-count"',
        )
        # One row per event.
        self.assertContains(
            response, 'data-testid="sprint-event-group-row"', count=2,
        )
        # Each event title links to its Studio edit page.
        self.assertContains(
            response, f'/studio/events/{self.event_1.pk}/edit',
        )
        self.assertContains(
            response, f'/studio/events/{self.event_2.pk}/edit',
        )

    def test_unlinked_sprint_shows_empty_state_and_cta(self):
        response = self.client.get(
            f'/studio/sprints/{self.unlinked_sprint.pk}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="sprint-event-group-empty"',
        )
        self.assertContains(
            response, 'data-testid="sprint-event-group-link-cta"',
        )
        # CTA points to the edit form with the anchor for the field.
        self.assertContains(
            response,
            f'/studio/sprints/{self.unlinked_sprint.pk}/edit#event-group-field',
        )
        # No occurrence table is rendered.
        self.assertNotContains(
            response, 'data-testid="sprint-event-group-row"',
        )

    def test_linked_group_with_no_events_shows_empty_events_state(self):
        empty_group = _make_event_group(
            name='Empty group', slug='empty-group',
        )
        sprint = Sprint.objects.create(
            name='Empty', slug='empty-link',
            start_date=datetime.date(2026, 5, 1),
            event_group=empty_group,
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/')
        self.assertEqual(response.status_code, 200)
        # The group is linked, so the link still renders ...
        self.assertContains(
            response, 'data-testid="sprint-event-group-link"',
        )
        # ... but there is an inner empty-events note (Studio-only copy).
        self.assertContains(
            response,
            'data-testid="sprint-event-group-empty-events"',
        )
        self.assertNotContains(
            response, 'data-testid="sprint-event-group-row"',
        )
