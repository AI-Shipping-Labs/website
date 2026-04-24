"""Tests for the workshop sync pipeline (issue #295).

Covers the parser and Event-linking behavior called out in the spec:
- Happy path: one workshop.yaml + two pages -> 1 Workshop, 2 WorkshopPage,
  1 linked Event with kind='workshop', event_type='async', status='completed'
  and recording fields populated.
- Re-sync is idempotent: running sync twice does NOT create a second Event
  or a second Workshop.
- Pre-existing Event with matching slug is reused (not re-created); content
  fields update, operational fields (start_datetime, zoom_*, status) do not.
- Folder without workshop.yaml is silently skipped — no Workshop row, no
  error in the sync log.
- Parser rejects a workshop.yaml missing a required field (error logged,
  no Workshop row created, sync keeps going).
- Parser rejects a recording.url set without a recording.required_level
  (fails closed per spec).
- Parser rejects a recording.required_level < pages_required_level
  (fails closed per spec).

The parser-level tests operate on a local temp directory via
``sync_content_source(source, repo_dir=...)`` — same pattern used by
existing course sync tests.
"""

import os
import shutil
import tempfile
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone

from django.test import TestCase

from content.models import Workshop, WorkshopPage
from events.models import Event
from integrations.models import ContentSource
from integrations.services.github import sync_content_source

SAMPLE_WORKSHOP_UUID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'


