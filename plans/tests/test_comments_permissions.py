"""Tests for the plan-aware comments permission hook (issue #499).

The hook lets the existing ``comments`` app safely host plan
threads. Behaviour we lock in:

- Anonymous viewers can read non-plan threads but cannot read or
  write plan threads.
- Cohort plans accept reads and writes from the owner, sprint-mates,
  and staff. Outsiders (no enrollment) get a 404 on read so the
  existence of a private plan does not leak.
- Private plans accept reads from the owner and staff; writes are
  staff-only.
- Non-plan ``content_id`` UUIDs preserve the existing comments
  behaviour (any authenticated user may write, anyone can read).
"""

import datetime
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from comments.models import Comment, CommentVote
from plans.comments_permissions import (
    composer_state_for_owner_view,
    resolve_plan_for_content_id,
    viewer_can_read_plan_thread,
    viewer_can_write_plan_thread,
)
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class _PlanCommentsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Comments Sprint', slug='comments-sprint',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.other_sprint = Sprint.objects.create(
            name='Other Sprint', slug='other-sprint',
            start_date=datetime.date(2026, 6, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner-c@test.com', password='pw',
        )
        cls.teammate = User.objects.create_user(
            email='teammate-c@test.com', password='pw',
        )
        cls.outsider = User.objects.create_user(
            email='outsider-c@test.com', password='pw',
        )
        cls.staff = User.objects.create_user(
            email='staff-c@test.com', password='pw', is_staff=True,
        )
        cls.cohort_plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='cohort',
        )
        cls.private_plan = Plan.objects.create(
            member=cls.owner, sprint=cls.other_sprint, visibility='private',
        )
        # Plan creation auto-creates a SprintEnrollment for the
        # owner (see ``plans/signals.py``); the teammate needs an
        # explicit row.
        SprintEnrollment.objects.get_or_create(
            sprint=cls.sprint, user=cls.teammate,
        )


class ResolvePlanHelperTest(_PlanCommentsBase):
    def test_returns_plan_for_matching_uuid(self):
        plan = resolve_plan_for_content_id(self.cohort_plan.comment_content_id)
        self.assertEqual(plan.pk, self.cohort_plan.pk)

    def test_returns_none_for_unrelated_uuid(self):
        self.assertIsNone(resolve_plan_for_content_id(uuid.uuid4()))

    def test_returns_none_for_none_content_id(self):
        self.assertIsNone(resolve_plan_for_content_id(None))


class PlanThreadReadPermsTest(_PlanCommentsBase):
    def test_owner_can_read_private_plan(self):
        self.assertTrue(
            viewer_can_read_plan_thread(self.private_plan, self.owner),
        )

    def test_owner_can_read_cohort_plan(self):
        self.assertTrue(
            viewer_can_read_plan_thread(self.cohort_plan, self.owner),
        )

    def test_teammate_can_read_cohort_plan(self):
        self.assertTrue(
            viewer_can_read_plan_thread(self.cohort_plan, self.teammate),
        )

    def test_outsider_cannot_read_cohort_plan(self):
        self.assertFalse(
            viewer_can_read_plan_thread(self.cohort_plan, self.outsider),
        )

    def test_teammate_cannot_read_private_plan(self):
        self.assertFalse(
            viewer_can_read_plan_thread(self.private_plan, self.teammate),
        )

    def test_staff_can_read_private_plan(self):
        self.assertTrue(
            viewer_can_read_plan_thread(self.private_plan, self.staff),
        )

    def test_anonymous_cannot_read(self):
        self.assertFalse(
            viewer_can_read_plan_thread(self.cohort_plan, None),
        )


class PlanThreadWritePermsTest(_PlanCommentsBase):
    def test_owner_can_write_cohort_plan(self):
        self.assertTrue(
            viewer_can_write_plan_thread(self.cohort_plan, self.owner),
        )

    def test_teammate_can_write_cohort_plan(self):
        self.assertTrue(
            viewer_can_write_plan_thread(self.cohort_plan, self.teammate),
        )

    def test_owner_cannot_write_private_plan(self):
        """Non-staff owner of a private plan cannot post; only staff can."""
        self.assertFalse(
            viewer_can_write_plan_thread(self.private_plan, self.owner),
        )

    def test_staff_can_write_private_plan(self):
        self.assertTrue(
            viewer_can_write_plan_thread(self.private_plan, self.staff),
        )

    def test_outsider_cannot_write_cohort_plan(self):
        self.assertFalse(
            viewer_can_write_plan_thread(self.cohort_plan, self.outsider),
        )

    def test_anonymous_cannot_write(self):
        self.assertFalse(
            viewer_can_write_plan_thread(self.cohort_plan, None),
        )


class ComposerStateHelperTest(_PlanCommentsBase):
    def test_cohort_owner_composer_enabled(self):
        disabled, reason = composer_state_for_owner_view(
            self.cohort_plan, self.owner,
        )
        self.assertFalse(disabled)
        self.assertEqual(reason, '')

    def test_private_owner_composer_disabled_with_reason(self):
        disabled, reason = composer_state_for_owner_view(
            self.private_plan, self.owner,
        )
        self.assertTrue(disabled)
        self.assertIn('cohort', reason.lower())

    def test_private_owner_who_is_staff_composer_enabled(self):
        # Staff owner: pretend the owner is staff (different scenario).
        self.owner.is_staff = True
        disabled, reason = composer_state_for_owner_view(
            self.private_plan, self.owner,
        )
        self.assertFalse(disabled)


