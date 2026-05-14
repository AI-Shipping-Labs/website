"""View tests for the cohort progress board (issue #461).

The board renders one row per enrolled member, classified into ``cohort``
(clickable, full content), ``private`` (counts-only stub), and
``no_plan`` (em-dash, "No plan yet" caption). These tests pin down the
context shape, the privacy guarantee for private rows, sort order, and
the no-plan row.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from plans.models import (
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
    SprintEnrollment,
    Week,
)

User = get_user_model()


def _make_user(email):
    return User.objects.create_user(email=email, password='pw')


class CohortBoardProgressRowsContextShapeTest(TestCase):
    """Each row in ``progress_rows`` has the expected shape and kind."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = _make_user('viewer@test.com')
        cls.viewer_plan = Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        cls.cohort_member = _make_user('cohort@test.com')
        cls.cohort_plan = Plan.objects.create(
            member=cls.cohort_member, sprint=cls.sprint,
            visibility='cohort',
            goal='Ship cohort goal',
            focus_main='Cohort focus text',
        )
        cls.private_member = _make_user('private@test.com')
        cls.private_plan = Plan.objects.create(
            member=cls.private_member, sprint=cls.sprint,
            visibility='private',
            focus_main='PRIVATE_FOCUS_TEXT',
        )
        cls.no_plan_member = _make_user('noplan@test.com')
        SprintEnrollment.objects.create(
            sprint=cls.sprint, user=cls.no_plan_member,
        )

    def setUp(self):
        self.client.force_login(self.viewer)

    def _get_rows(self):
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response, response.context['progress_rows']

    def test_one_row_per_enrolled_member(self):
        _, rows = self._get_rows()
        member_pks = {row['member'].pk for row in rows}
        self.assertEqual(
            member_pks,
            {
                self.viewer.pk,
                self.cohort_member.pk,
                self.private_member.pk,
                self.no_plan_member.pk,
            },
        )

    def test_cohort_row_kind_and_focus_in_html(self):
        response, rows = self._get_rows()
        row = next(r for r in rows if r['member'].pk == self.cohort_member.pk)
        self.assertEqual(row['kind'], 'cohort')
        self.assertEqual(row['plan'].pk, self.cohort_plan.pk)
        self.assertContains(response, 'Cohort focus text')

    def test_cohort_row_goal_visible_in_html(self):
        response, _ = self._get_rows()
        self.assertContains(
            response,
            f'data-testid="cohort-row-goal-{self.cohort_member.pk}"',
        )
        self.assertContains(response, 'Ship cohort goal')

    def test_private_row_kind_and_focus_not_in_html(self):
        response, rows = self._get_rows()
        row = next(r for r in rows if r['member'].pk == self.private_member.pk)
        self.assertEqual(row['kind'], 'private')
        self.assertEqual(row['plan'].pk, self.private_plan.pk)
        # The private plan's focus text MUST NOT appear in the rendered
        # HTML, even though the row exists and the count is shown.
        self.assertNotContains(response, 'PRIVATE_FOCUS_TEXT')
        # The private badge is rendered.
        self.assertContains(
            response,
            f'data-testid="private-badge-{self.private_member.pk}"',
        )

    def test_no_plan_row_kind_and_caption(self):
        response, rows = self._get_rows()
        row = next(r for r in rows if r['member'].pk == self.no_plan_member.pk)
        self.assertEqual(row['kind'], 'no_plan')
        self.assertIsNone(row['plan'])
        self.assertContains(response, 'No plan yet')

    def test_no_plan_row_is_not_clickable(self):
        response, _ = self._get_rows()
        # The no-plan row container has its own non-link testid; there
        # is no anchor tag wrapping the no-plan member's row.
        self.assertContains(
            response,
            f'data-testid="progress-row-no-plan-{self.no_plan_member.pk}"',
        )
        self.assertNotContains(
            response,
            f'data-testid="progress-row-link-{self.no_plan_member.pk}"',
        )

    def test_private_row_is_not_clickable(self):
        response, _ = self._get_rows()
        # Private row has a non-link container, no member_plan_detail anchor
        # for the private member.
        forbidden_href = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.private_plan.pk,
            },
        )
        self.assertNotContains(response, f'href="{forbidden_href}"')

    def test_self_row_links_to_my_plan_detail(self):
        response, rows = self._get_rows()
        self_row = next(r for r in rows if r['member'].pk == self.viewer.pk)
        self.assertTrue(self_row['is_self'])
        # Viewer's own row links to the editable my-plan page, NOT the
        # read-only member_plan_detail.
        my_plan_url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.viewer_plan.pk,
            },
        )
        self.assertContains(response, f'href="{my_plan_url}"')


