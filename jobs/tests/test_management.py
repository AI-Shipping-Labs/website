"""
Tests for the setup_schedules management command.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django_q.models import Schedule


class SetupSchedulesCommandTest(TestCase):
    """Tests for the setup_schedules management command."""

    def test_creates_health_check_schedule(self):
        """Command creates health-check schedule."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='health-check')
        self.assertEqual(schedule.func, 'jobs.tasks.healthcheck.health_check')
        self.assertEqual(schedule.cron, '*/15 * * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)

    def test_creates_cleanup_schedule(self):
        """Command creates cleanup-webhook-logs schedule."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='cleanup-webhook-logs')
        self.assertEqual(schedule.func, 'jobs.tasks.cleanup.cleanup_old_webhook_logs')
        self.assertEqual(schedule.cron, '0 3 * * *')

    def test_creates_event_reminders_schedule(self):
        """Command creates event-reminders schedule."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='event-reminders')
        self.assertEqual(schedule.func, 'notifications.services.event_reminders.check_event_reminders')
        self.assertEqual(schedule.cron, '*/15 * * * *')

    def test_creates_complete_finished_events_schedule(self):
        """Command creates complete-finished-events schedule (issue #573)."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='complete-finished-events')
        self.assertEqual(
            schedule.func,
            'events.tasks.complete_finished_events.complete_finished_events',
        )
        self.assertEqual(schedule.cron, '*/5 * * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)

    def test_creates_expire_tier_overrides_schedule(self):
        """Command creates expire-tier-overrides schedule."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='expire-tier-overrides')
        self.assertEqual(schedule.func, 'jobs.tasks.expire_overrides.expire_tier_overrides')
        self.assertEqual(schedule.cron, '*/15 * * * *')

    def test_creates_slack_membership_refresh_schedule(self):
        """Command creates slack-membership-refresh schedule (issue #358)."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='slack-membership-refresh')
        self.assertEqual(
            schedule.func,
            'community.tasks.slack_membership.refresh_slack_membership',
        )
        self.assertEqual(schedule.cron, '*/30 * * * *')

    def test_creates_remind_unverified_users_schedule(self):
        """Command creates remind-unverified-users schedule (issue #452)."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='remind-unverified-users')
        self.assertEqual(
            schedule.func,
            'accounts.tasks.remind_unverified_users',
        )
        self.assertEqual(schedule.cron, '0 7 * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)

    def test_creates_purge_unverified_users_schedule(self):
        """Command creates purge-unverified-users schedule (issue #452)."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='purge-unverified-users')
        self.assertEqual(
            schedule.func,
            'accounts.tasks.purge_unverified_users',
        )
        self.assertEqual(schedule.cron, '0 8 * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)

    def test_idempotent(self):
        """Running command twice does not create duplicate schedules."""
        call_command('setup_schedules', stdout=StringIO())
        call_command('setup_schedules', stdout=StringIO())
        self.assertEqual(Schedule.objects.filter(name='health-check').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='cleanup-webhook-logs').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='event-reminders').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='complete-finished-events').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='expire-tier-overrides').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='slack-membership-refresh').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='import-slack-daily').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='import-stripe-daily').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='remind-unverified-users').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='purge-unverified-users').count(), 1)

    def test_no_unexpected_schedules_created(self):
        """Command does not create any schedules outside the expected set."""
        call_command('setup_schedules', stdout=StringIO())
        names = set(Schedule.objects.values_list('name', flat=True))
        expected = {
            'health-check',
            'cleanup-webhook-logs',
            'event-reminders',
            'complete-finished-events',
            'expire-tier-overrides',
            'slack-membership-refresh',
            'import-slack-daily',
            'import-stripe-daily',
            'remind-unverified-users',
            'purge-unverified-users',
        }
        self.assertEqual(names, expected)

    def test_creates_daily_import_schedules(self):
        """Command creates daily Slack and Stripe import schedules."""
        call_command('setup_schedules', stdout=StringIO())

        slack = Schedule.objects.get(name='import-slack-daily')
        self.assertEqual(slack.func, 'accounts.tasks.run_scheduled_import')
        self.assertEqual(slack.cron, '0 3 * * *')
        self.assertEqual(slack.schedule_type, Schedule.CRON)
        self.assertEqual(slack.kwargs, "{'source': 'slack'}")

        stripe = Schedule.objects.get(name='import-stripe-daily')
        self.assertEqual(stripe.func, 'accounts.tasks.run_scheduled_import')
        self.assertEqual(stripe.cron, '30 3 * * *')
        self.assertEqual(stripe.schedule_type, Schedule.CRON)
        self.assertEqual(stripe.kwargs, "{'source': 'stripe'}")

    def test_setup_preserves_disabled_import_schedule(self):
        """Refreshing schedules keeps disabled import schedules disabled."""
        call_command('setup_schedules', stdout=StringIO())
        Schedule.objects.filter(name='import-slack-daily').update(repeats=0)

        call_command('setup_schedules', stdout=StringIO())

        self.assertEqual(Schedule.objects.filter(name='import-slack-daily').count(), 1)
        slack = Schedule.objects.get(name='import-slack-daily')
        self.assertEqual(slack.repeats, 0)
