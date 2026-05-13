"""Tests for the read-only individual plan view (issue #440).

The view is the cross-member read of one plan. The owner is redirected
to ``my_plan_detail`` so the visibility toggle is reachable; teammates
see the shareable body without any interview-note or toggle UI.
"""

import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Token
from plans.models import (
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
    Week,
)

User = get_user_model()


class MemberPlanDetailTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.other_sprint = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Smith',
        )
        cls.alice_plan_cohort = Plan.objects.create(
            member=cls.alice, sprint=cls.sprint, visibility='cohort',
            focus_main='Ship the SME agent prototype',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
            first_name='Bob', last_name='Jones',
        )
        cls.bob_plan_cohort = Plan.objects.create(
            member=cls.bob, sprint=cls.sprint, visibility='cohort',
        )
        cls.charlie = User.objects.create_user(
            email='charlie@test.com', password='pw',
        )
        cls.charlie_plan_private = Plan.objects.create(
            member=cls.charlie, sprint=cls.sprint, visibility='private',
        )
        cls.outsider = User.objects.create_user(
            email='outsider@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.outsider, sprint=cls.other_sprint,
            visibility='cohort',
        )

    def test_member_plan_detail_owner_redirects_to_my_plan(self):
        self.client.force_login(self.alice)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        my_plan_url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertRedirects(response, my_plan_url, fetch_redirect_response=False)

    def test_member_plan_detail_teammate_can_view_cohort_plan(self):
        self.client.force_login(self.bob)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ship the SME agent prototype')

    def test_member_plan_detail_renders_markdown_safely(self):
        self.alice_plan_cohort.goal = 'Ship **bold** work'
        self.alice_plan_cohort.summary_goal = 'Ship **bold** work'
        self.alice_plan_cohort.focus_main = '[Docs](https://example.com)'
        self.alice_plan_cohort.accountability = '<script>alert(1)</script>\n\n- Weekly'
        self.alice_plan_cohort.save(
            update_fields=['goal', 'summary_goal', 'focus_main', 'accountability'],
        )
        week = Week.objects.create(plan=self.alice_plan_cohort, week_number=1)
        Checkpoint.objects.create(
            week=week,
            description='Checkpoint with `code` and [bad](javascript:alert(1))',
        )
        Resource.objects.create(
            plan=self.alice_plan_cohort,
            title='Resource',
            note='Use **notes**',
        )
        Deliverable.objects.create(
            plan=self.alice_plan_cohort,
            description='Deliver **value**',
        )
        NextStep.objects.create(
            plan=self.alice_plan_cohort,
            description='Next *step*',
        )

        self.client.force_login(self.bob)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<strong>bold</strong>', html=True)
        self.assertContains(response, '<code>code</code>', html=True)
        self.assertContains(response, '<strong>notes</strong>', html=True)
        self.assertContains(response, '<strong>value</strong>', html=True)
        self.assertContains(response, '<em>step</em>', html=True)
        self.assertContains(response, 'href="https://example.com"')
        self.assertNotContains(response, '<script>alert')
        self.assertNotContains(response, 'alert(1)')
        self.assertNotContains(response, 'href="javascript:')

    def test_member_plan_detail_teammate_has_read_only_status_indicators(self):
        week = Week.objects.create(plan=self.alice_plan_cohort, week_number=1)
        Checkpoint.objects.create(week=week, description='Read only checkpoint')
        Deliverable.objects.create(
            plan=self.alice_plan_cohort,
            description='Read only deliverable',
        )
        NextStep.objects.create(
            plan=self.alice_plan_cohort,
            description='Read only next step',
        )

        self.client.force_login(self.bob)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-done-toggle')
        self.assertNotContains(response, 'data-markdown-input')
        self.assertNotContains(response, 'data-testid="plan-item-edit"')

    def test_member_plan_detail_teammate_blocked_from_private_plan(self):
        self.client.force_login(self.bob)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.charlie_plan_private.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_member_plan_detail_other_sprint_blocked(self):
        """A user enrolled only in another sprint cannot view the plan.

        Even when the plan's visibility is ``cohort`` -- the cohort
        scope is per-sprint, not global.
        """
        self.client.force_login(self.outsider)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_member_plan_detail_anonymous_redirects(self):
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])


class MemberPlanDetailNoteIsolationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
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
        )
        Plan.objects.create(
            member=cls.bob, sprint=cls.sprint, visibility='cohort',
        )

    def setUp(self):
        self.client.force_login(self.bob)

    def _url(self):
        return reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan.pk,
            },
        )

    def test_member_plan_detail_does_not_render_internal_note(self):
        InterviewNote.objects.create(
            plan=self.alice_plan, member=self.alice,
            visibility='internal', body='SECRET_INTERNAL_DETAIL',
        )
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'SECRET_INTERNAL_DETAIL')

    def test_member_plan_detail_does_not_render_external_note(self):
        InterviewNote.objects.create(
            plan=self.alice_plan, member=self.alice,
            visibility='external', body='NOT_SURFACED_DETAIL',
        )
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'NOT_SURFACED_DETAIL')


class MyPlanDetailOwnerSurfaceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='other@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.owner,
            sprint=cls.sprint,
            visibility='cohort',
            summary_goal='Ship **owner** view',
        )
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)
        cls.checkpoint = Checkpoint.objects.create(
            week=cls.week,
            description='Build **prototype**',
        )
        cls.deliverable = Deliverable.objects.create(
            plan=cls.plan,
            description='Demo **recording**',
        )
        cls.next_step = NextStep.objects.create(
            plan=cls.plan,
            description='Book **review**',
        )
        cls.linked_resource = Resource.objects.create(
            plan=cls.plan,
            title='Launch checklist',
            url='https://example.com/checklist',
            note='Use before demo',
        )
        cls.unlinked_resource = Resource.objects.create(
            plan=cls.plan,
            title='Internal prep notes',
        )

    def test_owner_page_renders_markdown_and_edit_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse(
                'my_plan_detail',
                kwargs={
                    'sprint_slug': self.sprint.slug,
                    'plan_id': self.plan.pk,
                },
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<strong>owner</strong>', html=True)
        self.assertContains(response, '<strong>prototype</strong>', html=True)
        self.assertContains(response, 'data-testid="plan-row-done-toggle"')
        self.assertContains(response, 'data-testid="plan-item-markdown-input"')
        self.assertContains(response, 'data-testid="plan-item-edit"')
        self.assertNotContains(response, 'data-api-token=')
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertEqual(
            Token.objects.filter(
                user=self.owner, name='member-plan-editor',
            ).count(),
            0,
        )
        self.assertNotContains(response, 'Internal notes')
        self.assertNotContains(response, 'href="/studio/')

    def test_owner_workspace_prioritizes_timeline_and_resources(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse(
                'my_plan_detail',
                kwargs={
                    'sprint_slug': self.sprint.slug,
                    'plan_id': self.plan.pk,
                },
            ),
        )
        body = response.content.decode('utf-8')
        self.assertContains(response, 'Sprint workspace')
        self.assertContains(response, 'data-testid="plan-weeks"')
        self.assertContains(response, 'data-testid="plan-deliverables"')
        self.assertContains(response, 'data-testid="plan-next-steps"')
        self.assertContains(response, 'data-testid="plan-resources"')
        self.assertContains(response, 'href="https://example.com/checklist"')
        self.assertContains(response, 'Internal prep notes')
        self.assertLess(
            body.index('data-testid="plan-weeks"'),
            body.index('data-testid="plan-summary"'),
        )

    def test_other_member_gets_404_for_owner_page(self):
        self.client.force_login(self.other)
        response = self.client.get(
            reverse(
                'my_plan_detail',
                kwargs={
                    'sprint_slug': self.sprint.slug,
                    'plan_id': self.plan.pk,
                },
            ),
        )
        self.assertEqual(response.status_code, 404)
