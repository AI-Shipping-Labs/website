"""Studio sprint roster activity tests (issue #1202)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from freezegun import freeze_time

from crm.models import SlackMessage, SlackThread
from plans.models import Checkpoint, Plan, Sprint, SprintEnrollment, Week

User = get_user_model()


class StudioSprintRosterActivityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _member(self, email):
        return User.objects.create_user(email=email, password='pw')

    def _sprint(self, *, start_date=datetime.date(2026, 7, 6), slug='roster'):
        return Sprint.objects.create(
            name='Roster Sprint',
            slug=slug,
            start_date=start_date,
            duration_weeks=4,
            status='active',
        )

    def _plan_with_checkpoint(self, sprint, member, *, done_at=None):
        plan = Plan.objects.create(sprint=sprint, member=member)
        week = Week.objects.create(plan=plan, week_number=1)
        Checkpoint.objects.create(week=week, description='Done', done_at=done_at)
        Checkpoint.objects.create(week=week, description='Open')
        return plan

    def _slack_update(self, plan, posted_at):
        thread = SlackThread.objects.create(
            channel_id='C_PLAN_SPRINTS',
            thread_ts='1770000000.000100',
            member=plan.member,
            plan=plan,
            posted_at=posted_at,
        )
        SlackMessage.objects.create(
            thread=thread,
            ts=thread.thread_ts,
            author_display='Member',
            text='Progress',
            posted_at=posted_at,
            is_root=True,
        )

    @freeze_time('2026-07-10T12:00:00Z')
    def test_detail_renders_activity_columns_without_losing_actions(self):
        sprint = self._sprint()
        member = self._member('member@test.com')
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = self._plan_with_checkpoint(
            sprint,
            member,
            done_at=timezone.now() - datetime.timedelta(hours=2),
        )
        self._slack_update(plan, timezone.now() - datetime.timedelta(hours=1))

        response = self.client.get(f'/studio/sprints/{sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Progress')
        self.assertContains(response, 'Last update')
        self.assertContains(response, 'This week')
        self.assertContains(response, '1/2 checkpoints')
        self.assertContains(response, 'Slack update')
        self.assertContains(response, 'Updated this week')
        self.assertContains(response, 'No update this week')
        self.assertContains(response, '0 no update this week')
        self.assertContains(response, 'View plan')
        self.assertContains(response, 'Edit plan')
        self.assertContains(response, 'plan-ready-email-state')
        self.assertContains(response, 'sprint-accountability-randomize-form')
        self.assertContains(response, 'sprint-unenroll-form')

    @freeze_time('2026-07-10T12:00:00Z')
    def test_no_update_filter_persists_in_url_and_orders_triage_rows(self):
        sprint = self._sprint()
        updated = self._member('updated@test.com')
        no_plan = self._member('no-plan@test.com')
        stale_old = self._member('old@test.com')
        stale_new = self._member('new@test.com')
        plan_only = self._member('plan-only@test.com')
        for member in [updated, no_plan, stale_old, stale_new]:
            SprintEnrollment.objects.create(sprint=sprint, user=member)

        self._plan_with_checkpoint(
            sprint,
            updated,
            done_at=timezone.now() - datetime.timedelta(hours=1),
        )
        self._plan_with_checkpoint(
            sprint,
            stale_old,
            done_at=timezone.now() - datetime.timedelta(days=10),
        )
        self._plan_with_checkpoint(
            sprint,
            stale_new,
            done_at=timezone.now() - datetime.timedelta(days=9),
        )
        self._plan_with_checkpoint(
            sprint,
            plan_only,
            done_at=timezone.now() - datetime.timedelta(days=8),
        )
        SprintEnrollment.objects.filter(sprint=sprint, user=plan_only).delete()

        response = self.client.get(
            f'/studio/sprints/{sprint.pk}/?activity=no_update_this_week',
        )

        self.assertEqual(response.status_code, 200)
        emails = [
            row['member'].email
            for row in response.context['sprint_member_rows']
        ]
        self.assertEqual(
            emails,
            [
                'no-plan@test.com',
                'old@test.com',
                'new@test.com',
                'plan-only@test.com',
            ],
        )
        body = response.content.decode()
        self.assertNotIn('data-user-email="updated@test.com"', body)
        self.assertEqual(response.context['activity_filter'], 'no_update_this_week')
        self.assertIn('sprint-members-clear-activity-filter', body)

        full = self.client.get(f'/studio/sprints/{sprint.pk}/')
        self.assertContains(full, 'data-user-email="updated@test.com"')
        self.assertContains(full, 'sprint-members-no-update-filter')

    @freeze_time('2026-07-10T12:00:00Z')
    def test_future_sprint_shows_no_active_week_state(self):
        sprint = self._sprint(start_date=datetime.date(2026, 8, 1), slug='future')
        member = self._member('member@test.com')
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        self._plan_with_checkpoint(
            sprint,
            member,
            done_at=timezone.now() - datetime.timedelta(days=1),
        )

        response = self.client.get(f'/studio/sprints/{sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1/2 checkpoints')
        self.assertContains(response, 'No active sprint week')
        self.assertNotContains(response, 'no update this week</span>')
