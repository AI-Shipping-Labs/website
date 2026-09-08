"""Tests for the community-base durable jobs integration (plan issue A1.1)."""

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django_q.models import Schedule

from jobs.tasks import community_base_jobs


class CommunityBaseScheduleRegistrationTest(TestCase):
    """`setup_schedules` registers the package housekeeping schedules."""

    def setUp(self):
        call_command("setup_schedules", stdout=StringIO())

    def test_run_due_schedule_registered_every_minute(self):
        row = Schedule.objects.get(name="cb-jobs-run-due")
        self.assertEqual(row.func, "jobs.tasks.community_base_jobs.run_due_jobs")
        self.assertEqual(row.cron, "* * * * *")
        self.assertEqual(row.schedule_type, Schedule.CRON)

    def test_sweep_schedule_registered_five_minute_cadence(self):
        row = Schedule.objects.get(name="cb-jobs-sweep")
        self.assertEqual(row.func, "jobs.tasks.community_base_jobs.sweep_jobs")
        self.assertEqual(row.cron, "*/5 * * * *")
        self.assertEqual(row.schedule_type, Schedule.CRON)

    def test_setup_schedules_is_idempotent(self):
        call_command("setup_schedules", stdout=StringIO())
        self.assertEqual(Schedule.objects.filter(name="cb-jobs-run-due").count(), 1)
        self.assertEqual(Schedule.objects.filter(name="cb-jobs-sweep").count(), 1)


class CommunityBaseTaskWrapperTest(TestCase):
    """The q-task wrappers invoke the package management commands."""

    @mock.patch("jobs.tasks.community_base_jobs.call_command")
    def test_run_due_jobs_calls_package_command(self, call_command_mock):
        community_base_jobs.run_due_jobs()
        call_command_mock.assert_called_once_with("jobs_run_due")

    @mock.patch("jobs.tasks.community_base_jobs.call_command")
    def test_sweep_jobs_calls_package_command(self, call_command_mock):
        community_base_jobs.sweep_jobs()
        call_command_mock.assert_called_once_with("jobs_sweep")


class CommunityBaseStudioPageTest(TestCase):
    """The mounted package intents page renders for staff; the local worker
    page links to it (browser screenshot passes run in Playwright; this pins
    the server-rendered contract)."""

    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            email="staff-jobs-viewer@test.com",
            password="test-password-123",
            is_staff=True,
        )

    def test_intents_page_renders_for_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get("/studio/jobs/")
        self.assertContains(response, "Durable")

    def test_worker_page_links_to_intents_page(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("studio_worker"))
        self.assertContains(response, 'href="/studio/jobs/"')
