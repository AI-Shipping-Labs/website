"""Course-unit structured code annotation contract for issue #1589."""

import html
import re

from django.test import TestCase

from content.models import Course, Module, Unit
from content.utils.code_annotations import (
    CodeAnnotation,
    CodeAnnotationError,
    parse_course_unit_body,
    render_course_unit_body,
)
from content.utils.linkify import linkify_urls
from content.utils.markdown import render_markdown

ANNOTATED_BODY = """Intro.

```python
first = 1

third = first + 2
print(third)
```
<!--
structured: true
code_annotations:
  - line: 1
    text: Set the initial value.
  - lines: "2-3"
    text: >-
      The blank line still counts before the
      calculation on line three.
-->
"""


class CourseUnitCodeAnnotationParserTest(TestCase):
    def test_valid_payload_is_stripped_and_preserves_ranges_and_note_order(self):
        parsed = parse_course_unit_body(ANNOTATED_BODY)

        self.assertNotIn('structured: true', parsed.markdown)
        self.assertIn('```python', parsed.markdown)
        self.assertEqual(
            parsed.annotations_by_block,
            ((
                CodeAnnotation(1, 1, 'Set the initial value.'),
                CodeAnnotation(
                    2,
                    3,
                    'The blank line still counts before the calculation on line three.',
                ),
            ),),
        )

    def test_quoted_top_level_keys_are_recognized_as_yaml_mapping_keys(self):
        body = (
            '```text\none\n```\n<!--\n'
            '"structured": true\n"code_annotations":\n'
            '  - line: 1\n    text: Quoted keys are valid YAML.\n-->\n'
        )

        parsed = parse_course_unit_body(body)

        self.assertEqual(
            parsed.annotations_by_block,
            ((CodeAnnotation(1, 1, 'Quoted keys are valid YAML.'),),),
        )
        self.assertNotIn('code_annotations', parsed.markdown)

    def test_nested_reserved_key_in_unrelated_comment_is_ignored(self):
        body = (
            'Before.\n\n<!--\n'
            'documentation:\n  structured: true\n'
            '-->\n'
        )

        parsed = parse_course_unit_body(body)

        self.assertEqual(parsed.markdown, body)
        self.assertEqual(parsed.annotations_by_block, ())

    def test_quoted_reserved_keys_are_rejected_in_inline_comments(self):
        for key in ('"structured"', "'code_annotations'"):
            with self.subTest(key=key):
                body = f'```text\none\n```\n<!-- {key}: true -->\n'

                with self.assertRaisesRegex(
                    CodeAnnotationError,
                    'must use a standalone multi-line HTML comment',
                ):
                    parse_course_unit_body(body)

    def test_multiple_blocks_keep_separate_annotation_groups(self):
        body = (
            '```python\na = 1\n```\n'
            '<!--\nstructured: true\ncode_annotations:\n'
            '  - line: 1\n    text: First block.\n-->\n\n'
            'Between.\n\n'
            '```bash\necho second\n```\n'
            '<!--\nstructured: true\ncode_annotations:\n'
            '  - line: 1\n    text: Second block.\n-->\n'
        )

        parsed = parse_course_unit_body(body)

        self.assertEqual(
            parsed.annotations_by_block,
            (
                (CodeAnnotation(1, 1, 'First block.'),),
                (CodeAnnotation(1, 1, 'Second block.'),),
            ),
        )

    def test_unrelated_and_superseded_comments_are_ordinary_markdown(self):
        body = (
            '```python {highlight="1"}\nprint("legacy")\n```\n\n'
            '<!-- code-annotations -->\n\n'
            '- Line 1: legacy note\n\n'
            '<!-- editorial: keep this comment -->\n'
        )

        parsed = parse_course_unit_body(body)

        self.assertEqual(parsed.markdown, body)
        self.assertEqual(parsed.annotations_by_block, (None,))

    def test_invalid_schema_association_and_ranges_are_rejected(self):
        cases = {
            'malformed YAML': (
                '```text\none\n```\n<!--\nstructured: true\n'
                'code_annotations: [unterminated\n-->\n'
            ),
            'duplicate mapping key': (
                '```text\none\n```\n<!--\nstructured: true\n'
                'structured: true\ncode_annotations:\n'
                '  - line: 1\n    text: Duplicate key.\n-->\n'
            ),
            'unclosed comment': (
                '```text\none\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 1\n    text: Missing close.\n'
            ),
            'inline candidate comment': (
                '```text\none\n```\n'
                '<!-- structured: true, code_annotations: [] -->\n'
            ),
            'missing required key': (
                '```text\none\n```\n<!--\nstructured: true\n-->\n'
            ),
            'wrong structured value': (
                '```text\none\n```\n<!--\nstructured: false\n'
                'code_annotations:\n  - line: 1\n    text: Bad marker.\n-->\n'
            ),
            'empty note': (
                '```text\none\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 1\n    text: "  "\n-->\n'
            ),
            'mixed selectors': (
                '```text\none\ntwo\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 1\n    lines: "1-2"\n'
                '    text: Ambiguous.\n-->\n'
            ),
            'unquoted range': (
                '```text\none\ntwo\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - lines: 1-2\n    text: Bad.\n-->\n'
            ),
            'overlap': (
                '```text\none\ntwo\nthree\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - lines: "1-2"\n    text: First.\n'
                '  - lines: "2-3"\n    text: Second.\n-->\n'
            ),
            'out of range': (
                '```text\none\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 2\n    text: Missing.\n-->\n'
            ),
            'prose separated': (
                '```text\none\n```\nProse.\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 1\n    text: Orphan.\n-->\n'
            ),
            'special fence': (
                '```mermaid\nflowchart LR\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 1\n    text: Invalid target.\n-->\n'
            ),
            'duplicate payload': (
                '```text\none\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 1\n    text: First.\n-->\n'
                '<!--\nstructured: true\ncode_annotations:\n'
                '  - line: 1\n    text: Duplicate.\n-->\n'
            ),
            'unknown annotation key': (
                '```text\none\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 1\n    text: Note.\n'
                '    html: true\n-->\n'
            ),
            'unsafe YAML tag': (
                '```text\none\n```\n<!--\nstructured: true\n'
                'code_annotations:\n  - line: 1\n'
                '    text: !!python/object/apply:os.system [id]\n-->\n'
            ),
        }

        for label, body in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(CodeAnnotationError):
                    parse_course_unit_body(body)