class _WorkshopSyncFixtureBase(TestCase):
    """Helpers to assemble a workshops-content-shaped repo on disk."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
            content_type='workshop',
            content_path='',
            is_private=False,
        )
        self.temp_dir = tempfile.mkdtemp(prefix='workshop-sync-')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, rel_path, text):
        full = os.path.join(self.temp_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(text)
        return full

    def _write_workshop_yaml(
        self, folder='2026/2026-04-21-demo', *, content_id=SAMPLE_WORKSHOP_UUID,
        slug='demo', title='Demo Workshop',
        pages_required_level=10, landing_required_level=None, extra_yaml='',
    ):
        body = (
            f'content_id: {content_id}\n'
            f'slug: {slug}\n'
            f'title: "{title}"\n'
            f'date: 2026-04-21\n'
            f'pages_required_level: {pages_required_level}\n'
        )
        if landing_required_level is not None:
            body += f'landing_required_level: {landing_required_level}\n'
        body += 'instructor_name: "Alexey Grigorev"\n'
        body += extra_yaml
        self._write(f'{folder}/workshop.yaml', body)

    def _write_page(self, folder, filename, *, title, body='Page body.\n',
                    extra_frontmatter=''):
        text = (
            '---\n'
            f'title: "{title}"\n'
        ) + extra_frontmatter + '---\n' + body
        self._write(f'{folder}/{filename}', text)


class WorkshopSyncHappyPathTest(_WorkshopSyncFixtureBase):
    """End-to-end: one valid workshop folder -> Workshop + pages + Event."""

    def test_happy_path_creates_workshop_pages_and_event(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            extra_yaml=(
                'recording:\n'
                '  url: https://www.youtube.com/watch?v=h84rcRezNM4\n'
                '  embed_url: https://www.youtube.com/embed/h84rcRezNM4\n'
                '  required_level: 20\n'
                '  timestamps:\n'
                '    - { time: "00:00", title: "Intro" }\n'
                '  materials:\n'
                '    - { title: "Slides", url: "https://example.com/slides", type: "slides" }\n'
            ),
        )
        self._write_page(folder, '01-overview.md', title='Overview',
                         body='# Overview\n\nHello.\n')
        self._write_page(folder, '02-setup.md', title='Setup',
                         body='# Setup\n\nRun this.\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            sync_log.errors, [],
            f'Expected no errors, got: {sync_log.errors}',
        )
        self.assertEqual(Workshop.objects.count(), 1)
        workshop = Workshop.objects.get()
        self.assertEqual(workshop.slug, 'demo')
        self.assertEqual(workshop.title, 'Demo Workshop')
        self.assertEqual(workshop.pages_required_level, 10)
        self.assertEqual(workshop.recording_required_level, 20)
        self.assertEqual(workshop.instructor_name, 'Alexey Grigorev')

        pages = list(WorkshopPage.objects.filter(workshop=workshop).order_by('sort_order'))
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].slug, 'overview')
        self.assertEqual(pages[0].sort_order, 1)
        self.assertEqual(pages[0].title, 'Overview')
        self.assertEqual(pages[1].slug, 'setup')
        self.assertEqual(pages[1].sort_order, 2)

        # Linked event
        self.assertIsNotNone(workshop.event_id)
        event = Event.objects.get(pk=workshop.event_id)
        self.assertEqual(event.slug, 'demo')
        self.assertEqual(event.kind, 'workshop')
        self.assertEqual(event.event_type, 'async')
        self.assertEqual(event.status, 'completed')
        self.assertEqual(
            event.recording_url,
            'https://www.youtube.com/watch?v=h84rcRezNM4',
        )
        self.assertEqual(
            event.recording_embed_url,
            'https://www.youtube.com/embed/h84rcRezNM4',
        )
        self.assertEqual(event.required_level, 20)
        self.assertEqual(event.speaker_name, 'Alexey Grigorev')
        self.assertEqual(len(event.timestamps), 1)
        self.assertEqual(len(event.materials), 1)
        self.assertTrue(event.published)


class WorkshopSyncFlatRootLayoutTest(_WorkshopSyncFixtureBase):
    """Flat layout at repo root — ``YYYY-MM-DD-slug/`` with no ``YYYY/`` wrapper.

    This is the real layout used by the ``workshops-content`` repo. The
    sync walker must recognise any ``^\\d{4}-\\d{2}-\\d{2}-`` dir at the
    repo root as a candidate workshop folder.
    """

    def test_flat_root_folder_syncs_to_workshop_and_event(self):
        folder = '2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            extra_yaml=(
                'recording:\n'
                '  url: https://www.youtube.com/watch?v=h84rcRezNM4\n'
                '  embed_url: https://www.youtube.com/embed/h84rcRezNM4\n'
                '  required_level: 20\n'
                '  timestamps:\n'
                '    - { time: "00:00", title: "Intro" }\n'
            ),
        )
        self._write_page(folder, '01-overview.md', title='Overview',
                         body='Body.\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            sync_log.errors, [],
            f'Expected no errors, got: {sync_log.errors}',
        )
        self.assertEqual(
            Workshop.objects.count(), 1,
            'Flat-root folder must sync into exactly one Workshop.',
        )
        workshop = Workshop.objects.get()
        self.assertEqual(workshop.slug, 'demo')
        self.assertEqual(workshop.source_path, folder)

        self.assertEqual(
            Event.objects.filter(slug='demo').count(), 1,
            'Flat-root folder must produce exactly one linked Event.',
        )
        event = Event.objects.get(slug='demo')
        self.assertEqual(workshop.event_id, event.pk)
        self.assertEqual(event.kind, 'workshop')


class WorkshopSyncIdempotencyTest(_WorkshopSyncFixtureBase):
    """Running sync twice must not create duplicate Workshops / Events."""

    def test_second_sync_creates_no_duplicates(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            extra_yaml=(
                'recording:\n'
                '  url: https://www.youtube.com/watch?v=h84rcRezNM4\n'
                '  required_level: 20\n'
            ),
        )
        self._write_page(folder, '01-overview.md', title='Overview')
        self._write_page(folder, '02-setup.md', title='Setup')

        sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(Workshop.objects.count(), 1)
        self.assertEqual(Event.objects.filter(slug='demo').count(), 1)
        self.assertEqual(WorkshopPage.objects.count(), 2)
        event_pk_before = Event.objects.get(slug='demo').pk
        workshop_pk_before = Workshop.objects.get().pk

        # Second run — everything unchanged.
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.errors, [])
        self.assertEqual(
            Workshop.objects.count(), 1,
            'Second sync must not create a duplicate Workshop.',
        )
        self.assertEqual(
            Event.objects.filter(slug='demo').count(), 1,
            'Second sync must not create a duplicate Event.',
        )
        self.assertEqual(WorkshopPage.objects.count(), 2)
        self.assertEqual(Event.objects.get(slug='demo').pk, event_pk_before)
        self.assertEqual(Workshop.objects.get().pk, workshop_pk_before)


class WorkshopSyncReusesExistingEventTest(_WorkshopSyncFixtureBase):
    """If an Event with the same slug exists, reuse it — don't overwrite ops fields."""

    def test_existing_event_is_linked_not_recreated(self):
        # Pre-create an Event with distinct operational fields.
        existing_start = datetime(2026, 1, 1, 10, 0, tzinfo=dt_timezone.utc)
        existing = Event.objects.create(
            slug='demo',
            title='Stale Title',
            start_datetime=existing_start,
            event_type='live',
            status='upcoming',
            zoom_meeting_id='999-999-999',
            zoom_join_url='https://zoom.us/j/9999',
            published=True,
        )

        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            slug='demo',
            title='Demo Workshop',
            extra_yaml=(
                'recording:\n'
                '  url: https://www.youtube.com/watch?v=h84rcRezNM4\n'
                '  required_level: 20\n'
            ),
        )
        self._write_page(folder, '01-overview.md', title='Overview')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            Event.objects.filter(slug='demo').count(), 1,
            'Should reuse the existing Event — not create a second one.',
        )
        reloaded = Event.objects.get(slug='demo')
        self.assertEqual(reloaded.pk, existing.pk)

        # Content fields DID update
        self.assertEqual(reloaded.title, 'Demo Workshop')
        self.assertEqual(
            reloaded.recording_url,
            'https://www.youtube.com/watch?v=h84rcRezNM4',
        )
        self.assertEqual(reloaded.required_level, 20)

        # Operational fields must NOT have been overwritten
        self.assertEqual(reloaded.start_datetime, existing_start)
        self.assertEqual(reloaded.status, 'upcoming')
        self.assertEqual(reloaded.zoom_meeting_id, '999-999-999')
        self.assertEqual(reloaded.zoom_join_url, 'https://zoom.us/j/9999')

        # Workshop is linked to the existing event
        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.event_id, existing.pk)


