"""Tests for cover image URL rewriting during content sync -- issue #155.

Covers:
- rewrite_cover_image_url helper function (relative, absolute, empty paths)
- Integration with _sync_articles, _sync_courses, _sync_projects
"""

import os
import shutil
import tempfile
import uuid

from django.test import TestCase, override_settings

from content.models import Article, Course, Project
from integrations.models import ContentSource
from integrations.services.github import (
    rewrite_cover_image_url,
    sync_content_source,
)


class RewriteCoverImageUrlTest(TestCase):
    """Test the rewrite_cover_image_url helper function."""

    @classmethod
    def setUpTestData(cls):
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            webhook_secret='secret',
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_relative_path_rewritten_to_cdn_url(self):
        result = rewrite_cover_image_url(
            'images/cover.jpg', self.source, 'blog/my-post.md',
        )
        self.assertEqual(result, 'https://cdn.example.com/content/blog/images/cover.jpg')

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_full_url_used_as_is(self):
        url = 'https://example.com/images/cover.jpg'
        result = rewrite_cover_image_url(url, self.source, 'blog/my-post.md')
        self.assertEqual(result, url)

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_http_url_used_as_is(self):
        url = 'http://example.com/images/cover.jpg'
        result = rewrite_cover_image_url(url, self.source, 'blog/my-post.md')
        self.assertEqual(result, url)

    def test_empty_string_returns_empty(self):
        result = rewrite_cover_image_url('', self.source, 'blog/my-post.md')
        self.assertEqual(result, '')

    def test_none_returns_empty(self):
        result = rewrite_cover_image_url(None, self.source, 'blog/my-post.md')
        self.assertEqual(result, '')

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_leading_slash_stripped(self):
        result = rewrite_cover_image_url(
            '/images/cover.jpg', self.source, 'blog/my-post.md',
        )
        self.assertEqual(result, 'https://cdn.example.com/content/blog/images/cover.jpg')

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_path_normalization_removes_dotdot(self):
        result = rewrite_cover_image_url(
            '../images/cover.jpg', self.source, 'blog/posts/my-post.md',
        )
        self.assertEqual(result, 'https://cdn.example.com/content/blog/images/cover.jpg')

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_top_level_file_path(self):
        result = rewrite_cover_image_url(
            'cover.jpg', self.source, 'my-post.md',
        )
        self.assertEqual(result, 'https://cdn.example.com/content/cover.jpg')


