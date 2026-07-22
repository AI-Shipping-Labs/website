"""Public and model coverage for article source events (issue #1331)."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from content.models import Article
from events.models import Event


class ArticleSourceEventModelTest(TestCase):
    def test_deleting_event_clears_link_without_deleting_article(self):
        event = Event.objects.create(
            slug='source-session',
            title='Source Session',
            start_datetime=timezone.now(),
        )
        article = Article.objects.create(
            slug='surviving-article',
            title='Surviving Article',
            date=date(2026, 7, 22),
            source_event=event,
        )

        event.delete()

        article.refresh_from_db()
        self.assertIsNone(article.source_event)
        self.assertTrue(article.published)


class ArticleSourceEventPublicTest(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            slug='source-session',
            title='Source Session',
            start_datetime=timezone.now() - timedelta(hours=2),
            end_datetime=timezone.now() - timedelta(hours=1),
            status='completed',
            published=True,
        )
        self.article = Article.objects.create(
            slug='source-article',
            title='Source Article',
            description='A useful article.',
            content_markdown='Full article body.',
            date=date(2026, 7, 22),
            required_level=20,
            source_event=self.event,
            published=True,
        )

    def test_gated_article_shows_public_source_event_handoff(self):
        response = self.client.get(self.article.get_absolute_url())

        self.assertContains(response, 'data-testid="gated-access-card"')
        self.assertContains(response, 'data-testid="article-source-event"')
        self.assertContains(response, 'From the event')
        self.assertContains(response, self.event.title)
        self.assertContains(response, self.event.get_absolute_url())
        self.assertContains(response, 'View source event')

    def test_private_or_non_public_event_states_never_leak(self):
        cases = (
            {'status': 'draft', 'published': True},
            {'status': 'cancelled', 'published': True},
            {'status': 'completed', 'published': False},
        )
        for values in cases:
            with self.subTest(**values):
                Event.objects.filter(pk=self.event.pk).update(**values)
                response = self.client.get(self.article.get_absolute_url())
                self.assertNotContains(response, 'article-source-event')
                self.assertNotContains(response, self.event.title)
                self.assertContains(response, 'gated-access-card')

    def test_unlinked_article_has_no_handoff(self):
        self.article.source_event = None
        self.article.save(update_fields=['source_event'])

        response = self.client.get(self.article.get_absolute_url())

        self.assertNotContains(response, 'article-source-event')


class EventSourceArticlesPublicTest(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            slug='article-producing-session',
            title='Article Producing Session',
            start_datetime=timezone.now() - timedelta(hours=2),
            end_datetime=timezone.now() - timedelta(hours=1),
            status='completed',
            published=True,
        )
        self.older = Article.objects.create(
            slug='older-source-article',
            title='Older Source Article',
            description='Older description.',
            date=date(2026, 7, 20),
            required_level=20,
            source_event=self.event,
            published=True,
        )
        self.newer = Article.objects.create(
            slug='newer-source-article',
            title='Newer Source Article',
            description='Newer description.',
            date=date(2026, 7, 22),
            source_event=self.event,
            published=True,
        )

    def test_event_lists_published_blog_articles_newest_first_with_badges(self):
        response = self.client.get(self.event.get_absolute_url())
        body = response.content.decode()

        self.assertContains(response, 'Articles from this event')
        self.assertContains(
            response,
            'data-testid="event-source-article-card"',
            count=2,
        )
        self.assertContains(response, self.older.get_absolute_url())
        self.assertContains(response, self.newer.get_absolute_url())
        self.assertContains(response, 'data-testid="event-source-article-access"', count=2)
        self.assertLess(body.index(self.newer.title), body.index(self.older.title))

    def test_deterministic_tie_order_uses_primary_key(self):
        self.older.date = self.newer.date
        self.older.save(update_fields=['date'])

        response = self.client.get(self.event.get_absolute_url())

        self.assertEqual(
            [article.pk for article in response.context['source_articles']],
            [self.older.pk, self.newer.pk],
        )

    def test_draft_non_blog_and_unpublished_rows_are_excluded(self):
        Article.objects.create(
            slug='draft-source-article',
            title='Draft Source Article',
            date=date(2026, 7, 23),
            source_event=self.event,
            published=False,
        )
        Article.objects.create(
            slug='learning-path-source',
            title='Learning Path Source',
            date=date(2026, 7, 23),
            source_event=self.event,
            published=True,
            page_type='learning_path',
        )

        response = self.client.get(self.event.get_absolute_url())

        self.assertNotContains(response, 'Draft Source Article')
        self.assertNotContains(response, 'Learning Path Source')

    def test_section_omitted_when_no_qualifying_articles(self):
        Article.objects.filter(pk__in=[self.older.pk, self.newer.pk]).update(
            published=False,
        )

        response = self.client.get(self.event.get_absolute_url())

        self.assertNotContains(response, 'Articles from this event')
        self.assertNotContains(response, 'event-source-articles')
