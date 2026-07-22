"""Studio read-only source-event surfaces for issue #1331."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Article
from events.models import Event

User = get_user_model()


class StudioArticleSourceEventTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='article-source-staff@example.com',
            password='testpass',
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.event = Event.objects.create(
            slug='studio-source-event',
            title='Studio Source Event',
            start_datetime=timezone.now(),
            status='upcoming',
            published=True,
        )

    def test_linked_article_shows_public_and_studio_event_links_read_only(self):
        article = Article.objects.create(
            slug='studio-linked-article',
            title='Studio Linked Article',
            date=date(2026, 7, 22),
            source_event=self.event,
        )

        response = self.client.get(article.get_studio_edit_url())

        self.assertContains(response, 'Source event')
        self.assertContains(response, self.event.title)
        self.assertContains(response, self.event.get_absolute_url())
        self.assertContains(
            response,
            f'/studio/events/{self.event.pk}/edit',
        )
        self.assertContains(response, 'Edit in Studio')
        self.assertNotContains(response, 'name="source_event"')

    def test_unlinked_article_shows_no_source_event(self):
        article = Article.objects.create(
            slug='studio-unlinked-article',
            title='Studio Unlinked Article',
            date=date(2026, 7, 22),
        )

        response = self.client.get(article.get_studio_edit_url())

        self.assertContains(response, 'No source event')


class StudioEventSourceArticlesTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='event-source-staff@example.com',
            password='testpass',
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.event = Event.objects.create(
            slug='studio-article-event',
            title='Studio Article Event',
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=1),
            status='upcoming',
            published=True,
        )

    def test_event_lists_published_and_draft_articles_with_destinations(self):
        published = Article.objects.create(
            slug='studio-published-source',
            title='Studio Published Source',
            date=date(2026, 7, 22),
            source_event=self.event,
            published=True,
        )
        draft = Article.objects.create(
            slug='studio-draft-source',
            title='Studio Draft Source',
            date=date(2026, 7, 21),
            source_event=self.event,
            published=False,
        )

        response = self.client.get(
            f'/studio/events/{self.event.pk}/edit',
        )

        self.assertContains(response, 'Source articles')
        self.assertContains(response, published.title)
        self.assertContains(response, draft.title)
        self.assertContains(response, published.get_absolute_url())
        self.assertContains(response, draft.get_preview_url())
        self.assertContains(response, published.get_studio_edit_url())
        self.assertContains(response, draft.get_studio_edit_url())
        self.assertContains(response, 'Published')
        self.assertContains(response, 'Draft')
        self.assertNotContains(response, 'name="source_articles"')

    def test_event_without_articles_shows_no_source_articles(self):
        response = self.client.get(
            f'/studio/events/{self.event.pk}/edit',
        )

        self.assertContains(response, 'No source articles')
