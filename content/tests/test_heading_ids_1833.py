"""Section anchors: heading ids in rendered content (issue #1833)."""

import importlib
import re
import uuid
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase

from content.models import (
    Article,
    Course,
    CourseExtension,
    Instructor,
    MarketingPage,
    Module,
    Project,
    Tutorial,
    Unit,
    Workshop,
    WorkshopPage,
)
from content.utils.code_annotations import render_course_unit_body
from content.utils.heading_ids import add_heading_ids
from content.utils.markdown import (
    markdown_to_plain_text,
    render_email_markdown,
    render_markdown,
    sanitize_html,
)

_HEADING_ID_RE = re.compile(r'<h[1-6][^>]*\sid="([^"]*)"')


def _heading_ids(html):
    return _HEADING_ID_RE.findall(html)


class RenderMarkdownHeadingIdsTest(TestCase):
    def test_heading_gets_slug_id(self):
        html = render_markdown('## Streaming Responses\n\ntext')
        self.assertIn(
            '<h2 id="streaming-responses">Streaming Responses</h2>', html,
        )

    def test_duplicate_headings_get_hyphen_suffixes(self):
        html = render_markdown(
            '## Example\n\na\n\n## Example\n\nb\n\n## Example\n\nc',
        )
        self.assertEqual(
            _heading_ids(html), ['example', 'example-1', 'example-2'],
        )

    def test_author_id_wins_and_suppresses_generated_slug(self):
        html = render_markdown('## Streaming {#custom-stream}\n\ntext')
        self.assertIn('<h2 id="custom-stream">Streaming</h2>', html)
        self.assertNotIn('id="streaming"', html)

    def test_generated_slug_dedupes_against_later_author_id(self):
        html = render_markdown(
            '## Streaming\n\na\n\n## Other {#streaming}\n\nb',
        )
        self.assertEqual(_heading_ids(html), ['streaming-1', 'streaming'])

    def test_empty_slug_falls_back_to_section(self):
        html = render_markdown('## \U0001F680\n\ntext')
        self.assertIn('<h2 id="section">\U0001F680</h2>', html)

    def test_punctuation_and_accents_are_folded(self):
        html = render_markdown('## Café & RAG')
        self.assertEqual(_heading_ids(html), ['cafe-rag'])

    def test_every_heading_level_gets_an_id(self):
        source = '\n\n'.join(f'{"#" * level} Level {level}' for level in range(1, 7))
        html = render_markdown(source)
        for level in range(1, 7):
            self.assertIn(
                f'<h{level} id="level-{level}">Level {level}</h{level}>', html,
            )

    def test_literal_toc_marker_stays_text(self):
        html = render_markdown('[TOC]\n\n## Setup')
        self.assertIn('<p>[TOC]</p>', html)
        self.assertNotIn('class="toc"', html)

    def test_heading_has_no_permalink_markup(self):
        html = render_markdown('## Setup\n\n### Details')
        self.assertIn('<h2 id="setup">Setup</h2>', html)
        self.assertIn('<h3 id="details">Details</h3>', html)
        self.assertNotIn('headerlink', html)
        self.assertNotIn('¶', html)
        self.assertNotIn('#', html)

    def test_email_markdown_has_no_heading_ids(self):
        html = render_email_markdown('## Setup\n\ntext\n\n### Setup')
        self.assertIn('<h2>Setup</h2>', html)
        self.assertIn('<h3>Setup</h3>', html)
        self.assertNotIn('id=', html)

    def test_plain_text_is_heading_text_only(self):
        self.assertEqual(
            markdown_to_plain_text('## Streaming Responses\n\nSome text.'),
            'Streaming Responses Some text.',
        )


