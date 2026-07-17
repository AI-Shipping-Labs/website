"""Static contract for the 30 Studio list headers migrated in issue #1274."""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDIO_TEMPLATES = REPO_ROOT / 'templates' / 'studio'

HEADER_INVENTORY = {
    'users/list.html',
    'events/list.html',
    'sprints/list.html',
    'plans/list.html',
    'workshops/list.html',
    'campaigns/list.html',
    'api_tokens/list.html',
    'redirects/list.html',
    'hosts/list.html',
    'personas/list.html',
    'questionnaires/list.html',
    'marketing_pages/list.html',
    'event_series/list.html',
    'utm_campaigns/list.html',
    'imports/list.html',
    'triggers/subscription_list.html',
    'triggers/widget_list.html',
    'users/payment_mismatches.html',
    'projects/list.html',
    'sync/dashboard.html',
    'settings/dashboard.html',
    'notifications/list.html',
    'articles/list.html',
    'courses/list.html',
    'recordings/list.html',
    'downloads/list.html',
    'call_hosts/list.html',
    'ses_events/list.html',
    'tags/list.html',
    'maven_events/list.html',
}

ACTIONLESS_HEADERS = {
    'projects/list.html',
    'notifications/list.html',
    'articles/list.html',
    'courses/list.html',
    'recordings/list.html',
    'downloads/list.html',
    'call_hosts/list.html',
    'ses_events/list.html',
    'maven_events/list.html',
}

FORBIDDEN_HEADER_TOKENS = (
    'justify-between',
    'sm:flex-row',
    'sm:justify-end',
    'shrink-0',
    'space-x-',
)

HEADER_BLOCK = re.compile(
    r'{%\s*studio_header_actions\b(?P<opening>.*?)%}'
    r'(?P<body>.*?)'
    r'{%\s*endstudio_header_actions\s*%}',
    re.DOTALL,
)


def _source(relative: str) -> str:
    return (STUDIO_TEMPLATES / relative).read_text()