class CohortBoardSortOrderTest(TestCase):
    """Viewer row is first; remaining rows sort by progress, then email."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = _make_user('z-viewer@test.com')
        cls.viewer_plan = cls._plan_with(cls.viewer, total=5, done=2)

        cls.alice = _make_user('alice@test.com')
        cls.alice_plan = cls._plan_with(cls.alice, total=5, done=4)

        cls.bob = _make_user('bob@test.com')
        cls.bob_plan = cls._plan_with(cls.bob, total=3, done=1)

        cls.carol = _make_user('carol@test.com')
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.carol)

    @classmethod
    def _plan_with(cls, member, *, total, done):
        plan = Plan.objects.create(
            member=member, sprint=cls.sprint, visibility='cohort',
        )
        week = Week.objects.create(plan=plan, week_number=1)
        for i in range(total):
            Checkpoint.objects.create(
                week=week,
                description=f'cp {i}',
                done_at=timezone.now() if i < done else None,
            )
        return plan

    def setUp(self):
        self.client.force_login(self.viewer)

    def test_viewer_row_first_then_plan_rows_sorted_by_progress(self):
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        rows = response.context['progress_rows']
        plan_rows = [r for r in rows if r['plan'] is not None]
        emails_in_order = [r['member'].email for r in plan_rows]
        # viewer is pinned first, then alice 4/5 -> bob 1/3.
        self.assertEqual(
            emails_in_order,
            ['z-viewer@test.com', 'alice@test.com', 'bob@test.com'],
        )

    def test_no_plan_rows_pinned_to_bottom(self):
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        rows = response.context['progress_rows']
        # Last row is the no-plan member.
        self.assertEqual(rows[-1]['kind'], 'no_plan')
        self.assertEqual(rows[-1]['member'].pk, self.carol.pk)

    def test_board_uses_table_markup_for_progress_comparison(self):
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertContains(response, '<table')
        self.assertContains(response, '<th scope="col"', count=4)
        self.assertContains(response, 'Member')
        self.assertContains(response, 'Progress')
        self.assertContains(response, 'Status')
        self.assertContains(response, 'Details')


class CohortBoardSortTiebreakTest(TestCase):
    """When done/total tie, sort tiebreaker is ``member.email`` ascending."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = _make_user('viewer@test.com')
        Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        # Three peers all at 3/5 done.
        cls.peers = []
        for email in ('charlie@test.com', 'alice@test.com', 'bob@test.com'):
            member = _make_user(email)
            plan = Plan.objects.create(
                member=member, sprint=cls.sprint, visibility='cohort',
            )
            week = Week.objects.create(plan=plan, week_number=1)
            for i in range(5):
                Checkpoint.objects.create(
                    week=week,
                    description=f'cp {i}',
                    done_at=timezone.now() if i < 3 else None,
                )
            cls.peers.append(member)

    def test_tied_peers_ordered_alphabetically_by_email(self):
        self.client.force_login(self.viewer)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        peer_emails = [
            r['member'].email
            for r in response.context['progress_rows']
            if r['plan'] is not None and r['member'].pk != self.viewer.pk
        ]
        self.assertEqual(
            peer_emails,
            ['alice@test.com', 'bob@test.com', 'charlie@test.com'],
        )


