"""Tests for the persona + onboarding seed data migration (issue #801).

The seed migration runs against the test DB, so the rows already exist.
These assertions fail if the seed is removed, renamed, or produces the
wrong shape.
"""

from django.test import TestCase

from questionnaires.models import Persona, Questionnaire

# Expected base-question counts: common spine (11) + per-persona deltas
# (5 each) = 16; fallback = spine (11) + fallback deltas (6) = 17.
_SPINE = 11
_PERSONA_QUESTION_COUNT = _SPINE + 5
_FALLBACK_QUESTION_COUNT = _SPINE + 6

_EXPECTED = {
    'alex': 'The Engineer transitioning to AI',
    'priya': 'The Improver (working junior/mid AI/ML engineer)',
    'sam': 'The Technical Professional moving to AI (analyst/PM with coding)',
    'taylor': (
        'The Research-to-Engineering transitioner (researcher/DS/academic)'
    ),
}


class SeedPersonasTest(TestCase):
    def test_four_documented_personas_seeded(self):
        for slug, archetype in _EXPECTED.items():
            persona = Persona.objects.get(slug=slug)
            self.assertEqual(persona.archetype, archetype)
            self.assertTrue(persona.is_active)
            self.assertNotEqual(persona.description, '')

    def test_persona_order_is_alex_priya_sam_taylor(self):
        ordered_slugs = list(
            Persona.objects.filter(slug__in=_EXPECTED)
            .order_by('order')
            .values_list('slug', flat=True)
        )
        self.assertEqual(ordered_slugs, ['alex', 'priya', 'sam', 'taylor'])

    def test_each_persona_links_its_onboarding_questionnaire(self):
        for slug in _EXPECTED:
            persona = Persona.objects.get(slug=slug)
            self.assertIsNotNone(persona.default_questionnaire)
            self.assertEqual(persona.default_questionnaire.purpose, 'onboarding')
            self.assertEqual(persona.default_questionnaire.slug, f'onboarding-{slug}')
            self.assertEqual(
                persona.default_questionnaire.questions.count(),
                _PERSONA_QUESTION_COUNT,
            )

    def test_generic_fallback_onboarding_questionnaire_seeded(self):
        fallback = Questionnaire.objects.get(slug='onboarding-general')
        self.assertEqual(fallback.purpose, 'onboarding')
        self.assertEqual(fallback.questions.count(), _FALLBACK_QUESTION_COUNT)

    def test_choice_questions_have_options_and_scale_has_range(self):
        # The fallback set contains both scale questions and choice
        # questions, so assert the option / range shapes there.
        fallback = Questionnaire.objects.get(slug='onboarding-general')
        scale_q = fallback.questions.filter(question_type='scale').first()
        self.assertIsNotNone(scale_q)
        self.assertEqual(scale_q.scale_min, 1)
        self.assertEqual(scale_q.scale_max, 5)
        choice_q = fallback.questions.filter(question_type='single_choice').first()
        self.assertIsNotNone(choice_q)
        self.assertGreater(choice_q.options.count(), 0)

    def test_seed_is_idempotent_on_rerun(self):
        import importlib

        from django.apps import apps

        seed_module = importlib.import_module(
            'questionnaires.migrations.0003_seed_personas_and_onboarding'
        )
        before_personas = Persona.objects.count()
        before_questionnaires = Questionnaire.objects.count()
        seed_module.seed(apps, None)
        self.assertEqual(Persona.objects.count(), before_personas)
        self.assertEqual(Questionnaire.objects.count(), before_questionnaires)
