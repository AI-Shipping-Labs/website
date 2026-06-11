"""Tests for the banner-generator dispatcher + task (issue #788).

Covers ``enqueue_if_missing`` short-circuits, per-type payload mapping,
S3 key derivation, and the ``render_banner_for_content`` worker task
including the ``.update()``-based persistence path.
"""

import datetime as dt
import os
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.test import TestCase

from content.access import LEVEL_BASIC, LEVEL_MAIN, LEVEL_OPEN, LEVEL_PREMIUM
from content.models import Article, Course, Download, Project, Workshop
from events.models import Event
from integrations.models import IntegrationSetting
from integrations.services.banner_generator import BannerGeneratorError
from integrations.services.banner_generator.dispatch import (
    enqueue_force,
    enqueue_if_missing,
    title_hash,
)
from integrations.services.banner_generator.tasks import (
    build_payload,
    cdn_url_for,
    cdn_url_for_key,
    delete_generated_banner_object,
    render_banner_for_content,
    s3_key_for,
)

DISPATCH_PATCH = (
    'integrations.services.banner_generator.dispatch.async_task'
)


class _BannerGeneratorCacheCleanupMixin:
    """Clear the in-process config cache before and after each test.

    The settings cache is module-level and survives Django's per-test
    DB rollback. Without this teardown a setting written by one test
    leaks into the next when tests run in parallel and a worker happens
    to reuse the process for an unrelated test class.
    """

    def setUp(self):
        super().setUp()
        env_patch = patch.dict(os.environ, {
            'BANNER_GENERATOR_FUNCTION_URL': '',
            'BANNER_GENERATOR_AUTH_TOKEN': '',
            'AWS_S3_CONTENT_BUCKET': '',
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)
        from integrations.config import clear_config_cache
        clear_config_cache()
        self.addCleanup(clear_config_cache)


def _set_setting(key, value):
    IntegrationSetting.objects.update_or_create(
        key=key,
        defaults={'value': value, 'is_secret': False, 'group': 'banner_generator', 'description': ''},
    )


def _configure_banner_generator():
    _set_setting('BANNER_GENERATOR_FUNCTION_URL', 'https://lambda.example.com/')
    _set_setting('BANNER_GENERATOR_AUTH_TOKEN', 'token-abc')
    _set_setting('AWS_S3_CONTENT_BUCKET', 'content-bucket')
    _set_setting('CONTENT_CDN_BASE', 'https://cdn.example.com')
    from integrations.config import clear_config_cache
    clear_config_cache()


def _make_article(**overrides):
    defaults = {
        'title': 'Hello World',
        'slug': 'hello-world',
        'description': 'A nice description.',
        'date': dt.date(2026, 1, 1),
        'tags': ['Guides', 'AI'],
        'cover_image_url': '',
    }
    defaults.update(overrides)
    return Article.objects.create(**defaults)


def _make_download(**overrides):
    defaults = {
        'title': 'Cheat Sheet',
        'slug': 'cheat-sheet',
        'description': 'A handy reference.',
        'file_url': 'https://example.com/cheat.pdf',
        'file_type': 'pdf',
        'tags': ['ml', 'reference'],
        'cover_image_url': '',
    }
    defaults.update(overrides)
    return Download.objects.create(**defaults)


def _make_project(**overrides):
    defaults = {
        'title': 'RAG Demo',
        'slug': 'rag-demo',
        'description': 'Build a small retrieval pipeline.',
        'date': dt.date(2026, 1, 1),
        'tags': ['rag', 'embeddings'],
        'difficulty': 'intermediate',
        'cover_image_url': '',
    }
    defaults.update(overrides)
    return Project.objects.create(**defaults)


def _make_course(**overrides):
    defaults = {
        'title': 'AI Hero',
        'slug': 'ai-hero',
        'description': 'A self-paced course.',
        'tags': ['agents', 'rag', 'eval', 'extra'],
        'status': 'published',
        'cover_image_url': '',
    }
    defaults.update(overrides)
    return Course.objects.create(**defaults)


def _make_event(**overrides):
    defaults = {
        'slug': 'shipping-agents-in-production',
        'title': 'Shipping Agents in Production',
        'description': 'A live session on deploying agents.',
        'start_datetime': dt.datetime(2026, 5, 28, 16, 0, tzinfo=dt.timezone.utc),
        'end_datetime': dt.datetime(2026, 5, 28, 17, 0, tzinfo=dt.timezone.utc),
        'timezone': 'Europe/Berlin',
        'location': 'Zoom',
        'tags': ['agents', 'production'],
        'required_level': LEVEL_MAIN,
        'status': 'upcoming',
        'origin': 'studio',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def _make_workshop(**overrides):
    defaults = {
        'slug': 'reliable-agents',
        'title': 'Reliable Agents',
        'date': dt.date(2026, 4, 13),
        'description': 'Hands-on workshop description.',
        'tags': ['agents', 'workflow'],
        'pages_required_level': 5,
        'recording_required_level': 20,
        'cover_image_url': '',
    }
    defaults.update(overrides)
    return Workshop.objects.create(**defaults)


# --------------------------------------------------------------------------
# enqueue_if_missing
# --------------------------------------------------------------------------


class EnqueueIfMissingTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def setUp(self):
        super().setUp()
        _configure_banner_generator()

    @patch(DISPATCH_PATCH)
    def test_enqueues_when_no_cover_no_banner(self, mock_async):
        article = _make_article()
        mock_async.return_value = 'task-id-1'
        result = enqueue_if_missing('article', article.pk)
        self.assertEqual(result, 'task-id-1')
        mock_async.assert_called_once()
        args = mock_async.call_args[0]
        self.assertEqual(
            args[0],
            'integrations.services.banner_generator.tasks.render_banner_for_content',
        )
        self.assertEqual(args[1], 'article')
        self.assertEqual(args[2], article.pk)

    @patch(DISPATCH_PATCH)
    def test_skips_when_cover_image_set(self, mock_async):
        article = _make_article(cover_image_url='https://cdn.example.com/cover.png')
        result = enqueue_if_missing('article', article.pk)
        self.assertIsNone(result)
        mock_async.assert_not_called()

    @patch(DISPATCH_PATCH)
    def test_skips_when_banner_url_and_title_hash_match(self, mock_async):
        article = _make_article()
        Article.objects.filter(pk=article.pk).update(
            auto_banner_url='https://cdn.example.com/banners/article/1.jpg',
            auto_banner_title_hash=title_hash(article.title),
        )
        result = enqueue_if_missing('article', article.pk)
        self.assertIsNone(result)
        mock_async.assert_not_called()

    @patch(DISPATCH_PATCH)
    def test_enqueues_when_banner_url_but_title_hash_stale(self, mock_async):
        article = _make_article()
        Article.objects.filter(pk=article.pk).update(
            auto_banner_url='https://cdn.example.com/banners/article/1.jpg',
            auto_banner_title_hash=title_hash('OLD TITLE'),
        )
        mock_async.return_value = 'task-id-2'
        result = enqueue_if_missing('article', article.pk)
        self.assertEqual(result, 'task-id-2')
        mock_async.assert_called_once()

    @patch(DISPATCH_PATCH)
    def test_skips_when_banner_generator_not_configured(self, mock_async):
        IntegrationSetting.objects.filter(
            key='BANNER_GENERATOR_FUNCTION_URL',
        ).delete()
        from integrations.config import clear_config_cache
        clear_config_cache()
        article = _make_article()
        with self.assertNoLogs(
            'integrations.services.banner_generator.dispatch',
            level='ERROR',
        ):
            result = enqueue_if_missing('article', article.pk)
        self.assertIsNone(result)
        mock_async.assert_not_called()

    @patch(DISPATCH_PATCH)
    def test_skips_on_unknown_content_type(self, mock_async):
        result = enqueue_if_missing('not-a-type', 1)
        self.assertIsNone(result)
        mock_async.assert_not_called()

    @patch(DISPATCH_PATCH)
    def test_skips_on_missing_record(self, mock_async):
        result = enqueue_if_missing('article', 99999)
        self.assertIsNone(result)
        mock_async.assert_not_called()

    @patch(DISPATCH_PATCH)
    def test_event_enqueues_when_no_cover_no_banner(self, mock_async):
        event = _make_event()
        mock_async.return_value = 'event-task'
        result = enqueue_if_missing('event', event.pk)
        self.assertEqual(result, 'event-task')
        args = mock_async.call_args[0]
        self.assertEqual(args[1], 'event')
        self.assertEqual(args[2], event.pk)

    @patch(DISPATCH_PATCH)
    def test_event_skips_when_cover_image_set(self, mock_async):
        event = _make_event(
            cover_image_url='https://cdn.example.com/event-cover.png',
        )
        self.assertIsNone(enqueue_if_missing('event', event.pk))
        mock_async.assert_not_called()

    @patch(DISPATCH_PATCH)
    def test_event_skips_when_title_hash_matches(self, mock_async):
        event = _make_event()
        Event.objects.filter(pk=event.pk).update(
            auto_banner_url='https://cdn.example.com/banners/event/x.jpg',
            auto_banner_title_hash=title_hash(event.title),
        )
        self.assertIsNone(enqueue_if_missing('event', event.pk))
        mock_async.assert_not_called()

    @patch(DISPATCH_PATCH)
    def test_event_enqueues_when_title_hash_drifts(self, mock_async):
        event = _make_event()
        Event.objects.filter(pk=event.pk).update(
            auto_banner_url='https://cdn.example.com/banners/event/x.jpg',
            auto_banner_title_hash=title_hash('OLD EVENT TITLE'),
        )
        mock_async.return_value = 'event-redraw'
        self.assertEqual(
            enqueue_if_missing('event', event.pk), 'event-redraw',
        )
        mock_async.assert_called_once()


class EnqueueForceTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def setUp(self):
        super().setUp()
        _configure_banner_generator()

    @patch(DISPATCH_PATCH)
    def test_enqueues_even_when_cover_image_set(self, mock_async):
        article = _make_article(cover_image_url='https://cdn.example.com/cover.png')
        Article.objects.filter(pk=article.pk).update(
            auto_banner_url='https://cdn.example.com/banners/article/x.jpg',
            auto_banner_title_hash=title_hash(article.title),
        )
        mock_async.return_value = 'task-force'
        result = enqueue_force('article', article.pk)
        self.assertEqual(result, 'task-force')
        mock_async.assert_called_once()

    @patch(DISPATCH_PATCH)
    def test_skips_when_not_configured(self, mock_async):
        IntegrationSetting.objects.filter(
            key='BANNER_GENERATOR_AUTH_TOKEN',
        ).delete()
        from integrations.config import clear_config_cache
        clear_config_cache()
        article = _make_article()
        self.assertIsNone(enqueue_force('article', article.pk))
        mock_async.assert_not_called()


# --------------------------------------------------------------------------
# S3 key derivation
# --------------------------------------------------------------------------


class S3KeyTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def test_key_shape_per_content_type(self):
        cases = [
            ('article', 42),
            ('course', 7),
            ('project', 3),
            ('download', 11),
            ('workshop', 99),
        ]
        for content_type, pk in cases:
            with self.subTest(content_type=content_type):
                key = s3_key_for(content_type, pk)
                self.assertRegex(
                    key,
                    rf'^banners/{content_type}/{pk}-[0-9a-f]{{32}}\.jpg$',
                )

    def test_key_is_unique_for_each_render(self):
        first = s3_key_for('article', 42)
        second = s3_key_for('article', 42)
        self.assertNotEqual(first, second)

    def test_cdn_url_uses_content_cdn_base(self):
        _configure_banner_generator()
        url = cdn_url_for('article', 42)
        self.assertRegex(
            url,
            r'^https://cdn\.example\.com/banners/article/42-[0-9a-f]{32}\.jpg$',
        )

    def test_cdn_url_for_key_uses_exact_s3_key(self):
        _configure_banner_generator()
        key = 'banners/article/42-exact.jpg'
        self.assertEqual(
            cdn_url_for_key(key),
            'https://cdn.example.com/banners/article/42-exact.jpg',
        )

    def test_cdn_url_empty_when_cdn_base_unset(self):
        from django.test import override_settings
        IntegrationSetting.objects.filter(key='CONTENT_CDN_BASE').delete()
        from integrations.config import clear_config_cache
        clear_config_cache()
        # The default Django setting falls back to a non-empty value
        # (``/static/content-images``); override to '' to exercise the
        # blank-CDN code path that returns the empty string.
        with override_settings(CONTENT_CDN_BASE=''):
            self.assertEqual(cdn_url_for('article', 42), '')


class DeleteGeneratedBannerObjectTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def setUp(self):
        super().setUp()
        _configure_banner_generator()

    @patch('integrations.services.banner_generator.tasks.boto3.client')
    def test_deletes_matching_generated_banner_key(self, mock_client):
        result = delete_generated_banner_object(
            'article',
            'https://cdn.example.com/banners/article/42-old.jpg',
        )
        self.assertTrue(result)
        mock_client.assert_called_once_with(
            's3',
            region_name='eu-west-1',
        )
        mock_client.return_value.delete_object.assert_called_once_with(
            Bucket='content-bucket',
            Key='banners/article/42-old.jpg',
        )

    @patch('integrations.services.banner_generator.tasks.boto3.client')
    def test_uses_configured_s3_credentials_when_present(self, mock_client):
        IntegrationSetting.objects.update_or_create(
            key='AWS_ACCESS_KEY_ID',
            defaults={
                'value': 'AKIA_TEST',
                'group': 'aws',
                'is_secret': True,
            },
        )
        IntegrationSetting.objects.update_or_create(
            key='AWS_SECRET_ACCESS_KEY',
            defaults={
                'value': 'SECRET_TEST',
                'group': 'aws',
                'is_secret': True,
            },
        )
        from integrations.config import clear_config_cache
        clear_config_cache()

        result = delete_generated_banner_object(
            'article',
            'https://cdn.example.com/banners/article/42-old.jpg',
        )

        self.assertTrue(result)
        mock_client.assert_called_once_with(
            's3',
            region_name='eu-west-1',
            aws_access_key_id='AKIA_TEST',
            aws_secret_access_key='SECRET_TEST',
        )

    @patch('integrations.services.banner_generator.tasks.boto3.client')
    def test_does_not_delete_urls_outside_safe_generated_prefixes(self, mock_client):
        cases = [
            'https://images.example.org/banners/article/42-old.jpg',
            'https://cdn.example.com/content/article/42-old.jpg',
            'https://cdn.example.com/banners/course/42-old.jpg',
            'https://cdn.example.com/banners/article/42-old.jpg?version=1',
            'https://cdn.example.com/banners/article/42%2Fold.jpg',
            'https://cdn.example.com/banners/article/../course/42-old.jpg',
            '',
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertFalse(delete_generated_banner_object('article', url))
        mock_client.assert_not_called()

    @patch('integrations.services.banner_generator.tasks.boto3.client')
    def test_missing_bucket_config_logs_warning_and_skips_delete(self, mock_client):
        IntegrationSetting.objects.filter(key='AWS_S3_CONTENT_BUCKET').delete()
        from integrations.config import clear_config_cache
        clear_config_cache()
        from django.test import override_settings
        with override_settings(AWS_S3_CONTENT_BUCKET=''):
            with self.assertLogs(
                'integrations.services.banner_generator.tasks',
                level='WARNING',
            ) as log_cm:
                result = delete_generated_banner_object(
                    'article',
                    'https://cdn.example.com/banners/article/42-old.jpg',
                )
        self.assertFalse(result)
        mock_client.assert_not_called()
        self.assertIn('bucket unset', '\n'.join(log_cm.output))

    @patch('integrations.services.banner_generator.tasks.boto3.client')
    def test_delete_error_logs_warning_and_returns_false(self, mock_client):
        mock_client.return_value.delete_object.side_effect = ClientError(
            {
                'Error': {
                    'Code': 'AccessDenied',
                    'Message': 'no delete permission',
                },
            },
            'DeleteObject',
        )
        with self.assertLogs(
            'integrations.services.banner_generator.tasks',
            level='WARNING',
        ) as log_cm:
            result = delete_generated_banner_object(
                'article',
                'https://cdn.example.com/banners/article/42-old.jpg',
            )
        self.assertFalse(result)
        self.assertIn('failed to delete banners/article/42-old.jpg', '\n'.join(log_cm.output))


# --------------------------------------------------------------------------
# Per-type payload mapping
# --------------------------------------------------------------------------


class BuildPayloadTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def test_article_payload(self):
        article = _make_article(
            title='What Skills Do You Need',
            description='Based on real job descriptions.',
            tags=['guides', 'Career', 'AI Engineering'],
        )
        payload = build_payload('article', article)
        self.assertEqual(payload['kind'], 'Article')
        # Tags are normalised to lowercase-hyphenated on save (see
        # content.utils.tags.normalize_tags), so the first tag here is
        # 'guides' rather than 'Guides' — ``.title()`` capitalises.
        self.assertEqual(payload['kicker'], 'Guides')
        self.assertEqual(payload['title'], 'What Skills Do You Need')
        self.assertIn('Based on real job descriptions', payload['subtitle'])
        self.assertEqual(payload['meta_primary'], 'Blog')
        self.assertEqual(
            payload['meta_secondary'], 'guides / career / ai-engineering',
        )
        self.assertEqual(payload['footer'], 'aishippinglabs.com/blog')

    def test_article_payload_empty_kicker_when_no_tags(self):
        article = _make_article(tags=[])
        payload = build_payload('article', article)
        self.assertEqual(payload['kicker'], '')

    def test_course_payload_free(self):
        course = _make_course(
            required_level=LEVEL_OPEN,
            slug='aihero',
            tags=['rag', 'search', 'agents'],
        )
        payload = build_payload('course', course)
        self.assertEqual(payload['kind'], 'Course')
        self.assertEqual(payload['kicker'], 'Self-paced course')
        self.assertEqual(payload['meta_primary'], 'Free')
        self.assertEqual(payload['footer'], 'aishippinglabs.com/courses/aihero')

    def test_course_payload_basic_tier_label(self):
        course = _make_course(required_level=LEVEL_BASIC, slug='basic')
        payload = build_payload('course', course)
        self.assertEqual(payload['meta_primary'], 'Basic')

    def test_course_payload_main_tier_label(self):
        course = _make_course(required_level=LEVEL_MAIN, slug='main')
        payload = build_payload('course', course)
        self.assertEqual(payload['meta_primary'], 'Main')

    def test_course_payload_premium_tier_label(self):
        course = _make_course(required_level=LEVEL_PREMIUM, slug='premium')
        payload = build_payload('course', course)
        self.assertEqual(payload['meta_primary'], 'Premium')

    def test_project_payload(self):
        project = _make_project(
            title='Multi-Agent Research',
            difficulty='advanced',
            tags=['agents', 'openai', 'pydantic'],
        )
        payload = build_payload('project', project)
        self.assertEqual(payload['kind'], 'Project')
        self.assertEqual(payload['kicker'], 'Advanced build')
        self.assertEqual(payload['meta_primary'], 'agents')
        self.assertEqual(payload['meta_secondary'], 'agents / openai / pydantic')
        self.assertEqual(payload['footer'], 'AI Shipping Labs Projects')

    def test_project_payload_no_difficulty(self):
        project = _make_project(difficulty='', tags=[])
        payload = build_payload('project', project)
        self.assertEqual(payload['kicker'], 'Project')
        self.assertEqual(payload['meta_primary'], 'Project')

    def test_download_payload_uses_resource_kind(self):
        download = _make_download(
            title='AI Notebooks',
            file_type='notebook',
            tags=['ml', 'reference'],
        )
        payload = build_payload('download', download)
        self.assertEqual(payload['kind'], 'Resource')
        self.assertEqual(payload['kicker'], 'Download')
        self.assertEqual(payload['meta_primary'], 'Notebook')
        self.assertEqual(payload['footer'], 'AI Shipping Labs Downloads')

    def test_workshop_payload(self):
        workshop = _make_workshop(
            title='Build Reliable AI Agents',
            tags=['agents', 'rag', 'tools'],
        )
        payload = build_payload('workshop', workshop)
        self.assertEqual(payload['kind'], 'Workshop')
        self.assertEqual(payload['kicker'], 'Hands-on workshop')
        self.assertEqual(payload['title'], 'Build Reliable AI Agents')
        self.assertEqual(payload['meta_primary'], 'Live online')
        self.assertEqual(payload['meta_secondary'], 'agents / rag / tools')
        self.assertEqual(payload['footer'], 'AI Shipping Labs Workshops')

    def test_event_payload_all_nine_slots(self):
        event = _make_event(
            title='Shipping Agents in Production',
            tags=['workshop', 'agents'],
            required_level=LEVEL_MAIN,
        )
        payload = build_payload('event', event)
        self.assertEqual(payload['brand'], 'AI Shipping Labs')
        self.assertEqual(payload['kind'], 'Live Session')
        # First tag, title-cased.
        self.assertEqual(payload['kicker'], 'Workshop')
        self.assertEqual(payload['title'], 'Shipping Agents in Production')
        self.assertEqual(
            payload['subtitle'], 'A live session on deploying agents.',
        )
        self.assertEqual(payload['meta_primary'], 'May 28, 2026')
        # 16:00 UTC == 18:00 CEST in late May; location is Zoom.
        self.assertEqual(payload['meta_secondary'], '18:00 CEST / Zoom')
        self.assertEqual(payload['tag_one'], 'Main')
        self.assertEqual(payload['footer'], 'aishippinglabs.com/events')
        # All nine documented slots present.
        self.assertEqual(
            set(payload),
            {
                'brand', 'kind', 'kicker', 'title', 'subtitle',
                'meta_primary', 'meta_secondary', 'tag_one', 'footer',
            },
        )

    def test_event_payload_tier_label_free_for_open(self):
        event = _make_event(required_level=LEVEL_OPEN)
        self.assertEqual(build_payload('event', event)['tag_one'], 'Free')

    def test_event_payload_tier_label_basic(self):
        event = _make_event(required_level=LEVEL_BASIC)
        self.assertEqual(build_payload('event', event)['tag_one'], 'Basic')

    def test_event_payload_tier_label_premium(self):
        event = _make_event(required_level=LEVEL_PREMIUM)
        self.assertEqual(build_payload('event', event)['tag_one'], 'Premium')

    def test_event_payload_external_host_used_as_tag_one(self):
        event = _make_event(external_host='Maven', required_level=LEVEL_MAIN)
        self.assertEqual(build_payload('event', event)['tag_one'], 'Maven')

    def test_event_payload_kicker_falls_back_when_no_tags(self):
        event = _make_event(tags=[])
        self.assertEqual(build_payload('event', event)['kicker'], 'Live event')

    def test_event_payload_meta_secondary_platform_when_no_location(self):
        event = _make_event(location='', platform='zoom')
        # No location -> platform label (Zoom). Time half still present.
        self.assertTrue(build_payload('event', event)['meta_secondary'].endswith('/ Zoom'))

    def test_event_payload_no_raise_on_edge_cases(self):
        """Empty tags, empty description, null end, missing location."""
        event = _make_event(
            tags=[],
            description='',
            end_datetime=None,
            location='',
        )
        payload = build_payload('event', event)
        self.assertEqual(payload['subtitle'], '')
        self.assertEqual(payload['kicker'], 'Live event')
        # Did not raise; meta_secondary still resolves a platform fallback.
        self.assertIn('Zoom', payload['meta_secondary'])

    def test_subtitle_truncated_at_140_chars(self):
        long_desc = 'word ' * 100  # 500 chars
        article = _make_article(description=long_desc)
        payload = build_payload('article', article)
        self.assertLessEqual(len(payload['subtitle']), 141)
        self.assertTrue(payload['subtitle'].endswith('…'))

    def test_subtitle_strips_markdown(self):
        article = _make_article(
            description='**Bold** and [linked](http://x) text with `code`.',
        )
        payload = build_payload('article', article)
        self.assertNotIn('**', payload['subtitle'])
        self.assertNotIn('](', payload['subtitle'])
        self.assertNotIn('`', payload['subtitle'])
        self.assertIn('Bold', payload['subtitle'])
        self.assertIn('linked', payload['subtitle'])
        self.assertIn('code', payload['subtitle'])

    def test_meta_secondary_caps_at_three_tags(self):
        article = _make_article(tags=['a', 'b', 'c', 'd', 'e'])
        payload = build_payload('article', article)
        self.assertEqual(payload['meta_secondary'].count(' / '), 2)


# --------------------------------------------------------------------------
# render_banner_for_content
# --------------------------------------------------------------------------


CLIENT_PATCH = 'integrations.services.banner_generator.tasks.render_to_s3'
DELETE_PATCH = (
    'integrations.services.banner_generator.tasks.delete_generated_banner_object'
)


class RenderBannerForContentTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def setUp(self):
        super().setUp()
        _configure_banner_generator()

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_writes_url_and_hash_on_success(self, mock_render, mock_delete):
        mock_render.return_value = {'ok': True}
        article = _make_article(title='Persist Me')
        render_banner_for_content('article', article.pk)
        article.refresh_from_db()
        self.assertRegex(
            article.auto_banner_url,
            rf'^https://cdn\.example\.com/banners/article/'
            rf'{article.pk}-[0-9a-f]{{32}}\.jpg$',
        )
        self.assertEqual(
            article.auto_banner_title_hash,
            title_hash('Persist Me'),
        )
        mock_delete.assert_called_once_with('article', '')

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_swallows_banner_generator_error(self, mock_render, mock_delete):
        mock_render.side_effect = BannerGeneratorError('boom', status_code=500)
        article = _make_article(
            auto_banner_url='https://cdn.example.com/banners/article/old.jpg',
            auto_banner_title_hash='old-hash',
        )
        # Should NOT raise.
        render_banner_for_content('article', article.pk)
        article.refresh_from_db()
        self.assertEqual(
            article.auto_banner_url,
            'https://cdn.example.com/banners/article/old.jpg',
        )
        self.assertEqual(article.auto_banner_title_hash, 'old-hash')
        mock_delete.assert_not_called()

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_persists_via_update_does_not_trigger_article_save(
        self, mock_render, mock_delete,
    ):
        """``.update()`` skips Article.save(), so derived fields aren't re-run."""
        mock_render.return_value = {'ok': True}
        article = _make_article(content_markdown='# Title\n\nbody')
        original_html = article.content_html
        # Mutate the markdown WITHOUT save() so the rendered HTML is stale.
        Article.objects.filter(pk=article.pk).update(
            content_markdown='# Title\n\nDIFFERENT BODY',
        )
        render_banner_for_content('article', article.pk)
        article.refresh_from_db()
        # The save-time markdown renderer would have updated content_html if
        # render_banner_for_content had called .save(). Instead the html
        # stays at its previously-saved value.
        self.assertEqual(article.content_html, original_html)
        self.assertTrue(article.auto_banner_url)

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_persists_for_each_content_type(self, mock_render, mock_delete):
        mock_render.return_value = {'ok': True}
        for content_type, factory in (
            ('article', _make_article),
            ('course', _make_course),
            ('project', _make_project),
            ('download', _make_download),
            ('workshop', _make_workshop),
            ('event', _make_event),
        ):
            with self.subTest(content_type=content_type):
                record = factory()
                render_banner_for_content(content_type, record.pk)
                record.refresh_from_db()
                self.assertRegex(
                    record.auto_banner_url,
                    rf'^https://cdn\.example\.com/banners/{content_type}/'
                    rf'{record.pk}-[0-9a-f]{{32}}\.jpg$',
                )
                self.assertTrue(record.auto_banner_title_hash)

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_event_renders_with_event_stage_template(
        self, mock_render, mock_delete,
    ):
        mock_render.return_value = {'ok': True}
        event = _make_event()
        render_banner_for_content('event', event.pk)
        self.assertEqual(
            mock_render.call_args.kwargs['template'], 'asl-event-stage',
        )

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_non_event_keeps_content_card_template(
        self, mock_render, mock_delete,
    ):
        mock_render.return_value = {'ok': True}
        article = _make_article()
        render_banner_for_content('article', article.pk)
        self.assertEqual(
            mock_render.call_args.kwargs['template'], 'asl-content-card',
        )

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_render_request_key_matches_persisted_url(
        self, mock_render, mock_delete,
    ):
        mock_render.return_value = {'ok': True}
        article = _make_article()
        render_banner_for_content('article', article.pk)
        article.refresh_from_db()
        s3_key = mock_render.call_args.kwargs['s3_key']
        self.assertEqual(
            article.auto_banner_url,
            f'https://cdn.example.com/{s3_key}',
        )
        self.assertTrue(s3_key.endswith('.jpg'))
        self.assertTrue(s3_key.startswith('banners/article/'))
        self.assertEqual(mock_render.call_args.kwargs['fmt'], 'jpeg')

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_two_successful_renders_produce_distinct_urls(
        self, mock_render, mock_delete,
    ):
        mock_render.return_value = {'ok': True}
        article = _make_article()
        render_banner_for_content('article', article.pk)
        article.refresh_from_db()
        first_url = article.auto_banner_url
        first_key = mock_render.call_args.kwargs['s3_key']

        render_banner_for_content('article', article.pk)
        article.refresh_from_db()
        second_url = article.auto_banner_url
        second_key = mock_render.call_args.kwargs['s3_key']

        self.assertNotEqual(first_url, second_url)
        self.assertNotEqual(first_key, second_key)
        self.assertEqual(second_url, f'https://cdn.example.com/{second_key}')
        mock_delete.assert_called_with('article', first_url)

    @patch(DELETE_PATCH)
    @patch(CLIENT_PATCH)
    def test_force_regenerate_does_not_touch_manual_cover_image(
        self, mock_render, mock_delete,
    ):
        mock_render.return_value = {'ok': True}
        article = _make_article(
            cover_image_url='https://cdn.example.com/manual/article-cover.png',
            auto_banner_url='https://cdn.example.com/banners/article/old.jpg',
        )
        render_banner_for_content('article', article.pk)
        article.refresh_from_db()
        self.assertEqual(
            article.cover_image_url,
            'https://cdn.example.com/manual/article-cover.png',
        )
        self.assertRegex(
            article.auto_banner_url,
            rf'^https://cdn\.example\.com/banners/article/'
            rf'{article.pk}-[0-9a-f]{{32}}\.jpg$',
        )
        mock_delete.assert_called_once_with(
            'article',
            'https://cdn.example.com/banners/article/old.jpg',
        )

    @patch('integrations.services.banner_generator.tasks.boto3.client')
    @patch(CLIENT_PATCH)
    def test_cleanup_failure_does_not_undo_successful_render(
        self, mock_render, mock_client,
    ):
        mock_render.return_value = {'ok': True}
        mock_client.return_value.delete_object.side_effect = ClientError(
            {
                'Error': {
                    'Code': 'AccessDenied',
                    'Message': 'no delete permission',
                },
            },
            'DeleteObject',
        )
        article = _make_article(
            auto_banner_url='https://cdn.example.com/banners/article/old.jpg',
        )
        with self.assertLogs(
            'integrations.services.banner_generator.tasks',
            level='WARNING',
        ) as log_cm:
            result = render_banner_for_content('article', article.pk)
        article.refresh_from_db()
        self.assertEqual(article.auto_banner_url, result)
        self.assertRegex(
            article.auto_banner_url,
            rf'^https://cdn\.example\.com/banners/article/'
            rf'{article.pk}-[0-9a-f]{{32}}\.jpg$',
        )
        self.assertIn('failed to delete banners/article/old.jpg', '\n'.join(log_cm.output))

    @patch(CLIENT_PATCH)
    def test_unsupported_content_type_no_op(self, mock_render):
        render_banner_for_content('not-real', 1)
        mock_render.assert_not_called()

    @patch(CLIENT_PATCH)
    def test_no_op_when_record_missing(self, mock_render):
        render_banner_for_content('article', 99999)
        mock_render.assert_not_called()
