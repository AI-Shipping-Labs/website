"""Tests for the Django glue of the AI onboarding flow (issue #804).

Covers the ORM-touching layer (:mod:`questionnaires.services_onboarding_ai`):
the persona catalog excludes internal names, completion writes the SAME
#800 ``Response`` / ``Answer`` artifacts as #802 (no side table), the
blend/other signal routes to the generic questionnaire, and the internal
``persona_signal`` is stored Studio-side. The LLM is mocked at the
boundary -- CI never makes a live call.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from integrations.services.llm import LLMResult
from questionnaires.models import (
    Answer,
    OnboardingConversation,
    Response,
)
from questionnaires.onboarding import GENERIC_ONBOARDING_SLUG
from questionnaires.services_onboarding_ai import (
    build_persona_catalog,
    get_or_create_ai_onboarding_response,
    run_member_turn,
)
from questionnaires.tests.test_onboarding_ai_core import VALID_EXTRACTION

User = get_user_model()

PERSONA_NAMES = ['Alex', 'Priya', 'Sam', 'Taylor']


@tag('core')
class PersonaCatalogTest(TestCase):
    def test_catalog_excludes_internal_persona_name(self):
        catalog = build_persona_catalog()
        self.assertTrue(catalog)
        serialized = '\n'.join(
            f'{p.signal} {p.archetype} {p.description}' for p in catalog
        )
        for name in PERSONA_NAMES:
            self.assertNotIn(name, serialized)

    def test_catalog_signal_matches_persona_slug(self):
        catalog = build_persona_catalog()
        signals = {p.signal for p in catalog}
        self.assertTrue(signals.issubset({'alex', 'priya', 'sam', 'taylor'}))
        # Every catalog persona carries its question spine.
        self.assertTrue(all(p.questions for p in catalog))


@tag('core')
class FinalizeWritesStandardArtifactsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ai-finalize@test.com', password='pw',
        )

    def _complete_chat(self, extraction):
        response, conversation = get_or_create_ai_onboarding_response(
            self.member,
        )
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='done',
                tool_input=dict(extraction),
                tool_name='record_onboarding',
            ),
        ):
            run_member_turn(conversation, 'here are my answers')
        return response, conversation

    def test_completion_writes_response_and_answers(self):
        response, conversation = self._complete_chat(VALID_EXTRACTION)
        response.refresh_from_db()
        self.assertEqual(response.status, 'submitted')
        self.assertIsNotNone(response.submitted_at)
        # Materialized questions + at least the primary-goal answer.
        self.assertTrue(response.response_questions.exists())
        primary = response.answers.filter(
            text_value='Ship a RAG chatbot for my docs',
        )
        self.assertTrue(primary.exists())

    def test_only_one_onboarding_response_per_member(self):
        self._complete_chat(VALID_EXTRACTION)
        count = Response.objects.filter(
            respondent=self.member,
            questionnaire__purpose='onboarding',
        ).count()
        self.assertEqual(count, 1)

    def test_alex_signal_routes_to_alex_questionnaire(self):
        response, _ = self._complete_chat(VALID_EXTRACTION)
        response.refresh_from_db()
        self.assertEqual(response.questionnaire.slug, 'onboarding-alex')

    def test_blend_signal_routes_to_generic_questionnaire(self):
        blend = dict(VALID_EXTRACTION, persona_signal='blend')
        response, _ = self._complete_chat(blend)
        response.refresh_from_db()
        self.assertEqual(response.questionnaire.slug, GENERIC_ONBOARDING_SLUG)

    def test_persona_signal_stored_on_conversation(self):
        _, conversation = self._complete_chat(VALID_EXTRACTION)
        conversation.refresh_from_db()
        self.assertEqual(conversation.persona_signal, 'alex')

    def test_no_side_answer_table_used(self):
        response, _ = self._complete_chat(VALID_EXTRACTION)
        # Every answer is a #800 Answer row tied to a ResponseQuestion.
        for answer in response.answers.all():
            self.assertIsNotNone(answer.question_id)
        self.assertTrue(
            Answer.objects.filter(response=response).exists(),
        )


@tag('core')
class CompletionRepointsToInferredPersonaTest(TestCase):
    """Issue #823: completion still repoints to the inferred persona.

    The interview is now archetype-aware DURING the chat, but the
    completion CONTRACT is unchanged: a recognised ``persona_signal``
    repoints the ``Response`` to that persona's default questionnaire and
    the extracted answers land on its materialized ``ResponseQuestion``
    rows; ``blend``/``other`` falls back to the generic questionnaire.
    """

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ai-repoint@test.com', password='pw',
        )

    def _complete_with_signal(self, persona_signal):
        response, conversation = get_or_create_ai_onboarding_response(
            self.member,
        )
        extraction = dict(VALID_EXTRACTION, persona_signal=persona_signal)
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='done',
                tool_input=extraction,
                tool_name='record_onboarding',
            ),
        ):
            run_member_turn(conversation, 'here are my answers')
        response.refresh_from_db()
        return response

    def test_taylor_signal_repoints_response_and_lands_answers(self):
        response = self._complete_with_signal('taylor')
        # Repointed to the Taylor persona questionnaire.
        self.assertEqual(response.questionnaire.slug, 'onboarding-taylor')
        # The extracted primary-goal spine answer lands on a ResponseQuestion
        # of the Taylor questionnaire.
        primary_prompt = (
            'What would you like to have achieved 6 to 8 weeks from now?'
        )
        rq = response.response_questions.filter(prompt=primary_prompt).first()
        self.assertIsNotNone(rq)
        answer = response.answers.filter(question=rq).first()
        self.assertIsNotNone(answer)
        self.assertEqual(answer.text_value, VALID_EXTRACTION['primary_goal'])

    def test_blend_signal_falls_back_to_generic(self):
        response = self._complete_with_signal('blend')
        self.assertEqual(response.questionnaire.slug, GENERIC_ONBOARDING_SLUG)
        # The answer still lands on the generic questionnaire's spine.
        self.assertTrue(
            response.answers.filter(
                text_value=VALID_EXTRACTION['primary_goal'],
            ).exists(),
        )


@tag('core')
class ResumeConversationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ai-resume@test.com', password='pw',
        )

    def test_transcript_persisted_across_turns(self):
        response, conversation = get_or_create_ai_onboarding_response(
            self.member,
        )
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='And what blocks you?'),
        ):
            run_member_turn(conversation, 'I want to ship a RAG app')
        conversation.refresh_from_db()
        roles = [t['role'] for t in conversation.transcript]
        self.assertEqual(roles, ['user', 'assistant'])
        self.assertEqual(
            conversation.transcript[0]['content'], 'I want to ship a RAG app',
        )

    def test_get_or_create_reuses_existing_response(self):
        r1, _ = get_or_create_ai_onboarding_response(self.member)
        r2, _ = get_or_create_ai_onboarding_response(self.member)
        self.assertEqual(r1.pk, r2.pk)
        self.assertEqual(
            OnboardingConversation.objects.filter(response=r1).count(), 1,
        )