class PlanCommentsAPITest(_PlanCommentsBase):
    """End-to-end checks against the comments API URLs.

    Confirms the permission hook is wired into ``list_comments`` /
    ``create_comment`` / ``reply_to_comment`` / ``toggle_vote`` -- not
    just available as an importable helper.
    """

    def _list_url(self, plan):
        return reverse(
            'comments_endpoint',
            kwargs={'content_id': plan.comment_content_id},
        )

    def test_outsider_get_returns_404_for_cohort_plan(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self._list_url(self.cohort_plan))
        self.assertEqual(response.status_code, 404)
        # Make absolutely sure no comment bodies were ever in the
        # response (defence in depth: test_payload would never be
        # populated, but assert the JSON shape we want).
        try:
            data = response.json()
            self.assertNotIn('comments', data)
        except (ValueError, AttributeError):
            pass

    def test_outsider_post_top_level_returns_403(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            self._list_url(self.cohort_plan),
            data=json.dumps({'body': 'sneaky'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            Comment.objects.filter(
                content_id=self.cohort_plan.comment_content_id,
            ).count(),
            0,
        )

    def test_anonymous_post_returns_401(self):
        response = self.client.post(
            self._list_url(self.cohort_plan),
            data=json.dumps({'body': 'anon'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_teammate_can_post_on_cohort_plan(self):
        self.client.force_login(self.teammate)
        response = self.client.post(
            self._list_url(self.cohort_plan),
            data=json.dumps({'body': 'How are you evaluating retrieval?'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn('id', body)
        self.assertEqual(
            Comment.objects.filter(
                content_id=self.cohort_plan.comment_content_id,
            ).count(),
            1,
        )

    def test_owner_cannot_post_on_private_plan(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._list_url(self.private_plan),
            data=json.dumps({'body': 'cannot write'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            Comment.objects.filter(
                content_id=self.private_plan.comment_content_id,
            ).count(),
            0,
        )

    def test_staff_can_post_on_private_plan(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            self._list_url(self.private_plan),
            data=json.dumps({'body': 'staff comment'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)

    def test_owner_can_read_existing_private_thread_comments(self):
        Comment.objects.create(
            content_id=self.private_plan.comment_content_id,
            user=self.staff,
            body='STAFF_PRIVATE_COMMENT',
        )
        self.client.force_login(self.owner)
        response = self.client.get(self._list_url(self.private_plan))
        self.assertEqual(response.status_code, 200)
        bodies = [c['body'] for c in response.json()['comments']]
        self.assertIn('STAFF_PRIVATE_COMMENT', bodies)

    def test_outsider_cannot_reply_to_plan_thread(self):
        parent = Comment.objects.create(
            content_id=self.cohort_plan.comment_content_id,
            user=self.teammate,
            body='top',
        )
        self.client.force_login(self.outsider)
        response = self.client.post(
            reverse('comments_reply', kwargs={'comment_id': parent.pk}),
            data=json.dumps({'body': 'sneaky reply'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(parent.replies.count(), 0)

    def test_outsider_cannot_vote_on_plan_thread(self):
        comment = Comment.objects.create(
            content_id=self.cohort_plan.comment_content_id,
            user=self.teammate,
            body='top',
        )
        self.client.force_login(self.outsider)
        response = self.client.post(
            reverse('comments_vote', kwargs={'comment_id': comment.pk}),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(CommentVote.objects.filter(comment=comment).count(), 0)

    def test_owner_cannot_vote_on_private_plan(self):
        comment = Comment.objects.create(
            content_id=self.private_plan.comment_content_id,
            user=self.staff,
            body='staff top',
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('comments_vote', kwargs={'comment_id': comment.pk}),
        )
        self.assertEqual(response.status_code, 403)

    def test_non_plan_uuid_keeps_default_behaviour(self):
        """A ``content_id`` that does not match any plan must keep the
        original "any authenticated user can write" rule.

        Regression guard: the plan permission hook must NOT regress
        the course / workshop comments threads.
        """
        unrelated = uuid.uuid4()
        self.client.force_login(self.outsider)
        response = self.client.post(
            reverse(
                'comments_endpoint', kwargs={'content_id': unrelated},
            ),
            data=json.dumps({'body': 'unrelated thread'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Comment.objects.filter(content_id=unrelated).count(), 1,
        )


class MyPlanRendersCommentsSectionTest(_PlanCommentsBase):
    """The owner page embeds ``comments/_qa_section.html`` with the plan UUID."""

    def test_owner_page_includes_comments_section(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('my_plan_detail', kwargs={'plan_id': self.cohort_plan.pk}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-comments-section"')
        self.assertContains(
            response,
            f'data-content-id="{self.cohort_plan.comment_content_id}"',
        )
        # On a cohort plan the composer is enabled.
        self.assertContains(response, 'id="qa-new-question"')

    def test_private_owner_page_disables_composer(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('my_plan_detail', kwargs={'plan_id': self.private_plan.pk}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-comments-section"')
        self.assertContains(response, 'data-testid="qa-composer-disabled"')
        self.assertNotContains(response, 'id="qa-new-question"')

    def test_teammate_page_includes_enabled_composer(self):
        self.client.force_login(self.teammate)
        response = self.client.get(
            reverse(
                'member_plan_detail',
                kwargs={
                    'sprint_slug': self.sprint.slug,
                    'plan_id': self.cohort_plan.pk,
                },
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-comments-section"')
        self.assertContains(response, 'id="qa-new-question"')
