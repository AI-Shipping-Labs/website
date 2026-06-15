"""
Tests for the setup_schedules management command.
"""

import ast
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django_q.models import Schedule


def _decode_schedule_kwargs(raw):
    """Decode a Schedule.kwargs value the same way django-q does at fire time.

    Schedule.kwargs is a TextField; Django stores ``str(dict)`` when the
    helper hands a dict to ``update_or_create``. The scheduler reads it
    back with ``ast.literal_eval``. We mirror that round-trip so the test
    assertions reflect the value seen by ``django_q.scheduler.scheduler``.
    """
    if raw in (None, ''):
        return {}
    if isinstance(raw, dict):
        return raw
    return ast.literal_eval(raw)


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
        """Command registers event-reminders every 15 min (issue #1001).

        Cadence was restored from ``0 * * * *`` (#919) back to
        ``*/15 * * * *`` because the 10-min-wide 20m window needs a
        sub-20-min tick — hourly never caught events starting outside
        :15..:25.
        """
        out = StringIO()
        call_command('setup_schedules', stdout=out)
        schedule = Schedule.objects.get(name='event-reminders')
        self.assertEqual(schedule.func, 'notifications.services.event_reminders.check_event_reminders')
        self.assertEqual(schedule.cron, '*/15 * * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)
        self.assertIn('event-reminders (every 15 min)', out.getvalue())
        self.assertNotIn('event-reminders (hourly at minute 0)', out.getvalue())

    def test_creates_complete_finished_events_schedule(self):
        """Command registers complete-finished-events at daily cadence.

        Issue #573 introduced the schedule; issue #713 reduced its
        cadence from ``*/5 * * * *`` to ``0 4 * * *`` because every
        user-facing surface is now time-derived and the cron only
        powers the post-event follow-up fan-out + stored-field
        bookkeeping.
        """
        out = StringIO()
        call_command('setup_schedules', stdout=out)
        schedule = Schedule.objects.get(name='complete-finished-events')
        self.assertEqual(
            schedule.func,
            'events.tasks.complete_finished_events.complete_finished_events',
        )
        self.assertEqual(schedule.cron, '0 4 * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)
        self.assertIn(
            'complete-finished-events (daily at 04:00 UTC)',
            out.getvalue(),
        )
        self.assertNotIn('every 5 min', out.getvalue())

    def test_creates_expire_tier_overrides_schedule(self):
        """Command creates expire-tier-overrides schedule."""
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='expire-tier-overrides')
        self.assertEqual(schedule.func, 'jobs.tasks.expire_overrides.expire_tier_overrides')
        self.assertEqual(schedule.cron, '*/15 * * * *')

    def test_creates_slack_membership_refresh_schedule(self):
        """Command registers slack-membership-refresh at daily cadence.

        Issue #358 introduced the schedule; issue #919 reduced its cadence
        from ``*/30 * * * *`` to ``0 6 * * *`` (daily at 06:00 UTC) — once
        per day is enough, and 06:00 UTC is clear of the other scheduled
        jobs.
        """
        out = StringIO()
        call_command('setup_schedules', stdout=out)
        schedule = Schedule.objects.get(name='slack-membership-refresh')
        self.assertEqual(
            schedule.func,
            'community.tasks.slack_membership.refresh_slack_membership',
        )
        self.assertEqual(schedule.cron, '0 6 * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)
        self.assertIn('slack-membership-refresh (daily at 06:00 UTC)', out.getvalue())
        self.assertNotIn('every 30 min', out.getvalue())

    def test_creates_remind_unverified_users_schedule(self):
        """Command creates remind-unverified-users schedule (issue #452).

        Issue #716: ``func`` must be the fully-qualified dotted path to the
        function, not the parent package. The submodule name matches the
        re-exported function name, and ``pydoc.locate`` resolves the
        ambiguous shorter form to the module — which django-q then tries
        to call, raising ``TypeError: 'module' object is not callable``.
        """
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='remind-unverified-users')
        self.assertEqual(
            schedule.func,
            'accounts.tasks.remind_unverified_users.remind_unverified_users',
        )
        self.assertEqual(schedule.cron, '0 7 * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)

    def test_creates_purge_unverified_users_schedule(self):
        """Command creates purge-unverified-users schedule (issue #452).

        Issue #716: see ``test_creates_remind_unverified_users_schedule``
        — the registered ``func`` must point at the function, not the
        submodule of the same name, otherwise django-q raises a
        ``TypeError`` at fire time.
        """
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='purge-unverified-users')
        self.assertEqual(
            schedule.func,
            'accounts.tasks.purge_unverified_users.purge_unverified_users',
        )
        self.assertEqual(schedule.cron, '0 8 * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)

    def test_creates_purge_user_activity_schedule(self):
        """Command creates the purge-user-activity schedule (issue #853).

        The registered ``func`` must point at the task function so django-q
        can resolve it at fire time. Runs daily, off-peak.
        """
        call_command('setup_schedules', stdout=StringIO())
        schedule = Schedule.objects.get(name='purge-user-activity')
        self.assertEqual(
            schedule.func, 'analytics.tasks.purge_old_user_activity',
        )
        self.assertEqual(schedule.cron, '30 3 * * *')
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

    def test_updates_stale_cadence_on_existing_rows(self):
        """Re-running setup_schedules updates an existing row's cron in place.

        The operator's running cluster already holds rows with older
        cadences. setup_schedules must UPDATE the existing Schedule rows
        (not just create-if-missing) so the current cadence takes effect on
        the next run. We seed the stale crons, re-run, and assert each cron
        is rewritten on the SAME row (no duplicate).

        Issue #1001: event-reminders is seeded with the stale hourly
        ``0 * * * *`` (#919) and must be restored to ``*/15 * * * *`` in
        place. slack-membership-refresh stays the #919 daily ``0 6 * * *``.
        """
        # Pre-seed the existing rows with the stale cadences.
        Schedule.objects.create(
            name='event-reminders',
            func='notifications.services.event_reminders.check_event_reminders',
            schedule_type=Schedule.CRON,
            cron='0 * * * *',
        )
        Schedule.objects.create(
            name='slack-membership-refresh',
            func='community.tasks.slack_membership.refresh_slack_membership',
            schedule_type=Schedule.CRON,
            cron='*/30 * * * *',
        )
        event_id = Schedule.objects.get(name='event-reminders').id
        slack_id = Schedule.objects.get(name='slack-membership-refresh').id

        call_command('setup_schedules', stdout=StringIO())

        # Same rows (no duplicates), with the new cadences.
        self.assertEqual(Schedule.objects.filter(name='event-reminders').count(), 1)
        self.assertEqual(Schedule.objects.filter(name='slack-membership-refresh').count(), 1)

        event = Schedule.objects.get(name='event-reminders')
        slack = Schedule.objects.get(name='slack-membership-refresh')
        self.assertEqual(event.id, event_id)
        self.assertEqual(slack.id, slack_id)
        self.assertEqual(event.cron, '*/15 * * * *')
        self.assertEqual(slack.cron, '0 6 * * *')

    def test_no_unexpected_schedules_created(self):
        """Command does not create any schedules outside the expected set."""
        call_command('setup_schedules', stdout=StringIO())
        names = set(Schedule.objects.values_list('name', flat=True))
        expected = {
            'health-check',
            'cleanup-webhook-logs',
            'purge-user-activity',
            'event-reminders',
            'complete-finished-events',
            'expire-tier-overrides',
            'slack-membership-refresh',
            'import-slack-daily',
            'import-stripe-daily',
            'remind-unverified-users',
            'purge-unverified-users',
            'ingest-plan-sprints',
        }
        self.assertEqual(names, expected)

    def test_creates_daily_import_schedules(self):
        """Command creates daily Slack and Stripe import schedules.

        Issue #717: the schedule helper writes q_options.task_name into
        kwargs so each fire lands a descriptive Task.name. The function
        kwarg ``source`` rides alongside it.
        """
        call_command('setup_schedules', stdout=StringIO())

        slack = Schedule.objects.get(name='import-slack-daily')
        self.assertEqual(slack.func, 'accounts.tasks.run_scheduled_import')
        self.assertEqual(slack.cron, '0 3 * * *')
        self.assertEqual(slack.schedule_type, Schedule.CRON)
        slack_kwargs = _decode_schedule_kwargs(slack.kwargs)
        self.assertEqual(slack_kwargs['source'], 'slack')
        self.assertEqual(
            slack_kwargs['q_options']['task_name'], 'import-slack-daily',
        )

        stripe = Schedule.objects.get(name='import-stripe-daily')
        self.assertEqual(stripe.func, 'accounts.tasks.run_scheduled_import')
        self.assertEqual(stripe.cron, '30 3 * * *')
        self.assertEqual(stripe.schedule_type, Schedule.CRON)
        stripe_kwargs = _decode_schedule_kwargs(stripe.kwargs)
        self.assertEqual(stripe_kwargs['source'], 'stripe')
        self.assertEqual(
            stripe_kwargs['q_options']['task_name'], 'import-stripe-daily',
        )

    def test_every_schedule_carries_q_options_task_name(self):
        """Issue #717: every setup_schedules row carries q_options.task_name.

        Django-Q 1.x's scheduler reads q_options from the Schedule's
        stored kwargs at fire time and forwards ``task_name`` into the
        resulting ``Task.name``. Without this, every scheduled fire
        lands a random Django-Q codename in the worker history.
        """
        call_command('setup_schedules', stdout=StringIO())

        for row in Schedule.objects.all():
            decoded = _decode_schedule_kwargs(row.kwargs)
            self.assertIn(
                'q_options', decoded,
                f"Schedule {row.name!r} missing q_options in kwargs={row.kwargs!r}",
            )
            self.assertEqual(
                decoded['q_options'].get('task_name'), row.name,
                f"Schedule {row.name!r} q_options.task_name mismatch: {decoded!r}",
            )

    def test_setup_preserves_disabled_import_schedule(self):
        """Refreshing schedules keeps disabled import schedules disabled."""
        call_command('setup_schedules', stdout=StringIO())
        Schedule.objects.filter(name='import-slack-daily').update(repeats=0)

        call_command('setup_schedules', stdout=StringIO())

        self.assertEqual(Schedule.objects.filter(name='import-slack-daily').count(), 1)
        slack = Schedule.objects.get(name='import-slack-daily')
        self.assertEqual(slack.repeats, 0)
