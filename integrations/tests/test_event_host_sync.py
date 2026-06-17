"""GitHub content sync host mapping tests."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from content.models import Instructor, Workshop
from events.models import Event, EventHost, EventInstructor, Host
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


class _EventHostSyncFixture(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/content',
            is_private=False,
            prefix='event-host-sync-',
        )
        self.alexey, _ = Host.objects.get_or_create(
            slug='alexey-grigorev',
            defaults={'name': 'Alexey Grigorev'},
        )
        self.valeriia, _ = Host.objects.get_or_create(
            slug='valeriia-kuka',
            defaults={'name': 'Valeriia Kuka'},
        )

    def _write_event(self, **overrides):
        data = {
            'content_id': '11111111-1111-1111-1111-111111111111',
            'slug': 'hosted-event',
            'title': 'Hosted Event',
            'status': 'completed',
            'start_datetime': '2026-06-10T12:00:00Z',
            'recording_url': 'https://example.com/recording',
        }
        data.update(overrides)
        self.repo.write_yaml('events/hosted-event.yaml', data)

    def _write_workshop(self, **overrides):
        data = {
            'content_id': '22222222-2222-2222-2222-222222222222',
            'slug': 'hosted-workshop',
            'title': 'Hosted Workshop',
            'date': '2026-06-11',
            'pages_required_level': 0,
            'recording': {
                'url': 'https://www.youtube.com/watch?v=abcdefghijk',
                'required_level': 0,
            },
        }
        data.update(overrides)
        self.repo.write_yaml('2026/2026-06-11-hosted-workshop/workshop.yaml', data)

    def _event_host_slugs(self, event):
        return list(
            EventHost.objects.filter(event=event)
            .order_by('position')
            .values_list('host__slug', flat=True)
        )


class ContentEventHostSyncTest(_EventHostSyncFixture):
    def test_content_event_hosts_replace_in_order_and_instructors_stay_separate(self):
        Instructor.objects.create(
            instructor_id='alexey-grigorev',
            name='Alexey Grigorev',
            status='published',
        )
        self._write_event(
            hosts=['valeriia-kuka', 'alexey-grigorev'],
            instructors=['alexey-grigorev'],
            speaker_name='Alexey Grigorev',
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(sync_log.errors, [], sync_log.errors)
        event = Event.objects.get(slug='hosted-event')
        self.assertEqual(
            self._event_host_slugs(event),
            ['valeriia-kuka', 'alexey-grigorev'],
        )
        self.assertEqual(
            list(
                EventInstructor.objects.filter(event=event)
                .order_by('position')
                .values_list('instructor__instructor_id', flat=True)
            ),
            ['alexey-grigorev'],
        )

        self._write_event(hosts=['alexey-grigorev'])
        sync_repo(self.source, self.repo)
        self.assertEqual(self._event_host_slugs(event), ['alexey-grigorev'])
        self.assertEqual(EventInstructor.objects.filter(event=event).count(), 1)

    def test_legacy_speaker_name_fallback_and_hosts_empty_clear(self):
        self._write_event(speaker_name='Alexey Grigorev')
        sync_repo(self.source, self.repo)
        event = Event.objects.get(slug='hosted-event')
        self.assertEqual(self._event_host_slugs(event), ['alexey-grigorev'])

        self._write_event(hosts=[], speaker_name='Alexey Grigorev')
        sync_repo(self.source, self.repo)
        self.assertEqual(self._event_host_slugs(event), [])

    def test_all_unknown_hosts_leave_existing_assignment_unchanged(self):
        self._write_event(hosts=['alexey-grigorev'])
        sync_repo(self.source, self.repo)
        event = Event.objects.get(slug='hosted-event')

        self._write_event(hosts=['unknown-host'])
        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(sync_log.errors, [], sync_log.errors)
        self.assertEqual(self._event_host_slugs(event), ['alexey-grigorev'])
        self.assertFalse(Host.objects.filter(slug='unknown-host').exists())

    def test_malformed_hosts_records_sync_error_and_leaves_existing_assignment(self):
        self._write_event(hosts=['alexey-grigorev'])
        sync_repo(self.source, self.repo)
        event = Event.objects.get(slug='hosted-event')

        self._write_event(hosts='alexey-grigorev')
        sync_log = sync_repo(self.source, self.repo)

        self.assertTrue(
            any('hosts:' in error['error'] for error in sync_log.errors),
            sync_log.errors,
        )
        self.assertEqual(self._event_host_slugs(event), ['alexey-grigorev'])

    def test_re_sync_unchanged_hosts_is_idempotent(self):
        self._write_event(hosts=['valeriia-kuka', 'alexey-grigorev'])
        sync_repo(self.source, self.repo)

        sync_log = sync_repo(self.source, self.repo)
        event = Event.objects.get(slug='hosted-event')

        self.assertEqual(
            self._event_host_slugs(event),
            ['valeriia-kuka', 'alexey-grigorev'],
        )
        self.assertEqual(EventHost.objects.filter(event=event).count(), 2)
        self.assertEqual(sync_log.items_updated, 0)
        self.assertFalse(
            any(
                item.get('content_type') == 'event'
                and item.get('action') == 'updated'
                for item in sync_log.items_detail
            ),
            sync_log.items_detail,
        )


class WorkshopEventHostSyncTest(_EventHostSyncFixture):
    def test_workshop_hosts_replace_generated_github_event_hosts(self):
        self._write_workshop(hosts=['alexey-grigorev'])
        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(sync_log.errors, [], sync_log.errors)
        workshop = Workshop.objects.get(slug='hosted-workshop')
        self.assertEqual(self._event_host_slugs(workshop.event), ['alexey-grigorev'])

        self._write_workshop(
            hosts=['valeriia-kuka'],
            instructor_name='Alexey Grigorev',
        )
        sync_repo(self.source, self.repo)
        workshop.refresh_from_db()
        self.assertEqual(self._event_host_slugs(workshop.event), ['valeriia-kuka'])

    def test_workshop_legacy_instructor_name_fallback(self):
        self._write_workshop(instructor_name='Valeriia Kuka')
        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(sync_log.errors, [], sync_log.errors)
        workshop = Workshop.objects.get(slug='hosted-workshop')
        self.assertEqual(self._event_host_slugs(workshop.event), ['valeriia-kuka'])

    def test_workshop_linked_studio_event_keeps_api_managed_hosts(self):
        start = timezone.now() + timedelta(days=7)
        studio_event = Event.objects.create(
            title='Studio Event',
            slug='studio-linked-event',
            start_datetime=start,
            status='upcoming',
            origin='studio',
        )
        EventHost.objects.create(
            event=studio_event,
            host=self.valeriia,
            position=0,
        )
        self._write_workshop(
            event_id=studio_event.id,
            hosts=['alexey-grigorev'],
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(sync_log.errors, [], sync_log.errors)
        workshop = Workshop.objects.get(slug='hosted-workshop')
        self.assertEqual(workshop.event_id, studio_event.id)
        self.assertEqual(self._event_host_slugs(studio_event), ['valeriia-kuka'])
