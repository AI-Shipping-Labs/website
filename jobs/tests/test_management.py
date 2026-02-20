"""
Tests for the setup_schedules management command.
"""

from django.test import TestCase
from django.core.management import call_command

from django_q.models import Schedule


class SetupSchedulesCommandTest(TestCase):
    """Tests for the setup_schedules management command."""

    def test_creates_health_check_schedule(self):
        """Command creates health-check schedule."""
        call_command('setup_schedules')
        schedule = Schedule.objects.get(name='health-check')
        self.assertEqual(schedule.func, 'jobs.tasks.healthcheck.health_check')
        self.assertEqual(schedule.cron, '*/15 * * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)

    def test_creates_cleanup_schedule(self):
        """Command creates cleanup-webhook-logs schedule."""
        call_command('setup_schedules')
        schedule = Schedule.objects.get(name='cleanup-webhook-logs')
        self.assertEqual(schedule.func, 'jobs.tasks.cleanup.cleanup_old_webhook_logs')
        self.assertEqual(schedule.cron, '0 3 * * *')

    def test_creates_event_reminders_schedule(self):
        """Command creates event-reminders schedule."""
        call_command('setup_schedules')
        schedule = Schedule.objects.get(name='event-reminders')
        self.assertEqual(schedule.func, 'notifications.services.event_reminders.check_event_reminders')
        self.assertEqual(schedule.cron, '*/15 * * * *')

    def test_idempotent(self):
        """Running command twice does not create duplicate schedules."""
        call_command('setup_schedules')
        call_command('setup_schedules')
        self.assertEqual(Schedule.objects.filter(name='health-check').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='cleanup-webhook-logs').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='event-reminders').count(), 1)

    def test_total_schedules_created(self):
        """Command creates exactly the expected number of schedules."""
        call_command('setup_schedules')
        self.assertEqual(Schedule.objects.count(), 3)
