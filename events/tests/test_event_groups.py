"""Tests for the EventGroup model and origin invariant on Event.

Issue #564.

Covers:
- ``EventGroup`` model: slug auto-derivation, description markdown.
- ``Event.origin`` invariant: github iff source_repo is set.
- ``studio.utils.is_synced`` branching on ``origin``.
- Public ``/events/groups/<slug>`` view.
- Public events list shows series link when an event belongs to a group.
"""

from datetime import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventGroup
from studio.utils import is_synced

User = get_user_model()


class EventGroupModelTest(TestCase):
    """EventGroup save behavior and computed properties."""

    def test_slug_auto_derived_from_name(self):
        group = EventGroup.objects.create(
            name='Spring Workshop Series',
            start_time=time(18, 0),
        )
        self.assertEqual(group.slug, 'spring-workshop-series')

    def test_explicit_slug_preserved(self):
        group = EventGroup.objects.create(
            name='Spring Workshop Series',
            slug='custom-slug',
            start_time=time(18, 0),
        )
        self.assertEqual(group.slug, 'custom-slug')

    def test_description_renders_to_html(self):
        group = EventGroup.objects.create(
            name='Markdown Series',
            description='# Heading\n\nA paragraph.',
            start_time=time(18, 0),
        )
        self.assertIn('<h1>Heading</h1>', group.description_html)

    def test_event_count_reflects_member_events(self):
        group = EventGroup.objects.create(
            name='Counted', start_time=time(18, 0),
        )
        Event.objects.create(
            title='Session 1', slug='counted-session-1',
            start_datetime=timezone.now(),
            event_group=group, series_position=1, origin='studio',
        )
        self.assertEqual(group.event_count, 1)


class EventOriginInvariantTest(TestCase):
    """``Event.save()`` enforces origin/source_repo consistency."""

    def test_studio_origin_with_source_repo_raises(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            Event.objects.create(
                title='Bad', slug='bad-studio',
                start_datetime=timezone.now(),
                origin='studio',
                source_repo='AI-Shipping-Labs/content',
            )

    def test_github_origin_without_source_repo_raises(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            Event.objects.create(
                title='Bad', slug='bad-github',
                start_datetime=timezone.now(),
                origin='github',
                source_repo='',
            )

    def test_studio_origin_with_empty_source_repo_succeeds(self):
        event = Event.objects.create(
            title='Good', slug='good-studio',
            start_datetime=timezone.now(),
            origin='studio',
        )
        self.assertEqual(event.origin, 'studio')

    def test_github_origin_with_source_repo_succeeds(self):
        event = Event.objects.create(
            title='Good', slug='good-github',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/good.yaml',
        )
        self.assertEqual(event.origin, 'github')


class IsSyncedHelperTest(TestCase):
    """``studio.utils.is_synced`` branches on ``origin`` for events."""

    def test_studio_origin_event_is_not_synced(self):
        event = Event.objects.create(
            title='Studio Event', slug='studio-event',
            start_datetime=timezone.now(),
            origin='studio',
        )
        self.assertFalse(is_synced(event))

    def test_github_origin_event_is_synced(self):
        event = Event.objects.create(
            title='GitHub Event', slug='github-event',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
        )
        self.assertTrue(is_synced(event))

    def test_legacy_object_without_origin_still_works(self):
        """Models without ``origin`` keep the legacy source_repo fallback."""
        # Use a plain object that mimics a non-event model with no origin.
        class Legacy:
            origin = None
            source_repo = 'AI-Shipping-Labs/content'
        self.assertTrue(is_synced(Legacy()))

        class LegacyEmpty:
            source_repo = None
        self.assertFalse(is_synced(LegacyEmpty()))


class PublicEventGroupViewTest(TestCase):
    """Public ``/events/groups/<slug>`` page."""

    @classmethod
    def setUpTestData(cls):
        cls.group = EventGroup.objects.create(
            name='Spring Series', start_time=time(18, 0),
        )
        cls.published_event = Event.objects.create(
            title='Series Session 1', slug='series-session-1',
            start_datetime=timezone.now(),
            status='upcoming',
            event_group=cls.group, series_position=1, origin='studio',
        )
        cls.draft_event = Event.objects.create(
            title='Series Session 2', slug='series-session-2',
            start_datetime=timezone.now(),
            status='draft',
            event_group=cls.group, series_position=2, origin='studio',
        )

    def test_anonymous_visitor_sees_published_events(self):
        response = self.client.get(f'/events/groups/{self.group.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Series Session 1')

    def test_anonymous_visitor_does_not_see_drafts(self):
        response = self.client.get(f'/events/groups/{self.group.slug}')
        self.assertNotContains(response, 'Series Session 2')

    def test_unknown_slug_returns_404(self):
        response = self.client.get('/events/groups/does-not-exist')
        self.assertEqual(response.status_code, 404)

    def test_staff_sees_drafts(self):
        staff = User.objects.create_user(
            email='staff@test.com', password='pass', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(f'/events/groups/{self.group.slug}')
        self.assertContains(response, 'Series Session 2')

    def test_event_detail_url_still_resolves_after_groups_route(self):
        """The ``/events/groups/<slug>`` route must not swallow event slugs."""
        response = self.client.get(f'/events/{self.published_event.slug}')
        self.assertEqual(response.status_code, 200)


class PublicEventsListSeriesLinkTest(TestCase):
    """Public events listing surfaces a series link for grouped events."""

    @classmethod
    def setUpTestData(cls):
        cls.group = EventGroup.objects.create(
            name='Grouped Series', slug='grouped-series',
            start_time=time(18, 0),
        )
        cls.grouped = Event.objects.create(
            title='Grouped Event', slug='grouped-event',
            start_datetime=timezone.now() + timezone.timedelta(days=1),
            status='upcoming',
            event_group=cls.group, series_position=1, origin='studio',
        )
        cls.standalone = Event.objects.create(
            title='Standalone Event', slug='standalone-event',
            start_datetime=timezone.now() + timezone.timedelta(days=1),
            status='upcoming',
            origin='studio',
        )

    def test_grouped_event_has_series_link(self):
        response = self.client.get('/events?filter=upcoming')
        self.assertContains(response, 'Series: Grouped Series')
        self.assertContains(response, '/events/groups/grouped-series')

    def test_standalone_event_has_no_series_link(self):
        response = self.client.get('/events?filter=upcoming')
        # The standalone event title is present but no "Series: " label
        # is rendered for it.
        self.assertContains(response, 'Standalone Event')
        # The total "Series: " occurrences must equal the number of
        # grouped events on the page (1).
        self.assertEqual(
            response.content.decode().count('Series:'), 1,
        )
