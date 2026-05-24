"""Tests for the banner-generator status hint in the Studio edit page (issue #790).

Covers the four-state matrix surfaced by
``studio.services.banner_status.get_last_banner_task`` and its
rendering in ``templates/studio/includes/banner_generator_section.html``:

* ``none``        — no task history → no hint rendered, button enabled.
* ``success``     — most recent ``Task`` row has ``success=True``.
* ``failed``      — most recent ``Task`` row has ``success=False``.
* ``in_progress`` — an ``OrmQ`` payload references this content record.
"""

import datetime as dt
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django_q.models import OrmQ, Task
from django_q.signing import SignedPackage

from content.models import Article, Course, Download, Project, Workshop
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from studio.services.banner_status import (
    RENDER_TASK_PATH,
    get_last_banner_task,
)

User = get_user_model()


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


def _make_task(content_type, content_pk, success, started, result=None,
               source='studio regenerate button'):
    name = f'Render banner: {content_type} #{content_pk} from {source}'
    return Task.objects.create(
        id=uuid.uuid4().hex,
        name=name,
        func=RENDER_TASK_PATH,
        hook='',
        args=(content_type, content_pk),
        kwargs={},
        result=result,
        started=started,
        stopped=started + dt.timedelta(seconds=2),
        success=success,
        attempt_count=1,
    )


def _make_ormq(content_type, content_pk, lock=None):
    payload = {
        'id': uuid.uuid4().hex,
        'name': f'Render banner: {content_type} #{content_pk}',
        'func': RENDER_TASK_PATH,
        'args': (content_type, content_pk),
        'kwargs': {},
    }
    return OrmQ.objects.create(
        key='default',
        payload=SignedPackage.dumps(payload),
        lock=lock,
    )


class _CacheCleanupMixin:
    def setUp(self):
        super().setUp()
        clear_config_cache()
        self.addCleanup(clear_config_cache)


class GetLastBannerTaskHelperTest(_CacheCleanupMixin, TestCase):
    """The pure helper returns the right state dict for each scenario."""

    def test_returns_none_when_no_task_history(self):
        result = get_last_banner_task('article', 12345)
        self.assertEqual(result['state'], 'none')
        self.assertIsNone(result['started_at'])
        self.assertIsNone(result['task_detail_url'])

    def test_returns_success_for_latest_successful_task(self):
        started = timezone.now() - dt.timedelta(minutes=5)
        task = _make_task('article', 7, success=True, started=started)
        result = get_last_banner_task('article', 7)
        self.assertEqual(result['state'], 'success')
        self.assertEqual(result['started_at'], started)
        self.assertIn(task.id, result['task_detail_url'])

    def test_returns_failed_with_clamped_excerpt(self):
        long_error = 'A' * 500
        started = timezone.now() - dt.timedelta(minutes=2)
        task = _make_task(
            'course', 9, success=False, started=started, result=long_error,
        )
        result = get_last_banner_task('course', 9)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['started_at'], started)
        self.assertEqual(len(result['result_excerpt']), 200)
        self.assertIn(task.id, result['task_detail_url'])

    def test_failed_excerpt_collapses_to_one_line(self):
        multiline = 'first line\n\nsecond line\twith\ttabs'
        _make_task(
            'project', 3, success=False,
            started=timezone.now() - dt.timedelta(seconds=10),
            result=multiline,
        )
        result = get_last_banner_task('project', 3)
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('\n', result['result_excerpt'])
        self.assertNotIn('\t', result['result_excerpt'])
        # All whitespace runs collapsed to single spaces.
        self.assertEqual(
            result['result_excerpt'],
            'first line second line with tabs',
        )

    def test_most_recent_task_wins(self):
        old = timezone.now() - dt.timedelta(hours=1)
        new = timezone.now() - dt.timedelta(minutes=1)
        _make_task('article', 4, success=False, started=old)
        _make_task('article', 4, success=True, started=new)
        result = get_last_banner_task('article', 4)
        self.assertEqual(result['state'], 'success')
        self.assertEqual(result['started_at'], new)

    def test_in_progress_overrides_terminal_history(self):
        _make_task(
            'download', 8, success=False,
            started=timezone.now() - dt.timedelta(minutes=10),
        )
        _make_ormq('download', 8, lock=timezone.now())
        result = get_last_banner_task('download', 8)
        self.assertEqual(result['state'], 'in_progress')

    def test_unrelated_task_does_not_match(self):
        _make_task(
            'article', 99, success=True,
            started=timezone.now() - dt.timedelta(seconds=5),
        )
        result = get_last_banner_task('article', 100)
        self.assertEqual(result['state'], 'none')

    def test_unrelated_ormq_does_not_match(self):
        _make_ormq('article', 99)
        result = get_last_banner_task('article', 100)
        self.assertEqual(result['state'], 'none')

    def test_in_progress_matches_list_args(self):
        # Pickled payload args may decode as list or tuple — both must match.
        payload = {
            'id': uuid.uuid4().hex,
            'name': 'Render banner: workshop #5',
            'func': RENDER_TASK_PATH,
            'args': ['workshop', 5],
            'kwargs': {},
        }
        OrmQ.objects.create(
            key='default',
            payload=SignedPackage.dumps(payload),
            lock=None,
        )
        result = get_last_banner_task('workshop', 5)
        self.assertEqual(result['state'], 'in_progress')

    def test_lookup_uses_name_icontains_not_args_contains(self):
        """Args is pickled, so we must filter by ``name__icontains``."""
        # Spy on Task.objects.filter to verify the kwargs the helper used.
        from django_q.models import Task as TaskModel

        original_filter = TaskModel.objects.filter
        captured = {}

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return original_filter(*args, **kwargs)

        with patch.object(TaskModel.objects, 'filter', side_effect=spy):
            get_last_banner_task('article', 42)

        self.assertIn('name__icontains', captured)
        self.assertEqual(captured['name__icontains'], 'article #42')
        self.assertNotIn('args__contains', captured)


