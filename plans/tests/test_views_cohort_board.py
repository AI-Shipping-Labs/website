"""Tests for the cohort board view (issue #440).

The cohort board renders other members' cohort-visible plans plus
their progress counts. Visibility, enrolment, and the empty-state
copy are all enforced server-side -- these tests cover the
authoritative behaviour.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from plans.models import (
    Checkpoint,
    InterviewNote,
    Plan,
    Sprint,
    Week,
)

User = get_user_model()


def _build_sprint_with_two_members():
    sprint = Sprint.objects.create(
        name='May 2026', slug='may-2026',
        start_date=datetime.date(2026, 5, 1),
    )
    viewer = User.objects.create_user(
        email='viewer@test.com', password='pw',
    )
    teammate = User.objects.create_user(
        email='alice@test.com', password='pw',
        first_name='Alice', last_name='Smith',
    )
    Plan.objects.create(
        member=viewer, sprint=sprint, visibility='cohort',
    )
    teammate_plan = Plan.objects.create(
        member=teammate, sprint=sprint, visibility='cohort',
    )
    return sprint, viewer, teammate, teammate_plan


class CohortBoardAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.member, sprint=cls.sprint, visibility='private',
        )

    def test_board_redirects_anonymous_to_login(self):
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_board_returns_404_for_non_enrolled_member(self):
        outsider = User.objects.create_user(
            email='outsider@test.com', password='pw',
        )
        self.client.force_login(outsider)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_board_returns_404_for_non_enrolled_staff_member(self):
        """The board is member-scoped -- staff who are not enrolled get 404.

        Staff use Studio (#432) for full access; the cohort board is
        the same surface every member sees.
        """
        staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_board_returns_200_for_enrolled_member(self):
        self.client.force_login(self.member)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class CohortBoardContentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = User.objects.create_user(
            email='viewer@test.com', password='pw',
            first_name='Vee', last_name='Viewer',
        )
        cls.viewer_plan = Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Smith',
        )
        cls.alice_plan = Plan.objects.create(
            member=cls.alice, sprint=cls.sprint, visibility='cohort',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
            first_name='Bob', last_name='Jones',
        )
        cls.bob_plan = Plan.objects.create(
            member=cls.bob, sprint=cls.sprint, visibility='cohort',
        )
        cls.charlie = User.objects.create_user(
            email='charlie@test.com', password='pw',
            first_name='Charlie', last_name='Hidden',
        )
        cls.charlie_plan = Plan.objects.create(
            member=cls.charlie, sprint=cls.sprint, visibility='private',
        )

    def setUp(self):
        self.client.force_login(self.viewer)

    def test_board_renders_other_members_cohort_plans(self):
        """Issue #461: rows for cohort-visible teammates render names + content."""
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        rows_by_plan_pk = {
            row['plan'].pk: row
            for row in response.context['progress_rows']
            if row['plan'] is not None
        }
        # Alice and Bob are cohort-visible; Charlie is private (still
        # shown but as a counts-only row, see CohortBoardPrivateRowTest).
        self.assertEqual(
            rows_by_plan_pk[self.alice_plan.pk]['kind'], 'cohort',
        )
        self.assertEqual(
            rows_by_plan_pk[self.bob_plan.pk]['kind'], 'cohort',
        )
        self.assertEqual(
            rows_by_plan_pk[self.charlie_plan.pk]['kind'], 'private',
        )
        self.assertContains(response, 'Alice Smith')
        self.assertContains(response, 'Bob Jones')
        # Charlie's display name still renders in the private row -- the
        # privacy guarantee covers PLAN CONTENT, not the member's name.
        self.assertContains(response, 'Charlie Hidden')

    def test_board_includes_viewer_own_row_with_self_flag(self):
        """Issue #461: the viewer's own row appears, marked ``is_self``."""
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        viewer_rows = [
            row for row in response.context['progress_rows']
            if row['plan'] is not None
            and row['plan'].pk == self.viewer_plan.pk
        ]
        self.assertEqual(len(viewer_rows), 1)
        self.assertTrue(viewer_rows[0]['is_self'])


class CohortBoardProgressTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = User.objects.create_user(
            email='viewer@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )

    def setUp(self):
        self.client.force_login(self.viewer)

    def _make_plan_with_checkpoints(self, *, total, done, email):
        member = User.objects.create_user(
            email=email, password='pw',
            first_name='Member', last_name=email.split('@')[0],
        )
        plan = Plan.objects.create(
            member=member, sprint=self.sprint, visibility='cohort',
        )
        for week_idx in range(1, 4):  # 3 weeks
            week = Week.objects.create(plan=plan, week_number=week_idx)
            checkpoints_in_week = total // 3 + (
                1 if week_idx <= total % 3 else 0
            )
            for cp_idx in range(checkpoints_in_week):
                done_at = (
                    timezone.now() if (week_idx - 1) * (total // 3) + cp_idx < done
                    else None
                )
                Checkpoint.objects.create(
                    week=week,
                    description=f'cp {week_idx}-{cp_idx}',
                    done_at=done_at,
                )
        return plan

    def _row_for_plan(self, response, plan_pk):
        rows = [
            row for row in response.context['progress_rows']
            if row['plan'] is not None and row['plan'].pk == plan_pk
        ]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_board_renders_progress_counts(self):
        plan = self._make_plan_with_checkpoints(
            total=18, done=12, email='alpha@test.com',
        )
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        row = self._row_for_plan(response, plan.pk)
        self.assertEqual(row['progress_total'], 18)
        self.assertEqual(row['progress_done'], 12)
        self.assertContains(
            response,
            f'data-testid="progress-count-{plan.member.pk}">12 of 18',
        )

    def test_board_renders_zero_checkpoints_safely(self):
        plan = self._make_plan_with_checkpoints(
            total=0, done=0, email='zero@test.com',
        )
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Zero-checkpoint plan still appears with the explicit "0 of 0"
        # count (counts are intentionally exposed for every kind).
        self.assertContains(response, 'No checkpoints yet')
        # But there is no progress bar for zero-total rows.
        self.assertNotContains(
            response, f'data-testid="progress-bar-{plan.member.pk}"',
        )

    def test_board_renders_all_done_progress(self):
        plan = self._make_plan_with_checkpoints(
            total=6, done=6, email='all-done@test.com',
        )
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertContains(
            response,
            f'data-testid="progress-count-{plan.member.pk}">6 of 6',
        )


class CohortBoardEmptyStateTest(TestCase):
    def test_board_shows_viewer_row_when_viewer_is_solo_with_plan(self):
        """Issue #461: solo viewer with a plan still sees their own row.

        The pre-#461 board returned an empty state when nobody else had
        shared. The new board renders the viewer's own row inline, so
        only the no-row case (no plan + nobody else enrolled) shows the
        empty state.
        """
        sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        viewer = User.objects.create_user(
            email='solo@test.com', password='pw',
        )
        viewer_plan = Plan.objects.create(
            member=viewer, sprint=sprint, visibility='cohort',
        )
        self.client.force_login(viewer)
        url = reverse('cohort_board', kwargs={'sprint_slug': sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # The viewer's own row IS in the list now.
        plan_pks = [
            row['plan'].pk for row in response.context['progress_rows']
            if row['plan'] is not None
        ]
        self.assertEqual(plan_pks, [viewer_plan.pk])
        # And the empty-state callout does NOT fire.
        self.assertNotContains(response, 'data-testid="cohort-board-empty"')


class CohortBoardInterviewNoteIsolationTest(TestCase):
    """Internal/external interview notes never leak via the cohort board."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = User.objects.create_user(
            email='viewer@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
            first_name='Team', last_name='Mate',
        )
        cls.teammate_plan = Plan.objects.create(
            member=cls.teammate, sprint=cls.sprint, visibility='cohort',
        )

    def setUp(self):
        self.client.force_login(self.viewer)

    def test_board_does_not_render_internal_interview_note(self):
        InterviewNote.objects.create(
            plan=self.teammate_plan, member=self.teammate,
            visibility='internal', body='SECRET_INTERNAL_BODY',
        )
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'SECRET_INTERNAL_BODY')

    def test_board_does_not_render_external_interview_note(self):
        InterviewNote.objects.create(
            plan=self.teammate_plan, member=self.teammate,
            visibility='external', body='NOT_FOR_BOARD_BODY',
        )
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'NOT_FOR_BOARD_BODY')
