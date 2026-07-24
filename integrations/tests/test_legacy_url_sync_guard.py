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


# --- Issue #1342: content-repo-relative-link guard -------------------------

# The href shape from the reported bug: climbs out of the workshop folder
# into a sibling date-slug directory. Meaningless as a site route.
_RELATIVE_LINK_BODY = (
    'See [selecting a portfolio project]'
    '(../../06/2026-06-29-selecting-a-portfolio-project/) for context.\n'
)
_CANONICAL_LINK_BODY = (
    'See [selecting a portfolio project]'
    '(/workshops/selecting-a-portfolio-project) for context.\n'
)


def _relative_warnings_for(sync_log, source_path):
    """Return error records that mention ``source_path`` and a relative href."""
    return [
        record for record in (sync_log.errors or [])
        if record.get('file') == source_path
        and 'Relative content-repo link' in (record.get('error') or '')
    ]


class WorkshopDescriptionRelativeLinkGuardTest(TestCase):
    """Workshop README (description_html): relative link -> one warning."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/workshops',
            prefix='relative-workshop-sync-',
        )

    def _build_workshop(self, readme_body):
        self.repo.write_yaml(
            '2026-07-08-tailor-cv/workshop.yaml',
            {
                'title': 'Tailor CV',
                'slug': 'tailor-cv',
                'date': '2026-07-08',
                'pages_required_level': 0,
                'instructors': [],
            },
            ensure_content_id=True,
        )
        self.repo.write_text(
            '2026-07-08-tailor-cv/README.md',
            readme_body,
        )
        self.repo.write_markdown(
            '2026-07-08-tailor-cv/01-overview.md',
            {'title': 'Overview'},
            'Overview page copy.\n',
        )

    def test_relative_link_in_readme_emits_warning_and_still_syncs(self):
        self._build_workshop(_RELATIVE_LINK_BODY)
        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'))
        workshop = Workshop.objects.get(slug='tailor-cv')
        # No auto-rewrite — the relative href survives verbatim.
        self.assertIn(
            'href="../../06/2026-06-29-selecting-a-portfolio-project/"',
            workshop.description_html,
        )
        # The workshop landing description is attributed to the workshop
        # folder path (matching the existing detect_legacy_urls call site).
        wk_path = '2026-07-08-tailor-cv'
        warnings = _relative_warnings_for(sync_log, wk_path)
        self.assertEqual(len(warnings), 1, f'got {sync_log.errors!r}')
        message = warnings[0]['error']
        self.assertIn(wk_path, message)
        self.assertIn(
            '../../06/2026-06-29-selecting-a-portfolio-project/', message,
        )

    def test_canonical_link_in_readme_emits_no_warning(self):
        self._build_workshop(_CANONICAL_LINK_BODY)
        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'))
        workshop = Workshop.objects.get(slug='tailor-cv')
        self.assertIn(
            'href="/workshops/selecting-a-portfolio-project"',
            workshop.description_html,
        )
        wk_path = '2026-07-08-tailor-cv'
        self.assertEqual(_relative_warnings_for(sync_log, wk_path), [])


class WorkshopPageRelativeLinkGuardTest(TestCase):
    """Workshop page body_html is scanned for relative links too."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/workshops',
            prefix='relative-workshop-page-sync-',
        )

    def _build_workshop(self, page_body):
        self.repo.write_yaml(
            '2026-07-08-tailor-cv/workshop.yaml',
            {
                'title': 'Tailor CV',
                'slug': 'tailor-cv',
                'date': '2026-07-08',
                'pages_required_level': 0,
                'instructors': [],
            },
            ensure_content_id=True,
        )
        self.repo.write_text(
            '2026-07-08-tailor-cv/README.md',
            'Workshop landing copy.\n',
        )
        self.repo.write_markdown(
            '2026-07-08-tailor-cv/05-tailor-to-domain.md',
            {'title': 'Tailor to domain'},
            page_body,
        )

    def test_relative_link_in_page_emits_warning_and_still_syncs(self):
        self._build_workshop(_RELATIVE_LINK_BODY)
        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'))
        workshop = Workshop.objects.get(slug='tailor-cv')
        page = WorkshopPage.objects.get(
            workshop=workshop, slug='tailor-to-domain',
        )
        self.assertIn(
            'href="../../06/2026-06-29-selecting-a-portfolio-project/"',
            page.body_html,
        )
        page_path = '2026-07-08-tailor-cv/05-tailor-to-domain.md'
        warnings = _relative_warnings_for(sync_log, page_path)
        self.assertEqual(len(warnings), 1, f'got {sync_log.errors!r}')


class ArticleRelativeLinkGuardTest(TestCase):
    """The relative-link guard runs in the article dispatcher too."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/blog',
            prefix='relative-article-sync-',
        )

    def test_relative_link_in_article_emits_warning_and_still_syncs(self):
        self.repo.write_markdown(
            'rel.md',
            {
                'title': 'Relative',
                'slug': 'relative-article',
                'date': '2026-01-15',
            },
            _RELATIVE_LINK_BODY,
        )
        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'))
        article = Article.objects.get(slug='relative-article')
        self.assertTrue(article.published)
        warnings = _relative_warnings_for(sync_log, 'rel.md')
        self.assertEqual(len(warnings), 1, f'got {sync_log.errors!r}')
