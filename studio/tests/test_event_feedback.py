"""Tests for the Studio event Feedback panel (issue #679).

Covers:
- ``event_edit`` exposes ``feedback_entries``, ``feedback_count``,
  ``feedback_avg``, and ``feedback_comment_count`` to the template.
- The Feedback panel renders on the edit page (only).
- Empty state copy renders when no rows exist.
- The summary row shows the rounded average, rating count, and
  comment count when rows exist.
- One row per submission appears in the table.
- Anonymous and non-staff users are rejected by ``@staff_required``.
- Aggregate excludes comment-only rows.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventFeedback
from tests.fixtures import StaffUserMixin, TierSetupMixin

User = get_user_model()


def _make_past_event():
    now = timezone.now()
    return Event.objects.create(
        title='Feedback Event',
        slug='feedback-event',
        start_datetime=now - timedelta(hours=3),
        end_datetime=now - timedelta(hours=1),
        status='completed',
    )


class StudioEventFeedbackContextTest(TierSetupMixin, StaffUserMixin, TestCase):
    """``event_edit`` exposes the per-event feedback context."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_past_event()
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Anders',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
        )
        cls.carol = User.objects.create_user(
            email='carol@test.com', password='pw',
        )
        # Mixed: two rated, one comment-only.
        EventFeedback.objects.create(
            event=cls.event, user=cls.alice, rating=5,
            comment='Loved the live coding',
        )
        EventFeedback.objects.create(
            event=cls.event, user=cls.bob, rating=4,
        )
        EventFeedback.objects.create(
            event=cls.event, user=cls.carol, comment='Sound was choppy',
        )

    def test_edit_context_exposes_feedback_fields(self):
        self.client.login(**self.staff_credentials)
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        # Two rated entries (5, 4 → avg 4.5)
        self.assertEqual(ctx['feedback_count'], 2)
        self.assertEqual(ctx['feedback_avg'], 4.5)
        # Two non-empty comments (Alice + Carol).
        self.assertEqual(ctx['feedback_comment_count'], 2)
        # All three rows are in the queryset.
        self.assertEqual(len(ctx['feedback_entries']), 3)

    def test_panel_renders_count_chip(self):
        self.client.login(**self.staff_credentials)
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="event-feedback-panel"')
        self.assertContains(response, 'data-testid="feedback-count-chip"')
        # "2 ratings" plural form
        self.assertContains(response, '2 ratings')

    def test_panel_renders_one_row_per_submission(self):
        self.client.login(**self.staff_credentials)
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        # 3 rows total in the tbody — all three submissions appear.
        self.assertContains(response, 'data-testid="feedback-row"', count=3)
        # Comments surface in the table.
        self.assertContains(response, 'Loved the live coding')
        self.assertContains(response, 'Sound was choppy')

    def test_summary_shows_avg_and_counts(self):
        self.client.login(**self.staff_credentials)
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="feedback-summary"')
        self.assertContains(response, '4.5')
        self.assertContains(response, '2 ratings')
        self.assertContains(response, '2 comments')


class StudioEventFeedbackEmptyStateTest(TierSetupMixin, StaffUserMixin, TestCase):
    """When there are no rows, the empty state replaces the table."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_past_event()

    def test_empty_state_copy_renders(self):
        self.client.login(**self.staff_credentials)
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="feedback-empty"')
        self.assertContains(response, 'No feedback submitted yet.')
        # Table itself is not rendered when empty.
        self.assertNotContains(response, 'data-testid="feedback-table"')


class StudioEventFeedbackCreateGuardTest(TierSetupMixin, StaffUserMixin, TestCase):
    """The Feedback panel only renders on edit, not create."""

    def test_create_form_has_no_feedback_panel(self):
        self.client.login(**self.staff_credentials)
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)
        # form_action == 'create' → the whole edit-only wrapper is
        # skipped, so the panel testid must not appear.
        self.assertNotContains(response, 'data-testid="event-feedback-panel"')


class StudioEventFeedbackAccessTest(TierSetupMixin, TestCase):
    """Non-staff and anonymous users cannot read the edit page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_past_event()
        cls.non_staff = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def test_anonymous_redirected(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        # ``@staff_required`` either redirects or 403s; both prevent
        # access. We assert the page is NOT the studio edit page.
        self.assertNotEqual(response.status_code, 200)

    def test_non_staff_blocked(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertNotEqual(response.status_code, 200)
