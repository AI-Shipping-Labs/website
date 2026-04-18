"""Integration test: course sync rewrites intra-content `.md` links (issue #226).

Synthesises a tiny course on disk with two units and one cross-module link,
runs the real sync, and asserts the rendered ``Unit.body_html`` contains
platform URLs (no raw ``.md`` references) for the rewriteable cases.
"""

import os
import shutil
import tempfile

from django.test import TestCase

from content.models import Course, Unit
from integrations.models import ContentSource
from integrations.services.github import sync_content_source


class CourseSyncMdLinkRewriteTest(TestCase):
    """Run the real sync and verify links land as platform URLs."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/courses',
            content_type='course',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        path = os.path.join(self.temp_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)

    def _build_course(self):
        """Create one course with two modules and a link from intro -> setup
        plus a cross-module link, an external link, and one broken link."""
        self._write(
            'python-course/course.yaml',
            (
                'title: "Python Course"\n'
                'slug: "python-course"\n'
                'description: "Learn Python"\n'
                'instructor_name: "Test"\n'
                'required_level: 0\n'
                'content_id: "11111111-1111-1111-1111-111111111111"\n'
            ),
        )
        # Module 1: fundamentals
        self._write(
            'python-course/01-fundamentals/module.yaml',
            'title: "Fundamentals"\nsort_order: 1\n',
        )
        self._write(
            'python-course/01-fundamentals/01-intro.md',
            (
                '---\n'
                'title: "Intro"\n'
                'sort_order: 1\n'
                'content_id: "22222222-2222-2222-2222-222222222222"\n'
                '---\n'
                'Welcome. Continue with [Setup](02-setup.md).\n'
                'Or jump to [Deploy](../02-advanced/01-deploy.md).\n'
                'See the [docs](https://example.com/docs).\n'
                'Coming soon: [Future](99-not-yet.md).\n'
            ),
        )
        self._write(
            'python-course/01-fundamentals/02-setup.md',
            (
                '---\n'
                'title: "Setup"\n'
                'sort_order: 2\n'
                'content_id: "33333333-3333-3333-3333-333333333333"\n'
                '---\n'
                'Setup body.\n'
            ),
        )
        # Module 2: advanced
        self._write(
            'python-course/02-advanced/module.yaml',
            'title: "Advanced"\nsort_order: 2\n',
        )
        self._write(
            'python-course/02-advanced/01-deploy.md',
            (
                '---\n'
                'title: "Deploy"\n'
                'sort_order: 1\n'
                'content_id: "44444444-4444-4444-4444-444444444444"\n'
                '---\n'
                'Deploy body.\n'
            ),
        )

    def test_sync_rewrites_md_links_in_unit_body_html(self):
        self._build_course()
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertIn(sync_log.status, ('success', 'partial'))

        course = Course.objects.get(slug='python-course')
        intro = Unit.objects.get(
            module__course=course, slug='intro',
        )

        # Sibling link rewritten.
        self.assertIn(
            'href="/courses/python-course/fundamentals/setup"',
            intro.body_html,
        )
        # Cross-module link rewritten (numeric prefix on module dir name
        # in the link is stripped because Module.slug strips it).
        self.assertIn(
            'href="/courses/python-course/advanced/deploy"',
            intro.body_html,
        )
        # External link untouched.
        self.assertIn('href="https://example.com/docs"', intro.body_html)
        # Unresolvable link left as-is — the rendered href is the original
        # `.md` filename, and a warning was logged to the SyncLog.
        self.assertIn('99-not-yet.md', intro.body_html)
        self.assertTrue(
            any('99-not-yet.md' in (e.get('error') or '')
                for e in (sync_log.errors or [])),
            f'Expected unresolvable-link warning in sync log; got '
            f'{sync_log.errors!r}',
        )
