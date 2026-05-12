"""Tests for the JS confirm dialog wiring on the Leave sprint buttons.

The actual confirm-vs-cancel JS interaction lives in Playwright. These
tests only verify that the form submit is wrapped with an ``onsubmit``
handler that calls ``confirm()`` -- so a future refactor that removes
the wrapper is caught at the Django layer too.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class LeaveConfirmDialogWiringTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            status='active', min_tier_level=0,
        )
        cls.member = User.objects.create_user(
            email='m@test.com', password='pw',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.member)

    def test_sprint_detail_leave_button_has_confirm_dialog(self):
        self.client.force_login(self.member)
        url = reverse(
            'sprint_detail', kwargs={'sprint_slug': self.sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Specifically the leave form needs a confirm wrapper.
        self.assertContains(response, 'data-testid="sprint-cta-leave"')
        # The onsubmit handler is the wiring under test.
        self.assertContains(response, 'onsubmit="return confirm(')

    def test_cohort_board_leave_button_has_confirm_dialog(self):
        # Member needs a plan to see the cohort_board callout flavour
        # of the leave button.
        Plan.objects.create(
            member=self.member, sprint=self.sprint, visibility='cohort',
        )
        self.client.force_login(self.member)
        url = reverse(
            'cohort_board', kwargs={'sprint_slug': self.sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="cohort-board-leave-sprint"',
        )
        self.assertContains(response, 'onsubmit="return confirm(')

    def test_cohort_board_leave_button_visible_in_no_plan_pending_aside(self):
        """Leave button also appears in the viewer-plan-pending callout."""
        self.client.force_login(self.member)
        url = reverse(
            'cohort_board', kwargs={'sprint_slug': self.sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="viewer-plan-pending"')
        self.assertContains(
            response, 'data-testid="cohort-board-leave-sprint"',
        )
