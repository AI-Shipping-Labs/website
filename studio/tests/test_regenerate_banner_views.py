"""Tests for the Studio Regenerate Banner POST endpoints (issue #788).

Covers access gating (anonymous / non-staff / staff), the GET → 405
guard, that staff POSTs enqueue exactly one task, and that the edit
page renders the placeholder / image / disabled-button states.
"""

import datetime as dt
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.models import Article, Course, Download, Project, Workshop
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


class _BannerGeneratorCacheCleanupMixin:
    """Clear the in-process config cache before and after each test."""

    def setUp(self):
        super().setUp()
        env_patch = patch.dict(os.environ, {
            'BANNER_GENERATOR_FUNCTION_URL': '',
            'BANNER_GENERATOR_AUTH_TOKEN': '',
            'AWS_S3_CONTENT_BUCKET': '',
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)
        clear_config_cache()
        self.addCleanup(clear_config_cache)

User = get_user_model()

ENQUEUE_PATCH = (
    'studio.views.banner_regenerate.enqueue_force'
)
DISPATCH_ASYNC_PATCH = (
    'integrations.services.banner_generator.dispatch.async_task'
)


def _set_banner_generator(enabled=True):
    for key, value in (
        ('BANNER_GENERATOR_FUNCTION_URL', 'https://lambda.example.com/'),
        ('BANNER_GENERATOR_AUTH_TOKEN', 'token-abc'),
        ('AWS_S3_CONTENT_BUCKET', 'content-bucket'),
        ('CONTENT_CDN_BASE', 'https://cdn.example.com'),
    ):
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={
                'value': value if enabled else '',
                'is_secret': False,
                'group': 'banner_generator',
                'description': '',
            },
        )
    if not enabled:
        IntegrationSetting.objects.filter(
            key__startswith='BANNER_GENERATOR_',
        ).delete()
    clear_config_cache()


def _make_article(**overrides):
    defaults = {
        'title': 'A title',
        'slug': 'a-title',
        'date': dt.date(2026, 1, 1),
    }
    defaults.update(overrides)
    return Article.objects.create(**defaults)


def _make_course():
    return Course.objects.create(
        title='Course', slug='course', status='published',
    )


def _make_project():
    return Project.objects.create(
        title='Project', slug='project', date=dt.date(2026, 1, 1),
    )


def _make_download():
    return Download.objects.create(
        title='DL', slug='dl', file_url='https://example.com/x.pdf',
    )


def _make_workshop():
    return Workshop.objects.create(
        slug='ws', title='WS', date=dt.date(2026, 4, 13),
        pages_required_level=5, recording_required_level=20,
    )


class RegenerateBannerAccessTest(_BannerGeneratorCacheCleanupMixin, TestCase):
    """Access matrix per content type — staff vs non-staff vs anonymous."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.normal = User.objects.create_user(
            email='user@test.com', password='testpass',
        )
        _set_banner_generator(enabled=True)

    def _per_type_urls(self):
        return [
            ('article', f'/studio/articles/{_make_article().pk}/regenerate-banner'),
            ('course', f'/studio/courses/{_make_course().pk}/regenerate-banner'),
            ('project', f'/studio/projects/{_make_project().pk}/regenerate-banner'),
            ('download', f'/studio/downloads/{_make_download().pk}/regenerate-banner'),
            ('workshop', f'/studio/workshops/{_make_workshop().pk}/regenerate-banner'),
        ]

    def test_anonymous_post_redirects_to_login(self):
        for name, url in self._per_type_urls():
            with self.subTest(content_type=name):
                response = self.client.post(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response.url)

    def test_non_staff_post_returns_403(self):
        self.client.login(email='user@test.com', password='testpass')
        for name, url in self._per_type_urls():
            with self.subTest(content_type=name):
                response = self.client.post(url)
                self.assertEqual(response.status_code, 403)

    @patch(ENQUEUE_PATCH)
    def test_staff_post_enqueues_one_task_and_redirects(self, mock_enqueue):
        mock_enqueue.return_value = 'task-id-x'
        self.client.login(email='staff@test.com', password='testpass')
        for name, url in self._per_type_urls():
            with self.subTest(content_type=name):
                mock_enqueue.reset_mock()
                response = self.client.post(url)
                self.assertEqual(response.status_code, 302)
                mock_enqueue.assert_called_once()

    def test_get_returns_405(self):
        self.client.login(email='staff@test.com', password='testpass')
        for name, url in self._per_type_urls():
            with self.subTest(content_type=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 405)


class RegenerateBannerNoOpWhenDisabledTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        _set_banner_generator(enabled=False)

    @patch(DISPATCH_ASYNC_PATCH)
    def test_staff_post_does_not_enqueue_when_disabled(self, mock_async):
        article = _make_article()
        response = self.client.post(
            f'/studio/articles/{article.pk}/regenerate-banner',
        )
        self.assertEqual(response.status_code, 302)
        mock_async.assert_not_called()


class ArticleEditBannerSectionTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_placeholder_shown_when_no_banner(self):
        _set_banner_generator(enabled=True)
        article = _make_article()
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="banner-generator-section"')
        self.assertContains(response, 'data-testid="banner-generator-placeholder"')
        self.assertContains(response, 'No banner generated yet')

    def test_image_shown_when_banner_url_set(self):
        _set_banner_generator(enabled=True)
        article = _make_article()
        Article.objects.filter(pk=article.pk).update(
            auto_banner_url='https://cdn.example.com/banners/article/x.jpg',
        )
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-image"')
        self.assertContains(
            response,
            'src="https://cdn.example.com/banners/article/x.jpg"',
        )

    def test_regenerate_button_disabled_when_function_url_unset(self):
        _set_banner_generator(enabled=False)
        article = _make_article()
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(
            response,
            'data-testid="banner-generator-regenerate-button-disabled"',
        )
        self.assertContains(
            response, 'Configure banner generator in Settings to enable',
        )
        self.assertContains(response, '/studio/settings/#content_tools')

    def test_regenerate_button_enabled_when_configured(self):
        _set_banner_generator(enabled=True)
        article = _make_article()
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(
            response, 'data-testid="banner-generator-regenerate-button"',
        )
        self.assertNotContains(
            response,
            'data-testid="banner-generator-regenerate-button-disabled"',
        )


class TokenSafetyInRenderedPageTest(_BannerGeneratorCacheCleanupMixin, TestCase):
    """Make sure the bearer token never reaches HTML output."""

    SECRET = 'super-secret-do-not-leak-token'

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        # Configure with a recognisable token then mark it secret.
        IntegrationSetting.objects.update_or_create(
            key='BANNER_GENERATOR_FUNCTION_URL',
            defaults={
                'value': 'https://lambda.example.com/',
                'is_secret': False, 'group': 'banner_generator', 'description': '',
            },
        )
        IntegrationSetting.objects.update_or_create(
            key='BANNER_GENERATOR_AUTH_TOKEN',
            defaults={
                'value': self.SECRET,
                'is_secret': True, 'group': 'banner_generator', 'description': '',
            },
        )
        clear_config_cache()

    def test_token_not_in_article_edit_page(self):
        article = _make_article()
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertNotIn(
            self.SECRET, response.content.decode('utf-8'),
        )

    def test_token_not_in_course_edit_page(self):
        course = _make_course()
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertNotIn(
            self.SECRET, response.content.decode('utf-8'),
        )

    def test_token_not_in_workshop_edit_page(self):
        workshop = _make_workshop()
        response = self.client.get(f'/studio/workshops/{workshop.pk}/edit')
        self.assertNotIn(
            self.SECRET, response.content.decode('utf-8'),
        )
