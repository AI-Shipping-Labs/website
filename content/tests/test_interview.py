import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings


def _create_temp_content(base_dir):
    """Create a temporary content repo structure with interview questions."""
    interview_dir = base_dir / 'interview-questions'
    interview_dir.mkdir(parents=True, exist_ok=True)

    # Theory: full content with sections
    (interview_dir / 'theory.md').write_text(
        '---\n'
        'title: "Theory Interview Questions"\n'
        'description: "Prepare for AI engineer interviews with theory questions."\n'
        '\n'
        'sections:\n'
        '  - id: "llm-practice"\n'
        '    title: "1. Working with LLMs"\n'
        '    intro: "This section covers LLM fundamentals."\n'
        '    qa:\n'
        '      - question: "How do LLMs work?"\n'
        '      - question: "What parameters control LLM output?"\n'
        '  - id: "rag-systems"\n'
        '    title: "2. RAG Systems"\n'
        '    intro: "This section focuses on RAG."\n'
        '    qa:\n'
        '      - question: "What is RAG?"\n'
        '---\n'
        '\n'
        '## Introduction\n'
        '\n'
        'These are theory questions.\n'
        '\n'
        '## Format\n'
        '\n'
        'The interview is conversational.\n'
        '\n'
        '<!-- after-questions -->\n'
        '\n'
        '## Common Mistakes\n'
        '\n'
        '1. Not knowing trade-offs.\n'
    )

    # Coming-soon pages
    for slug in ['coding', 'system-design', 'behavioral', 'project-deep-dive', 'home-assignments']:
        title = slug.replace('-', ' ').title()
        (interview_dir / f'{slug}.md').write_text(
            f'---\n'
            f'title: "{title} Questions"\n'
            f'description: "Description for {title}."\n'
            f'status: "coming-soon"\n'
            f'---\n'
        )

    return base_dir


class InterviewHubViewTest(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.content_dir = Path(self.temp_dir)
        _create_temp_content(self.content_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @override_settings()
    def test_interview_hub_returns_200(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview')
        self.assertEqual(response.status_code, 200)

    @override_settings()
    def test_interview_hub_shows_all_categories(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Theory Interview Questions', content)
        self.assertIn('Coding Questions', content)
        self.assertIn('System Design Questions', content)
        self.assertIn('Behavioral Questions', content)

    @override_settings()
    def test_interview_hub_shows_coming_soon_labels(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview')
        content = response.content.decode()
        self.assertIn('Coming soon', content)

    @override_settings()
    def test_interview_hub_active_card_links(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview')
        content = response.content.decode()
        self.assertIn('href="/interview/theory"', content)

    @override_settings()
    def test_interview_hub_coming_soon_no_link(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview')
        content = response.content.decode()
        # Coming-soon cards should not have links to detail pages
        self.assertNotIn('href="/interview/coding"', content)
        self.assertNotIn('href="/interview/system-design"', content)

    @override_settings()
    def test_interview_hub_uses_correct_template(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview')
        self.assertTemplateUsed(response, 'content/interview_hub.html')

    @override_settings()
    def test_interview_hub_404_when_no_content_repo(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path('/nonexistent/path')

        response = self.client.get('/interview')
        self.assertEqual(response.status_code, 404)


class InterviewDetailViewTest(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.content_dir = Path(self.temp_dir)
        _create_temp_content(self.content_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @override_settings()
    def test_theory_detail_returns_200(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview/theory')
        self.assertEqual(response.status_code, 200)

    @override_settings()
    def test_theory_detail_renders_sections(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview/theory')
        content = response.content.decode()
        self.assertIn('1. Working with LLMs', content)
        self.assertIn('2. RAG Systems', content)
        self.assertIn('How do LLMs work?', content)
        self.assertIn('What is RAG?', content)

    @override_settings()
    def test_theory_detail_renders_before_questions_content(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview/theory')
        content = response.content.decode()
        self.assertIn('Introduction', content)
        self.assertIn('These are theory questions', content)

    @override_settings()
    def test_theory_detail_renders_after_questions_content(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview/theory')
        content = response.content.decode()
        self.assertIn('Common Mistakes', content)
        self.assertIn('Not knowing trade-offs', content)

    @override_settings()
    def test_theory_detail_uses_correct_template(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview/theory')
        self.assertTemplateUsed(response, 'content/interview_detail.html')

    @override_settings()
    def test_coming_soon_returns_404(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview/coding')
        self.assertEqual(response.status_code, 404)

    @override_settings()
    def test_nonexistent_slug_returns_404(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview/does-not-exist')
        self.assertEqual(response.status_code, 404)

    @override_settings()
    def test_detail_404_when_no_content_repo(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path('/nonexistent/path')

        response = self.client.get('/interview/theory')
        self.assertEqual(response.status_code, 404)

    @override_settings()
    def test_section_intros_rendered_as_html(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/interview/theory')
        content = response.content.decode()
        # The intro text should be rendered (as HTML paragraph)
        self.assertIn('This section covers LLM fundamentals.', content)