class SanitizeHeadingIdTest(TestCase):
    def test_keeps_id_on_headings_only(self):
        html = sanitize_html(
            ''.join(
                f'<h{level} id="s{level}">H{level}</h{level}>'
                for level in range(1, 7)
            )
            + '<p id="para">p</p><div id="box">d</div><a id="lnk" href="/x">a</a>'
        )
        for level in range(1, 7):
            self.assertIn(f'<h{level} id="s{level}">H{level}</h{level}>', html)
        self.assertNotIn('id="para"', html)
        self.assertNotIn('id="box"', html)
        self.assertNotIn('id="lnk"', html)

    def test_still_strips_script_and_event_handlers(self):
        html = sanitize_html(
            '<h2 id="ok" onclick="alert(1)">T</h2><script>alert(2)</script>'
            '<img src="/x.png" onerror="alert(3)">'
        )
        self.assertIn('<h2 id="ok">T</h2>', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('onclick', html)
        self.assertNotIn('onerror', html)


class AddHeadingIdsHelperTest(TestCase):
    PARITY_SOURCE = (
        '# Guide\n\n'
        '## Setup\n\ntext\n\n'
        '## Use `stream()` now!\n\ntext\n\n'
        '## Setup\n\ntext\n\n'
        '### What\'s next?\n\ntext\n\n'
        '## Custom {#setup-2}\n\ntext\n\n'
        '## Setup\n\ntext\n'
    )

    def test_backfill_slugs_match_renderer_slugs(self):
        stored_without_ids = render_markdown(
            self.PARITY_SOURCE, include_heading_ids=False,
        )
        # attr_list still emits the author id; everything else lacks one.
        self.assertEqual(_heading_ids(stored_without_ids), ['setup-2'])

        backfilled = add_heading_ids(stored_without_ids)

        self.assertEqual(backfilled, render_markdown(self.PARITY_SOURCE))
        self.assertEqual(
            _heading_ids(backfilled),
            ['guide', 'setup', 'use-stream-now', 'setup-1', 'whats-next',
             'setup-2', 'setup-3'],
        )

    def test_running_twice_is_idempotent(self):
        once = add_heading_ids('<h2>Setup</h2><p>x</p><h2>Setup</h2>')
        self.assertEqual(
            once, '<h2 id="setup">Setup</h2><p>x</p><h2 id="setup-1">Setup</h2>',
        )
        self.assertEqual(add_heading_ids(once), once)

    def test_keeps_existing_ids_and_dedupes_against_them(self):
        html = add_heading_ids(
            '<h2 id="setup">Intro</h2><h2>Setup</h2><h3 class="note">Setup</h3>'
        )
        self.assertEqual(
            html,
            '<h2 id="setup">Intro</h2><h2 id="setup-1">Setup</h2>'
            '<h3 id="setup-2" class="note">Setup</h3>',
        )

    def test_only_heading_ids_change_in_expanded_include_html(self):
        stored = (
            '<p>Intro</p>\n'
            '<div class="include-card" data-include="cta.md">'
            '<h2>Join the <em>community</em></h2>'
            '<iframe src="https://www.youtube.com/embed/abc" '
            'allowfullscreen></iframe></div>\n'
            '<div class="mermaid">graph TD; A--&gt;B</div>\n'
            '<pre><code>&lt;h2&gt;not a heading&lt;/h2&gt;</code></pre>\n'
            '<h3 data-id="x">Details &amp; more</h3>\n'
        )

        backfilled = add_heading_ids(stored)

        self.assertEqual(
            _heading_ids(backfilled), ['join-the-community', 'details-more'],
        )
        self.assertEqual(re.sub(r' id="[^"]*"', '', backfilled), stored)

    def test_html_without_headings_is_unchanged(self):
        stored = '<p>No <strong>headings</strong> here.</p><hr><header>x</header>'
        self.assertEqual(add_heading_ids(stored), stored)


class CourseUnitAnnotatedBodyHeadingIdsTest(TestCase):
    def test_annotation_markup_kept_and_headings_get_ids(self):
        body = (
            '## Streaming Responses\n\nIntro.\n\n'
            '```python\nfirst = 1\nprint(first)\n```\n'
            '<!--\nstructured: true\ncode_annotations:\n'
            '  - line: 1\n    text: Set the initial value.\n-->\n\n'
            '## Error Handling\n\nMore.\n'
        )

        html = render_course_unit_body(body)

        self.assertIn('data-testid="code-annotations"', html)
        self.assertIn('class="codehilite annotated-code-block"', html)
        self.assertIn(
            '<h3 id="code-annotations-1" class="code-annotations-heading">', html,
        )
        self.assertIn(
            '<h2 id="streaming-responses">Streaming Responses</h2>', html,
        )
        self.assertIn('<h2 id="error-handling">Error Handling</h2>', html)


class UnitShareLinkFragmentTest(TestCase):
    def test_share_link_location_has_no_fragment_and_keeps_query(self):
        course = Course.objects.create(
            title='Anchors', slug='anchors', status='published',
        )
        module = Module.objects.create(
            course=course, title='Module', slug='module', sort_order=1,
        )
        content_id = uuid.uuid4()
        unit = Unit.objects.create(
            module=module, title='Lesson', slug='lesson', sort_order=1,
            source_content_id=content_id,
            body='## Streaming Responses\n\ntext',
        )

        response = self.client.get(f'/c/{content_id}?ref=slack')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'], f'{unit.get_absolute_url()}?ref=slack',
        )
        self.assertNotIn('#', response['Location'])


