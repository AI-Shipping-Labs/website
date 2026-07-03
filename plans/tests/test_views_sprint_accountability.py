import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import Sprint, SprintEnrollment
from plans.services.accountability import assign_accountability_partners

User = get_user_model()


class SprintDetailAccountabilityPartnerTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com',
            password='pw',
            is_staff=True,
        )
        self.alice = User.objects.create_user(
            email='alice@example.com',
            password='pw',
            first_name='Alice',
            last_name='A',
        )
        self.bob = User.objects.create_user(
            email='bob@example.com',
            password='pw',
            first_name='Bob',
            last_name='B',
        )
        self.sprint = Sprint.objects.create(
            name='May Sprint',
            slug='may-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.alice)
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.bob)

    def test_enrolled_member_sees_assigned_partner(self):
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )
        self.client.login(email='alice@example.com', password='pw')

        response = self.client.get('/sprints/may-sprint')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-accountability-partners"')
        self.assertContains(response, 'Your partners')
        self.assertContains(response, 'Bob B')
        self.assertContains(response, 'bob@example.com')

    def test_enrolled_member_without_partner_sees_waiting_state(self):
        self.client.login(email='alice@example.com', password='pw')

        response = self.client.get('/sprints/may-sprint')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Partners have not been assigned yet.')