class WorkshopSyncSkipsFolderWithoutYamlTest(_WorkshopSyncFixtureBase):
    """A folder without workshop.yaml is code-only and silently skipped."""

    def test_code_only_folder_is_skipped(self):
        # One valid workshop, one code-only folder next to it.
        self._write_workshop_yaml(
            folder='2026/2026-04-21-demo', slug='demo', title='Demo',
        )
        self._write_page('2026/2026-04-21-demo', '01-overview.md', title='Overview')
        # Code-only folder — has .md files but NO workshop.yaml.
        self._write('2026/2026-05-12-code-only/README.md', '# Just code')
        self._write('2026/2026-05-12-code-only/app.py', 'print("hi")\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.errors, [])
        self.assertEqual(Workshop.objects.count(), 1)
        self.assertEqual(Workshop.objects.get().slug, 'demo')


class WorkshopSyncMissingRequiredFieldTest(_WorkshopSyncFixtureBase):
    """Missing required frontmatter -> per-file error, no Workshop row."""

    def test_missing_title_logged_and_no_workshop_created(self):
        folder = '2026/2026-04-21-demo'
        # Omit `title` from the yaml.
        self._write(
            f'{folder}/workshop.yaml',
            'content_id: ' + str(uuid.uuid4()) + '\n'
            'slug: demo\n'
            'date: 2026-04-21\n'
            'pages_required_level: 10\n',
        )
        self._write_page(folder, '01-overview.md', title='Overview')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(Workshop.objects.count(), 0)
        self.assertTrue(
            any('title' in err.get('error', '') for err in sync_log.errors),
            f'Expected a "title"-related error, got: {sync_log.errors}',
        )

    def test_missing_content_id_logged_and_no_workshop_created(self):
        folder = '2026/2026-04-21-demo'
        self._write(
            f'{folder}/workshop.yaml',
            'slug: demo\n'
            'title: Demo\n'
            'date: 2026-04-21\n'
            'pages_required_level: 10\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(Workshop.objects.count(), 0)
        self.assertTrue(
            any('content_id' in err.get('error', '') for err in sync_log.errors),
            f'Expected a "content_id"-related error, got: {sync_log.errors}',
        )

    def test_missing_pages_required_level_logged(self):
        folder = '2026/2026-04-21-demo'
        self._write(
            f'{folder}/workshop.yaml',
            'content_id: ' + str(uuid.uuid4()) + '\n'
            'slug: demo\n'
            'title: Demo\n'
            'date: 2026-04-21\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(Workshop.objects.count(), 0)
        self.assertTrue(
            any('pages_required_level' in err.get('error', '')
                for err in sync_log.errors),
            f'Expected a pages_required_level error, got: {sync_log.errors}',
        )

    def test_other_workshops_still_sync_when_one_fails(self):
        """A bad workshop.yaml must not abort the whole sync."""
        # Bad one
        self._write(
            '2026/2026-04-21-bad/workshop.yaml',
            'slug: bad\npages_required_level: 10\ndate: 2026-04-21\n',  # no content_id, no title
        )
        # Good one
        self._write_workshop_yaml(
            folder='2026/2026-04-22-good', slug='good', title='Good',
        )
        self._write_page('2026/2026-04-22-good', '01-p.md', title='P')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(Workshop.objects.count(), 1)
        self.assertEqual(Workshop.objects.get().slug, 'good')
        # The bad one produced an error entry.
        self.assertGreater(len(sync_log.errors), 0)


class WorkshopSyncRecordingGateValidationTest(_WorkshopSyncFixtureBase):
    """Recording gate must be set and >= pages gate when url is set."""

    def test_missing_recording_required_level_rejects_workshop(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            extra_yaml=(
                'recording:\n'
                '  url: https://www.youtube.com/watch?v=abc\n'
                # required_level deliberately omitted
            ),
        )
        self._write_page(folder, '01-p.md', title='P')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            Workshop.objects.count(), 0,
            'Fails closed: no workshop row when recording gate is missing.',
        )
        self.assertTrue(
            any('required_level' in err.get('error', '') for err in sync_log.errors),
            f'Expected a required_level error, got: {sync_log.errors}',
        )

    def test_recording_gate_below_pages_gate_rejects_workshop(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            pages_required_level=20,  # Main
            extra_yaml=(
                'recording:\n'
                '  url: https://www.youtube.com/watch?v=abc\n'
                '  required_level: 10\n'  # Basic — too low!
            ),
        )
        self._write_page(folder, '01-p.md', title='P')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            Workshop.objects.count(), 0,
            'Fails closed: recording gate must be >= pages gate.',
        )
        self.assertTrue(
            any('required_level' in err.get('error', '') for err in sync_log.errors),
            f'Expected a gate-ordering error, got: {sync_log.errors}',
        )


class WorkshopSyncLandingGateValidationTest(_WorkshopSyncFixtureBase):
    """Landing gate is optional, defaults to 0, must be <= pages gate."""

    def test_landing_required_level_defaults_to_zero_when_omitted(self):
        """workshop.yaml without the key syncs and yields landing=0."""
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(folder=folder)
        self._write_page(folder, '01-p.md', title='P')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            sync_log.errors, [],
            f'Expected no errors, got: {sync_log.errors}',
        )
        self.assertEqual(Workshop.objects.count(), 1)
        self.assertEqual(
            Workshop.objects.get().landing_required_level, 0,
        )

    def test_landing_required_level_explicit_value_persists(self):
        """Explicit landing=10 with pages=20 is valid and persisted."""
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            pages_required_level=20,
            landing_required_level=10,
        )
        self._write_page(folder, '01-p.md', title='P')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            sync_log.errors, [],
            f'Expected no errors, got: {sync_log.errors}',
        )
        self.assertEqual(Workshop.objects.count(), 1)
        workshop = Workshop.objects.get()
        self.assertEqual(workshop.landing_required_level, 10)
        self.assertEqual(workshop.pages_required_level, 20)

    def test_landing_gate_above_pages_gate_rejects_workshop(self):
        """landing=20 with pages=10 fails closed — no Workshop row."""
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            pages_required_level=10,
            landing_required_level=20,
        )
        self._write_page(folder, '01-p.md', title='P')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            Workshop.objects.count(), 0,
            'Fails closed: landing gate must be <= pages gate.',
        )
        self.assertTrue(
            any('landing_required_level' in err.get('error', '')
                for err in sync_log.errors),
            f'Expected a landing_required_level error, got: {sync_log.errors}',
        )

    def test_invalid_landing_required_level_rejects_workshop(self):
        """A landing_required_level not in VISIBILITY_CHOICES is rejected."""
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            pages_required_level=10,
            landing_required_level=7,  # not a valid tier level
        )
        self._write_page(folder, '01-p.md', title='P')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            Workshop.objects.count(), 0,
            'Fails closed: invalid landing_required_level rejects workshop.',
        )
        self.assertTrue(
            any('landing_required_level' in err.get('error', '')
                for err in sync_log.errors),
            f'Expected a landing_required_level error, got: {sync_log.errors}',
        )


