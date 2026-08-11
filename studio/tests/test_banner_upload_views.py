"""Tests for the Studio custom-banner upload/remove endpoints (issue #931).

Covers the access matrix (anonymous / non-staff / staff), the GET -> 405
guard, the upload happy path (S3 mocked) persisting ``custom_banner_url``,
the remove path clearing it, validation rejections leaving the record
untouched, the disabled-when-unconfigured behavior, and the shared panel's
source badge + Remove control.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from content.models import Article, Course, Download, Project, Workshop
from events.models import Event, EventSeries
from integrations.config import clear_config_cache

User = get_user_model()

CDN_BASE = 'https://cdn.example.com'
BUCKET = 'content-bucket'
GENERATED_URL = f'{CDN_BASE}/banners/article/generated.jpg'
S3_CLIENT_PATH = (
    'integrations.services.banner_generator.custom_upload._s3_client'
)

# ``CONTENT_CDN_BASE`` and ``AWS_S3_CONTENT_BUCKET`` are Django settings that
# get_config consults before the process env, so the enabled/disabled gate is
# driven via @override_settings rather than env or DB rows. AWS_S3_CONTENT_*
# credentials are not set so _s3_client() is always mocked in these tests.
enabled_config = override_settings(
    CONTENT_CDN_BASE=CDN_BASE,
    AWS_S3_CONTENT_BUCKET=BUCKET,
    AWS_S3_CONTENT_REGION='eu-west-1',
)
disabled_config = override_settings(
    CONTENT_CDN_BASE='',
    AWS_S3_CONTENT_BUCKET='',
)


def _png(name='b.png', size=1024):
    return SimpleUploadedFile(name, b'x' * size, content_type='image/png')


class _ConfigCleanupMixin:
    def setUp(self):
        super().setUp()
        clear_config_cache()
        self.addCleanup(clear_config_cache)


def _make_article():
    return Article.objects.create(
        title='A', slug='a', date=dt.date(2026, 1, 1),
    )


def _make_course():
    return Course.objects.create(title='C', slug='c', status='published')


def _make_project():
    return Project.objects.create(
        title='P', slug='p', date=dt.date(2026, 1, 1),
    )


def _make_download():
    return Download.objects.create(
        title='D', slug='d', file_url='https://example.com/x.pdf',
    )


def _make_workshop():
    return Workshop.objects.create(
        slug='w', title='W', date=dt.date(2026, 4, 13),
        pages_required_level=5, recording_required_level=20,
    )


def _make_event():
    return Event.objects.create(
        title='E', slug='e',
        start_datetime=dt.datetime(2026, 5, 1, 18, tzinfo=dt.timezone.utc),
        status='upcoming',
    )


def _make_series():
    return EventSeries.objects.create(
        name='S', slug='s', day_of_week=2,
        start_time=dt.time(18, 0), timezone='Europe/Berlin',
    )


def _set_banner_state(record, content_type):
    """Give a record distinct cover/custom/generated values for mutation tests."""
    values = {
        'custom_banner_url': (
            f'{CDN_BASE}/custom-banners/{content_type}/{record.pk}-custom.png'
        ),
        'auto_banner_url': (
            f'{CDN_BASE}/banners/{content_type}/{record.pk}-generated.jpg'
        ),
    }
    if hasattr(record, 'cover_image_url'):
        values['cover_image_url'] = (
            f'{CDN_BASE}/content-repo/{content_type}/{record.pk}-cover.png'
        )
    type(record).objects.filter(pk=record.pk).update(**values)
    record.refresh_from_db()
    return values


def _assert_banner_state(test_case, record, expected):
    record.refresh_from_db()
    for field, value in expected.items():
        test_case.assertEqual(getattr(record, field), value)


def _remove_cases():
    return [
        (
            'article', _make_article(), 'studio_article_remove_banner',
            'article_id', 'studio_article_edit',
        ),
        (
            'course', _make_course(), 'studio_course_remove_banner',
            'course_id', 'studio_course_edit',
        ),
        (
            'project', _make_project(), 'studio_project_remove_banner',
            'project_id', 'studio_project_review',
        ),
        (
            'download', _make_download(), 'studio_download_remove_banner',
            'download_id', 'studio_download_edit',
        ),
        (
            'workshop', _make_workshop(), 'studio_workshop_remove_banner',
            'workshop_id', 'studio_workshop_edit',
        ),
        (
            'event', _make_event(), 'studio_event_remove_banner',
            'event_id', 'studio_event_edit',
        ),
        (
            'event_series', _make_series(),
            'studio_event_series_remove_banner', 'series_id',
            'studio_event_series_detail',
        ),
    ]


@enabled_config
class UploadAccessTest(_ConfigCleanupMixin, TestCase):
    """Access matrix across all 7 content types for upload + remove."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        User.objects.create_user(email='user@test.com', password='pw')

    def _upload_urls(self):
        return [
            f'/studio/articles/{_make_article().pk}/upload-banner',
            f'/studio/courses/{_make_course().pk}/upload-banner',
            f'/studio/projects/{_make_project().pk}/upload-banner',
            f'/studio/downloads/{_make_download().pk}/upload-banner',
            f'/studio/workshops/{_make_workshop().pk}/upload-banner',
            f'/studio/events/{_make_event().pk}/upload-banner',
            f'/studio/event-series/{_make_series().pk}/upload-banner',
        ]

    def test_anonymous_upload_redirects_to_login(self):
        for url in self._upload_urls():
            with self.subTest(url=url):
                response = self.client.post(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response.url)

    def test_anonymous_remove_redirects_without_mutation(self):
        for content_type, record, url_name, id_kwarg, _ in _remove_cases():
            expected = _set_banner_state(record, content_type)
            url = reverse(url_name, kwargs={id_kwarg: record.pk})
            with self.subTest(content_type=content_type):
                response = self.client.post(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response.url)
                _assert_banner_state(self, record, expected)

    def test_non_staff_upload_returns_403(self):
        self.client.login(email='user@test.com', password='pw')
        for url in self._upload_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 403)

    def test_non_staff_remove_returns_403(self):
        self.client.login(email='user@test.com', password='pw')
        for content_type, record, url_name, id_kwarg, _ in _remove_cases():
            expected = _set_banner_state(record, content_type)
            url = reverse(url_name, kwargs={id_kwarg: record.pk})
            with self.subTest(content_type=content_type):
                self.assertEqual(self.client.post(url).status_code, 403)
                _assert_banner_state(self, record, expected)

    def test_get_upload_returns_405(self):
        self.client.login(email='staff@test.com', password='pw')
        for url in self._upload_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_get_remove_returns_405(self):
        self.client.login(email='staff@test.com', password='pw')
        for content_type, record, url_name, id_kwarg, _ in _remove_cases():
            expected = _set_banner_state(record, content_type)
            url = reverse(url_name, kwargs={id_kwarg: record.pk})
            with self.subTest(content_type=content_type):
                self.assertEqual(self.client.get(url).status_code, 405)
                _assert_banner_state(self, record, expected)

    def test_remove_requires_csrf_token_without_mutation(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email='staff@test.com', password='pw')
        article = _make_article()
        expected = _set_banner_state(article, 'article')

        response = csrf_client.post(
            f'/studio/articles/{article.pk}/remove-banner',
        )

        self.assertEqual(response.status_code, 403)
        _assert_banner_state(self, article, expected)


