"""Issue #988: event and event-series descriptions render through the shared
markdown pipeline (``render_markdown`` + bare-URL linkify), matching
courses/articles, and are sanitised so a raw ``<script>`` is stripped rather
than executed. Existing rows are re-rendered by a data migration.
"""

from datetime import time

from django.test import TestCase
from django.utils import timezone

from content.utils.linkify import linkify_urls
from content.utils.markdown import (
    render_description_html,
    render_markdown,
    sanitize_html,
)
from events.models import Event, EventSeries


class EventDescriptionPipelineTest(TestCase):
    """Event.save() renders description through the shared description pipeline."""

    def test_bullet_list_renders_as_ul(self):
        event = Event.objects.create(
            title='Bullets', slug='bullets',
            description="What we'll explore:\n\n- item one\n- item two",
            start_datetime=timezone.now(),
        )
        self.assertIn('<ul>', event.description_html)
        self.assertIn('<li>item one</li>', event.description_html)
        self.assertIn('<li>item two</li>', event.description_html)
        self.assertNotIn('- item one', event.description_html)

    def test_bare_url_is_linkified(self):
        event = Event.objects.create(
            title='Bare URL', slug='bare-url',
            description='See https://example.com/resource for details',
            start_datetime=timezone.now(),
        )
        self.assertIn('href="https://example.com/resource"', event.description_html)
        self.assertIn('target="_blank"', event.description_html)
        self.assertIn('rel="noopener noreferrer"', event.description_html)

    def test_markdown_link_not_double_linked(self):
        event = Event.objects.create(
            title='MD Link', slug='md-link',
            description='Read [the docs](https://example.com/docs) now',
            start_datetime=timezone.now(),
        )
        html = event.description_html
        self.assertEqual(html.count('<a '), 1)
        self.assertIn('>the docs</a>', html)
        # The bare URL is not additionally emitted as visible text.
        self.assertNotIn('>https://example.com/docs<', html)

    def test_script_tag_is_stripped(self):
        event = Event.objects.create(
            title='XSS', slug='xss-evt',
            description="Hello\n\n<script>alert('xss')</script>\n\n- safe",
            start_datetime=timezone.now(),
        )
        html = event.description_html
        # The script element is removed entirely (sanitised), not just escaped.
        self.assertNotIn('<script', html)
        self.assertNotIn('alert(', html)
        # Legitimate markdown still renders as a real list (no double-escape).
        self.assertIn('<li>safe</li>', html)
        self.assertNotIn('&lt;ul&gt;', html)

    def test_img_onerror_handler_is_stripped(self):
        event = Event.objects.create(
            title='Onerror', slug='onerror-evt',
            description='<img src=x onerror=alert(1)>',
            start_datetime=timezone.now(),
        )
        self.assertNotIn('onerror', event.description_html)

    def test_empty_description_html_is_blank(self):
        event = Event.objects.create(
            title='Empty', slug='empty-evt',
            start_datetime=timezone.now(),
        )
        self.assertEqual(event.description_html, '')


class EventSeriesDescriptionPipelineTest(TestCase):
    """EventSeries.save() renders description through the shared pipeline."""

    def test_bare_url_is_linkified(self):
        series = EventSeries.objects.create(
            name='Office Hours',
            description='Course content: https://github.com/org/repo',
            start_time=time(18, 0),
        )
        self.assertIn('href="https://github.com/org/repo"', series.description_html)
        self.assertIn('target="_blank"', series.description_html)
        self.assertIn('rel="noopener noreferrer"', series.description_html)

    def test_markdown_formatting_renders(self):
        series = EventSeries.objects.create(
            name='Formatted',
            description='# Heading\n\n*emph* text\n\n- one\n- two',
            start_time=time(18, 0),
        )
        html = series.description_html
        self.assertIn('<h1>Heading</h1>', html)
        self.assertIn('<em>emph</em>', html)
        self.assertIn('<ul>', html)
        self.assertIn('<li>one</li>', html)

    def test_script_tag_is_stripped(self):
        series = EventSeries.objects.create(
            name='Series XSS',
            description="Intro\n\n<script>alert(1)</script>\n\n- safe item",
            start_time=time(18, 0),
        )
        html = series.description_html
        self.assertNotIn('<script', html)
        self.assertIn('<li>safe item</li>', html)
        self.assertNotIn('&lt;ul&gt;', html)

    def test_empty_description_html_is_blank(self):
        series = EventSeries.objects.create(
            name='No Desc Series',
            start_time=time(18, 0),
        )
        self.assertEqual(series.description_html, '')


class EventCourseParityTest(TestCase):
    """Identical (benign) markdown renders identically for an event and a
    course — same heading, list, emphasis, and linkified bare URL."""

    # No leading H1: Course.save() strips a leading H1 that duplicates the
    # course title (issue #227), so a ``# <title>`` line would diverge for a
    # reason unrelated to the renderer. A non-title heading exercises heading
    # parity cleanly.
    SOURCE = (
        '## Schedule\n\n'
        'We will cover *several* topics.\n\n'
        '- Setup\n- Build\n- Ship\n\n'
        'Resource: https://example.com/guide'
    )

    def test_event_renders_same_as_course(self):
        from content.models import Course

        event = Event.objects.create(
            title='Parity', slug='parity-evt',
            description=self.SOURCE,
            start_datetime=timezone.now(),
        )
        course = Course.objects.create(
            title='Parity Course', slug='parity-course',
            description=self.SOURCE,
        )
        self.assertIn('<h2>Schedule</h2>', event.description_html)
        self.assertIn('<em>several</em>', event.description_html)
        self.assertIn('<ul>', event.description_html)
        self.assertIn('href="https://example.com/guide"', event.description_html)
        # Benign markdown survives sanitisation untouched, so the event renders
        # the same HTML a course produces from the same source.
        self.assertEqual(event.description_html, course.description_html)

    def test_series_matches_shared_pipeline(self):
        series = EventSeries.objects.create(
            name='Parity Series',
            description=self.SOURCE,
            start_time=time(18, 0),
        )
        self.assertEqual(
            series.description_html,
            render_description_html(self.SOURCE),
        )


class BackfillMigrationTest(TestCase):
    """The 0034 data migration re-renders stale description_html in place."""

    def test_stale_html_is_rebuilt_through_pipeline(self):
        source = 'Intro\n\n- a\n- b\n\nLink https://example.com/x'
        event = Event.objects.create(
            title='Stale', slug='stale-evt',
            description=source,
            start_datetime=timezone.now(),
        )
        # Simulate a pre-existing stale row: bare render_markdown (no linkify),
        # written via the queryset to bypass save().
        Event.objects.filter(pk=event.pk).update(
            description_html=render_markdown(source),
        )
        stale = Event.objects.get(pk=event.pk)
        self.assertNotIn('href="https://example.com/x"', stale.description_html)

        # Run the backfill function directly against the live models.
        import importlib

        migration = importlib.import_module(
            'events.migrations.0034_backfill_description_html'
        )

        class _Apps:
            def get_model(self, app, model):
                return {'Event': Event, 'EventSeries': EventSeries}[model]

        migration.backfill_description_html(_Apps(), None)

        rebuilt = Event.objects.get(pk=event.pk)
        self.assertIn('href="https://example.com/x"', rebuilt.description_html)
        self.assertIn('target="_blank"', rebuilt.description_html)
        self.assertEqual(
            rebuilt.description_html,
            render_description_html(source),
        )
        # The rebuilt HTML is exactly markdown -> linkify -> sanitize.
        self.assertEqual(
            rebuilt.description_html,
            sanitize_html(linkify_urls(render_markdown(source))),
        )