class CourseUnitCodeAnnotationRenderingTest(TestCase):
    def test_annotated_block_renders_raw_code_and_accessible_ordered_notes(self):
        rendered = render_course_unit_body(ANNOTATED_BODY)

        self.assertIn('codehilite annotated-code-block', rendered)
        self.assertIn('<span class="n">first</span>', rendered)
        self.assertIn('class="code-line-gutter" aria-hidden="true"', rendered)
        self.assertEqual(rendered.count('class="code-line-number"'), 4)
        self.assertEqual(rendered.count('is-highlighted'), 3)
        self.assertIn('<ol class="code-annotation-list">', rendered)
        self.assertLess(
            rendered.index('Line 1:'),
            rendered.index('Lines 2\N{EN DASH}3:'),
        )
        self.assertNotIn('structured: true', rendered)

        code_match = re.search(r'<code>(?P<code>.*?)</code>', rendered, re.DOTALL)
        self.assertIsNotNone(code_match)
        code_text = html.unescape(re.sub(r'<[^>]+>', '', code_match.group('code')))
        self.assertEqual(
            code_text,
            'first = 1\n\nthird = first + 2\nprint(third)',
        )
        self.assertNotIn('Set the initial value.', code_match.group('code'))

    def test_note_text_is_plain_escaped_text(self):
        body = (
            'Read https://example.com/guide first.\n\n'
            '```text\nvalue\n```\n<!--\nstructured: true\n'
            'code_annotations:\n  - line: 1\n'
            '    text: "[Guide](https://example.com/note) '
            '<img src=x onerror=alert(1)> & explain"\n-->\n'
        )

        rendered = render_course_unit_body(body)

        self.assertIn(
            '<a href="https://example.com/guide"',
            rendered,
        )
        notes_html = rendered[rendered.index('<aside'):]
        self.assertNotIn('<a ', notes_html)
        self.assertIn('[Guide](https://example.com/note)', notes_html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt; &amp; explain', rendered)
        self.assertNotIn('<img src=x', rendered)

    def test_unannotated_rendering_uses_canonical_markdown_output(self):
        body = 'Before.\n\n```python\nprint("same")\n```\n'

        self.assertEqual(
            render_course_unit_body(body),
            linkify_urls(render_markdown(body)),
        )


class UnitCodeAnnotationSaveTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(
            title='Annotation Course',
            slug='annotation-course',
            status='published',
        )
        cls.module = Module.objects.create(
            course=course,
            title='Module',
            slug='module',
        )

    def test_unit_preserves_source_body_and_derives_annotation_html(self):
        unit = Unit.objects.create(
            module=self.module,
            title='Annotated unit',
            slug='annotated-unit',
            body=ANNOTATED_BODY,
        )

        self.assertEqual(unit.body, ANNOTATED_BODY)
        self.assertIn('annotated-code-block', unit.body_html)
        self.assertNotIn('structured: true', unit.body_html)

    def test_invalid_update_does_not_replace_persisted_unit(self):
        unit = Unit.objects.create(
            module=self.module,
            title='Stable unit',
            slug='stable-unit',
            body='Previous valid lesson.',
        )
        old_html = unit.body_html
        unit.body = (
            '```text\none\n```\n<!--\nstructured: true\n'
            'code_annotations:\n  - line: 9\n    text: Invalid.\n-->\n'
        )

        with self.assertRaises(CodeAnnotationError):
            unit.save()

        persisted = Unit.objects.get(pk=unit.pk)
        self.assertEqual(persisted.body, 'Previous valid lesson.')
        self.assertEqual(persisted.body_html, old_html)
