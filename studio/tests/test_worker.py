"""Tests for the studio worker status dashboard.

Verifies that:
- Worker status page shows health indicator based on recent task activity
- Recent tasks are listed with status, duration, and error details
- Queue depth (pending tasks) is displayed
- Failed tasks section shows error details
- Staff-only access is enforced
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from django_q.models import OrmQ, Task

User = get_user_model()


def _create_task(**kwargs):
    """Create a django-q Task with an auto-generated id."""
    if 'id' not in kwargs:
        kwargs['id'] = uuid.uuid4().hex
    return Task.objects.create(**kwargs)


class WorkerStatusAccessTest(TestCase):
    """Test that the worker status page enforces staff-only access."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    def test_anonymous_redirects_to_login(self):
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_gets_403(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 403)

    def test_staff_gets_200(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 200)


class WorkerStatusHealthTest(TestCase):
    """Test worker health indicator based on recent task completions."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_worker_inactive_when_no_recent_tasks(self):
        response = self.client.get('/studio/worker/')
        self.assertFalse(response.context['worker_healthy'])
        self.assertContains(response, 'Worker Inactive')

    def test_worker_active_when_recent_task_completed(self):
        now = timezone.now()
        _create_task(
            name='recent-task',
            func='some.func',
            started=now - timedelta(minutes=2),
            stopped=now - timedelta(minutes=1),
            success=True,
        )
        response = self.client.get('/studio/worker/')
        self.assertTrue(response.context['worker_healthy'])
        self.assertContains(response, 'Worker Active')

    def test_worker_inactive_when_tasks_are_old(self):
        now = timezone.now()
        _create_task(
            name='old-task',
            func='some.func',
            started=now - timedelta(minutes=30),
            stopped=now - timedelta(minutes=20),
            success=True,
        )
        response = self.client.get('/studio/worker/')
        self.assertFalse(response.context['worker_healthy'])
        self.assertContains(response, 'Worker Inactive')


class WorkerStatusQueueDepthTest(TestCase):
    """Test that queue depth shows the number of pending tasks."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_queue_depth_zero_when_empty(self):
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.context['queue_depth'], 0)

    def test_queue_depth_reflects_pending_tasks(self):
        OrmQ.objects.create(key='task-1', payload='{}')
        OrmQ.objects.create(key='task-2', payload='{}')
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.context['queue_depth'], 2)


class WorkerStatusRecentTasksTest(TestCase):
    """Test that recent tasks are listed with correct details."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_recent_tasks_displayed_in_table(self):
        now = timezone.now()
        _create_task(
            name='sync-content',
            func='integrations.services.github.sync_content_source',
            started=now - timedelta(seconds=30),
            stopped=now - timedelta(seconds=10),
            success=True,
        )
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'sync-content')
        self.assertContains(response, 'Success')

    def test_failed_task_shows_in_table(self):
        now = timezone.now()
        _create_task(
            name='failing-task',
            func='some.broken.func',
            started=now - timedelta(seconds=30),
            stopped=now - timedelta(seconds=20),
            success=False,
            result='ConnectionError: timeout',
        )
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'failing-task')
        self.assertContains(response, 'Failed')

    def test_empty_state_when_no_tasks(self):
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'No tasks recorded yet.')

    def test_success_and_failure_counts(self):
        now = timezone.now()
        _create_task(
            name='ok1', func='f', started=now, stopped=now, success=True,
        )
        _create_task(
            name='ok2', func='f', started=now, stopped=now, success=True,
        )
        _create_task(
            name='fail1', func='f', started=now, stopped=now, success=False,
        )
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.context['success_count'], 2)
        self.assertEqual(response.context['failure_count'], 1)


class WorkerStatusFailedTasksTest(TestCase):
    """Test that the failed tasks section shows error details."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_failed_tasks_section_shows_error_details(self):
        now = timezone.now()
        _create_task(
            name='broken-sync',
            func='integrations.services.github.sync_content_source',
            started=now - timedelta(seconds=60),
            stopped=now - timedelta(seconds=50),
            success=False,
            result='RuntimeError: GitHub API rate limit exceeded',
        )
        response = self.client.get('/studio/worker/')
        self.assertEqual(len(response.context['failed_with_details']), 1)
        self.assertContains(response, 'Failed Tasks')
        self.assertContains(response, 'broken-sync')
        self.assertContains(response, 'RuntimeError: GitHub API rate limit exceeded')

    def test_no_failed_section_when_all_succeed(self):
        now = timezone.now()
        _create_task(
            name='good-task', func='f', started=now, stopped=now, success=True,
        )
        response = self.client.get('/studio/worker/')
        self.assertEqual(len(response.context['failed_with_details']), 0)


class WorkerStatusTemplateTest(TestCase):
    """Test that the worker page uses the correct template and has sidebar link."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_uses_correct_template(self):
        response = self.client.get('/studio/worker/')
        self.assertTemplateUsed(response, 'studio/worker.html')
        self.assertTemplateUsed(response, 'studio/base.html')

    def test_sidebar_has_worker_link(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'Worker')
        self.assertContains(response, '/studio/worker/')
