import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import (
    ACCOUNTABILITY_SOURCE_RANDOM,
    Plan,
    Sprint,
    SprintAccountabilityPartner,
    SprintEnrollment,
)
from plans.services.accountability import assign_accountability_partners

User = get_user_model()


def _make_sprint():
    return Sprint.objects.create(
        name='May Sprint',
        slug='may-sprint',
        start_date=datetime.date(2026, 5, 1),
        status='active',
    )


class StudioSprintAccountabilityTest(TestCase):
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
        self.cara = User.objects.create_user(
            email='cara@example.com',
            password='pw',
        )
        self.sprint = _make_sprint()
        for user in (self.alice, self.bob, self.cara):
            SprintEnrollment.objects.create(sprint=self.sprint, user=user)
        self.client.login(email='staff@example.com', password='pw')

    def test_sprint_detail_renders_partner_controls(self):
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )

        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Partners')
        self.assertContains(response, 'data-testid="sprint-member-partners"')
        self.assertContains(response, 'bob@example.com')
        self.assertContains(response, 'data-testid="sprint-accountability-add-form"')
        self.assertContains(
            response,
            'data-testid="sprint-accountability-randomize-form"',
        )
        rows = response.context['sprint_member_rows']
        alice_row = next(row for row in rows if row['member'] == self.alice)
        self.assertEqual(
            [partner.email for partner in alice_row['accountability_partners']],
            ['bob@example.com'],
        )
        self.assertEqual(
            [option.email for option in alice_row['accountability_partner_options']],
            ['cara@example.com'],
        )

    def test_manual_add_endpoint_creates_reciprocal_assignment(self):
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/accountability/add',
            {
                'member_id': self.alice.pk,
                'partner_id': self.bob.pk,
            },
        )

        self.assertRedirects(response, f'/studio/sprints/{self.sprint.pk}/')
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=self.alice,
                partner=self.bob,
            ).exists()
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=self.bob,
                partner=self.alice,
            ).exists()
        )

    def test_manual_add_rejects_non_enrolled_partner(self):
        outsider = User.objects.create_user(
            email='outsider@example.com',
            password='pw',
        )

        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/accountability/add',
            {
                'member_id': self.alice.pk,
                'partner_id': outsider.pk,
            },
        )

        self.assertRedirects(response, f'/studio/sprints/{self.sprint.pk}/')
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(sprint=self.sprint).exists()
        )

    def test_remove_endpoint_deletes_reciprocal_assignment(self):
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )

        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/accountability/remove',
            {
                'member_id': self.alice.pk,
                'partner_id': self.bob.pk,
            },
        )

        self.assertRedirects(response, f'/studio/sprints/{self.sprint.pk}/')
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(sprint=self.sprint).exists()
        )

    def test_randomize_endpoint_assigns_unpartnered_members(self):
        dana = User.objects.create_user(email='dana@example.com', password='pw')
        SprintEnrollment.objects.create(sprint=self.sprint, user=dana)
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )

        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/accountability/randomize',
        )

        self.assertRedirects(response, f'/studio/sprints/{self.sprint.pk}/')
        self.assertEqual(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                source=ACCOUNTABILITY_SOURCE_RANDOM,
            ).count(),
            2,
        )
        for user in (self.cara, dana):
            self.assertEqual(
                SprintAccountabilityPartner.objects.filter(
                    sprint=self.sprint,
                    member=user,
                    source=ACCOUNTABILITY_SOURCE_RANDOM,
                ).count(),
                1,
            )

    def test_unenroll_clears_accountability_assignments(self):
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )
        enrollment = SprintEnrollment.objects.get(
            sprint=self.sprint,
            user=self.alice,
        )
        Plan.objects.create(sprint=self.sprint, member=self.alice)

        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/enrollments/'
            f'{enrollment.pk}/unenroll',
        )

        self.assertRedirects(response, f'/studio/sprints/{self.sprint.pk}/')
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(sprint=self.sprint).exists()
        )
        self.assertTrue(
            Plan.objects.filter(sprint=self.sprint, member=self.alice).exists()
        )
