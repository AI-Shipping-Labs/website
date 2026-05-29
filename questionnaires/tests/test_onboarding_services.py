"""Unit tests for the onboarding routing helpers (issue #802).

The view-level routing is covered in ``accounts/tests/test_onboarding.py``;
these exercise the pure-Python seam directly.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from questionnaires.models import Persona, Questionnaire, Response
from questionnaires.onboarding import (
    GENERIC_ONBOARDING_SLUG,
    SELF_ID_MULTIPLE,
    SELF_ID_NONE,
    has_completed_onboarding,
    resolve_target_questionnaire,
    self_identification_options,
)

User = get_user_model()


class SelfIdentificationOptionsTest(TestCase):
    def test_options_label_with_archetype_not_persona_name(self):
        options = self_identification_options()
        labels = [o['label'] for o in options]
        for persona in Persona.objects.filter(
            is_active=True, default_questionnaire__isnull=False,
        ):
            self.assertIn(persona.archetype, labels)
            self.assertNotIn(persona.name, labels)

    def test_inactive_persona_excluded(self):
        inactive = Persona.objects.filter(is_active=True).first()
        inactive.is_active = False
        inactive.save()
        values = [o['value'] for o in self_identification_options()]
        self.assertNotIn(str(inactive.pk), values)

    def test_persona_without_questionnaire_excluded_from_options(self):
        orphan = Persona.objects.create(
            name='Orphan', archetype='No questionnaire here',
            slug='orphan-x', is_active=True, default_questionnaire=None,
        )
        values = [o['value'] for o in self_identification_options()]
        self.assertNotIn(str(orphan.pk), values)

    def test_includes_two_generic_options(self):
        values = [o['value'] for o in self_identification_options()]
        self.assertIn(SELF_ID_NONE, values)
        self.assertIn(SELF_ID_MULTIPLE, values)


class ResolveTargetQuestionnaireTest(TestCase):
    def test_none_and_multiple_resolve_to_generic(self):
        generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)
        self.assertEqual(resolve_target_questionnaire(SELF_ID_NONE), generic)
        self.assertEqual(resolve_target_questionnaire(SELF_ID_MULTIPLE), generic)

    def test_persona_resolves_to_its_questionnaire(self):
        persona = Persona.objects.filter(
            default_questionnaire__isnull=False,
        ).first()
        self.assertEqual(
            resolve_target_questionnaire(str(persona.pk)),
            persona.default_questionnaire,
        )

    def test_unknown_selection_falls_back_to_generic(self):
        generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)
        self.assertEqual(resolve_target_questionnaire('not-a-real-value'), generic)


class HasCompletedOnboardingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='hc@test.com', password='pw')
        cls.generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)

    def test_false_without_response(self):
        self.assertFalse(has_completed_onboarding(self.user))

    def test_false_with_draft_only(self):
        Response.objects.create(
            questionnaire=self.generic, respondent=self.user, status='draft',
        )
        self.assertFalse(has_completed_onboarding(self.user))

    def test_true_with_submitted_response(self):
        Response.objects.create(
            questionnaire=self.generic, respondent=self.user, status='submitted',
        )
        self.assertTrue(has_completed_onboarding(self.user))
