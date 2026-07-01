"""Unit tests for the onboarding routing helpers (issue #802).

The view-level routing is covered in ``accounts/tests/test_onboarding.py``;
these exercise the pure-Python seam directly.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from questionnaires.models import (
    Answer,
    AnswerOptionText,
    Persona,
    Questionnaire,
    Response,
    ResponseQuestion,
    ResponseQuestionOption,
)
from questionnaires.onboarding import (
    GENERIC_ONBOARDING_SLUG,
    SELF_ID_MULTIPLE,
    SELF_ID_NONE,
    flatten_response_answers,
    has_completed_onboarding,
    reroute_onboarding_response,
    resolve_target_questionnaire,
    self_identification_options,
)
from questionnaires.services import build_response_questions

User = get_user_model()

# A common-spine number question, shared (same prompt) across every
# persona questionnaire and the generic fallback.
WEEKLY_HOURS_PROMPT = (
    'How many hours per week can you realistically commit?'
)
# A common-spine single-choice question, also shared everywhere.
OUTCOME_PROMPT = 'Which path best fits that goal?'


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


class RerouteOnboardingResponseTest(TestCase):
    """The #822 persona-switch helper: re-route a draft and preserve spine."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='switch@test.com', password='pw')
        personas = list(
            Persona.objects
            .filter(is_active=True, default_questionnaire__isnull=False)
            .order_by('order', 'name')
        )
        # Two distinct persona questionnaires for the switch.
        cls.persona_a = personas[0]
        cls.persona_b = personas[1]
        cls.q_a = cls.persona_a.default_questionnaire
        cls.q_b = cls.persona_b.default_questionnaire
        cls.generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)

    def _start_draft(self, questionnaire):
        response = Response.objects.create(
            questionnaire=questionnaire, respondent=self.user, status='draft',
        )
        build_response_questions(response)
        return response

    def _rq(self, response, prompt):
        return response.response_questions.get(prompt=prompt)

    def _delta_prompts(self, questionnaire):
        """Prompts unique to ``questionnaire`` (its per-persona deltas)."""
        own = {q.prompt for q in questionnaire.questions.all()}
        generic = {q.prompt for q in self.generic.questions.all()}
        return own - generic

    def test_switch_repoints_questionnaire_and_shows_new_deltas(self):
        response = self._start_draft(self.q_a)
        reroute_onboarding_response(response, self.q_b)
        response.refresh_from_db()
        self.assertEqual(response.questionnaire_id, self.q_b.pk)
        new_prompts = {
            rq.prompt for rq in response.response_questions.all()
        }
        # Every base prompt of the new questionnaire is materialized.
        for q in self.q_b.questions.all():
            self.assertIn(q.prompt, new_prompts)
        # The old persona's delta prompts are gone.
        for prompt in self._delta_prompts(self.q_a) - self._delta_prompts(self.q_b):
            self.assertNotIn(prompt, new_prompts)

    def test_shared_number_answer_preserved_across_switch(self):
        response = self._start_draft(self.q_a)
        rq = self._rq(response, WEEKLY_HOURS_PROMPT)
        Answer.objects.create(response=response, question=rq, number_value=12)

        reroute_onboarding_response(response, self.q_b)

        new_rq = self._rq(response, WEEKLY_HOURS_PROMPT)
        answer = Answer.objects.get(response=response, question=new_rq)
        self.assertEqual(answer.number_value, 12)

    def test_shared_choice_answer_preserved_by_label(self):
        response = self._start_draft(self.q_a)
        rq = self._rq(response, OUTCOME_PROMPT)
        chosen = rq.options.first()
        answer = Answer.objects.create(response=response, question=rq)
        answer.selected_options.set([chosen])

        reroute_onboarding_response(response, self.q_b)

        new_rq = self._rq(response, OUTCOME_PROMPT)
        new_answer = Answer.objects.get(response=response, question=new_rq)
        labels = [o.label for o in new_answer.selected_options.all()]
        self.assertEqual(labels, [chosen.label])
        # The re-mapped option belongs to the NEW response-question's options.
        new_option_ids = {o.pk for o in new_rq.options.all()}
        self.assertTrue(
            {o.pk for o in new_answer.selected_options.all()} <= new_option_ids,
        )

    def test_old_choice_answer_preserved_by_prompt_and_label_alias(self):
        response = self._start_draft(self.q_a)
        rq = self._rq(response, OUTCOME_PROMPT)
        rq.prompt = 'Which best describes that outcome?'
        rq.save(update_fields=['prompt', 'updated_at'])
        chosen = rq.options.get(label='Improve or finish an existing project')
        chosen.label = 'Improve/finish existing'
        chosen.save(update_fields=['label', 'updated_at'])
        answer = Answer.objects.create(response=response, question=rq)
        answer.selected_options.set([chosen])

        reroute_onboarding_response(response, self.q_b)

        new_rq = self._rq(response, OUTCOME_PROMPT)
        new_answer = Answer.objects.get(response=response, question=new_rq)
        labels = [o.label for o in new_answer.selected_options.all()]
        self.assertEqual(labels, ['Improve or finish an existing project'])

    def test_delta_answer_dropped_when_absent_in_new_questionnaire(self):
        # Answer a delta question unique to persona A, then switch to B.
        only_in_a = self._delta_prompts(self.q_a) - self._delta_prompts(self.q_b)
        self.assertTrue(only_in_a, 'need a delta prompt unique to persona A')
        prompt = next(iter(only_in_a))
        response = self._start_draft(self.q_a)
        rq = self._rq(response, prompt)
        if rq.is_choice_type:
            answer = Answer.objects.create(response=response, question=rq)
            answer.selected_options.set([rq.options.first()])
        else:
            Answer.objects.create(
                response=response, question=rq, text_value='delta answer',
            )

        reroute_onboarding_response(response, self.q_b)

        response.refresh_from_db()
        # No response-question with that prompt remains, and no orphan answer.
        self.assertFalse(
            response.response_questions.filter(prompt=prompt).exists(),
        )
        for answer in response.answers.all():
            self.assertIn(
                answer.question.prompt,
                {rq.prompt for rq in response.response_questions.all()},
            )

    def test_switch_to_generic_routes_to_fallback_set(self):
        response = self._start_draft(self.q_a)
        reroute_onboarding_response(response, self.generic)
        response.refresh_from_db()
        self.assertEqual(response.questionnaire_id, self.generic.pk)

    def test_same_questionnaire_is_noop_keeps_questions(self):
        response = self._start_draft(self.q_a)
        before = set(
            response.response_questions.values_list('pk', flat=True),
        )
        reroute_onboarding_response(response, self.q_a)
        response.refresh_from_db()
        after = set(response.response_questions.values_list('pk', flat=True))
        self.assertEqual(before, after)

    def test_none_target_is_noop(self):
        response = self._start_draft(self.q_a)
        reroute_onboarding_response(response, None)
        response.refresh_from_db()
        self.assertEqual(response.questionnaire_id, self.q_a.pk)


