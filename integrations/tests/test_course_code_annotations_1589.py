"""Content-sync coverage for course code annotations (issue #1589)."""

from django.test import TestCase

from content.models import Unit
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

VALID_ANNOTATED_BODY = """```python
first = 1
second = first + 1
```
<!--
structured: true
code_annotations:
  - lines: "1-2"
    text: Build the result in order.
-->
"""


class CourseCodeAnnotationSyncTest(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/code-annotations-1589',
        )
        self.repo.write_yaml('course/course.yaml', {
            'title': 'Annotated Course',
            'slug': 'annotated-course',
            'content_id': '11111111-1111-1111-1111-111111111111',
        })
        self.repo.write_yaml('course/01-module/module.yaml', {
            'title': 'Module',
        })

    def _write_unit(self, filename, content_id, body, title):
        self.repo.write_markdown(
            f'course/01-module/{filename}',
            {'title': title, 'content_id': content_id},
            body,
        )

    def test_valid_metadata_syncs_and_resyncs_idempotently(self):
        self._write_unit(
            '01-annotated.md',
            '22222222-2222-2222-2222-222222222222',
            VALID_ANNOTATED_BODY,
            'Annotated unit',
        )

        first_log = sync_repo(self.source, self.repo)
        unit = Unit.objects.get(slug='annotated')
        first_html = unit.body_html
        second_log = sync_repo(self.source, self.repo)

        unit.refresh_from_db()
        self.assertEqual(first_log.status, 'success')
        self.assertEqual(second_log.status, 'success')
        self.assertEqual(second_log.items_updated, 0)
        self.assertGreaterEqual(second_log.items_unchanged, 1)
        self.assertEqual(unit.body, VALID_ANNOTATED_BODY.rstrip('\n'))
        self.assertEqual(unit.body_html, first_html)
        self.assertIn('annotated-code-block', unit.body_html)

    def test_invalid_replacement_and_new_unit_are_reported_without_partial_apply(self):
        self._write_unit(
            '01-stable.md',
            '33333333-3333-3333-3333-333333333333',
            'Previously published lesson.\n',
            'Stable unit',
        )
        self._write_unit(
            '02-other.md',
            '44444444-4444-4444-4444-444444444444',
            'Other version one.\n',
            'Other unit',
        )
        sync_repo(self.source, self.repo)
        stable = Unit.objects.get(slug='stable')
        stable_body = stable.body
        stable_html = stable.body_html
        stable_source_path = stable.source_path

        invalid = (
            '```text\none\n```\n<!--\nstructured: true\n'
            'code_annotations:\n  - line: 4\n    text: Out of range.\n-->\n'
        )
        self.repo.remove('course/01-module/01-stable.md')
        self._write_unit(
            '04-stable-renamed.md',
            '33333333-3333-3333-3333-333333333333',
            invalid,
            'Stable unit changed',
        )
        self._write_unit(
            '02-other.md',
            '44444444-4444-4444-4444-444444444444',
            'Other version two.\n',
            'Other unit',
        )
        self._write_unit(
            '03-invalid-new.md',
            '55555555-5555-5555-5555-555555555555',
            invalid,
            'Invalid new unit',
        )

        sync_log = sync_repo(self.source, self.repo)

        stable.refresh_from_db()
        other = Unit.objects.get(slug='other')
        error_files = {error['file'] for error in sync_log.errors}
        error_text = ' '.join(error['error'] for error in sync_log.errors)
        self.assertEqual(sync_log.status, 'partial')
        self.assertIn('course/01-module/04-stable-renamed.md', error_files)
        self.assertIn('course/01-module/03-invalid-new.md', error_files)
        self.assertIn('source line', error_text)
        self.assertTrue(
            any(
                'points to line 4' in error['error']
                and 'has 1 visible lines' in error['error']
                for error in sync_log.errors
            )
        )
        self.assertEqual(stable.title, 'Stable unit')
        self.assertEqual(stable.body, stable_body)
        self.assertEqual(stable.body_html, stable_html)
        self.assertEqual(stable.source_path, stable_source_path)
        self.assertEqual(other.body, 'Other version two.')
        self.assertFalse(Unit.objects.filter(slug='invalid-new').exists())
