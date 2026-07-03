import datetime
import random

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from plans.models import (
    ACCOUNTABILITY_SOURCE_MANUAL,
    ACCOUNTABILITY_SOURCE_RANDOM,
    Sprint,
    SprintAccountabilityPartner,
    SprintEnrollment,
)
from plans.services.accountability import (
    assign_accountability_partners,
    randomize_accountability_partners,
    remove_accountability_partners,
)

User = get_user_model()


class SprintAccountabilityPartnerModelTest(TestCase):
    def setUp(self):
        self.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        self.alice = User.objects.create_user('alice@example.com', password='pw')
        self.bob = User.objects.create_user('bob@example.com', password='pw')

    def test_unique_member_partner_per_sprint(self):
        SprintAccountabilityPartner.objects.create(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            SprintAccountabilityPartner.objects.create(
                sprint=self.sprint,
                member=self.alice,
                partner=self.bob,
            )

    def test_self_partner_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SprintAccountabilityPartner.objects.create(
                sprint=self.sprint,
                member=self.alice,
                partner=self.alice,
            )


class SprintAccountabilityServiceTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            'staff@example.com',
            password='pw',
            is_staff=True,
        )
        self.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        self.alice = User.objects.create_user('alice@example.com', password='pw')
        self.bob = User.objects.create_user('bob@example.com', password='pw')
        self.cara = User.objects.create_user('cara@example.com', password='pw')
        for user in (self.alice, self.bob, self.cara):
            SprintEnrollment.objects.create(sprint=self.sprint, user=user)

    def test_manual_assignment_creates_reciprocal_rows(self):
        created = assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )

        self.assertEqual(created, 2)
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=self.alice,
                partner=self.bob,
                source=ACCOUNTABILITY_SOURCE_MANUAL,
                assigned_by=self.staff,
            ).exists()
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=self.bob,
                partner=self.alice,
            ).exists()
        )

    def test_member_can_have_multiple_reciprocal_partners(self):
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.cara,
            assigned_by=self.staff,
        )

        self.assertEqual(
            set(
                SprintAccountabilityPartner.objects.filter(
                    sprint=self.sprint,
                    member=self.alice,
                ).values_list('partner__email', flat=True),
            ),
            {'bob@example.com', 'cara@example.com'},
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=self.bob,
                partner=self.alice,
            ).exists()
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=self.cara,
                partner=self.alice,
            ).exists()
        )

    def test_assignment_requires_enrolled_partner(self):
        outsider = User.objects.create_user('outsider@example.com', password='pw')

        with self.assertRaises(ValidationError):
            assign_accountability_partners(
                sprint=self.sprint,
                member=self.alice,
                partner=outsider,
                assigned_by=self.staff,
            )

    def test_remove_assignment_deletes_both_directions(self):
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )

        deleted = remove_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
        )

        self.assertEqual(deleted, 2)
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(sprint=self.sprint).exists()
        )

    def test_randomize_preserves_manual_assignments_and_pairs_unassigned(self):
        dana = User.objects.create_user('dana@example.com', password='pw')
        SprintEnrollment.objects.create(sprint=self.sprint, user=dana)
        assign_accountability_partners(
            sprint=self.sprint,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )

        summary = randomize_accountability_partners(
            sprint=self.sprint,
            assigned_by=self.staff,
            rng=random.Random(1),
        )

        self.assertEqual(summary['assigned_pair_count'], 1)
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=self.alice,
                partner=self.bob,
                source=ACCOUNTABILITY_SOURCE_MANUAL,
            ).exists()
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=self.cara,
                partner=dana,
                source=ACCOUNTABILITY_SOURCE_RANDOM,
            ).exists()
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                member=dana,
                partner=self.cara,
                source=ACCOUNTABILITY_SOURCE_RANDOM,
            ).exists()
        )

    def test_randomize_makes_odd_group_a_three_person_pod(self):
        summary = randomize_accountability_partners(
            sprint=self.sprint,
            assigned_by=self.staff,
            rng=random.Random(1),
        )

        self.assertEqual(summary['assigned_pair_count'], 3)
        self.assertEqual(
            SprintAccountabilityPartner.objects.filter(sprint=self.sprint).count(),
            6,
        )
        for user in (self.alice, self.bob, self.cara):
            self.assertEqual(
                SprintAccountabilityPartner.objects.filter(
                    sprint=self.sprint,
                    member=user,
                ).count(),
                2,
            )

    def test_randomize_clears_previous_random_assignments_before_rerolling(self):
        dana = User.objects.create_user('dana@example.com', password='pw')
        SprintEnrollment.objects.create(sprint=self.sprint, user=dana)
        randomize_accountability_partners(
            sprint=self.sprint,
            assigned_by=self.staff,
            rng=random.Random(1),
        )

        self.assertEqual(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                source=ACCOUNTABILITY_SOURCE_RANDOM,
            ).count(),
            4,
        )
        summary = randomize_accountability_partners(
            sprint=self.sprint,
            assigned_by=self.staff,
            rng=random.Random(2),
        )

        self.assertEqual(summary['assigned_pair_count'], 2)
        self.assertEqual(summary['unassigned_count'], 0)
        self.assertEqual(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint,
                source=ACCOUNTABILITY_SOURCE_RANDOM,
            ).count(),
            4,
        )
        for user in (self.alice, self.bob, self.cara, dana):
            self.assertEqual(
                SprintAccountabilityPartner.objects.filter(
                    sprint=self.sprint,
                    member=user,
                    source=ACCOUNTABILITY_SOURCE_RANDOM,
                ).count(),
                1,
            )
