"""Tests for the Sprint -> EventGroup link (issue #565).

Covers the model-level relationship (FK direction, ``SET_NULL`` semantics,
one-group-many-sprints) and the public sprint detail page's "Meeting
schedule" section (visible, hidden empty, hidden unlinked, single extra
query). Studio form / detail surfaces are covered by
``studio.tests.test_sprint_event_group``.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from events.models import Event, EventGroup
from plans.models import Sprint

User = get_user_model()


def _make_event_group(name='Weekly office hours', slug='weekly-oh'):
    return EventGroup.objects.create(
        name=name,
        slug=slug,
        cadence='weekly',
        cadence_weeks=1,
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone='Europe/Berlin',
    )


def _make_event(group, *, position, status='upcoming', location=''):
    base = datetime.datetime(2026, 5, 6, 18, 0, tzinfo=datetime.timezone.utc)
    start = base + datetime.timedelta(days=7 * (position - 1))
    slug = f'{group.slug}-session-{position}'
    return Event.objects.create(
        title=f'{group.name} — Session {position}',
        slug=slug,
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=start,
        timezone='Europe/Berlin',
        status=status,
        origin='studio',
        event_group=group,
        series_position=position,
        location=location,
        published=True,
    )


class SprintEventGroupRelationTest(TestCase):
    """Model-level FK semantics: ``SET_NULL`` + many-sprints-per-group."""

    @classmethod
    def setUpTestData(cls):
        cls.group = _make_event_group()
        cls.s1 = Sprint.objects.create(
            name='May cohort', slug='may-cohort',
            start_date=datetime.date(2026, 5, 1),
            event_group=cls.group,
        )
        cls.s2 = Sprint.objects.create(
            name='June cohort', slug='june-cohort',
            start_date=datetime.date(2026, 6, 1),
            event_group=cls.group,
        )

    def test_one_event_group_can_back_multiple_sprints(self):
        # Both sprints reference the same group; no uniqueness constraint
        # blocks the second assignment.
        self.assertEqual(self.s1.event_group_id, self.group.pk)
        self.assertEqual(self.s2.event_group_id, self.group.pk)
        self.assertEqual(self.group.sprints.count(), 2)

    def test_deleting_event_group_unlinks_sprints_but_keeps_them(self):
        event = _make_event(self.group, position=1)
        # SET_NULL: the sprint and the event should both survive deletion
        # of the group, with the FK cleared. The event has its own SET_NULL
        # back-link to the group (issue #564) so it stays alive too.
        self.group.delete()

        self.s1.refresh_from_db()
        self.s2.refresh_from_db()
        self.assertIsNone(self.s1.event_group)
        self.assertIsNone(self.s2.event_group)
        # The Event row survives -- only its event_group FK is cleared.
        event.refresh_from_db()
        self.assertIsNone(event.event_group)
        # Sprints are NOT cascaded.
        self.assertTrue(Sprint.objects.filter(pk=self.s1.pk).exists())
        self.assertTrue(Sprint.objects.filter(pk=self.s2.pk).exists())

    def test_event_group_unlink_does_not_delete_group(self):
        # Clearing the FK on a sprint must not touch the group or its events.
        event = _make_event(self.group, position=1)
        self.s1.event_group = None
        self.s1.save()

        self.assertTrue(EventGroup.objects.filter(pk=self.group.pk).exists())
        event.refresh_from_db()
        self.assertEqual(event.event_group_id, self.group.pk)
        # The other sprint's link is untouched.
        self.s2.refresh_from_db()
        self.assertEqual(self.s2.event_group_id, self.group.pk)


class PublicSprintDetailMeetingScheduleTest(TestCase):
    """The public ``/sprints/<slug>`` "Meeting schedule" section."""

    @classmethod
    def setUpTestData(cls):
        cls.group = _make_event_group(name='Wed OH', slug='wed-oh')
        cls.event_1 = _make_event(cls.group, position=1, location='Zoom')
        cls.event_2 = _make_event(cls.group, position=2, location='Zoom')
        cls.event_3 = _make_event(cls.group, position=3, location='Zoom')
        cls.linked_sprint = Sprint.objects.create(
            name='May 2026 sprint', slug='may-2026-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
            min_tier_level=0,
            event_group=cls.group,
        )
        cls.unlinked_sprint = Sprint.objects.create(
            name='Solo sprint', slug='solo-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
            min_tier_level=0,
        )
        cls.empty_group = _make_event_group(
            name='Empty group', slug='empty-group',
        )
        cls.empty_group_sprint = Sprint.objects.create(
            name='Empty sprint', slug='empty-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
            min_tier_level=0,
            event_group=cls.empty_group,
        )

    def test_section_renders_for_linked_sprint_with_events(self):
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.linked_sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-meeting-schedule"')
        # All three occurrences are listed.
        self.assertContains(
            response,
            'data-testid="sprint-meeting-schedule-row"',
            count=3,
        )
        # Each occurrence links to /events/<slug>.
        self.assertContains(response, f'/events/{self.event_1.slug}')
        self.assertContains(response, f'/events/{self.event_2.slug}')
        self.assertContains(response, f'/events/{self.event_3.slug}')
        # Heading appears.
        self.assertContains(response, 'Meeting schedule')

    def test_section_hidden_when_sprint_has_no_event_group(self):
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.unlinked_sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response, 'data-testid="sprint-meeting-schedule"',
        )
        self.assertNotContains(response, 'Meeting schedule')

    def test_section_hidden_when_linked_group_has_no_events(self):
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.empty_group_sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # The section must be hidden entirely when the group is empty
        # (no "no meetings yet" copy leaks to the public).
        self.assertNotContains(
            response, 'data-testid="sprint-meeting-schedule"',
        )
        self.assertNotContains(response, 'Meeting schedule')
        self.assertNotContains(response, 'no meetings yet')

    def test_occurrences_ordered_by_start_datetime(self):
        # Insert an out-of-order event with an earlier start to verify
        # the prefetch ORDER BY is honoured.
        out_of_order = Event.objects.create(
            title='Earlier',
            slug='wed-oh-earlier',
            description='',
            kind='standard',
            platform='zoom',
            start_datetime=datetime.datetime(
                2026, 4, 1, 18, 0, tzinfo=datetime.timezone.utc,
            ),
            timezone='Europe/Berlin',
            status='upcoming',
            origin='studio',
            event_group=self.group,
            series_position=99,
            published=True,
        )
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.linked_sprint.slug},
        )
        response = self.client.get(url)
        content = response.content.decode()
        # Earliest event slug appears before the May events.
        pos_earliest = content.index(out_of_order.slug)
        pos_session_1 = content.index(self.event_1.slug)
        self.assertLess(pos_earliest, pos_session_1)

    def test_query_count_is_bounded(self):
        # ``select_related('event_group')`` collapses the group lookup
        # into the sprint query; ``prefetch_related`` adds a second
        # query for the events. For an anonymous viewer the only
        # database hits are the sprint+group join and the events
        # prefetch -- exactly two queries. A regression to N+1 (one
        # query per event) would blow past this immediately as more
        # occurrences are added.
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.linked_sprint.slug},
        )
        with self.assertNumQueries(2):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Adding more events MUST NOT increase the query count -- the
        # prefetch should still cover the whole set.
        for i in range(4, 10):
            _make_event(self.group, position=i)
        with self.assertNumQueries(2):
            self.client.get(url)


class SprintEventGroupFKDirectionTest(TestCase):
    """The FK lives on Sprint and is optional on both ends."""

    @classmethod
    def setUpTestData(cls):
        cls.group = _make_event_group()

    def test_sprint_can_be_created_without_event_group(self):
        # blank=True / null=True default behavior: no value required.
        sprint = Sprint.objects.create(
            name='Lone', slug='lone',
            start_date=datetime.date(2026, 5, 1),
        )
        self.assertIsNone(sprint.event_group)

    def test_related_name_sprints_resolves_from_group(self):
        s = Sprint.objects.create(
            name='S', slug='s',
            start_date=datetime.date(2026, 5, 1),
            event_group=self.group,
        )
        self.assertIn(s, list(self.group.sprints.all()))
