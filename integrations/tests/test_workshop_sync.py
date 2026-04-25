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
        self.assertTrue(source.is_private)

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


class WorkshopPageVideoStartSyncTest(_WorkshopSyncFixtureBase):
    """Sync parses ``video_start`` from page frontmatter (issue #302).

    Valid timestamps are stored verbatim. Invalid timestamps log to
    ``stats['errors']`` and the field is stored empty (so the watch bar
    is not shown for that page until the author fixes the value).
    """

    def test_valid_video_start_persisted(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(folder=folder)
        self._write_page(
            folder, '01-page.md', title='P',
            extra_frontmatter='video_start: "16:00"\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.errors, [])
        page = WorkshopPage.objects.get(slug='page')
        self.assertEqual(page.video_start, '16:00')

    def test_invalid_video_start_logged_and_stored_empty(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(folder=folder)
        self._write_page(
            folder, '01-page.md', title='P',
            extra_frontmatter='video_start: "not-a-time"\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        page = WorkshopPage.objects.get(slug='page')
        # Field stored empty so the watch bar is hidden until fixed.
        self.assertEqual(page.video_start, '')
        # Error is logged with the offending value and the file path.
        self.assertTrue(
            any(
                'video_start' in err.get('error', '')
                and 'not-a-time' in err.get('error', '')
                for err in sync_log.errors
            ),
            f'Expected a video_start error, got: {sync_log.errors}',
        )

    def test_missing_video_start_is_not_an_error(self):
        # Pages without the key sync cleanly with video_start=''.
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(folder=folder)
        self._write_page(folder, '01-page.md', title='P')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.errors, [])
        page = WorkshopPage.objects.get(slug='page')
        self.assertEqual(page.video_start, '')

    def test_resync_with_unchanged_video_start_is_idempotent(self):
        # Acceptance criterion: re-syncing a workshop with the same
        # content does not bump items_updated.
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(folder=folder)
        self._write_page(
            folder, '01-page.md', title='P',
            extra_frontmatter='video_start: "16:00"\n',
        )

        first = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(first.errors, [])
        self.assertGreaterEqual(first.items_created, 1)

        second = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(second.errors, [])
        # Second sync is a no-op — no updates and no creates.
        self.assertEqual(second.items_created, 0)
        self.assertEqual(second.items_updated, 0)


class WorkshopSyncMdLinkRewriteTest(_WorkshopSyncFixtureBase):
    """Integration: cross-page ``.md`` links are rewritten at sync time (issue #301).

    The rewriter is the only thing standing between authors writing
    ``[10-qa.md](10-qa.md)`` and the rendered page producing a 404 link.
    Validates that after sync:
    - The stored markdown body has the rewritten URL.
    - The rendered ``body_html`` has the rewritten URL and substituted title.
    - Custom labels are preserved verbatim; only the URL is swapped.
    - Anchor fragments survive.
    - Broken sibling links surface as ``SyncLog.errors`` entries naming the
      missing filename and the workshop slug.
    """

    def test_cross_page_links_resolve_to_platform_urls(self):
        folder = '2026-04-21-end-to-end-agent-deployment'
        self._write_workshop_yaml(
            folder=folder,
            slug='end-to-end-agent-deployment',
            title='End-to-end agent deployment',
        )
        # Three pages cross-linking each other so we cover forward,
        # backward, and self-named-text references.
        self._write_page(
            folder, '01-overview.md', title='Welcome and overview',
            body=(
                'The agentic-RAG explanation is in '
                '[02-starting-notebook.md](02-starting-notebook.md).\n'
                'See [10-qa.md](10-qa.md) for why and how.\n'
                'For tmux details see [the Q&A page](10-qa.md#tmux).\n'
            ),
        )
        self._write_page(
            folder, '02-starting-notebook.md',
            title='Part 1: The starting notebook',
            body='Background lives in [01-overview.md](01-overview.md).\n',
        )
        self._write_page(
            folder, '10-qa.md', title='Q&A: side discussions',
            body='Back to the [start](01-overview.md).\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(
            sync_log.errors, [],
            f'Expected no errors, got: {sync_log.errors}',
        )

        workshop = Workshop.objects.get(slug='end-to-end-agent-deployment')
        overview = WorkshopPage.objects.get(workshop=workshop, slug='overview')

        # Stored markdown has the rewritten links — the bare filename is
        # gone and replaced with the title.
        self.assertIn(
            '[Part 1: The starting notebook]'
            '(/workshops/end-to-end-agent-deployment/tutorial/starting-notebook)',
            overview.body,
        )
        self.assertIn(
            '[Q&A: side discussions]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa)',
            overview.body,
        )
        self.assertIn(
            '[the Q&A page]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa#tmux)',
            overview.body,
        )
        self.assertNotIn('](10-qa.md)', overview.body)
        self.assertNotIn('](02-starting-notebook.md)', overview.body)

        # Rendered HTML carries the right hrefs.
        self.assertIn(
            'href="/workshops/end-to-end-agent-deployment/tutorial/qa"',
            overview.body_html,
        )
        self.assertIn(
            'href="/workshops/end-to-end-agent-deployment/tutorial/qa#tmux"',
            overview.body_html,
        )
        self.assertIn(
            'href="/workshops/end-to-end-agent-deployment/tutorial/starting-notebook"',
            overview.body_html,
        )
        # And no leftover bare-filename hrefs.
        self.assertNotIn('href="10-qa.md"', overview.body_html)
        self.assertNotIn('href="02-starting-notebook.md"', overview.body_html)

        # Backward reference (page 02 -> page 01) resolves too.
        starting = WorkshopPage.objects.get(
            workshop=workshop, slug='starting-notebook',
        )
        self.assertIn(
            'href="/workshops/end-to-end-agent-deployment/tutorial/overview"',
            starting.body_html,
        )

    def test_broken_cross_page_link_surfaces_in_sync_log(self):
        folder = '2026-04-21-end-to-end-agent-deployment'
        self._write_workshop_yaml(
            folder=folder,
            slug='end-to-end-agent-deployment',
            title='End-to-end agent deployment',
        )
        # 99-deleted.md does not exist on disk.
        self._write_page(
            folder, '01-overview.md', title='Welcome and overview',
            body='Old: [gone](99-deleted.md).\n',
        )
        self._write_page(
            folder, '10-qa.md', title='Q&A: side discussions',
            body='Body.\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # The page itself still synced — the rewriter never aborts the page.
        workshop = Workshop.objects.get(slug='end-to-end-agent-deployment')
        overview = WorkshopPage.objects.get(workshop=workshop, slug='overview')
        # Broken link is left intact (visible to the author in the rendered
        # page) so they can spot it.
        self.assertIn('[gone](99-deleted.md)', overview.body)
        self.assertIn('href="99-deleted.md"', overview.body_html)

        # And the SyncLog has a warning naming the missing file and workshop.
        broken_link_errors = [
            e for e in sync_log.errors
            if '99-deleted.md' in e.get('error', '')
        ]
        self.assertEqual(len(broken_link_errors), 1)
        msg = broken_link_errors[0]['error']
        self.assertIn('99-deleted.md', msg)
        self.assertIn('end-to-end-agent-deployment', msg)
        # The error is attributed to the source page that contained the link.
        self.assertIn('01-overview.md', broken_link_errors[0]['file'])


class WorkshopSyncCopyFileTest(_WorkshopSyncFixtureBase):
    """Workshop landing description sourced from copy_file / README (issue #304).

    Resolution priority:
    1. ``copy_file: <name>`` set in workshop.yaml
    2. Implicit ``README.md`` at the workshop folder root
    3. Yaml ``description:``
    4. Empty string (no error)

    File source wins over yaml ``description:``; processing strips
    frontmatter, leading H1, rewrites image URLs and intra-workshop
    .md links. ``Workshop.save()`` then renders ``description_html``
    once via ``render_markdown``.
    """

    def _write_yaml_no_description(self, folder, slug='demo', extra=''):
        """Write a minimal workshop.yaml WITHOUT a description: field."""
        self._write_workshop_yaml(
            folder=folder, slug=slug, extra_yaml=extra,
        )

    def test_implicit_readme_becomes_description(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(folder)
        # README starts with an H1 (GitHub-friendly) and contains a paragraph
        # plus a fenced code block. The H1 should be stripped by sync;
        # paragraphs and code block are preserved.
        self._write(
            f'{folder}/README.md',
            '# Demo Workshop\n\n'
            'Welcome to the demo workshop.\n\n'
            'A second paragraph here.\n\n'
            '```python\nprint("hi")\n```\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(
            [e for e in sync_log.errors if e.get('severity') != 'info'],
            [],
            f'Expected no errors, got: {sync_log.errors}',
        )

        workshop = Workshop.objects.get(slug='demo')
        # Markdown body excludes the leading H1 (we don't want to duplicate
        # the workshop title rendered above the description).
        self.assertNotIn('# Demo Workshop', workshop.description)
        self.assertIn('Welcome to the demo workshop.', workshop.description)
        self.assertIn('A second paragraph here.', workshop.description)
        self.assertIn('```python', workshop.description)

        # description_html was rendered through render_markdown — codehilite
        # class on the fenced code block confirms the pipeline ran.
        self.assertIn('codehilite', workshop.description_html)
        self.assertIn('Welcome to the demo workshop.', workshop.description_html)
        # The leading H1 must NOT be re-introduced in the rendered HTML.
        self.assertNotIn('<h1>Demo Workshop</h1>', workshop.description_html)

    def test_explicit_copy_file_wins(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(
            folder, extra='copy_file: 01-intro.md\n',
        )
        # README also exists but copy_file overrides it.
        self._write(f'{folder}/README.md', '# README body\n\nReadme content.\n')
        self._write_page(
            folder, '01-intro.md', title='Intro',
            body='# Intro heading\n\nIntro body content.\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(
            [e for e in sync_log.errors if e.get('severity') != 'info'],
            [],
        )

        workshop = Workshop.objects.get(slug='demo')
        self.assertIn('Intro body content.', workshop.description)
        self.assertNotIn('Readme content.', workshop.description)
        # Leading H1 from the source file should still be stripped.
        self.assertNotIn('# Intro heading', workshop.description)

        # 01-intro.md is also still synced as a tutorial page.
        page = WorkshopPage.objects.get(workshop=workshop, slug='intro')
        self.assertEqual(page.title, 'Intro')

    def test_yaml_description_used_when_no_file_source(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            extra_yaml='description: "Plain yaml description."\n',
        )
        # No README, no copy_file -> falls through to yaml description.

        sync_content_source(self.source, repo_dir=self.temp_dir)

        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.description, 'Plain yaml description.')
        self.assertIn('Plain yaml description.', workshop.description_html)

    def test_no_description_no_readme_no_copy_file_yields_empty(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(folder)
        # Need at least one tutorial page to make the sync produce a
        # workshop row (workshop sync requires no pages, but include one
        # to mirror real layouts).
        self._write_page(folder, '01-only.md', title='Only')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        # No errors at all when nothing is configured.
        self.assertEqual(sync_log.errors, [])

        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.description, '')
        self.assertEqual(workshop.description_html, '')

    def test_file_wins_over_yaml_description_with_info_log(self):
        folder = '2026/2026-04-21-demo'
        self._write_workshop_yaml(
            folder=folder,
            extra_yaml='description: "Stale yaml description."\n',
        )
        self._write(
            f'{folder}/README.md',
            'README body wins.\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        workshop = Workshop.objects.get(slug='demo')
        self.assertIn('README body wins.', workshop.description)
        self.assertNotIn('Stale yaml description.', workshop.description)

        # An info-level note flags the shadowed yaml description.
        info_entries = [
            e for e in sync_log.errors
            if e.get('severity') == 'info'
            and 'shadowed' in e.get('error', '')
        ]
        self.assertEqual(
            len(info_entries), 1,
            f'Expected exactly one shadowing info note, got: {sync_log.errors}',
        )

    def test_copy_file_missing_logs_error_workshop_still_synced(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(
            folder, extra='copy_file: missing.md\n',
        )
        # Add a page so the workshop is non-trivial.
        self._write_page(folder, '01-page.md', title='Page')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # The workshop row IS still created; copy_file errors do NOT skip.
        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.description, '')
        self.assertEqual(workshop.description_html, '')
        # Page sync still ran.
        self.assertTrue(
            WorkshopPage.objects.filter(workshop=workshop, slug='page').exists(),
        )

        not_found = [
            e for e in sync_log.errors
            if 'missing.md' in e.get('error', '')
            and 'not found' in e.get('error', '')
        ]
        self.assertEqual(len(not_found), 1, sync_log.errors)

    def test_copy_file_non_md_logs_error(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(
            folder, extra='copy_file: notes.txt\n',
        )
        self._write(f'{folder}/notes.txt', 'plain text body')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.description, '')
        self.assertTrue(
            any(
                'notes.txt' in e.get('error', '')
                and '.md' in e.get('error', '')
                for e in sync_log.errors
            ),
            sync_log.errors,
        )

    def test_copy_file_path_traversal_logs_error(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(
            folder, extra='copy_file: ../other/README.md\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.description, '')
        self.assertTrue(
            any(
                'must be a filename' in e.get('error', '')
                for e in sync_log.errors
            ),
            sync_log.errors,
        )

    def test_copy_file_subdir_logs_error(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(
            folder, extra='copy_file: subdir/foo.md\n',
        )
        self._write(f'{folder}/subdir/foo.md', 'body')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.description, '')
        self.assertTrue(
            any(
                'must be a filename' in e.get('error', '')
                for e in sync_log.errors
            ),
            sync_log.errors,
        )

    def test_copy_file_empty_file_no_error_empty_description(self):
        # Use README.md for the empty-file case so it doesn't also get
        # picked up by the tutorial-page sync (page sync ignores README.md
        # by name; arbitrary blank .md files would fail title validation).
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(folder)
        self._write(f'{folder}/README.md', '')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.description, '')
        # No error when the file exists but is blank — the spec is explicit.
        self.assertEqual(
            [
                e for e in sync_log.errors
                if 'README.md' in e.get('error', '')
                and e.get('severity') != 'info'
            ],
            [],
            sync_log.errors,
        )

    def test_copy_file_only_frontmatter_no_error_empty_description(self):
        # Use the README.md path for the only-frontmatter case so the
        # workshop-page sync doesn't ALSO try to ingest the file (workshop
        # pages skip README.md by filename, so we don't get crossover noise).
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(folder)
        self._write(
            f'{folder}/README.md',
            '---\ntitle: Stub\n---\n',
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        workshop = Workshop.objects.get(slug='demo')
        self.assertEqual(workshop.description, '')
        self.assertEqual(
            [
                e for e in sync_log.errors
                if 'README.md' in e.get('error', '')
                and e.get('severity') != 'info'
            ],
            [],
            sync_log.errors,
        )

    def test_copy_file_error_isolated_other_workshops_sync(self):
        # Workshop A has a broken copy_file; workshop B is fine.
        self._write_workshop_yaml(
            folder='2026/2026-04-21-broken',
            content_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1',
            slug='broken',
            extra_yaml='copy_file: nope.md\n',
        )
        self._write_page(
            '2026/2026-04-21-broken', '01-x.md', title='X',
        )
        self._write_workshop_yaml(
            folder='2026/2026-04-21-good',
            content_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2',
            slug='good',
        )
        self._write(
            '2026/2026-04-21-good/README.md',
            'Good workshop description.\n',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Both workshops created.
        broken = Workshop.objects.get(slug='broken')
        good = Workshop.objects.get(slug='good')
        self.assertEqual(broken.description, '')
        self.assertIn('Good workshop description.', good.description)

    def test_readme_link_resolves_to_workshop_landing(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(folder, slug='ws')
        self._write(
            f'{folder}/README.md',
            'README content.\n',
        )
        self._write_page(
            folder, '10-qa.md', title='Q&A',
            body=(
                'See [README.md](README.md) and '
                '[the overview](README.md#getting-started) and '
                '[case-insensitive](readme.MD).\n'
            ),
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # No "Unresolvable .md link" warning naming README.md.
        readme_link_errors = [
            e for e in sync_log.errors
            if 'README.md' in e.get('error', '')
            and 'Unresolvable' in e.get('error', '')
        ]
        self.assertEqual(
            readme_link_errors, [],
            f'Expected no README.md unresolvable warnings, got: '
            f'{readme_link_errors}',
        )

        page = WorkshopPage.objects.get(slug='qa')
        # Bare-filename label gets title-substituted to the workshop title.
        self.assertIn('[Demo Workshop](/workshops/ws)', page.body)
        # Custom label preserved with anchor.
        self.assertIn(
            '[the overview](/workshops/ws#getting-started)',
            page.body,
        )
        # Case-insensitive match still resolves.
        self.assertIn('](/workshops/ws)', page.body)

    def test_copy_file_links_to_landing_via_virtual_lookup(self):
        # When copy_file points at 01-intro.md, that filename also routes
        # to the landing for cross-page links (the file IS still synced as
        # a tutorial page, but a `[01-intro.md](01-intro.md)` reference
        # is treated as "the canonical landing source").
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(
            folder, slug='ws', extra='copy_file: 01-intro.md\n',
        )
        self._write_page(
            folder, '01-intro.md', title='Intro',
            body='Intro body.\n',
        )
        self._write_page(
            folder, '02-next.md', title='Next',
            body='Read [01-intro.md](01-intro.md).\n',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)

        next_page = WorkshopPage.objects.get(slug='next')
        # 01-intro.md routes to the workshop landing, not the tutorial URL,
        # and the visible label is title-substituted to the workshop title.
        self.assertIn('[Demo Workshop](/workshops/ws)', next_page.body)
        self.assertNotIn(
            '/workshops/ws/tutorial/intro', next_page.body,
        )

    def test_readme_image_url_rewritten_on_landing(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(folder, slug='ws')
        self._write(
            f'{folder}/README.md',
            'See ![arch](images/architecture.png)\n',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)

        workshop = Workshop.objects.get(slug='ws')
        # The image path is rewritten to a CDN-prefixed URL — bare path
        # is gone.
        self.assertNotIn('](images/architecture.png)', workshop.description)
        self.assertIn('architecture.png', workshop.description)
        # The rewrite uses the configured CDN base; check the prefix.
        from integrations.config import get_config
        cdn_base = get_config('CONTENT_CDN_BASE', '/static/content-images')
        self.assertIn(cdn_base, workshop.description)

    def test_resync_with_unchanged_readme_is_idempotent(self):
        folder = '2026/2026-04-21-demo'
        self._write_yaml_no_description(folder, slug='ws')
        self._write(f'{folder}/README.md', '# Title\n\nBody.\n')
        self._write_page(folder, '01-only.md', title='Only')

        first = sync_content_source(self.source, repo_dir=self.temp_dir)
        # Filter out info-level shadowing notes when counting errors.
        self.assertEqual(
            [e for e in first.errors if e.get('severity') != 'info'], [],
        )
        self.assertGreaterEqual(first.items_created, 1)

        second = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(
            [e for e in second.errors if e.get('severity') != 'info'], [],
        )
        # No work to do on the second sync — description stays the same.
        self.assertEqual(second.items_created, 0)
        self.assertEqual(second.items_updated, 0)
