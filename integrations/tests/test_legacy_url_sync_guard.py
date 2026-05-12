"""Sync-time legacy-URL guard (issue #595).

Asserts that when a synced markdown file leaves a legacy
``/event-recordings/...`` link in its body, the dispatcher records a
warning on ``SyncLog.errors`` (with the source path AND the offending
URL), but the article/project/event is still created or updated
normally — the guard must NOT block sync.

Covers the four content types that carry rendered markdown bodies:

- :class:`content.models.Article`
- :class:`content.models.Project`
- :class:`events.models.Event` (synced from YAML or md, description body)
- :class:`content.models.WorkshopPage` (synced inside a workshop folder)

A clean-body baseline (no warnings emitted) is included for each so the
test fails closed if the helper starts firing on every page.
"""


from django.test import TestCase

from content.models import Article, Project, Workshop, WorkshopPage
from events.models import Event
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

_LEGACY_LINK_BODY = (
    'See the [workshop recording](/event-recordings/foo) for more.\n'
)
_CLEAN_LINK_BODY = (
    'See the [workshop recording](/events/foo) for more.\n'
)


def _legacy_warnings_for(sync_log, source_path):
    """Return error records that mention ``source_path`` and `/event-recordings`."""
    return [
        record for record in (sync_log.errors or [])
        if record.get('file') == source_path
        and '/event-recordings/' in (record.get('error') or '')
    ]


class ArticleLegacyUrlGuardTest(TestCase):
    """Articles: legacy URL surfaces as a warning, sync still succeeds."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/blog',
            prefix='legacy-article-sync-',
        )

    def test_clean_article_body_emits_no_warning(self):
        self.repo.write_markdown(
            'clean.md',
            {
                'title': 'Clean',
                'slug': 'clean-article',
                'date': '2026-01-15',
            },
            _CLEAN_LINK_BODY,
        )
        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'))
        self.assertEqual(_legacy_warnings_for(sync_log, 'clean.md'), [])
        # And the article is published with the clean URL preserved.
        article = Article.objects.get(slug='clean-article')
        self.assertIn('href="/events/foo"', article.content_html)

    def test_article_with_legacy_url_emits_warning_and_still_syncs(self):
        self.repo.write_markdown(
            'oai.md',
            {
                'title': 'OpenAI Skills',
                'slug': 'openai-skills',
                'date': '2026-01-15',
            },
            _LEGACY_LINK_BODY,
        )
        sync_log = sync_repo(self.source, self.repo)
        # Sync did not blow up.
        self.assertIn(sync_log.status, ('success', 'partial'))
        # The article was created (the warning must not block writes).
        article = Article.objects.get(slug='openai-skills')
        self.assertTrue(article.published)
        # Body still contains the legacy href verbatim — no auto-rewrite.
        self.assertIn(
            'href="/event-recordings/foo"', article.content_html,
        )
        # And exactly one legacy-URL warning was recorded for this file.
        warnings = _legacy_warnings_for(sync_log, 'oai.md')
        self.assertEqual(len(warnings), 1, f'got {sync_log.errors!r}')
        message = warnings[0]['error']
        self.assertIn('oai.md', message)
        self.assertIn('/event-recordings/foo', message)
        # Replacement hint is included so authors know what to do.
        self.assertIn('/events/foo', message)


class ProjectLegacyUrlGuardTest(TestCase):
    """Projects use the same dispatcher pattern as articles."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/projects',
            prefix='legacy-project-sync-',
        )

    def test_project_with_legacy_url_emits_warning_and_still_syncs(self):
        self.repo.write_markdown(
            'demo.md',
            {
                'title': 'Demo Project',
                'slug': 'demo-project',
                'description': 'A demo',
                'author': 'Alice',
                'difficulty': 'beginner',
                'date': '2026-01-15',
            },
            _LEGACY_LINK_BODY,
        )
        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'))
        project = Project.objects.get(slug='demo-project')
        self.assertTrue(project.published)
        self.assertIn(
            'href="/event-recordings/foo"', project.content_html,
        )
        warnings = _legacy_warnings_for(sync_log, 'demo.md')
        self.assertEqual(len(warnings), 1, f'got {sync_log.errors!r}')


class EventDescriptionLegacyUrlGuardTest(TestCase):
    """Events synced from markdown carry a description body — scan it."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/events',
            prefix='legacy-event-sync-',
        )

    def test_event_description_with_legacy_url_emits_warning_and_still_syncs(self):
        # Markdown-shaped event file under events/: body becomes the
        # description, the rendered description_html is what the guard
        # scans. The events/ subtree is what the classifier routes to
        # the events dispatcher (md or yaml, with or without
        # start_datetime).
        self.repo.write_markdown(
            'events/coding-agent-skills.md',
            {
                'title': 'Coding Agent Skills',
                'slug': 'coding-agent-skills',
                'start_datetime': '2026-01-15T18:00:00Z',
                'status': 'completed',
                'platform': 'youtube',
                'kind': 'standard',
            },
            _LEGACY_LINK_BODY,
        )
        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'))
        event = Event.objects.get(slug='coding-agent-skills')
        self.assertTrue(event.published)
        self.assertIn(
            'href="/event-recordings/foo"', event.description_html,
        )
        rel_path = 'events/coding-agent-skills.md'
        warnings = _legacy_warnings_for(sync_log, rel_path)
        self.assertEqual(len(warnings), 1, f'got {sync_log.errors!r}')


class WorkshopPageLegacyUrlGuardTest(TestCase):
    """Workshop pages render markdown body to body_html — scan it."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/workshops',
            prefix='legacy-workshop-sync-',
        )

    def _build_workshop(self, page_body):
        # Minimal but valid workshop folder: workshop.yaml + one page.
        # README.md is required as the landing-description source.
        self.repo.write_yaml(
            '2026-01-15-coding-agents/workshop.yaml',
            {
                'title': 'Coding Agents',
                'slug': 'coding-agents',
                'date': '2026-01-15',
                'pages_required_level': 0,
                'instructors': [],
            },
            ensure_content_id=True,
        )
        self.repo.write_text(
            '2026-01-15-coding-agents/README.md',
            'Workshop landing copy.\n',
        )
        self.repo.write_markdown(
            '2026-01-15-coding-agents/01-overview.md',
            {'title': 'Overview'},
            page_body,
        )

    def test_workshop_page_with_legacy_url_emits_warning_and_still_syncs(self):
        self._build_workshop(_LEGACY_LINK_BODY)
        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'))
        workshop = Workshop.objects.get(slug='coding-agents')
        page = WorkshopPage.objects.get(workshop=workshop, slug='overview')
        self.assertIn('href="/event-recordings/foo"', page.body_html)
        page_path = '2026-01-15-coding-agents/01-overview.md'
        warnings = _legacy_warnings_for(sync_log, page_path)
        self.assertEqual(len(warnings), 1, f'got {sync_log.errors!r}')
