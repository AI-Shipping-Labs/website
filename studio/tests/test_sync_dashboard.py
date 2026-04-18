"""Tests for Studio content sync dashboard views."""

import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from integrations.models import ContentSource, SyncLog

User = get_user_model()


class StudioSyncDashboardTest(TestCase):
    """Test the unified sync dashboard view."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_dashboard_returns_200(self):
        response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)

    def test_dashboard_uses_correct_template(self):
        response = self.client.get('/studio/sync/')
        self.assertTemplateUsed(response, 'studio/sync/dashboard.html')

    def test_dashboard_shows_repo_name(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'AI-Shipping-Labs/content')

    def test_dashboard_groups_sources_by_repo(self):
        """Multiple content types from same repo appear as one card."""
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        response = self.client.get('/studio/sync/')
        # Context should have exactly one repo entry
        self.assertEqual(len(response.context['repos']), 1)
        self.assertEqual(len(response.context['repos'][0]['sources']), 2)

    def test_dashboard_shows_content_type_count(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '2 content types')

    def test_dashboard_shows_sync_status(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'success')

    def test_dashboard_shows_never_synced(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Never synced')

    def test_dashboard_empty_state(self):
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'No content sources configured')

    def test_dashboard_has_sync_all_button(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Sync All')

    def test_dashboard_per_repo_button_posts_to_repo_trigger(self):
        """The per-repo button must post to ``studio_sync_repo_trigger`` with
        the repo name, not to ``studio_sync_all`` (issue #232).
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(
            response,
            'action="/studio/sync/AI-Shipping-Labs/content/trigger-repo/"',
        )
        # Button label is the generic ``Sync now`` (not per-content-type).
        self.assertContains(response, 'Sync now')

    def test_dashboard_per_repo_button_does_not_post_to_sync_all(self):
        """Regression test for #232: per-repo button must not point to
        ``/studio/sync/all/`` (which would trigger every repo, not one).
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        response = self.client.get('/studio/sync/')
        html = response.content.decode()
        # Exactly one form on the page should post to /studio/sync/all/ —
        # the top-level Sync All button. The per-repo card must use the
        # new repo-trigger URL.
        self.assertEqual(html.count('action="/studio/sync/all/"'), 1)
        self.assertIn(
            'action="/studio/sync/AI-Shipping-Labs/content/trigger-repo/"',
            html,
        )

    def test_dashboard_renders_one_button_per_repo(self):
        """A repo with N content sources renders ONE ``Sync now`` button
        (the fan-out happens server-side, see issue #232).
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        response = self.client.get('/studio/sync/')
        html = response.content.decode()
        # One per-repo trigger form for the single repo card.
        self.assertEqual(
            html.count(
                'action="/studio/sync/AI-Shipping-Labs/content/trigger-repo/"'
            ),
            1,
        )

    def test_dashboard_has_history_link(self):
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '/studio/sync/history/')

    def test_dashboard_shows_last_batch_results(self):
        """Dashboard shows per-content-type breakdown from latest sync."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        batch_id = uuid.uuid4()
        SyncLog.objects.create(
            source=source,
            batch_id=batch_id,
            status='success',
            items_created=3,
            items_updated=2,
            items_deleted=0,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '+3 created')
        self.assertContains(response, '2 updated')

    def test_dashboard_shows_tiers_synced(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            tiers_synced=True,
            tiers_count=3,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Tiers')
        self.assertContains(response, '3 tiers')

    def test_dashboard_does_not_leak_other_repos_logs_via_batch_id(self):
        """A Sync All batch shares one batch_id across repos. Each card must
        only show its own repo's per-type rows, not the other repo's.
        """
        batch_id = uuid.uuid4()

        course_src = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=course_src,
            batch_id=batch_id,
            status='success',
            items_updated=10,
            finished_at=timezone.now(),
        )

        content_project_src = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=content_project_src,
            batch_id=batch_id,
            status='success',
            items_updated=10,
            finished_at=timezone.now(),
        )

        response = self.client.get('/studio/sync/')
        repos = {repo['repo_name']: repo for repo in response.context['repos']}

        course_card = repos['AI-Shipping-Labs/python-course']
        course_types = [row['content_type'] for row in course_card['last_batch']['per_type']]
        self.assertEqual(course_types, ['course'])

        content_card = repos['AI-Shipping-Labs/content']
        content_types = [row['content_type'] for row in content_card['last_batch']['per_type']]
        self.assertEqual(content_types, ['project'])

    def test_dashboard_shows_items_detail(self):
        """Changed items are listed with links."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_created=1,
            items_detail=[{
                'title': 'My New Article',
                'slug': 'my-new-article',
                'action': 'created',
                'content_type': 'article',
            }],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'My New Article')
        self.assertContains(response, '/blog/my-new-article')

    def test_dashboard_requires_staff(self):
        client = Client()
        response = client.get('/studio/sync/')
        self.assertEqual(response.status_code, 302)

    def test_dashboard_non_staff_gets_403(self):
        User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )
        client = Client()
        client.login(email='user@test.com', password='testpass')
        response = client.get('/studio/sync/')
        self.assertEqual(response.status_code, 403)


