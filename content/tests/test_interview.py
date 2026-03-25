from django.test import TestCase

from content.models import InterviewCategory


class InterviewHubDbViewTest(TestCase):
    """Test interview hub reads from the database."""

    @classmethod
    def setUpTestData(cls):
        cls.theory = InterviewCategory.objects.create(
            slug='theory',
            title='Theory Interview Questions',
            description='Prepare for theory questions.',
            status='',
            sections_json=[
                {
                    'id': 'llm-practice',
                    'title': '1. Working with LLMs',
                    'intro': 'This section covers LLM fundamentals.',
                    'qa': [{'question': 'How do LLMs work?'}],
                },
            ],
            body_markdown=(
                '## Introduction\n\n'
                'These are theory questions.\n\n'
                '<!-- after-questions -->\n\n'
                '## Common Mistakes\n\n'
                '1. Not knowing trade-offs.\n'
            ),
        )
        cls.coding = InterviewCategory.objects.create(
            slug='coding',
            title='Coding Questions',
            description='Description for Coding.',
            status='coming-soon',
            sections_json=[],
            body_markdown='',
        )

    def test_hub_returns_200_from_db(self):
        response = self.client.get('/interview')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Theory Interview Questions', content)
        self.assertIn('Coding Questions', content)

    def test_hub_uses_correct_template_from_db(self):
        response = self.client.get('/interview')
        self.assertTemplateUsed(response, 'content/interview_hub.html')

    def test_hub_returns_404_when_no_categories(self):
        InterviewCategory.objects.all().delete()
        response = self.client.get('/interview')
        self.assertEqual(response.status_code, 404)


class InterviewDetailDbViewTest(TestCase):
    """Test interview detail reads from the database."""

    @classmethod
    def setUpTestData(cls):
        cls.theory = InterviewCategory.objects.create(
            slug='theory',
            title='Theory Interview Questions',
            description='Prepare for theory questions.',
            status='',
            sections_json=[
                {
                    'id': 'llm-practice',
                    'title': '1. Working with LLMs',
                    'intro': 'This section covers LLM fundamentals.',
                    'qa': [{'question': 'How do LLMs work?'}],
                },
            ],
            body_markdown=(
                '## Introduction\n\n'
                'These are theory questions.\n\n'
                '<!-- after-questions -->\n\n'
                '## Common Mistakes\n\n'
                '1. Not knowing trade-offs.\n'
            ),
        )
        cls.coding = InterviewCategory.objects.create(
            slug='coding',
            title='Coding Questions',
            status='coming-soon',
            sections_json=[],
            body_markdown='',
        )

    def test_detail_returns_200_from_db(self):
        response = self.client.get('/interview/theory')
        self.assertEqual(response.status_code, 200)

    def test_detail_renders_sections_from_db(self):
        response = self.client.get('/interview/theory')
        content = response.content.decode()
        self.assertIn('1. Working with LLMs', content)
        self.assertIn('How do LLMs work?', content)

    def test_detail_renders_body_from_db(self):
        response = self.client.get('/interview/theory')
        content = response.content.decode()
        self.assertIn('Introduction', content)
        self.assertIn('Common Mistakes', content)

    def test_coming_soon_returns_404_from_db(self):
        response = self.client.get('/interview/coding')
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_slug_returns_404_from_db(self):
        response = self.client.get('/interview/does-not-exist')
        self.assertEqual(response.status_code, 404)
