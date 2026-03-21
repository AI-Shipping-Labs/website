"""Tests for interview questions and learning path sync pipeline."""

import os
import tempfile
import shutil

from django.test import TestCase

from content.models import InterviewCategory, LearningPath
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


class SyncLearningPathsTest(TestCase):
    """Test syncing learning paths from a mock repo directory."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='learning_path',
            content_path='learning-path',
        )
        self.temp_dir = tempfile.mkdtemp()
        self.content_dir = os.path.join(self.temp_dir, 'learning-path')
        os.makedirs(self.content_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_yaml(self, subdir, filename, content):
        dirpath = os.path.join(self.content_dir, subdir)
        os.makedirs(dirpath, exist_ok=True)
        with open(os.path.join(dirpath, filename), 'w') as f:
            f.write(content)

    def test_sync_creates_learning_path(self):
        self._write_yaml('ai-engineer', 'data.yaml', (
            'title: "AI Engineer Learning Path"\n'
            'description: "A visual learning path."\n'
            'skill_categories:\n'
            '  - id: genai\n'
            '    label: "GenAI Skills"\n'
            '    skills:\n'
            '      - name: "RAG"\n'
            '        pct: 35.9\n'
            'learning_stages:\n'
            '  - stage: "1"\n'
            '    title: "Python Foundations"\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_created, 1)

        lp = LearningPath.objects.get(slug='ai-engineer')
        self.assertEqual(lp.title, 'AI Engineer Learning Path')
        self.assertEqual(lp.description, 'A visual learning path.')
        self.assertIn('skill_categories', lp.data_json)
        self.assertEqual(
            lp.data_json['skill_categories'][0]['label'], 'GenAI Skills'
        )
        self.assertEqual(lp.source_repo, 'AI-Shipping-Labs/content')

    def test_sync_updates_existing_path(self):
        LearningPath.objects.create(
            slug='ai-engineer',
            title='Old Title',
            data_json={'title': 'Old'},
            source_repo='AI-Shipping-Labs/content',
        )

        self._write_yaml('ai-engineer', 'data.yaml', (
            'title: "Updated AI Engineer Path"\n'
            'description: "Updated description."\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_updated, 1)
        self.assertEqual(sync_log.items_created, 0)

        lp = LearningPath.objects.get(slug='ai-engineer')
        self.assertEqual(lp.title, 'Updated AI Engineer Path')

    def test_sync_deletes_stale_paths(self):
        LearningPath.objects.create(
            slug='old-path',
            title='Old Path',
            data_json={},
            source_repo='AI-Shipping-Labs/content',
        )

        self._write_yaml('ai-engineer', 'data.yaml', (
            'title: "AI Engineer"\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_deleted, 1)
        self.assertFalse(
            LearningPath.objects.filter(slug='old-path').exists()
        )

    def test_sync_multiple_paths(self):
        for name in ['ai-engineer', 'ml-engineer']:
            self._write_yaml(name, 'data.yaml', (
                f'title: "{name.replace("-", " ").title()} Path"\n'
            ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_created, 2)
        self.assertEqual(LearningPath.objects.count(), 2)

    def test_sync_ignores_dirs_without_data_yaml(self):
        # Create a directory without data.yaml
        os.makedirs(os.path.join(self.content_dir, 'empty-dir'))

        self._write_yaml('ai-engineer', 'data.yaml', (
            'title: "AI Engineer"\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 1)

    def test_sync_data_yml_extension(self):
        """Also supports .yml extension."""
        self._write_yaml('ai-engineer', 'data.yml', (
            'title: "AI Engineer Path"\n'
            'description: "With .yml extension."\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_created, 1)

        lp = LearningPath.objects.get(slug='ai-engineer')
        self.assertEqual(lp.title, 'AI Engineer Path')


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
        with self.assertRaises(Exception):
            InterviewCategory.objects.create(slug='theory', title='Theory 2')


class LearningPathModelTest(TestCase):
    """Test LearningPath model."""

    def test_create_path(self):
        lp = LearningPath.objects.create(
            slug='ai-engineer',
            title='AI Engineer Learning Path',
            description='A learning path.',
            data_json={'skill_categories': []},
        )
        self.assertEqual(str(lp), 'AI Engineer Learning Path')
        self.assertEqual(lp.get_absolute_url(), '/learning-path/ai-engineer')

    def test_slug_unique(self):
        LearningPath.objects.create(slug='ai-engineer', title='Path 1')
        with self.assertRaises(Exception):
            LearningPath.objects.create(slug='ai-engineer', title='Path 2')
