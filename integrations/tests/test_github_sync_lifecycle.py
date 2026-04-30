"""Focused tests for shared GitHub sync lifecycle helpers."""

from datetime import date

from django.test import TestCase

from content.models import Article
from integrations.services.github_sync.lifecycle import (
    cleanup_stale_synced_objects,
    find_synced_object,
    upsert_synced_object,
)


def _stats():
    return {
        'created': 0,
        'updated': 0,
        'unchanged': 0,
        'deleted': 0,
        'errors': [],
        'items_detail': [],
    }


def _article_defaults(**overrides):
    defaults = {
        'title': 'Original',
        'content_markdown': 'Body',
        'date': date(2026, 1, 1),
        'source_repo': 'repo',
        'source_path': 'articles/original.md',
        'source_commit': 'sha-1',
        'status': 'published',
    }
    defaults.update(overrides)
    return defaults


def _detail(article, action):
    return {
        'title': article.title,
        'slug': article.slug,
        'action': action,
        'content_type': 'article',
    }


class GitHubSyncLifecycleHelperTest(TestCase):
    def test_find_synced_object_uses_caller_lookup_order(self):
        older = Article.objects.create(
            slug='older',
            **_article_defaults(title='Older'),
        )
        newer = Article.objects.create(
            slug='newer',
            **_article_defaults(title='Newer'),
        )

        found = find_synced_object((
            lambda: None,
            lambda: newer,
            lambda: older,
        ))

        self.assertEqual(found, newer)

    def test_upsert_records_created_updated_and_unchanged(self):
        stats = _stats()

        created = upsert_synced_object(
            model=Article,
            lookup=lambda: None,
            defaults=_article_defaults(),
            stats=stats,
            create_kwargs={'slug': 'original'},
            detail=_detail,
        )
        self.assertTrue(created.created)
        self.assertTrue(created.changed)
        self.assertEqual(stats['created'], 1)
        self.assertEqual(stats['items_detail'][0]['action'], 'created')

        article = created.instance
        unchanged = upsert_synced_object(
            model=Article,
            lookup=lambda: article,
            defaults=_article_defaults(source_commit='sha-2'),
            stats=stats,
            detail=_detail,
        )
        self.assertFalse(unchanged.created)
        self.assertFalse(unchanged.changed)
        self.assertEqual(stats['unchanged'], 1)
        self.assertEqual(len(stats['items_detail']), 1)

        updated = upsert_synced_object(
            model=Article,
            lookup=lambda: article,
            defaults=_article_defaults(title='Renamed'),
            stats=stats,
            detail=_detail,
        )
        self.assertFalse(updated.created)
        self.assertTrue(updated.changed)
        self.assertEqual(stats['updated'], 1)
        self.assertEqual(stats['items_detail'][-1]['action'], 'updated')
        article.refresh_from_db()
        self.assertEqual(article.title, 'Renamed')

    def test_upsert_applies_caller_identity_update(self):
        article = Article.objects.create(
            slug='old-slug',
            **_article_defaults(),
        )
        stats = _stats()

        result = upsert_synced_object(
            model=Article,
            lookup=lambda: article,
            defaults=_article_defaults(source_path='articles/new-slug.md'),
            stats=stats,
            detail=_detail,
            identity_changed=lambda obj: obj.slug != 'new-slug',
            apply_identity=lambda obj: setattr(obj, 'slug', 'new-slug'),
        )

        self.assertTrue(result.changed)
        article.refresh_from_db()
        self.assertEqual(article.slug, 'new-slug')
        self.assertEqual(article.source_path, 'articles/new-slug.md')
        self.assertEqual(stats['updated'], 1)

    def test_stale_cleanup_records_deleted_and_uses_callback_behavior(self):
        stale = Article.objects.create(
            slug='stale',
            **_article_defaults(title='Stale'),
        )
        kept = Article.objects.create(
            slug='kept',
            **_article_defaults(title='Kept'),
        )
        stats = _stats()
        cleaned = []

        deleted_count = cleanup_stale_synced_objects(
            Article.objects.filter(slug='stale'),
            stats=stats,
            detail=_detail,
            cleanup=lambda objects: cleaned.extend(obj.pk for obj in objects),
        )

        self.assertEqual(deleted_count, 1)
        self.assertEqual(cleaned, [stale.pk])
        self.assertEqual(stats['deleted'], 1)
        self.assertEqual(stats['items_detail'][0]['action'], 'deleted')
        self.assertTrue(Article.objects.filter(pk=kept.pk).exists())
