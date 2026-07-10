"""Selector tests for sprint roster activity (issue #1202)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from freezegun import freeze_time

from crm.models import SlackMessage, SlackThread
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Sprint,
    SprintEnrollment,
    Week,
    WeekNote,
)
from plans.services.roster_activity import (
    ACTIVITY_FILTER_NO_UPDATE_THIS_WEEK,
    build_sprint_roster_activity,
)

User = get_user_model()


class SprintRosterActivitySelectorTest(TestCase):
    def _member(self, email):
        return User.objects.create_user(email=email, password='pw')

    def _sprint(self, *, start_date=datetime.date(2026, 7, 6), slug='july'):
        return Sprint.objects.create(
            name='July Sprint',
            slug=slug,
            start_date=start_date,
            duration_weeks=4,
            status='active',
        )

    def _plan(self, sprint, member):
        return Plan.objects.create(sprint=sprint, member=member)

    def _week(self, plan):
        return Week.objects.create(plan=plan, week_number=1)

    def _slack_message(self, plan, posted_at):
        thread = SlackThread.objects.create(
            channel_id='C_PLAN_SPRINTS',
            thread_ts=f'{posted_at.timestamp():.6f}',
            member=plan.member,
            plan=plan,
            posted_at=posted_at,
        )
        return SlackMessage.objects.create(
            thread=thread,
            ts=thread.thread_ts,
            author_display='Member',
            text='Progress update',
            posted_at=posted_at,
            is_root=True,
        )

    @freeze_time('2026-07-10T12:00:00Z')
    def test_progress_latest_source_and_current_week_state(self):
        sprint = self._sprint()
        member = self._member('member@test.com')
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = self._plan(sprint, member)
        week = self._week(plan)
        older = timezone.now() - datetime.timedelta(days=2)
        newer = timezone.now() - datetime.timedelta(hours=2)
        newest = timezone.now() - datetime.timedelta(hours=1)
        Checkpoint.objects.create(week=week, description='Done', done_at=older)
        Checkpoint.objects.create(week=week, description='Open')
        note = WeekNote.objects.create(week=week, body='Midweek note')
        WeekNote.objects.filter(pk=note.pk).update(updated_at=newer)
        self._slack_message(plan, newest)

        activity = build_sprint_roster_activity(sprint)
        row = activity['rows'][0]

        self.assertEqual(row['progress']['label'], '1/2 checkpoints')
        self.assertEqual(row['last_update']['source'], 'slack')
        self.assertEqual(row['last_update']['source_label'], 'Slack update')
        self.assertEqual(row['this_week']['label'], 'Updated this week')
        self.assertEqual(activity['current_week']['week_number'], 1)

        later = timezone.now() - datetime.timedelta(minutes=10)
        Deliverable.objects.create(
            plan=plan,
            description='Demo',
            done_at=later,
        )

        activity = build_sprint_roster_activity(sprint)
        self.assertEqual(activity['rows'][0]['last_update']['source'], 'deliverable')
        self.assertEqual(
            activity['rows'][0]['last_update']['source_label'],
            'Deliverable completed',
        )

    @freeze_time('2026-07-10T12:00:00Z')
    def test_no_plan_zero_checkpoint_and_filter_ordering(self):
        sprint = self._sprint()
        updated = self._member('updated@test.com')
        no_plan = self._member('no-plan@test.com')
        never = self._member('never@test.com')
        stale_old = self._member('old@test.com')
        stale_new = self._member('new@test.com')
        for member in [updated, no_plan, never, stale_old, stale_new]:
            SprintEnrollment.objects.create(sprint=sprint, user=member)

        updated_plan = self._plan(sprint, updated)
        updated_week = self._week(updated_plan)
        Checkpoint.objects.create(
            week=updated_week,
            description='Done',
            done_at=timezone.now() - datetime.timedelta(hours=1),
        )

        self._plan(sprint, never)

        old_plan = self._plan(sprint, stale_old)
        self._week(old_plan)
        NextStep.objects.create(
            plan=old_plan,
            description='Old',
            done_at=timezone.now() - datetime.timedelta(days=10),
        )

        new_plan = self._plan(sprint, stale_new)
        self._week(new_plan)
        Deliverable.objects.create(
            plan=new_plan,
            description='Newer stale',
            done_at=timezone.now() - datetime.timedelta(days=9),
        )

        activity = build_sprint_roster_activity(
            sprint,
            activity_filter=ACTIVITY_FILTER_NO_UPDATE_THIS_WEEK,
        )

        self.assertEqual(activity['totals']['no_update_this_week'], 4)
        self.assertEqual(
            [row['member'].email for row in activity['rows']],
            [
                'no-plan@test.com',
                'never@test.com',
                'old@test.com',
                'new@test.com',
            ],
        )
        no_plan_row = activity['rows'][0]
        never_row = activity['rows'][1]
        self.assertEqual(no_plan_row['progress']['label'], 'No plan')
        self.assertEqual(no_plan_row['this_week']['label'], 'No plan')
        self.assertEqual(never_row['progress']['label'], '0/0 checkpoints')
        self.assertEqual(never_row['last_update']['label'], 'No updates yet')

    @freeze_time('2026-07-10T12:00:00Z')
    def test_future_and_ended_sprints_have_neutral_week_state(self):
        member = self._member('member@test.com')
        future = self._sprint(
            start_date=datetime.date(2026, 8, 1),
            slug='future',
        )
        ended = self._sprint(
            start_date=datetime.date(2026, 5, 1),
            slug='ended',
        )
        for sprint in [future, ended]:
            SprintEnrollment.objects.create(sprint=sprint, user=member)
            self._plan(sprint, member)

        future_activity = build_sprint_roster_activity(future)
        ended_activity = build_sprint_roster_activity(ended)

        self.assertFalse(future_activity['current_week']['active'])
        self.assertFalse(ended_activity['current_week']['active'])
        self.assertEqual(
            future_activity['rows'][0]['this_week']['label'],
            'No active sprint week',
        )
        self.assertEqual(
            ended_activity['rows'][0]['this_week']['label'],
            'No active sprint week',
        )
