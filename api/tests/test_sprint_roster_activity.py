"""Tests for ``/api/sprints/<slug>/roster-activity`` (issue #1202)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from freezegun import freeze_time

from accounts.models import Token
from crm.models import (
    AppliedProgressChange,
    IngestedProgressEvent,
    SlackMessage,
    SlackThread,
)
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

User = get_user_model()


class SprintRosterActivityApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='ops')
        cls.member = User.objects.create_user(email='member@test.com', password='pw')
        cls.nonstaff_token = Token(
            key='nonstaff-roster-token',
            user=cls.member,
            name='bad',
        )
        Token.objects.bulk_create([cls.nonstaff_token])

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _url(self, slug='july', query=''):
        return f'/api/sprints/{slug}/roster-activity{query}'

    def _member(self, email):
        return User.objects.create_user(email=email, password='pw')

    def _sprint(self):
        return Sprint.objects.create(
            name='July Sprint',
            slug='july',
            start_date=datetime.date(2026, 7, 6),
            duration_weeks=4,
            status='active',
        )

    def _plan(self, sprint, member):
        return Plan.objects.create(sprint=sprint, member=member)

    def _week(self, plan):
        return Week.objects.create(plan=plan, week_number=1)

    def _thread(self, plan, posted_at):
        thread = SlackThread.objects.create(
            channel_id='C_PLAN_SPRINTS',
            thread_ts=f'{posted_at.timestamp():.6f}',
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
        return thread

    @freeze_time('2026-07-10T12:00:00Z')
    def test_staff_token_gets_roster_activity_shape_and_filter(self):
        sprint = self._sprint()
        updated = self._member('updated@test.com')
        stale = self._member('stale@test.com')
        no_plan = self._member('no-plan@test.com')
        plan_only = self._member('plan-only@test.com')
        for member in [updated, stale, no_plan]:
            SprintEnrollment.objects.create(sprint=sprint, user=member)

        updated_plan = self._plan(sprint, updated)
        updated_week = self._week(updated_plan)
        for index in range(5):
            Checkpoint.objects.create(
                week=updated_week,
                description=f'Checkpoint {index}',
                done_at=(
                    timezone.now() - datetime.timedelta(hours=1)
                    if index < 3 else None
                ),
            )
        self._thread(updated_plan, timezone.now() - datetime.timedelta(minutes=30))

        stale_plan = self._plan(sprint, stale)
        self._week(stale_plan)
        Deliverable.objects.create(
            plan=stale_plan,
            description='Old delivery',
            done_at=timezone.now() - datetime.timedelta(days=10),
        )

        plan_only_plan = self._plan(sprint, plan_only)
        self._week(plan_only_plan)
        NextStep.objects.create(
            plan=plan_only_plan,
            description='Plan-only stale',
            done_at=timezone.now() - datetime.timedelta(days=8),
        )
        SprintEnrollment.objects.filter(sprint=sprint, user=plan_only).delete()

        response = self.client.get(self._url(), **self._auth())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['sprint']['slug'], 'july')
        self.assertTrue(body['current_week']['active'])
        self.assertEqual(body['current_week']['week_number'], 1)
        self.assertEqual(body['totals']['members'], 4)
        self.assertEqual(body['totals']['enrolled'], 3)
        self.assertEqual(body['totals']['plans'], 3)
        self.assertEqual(body['totals']['no_update_this_week'], 3)
        rows = {row['member']['email']: row for row in body['members']}
        self.assertEqual(rows['updated@test.com']['progress']['label'], '3/5 checkpoints')
        self.assertEqual(rows['updated@test.com']['last_update']['source'], 'slack')
        self.assertEqual(
            rows['updated@test.com']['this_week']['label'],
            'Updated this week',
        )
        self.assertEqual(rows['no-plan@test.com']['plan']['exists'], False)
        self.assertEqual(rows['no-plan@test.com']['progress']['label'], 'No plan')

        filtered = self.client.get(
            self._url(query='?activity=no_update_this_week'),
            **self._auth(),
        )

        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(
            [row['member']['email'] for row in filtered.json()['members']],
            ['no-plan@test.com', 'stale@test.com', 'plan-only@test.com'],
        )

    def test_auth_matrix_rejects_non_staff_and_session_only_callers(self):
        sprint = self._sprint()

        anonymous = self.client.get(self._url(sprint.slug))
        invalid = self.client.get(
            self._url(sprint.slug),
            HTTP_AUTHORIZATION='Token nope',
        )
        nonstaff = self.client.get(
            self._url(sprint.slug),
            **self._auth(self.nonstaff_token),
        )
        self.client.force_login(self.staff)
        session_only = self.client.get(self._url(sprint.slug))

        for response in [anonymous, invalid, nonstaff, session_only]:
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('members', response.json())

    @freeze_time('2026-07-10T12:00:00Z')
    def test_endpoint_is_read_only(self):
        sprint = self._sprint()
        member = self._member('readonly@test.com')
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = self._plan(sprint, member)
        week = self._week(plan)
        checkpoint = Checkpoint.objects.create(
            week=week,
            description='Checkpoint',
            done_at=timezone.now() - datetime.timedelta(days=1),
        )
        deliverable = Deliverable.objects.create(
            plan=plan,
            description='Deliverable',
            done_at=timezone.now() - datetime.timedelta(days=2),
        )
        next_step = NextStep.objects.create(
            plan=plan,
            description='Next',
            done_at=timezone.now() - datetime.timedelta(days=3),
        )
        note = WeekNote.objects.create(week=week, body='Note')
        thread = self._thread(plan, timezone.now() - datetime.timedelta(hours=2))
        event = IngestedProgressEvent.objects.create(thread=thread, plan=plan)
        AppliedProgressChange.objects.create(
            event=event,
            item_kind='checkpoint',
            checkpoint=checkpoint,
            previous_done_at=None,
        )
        before = {
            'plans': Plan.objects.count(),
            'enrollments': SprintEnrollment.objects.count(),
            'threads': SlackThread.objects.count(),
            'messages': SlackMessage.objects.count(),
            'events': IngestedProgressEvent.objects.count(),
            'changes': AppliedProgressChange.objects.count(),
            'notes': WeekNote.objects.count(),
            'checkpoint': checkpoint.done_at,
            'deliverable': deliverable.done_at,
            'next_step': next_step.done_at,
            'note_updated': note.updated_at,
        }

        response = self.client.get(self._url(sprint.slug), **self._auth())

        self.assertEqual(response.status_code, 200)
        checkpoint.refresh_from_db()
        deliverable.refresh_from_db()
        next_step.refresh_from_db()
        note.refresh_from_db()
        after = {
            'plans': Plan.objects.count(),
            'enrollments': SprintEnrollment.objects.count(),
            'threads': SlackThread.objects.count(),
            'messages': SlackMessage.objects.count(),
            'events': IngestedProgressEvent.objects.count(),
            'changes': AppliedProgressChange.objects.count(),
            'notes': WeekNote.objects.count(),
            'checkpoint': checkpoint.done_at,
            'deliverable': deliverable.done_at,
            'next_step': next_step.done_at,
            'note_updated': note.updated_at,
        }
        self.assertEqual(after, before)
