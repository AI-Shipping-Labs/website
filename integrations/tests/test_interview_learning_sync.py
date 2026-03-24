"""Tests for interview questions sync pipeline."""

import os
import tempfile
import shutil

from django.db import IntegrityError
from django.test import TestCase

from content.models import InterviewCategory
from integrations.models import ContentSource
from integrations.services.github import sync_content_source


class SyncInterviewQuestionsTest(TestCase):
    """Test syncing interview question categories from a mock repo directory."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='interview_question',
            content_path='interview-questions',
        )
        self.temp_dir = tempfile.mkdtemp()
        # The sync resolves content_path relative to repo_dir,
        # so create the interview-questions subdir
        self.content_dir = os.path.join(self.temp_dir, 'interview-questions')
        os.makedirs(self.content_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_md(self, filename, content):
        filepath = os.path.join(self.content_dir, filename)
        with open(filepath, 'w') as f:
            f.write(content)

    def test_sync_creates_interview_category(self):
        self._write_md('theory.md', (
            '---\n'
            'title: "Theory Interview Questions"\n'
            'description: "Prepare for theory questions."\n'
            '\n'
            'sections:\n'
            '  - id: "llm"\n'
            '    title: "LLM Section"\n'
            '    intro: "Covers LLMs."\n'
            '    qa:\n'
            '      - question: "How do LLMs work?"\n'
            '---\n'
            '\n'
            'Body content here.\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_created, 1)

        cat = InterviewCategory.objects.get(slug='theory')
        self.assertEqual(cat.title, 'Theory Interview Questions')
        self.assertEqual(cat.description, 'Prepare for theory questions.')
        self.assertEqual(len(cat.sections_json), 1)
        self.assertEqual(cat.sections_json[0]['id'], 'llm')
        self.assertIn('Body content here', cat.body_markdown)
        self.assertEqual(cat.source_repo, 'AI-Shipping-Labs/content')

    def test_sync_updates_existing_category(self):
        InterviewCategory.objects.create(
            slug='theory',
            title='Old Title',
            source_repo='AI-Shipping-Labs/content',
        )

        self._write_md('theory.md', (
            '---\n'
            'title: "New Theory Title"\n'
            '---\n'
            'Updated body.\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_updated, 1)
        self.assertEqual(sync_log.items_created, 0)

        cat = InterviewCategory.objects.get(slug='theory')
        self.assertEqual(cat.title, 'New Theory Title')

    def test_sync_deletes_stale_categories(self):
        InterviewCategory.objects.create(
            slug='old-category',
            title='Old Category',
            source_repo='AI-Shipping-Labs/content',
        )

        self._write_md('theory.md', (
            '---\n'
            'title: "Theory"\n'
            '---\n'
            'Body.\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_deleted, 1)
        self.assertFalse(
            InterviewCategory.objects.filter(slug='old-category').exists()
        )

    def test_sync_multiple_files(self):
        for slug in ['theory', 'coding', 'behavioral']:
            self._write_md(f'{slug}.md', (
                f'---\n'
                f'title: "{slug.title()} Questions"\n'
                f'---\n'
                f'Body for {slug}.\n'
            ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_created, 3)
        self.assertEqual(InterviewCategory.objects.count(), 3)

    def test_sync_coming_soon_status(self):
        self._write_md('coding.md', (
            '---\n'
            'title: "Coding Questions"\n'
            'status: "coming-soon"\n'
            '---\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')

        cat = InterviewCategory.objects.get(slug='coding')
        self.assertEqual(cat.status, 'coming-soon')

    def test_sync_skips_readme(self):
        self._write_md('README.md', '# Interview Questions\n')
        self._write_md('theory.md', (
            '---\n'
            'title: "Theory"\n'
            '---\n'
            'Body.\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 1)
        self.assertFalse(
            InterviewCategory.objects.filter(slug='README').exists()
        )


class InterviewCategoryModelTest(TestCase):
    """Test InterviewCategory model."""

    def test_create_category(self):
        cat = InterviewCategory.objects.create(
            slug='theory',
            title='Theory Questions',
            description='Theory description.',
            sections_json=[{'id': 'llm', 'title': 'LLM'}],
            body_markdown='# Body',
        )
        self.assertEqual(str(cat), 'Theory Questions')
        self.assertEqual(cat.get_absolute_url(), '/interview/theory')
        self.assertEqual(len(cat.sections_json), 1)

    def test_slug_unique(self):
        InterviewCategory.objects.create(slug='theory', title='Theory')
        with self.assertRaises(IntegrityError):
            InterviewCategory.objects.create(slug='theory', title='Theory 2')


