"""Tests for studio dashboard.

Verifies that:
- Dashboard shows quick stats (courses, articles, subscribers, events)
- Dashboard shows recent articles and pending projects
- Dashboard template renders correctly
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from content.models import Article, Course, Project
from events.models import Event

User = get_user_model()


class StudioDashboardTest(TestCase):
    """Test studio dashboard view and stats."""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
            unsubscribed=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def _create_event(self, **overrides):
        defaults = {
            'title': 'Studio Event',
            'slug': 'studio-event',
            'status': 'upcoming',
            'platform': 'zoom',
            'start_datetime': timezone.now() + timezone.timedelta(days=1),
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    def _attention_item(self, response, label):
        return next(
            (
                item for item in response.context['attention_items']
                if item['label'] == label
            ),
            None,
        )

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
        User.objects.create_user(email='a@test.com', unsubscribed=False)
        User.objects.create_user(email='b@test.com', unsubscribed=True)
        response = self.client.get('/studio/')
        stats = response.context['stats']
        self.assertEqual(stats['active_subscribers'], 1)
        self.assertEqual(stats['total_subscribers'], 3)

    def test_dashboard_counts_upcoming_events(self):
        self._create_event(
            title='E1', slug='e1', status='upcoming',
            zoom_join_url='https://zoom.us/j/123',
        )
        self._create_event(
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
        """The view still surfaces pending projects via context for the
        attention summary; the dashboard no longer renders the project
        titles in a 'Next up' panel.
        """
        Project.objects.create(
            title='Pending Project', slug='pend',
            date=timezone.now().date(), status='pending_review', published=False,
        )
        response = self.client.get('/studio/')
        self.assertEqual(len(response.context['pending_projects']), 1)

    def test_dashboard_shows_sidebar_navigation(self):
        """The sidebar nav exposes the core content + outreach link labels.

        Issue #570 renamed ``Campaigns`` to ``Email campaigns`` and moved
        ``Subscribers`` out of the sidebar. The remaining labels are the
        ones every Studio operator relies on to navigate.
        """
        response = self.client.get('/studio/')
        for label in [
            'Courses',
            'Articles',
            'Events',
            'Recordings',
            'Email campaigns',
            'Downloads',
            'Projects',
        ]:
            self.assertContains(
                response, f'<span>{label}</span>', html=True,
            )

    def test_dashboard_sidebar_has_sections(self):
        """The reorganised sidebar renders collapsible section toggles
        for the live navigation groupings. The legacy
        ``Members``/``Events & Outreach``/``Users``/``System``/``Analytics``
        flat sections are gone; the marketing section was further split
        into communication and tracking.
        """
        response = self.client.get('/studio/')
        content = response.content.decode()
        for slug in (
            'content',
            'people',
            'events',
            'planning',
            'onboarding',
            'communication',
            'tracking',
            'operations',
        ):
            self.assertIn(
                f'aria-controls="studio-section-{slug}"',
                content,
                f'expected section toggle for {slug!r}',
            )

    def test_dashboard_has_studio_title(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'Studio')

    def test_dashboard_context_prioritizes_operational_sections(self):
        Project.objects.create(
            title='Needs Review',
            slug='needs-review',
            date=timezone.now().date(),
            status='pending_review',
            published=False,
        )
        Article.objects.create(
            title='Draft Article',
            slug='draft-article',
            date=timezone.now().date(),
            published=False,
        )
        self._create_event(
            title='Launch Session',
            slug='launch-session',
            status='upcoming',
            start_datetime=timezone.now() + timezone.timedelta(days=3),
            zoom_join_url='https://zoom.us/j/launch',
        )

        response = self.client.get('/studio/')

        attention_labels = [
            item['label'] for item in response.context['attention_items']
        ]
        self.assertIn('Pending project reviews', attention_labels)
        self.assertIn('Draft content', attention_labels)
        self.assertNotIn('Upcoming events', attention_labels)
        self.assertNotIn('Missing Zoom links', attention_labels)
        self.assertContains(response, 'Attention')
        self.assertContains(response, 'Recent activity')
        self.assertContains(response, 'Quick actions')
        self.assertContains(
            response,
            f'{reverse("studio_project_list")}?status=pending_review',
        )

    def test_dashboard_missing_zoom_link_attention_counts_one(self):
        self._create_event(slug='missing-zoom-link', zoom_join_url='')

        response = self.client.get('/studio/')

        item = self._attention_item(response, 'Missing Zoom links')
        self.assertIsNotNone(item)
        self.assertEqual(item['count'], 1)
        self.assertEqual(item['url'], reverse('studio_event_list'))
        self.assertIn('Add or check missing Zoom join links', item['description'])
        self.assertContains(response, 'Missing Zoom links')
        self.assertContains(response, 'Add or check missing Zoom join links')

    def test_dashboard_missing_zoom_link_attention_counts_multiple(self):
        self._create_event(slug='missing-zoom-link-1', zoom_join_url='')
        self._create_event(slug='missing-zoom-link-2', zoom_join_url='   ')

        response = self.client.get('/studio/')

        item = self._attention_item(response, 'Missing Zoom links')
        self.assertIsNotNone(item)
        self.assertEqual(item['count'], 2)

    def test_dashboard_missing_zoom_link_attention_excludes_non_qualifying_events(self):
        self._create_event(
            slug='zoom-with-link',
            zoom_join_url='https://zoom.us/j/123456',
        )
        self._create_event(
            slug='custom-platform',
            platform='custom',
            zoom_join_url='https://example.com/live',
        )
        self._create_event(
            slug='external-host',
            external_host='Maven',
            zoom_join_url='',
        )
        self._create_event(
            slug='past-zoom',
            start_datetime=timezone.now() - timezone.timedelta(days=1),
            zoom_join_url='',
        )
        self._create_event(slug='draft-zoom', status='draft', zoom_join_url='')
        self._create_event(
            slug='cancelled-zoom',
            status='cancelled',
            zoom_join_url='',
        )

        response = self.client.get('/studio/')

        attention_labels = [
            item['label'] for item in response.context['attention_items']
        ]
        self.assertNotIn('Missing Zoom links', attention_labels)
        self.assertNotIn('Upcoming events', attention_labels)
        self.assertNotContains(response, 'Check Zoom links')
        self.assertNotContains(response, 'Missing Zoom links')

    def test_dashboard_preserves_upcoming_event_summary_and_context(self):
        first = self._create_event(
            slug='future-zoom-linked',
            start_datetime=timezone.now() + timezone.timedelta(days=1),
            zoom_join_url='https://zoom.us/j/first',
        )
        second = self._create_event(
            slug='future-custom',
            platform='custom',
            start_datetime=timezone.now() + timezone.timedelta(days=2),
            zoom_join_url='https://example.com/live',
        )
        self._create_event(
            slug='past-linked',
            start_datetime=timezone.now() - timezone.timedelta(days=1),
            zoom_join_url='https://zoom.us/j/past',
        )
        self._create_event(
            slug='draft-linked',
            status='draft',
            zoom_join_url='https://zoom.us/j/draft',
        )
        self._create_event(
            slug='cancelled-linked',
            status='cancelled',
            zoom_join_url='https://zoom.us/j/cancelled',
        )

        response = self.client.get('/studio/')

        self.assertEqual(response.context['stats']['upcoming_events'], 2)
        events_metric = next(
            metric for metric in response.context['summary_metrics']
            if metric['label'] == 'Events'
        )
        self.assertEqual(events_metric['value'], 2)
        self.assertEqual(list(response.context['upcoming_events']), [first, second])

    @patch('studio.views.dashboard.OrmQ.objects.count', return_value=0)
    def test_worker_down_attention_item_uses_health_value(self, mock_queue_count):
        with patch('studio.views.dashboard.get_worker_status', return_value={
            'alive': False,
            'cluster_count': 0,
            'last_heartbeat_age': None,
            'idle': False,
            'clusters': [],
            'expect_worker': True,
            'error': None,
        }):
            response = self.client.get('/studio/')

        worker_item = next(
            item for item in response.context['attention_items']
            if item['label'] == 'Worker not running'
        )
        self.assertEqual(worker_item['count'], 'Down')
        self.assertIn('0 queued tasks', worker_item['description'])
        self.assertContains(response, '>Down<')
        self.assertContains(response, '0 queued tasks')
        self.assertNotContains(response, '>0</span>\n                <span class="text-sm font-medium text-foreground">Worker not running</span>')

    def test_dashboard_quick_actions_link_to_valid_studio_pages(self):
        response = self.client.get('/studio/')

        quick_actions = response.context['quick_actions']
        self.assertEqual(
            {action['label'] for action in quick_actions},
            {
                'Sync Dashboard',
                'Courses',
                'Users',
                'Project reviews',
                'Events',
                'Worker dashboard',
            },
        )

        for action in quick_actions:
            action_response = self.client.get(action['url'])
            self.assertNotEqual(
                action_response.status_code,
                404,
                f'{action["label"]} linked to 404 at {action["url"]}',
            )

    def test_dashboard_recent_activity_and_empty_states(self):
        member = User.objects.create_user(
            email='recent-member@test.com',
            password='testpass',
            is_staff=False,
        )
        Article.objects.create(
            title='Recently Edited',
            slug='recently-edited',
            date=timezone.now().date(),
            published=True,
        )

        response = self.client.get('/studio/')

        self.assertEqual(list(response.context['recent_users']), [member])
        self.assertContains(response, 'recent-member@test.com')
        self.assertContains(response, 'Recently Edited')

    def test_dashboard_empty_states_render_without_recent_data(self):
        with patch('studio.views.dashboard.get_worker_status', return_value={
            'alive': False,
            'cluster_count': 0,
            'last_heartbeat_age': None,
            'idle': False,
            'clusters': [],
            'expect_worker': False,
            'error': None,
        }):
            response = self.client.get('/studio/')

        self.assertEqual(list(response.context['recent_users']), [])
        self.assertEqual(response.context['recent_content'], [])
        self.assertContains(response, 'Nothing needs immediate attention.')
        self.assertContains(response, 'No users yet.')
        self.assertContains(response, 'No content changes yet.')
