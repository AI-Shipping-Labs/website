"""Tests for the additive ``CRMRecord.persona_ref`` FK (issue #801).

The structured FK is additive: the free-text ``persona`` field stays the
source of truth (and the CRM list search still filters on it), and
deleting a persona must never delete a CRM record.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from crm.models import CRMRecord
from questionnaires.models import Persona

User = get_user_model()


class CRMRecordPersonaRefTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.persona = Persona.objects.create(
            name='SamTest', archetype='The Tech Pro', slug='sam-crm-test',
        )

    def test_free_text_and_structured_persona_coexist(self):
        record = CRMRecord.objects.create(
            user=self.member,
            persona='Sam — free text',
            persona_ref=self.persona,
        )
        record.refresh_from_db()
        self.assertEqual(record.persona, 'Sam — free text')
        self.assertEqual(record.persona_ref, self.persona)

    def test_deleting_persona_nulls_ref_keeps_record_and_free_text(self):
        record = CRMRecord.objects.create(
            user=self.member,
            persona='Sam — free text',
            persona_ref=self.persona,
        )
        self.persona.delete()
        record.refresh_from_db()
        self.assertIsNone(record.persona_ref)
        self.assertEqual(record.persona, 'Sam — free text')
        self.assertTrue(CRMRecord.objects.filter(pk=record.pk).exists())
