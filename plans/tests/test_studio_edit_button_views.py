"""View tests for the "Edit in Studio" button on sprint surfaces (issue #667).

Covers the public sprint detail page (anonymous-visible) and the cohort
board (login + enrollment-gated). Both render the staff-only button via
``includes/_studio_edit_button.html`` so the button MUST be absent from
the HTML for non-staff visitors.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from plans.models import Sprint, SprintEnrollment

User = get_user_model()

STUDIO_BUTTON_TESTID = 'data-testid="studio-edit-button"'


@tag('core')
class StudioEditButtonSprintDetailTest(TestCase):
    """Sprint detail page renders the button for staff only."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )

    def test_staff_sees_button(self):
        User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/sprints/{self.sprint.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.sprint.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(f'/sprints/{self.sprint.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')

    def test_free_user_does_not_see_button(self):
        User.objects.create_user(email='free@test.com', password='pw')
        self.client.login(email='free@test.com', password='pw')
        response = self.client.get(f'/sprints/{self.sprint.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')


@tag('core')
class StudioEditButtonCohortBoardTest(TestCase):
    """Cohort board renders the button for staff (enrolled) only."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='June 2026',
            slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
            status='active',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.staff)

    def test_staff_sees_button(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/sprints/{self.sprint.slug}/board')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.sprint.get_studio_edit_url()}"',
        )
