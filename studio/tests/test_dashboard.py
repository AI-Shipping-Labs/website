"""Tests for studio dashboard.

Verifies that:
- Dashboard shows quick stats (courses, articles, subscribers, events)
- Dashboard shows recent articles and pending projects
- Dashboard template renders correctly
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.models import Article, Course, Recording, Download, Project
from events.models import Event
from email_app.models import NewsletterSubscriber, EmailCampaign

User = get_user_model()


class StudioDashboardTest(TestCase):
    """Test studio dashboard view and stats."""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_dashboard_returns_200(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)

    def test_dashboard_uses_correct_template(self):
        response = self.client.get('/studio/')
        self.assertTemplateUsed(response, 'studio/dashboard.html')
        self.assertTemplateUsed(response, 'studio/base.html')

    def test_dashboard_has_stats_in_context(self):
        response = self.client.get('/studio/')
        stats = response.context['stats']
        self.assertIn('total_courses', stats)
        self.assertIn('published_articles', stats)
        self.assertIn('active_subscribers', stats)
        self.assertIn('upcoming_events', stats)
        self.assertIn('total_recordings', stats)
        self.assertIn('total_downloads', stats)
        self.assertIn('pending_projects', stats)
        self.assertIn('total_campaigns', stats)

    def test_dashboard_counts_courses(self):
        Course.objects.create(title='C1', slug='c1', status='published')
        Course.objects.create(title='C2', slug='c2', status='draft')
        response = self.client.get('/studio/')
        stats = response.context['stats']
        self.assertEqual(stats['total_courses'], 2)
        self.assertEqual(stats['published_courses'], 1)

    def test_dashboard_counts_articles(self):
        Article.objects.create(
            title='A1', slug='a1', date=timezone.now().date(), published=True,
        )
        Article.objects.create(
            title='A2', slug='a2', date=timezone.now().date(), published=False,
        )
        response = self.client.get('/studio/')
        stats = response.context['stats']
        self.assertEqual(stats['total_articles'], 2)
        self.assertEqual(stats['published_articles'], 1)

    def test_dashboard_counts_subscribers(self):
        NewsletterSubscriber.objects.create(email='a@test.com', is_active=True)
        NewsletterSubscriber.objects.create(email='b@test.com', is_active=False)
        response = self.client.get('/studio/')
        stats = response.context['stats']
        self.assertEqual(stats['active_subscribers'], 1)
        self.assertEqual(stats['total_subscribers'], 2)

    def test_dashboard_counts_upcoming_events(self):
        Event.objects.create(
            title='E1', slug='e1', status='upcoming',
            start_datetime=timezone.now() + timezone.timedelta(days=1),
        )
        Event.objects.create(
            title='E2', slug='e2', status='completed',
            start_datetime=timezone.now() - timezone.timedelta(days=1),
        )
        response = self.client.get('/studio/')
        stats = response.context['stats']
        self.assertEqual(stats['upcoming_events'], 1)
        self.assertEqual(stats['total_events'], 2)

    def test_dashboard_counts_pending_projects(self):
        Project.objects.create(
            title='P1', slug='p1', date=timezone.now().date(),
            status='pending_review', published=False,
        )
        Project.objects.create(
            title='P2', slug='p2', date=timezone.now().date(),
            status='published', published=True,
        )
        response = self.client.get('/studio/')
        stats = response.context['stats']
        self.assertEqual(stats['pending_projects'], 1)

    def test_dashboard_shows_recent_articles(self):
        Article.objects.create(
            title='Recent Article', slug='recent',
            date=timezone.now().date(), published=True,
        )
        response = self.client.get('/studio/')
        self.assertEqual(len(response.context['recent_articles']), 1)
        self.assertContains(response, 'Recent Article')

    def test_dashboard_shows_pending_projects(self):
        Project.objects.create(
            title='Pending Project', slug='pend',
            date=timezone.now().date(), status='pending_review', published=False,
        )
        response = self.client.get('/studio/')
        self.assertEqual(len(response.context['pending_projects']), 1)
        self.assertContains(response, 'Pending Project')

    def test_dashboard_shows_sidebar_navigation(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'Courses')
        self.assertContains(response, 'Articles')
        self.assertContains(response, 'Events')
        self.assertContains(response, 'Recordings')
        self.assertContains(response, 'Campaigns')
        self.assertContains(response, 'Subscribers')
        self.assertContains(response, 'Downloads')
        self.assertContains(response, 'Projects')

    def test_dashboard_sidebar_has_sections(self):
        response = self.client.get('/studio/')
        content = response.content.decode()
        self.assertIn('Content', content)
        self.assertIn('Communications', content)
        self.assertIn('Community', content)

    def test_dashboard_has_studio_title(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'Studio')
