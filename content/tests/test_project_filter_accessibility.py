from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, tag

from content.models import Project


class _DifficultyActionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.actions = {}

    def handle_starttag(self, tag_name, attrs):
        attrs = dict(attrs)
        testid = attrs.get('data-testid', '')
        if tag_name == 'a' and testid.startswith('project-difficulty-'):
            self.actions[testid] = attrs


@tag('visual_regression')
class ProjectDifficultyAccessibilityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for difficulty, tags in (
            ('beginner', ['python', 'rag']),
            ('intermediate', ['python']),
            ('advanced', ['rag']),
        ):
            Project.objects.create(
                title=f'{difficulty.title()} project',
                slug=f'{difficulty}-project-1224',
                description='Accessible filter fixture',
                date=date(2026, 7, 13),
                difficulty=difficulty,
                tags=tags,
                published=True,
            )

    def test_selected_inactive_and_clear_filters_preserve_tag_context(self):
        response = self.client.get(
            '/projects?difficulty=beginner&tag=python&tag=rag'
        )
        self.assertEqual(response.status_code, 200)
        parser = _DifficultyActionParser()
        parser.feed(response.content.decode())

        self.assertEqual(
            set(parser.actions),
            {
                'project-difficulty-clear',
                'project-difficulty-beginner',
                'project-difficulty-intermediate',
                'project-difficulty-advanced',
            },
        )
        current = [
            testid
            for testid, attrs in parser.actions.items()
            if attrs.get('aria-current') == 'page'
        ]
        self.assertEqual(current, ['project-difficulty-beginner'])

        for testid, attrs in parser.actions.items():
            classes = set(attrs['class'].split())
            self.assertIn('inline-flex', classes, testid)
            self.assertIn('min-h-[44px]', classes, testid)
            self.assertIn('px-4', classes, testid)
            self.assertIn('py-2', classes, testid)
            self.assertIn('text-sm', classes, testid)

            query = parse_qs(urlparse(attrs['href']).query)
            self.assertEqual(query['tag'], ['python', 'rag'], testid)
            if testid == 'project-difficulty-clear':
                self.assertNotIn('difficulty', query)
            else:
                self.assertEqual(
                    query['difficulty'],
                    [testid.removeprefix('project-difficulty-')],
                )
