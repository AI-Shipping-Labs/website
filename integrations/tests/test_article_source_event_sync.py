"""Article source-event sync coverage for issue #1331."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import yaml
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from content.models import Article, Workshop
from events.models import Event
from integrations.models import ContentSource
from integrations.services.github_sync.dispatchers.articles import (
    _dispatch_articles,
)

KNOWN_CONTENT_ID = '2f8d02cb-ad72-4923-bb9e-a7b5592776ac'


def _stats():
    return {
        'created': 0,
        'updated': 0,
        'deleted': 0,
        'unchanged': 0,
        'errors': [],
        'items_detail': [],
    }


class ArticleSourceEventSyncTest(TestCase):
    def setUp(self):
        temp_root = Path(settings.BASE_DIR) / '.tmp'
        temp_root.mkdir(exist_ok=True)
        self.tempdir = tempfile.TemporaryDirectory(
            prefix='article-source-event-',
            dir=temp_root,
        )
        self.addCleanup(self.tempdir.cleanup)
        self.repo_dir = Path(self.tempdir.name)
        self.rel_path = 'blog/ai-engineering-hiring-manager-interview.md'
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content-source-event-test',
        )
        now = timezone.now()
        self.event = Event.objects.create(
            slug='mock-interviews-for-ai-engineering-roles',
            title='Mock Interviews for AI Engineering Roles',
            start_datetime=now - timedelta(days=1),
            end_datetime=now - timedelta(hours=23),
            status='completed',
            published=True,
            origin='studio',
            source_repo='',
            location='Zoom',
            tags=['interviews'],
        )
        self.workshop = Workshop.objects.create(
            slug='mock-interviews-workshop',
            title='Mock Interviews Workshop',
            date=date(2026, 7, 21),
            event=self.event,
        )

    def _write_article(self, **overrides):
        metadata = {
            'content_id': KNOWN_CONTENT_ID,
            'slug': 'ai-engineering-hiring-manager-interview',
            'title': 'What to Expect in an AI Engineering Hiring Manager Interview',
            'date': '2026-07-22',
            'event_slug': self.event.slug,
        }
        metadata.update(overrides)
        path = self.repo_dir / self.rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\n{yaml.safe_dump(metadata, sort_keys=False)}---\nArticle body.\n",
            encoding='utf-8',
        )

    def _sync(self, commit='abc123'):
        stats = _stats()
        _dispatch_articles(
            self.source,
            str(self.repo_dir),
            [self.rel_path],
            commit,
            stats,
        )
        return stats

    def test_known_event_slug_links_without_changing_studio_event(self):
        self._write_article()
        event_values = {
            field.attname: getattr(self.event, field.attname)
            for field in self.event._meta.concrete_fields
        }

        stats = self._sync()

        article = Article.objects.get(content_id=KNOWN_CONTENT_ID)
        self.assertEqual(article.source_event, self.event)
        self.assertEqual(stats['created'], 1)
        self.assertEqual(Event.objects.count(), 1)
        self.event.refresh_from_db()
        self.assertEqual(
            {
                field.attname: getattr(self.event, field.attname)
                for field in self.event._meta.concrete_fields
            },
            event_values,
        )
        self.assertEqual(self.event.origin, 'studio')
        self.assertEqual(self.event.source_repo, '')
        self.assertEqual(self.event.workshop, self.workshop)

    def test_event_id_wins_over_conflicting_slug_and_resync_is_idempotent(self):
        other = Event.objects.create(
            slug='different-event',
            title='Different Event',
            start_datetime=timezone.now(),
            status='upcoming',
            published=True,
        )
        self._write_article(event_id=self.event.pk, event_slug=other.slug)

        first = self._sync('one')
        second = self._sync('two')

        article = Article.objects.get(content_id=KNOWN_CONTENT_ID)
        self.assertEqual(article.source_event, self.event)
        self.assertEqual(first['created'], 1)
        self.assertEqual(second['unchanged'], 1)
        self.assertEqual(Article.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 2)

    def test_invalid_reference_skips_update_and_preserves_last_known_good_link(self):
        self._write_article()
        self._sync()
        article = Article.objects.get(content_id=KNOWN_CONTENT_ID)
        original_title = article.title
        self._write_article(
            title='This change must not persist',
            event_slug='missing-source-event',
        )

        stats = self._sync('bad-ref')

        article.refresh_from_db()
        self.assertEqual(article.title, original_title)
        self.assertEqual(article.source_event, self.event)
        self.assertEqual(stats['updated'], 0)
        self.assertEqual(len(stats['errors']), 1)
        self.assertEqual(stats['errors'][0]['file'], self.rel_path)
        self.assertIn('missing-source-event', stats['errors'][0]['error'])
        self.assertIn(self.rel_path, stats['errors'][0]['error'])

    def test_malformed_blank_reference_skips_new_article(self):
        self._write_article(event_slug='   ')

        stats = self._sync()

        self.assertFalse(Article.objects.exists())
        self.assertIn("Invalid event_slug '   '", stats['errors'][0]['error'])

    def test_removing_reference_keys_clears_existing_link(self):
        self._write_article()
        self._sync()
        self._write_article(event_slug=None)
        # Omitting both keys (rather than writing YAML null) is the explicit
        # unlink operation.
        metadata = {
            'content_id': KNOWN_CONTENT_ID,
            'slug': 'ai-engineering-hiring-manager-interview',
            'title': 'What to Expect in an AI Engineering Hiring Manager Interview',
            'date': '2026-07-22',
        }
        path = self.repo_dir / self.rel_path
        path.write_text(
            f"---\n{yaml.safe_dump(metadata, sort_keys=False)}---\nArticle body.\n",
            encoding='utf-8',
        )

        stats = self._sync('unlink')

        article = Article.objects.get(content_id=KNOWN_CONTENT_ID)
        self.assertIsNone(article.source_event)
        self.assertEqual(stats['updated'], 1)

    def test_blank_event_id_takes_precedence_and_does_not_fall_back_to_slug(self):
        self._write_article(event_id='', event_slug=self.event.slug)

        stats = self._sync()

        self.assertFalse(Article.objects.exists())
        self.assertIn("Invalid event_id ''", stats['errors'][0]['error'])
