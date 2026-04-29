import datetime

from django.test import TestCase, override_settings
from django.utils import timezone

from content.models import Article, Course, Module, Unit
from events.models import Event


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class SharedMarkdownRuntimeTest(TestCase):
    rich_markdown = (
        'Intro with [external](https://example.com/docs){#external-docs}.\n\n'
        '```python\n'
        'print("hello")\n'
        '```\n\n'
        '| A | B |\n'
        '| - | - |\n'
        '| 1 | 2 |\n\n'
        '```mermaid\n'
        'flowchart LR\n'
        '    A --> B\n'
        '```\n\n'
        '<section markdown="1">**inside html**</section>\n'
    )

    def assert_rich_markdown_rendered(self, html):
        self.assertIn('class="codehilite"', html)
        self.assertIn('<table>', html)
        self.assertIn('<div class="mermaid">', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('id="external-docs"', html)
        self.assertIn('<section>', html)
        self.assertIn('<strong>inside html</strong>', html)

    def test_article_uses_shared_renderer_and_preserves_h1_strip_and_linkify(self):
        article = Article.objects.create(
            title='Runtime Article',
            slug='runtime-article',
            date=datetime.date(2026, 1, 1),
            content_markdown=(
                '# Runtime Article\n\n'
                f'{self.rich_markdown}\n'
                'Bare URL: https://example.org/plain'
            ),
        )

        self.assertNotIn('<h1>Runtime Article</h1>', article.content_html)
        self.assert_rich_markdown_rendered(article.content_html)
        self.assertIn('href="https://example.org/plain"', article.content_html)

    def test_course_module_and_unit_paths_use_shared_renderer(self):
        course = Course.objects.create(
            title='Runtime Course',
            slug='runtime-course',
            description=f'# Runtime Course\n\n{self.rich_markdown}',
            peer_review_criteria='See https://example.org/rubric',
        )
        module = Module.objects.create(
            course=course,
            title='Runtime Module',
            slug='runtime-module',
            overview=f'# Runtime Module\n\n{self.rich_markdown}',
        )
        unit = Unit.objects.create(
            module=module,
            title='Runtime Unit',
            slug='runtime-unit',
            body=f'# Runtime Unit\n\n{self.rich_markdown}',
            homework=self.rich_markdown,
        )

        self.assertNotIn('<h1>Runtime Course</h1>', course.description_html)
        self.assertNotIn('<h1>Runtime Module</h1>', module.overview_html)
        self.assertNotIn('<h1>Runtime Unit</h1>', unit.body_html)
        self.assert_rich_markdown_rendered(course.description_html)
        self.assert_rich_markdown_rendered(module.overview_html)
        self.assert_rich_markdown_rendered(unit.body_html)
        self.assert_rich_markdown_rendered(unit.homework_html)
        self.assertIn('href="https://example.org/rubric"', course.peer_review_criteria_html)

    def test_event_uses_shared_renderer_without_content_linkify_or_h1_strip(self):
        event = Event.objects.create(
            title='Runtime Event',
            slug='runtime-event',
            start_datetime=timezone.now(),
            description=f'# Runtime Event\n\n{self.rich_markdown}\nBare https://example.org/plain',
        )

        self.assertIn('<h1>Runtime Event</h1>', event.description_html)
        self.assert_rich_markdown_rendered(event.description_html)
        self.assertNotIn('href="https://example.org/plain"', event.description_html)
