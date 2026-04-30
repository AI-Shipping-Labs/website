"""Tests for long Studio edit form ergonomics."""

from datetime import datetime

from django.test import TestCase
from django.utils import timezone

from content.models import Article, Course
from events.models import Event
from tests.fixtures import StaffUserMixin


class StudioLongFormErgonomicsTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_course_edit_has_sticky_save_and_meta_panel(self):
        course = Course.objects.create(
            title='Long Course',
            slug='long-course',
            status='published',
            required_level=20,
            individual_price_eur='49.00',
        )

        response = self.client.get(f'/studio/courses/{course.pk}/edit')

        self.assertContains(response, 'data-testid="sticky-action-bar"')
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'form="course-edit-form"')
        self.assertContains(response, 'data-testid="studio-meta-actions-panel"')
        self.assertContains(response, 'Manage Access')
        self.assertContains(response, 'Manage Peer Reviews')
        self.assertContains(response, 'Manage Enrollments')
        self.assertContains(response, 'data-testid="notification-actions"')
        self.assertContains(response, 'data-testid="stripe-product-panel"')

    def test_synced_course_sticky_bar_uses_source_actions_only(self):
        course = Course.objects.create(
            title='Synced Course',
            slug='synced-course',
            status='published',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/synced-course/course.yaml',
        )

        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        content = response.content.decode()

        self.assertContains(response, 'data-testid="sticky-action-bar"')
        self.assertContains(response, 'Source-managed course')
        self.assertNotContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'data-testid="sticky-github-source-link"')
        self.assertContains(response, 'data-testid="sticky-resync-source-button"')
        self.assertEqual(content.count('data-testid="resync-source-button"'), 1)

    def test_event_edit_has_sticky_save_and_integration_panel(self):
        event = Event.objects.create(
            title='Long Event',
            slug='long-event',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=datetime(2026, 6, 1, 11, 0),
            status='upcoming',
            required_level=10,
        )

        response = self.client.get(f'/studio/events/{event.pk}/edit')

        self.assertContains(response, 'data-testid="sticky-action-bar"')
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'form="event-edit-form"')
        self.assertContains(response, 'data-testid="studio-meta-actions-panel"')
        self.assertContains(response, 'data-testid="zoom-meeting-panel"')
        self.assertContains(response, 'Create Zoom Meeting')
        self.assertContains(response, 'data-testid="notification-actions"')
        self.assertContains(response, 'data-testid="registration-count"')

    def test_synced_event_sticky_save_is_limited_to_operational_fields(self):
        event = Event.objects.create(
            title='Synced Event',
            slug='synced-event',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            status='draft',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/synced-event.md',
        )

        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()

        self.assertContains(response, 'data-testid="sticky-action-bar"')
        self.assertContains(response, 'Save Operational Fields')
        self.assertNotContains(response, '>Save Changes</span>')
        self.assertContains(response, 'data-testid="origin-panel"')
        self.assertEqual(content.count('data-testid="resync-source-button"'), 1)

    def test_article_edit_has_sticky_save_and_meta_panel(self):
        article = Article.objects.create(
            title='Long Article',
            slug='long-article',
            date=timezone.now().date(),
            published=True,
            required_level=10,
        )

        response = self.client.get(f'/studio/articles/{article.pk}/edit')

        self.assertContains(response, 'data-testid="sticky-action-bar"')
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'form="article-edit-form"')
        self.assertContains(response, 'data-testid="studio-meta-actions-panel"')
        self.assertContains(response, 'data-testid="article-state-panel"')
        self.assertContains(response, 'data-testid="notification-actions"')
        self.assertContains(response, 'data-testid="panel-view-on-site"')

    def test_synced_article_sticky_bar_uses_source_actions_only(self):
        article = Article.objects.create(
            title='Synced Article',
            slug='synced-article',
            date=timezone.now().date(),
            published=True,
            source_repo='AI-Shipping-Labs/content',
            source_path='blog/synced-article.md',
        )

        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        content = response.content.decode()

        self.assertContains(response, 'data-testid="sticky-action-bar"')
        self.assertContains(response, 'Source-managed article')
        self.assertNotContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'data-testid="sticky-github-source-link"')
        self.assertContains(response, 'data-testid="sticky-resync-source-button"')
        self.assertEqual(content.count('data-testid="resync-source-button"'), 1)
