"""Tests for the persona + onboarding seed data migration (issue #801).

The seed migration runs against the test DB, so the rows already exist.
These assertions fail if the seed is removed, renamed, or produces the
wrong shape.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from questionnaires.models import Answer, Persona, Questionnaire, Response, ResponseQuestion

User = get_user_model()

# Expected base-question counts: common spine (10) + per-persona deltas
# (5 each) = 15; fallback = spine (10) + fallback deltas (6) = 16.
_SPINE = 10
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

    def test_revised_priya_copy_has_not_applicable_and_no_old_prompts(self):
        priya = Questionnaire.objects.get(slug='onboarding-priya')
        prompts = set(priya.questions.values_list('prompt', flat=True))
        self.assertIn(
            'What existing AI/ML project, workflow, or codebase could this plan build on?',
            prompts,
        )
        self.assertIn(
            'If you want to improve an existing project, what would make it '
            'more useful, reliable, or ready for real users?',
            prompts,
        )
        self.assertNotIn('Which existing project to build on + status?', prompts)
        self.assertNotIn('What makes it not production-grade?', prompts)
        readiness = priya.questions.get(
            prompt__startswith='If you want to improve an existing project',
        )
        self.assertTrue(
            readiness.options.filter(label='Not applicable').exists(),
        )

    def test_other_options_are_marked_for_free_text(self):
        generic = Questionnaire.objects.get(slug='onboarding-general')
        path = generic.questions.get(prompt='Which path best fits that goal?')
        other = path.options.get(label='Other')
        self.assertTrue(other.allows_free_text)

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

    def test_copy_update_is_idempotent_on_rerun(self):
        import importlib

        from django.apps import apps

        update_module = importlib.import_module(
            'questionnaires.migrations.0006_update_onboarding_questionnaire_copy_1099'
        )
        generic = Questionnaire.objects.get(slug='onboarding-general')
        before_count = generic.questions.count()
        update_module.update_questionnaires(apps, None)
        generic.refresh_from_db()
        self.assertEqual(generic.questions.count(), before_count)
        self.assertEqual(
            generic.questions.filter(
                prompt='Which path best fits that goal?',
                options__label='Other',
                options__allows_free_text=True,
            ).count(),
            1,
        )

    def test_copy_update_does_not_rewrite_submitted_snapshot(self):
        import importlib

        from django.apps import apps

        member = User.objects.create_user(email='snapshot@test.com', password='pw')
        generic = Questionnaire.objects.get(slug='onboarding-general')
        response = Response.objects.create(
            questionnaire=generic, respondent=member, status='submitted',
        )
        old_prompt = 'Which best describes that outcome?'
        rq = ResponseQuestion.objects.create(
            response=response,
            question_type='long_text',
            prompt=old_prompt,
            order=0,
        )
        Answer.objects.create(response=response, question=rq, text_value='Old answer')
        update_module = importlib.import_module(
            'questionnaires.migrations.0006_update_onboarding_questionnaire_copy_1099'
        )

        update_module.update_questionnaires(apps, None)

        rq.refresh_from_db()
        self.assertEqual(rq.prompt, old_prompt)
        answer = Answer.objects.get(response=response, question=rq)
        self.assertEqual(answer.text_value, 'Old answer')

    def test_copy_update_does_not_rewrite_answered_draft_snapshot(self):
        import importlib

        from django.apps import apps

        member = User.objects.create_user(email='draft-snapshot@test.com', password='pw')
        generic = Questionnaire.objects.get(slug='onboarding-general')
        response = Response.objects.create(
            questionnaire=generic, respondent=member, status='draft',
        )
        old_prompt = 'What usually makes it hard to stay consistent or finish?'
        rq = ResponseQuestion.objects.create(
            response=response,
            question_type='long_text',
            prompt=old_prompt,
            order=0,
        )
        Answer.objects.create(response=response, question=rq, text_value='Old draft')
        update_module = importlib.import_module(
            'questionnaires.migrations.0006_update_onboarding_questionnaire_copy_1099'
        )

        update_module.update_questionnaires(apps, None)

        rq.refresh_from_db()
        self.assertEqual(rq.prompt, old_prompt)
        answer = Answer.objects.get(response=response, question=rq)
        self.assertEqual(answer.text_value, 'Old draft')
