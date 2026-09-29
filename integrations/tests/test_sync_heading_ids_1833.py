"""Content sync keeps section-anchor heading ids (issue #1833 follow-up).

Migrations backfilled ids into stored HTML, but stored HTML is re-rendered
on every content sync. These tests sync fixture repos from disk and assert
that the freshly written HTML still carries a slug ``id`` on every heading,
so a sync can never wipe the anchors the backfill added.
"""

import re

from django.test import TestCase

from content.models import Article, Course, Instructor, Module, Unit, WorkshopPage
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

HEADING_RE = re.compile(r'<h([1-6])(?P<attrs>[^>]*)>(?P<text>.*?)</h\1>', re.DOTALL)


def heading_ids(html):
    """``[(text, id-or-None), ...]`` for every heading in ``html``."""
    result = []
    for match in HEADING_RE.finditer(html):
        id_match = re.search(r'\sid="([^"]*)"', match.group('attrs'))
        result.append((match.group('text'), id_match.group(1) if id_match else None))
    return result


class CourseSyncHeadingIdsTest(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/anchors-course', prefix='anchors-course-',
        )
        self.repo.write_yaml('course.yaml', {
            'title': 'Anchors Course',
            'slug': 'anchors-course',
            'instructor_name': 'Alexey Grigorev',
            'required_level': 0,
            'is_free': True,
            'content_id': '11111111-1111-1111-1111-111111111111',
        })
        self.repo.write_text(
            'README.md', '# Anchors Course\n\nIntro.\n\n## What you build\n\nText.\n',
        )
        self.repo.write_yaml('01-agents/module.yaml', {
            'title': 'Agents',
            'content_id': '22222222-2222-2222-2222-222222222222',
        })
        self.repo.write_text(
            '01-agents/README.md', '# Agents\n\n## Module goals\n\nGoals.\n',
        )
        self.repo.write_text(
            '01-agents/01-rag.md',
            '---\n'
            'title: "Agentic RAG"\n'
            'content_id: "33333333-3333-3333-3333-333333333333"\n'
            '---\n'
            'Intro paragraph.\n\n'
            '## RAG vs Agentic RAG\n\nCompare.\n\n'
            '### Example\n\nOne.\n\n'
            '### Example\n\nTwo.\n',
        )

    def test_synced_course_html_has_heading_ids(self):
        log = sync_repo(self.source, self.repo)
        self.assertEqual(log.errors, [])

        unit = Unit.objects.get(title='Agentic RAG')
        self.assertEqual(
            heading_ids(unit.body_html),
            [
                ('RAG vs Agentic RAG', 'rag-vs-agentic-rag'),
                ('Example', 'example'),
                ('Example', 'example-1'),
            ],
        )
        module = Module.objects.get(title='Agents')
        self.assertEqual(heading_ids(module.overview_html), [('Module goals', 'module-goals')])
        course = Course.objects.get(slug='anchors-course')
        self.assertEqual(
            heading_ids(course.description_html), [('What you build', 'what-you-build')],
        )

    def test_resync_with_changed_body_keeps_heading_ids(self):
        sync_repo(self.source, self.repo)
        self.repo.write_text(
            '01-agents/01-rag.md',
            '---\n'
            'title: "Agentic RAG"\n'
            'content_id: "33333333-3333-3333-3333-333333333333"\n'
            '---\n'
            '## Retrieval loop\n\nChanged body.\n',
        )

        log = sync_repo(self.source, self.repo)
        self.assertEqual(log.errors, [])

        unit = Unit.objects.get(title='Agentic RAG')
        self.assertIn('Changed body.', unit.body_html)
        self.assertEqual(heading_ids(unit.body_html), [('Retrieval loop', 'retrieval-loop')])


class WorkshopAndArticleSyncHeadingIdsTest(TestCase):
    def test_synced_workshop_page_has_heading_ids(self):
        Instructor.objects.get_or_create(
            instructor_id='alexey-grigorev',
            defaults={'name': 'Alexey Grigorev', 'status': 'published'},
        )
        source, repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/workshops-content', prefix='anchors-ws-',
        )
        folder = '2026/2026-04-21-anchors'
        repo.write_yaml(f'{folder}/workshop.yaml', {
            'content_id': '44444444-4444-4444-4444-444444444444',
            'slug': 'anchors',
            'title': 'Anchors Workshop',
            'date': '2026-04-21',
            'pages_required_level': 0,
            'instructors': ['alexey-grigorev'],
        })
        repo.write_markdown(
            f'{folder}/01-setup.md', {'title': 'Setup'},
            '## Install the tools\n\nRun it.\n', ensure_content_id=False,
        )

        log = sync_repo(source, repo)
        self.assertEqual(log.errors, [])

        page = WorkshopPage.objects.get(title='Setup')
        self.assertEqual(
            heading_ids(page.body_html), [('Install the tools', 'install-the-tools')],
        )

    def test_synced_article_has_heading_ids(self):
        source, repo = make_sync_repo(self, repo_name='test-org/blog', prefix='anchors-blog-')
        repo.write_markdown(
            'hello.md',
            {'title': 'Hello', 'slug': 'hello', 'date': '2026-04-21', 'author': 'Alexey'},
            '## First section\n\nBody.\n',
        )

        log = sync_repo(source, repo)
        self.assertEqual(log.errors, [])

        article = Article.objects.get(slug='hello')
        self.assertEqual(heading_ids(article.content_html), [('First section', 'first-section')])
