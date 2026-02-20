"""
Tests for Django-Q2 admin integration.

Django-Q2 automatically registers admin views for Task, Schedule, and other models
when 'django_q' is in INSTALLED_APPS. These tests verify the admin is accessible.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model

User = get_user_model()


class DjangoQAdminTest(TestCase):
    """Tests that Django-Q2 admin views are registered and accessible."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email='admin@example.com',
            password='testpass123',
        )
        self.client.login(email='admin@example.com', password='testpass123')

    def test_task_admin_list(self):
        """Admin can view the task (successful) list page."""
        response = self.client.get('/admin/django_q/success/')
        self.assertEqual(response.status_code, 200)

    def test_failure_admin_list(self):
        """Admin can view the failed task list page."""
        response = self.client.get('/admin/django_q/failure/')
        self.assertEqual(response.status_code, 200)

    def test_schedule_admin_list(self):
        """Admin can view the schedule list page."""
        response = self.client.get('/admin/django_q/schedule/')
        self.assertEqual(response.status_code, 200)

    def test_ormq_admin_list(self):
        """Admin can view the queued tasks (OrmQ) list page."""
        response = self.client.get('/admin/django_q/ormq/')
        self.assertEqual(response.status_code, 200)