class StudioSyncHistoryTest(TestCase):
    """Test the aggregated sync history view."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_history_returns_200(self):
        response = self.client.get('/studio/sync/history/')
        self.assertEqual(response.status_code, 200)

    def test_history_uses_correct_template(self):
        response = self.client.get('/studio/sync/history/')
        self.assertTemplateUsed(response, 'studio/sync/history.html')

    def test_history_shows_batch_with_counts(self):
        batch_id = uuid.uuid4()
        SyncLog.objects.create(
            source=self.source,
            batch_id=batch_id,
            status='success',
            items_created=5,
            items_updated=2,
            items_deleted=0,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, 'success')
        self.assertContains(response, '+5 created')
        self.assertContains(response, '2 updated')

    def test_history_shows_errors(self):
        SyncLog.objects.create(
            source=self.source,
            status='partial',
            errors=[{'file': 'test.md', 'error': 'parse error'}],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, 'test.md')
        self.assertContains(response, 'parse error')

    def test_history_empty_state(self):
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, 'No sync history yet')

    def test_history_requires_staff(self):
        client = Client()
        response = client.get('/studio/sync/history/')
        self.assertEqual(response.status_code, 302)

    def test_history_has_back_link(self):
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, '/studio/sync/')
        self.assertContains(response, 'Back to Content Sync')

    def test_history_aggregates_batch(self):
        """Logs with same batch_id are aggregated into one entry."""
        batch_id = uuid.uuid4()
        source2 = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        SyncLog.objects.create(
            source=self.source,
            batch_id=batch_id,
            status='success',
            items_created=3,
            finished_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source2,
            batch_id=batch_id,
            status='success',
            items_created=1,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        # Should show aggregated count and source count
        self.assertContains(response, '2 sources')

    def test_history_shows_tiers_synced(self):
        SyncLog.objects.create(
            source=self.source,
            status='success',
            tiers_synced=True,
            tiers_count=4,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, 'tiers synced')


class StudioSyncTriggerTest(TestCase):
    """Test the sync trigger endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_trigger_redirects_to_sync_dashboard(self, mock_async):
        """After enqueuing, the user stays on /studio/sync/ — being yanked
        to the worker page interrupted operator flow (see issue #239)."""
        response = self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_trigger_calls_sync(self, mock_async):
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        mock_async.assert_called_once()
        args = mock_async.call_args
        self.assertEqual(args[0][0], 'integrations.services.github.sync_content_source')

    def test_trigger_requires_post(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 405)

    def test_trigger_requires_staff(self):
        client = Client()
        response = client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)  # redirect to login

    def test_trigger_nonexistent_source_returns_404(self):
        fake_id = uuid.uuid4()
        response = self.client.post(f'/studio/sync/{fake_id}/trigger/')
        self.assertEqual(response.status_code, 404)

    @patch('django_q.tasks.async_task', side_effect=Exception('queue error'))
    def test_trigger_handles_sync_error(self, mock_async):
        response = self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)

    @patch('django_q.tasks.async_task')
    def test_trigger_only_syncs_targeted_source(self, mock_async):
        """Regression for #232: posting to /studio/sync/<id>/trigger/ must
        sync only that source, not every configured source.
        """
        # Create additional sources to make sure they're NOT triggered.
        other = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        # Exactly one async_task call (vs three if it had hit sync_all).
        mock_async.assert_called_once()
        # The single call must be for the targeted source, not the others.
        synced_source = mock_async.call_args[0][1]
        self.assertEqual(synced_source.pk, self.source.pk)
        self.assertNotEqual(synced_source.pk, other.pk)


