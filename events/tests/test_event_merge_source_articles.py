"""Duplicate-event merge safety for linked articles (issue #1331)."""

import datetime as dt
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from community.models import CommunityAuditLog
from content.models import Article
from events.models import Event
from events.services.event_merge import merge_duplicate_events

User = get_user_model()


class EventMergeSourceArticlesTest(TestCase):
    def setUp(self):
        start = dt.datetime(2026, 7, 21, 12, tzinfo=dt.timezone.utc)
        self.canonical = Event.objects.create(
            slug='canonical-source-event',
            title='Canonical Source Event',
            start_datetime=start,
            status='completed',
            published=True,
            origin='studio',
            source_repo='',
        )
        self.duplicate = Event.objects.create(
            slug='duplicate-source-event',
            title='Duplicate Source Event',
            start_datetime=start,
            status='completed',
            published=True,
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            kind='workshop',
        )
        self.article = Article.objects.create(
            slug='merged-source-article',
            title='Merged Source Article',
            date=dt.date(2026, 7, 22),
            source_event=self.duplicate,
        )

    def test_real_merge_relinks_articles_and_reports_audit_details(self):
        actor = User.objects.create_user(
            email='merge-source@example.com',
            password='testpass',
            is_staff=True,
        )

        plan = merge_duplicate_events(
            self.canonical,
            self.duplicate,
            actor_label='studio:merge-source@example.com',
            actor=actor,
            dry_run=False,
        )

        self.article.refresh_from_db()
        self.assertEqual(self.article.source_event, self.canonical)
        self.assertEqual(plan.source_articles_relinked, 1)
        self.assertEqual(
            plan.source_articles,
            [{'id': self.article.pk, 'title': self.article.title}],
        )
        details = json.loads(
            CommunityAuditLog.objects.get(action='merge_events').details,
        )
        self.assertEqual(details['source_articles_relinked'], 1)
        self.assertEqual(details['source_articles'][0]['id'], self.article.pk)

    def test_dry_run_reports_relink_but_rolls_it_back(self):
        plan = merge_duplicate_events(
            self.canonical,
            self.duplicate,
            actor_label='dry-run',
            dry_run=True,
        )

        self.article.refresh_from_db()
        self.duplicate.refresh_from_db()
        self.assertEqual(plan.source_articles_relinked, 1)
        self.assertEqual(self.article.source_event, self.duplicate)
        self.assertEqual(self.duplicate.status, 'completed')

    def test_repeat_merge_is_idempotent(self):
        merge_duplicate_events(
            self.canonical,
            self.duplicate,
            actor_label='first',
            dry_run=False,
        )
        self.canonical.refresh_from_db()
        self.duplicate.refresh_from_db()

        plan = merge_duplicate_events(
            self.canonical,
            self.duplicate,
            actor_label='second',
            dry_run=False,
        )

        self.assertTrue(plan.already_merged)
        self.assertEqual(plan.source_articles_relinked, 0)

    def test_retired_duplicate_with_leftover_article_is_repaired(self):
        Event.objects.filter(pk=self.duplicate.pk).update(
            status='cancelled',
            published=False,
        )
        self.duplicate.refresh_from_db()

        plan = merge_duplicate_events(
            self.canonical,
            self.duplicate,
            actor_label='repair',
            dry_run=False,
        )

        self.article.refresh_from_db()
        self.assertFalse(plan.already_merged)
        self.assertEqual(plan.source_articles_relinked, 1)
        self.assertEqual(self.article.source_event, self.canonical)