class CohortBoardNoPlanAlphabeticalTest(TestCase):
    """No-plan rows are sorted alphabetically by member email."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = _make_user('viewer@test.com')
        Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        for email in ('zoe@test.com', 'amy@test.com', 'mike@test.com'):
            member = _make_user(email)
            SprintEnrollment.objects.create(sprint=cls.sprint, user=member)

    def test_no_plan_rows_sorted_by_email(self):
        self.client.force_login(self.viewer)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        no_plan_emails = [
            r['member'].email
            for r in response.context['progress_rows']
            if r['kind'] == 'no_plan'
        ]
        self.assertEqual(
            no_plan_emails,
            ['amy@test.com', 'mike@test.com', 'zoe@test.com'],
        )


class CohortBoardPrivacySentinelTest(TestCase):
    """The single most important rule: private plan exposes counts only.

    Plant unique sentinel strings in every text field of a private
    teammate's plan and verify none of them surface in the rendered HTML.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = _make_user('viewer@test.com')
        Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        cls.dana = _make_user('dana@test.com')

        cls.dana_plan = Plan.objects.create(
            member=cls.dana,
            sprint=cls.sprint,
            visibility='private',
            focus_main='SENTINEL_FOCUS_MAIN',
            focus_supporting=['SENTINEL_FOCUS_SUPPORTING'],
            accountability='SENTINEL_ACCOUNTABILITY',
            assigned_persona='SENTINEL_PERSONA',
            summary_current_situation='SENTINEL_SUMMARY_CURRENT',
            summary_goal='SENTINEL_SUMMARY_GOAL',
            summary_main_gap='SENTINEL_SUMMARY_GAP',
            summary_weekly_hours='SENTINEL_SUMMARY_HOURS',
            summary_why_this_plan='SENTINEL_SUMMARY_WHY',
        )
        week = Week.objects.create(
            plan=cls.dana_plan,
            week_number=1,
            theme='SENTINEL_WEEK_THEME',
        )
        Checkpoint.objects.create(
            week=week,
            description='SENTINEL_CHECKPOINT_DESC',
            done_at=None,
        )
        Resource.objects.create(
            plan=cls.dana_plan,
            title='SENTINEL_RESOURCE_TITLE',
            url='https://example.com/SENTINEL_RESOURCE_URL',
            note='SENTINEL_RESOURCE_NOTE',
        )
        Deliverable.objects.create(
            plan=cls.dana_plan,
            description='SENTINEL_DELIVERABLE_DESC',
        )
        NextStep.objects.create(
            plan=cls.dana_plan,
            description='SENTINEL_NEXTSTEP_DESC',
        )
        InterviewNote.objects.create(
            plan=cls.dana_plan,
            member=cls.dana,
            visibility='internal',
            body='SENTINEL_INTERNAL_NOTE_BODY',
        )

    def test_private_plan_content_does_not_leak_to_board(self):
        self.client.force_login(self.viewer)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Counts ARE intentionally exposed (0 of 1).
        self.assertContains(
            response,
            f'data-testid="progress-count-{self.dana.pk}">0 of 1',
        )
        # And the private badge.
        self.assertContains(
            response,
            f'data-testid="private-badge-{self.dana.pk}"',
        )

        for sentinel in [
            'SENTINEL_FOCUS_MAIN',
            'SENTINEL_FOCUS_SUPPORTING',
            'SENTINEL_ACCOUNTABILITY',
            'SENTINEL_PERSONA',
            'SENTINEL_SUMMARY_CURRENT',
            'SENTINEL_SUMMARY_GOAL',
            'SENTINEL_SUMMARY_GAP',
            'SENTINEL_SUMMARY_HOURS',
            'SENTINEL_SUMMARY_WHY',
            'SENTINEL_WEEK_THEME',
            'SENTINEL_CHECKPOINT_DESC',
            'SENTINEL_RESOURCE_TITLE',
            'SENTINEL_RESOURCE_URL',
            'SENTINEL_RESOURCE_NOTE',
            'SENTINEL_DELIVERABLE_DESC',
            'SENTINEL_NEXTSTEP_ASSIGNEE',
            'SENTINEL_NEXTSTEP_DESC',
            'SENTINEL_INTERNAL_NOTE_BODY',
        ]:
            self.assertNotContains(response, sentinel)


class CohortBoardHeadingTest(TestCase):
    """The page heading reads "Cohort progress"; ``<title>`` keeps "cohort board"."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = _make_user('viewer@test.com')
        Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )

    def test_page_heading_says_cohort_progress(self):
        self.client.force_login(self.viewer)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertContains(response, 'Cohort progress')

    def test_page_title_keeps_cohort_board_for_back_compat(self):
        """``<title>`` stays "<sprint name> cohort board" -- bookmarks
        and existing Playwright title selectors keep working."""
        self.client.force_login(self.viewer)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertContains(response, '<title>May 2026 cohort board')


class CohortBoardDeletedEnrollmentExcludedTest(TestCase):
    """Members whose ``SprintEnrollment`` has been deleted disappear from the board."""

    def test_deleted_enrollment_drops_member_from_all_row_kinds(self):
        sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        viewer = _make_user('viewer@test.com')
        Plan.objects.create(
            member=viewer, sprint=sprint, visibility='cohort',
        )
        gabe = _make_user('gabe@test.com')
        gabe_plan = Plan.objects.create(
            member=gabe, sprint=sprint, visibility='cohort',
        )
        # Staff has just deleted Gabe's enrollment.
        SprintEnrollment.objects.filter(sprint=sprint, user=gabe).delete()

        self.client.force_login(viewer)
        url = reverse('cohort_board', kwargs={'sprint_slug': sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        member_pks = {
            row['member'].pk
            for row in response.context['progress_rows']
        }
        self.assertNotIn(gabe.pk, member_pks)
        # And nothing about Gabe's plan renders.
        self.assertNotContains(
            response, f'data-testid="progress-row-{gabe.pk}"',
        )
        self.assertNotContains(
            response,
            f'data-testid="progress-row-private-{gabe.pk}"',
        )
        # Reference gabe_plan to silence linters and pin the assumption.
        self.assertEqual(gabe_plan.member_id, gabe.pk)