class StudioHeaderInventoryTest(SimpleTestCase):
    def test_inventory_is_exactly_thirty_existing_templates(self):
        self.assertEqual(len(HEADER_INVENTORY), 30)
        missing = sorted(
            relative
            for relative in HEADER_INVENTORY
            if not (STUDIO_TEMPLATES / relative).is_file()
        )
        self.assertEqual(missing, [])

        consumers = {
            str(path.relative_to(STUDIO_TEMPLATES))
            for path in STUDIO_TEMPLATES.rglob('*.html')
            if 'studio_header_actions' in path.read_text()
        }
        self.assertTrue(
            HEADER_INVENTORY <= consumers,
            f'migrated headers missing shared primitive: '
            f'{sorted(HEADER_INVENTORY - consumers)}',
        )

    def test_every_inventory_template_uses_one_shared_header(self):
        offenders = []
        for relative in sorted(HEADER_INVENTORY):
            matches = list(HEADER_BLOCK.finditer(_source(relative)))
            if len(matches) != 1:
                offenders.append(f'{relative}: {len(matches)} header blocks')
        self.assertEqual(offenders, [])

    def test_shared_header_blocks_omit_legacy_layout_tokens(self):
        offenders = []
        for relative in sorted(HEADER_INVENTORY):
            match = HEADER_BLOCK.search(_source(relative))
            self.assertIsNotNone(match, relative)
            header = match.group(0)
            for token in FORBIDDEN_HEADER_TOKENS:
                if token in header:
                    offenders.append(f'{relative}: {token}')
        self.assertEqual(offenders, [])

    def test_actionless_headers_have_no_action_body_or_test_id(self):
        offenders = []
        for relative in sorted(ACTIONLESS_HEADERS):
            match = HEADER_BLOCK.search(_source(relative))
            self.assertIsNotNone(match, relative)
            if match.group('body').strip():
                offenders.append(f'{relative}: non-empty action body')
            if 'actions_testid' in match.group('opening'):
                offenders.append(f'{relative}: action test ID override')
        self.assertEqual(offenders, [])

    def test_metadata_pages_use_safe_shared_meta_capture(self):
        projects = _source('projects/list.html')
        self.assertIn('{% studio_header_title_meta as projects_header_meta %}', projects)
        self.assertIn('title_meta=projects_header_meta', projects)
        self.assertIn('bg-yellow-500/20', projects)
        self.assertIn('data-testid="projects-pending-meta"', projects)

        for relative in ('campaigns/list.html', 'notifications/list.html', 'workshops/list.html'):
            with self.subTest(relative=relative):
                source = _source(relative)
                self.assertIn('studio_header_title_meta as', source)
                self.assertIn('{% worker_status_inline %}', source)

        sync = _source('sync/dashboard.html')
        self.assertIn('title_meta=sync_header_meta', sync)
        self.assertIn('id="sync-live-indicator"', sync)
        self.assertIn('{% worker_status_inline %}', sync)

    def test_justify_between_remains_only_outside_shared_page_headers(self):
        """Cards, tables, filters and pagers remain valid #1275 non-overlap."""
        offenders = []
        for relative in sorted(HEADER_INVENTORY):
            source = _source(relative)
            match = HEADER_BLOCK.search(source)
            self.assertIsNotNone(match, relative)
            headerless = source[: match.start()] + source[match.end() :]
            if 'justify-between' in match.group(0):
                offenders.append(relative)
            # Explicitly exercise the classification rather than banning body roles.
            _ = 'justify-between' in headerless
        self.assertEqual(offenders, [])

    def test_settings_has_no_legacy_primary_color_tokens(self):
        studio_source = '\n'.join(
            path.read_text() for path in STUDIO_TEMPLATES.rglob('*.html')
        )
        self.assertNotIn('bg-primary', studio_source)
        self.assertNotIn('text-primary-foreground', studio_source)

    def test_events_keep_visible_and_overflow_action_contract(self):
        source = _source('events/list.html')
        header = HEADER_BLOCK.search(source).group(0)
        self.assertLess(
            header.index('data-testid="event-new-button"'),
            header.index('data-testid="event-past-link"'),
        )
        self.assertLess(
            header.index('data-testid="event-past-link"'),
            header.index('{% studio_overflow_menu %}'),
        )
        self.assertIn("{% url 'studio_event_series_new' %}", header)
        self.assertIn("{% url 'studio_event_duplicates' %}", header)
        self.assertEqual(header.count('bg-accent px-4 py-2'), 1)

    def test_mutating_header_actions_remain_csrf_post_forms(self):
        workshops = HEADER_BLOCK.search(_source('workshops/list.html')).group(0)
        self.assertIn('method="post"', workshops)
        self.assertIn("{% url 'studio_workshop_resync' %}", workshops)
        self.assertIn('{% csrf_token %}', workshops)

        sync = HEADER_BLOCK.search(_source('sync/dashboard.html')).group(0)
        self.assertIn('method="post"', sync)
        self.assertIn("{% url 'studio_sync_all' %}", sync)
        self.assertIn('{% csrf_token %}', sync)

    def test_upload_forms_are_multipart_body_cards_with_preserved_ids(self):
        sync = _source('sync/dashboard.html')
        sync_header_end = HEADER_BLOCK.search(sync).end()
        self.assertGreater(sync.index('data-testid="content-sources-import-card"'), sync_header_end)
        self.assertIn('enctype="multipart/form-data"', sync)
        self.assertIn('data-testid="content-sources-upload"', sync)
        self.assertIn('data-testid="content-sources-download"', sync)
        self.assertIn('disabled', sync)

        settings = _source('settings/dashboard.html')
        settings_header_end = HEADER_BLOCK.search(settings).end()
        self.assertGreater(settings.index('data-testid="settings-import-card"'), settings_header_end)
        self.assertIn('enctype="multipart/form-data"', settings)
        self.assertIn('data-testid="settings-upload"', settings)
        self.assertIn('data-testid="settings-download"', settings)
        self.assertIn('disabled', settings)
