"""
Management command to register default recurring job schedules.

Usage:
    python manage.py setup_schedules
"""

from django.core.management.base import BaseCommand

from jobs.tasks import schedule


class Command(BaseCommand):
    help = 'Register default recurring job schedules'

    def handle(self, *args, **options):
        # Health check every 15 minutes
        schedule(
            'jobs.tasks.healthcheck.health_check',
            cron='*/15 * * * *',
            name='health-check',
        )
        self.stdout.write(self.style.SUCCESS('Registered: health-check (every 15 min)'))

        # Cleanup old webhook logs daily at 3 AM
        schedule(
            'jobs.tasks.cleanup.cleanup_old_webhook_logs',
            cron='0 3 * * *',
            name='cleanup-webhook-logs',
            days=30,
        )
        self.stdout.write(self.style.SUCCESS('Registered: cleanup-webhook-logs (daily at 3 AM)'))

        # Event reminders every 15 minutes
        schedule(
            'notifications.services.event_reminders.check_event_reminders',
            cron='*/15 * * * *',
            name='event-reminders',
        )
        self.stdout.write(self.style.SUCCESS('Registered: event-reminders (every 15 min)'))

        # Expire tier overrides every 15 minutes
        schedule(
            'jobs.tasks.expire_overrides.expire_tier_overrides',
            cron='*/15 * * * *',
            name='expire-tier-overrides',
        )
        self.stdout.write(self.style.SUCCESS('Registered: expire-tier-overrides (every 15 min)'))

        # Refresh Slack workspace membership every 30 minutes (issue #358)
        schedule(
            'community.tasks.slack_membership.refresh_slack_membership',
            cron='*/30 * * * *',
            name='slack-membership-refresh',
        )
        self.stdout.write(self.style.SUCCESS('Registered: slack-membership-refresh (every 30 min)'))

        self.stdout.write(self.style.SUCCESS('All default schedules registered.'))