class ArticleCoverImageSyncTest(TestCase):
    """Test that article sync rewrites cover_image from frontmatter."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_article(self, filename, frontmatter_dict, body='Article body.'):
        filepath = os.path.join(self.temp_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if 'content_id' not in frontmatter_dict:
            frontmatter_dict['content_id'] = str(uuid.uuid4())
        # date: is required for the walker (issue #310) to bucket the
        # file as an article. Default it here when the test didn't set
        # one explicitly.
        if 'date' not in frontmatter_dict:
            frontmatter_dict['date'] = '2026-04-24'
        lines = ['---']
        for key, value in frontmatter_dict.items():
            if isinstance(value, list):
                lines.append(f'{key}:')
                for item in value:
                    lines.append(f'  - "{item}"')
            else:
                lines.append(f'{key}: "{value}"')
        lines.append('---')
        lines.append(body)
        with open(filepath, 'w') as f:
            f.write('\n'.join(lines))

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_relative_cover_image_rewritten(self):
        self._write_article('test-post.md', {
            'title': 'Test Article',
            'slug': 'test-post',
            'cover_image': 'images/hero.jpg',
        })
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        article = Article.objects.get(slug='test-post')
        self.assertEqual(
            article.cover_image_url,
            'https://cdn.example.com/content/images/hero.jpg',
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_full_url_cover_image_unchanged(self):
        self._write_article('test-post.md', {
            'title': 'Test Article',
            'slug': 'test-post',
            'cover_image': 'https://cdn.aishippinglabs.com/blog/hero.jpg',
        })
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        article = Article.objects.get(slug='test-post')
        self.assertEqual(
            article.cover_image_url,
            'https://cdn.aishippinglabs.com/blog/hero.jpg',
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_missing_cover_image_stays_empty(self):
        self._write_article('test-post.md', {
            'title': 'Test Article',
            'slug': 'test-post',
        })
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        article = Article.objects.get(slug='test-post')
        self.assertEqual(article.cover_image_url, '')


class CourseCoverImageSyncTest(TestCase):
    """Test that course sync rewrites cover_image from frontmatter."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_course_yaml(self, slug, cover_image_line=''):
        course_dir = os.path.join(self.temp_dir, slug)
        os.makedirs(course_dir, exist_ok=True)
        content_id = str(uuid.uuid4())
        content = f"""title: Test Course
content_id: {content_id}
slug: {slug}
{cover_image_line}
"""
        filepath = os.path.join(course_dir, 'course.yaml')
        with open(filepath, 'w') as f:
            f.write(content)

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_relative_cover_image_rewritten(self):
        self._write_course_yaml(
            'test-course', 'cover_image: images/course-cover.png',
        )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        course = Course.objects.get(slug='test-course')
        self.assertEqual(
            course.cover_image_url,
            'https://cdn.example.com/content/test-course/images/course-cover.png',
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_full_url_cover_image_unchanged(self):
        self._write_course_yaml(
            'test-course',
            'cover_image: https://cdn.aishippinglabs.com/courses/cover.png',
        )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        course = Course.objects.get(slug='test-course')
        self.assertEqual(
            course.cover_image_url,
            'https://cdn.aishippinglabs.com/courses/cover.png',
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_missing_cover_image_stays_empty(self):
        self._write_course_yaml('test-course')
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        course = Course.objects.get(slug='test-course')
        self.assertEqual(course.cover_image_url, '')


class ProjectCoverImageSyncTest(TestCase):
    """Test that project sync rewrites cover_image from frontmatter."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_project(self, filename, frontmatter_dict, body='Project body.'):
        filepath = os.path.join(self.temp_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if 'content_id' not in frontmatter_dict:
            frontmatter_dict['content_id'] = str(uuid.uuid4())
        # Issue #310: the walker classifies a markdown file as a project
        # when it carries a ``difficulty:`` field (independent of the
        # author/title check). Default it so legacy fixtures still route.
        if 'difficulty' not in frontmatter_dict:
            frontmatter_dict['difficulty'] = 'beginner'
        lines = ['---']
        for key, value in frontmatter_dict.items():
            if isinstance(value, list):
                lines.append(f'{key}:')
                for item in value:
                    lines.append(f'  - "{item}"')
            else:
                lines.append(f'{key}: "{value}"')
        lines.append('---')
        lines.append(body)
        with open(filepath, 'w') as f:
            f.write('\n'.join(lines))

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_relative_cover_image_rewritten(self):
        self._write_project('test-project.md', {
            'title': 'Test Project',
            'slug': 'test-project',
            'cover_image': 'images/project.jpg',
        })
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        project = Project.objects.get(slug='test-project')
        self.assertEqual(
            project.cover_image_url,
            'https://cdn.example.com/content/images/project.jpg',
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_full_url_cover_image_unchanged(self):
        self._write_project('test-project.md', {
            'title': 'Test Project',
            'slug': 'test-project',
            'cover_image': 'https://cdn.aishippinglabs.com/projects/cover.jpg',
        })
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        project = Project.objects.get(slug='test-project')
        self.assertEqual(
            project.cover_image_url,
            'https://cdn.aishippinglabs.com/projects/cover.jpg',
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_missing_cover_image_stays_empty(self):
        self._write_project('test-project.md', {
            'title': 'Test Project',
            'slug': 'test-project',
        })
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success', f'Sync errors: {sync_log.errors}')

        project = Project.objects.get(slug='test-project')
        self.assertEqual(project.cover_image_url, '')
