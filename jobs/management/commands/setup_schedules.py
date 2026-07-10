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

        # Prune old outbound webhook-delivery rows daily at 03:10 UTC
        # (issue #1070). Reuses the cleanup-webhook-logs wiring/cadence
        # rather than a new bespoke cron; 10 minutes after the inbound log
        # cleanup so the two pruning jobs don't overlap.
        schedule(
            'jobs.tasks.cleanup.cleanup_old_webhook_deliveries',
            cron='10 3 * * *',
            name='cleanup-webhook-deliveries',
            days=30,
        )
        self.stdout.write(self.style.SUCCESS('Registered: cleanup-webhook-deliveries (daily at 03:10 UTC)'))

        # Purge old per-user CRM activity timeline rows daily at 03:30 UTC
        # (issue #853). Off-peak, after the webhook-log cleanup. The
        # retention window comes from the Studio-editable
        # USER_ACTIVITY_RETENTION_DAYS setting (default 365 days).
        schedule(
            'analytics.tasks.purge_old_user_activity',
            cron='30 3 * * *',
            name='purge-user-activity',
        )
        self.stdout.write(self.style.SUCCESS('Registered: purge-user-activity (daily at 03:30 UTC)'))

        # Event reminders every 15 min (issue #1001; restored from the
        # hourly '0 * * * *' set in #919). The reminder windows in
        # event_reminders.py are a 10-min-wide 20m window and a 30-min-wide
        # 24h window engineered for a */15 tick — at hourly cadence the
        # 20-min reminder can never fire for events starting outside :15..:25,
        # so #919's "hourly is enough" was wrong. A punctual "starts in 20
        # minutes" reminder fundamentally needs a sub-20-min cadence.
        # update_or_create on the schedule name means an existing row's cron
        # is updated in place on the next setup_schedules run.
        schedule(
            'notifications.services.event_reminders.check_event_reminders',
            cron='*/15 * * * *',
            name='event-reminders',
        )
        self.stdout.write(self.style.SUCCESS('Registered: event-reminders (every 15 min)'))

        # Flip finished events from upcoming to completed daily at 04:00 UTC
        # (issue #573; cadence reduced from every 5 min to daily in #713 now
        # that user-facing surfaces derive past/upcoming from timestamps).
        schedule(
            'events.tasks.complete_finished_events.complete_finished_events',
            cron='0 4 * * *',
            name='complete-finished-events',
        )
        self.stdout.write(self.style.SUCCESS('Registered: complete-finished-events (daily at 04:00 UTC)'))

        # Expire tier overrides every 15 minutes
        schedule(
            'jobs.tasks.expire_overrides.expire_tier_overrides',
            cron='*/15 * * * *',
            name='expire-tier-overrides',
        )
        self.stdout.write(self.style.SUCCESS('Registered: expire-tier-overrides (every 15 min)'))

        # Refresh Slack workspace membership once per day at 06:00 UTC
        # (issue #358 introduced it; issue #919 reduced cadence from every
        # 30 min to daily — once per day is enough). 06:00 UTC is a
        # low-traffic hour clear of the 03:00-05:00 import/ingest jobs and
        # the 07:00-08:00 unverified-user sweep. update_or_create on the
        # schedule name updates an existing row's cron in place on the next
        # setup_schedules run.
        schedule(
            'community.tasks.slack_membership.refresh_slack_membership',
            cron='0 6 * * *',
            name='slack-membership-refresh',
        )
        self.stdout.write(self.style.SUCCESS('Registered: slack-membership-refresh (daily at 06:00 UTC)'))

        # Daily system imports for external user sources (issue #318)
        schedule(
            'accounts.tasks.run_scheduled_import',
            cron='0 3 * * *',
            name='import-slack-daily',
            preserve_disabled=True,
            source='slack',
        )
        self.stdout.write(self.style.SUCCESS('Registered: import-slack-daily (daily at 03:00 UTC)'))

        schedule(
            'accounts.tasks.run_scheduled_import',
            cron='30 3 * * *',
            name='import-stripe-daily',
            preserve_disabled=True,
            source='stripe',
        )
        self.stdout.write(self.style.SUCCESS('Registered: import-stripe-daily (daily at 03:30 UTC)'))

        # Issue #452: lifecycle of unverified email-signup accounts.
        # Reminder runs first (07:00 UTC) so users get a 24h heads-up
        # before the purge sweep (08:00 UTC) on the same calendar day.
        schedule(
            'accounts.tasks.remind_unverified_users.remind_unverified_users',
            cron='0 7 * * *',
            name='remind-unverified-users',
        )
        self.stdout.write(self.style.SUCCESS('Registered: remind-unverified-users (daily at 07:00 UTC)'))

        schedule(
            'accounts.tasks.purge_unverified_users.purge_unverified_users',
            cron='0 8 * * *',
            name='purge-unverified-users',
        )
        self.stdout.write(self.style.SUCCESS('Registered: purge-unverified-users (daily at 08:00 UTC)'))

        # Daily ingest of the #plan-sprints Slack channel (issue #889).
        # Runs at 05:00 UTC, clear of the 03:00-04:00 import/event jobs.
        schedule(
            'crm.tasks.ingest_plan_sprints.ingest_plan_sprints',
            cron='0 5 * * *',
            name='ingest-plan-sprints',
        )
        self.stdout.write(self.style.SUCCESS('Registered: ingest-plan-sprints (daily at 05:00 UTC)'))

        # Daily member sprint cadence nudges (issue #1200). Runs after the
        # #plan-sprints ingest and before onboarding reminders. The task is
        # idempotent through SprintCadenceDeliveryLog rows, so re-running setup
        # or the job itself never duplicates member notifications.
        schedule(
            'plans.tasks.sprint_cadence.send_sprint_cadence_notifications',
            cron='15 5 * * *',
            name='sprint-cadence-notifications',
        )
        self.stdout.write(self.style.SUCCESS('Registered: sprint-cadence-notifications (daily at 05:15 UTC)'))

        # One-week onboarding reminder sweep (issue #1133). Runs daily at
        # 06:30 UTC, an off-peak slot clear of the 03:00-05:00 import/ingest
        # jobs, the 06:00 Slack-membership refresh, and the 07:00-08:00
        # unverified-user sweep. The sweep is idempotent (EmailLog-based, one
        # reminder per member ever), so a daily re-tick never double-sends.
        # update_or_create on the schedule name updates an existing row's
        # cron in place on the next setup_schedules run.
        schedule(
            'accounts.tasks.remind_onboarding.remind_onboarding_incomplete',
            cron='30 6 * * *',
            name='onboarding-reminders',
        )
        self.stdout.write(self.style.SUCCESS('Registered: onboarding-reminders (daily at 06:30 UTC)'))

        self.stdout.write(self.style.SUCCESS('All default schedules registered.'))
