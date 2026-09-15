"""Fixture-backed sync coverage for the buildcamp content source (#1661).

``AI-Shipping-Labs/ai-buildcamp-course`` is registered as a content source
in ``seed_content_sources.py`` alongside the three existing repos. Grooming
established the parser and reader templates already handle every content
shape the converted v2 markdown produces — this file proves that against a
small fixture repo shaped like the converted output (flat module dirs,
following the ``AI-Shipping-Labs/python-course`` precedent):

- a unit with a ``https://www.loom.com/share/...`` ``video_url``
- a unit with no ``video_url`` key at all (the ``[VIDEO COMING SOON]`` case)
- a unit with ``is_homework: true`` and no ``video_url``
- a module directory with a ``README.md`` overview

No parser or template code changes are required for this issue; these are
pre-existing code paths exercised here for the first time against this
specific content shape.
"""

from io import StringIO

from community_base.content_sync.models import (
    ContentSource as PackageContentSource,
)
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from content.models import Course, Module, Unit
from content.templatetags.video_utils import prepare_video_context
from content.utils.code_annotations import CodeAnnotationError, parse_course_unit_body
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

BUILDCAMP_REPO = 'AI-Shipping-Labs/ai-buildcamp-course'

COURSE_CONTENT_ID = '9a1f6e10-0000-4000-8000-000000000001'
LOOM_UNIT_CONTENT_ID = '9a1f6e10-0000-4000-8000-000000000002'
NO_VIDEO_UNIT_CONTENT_ID = '9a1f6e10-0000-4000-8000-000000000003'
HOMEWORK_UNIT_CONTENT_ID = '9a1f6e10-0000-4000-8000-000000000004'

# Reused from integrations/tests/test_course_code_annotations_1589.py — a
# code block followed by an out-of-range structured-annotation comment.
# ``parse_course_unit_body`` raises ``CodeAnnotationError`` on this body;
# homework units must skip that validation entirely.
INVALID_CODE_ANNOTATION_BODY = (
    '```text\none\n```\n<!--\nstructured: true\n'
    'code_annotations:\n  - line: 4\n    text: Out of range.\n-->\n'
)