class WorkshopSyncStaleCleanupTest(_WorkshopSyncFixtureBase):
    """Workshops whose source folder disappeared are marked draft."""

    def test_stale_workshop_set_to_draft(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(folder=folder, slug='demo')
        self._write_page(folder, '01-p.md', title='P')

        sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(
            Workshop.objects.get(slug='demo').status, 'published',
        )

        # Delete the workshop folder, re-sync.
        shutil.rmtree(os.path.join(self.temp_dir, folder))
        sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            Workshop.objects.get(slug='demo').status, 'draft',
            'Stale workshop should be soft-deleted to draft.',
        )


class WorkshopSyncPageRemovalTest(_WorkshopSyncFixtureBase):
    """Pages whose source file disappeared are hard-deleted."""

    def test_removed_page_is_deleted(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(folder=folder, slug='demo')
        self._write_page(folder, '01-a.md', title='A')
        self._write_page(folder, '02-b.md', title='B')

        sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(WorkshopPage.objects.count(), 2)

        # Remove page 02-b.md, re-sync.
        os.remove(os.path.join(self.temp_dir, folder, '02-b.md'))
        sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(WorkshopPage.objects.count(), 1)
        self.assertEqual(WorkshopPage.objects.get().slug, 'a')


class WorkshopSeedContentSourceTest(TestCase):
    """seed_content_sources registers the workshops-content repo idempotently."""

    def test_seed_creates_workshop_source(self):
        from django.core.management import call_command

        call_command('seed_content_sources')
        qs = ContentSource.objects.filter(
            repo_name='AI-Shipping-Labs/workshops-content',
            content_type='workshop',
        )
        self.assertEqual(qs.count(), 1)
        source = qs.get()
        self.assertEqual(source.content_path, '')
        self.assertFalse(source.is_private)

    def test_seed_is_idempotent(self):
        from django.core.management import call_command

        call_command('seed_content_sources')
        call_command('seed_content_sources')

        self.assertEqual(
            ContentSource.objects.filter(
                repo_name='AI-Shipping-Labs/workshops-content',
                content_type='workshop',
            ).count(),
            1,
        )