_BACKFILL = importlib.import_module(
    'content.migrations.0079_backfill_heading_ids',
)
_STORED = '<p>Intro</p><h2>Setup</h2><p>x</p><h2>Setup</h2>'
_EXPECTED = (
    '<p>Intro</p><h2 id="setup">Setup</h2><p>x</p><h2 id="setup-1">Setup</h2>'
)
_NO_HEADINGS = '<p>Plain <em>paragraph</em> only.</p>'


class ContentHeadingIdBackfillMigrationTest(TestCase):
    """The data migration adds ids via ``update()`` for every covered field."""

    @classmethod
    def setUpTestData(cls):
        today = date(2026, 9, 1)
        course = Course.objects.create(
            title='Backfill course', slug='backfill-course', status='published',
            description='Course intro',
        )
        module = Module.objects.create(
            course=course, title='Backfill module', slug='backfill-module',
            sort_order=1, overview='Overview',
        )
        unit = Unit.objects.create(
            module=module, title='Backfill unit', slug='backfill-unit',
            sort_order=1, body='Body', homework='Homework',
        )
        workshop = Workshop.objects.create(
            title='Backfill workshop', slug='backfill-workshop', date=today,
            description='Workshop intro',
        )
        cls.targets = [
            (Article.objects.create(
                title='A', slug='a', date=today, content_markdown='x',
            ), 'content_html'),
            (Project.objects.create(
                title='P', slug='p', date=today, content_markdown='x',
            ), 'content_html'),
            (Tutorial.objects.create(title='T', slug='t', date=today), 'content_html'),
            (MarketingPage.objects.create(
                title='M', public_path='/backfill-page', content_markdown='x',
            ), 'content_html'),
            (workshop, 'description_html'),
            (WorkshopPage.objects.create(
                workshop=workshop, title='WP', slug='wp', body='x',
            ), 'body_html'),
            (Instructor.objects.create(
                instructor_id='backfill-instructor', name='I', bio='x',
            ), 'bio_html'),
            (CourseExtension.objects.get_or_create(course=course)[0],
             'peer_review_criteria_html'),
            (course, 'description_html'),
            (module, 'overview_html'),
            (unit, 'body_html'),
            (unit, 'homework_html'),
        ]
        # Stored HTML predating #1833 has no heading ids. Write it with
        # update() so save()-time rendering does not add them.
        for obj, field in cls.targets:
            type(obj).objects.filter(pk=obj.pk).update(**{field: _STORED})
        cls.untouched = Article.objects.create(
            title='No headings', slug='no-headings', date=today,
            content_markdown='x',
        )
        Article.objects.filter(pk=cls.untouched.pk).update(content_html=_NO_HEADINGS)

    def _run_backfill(self):
        schema_editor = SimpleNamespace(connection=connection)
        with redirect_stdout(StringIO()):
            _BACKFILL.backfill(django_apps, schema_editor)

    def test_every_covered_field_gains_heading_ids(self):
        self._run_backfill()

        for obj, field in self.targets:
            with self.subTest(model=type(obj).__name__, field=field):
                stored = type(obj).objects.values_list(field, flat=True).get(
                    pk=obj.pk,
                )
                self.assertEqual(stored, _EXPECTED)

    def test_rows_without_headings_stay_byte_identical(self):
        self._run_backfill()

        self.untouched.refresh_from_db()
        self.assertEqual(self.untouched.content_html, _NO_HEADINGS)

    def test_second_run_changes_nothing(self):
        self._run_backfill()
        self._run_backfill()

        article, field = self.targets[0]
        stored = Article.objects.values_list(field, flat=True).get(pk=article.pk)
        self.assertEqual(stored, _EXPECTED)