class FlattenResponseAnswersTest(TestCase):
    """The shared CRM/API answer-flattening helper (issue #871).

    Asserts the per-type normalized value and the human-readable display
    string the CRM template renders, including the unanswered case.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='flat@test.com', password='pw')
        cls.questionnaire = Questionnaire.objects.get(
            slug=GENERIC_ONBOARDING_SLUG,
        )

    def _make_response(self, status='submitted'):
        return Response.objects.create(
            questionnaire=self.questionnaire,
            respondent=self.user,
            status=status,
        )

    @staticmethod
    def _add_q(response, *, qtype, prompt, order):
        return ResponseQuestion.objects.create(
            response=response, question_type=qtype, prompt=prompt, order=order,
        )

    def test_each_answer_type_normalizes_and_displays(self):
        response = self._make_response()
        rq_text = self._add_q(
            response, qtype='long_text', prompt='Goals?', order=0,
        )
        Answer.objects.create(
            response=response, question=rq_text, text_value='Switch careers',
        )
        rq_scale = self._add_q(
            response, qtype='scale', prompt='Confidence?', order=1,
        )
        Answer.objects.create(
            response=response, question=rq_scale, number_value=4,
        )
        rq_multi = self._add_q(
            response, qtype='multiple_choice', prompt='Areas?', order=2,
        )
        opt1 = ResponseQuestionOption.objects.create(
            response_question=rq_multi, label='RAG', order=0,
        )
        opt2 = ResponseQuestionOption.objects.create(
            response_question=rq_multi, label='Agents', order=1,
        )
        opt_other = ResponseQuestionOption.objects.create(
            response_question=rq_multi, label='Other',
            allows_free_text=True, order=2,
        )
        ans_multi = Answer.objects.create(response=response, question=rq_multi)
        ans_multi.selected_options.set([opt1, opt2, opt_other])
        AnswerOptionText.objects.create(
            answer=ans_multi,
            selected_option=opt_other,
            text_value='Custom area',
        )

        rows = flatten_response_answers(response)
        self.assertEqual([r['prompt'] for r in rows], ['Goals?', 'Confidence?', 'Areas?'])
        self.assertEqual(rows[0]['value'], 'Switch careers')
        self.assertEqual(rows[0]['display'], 'Switch careers')
        self.assertEqual(rows[1]['value'], 4)
        self.assertEqual(rows[1]['display'], '4')
        self.assertEqual(rows[2]['value'], ['RAG', 'Agents', 'Other'])
        self.assertEqual(rows[2]['display'], 'RAG, Agents, Other: Custom area')
        self.assertTrue(all(r['answered'] for r in rows))

    def test_unanswered_question_is_present_and_marked_not_answered(self):
        response = self._make_response(status='draft')
        self._add_q(response, qtype='text', prompt='Anything else?', order=0)

        rows = flatten_response_answers(response)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['prompt'], 'Anything else?')
        self.assertIsNone(rows[0]['value'])
        self.assertEqual(rows[0]['display'], '')
        self.assertFalse(rows[0]['answered'])