class _BuildcampFixtureRepoTest(TestCase):
    """Assembles a python-course-shaped repo standing in for the converted
    ``ai-buildcamp-course`` output: root ``course.yaml``, one flat module
    dir with a ``README.md`` overview and three units covering the Loom
    video, missing-video, and homework shapes."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/ai-buildcamp-course',
            is_private=True,
            prefix='buildcamp-sync-',
        )
        self.repo.write_yaml('course.yaml', {
            'content_id': COURSE_CONTENT_ID,
            'title': 'AI Engineering Buildcamp',
            'slug': 'ai-buildcamp',
            'description': 'Cohort-based AI engineering course.',
            'required_level': 0,
        })
        self.repo.write_yaml('01-foundation/module.yaml', {
            'title': 'Foundation',
        })
        self.repo.write_text(
            '01-foundation/README.md',
            '# Foundation\n\nWelcome to the foundation module overview.\n',
        )
        self.repo.write_markdown(
            '01-foundation/01-loom-lesson.md',
            {
                'content_id': LOOM_UNIT_CONTENT_ID,
                'title': 'Loom Lesson',
                'video_url': 'https://www.loom.com/share/abc123def4567890',
            },
            '# Loom Lesson\n\nLesson body for the recorded lesson.\n',
        )
        self.repo.write_markdown(
            '01-foundation/02-coming-soon.md',
            {'content_id': NO_VIDEO_UNIT_CONTENT_ID, 'title': 'Coming Soon Lesson'},
            '# Coming Soon Lesson\n\nLesson body; video not recorded yet.\n',
        )
        self.repo.write_markdown(
            '01-foundation/03-homework.md',
            {
                'content_id': HOMEWORK_UNIT_CONTENT_ID,
                'title': 'Homework',
                'is_homework': True,
            },
            '# Homework\n\n' + INVALID_CODE_ANNOTATION_BODY,
        )

        self.sync_log = sync_repo(self.source, self.repo)
        self.course = Course.objects.get(content_id=COURSE_CONTENT_ID)
        self.module = Module.objects.get(course=self.course, slug='foundation')

        # The course syncs at LEVEL_OPEN, but the unit-page view still
        # nudges anonymous visitors to sign in (legacy behavior); log in a
        # plain user so the render-level assertions exercise the unit page
        # body instead of the sign-in gate.
        self.user = get_user_model().objects.create_user(
            email='learner@example.com', password='pw',
        )
        self.client.force_login(self.user)


class BuildcampSyncHappyPathTest(_BuildcampFixtureRepoTest):
    def test_sync_reports_no_errors(self):
        self.assertEqual(
            self.sync_log.errors, [],
            f'Expected no errors, got: {self.sync_log.errors}',
        )

    def test_module_and_units_created(self):
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(Module.objects.count(), 1)
        # README does not become a Unit — only the three lesson files do.
        self.assertEqual(Unit.objects.filter(module=self.module).count(), 3)


class BuildcampLoomVideoUnitTest(_BuildcampFixtureRepoTest):
    """A unit with a Loom share URL as video_url."""

    def test_video_url_synced_verbatim(self):
        unit = Unit.objects.get(content_id=LOOM_UNIT_CONTENT_ID)
        self.assertEqual(
            unit.video_url, 'https://www.loom.com/share/abc123def4567890',
        )

    def test_unit_page_video_context_resolves_loom_embed(self):
        unit = Unit.objects.get(content_id=LOOM_UNIT_CONTENT_ID)
        context = prepare_video_context(unit.video_url, unit.timestamps)
        self.assertEqual(context['source_type'], 'loom')
        self.assertIsNotNone(context['embed_url'])
        self.assertIn('loom.com/embed/', context['embed_url'])


class BuildcampMissingVideoUnitTest(_BuildcampFixtureRepoTest):
    """A unit with no video_url key — the converted [VIDEO COMING SOON] case."""

    def test_video_url_is_blank(self):
        unit = Unit.objects.get(content_id=NO_VIDEO_UNIT_CONTENT_ID)
        self.assertEqual(unit.video_url, '')

    def test_unit_page_renders_with_no_video_block(self):
        unit = Unit.objects.get(content_id=NO_VIDEO_UNIT_CONTENT_ID)
        url = f'/courses/{self.course.slug}/{self.module.slug}/{unit.slug}'
        response = self.client.get(url)

        self.assertContains(response, 'Coming Soon Lesson')
        self.assertContains(response, 'video not recorded yet')
        self.assertNotContains(response, '<iframe')


class BuildcampHomeworkUnitTest(_BuildcampFixtureRepoTest):
    """A unit with is_homework: true and no video_url."""

    def test_body_written_to_homework_not_body(self):
        unit = Unit.objects.get(content_id=HOMEWORK_UNIT_CONTENT_ID)
        self.assertEqual(unit.body, '')
        self.assertIn('Homework', unit.homework)
        self.assertIn('```text', unit.homework)

    def test_lesson_annotation_validation_is_skipped(self):
        # The raw body would fail structured-annotation validation as a
        # lesson body (out-of-range annotation line) — confirm that, then
        # confirm the sync (which treats it as homework) still succeeded.
        with self.assertRaises(CodeAnnotationError):
            parse_course_unit_body(INVALID_CODE_ANNOTATION_BODY)
        self.assertEqual(self.sync_log.errors, [])
        unit = Unit.objects.get(content_id=HOMEWORK_UNIT_CONTENT_ID)
        self.assertTrue(unit.homework_html)

    def test_unit_page_renders_homework_card_no_video_block(self):
        unit = Unit.objects.get(content_id=HOMEWORK_UNIT_CONTENT_ID)
        url = f'/courses/{self.course.slug}/{self.module.slug}/{unit.slug}'
        response = self.client.get(url)

        self.assertContains(response, 'Homework')
        self.assertNotContains(response, '<iframe')
        self.assertNotContains(response, 'data-testid="course-unit-body"')


class BuildcampModuleReadmeOverviewTest(_BuildcampFixtureRepoTest):
    """A module directory with a README.md."""

    def test_module_overview_and_html_populated(self):
        self.assertIn('Foundation', self.module.overview)
        self.assertIn('Welcome to the foundation module overview', self.module.overview_html)
        self.assertEqual(
            self.module.overview_source_path, '01-foundation/README.md',
        )

    def test_readme_is_not_synced_as_a_unit(self):
        self.assertFalse(
            Unit.objects.filter(module=self.module, title__icontains='Foundation').exists()
        )
        slugs = set(Unit.objects.filter(module=self.module).values_list('slug', flat=True))
        self.assertNotIn('readme', slugs)

    def test_module_overview_page_renders(self):
        url = f'/courses/{self.course.slug}/{self.module.slug}'
        response = self.client.get(url)
        self.assertContains(response, 'Welcome to the foundation module overview')


class SeedBuildcampContentSourceTest(TestCase):
    """``seed_content_sources`` registration of the buildcamp repo.

    The broader "all four rows created/idempotent" behavior is covered by
    ``integrations/tests/test_github_sync.py::SeedContentSourcesCommandTest``;
    this class asserts the specific fields and disabled-reporting text for
    ``AI-Shipping-Labs/ai-buildcamp-course``.
    """

    def test_creates_disabled_row_with_expected_fields(self):
        out = StringIO()
        call_command('seed_content_sources', stdout=out)

        source = PackageContentSource.objects.get(repo_name=BUILDCAMP_REPO)
        self.assertEqual(source.slug, 'ai-buildcamp-course')
        self.assertTrue(source.is_private)
        self.assertEqual(source.max_files, 5000)
        # No legacy webhook secret exists in a fresh test DB, so the new
        # source is created disabled — same behavior as the other three
        # sources when they're first seeded.
        self.assertFalse(source.is_enabled)
        self.assertIn(f'DISABLED: {BUILDCAMP_REPO}', out.getvalue())

    def test_second_run_is_idempotent(self):
        call_command('seed_content_sources', stdout=StringIO())
        first = PackageContentSource.objects.get(repo_name=BUILDCAMP_REPO).pk

        call_command('seed_content_sources', stdout=StringIO())
        self.assertEqual(
            PackageContentSource.objects.filter(repo_name=BUILDCAMP_REPO).count(), 1,
        )
        self.assertEqual(
            PackageContentSource.objects.get(repo_name=BUILDCAMP_REPO).pk, first,
        )