@enabled_config
class UploadFlowTest(_ConfigCleanupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='pw')

    @patch(S3_CLIENT_PATH)
    def test_upload_persists_custom_banner_url(self, mock_client):
        mock_client.return_value = MagicMock()
        article = _make_article()
        response = self.client.post(
            f'/studio/articles/{article.pk}/upload-banner',
            {'banner_image': _png()},
        )
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertTrue(
            article.custom_banner_url.startswith(
                f'{CDN_BASE}/custom-banners/article/{article.pk}-',
            ),
            article.custom_banner_url,
        )

    @patch(S3_CLIENT_PATH)
    def test_reupload_deletes_previous_object(self, mock_client):
        s3 = MagicMock()
        mock_client.return_value = s3
        workshop = _make_workshop()
        # First upload.
        self.client.post(
            f'/studio/workshops/{workshop.pk}/upload-banner',
            {'banner_image': _png()},
        )
        workshop.refresh_from_db()
        first_url = workshop.custom_banner_url
        s3.delete_object.assert_not_called()
        # Second upload should delete the first object.
        self.client.post(
            f'/studio/workshops/{workshop.pk}/upload-banner',
            {'banner_image': _png(name='second.png')},
        )
        workshop.refresh_from_db()
        self.assertNotEqual(workshop.custom_banner_url, first_url)
        s3.delete_object.assert_called_once()

    @patch(S3_CLIENT_PATH)
    def test_non_image_rejected_no_db_change(self, mock_client):
        mock_client.return_value = MagicMock()
        event = _make_event()
        bad = SimpleUploadedFile(
            'doc.pdf', b'%PDF', content_type='application/pdf',
        )
        self.client.post(
            f'/studio/events/{event.pk}/upload-banner',
            {'banner_image': bad},
        )
        event.refresh_from_db()
        self.assertEqual(event.custom_banner_url, '')

    @patch(S3_CLIENT_PATH)
    def test_oversized_rejected_no_db_change(self, mock_client):
        mock_client.return_value = MagicMock()
        project = _make_project()
        big = SimpleUploadedFile(
            'big.png', b'x' * (6 * 1024 * 1024), content_type='image/png',
        )
        self.client.post(
            f'/studio/projects/{project.pk}/upload-banner',
            {'banner_image': big},
        )
        project.refresh_from_db()
        self.assertEqual(project.custom_banner_url, '')

    @patch(S3_CLIENT_PATH)
    def test_remove_clears_only_custom_banner_for_all_supported_types(
        self, mock_client,
    ):
        s3 = MagicMock()
        mock_client.return_value = s3
        cases = _remove_cases()

        for content_type, record, remove_name, id_kwarg, redirect_name in cases:
            expected = _set_banner_state(record, content_type)
            label = getattr(record, 'title', getattr(record, 'name', ''))
            with self.subTest(content_type=content_type):
                response = self.client.post(
                    reverse(remove_name, kwargs={id_kwarg: record.pk}),
                )
                self.assertRedirects(
                    response,
                    reverse(redirect_name, kwargs={id_kwarg: record.pk}),
                    fetch_redirect_response=False,
                )
                expected['custom_banner_url'] = ''
                _assert_banner_state(self, record, expected)
                self.assertEqual(
                    getattr(record, 'title', getattr(record, 'name', '')),
                    label,
                )

        self.assertEqual(s3.delete_object.call_count, len(cases))

    @patch(S3_CLIENT_PATH)
    def test_remove_survives_expired_credential_refresh_and_shows_fallback(
        self, mock_client,
    ):
        s3 = MagicMock()
        s3.delete_object.side_effect = RuntimeError(
            'Credentials were refreshed, but the refreshed credentials '
            'are still expired.',
        )
        mock_client.return_value = s3
        article = _make_article()
        Article.objects.filter(pk=article.pk).update(
            custom_banner_url=(
                f'{CDN_BASE}/custom-banners/article/{article.pk}-custom.png'
            ),
            auto_banner_url=GENERATED_URL,
        )

        response = self.client.post(
            f'/studio/articles/{article.pk}/remove-banner',
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        article.refresh_from_db()
        self.assertEqual(article.custom_banner_url, '')
        self.assertEqual(article.auto_banner_url, GENERATED_URL)
        self.assertContains(
            response,
            'Custom banner removed. Showing the generated banner.',
        )
        self.assertContains(response, f'src="{GENERATED_URL}"')
        self.assertContains(response, 'Generated')
        self.assertNotContains(response, 'data-testid="banner-remove-button"')

    @patch('studio.views.banner_upload.safe_delete_custom_banner')
    def test_remove_stays_successful_when_cleanup_returns_false(
        self, mock_delete,
    ):
        mock_delete.return_value = False
        article = _make_article()
        expected = _set_banner_state(article, 'article')

        response = self.client.post(
            f'/studio/articles/{article.pk}/remove-banner',
        )

        self.assertEqual(response.status_code, 302)
        expected['custom_banner_url'] = ''
        _assert_banner_state(self, article, expected)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertEqual(
            messages,
            ['Custom banner removed. Showing the generated banner.'],
        )


@disabled_config
class UploadDisabledTest(_ConfigCleanupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='pw')

    @patch(S3_CLIENT_PATH)
    def test_post_makes_no_change_when_unconfigured(self, mock_client):
        article = _make_article()
        response = self.client.post(
            f'/studio/articles/{article.pk}/upload-banner',
            {'banner_image': _png()},
        )
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.custom_banner_url, '')
        # No S3 client is ever constructed when uploads are disabled.
        mock_client.assert_not_called()


