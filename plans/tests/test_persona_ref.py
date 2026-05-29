"""Tests for the additive ``Plan.assigned_persona_ref`` FK (issue #801).

The structured FK is additive: the free-text ``assigned_persona`` field
stays the source of truth, and deleting a persona must never delete a
plan.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import Plan, Sprint
from questionnaires.models import Persona

User = get_user_model()


class PlanPersonaRefTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.persona = Persona.objects.create(
            name='SamTest', archetype='The Tech Pro', slug='sam-plan-test',
        )

    def test_free_text_and_structured_persona_coexist(self):
        plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
            assigned_persona='Sam — free text',
            assigned_persona_ref=self.persona,
        )
        plan.refresh_from_db()
        self.assertEqual(plan.assigned_persona, 'Sam — free text')
        self.assertEqual(plan.assigned_persona_ref, self.persona)

    def test_deleting_persona_nulls_ref_keeps_plan_and_free_text(self):
        plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
            assigned_persona='Sam — free text',
            assigned_persona_ref=self.persona,
        )
        self.persona.delete()
        plan.refresh_from_db()
        self.assertIsNone(plan.assigned_persona_ref)
        self.assertEqual(plan.assigned_persona, 'Sam — free text')
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())
