import datetime

from django.test import TestCase, override_settings
from django.utils import timezone

from content.models import (
    Article,
    Course,
    Instructor,
    Module,
    Project,
    Unit,
    Workshop,
    WorkshopPage,
)
from content.models.interview_category import (
    render_markdown as render_interview_category_markdown,
)
from content.utils.markdown import render_markdown
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

    def test_workshop_and_project_paths_use_shared_renderer(self):
        workshop = Workshop.objects.create(
            title='Runtime Workshop',
            slug='runtime-workshop',
            date=datetime.date(2026, 1, 1),
            description=self.rich_markdown,
        )
        page = WorkshopPage.objects.create(
            workshop=workshop,
            title='Runtime Page',
            slug='runtime-page',
            body=self.rich_markdown,
        )
        project = Project.objects.create(
            title='Runtime Project',
            slug='runtime-project',
            date=datetime.date(2026, 1, 1),
            content_markdown=self.rich_markdown,
        )

        self.assert_rich_markdown_rendered(workshop.description_html)
        self.assert_rich_markdown_rendered(page.body_html)
        self.assert_rich_markdown_rendered(project.content_html)

    def test_instructor_renderer_keeps_mermaid_but_omits_external_links(self):
        instructor = Instructor.objects.create(
            instructor_id='runtime-instructor',
            name='Runtime Instructor',
            bio=self.rich_markdown,
        )

        self.assertIn('<div class="mermaid">', instructor.bio_html)
        self.assertIn('class="codehilite"', instructor.bio_html)
        self.assertIn('href="https://example.com/docs"', instructor.bio_html)
        self.assertNotIn('target="_blank"', instructor.bio_html)

    def test_interview_category_renderer_omits_mermaid_and_external_links(self):
        html = render_interview_category_markdown(self.rich_markdown)

        self.assertIn('class="codehilite"', html)
        self.assertIn('<table>', html)
        self.assertIn('href="https://example.com/docs"', html)
        self.assertNotIn('target="_blank"', html)
        self.assertNotIn('<div class="mermaid">', html)

    def test_shared_renderer_options_document_intentional_variants(self):
        md = (
            '[external](https://example.com)\n\n'
            '```mermaid\nflowchart LR\n    A --> B\n```\n'
        )

        instructor_html = render_markdown(md, include_external_links=False)
        self.assertIn('<div class="mermaid">', instructor_html)
        self.assertNotIn('target="_blank"', instructor_html)

        interview_html = render_markdown(
            md,
            include_mermaid=False,
            include_external_links=False,
        )
        self.assertNotIn('<div class="mermaid">', interview_html)
        self.assertNotIn('target="_blank"', interview_html)

    def test_shared_renderer_can_preserve_interview_codehilite_guessing(self):
        md = '```\ndef greet():\n    return 1\n```\n'

        default_html = render_markdown(md)
        guessed_html = render_markdown(md, codehilite_guess_lang=True)

        self.assertIn('def greet():', default_html)
        self.assertNotIn('<span class="nv">def</span>', default_html)
        self.assertIn('<span class="nv">def</span>', guessed_html)