class BannerGeneratorSectionRenderingTest(_CacheCleanupMixin, TestCase):
    """The include renders the right hint chrome for each state."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-section@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(email='staff-section@test.com', password='testpass')
        _set_banner_generator(enabled=True)

    def _make_article(self):
        return Article.objects.create(
            title='Hint article', slug='hint-article', date=dt.date(2026, 1, 1),
        )

    def test_no_history_renders_no_hint_chrome(self):
        article = self._make_article()
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'banner-generator-last-success')
        self.assertNotContains(response, 'banner-generator-last-failure')
        self.assertNotContains(response, 'banner-generator-in-progress')
        self.assertContains(
            response, 'data-testid="banner-generator-regenerate-button"',
        )

    def test_success_renders_success_hint(self):
        article = self._make_article()
        _make_task(
            'article', article.pk, success=True,
            started=timezone.now() - dt.timedelta(minutes=3),
        )
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-last-success"')
        self.assertContains(response, 'Last regenerated')
        self.assertContains(response, 'ago.')
        self.assertNotContains(response, 'banner-generator-last-failure')
        self.assertNotContains(response, 'banner-generator-in-progress')

    def test_failure_renders_failure_hint_with_view_task_link(self):
        article = self._make_article()
        task = _make_task(
            'article', article.pk, success=False,
            started=timezone.now() - dt.timedelta(minutes=1),
            result='botocore.exceptions.ClientError: AccessDenied on s3:PutObject',
        )
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-last-failure"')
        self.assertContains(response, 'Last attempt failed')
        self.assertContains(response, 'View task.')
        detail_url = reverse(
            'studio_worker_task_detail', kwargs={'task_id': task.id},
        )
        self.assertContains(response, f'href="{detail_url}"')
        self.assertContains(response, 'AccessDenied on s3:PutObject')
        self.assertNotContains(response, 'banner-generator-last-success')

    def test_in_progress_renders_disabled_inflight_button(self):
        article = self._make_article()
        _make_ormq('article', article.pk, lock=timezone.now())
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-in-progress"')
        self.assertContains(response, 'Regeneration in progress')
        self.assertContains(
            response,
            'data-testid="banner-generator-regenerate-button-disabled-inflight"',
        )
        # The enabled button is NOT present when a task is in-flight.
        self.assertNotContains(
            response, 'data-testid="banner-generator-regenerate-button"',
        )

    def test_disabled_generator_does_not_layer_failure_hint(self):
        article = self._make_article()
        _make_task(
            'article', article.pk, success=False,
            started=timezone.now() - dt.timedelta(seconds=30),
            result='boom',
        )
        _set_banner_generator(enabled=False)
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(
            response,
            'data-testid="banner-generator-regenerate-button-disabled"',
        )
        # No failure hint chrome layered on top of the not-configured state.
        self.assertNotContains(response, 'banner-generator-last-failure')
        self.assertNotContains(response, 'banner-generator-last-success')
        self.assertNotContains(response, 'banner-generator-in-progress')


class AllFiveEditViewsPassBannerLastTaskTest(_CacheCleanupMixin, TestCase):
    """Each of the five edit views wires ``banner_last_task`` into context."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-views@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(email='staff-views@test.com', password='testpass')
        _set_banner_generator(enabled=True)

    def _seed_success_task(self, content_type, pk):
        _make_task(
            content_type, pk, success=True,
            started=timezone.now() - dt.timedelta(minutes=2),
        )

    def test_article_edit_renders_success_hint(self):
        article = Article.objects.create(
            title='A', slug='a', date=dt.date(2026, 1, 1),
        )
        self._seed_success_task('article', article.pk)
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-last-success"')

    def test_course_edit_renders_success_hint(self):
        course = Course.objects.create(
            title='C', slug='c', status='published',
        )
        self._seed_success_task('course', course.pk)
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-last-success"')

    def test_project_review_renders_success_hint(self):
        project = Project.objects.create(
            title='P', slug='p', date=dt.date(2026, 1, 1),
        )
        self._seed_success_task('project', project.pk)
        response = self.client.get(f'/studio/projects/{project.pk}/review')
        self.assertContains(response, 'data-testid="banner-generator-last-success"')

    def test_download_edit_renders_success_hint(self):
        download = Download.objects.create(
            title='D', slug='d', file_url='https://example.com/x.pdf',
        )
        self._seed_success_task('download', download.pk)
        response = self.client.get(f'/studio/downloads/{download.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-last-success"')

    def test_workshop_edit_renders_success_hint(self):
        workshop = Workshop.objects.create(
            slug='ws', title='WS', date=dt.date(2026, 4, 13),
            pages_required_level=5, recording_required_level=20,
        )
        self._seed_success_task('workshop', workshop.pk)
        response = self.client.get(f'/studio/workshops/{workshop.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-last-success"')