class StudioSyncRepoTriggerTest(TestCase):
    """Test the per-repo fan-out trigger endpoint (issue #232)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_fans_out_to_all_sources(self, mock_async):
        """Posting to /studio/sync/<repo_name>/trigger-repo/ enqueues one
        task per ContentSource sharing that repo_name.
        """
        article = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        course = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        project = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        response = self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_async.call_count, 3)
        # Every call must target a ContentSource from the targeted repo.
        synced_pks = {call.args[1].pk for call in mock_async.call_args_list}
        self.assertEqual(synced_pks, {article.pk, course.pk, project.pk})

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_creates_one_batch_id(self, mock_async):
        """All fan-out calls share one batch_id so the batch shows up as a
        single row in history and the dashboard aggregator finds them.
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        batch_ids = [
            call.kwargs.get('batch_id')
            for call in mock_async.call_args_list
        ]
        self.assertEqual(len(batch_ids), 3)
        self.assertIsNotNone(batch_ids[0])
        self.assertEqual(len(set(batch_ids)), 1)

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_doesnt_touch_other_repos(self, mock_async):
        """Posting for one repo must NOT enqueue tasks for any other repo's
        sources (regression for the bug where the per-row button hit
        /studio/sync/all/).
        """
        target = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        other_repo_course = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            content_path='units/',
        )
        other_repo_article = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='article',
            content_path='posts/',
        )
        self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        synced_pks = {call.args[1].pk for call in mock_async.call_args_list}
        self.assertEqual(synced_pks, {target.pk})
        self.assertNotIn(other_repo_course.pk, synced_pks)
        self.assertNotIn(other_repo_article.pk, synced_pks)

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_redirects_to_sync_dashboard(self, mock_async):
        """Per #239, sync actions stay on /studio/sync/ rather than yanking
        the operator to /studio/worker/.
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_flash_names_the_repo(self, mock_async):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        response = self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/',
            follow=True,
        )
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, '2 sources')
        # Flash message includes a link back to the worker page so operators
        # can still watch the queue (consistent with sync_trigger / sync_all).
        self.assertContains(response, '/studio/worker/')

    def test_repo_trigger_unknown_repo_redirects_with_error(self):
        response = self.client.post(
            '/studio/sync/AI-Shipping-Labs/no-such-repo/trigger-repo/',
            follow=True,
        )
        self.assertContains(response, 'No content sources configured')

    def test_repo_trigger_requires_post(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        self.assertEqual(response.status_code, 405)

    def test_repo_trigger_requires_staff(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        client = Client()
        response = client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        self.assertEqual(response.status_code, 302)


class StudioSyncAllTest(TestCase):
    """Test the sync all endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_sync_all_redirects_to_sync_dashboard(self, mock_async):
        """Sync All redirects back to the sync dashboard so the operator
        can watch every per-source row update in place (see issue #239)."""
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_sync_all_triggers_all_sources(self, mock_async):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        self.client.post('/studio/sync/all/')
        self.assertEqual(mock_async.call_count, 2)

    @patch('django_q.tasks.async_task')
    def test_sync_all_passes_batch_id(self, mock_async):
        """Sync All passes a shared batch_id to all source syncs."""
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        self.client.post('/studio/sync/all/')
        # Both calls should have the same batch_id kwarg
        batch_ids = [call.kwargs.get('batch_id') for call in mock_async.call_args_list]
        self.assertEqual(len(batch_ids), 2)
        self.assertIsNotNone(batch_ids[0])
        self.assertEqual(batch_ids[0], batch_ids[1])

    def test_sync_all_requires_post(self):
        response = self.client.get('/studio/sync/all/')
        self.assertEqual(response.status_code, 405)

    def test_sync_all_requires_staff(self):
        client = Client()
        response = client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)

    @patch('django_q.tasks.async_task')
    def test_sync_all_with_no_sources(self, mock_async):
        response = self.client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)
        mock_async.assert_not_called()


class StudioSyncStatusTest(TestCase):
    """Test the JSON status polling endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_status_returns_json(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/status/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        data = json.loads(response.content)
        self.assertEqual(data['id'], str(self.source.pk))
        self.assertEqual(data['last_sync_status'], 'success')
        self.assertIsNotNone(data['last_synced_at'])

    def test_status_returns_null_for_never_synced(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
        )
        response = self.client.get(f'/studio/sync/{source.pk}/status/')
        data = json.loads(response.content)
        self.assertIsNone(data['last_sync_status'])
        self.assertIsNone(data['last_synced_at'])

    def test_status_requires_staff(self):
        client = Client()
        response = client.get(f'/studio/sync/{self.source.pk}/status/')
        self.assertEqual(response.status_code, 302)

    def test_status_nonexistent_returns_404(self):
        fake_id = uuid.uuid4()
        response = self.client.get(f'/studio/sync/{fake_id}/status/')
        self.assertEqual(response.status_code, 404)


class StudioSidebarSyncLinkTest(TestCase):
    """Test that the Content Sync link appears in the Studio sidebar."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_sidebar_has_content_sync_link(self):
        response = self.client.get('/studio/')
        self.assertContains(response, '/studio/sync/')
        self.assertContains(response, 'Content Sync')


class SyncLogModelTest(TestCase):
    """Test the SyncLog model new fields."""

    @classmethod
    def setUpTestData(cls):
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )

    def test_batch_id_groups_logs(self):
        batch_id = uuid.uuid4()
        SyncLog.objects.create(
            source=self.source, batch_id=batch_id, status='success',
        )
        source2 = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        SyncLog.objects.create(
            source=source2, batch_id=batch_id, status='success',
        )
        batch_logs = SyncLog.objects.filter(batch_id=batch_id)
        self.assertEqual(batch_logs.count(), 2)

    def test_items_detail_stores_json(self):
        detail = [
            {'title': 'Test', 'slug': 'test', 'action': 'created', 'content_type': 'article'},
        ]
        log = SyncLog.objects.create(
            source=self.source, status='success', items_detail=detail,
        )
        log.refresh_from_db()
        self.assertEqual(len(log.items_detail), 1)
        self.assertEqual(log.items_detail[0]['title'], 'Test')

    def test_tiers_synced_field(self):
        log = SyncLog.objects.create(
            source=self.source, status='success',
            tiers_synced=True, tiers_count=3,
        )
        log.refresh_from_db()
        self.assertTrue(log.tiers_synced)
        self.assertEqual(log.tiers_count, 3)
