"""Tests for the sprint plan workspace unification (issue #583).

This module covers the template + view changes the issue is filed for:

- The "Edit workspace" CTA is removed from ``my_plan_detail`` and the
  legacy /edit URL responds with HTTP 301 to the unified workspace.
- The in-page ``data-testid="plan-messages"`` block is gone; messages
  render exactly once via the base ``data-testid="messages-region"``.
- The visibility control is a switch (no ``<select>``, no separate Save
  button, no ``data-testid="visibility-save"``).
- The boilerplate "Checkpoints are the primary flow for this sprint
  plan." sentence is gone from ``_plan_body.html``.
- The deliverables / next-steps grid is single-column at every viewport
  (no ``lg:grid-cols-2`` on the action-items wrapper).
- The teammate read-only view still works and has no edit affordance.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Resource,
    Sprint,
    SprintEnrollment,
    Week,
)

User = get_user_model()


class UnifiedWorkspaceTemplateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='private',
        )
        week = Week.objects.create(plan=cls.plan, week_number=1, position=0)
        Checkpoint.objects.create(
            week=week, description='Build prototype', position=0,
        )
        Checkpoint.objects.create(
            week=week, description='Demo to user', position=1,
        )
        Deliverable.objects.create(
            plan=cls.plan, description='Record demo', position=0,
        )
        NextStep.objects.create(
            plan=cls.plan, description='Book review', position=0,
        )
        Resource.objects.create(
            plan=cls.plan, title='RAG paper', url='https://example.com/rag',
            position=0,
        )

    def _detail_url(self):
        return reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

    def test_edit_workspace_cta_is_removed(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="my-plan-edit-cta"')
        self.assertNotContains(response, 'Edit workspace')

    def test_in_page_plan_messages_block_is_removed(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        self.assertNotContains(response, 'data-testid="plan-messages"')
        self.assertNotContains(response, 'data-testid="plan-message"')

    def test_visibility_select_and_save_button_are_removed(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        self.assertNotContains(response, 'data-testid="visibility-select"')
        self.assertNotContains(response, 'data-testid="visibility-save"')
        # The new toggle uses role="switch" + a stable testid for tests.
        self.assertContains(response, 'data-testid="plan-visibility-toggle"')
        self.assertContains(response, 'role="switch"')

    def _toggle_label_text(self, response):
        body = response.content.decode()
        label_start = body.find('data-testid="plan-visibility-label"')
        self.assertGreater(label_start, 0)
        gt = body.find('>', label_start)
        lt = body.find('</span>', gt)
        return body[gt + 1:lt].strip()

    def test_visibility_toggle_renders_aria_checked_false_for_private(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        self.assertContains(response, 'aria-checked="false"')
        # Label reflects the stored state.
        self.assertEqual(self._toggle_label_text(response), 'Private')

    def test_visibility_toggle_renders_aria_checked_true_for_cohort(self):
        self.plan.visibility = 'cohort'
        self.plan.save(update_fields=['visibility'])
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        self.assertContains(response, 'aria-checked="true"')
        self.assertEqual(
            self._toggle_label_text(response), 'Shared with cohort',
        )

    def test_visibility_helper_text_is_private_when_private(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        self.assertContains(response, 'Only you and the team can see this plan.')
        self.assertNotContains(
            response,
            'Visible to other members of the same sprint on the cohort board.',
        )

    def test_visibility_helper_text_is_cohort_when_shared(self):
        self.plan.visibility = 'cohort'
        self.plan.save(update_fields=['visibility'])
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        self.assertContains(
            response,
            'Visible to other members of the same sprint on the cohort board.',
        )
        self.assertNotContains(
            response, 'Only you and the team can see this plan.',
        )

    def test_visibility_does_not_use_the_word_public(self):
        """The toggle is binary (private vs cohort). The reserved
        ``public`` enum value MUST NOT leak into the labels until a
        future issue adds the third state."""
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        toggle_section_start = response.content.find(
            b'data-testid="plan-visibility-form"'
        )
        toggle_section_end = response.content.find(
            b'</section>', toggle_section_start,
        )
        section_html = response.content[
            toggle_section_start:toggle_section_end
        ].decode()
        self.assertNotIn('Public', section_html)
        self.assertNotIn('public', section_html)

    def test_boilerplate_timeline_sentence_is_removed(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        self.assertNotContains(
            response,
            'Checkpoints are the primary flow for this sprint plan.',
        )

    def test_action_items_section_is_single_column(self):
        """The deliverables/next-steps wrapper must not opt into a
        two-column grid at any viewport (issue #583 item 5)."""
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        body = response.content.decode()
        action_start = body.find('data-testid="plan-action-items"')
        self.assertGreater(action_start, 0)
        # Find the close of the section we care about so we don't pick
        # up grid classes from unrelated sections like Plan context.
        action_end = body.find('</section>', action_start)
        section_html = body[action_start:action_end]
        self.assertNotIn('lg:grid-cols-2', section_html)
        self.assertNotIn('grid-cols-2', section_html)

    def test_section_order_is_resources_then_deliverables_then_next_steps(self):
        """Issue #583 item 5: section order must be
        weeks -> resources -> deliverables -> next steps -> plan
        context. We assert the relative positions of the testid markers
        in the rendered HTML."""
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        body = response.content.decode()
        weeks_at = body.find('data-testid="plan-weeks"')
        resources_at = body.find('data-testid="plan-resources"')
        action_items_at = body.find('data-testid="plan-action-items"')
        summary_at = body.find('data-testid="plan-summary"')
        self.assertGreater(weeks_at, 0)
        self.assertGreater(resources_at, weeks_at)
        self.assertGreater(action_items_at, resources_at)
        self.assertGreater(summary_at, action_items_at)

    def test_action_items_renders_deliverables_before_next_steps(self):
        """Within the action-items block, deliverables sit above next
        steps. The user-facing top-to-bottom order is documented in the
        spec; assert it via marker positions inside the section."""
        self.client.force_login(self.owner)
        response = self.client.get(self._detail_url())
        body = response.content.decode()
        action_start = body.find('data-testid="plan-action-items"')
        section_end = body.find('</section>', action_start)
        section_html = body[action_start:section_end]
        deliverables_at = section_html.find('data-testid="plan-deliverables"')
        next_steps_at = section_html.find('data-testid="plan-next-steps"')
        self.assertGreater(deliverables_at, 0)
        self.assertGreater(next_steps_at, deliverables_at)


class LegacyEditUrlRedirectTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='private',
        )

    def _edit_url(self):
        return reverse(
            'my_plan_edit',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

    def _detail_url(self):
        return reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

    def test_legacy_edit_url_returns_301_for_owner(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._edit_url())
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self._detail_url())

    def test_legacy_edit_url_returns_301_for_anonymous_user(self):
        """An anonymous user with a stale link should also land on the
        canonical URL via 301 -- ``my_plan_detail`` then enforces auth.
        Without this, the legacy URL would redirect them straight to
        the login page and they'd lose their stored target."""
        response = self.client.get(self._edit_url())
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self._detail_url())

    def test_legacy_edit_url_following_redirect_lands_on_workspace(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._edit_url(), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'plans/my_plan_detail.html')


class TeammateReadOnlyViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.owner)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.teammate)
        cls.shared_plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='cohort',
            focus_main='Ship the SME agent',
        )
        Plan.objects.create(
            member=cls.teammate, sprint=cls.sprint, visibility='private',
        )

    def test_teammate_sees_plan_without_edit_affordances(self):
        self.client.force_login(self.teammate)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.shared_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # No owner-only controls render on the teammate view.
        self.assertNotContains(response, 'data-testid="plan-visibility-form"')
        self.assertNotContains(response, 'data-testid="plan-visibility-toggle"')
        self.assertNotContains(response, 'data-testid="plan-row-done-toggle"')
        self.assertNotContains(response, 'data-testid="plan-item-edit"')
        self.assertNotContains(response, 'data-testid="my-plan-edit-cta"')


class MessagesRegionDedupeTest(TestCase):
    """After server-side actions, messages render exactly once."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        # Fresh plan per test so the week-note POST below doesn't leak.
        self.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        self.plan = Plan.objects.create(
            member=self.owner, sprint=self.sprint, visibility='private',
        )
        self.week = Week.objects.create(
            plan=self.plan, week_number=1, position=0,
        )

    def test_week_note_create_flash_renders_in_base_messages_region_only(self):
        """The week-note create flow emits a messages flash. After issue
        #583 it must appear exactly once -- in the base region.
        Previously the in-page ``plan-messages`` block re-rendered the
        same queue, so it showed twice."""
        self.client.force_login(self.owner)
        url = reverse(
            'week_note_create',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.plan.pk,
                'week_id': self.week.pk,
            },
        )
        response = self.client.post(
            url, {'body': 'Tried out the new agent.'}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The new authoritative region appears at most once on the page.
        self.assertEqual(body.count('data-testid="messages-region"'), 1)
        # The deprecated in-page region is gone.
        self.assertNotIn('data-testid="plan-messages"', body)
        self.assertNotIn('data-testid="plan-message"', body)
