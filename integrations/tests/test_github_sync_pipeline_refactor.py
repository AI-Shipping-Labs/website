"""Characterization tests for the GitHub sync pipeline refactor (#603)."""

from django.test import TestCase

from content.models import Course
from integrations.models import ContentSource
from integrations.services.github import sync_content_source
from integrations.services.github_sync.orchestration import _classify_repo_files
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


class RepoFileClaimingCharacterizationTest(TestCase):
    """The classifier owns course/workshop subtrees before leaf dispatch."""

    def test_course_subtree_claims_markdown_that_looks_like_article(self):
        _source, repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/classifier-603',
        )
        repo.write_yaml('courses/python/course.yaml', {
            'title': 'Python Course',
            'slug': 'python-course',
            'content_id': '11111111-1111-1111-1111-111111111111',
        })
        repo.write_yaml('courses/python/01-intro/module.yaml', {
            'title': 'Intro',
            'content_id': '22222222-2222-2222-2222-222222222222',
        })
        repo.write_markdown(
            'courses/python/01-intro/01-lesson.md',
            {
                'title': 'Lesson',
                'date': '2026-05-13',
                'content_id': '33333333-3333-3333-3333-333333333333',
            },
            'This unit has article-shaped frontmatter.',
        )
        repo.write_markdown(
            'blog/real-article.md',
            {
                'title': 'Real Article',
                'date': '2026-05-13',
                'content_id': '44444444-4444-4444-4444-444444444444',
            },
            'Article body.',
        )

        classified = _classify_repo_files(str(repo.path))

        self.assertEqual(
            [path.replace('\\', '/') for path in classified['article_files']],
            ['blog/real-article.md'],
        )
        self.assertEqual(len(classified['course_dirs']), 1)
        self.assertTrue(
            classified['course_dirs'][0].replace('\\', '/').endswith(
                'courses/python',
            ),
        )


class CourseStaleCleanupCharacterizationTest(TestCase):
    """Course stale cleanup keeps current delete/error reporting visible."""

    def test_missing_course_soft_deletes_and_records_deleted_detail(self):
        source, repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/course-cleanup-603',
        )
        repo.write_yaml('python/course.yaml', {
            'title': 'Python Course',
            'slug': 'python-course',
            'content_id': '55555555-5555-5555-5555-555555555555',
        })
        sync_repo(source, repo)
        course = Course.objects.get(slug='python-course')

        repo.remove('python/course.yaml')
        sync_log = sync_repo(source, repo)

        course.refresh_from_db()
        self.assertEqual(course.status, 'draft')
        self.assertEqual(sync_log.items_deleted, 1)
        self.assertIn(
            {
                'title': 'Python Course',
                'slug': 'python-course',
                'action': 'deleted',
                'content_type': 'course',
                'course_id': course.pk,
                'course_slug': 'python-course',
            },
            sync_log.items_detail,
        )

    def test_unparseable_course_yaml_records_error_and_soft_deletes(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/course-failure-603',
        )
        _unused, repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/course-failure-603-repo',
        )
        repo.write_yaml('python/course.yaml', {
            'title': 'Python Course',
            'slug': 'python-course',
            'content_id': '66666666-6666-6666-6666-666666666666',
        })
        sync_content_source(source, repo_dir=str(repo.path))
        course = Course.objects.get(slug='python-course')

        repo.write_text('python/course.yaml', 'slug: [unterminated\n')
        sync_log = sync_content_source(source, repo_dir=str(repo.path))

        course.refresh_from_db()
        self.assertEqual(course.status, 'draft')
        self.assertEqual(sync_log.status, 'partial')
        self.assertTrue(sync_log.errors)
        self.assertIn('course.yaml', sync_log.errors[0]['file'])