class PanelRenderTest(_ConfigCleanupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='pw')

    @enabled_config
    def test_custom_upload_shows_badge_and_remove_control(self):
        article = _make_article()
        Article.objects.filter(pk=article.pk).update(
            custom_banner_url=f'{CDN_BASE}/custom-banners/article/1-x.png',
        )
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'data-testid="banner-source-badge"')
        self.assertContains(response, 'Custom upload')
        self.assertContains(response, 'data-testid="banner-remove-button"')

    @enabled_config
    def test_remove_control_hidden_without_custom_banner(self):
        article = _make_article()
        Article.objects.filter(pk=article.pk).update(
            auto_banner_url=f'{CDN_BASE}/banners/article/1-x.jpg',
        )
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'Generated')
        self.assertNotContains(response, 'data-testid="banner-remove-button"')

    @disabled_config
    def test_upload_control_disabled_when_unconfigured(self):
        article = _make_article()
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(
            response, 'data-testid="banner-upload-button-disabled"',
        )
        self.assertContains(response, '/studio/settings/#content_tools')
        # The trailing quote distinguishes the enabled button testid from
        # the 'banner-upload-button-disabled' variant.
        self.assertNotContains(
            response, 'data-testid="banner-upload-button">',
        )

    @enabled_config
    def test_upload_control_enabled_when_configured(self):
        article = _make_article()
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'data-testid="banner-upload-form"')
        self.assertContains(response, 'data-testid="banner-upload-input"')

    @enabled_config
    def test_panel_labels_image_as_social(self):
        article = _make_article()
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'Banner / social image (1200x630)')
        self.assertContains(response, 'data-testid="banner-generator-explainer"')
