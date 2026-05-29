"""Model tests for ``questionnaires.Persona`` (issue #801)."""

from django.test import TestCase

from questionnaires.models import Persona, Questionnaire


class PersonaModelTest(TestCase):
    def test_slug_derived_from_name_when_blank(self):
        persona = Persona.objects.create(
            name='Jordan Lee', archetype='The Career-switcher',
        )
        self.assertEqual(persona.slug, 'jordan-lee')

    def test_explicit_slug_is_preserved(self):
        persona = Persona.objects.create(
            name='Jordan', archetype='The Career-switcher', slug='jl',
        )
        self.assertEqual(persona.slug, 'jl')

    def test_str_joins_name_and_archetype(self):
        persona = Persona(name='Alex', archetype='The Engineer transitioning to AI')
        self.assertEqual(str(persona), 'Alex — The Engineer transitioning to AI')

    def test_display_label_joins_name_and_archetype(self):
        persona = Persona(name='Priya', archetype='The Improver')
        self.assertEqual(persona.display_label, 'Priya — The Improver')

    def test_deleting_questionnaire_nulls_default_not_persona(self):
        questionnaire = Questionnaire.objects.create(
            title='Onboarding Alex', slug='onb-alex-model-test', purpose='onboarding',
        )
        persona = Persona.objects.create(
            name='Alex', archetype='The Engineer', slug='alex-model-test',
            default_questionnaire=questionnaire,
        )
        questionnaire.delete()
        persona.refresh_from_db()
        self.assertIsNone(persona.default_questionnaire)
        self.assertTrue(Persona.objects.filter(pk=persona.pk).exists())
