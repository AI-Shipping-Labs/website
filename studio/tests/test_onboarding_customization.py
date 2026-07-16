"""Studio per-member onboarding customization + persona assignment (#802).

Staff can add / edit / remove a single member's ``ResponseQuestion`` rows
without touching the shared base ``Question`` rows or any other member's
response, and can assign a structured ``Persona`` via the CRM control.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.models import CRMRecord
from questionnaires.models import (
    Answer,
    AnswerOptionText,
    Persona,
    Question,
    Questionnaire,
    Response,
    ResponseQuestion,
    ResponseQuestionOption,
)
from questionnaires.onboarding import GENERIC_ONBOARDING_SLUG
from questionnaires.services import build_response_questions

User = get_user_model()


class ResponseQuestionCustomizationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='m1@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='m2@test.com', password='pw',
        )
        cls.questionnaire = Questionnaire.objects.get(
            slug=GENERIC_ONBOARDING_SLUG,
        )
        cls.response = Response.objects.create(
            questionnaire=cls.questionnaire, respondent=cls.member,
        )
        build_response_questions(cls.response)
        cls.other_response = Response.objects.create(
            questionnaire=cls.questionnaire, respondent=cls.other,
        )
        build_response_questions(cls.other_response)

    def setUp(self):
        self.client.force_login(self.staff)

    def _detail_url(self):
        return reverse('studio_questionnaire_response_detail', kwargs={
            'questionnaire_id': self.questionnaire.pk,
            'response_id': self.response.pk,
        })

    def test_add_one_off_custom_question(self):
        before = self.response.response_questions.count()
        resp = self.client.post(
            reverse('studio_response_question_create', kwargs={
                'questionnaire_id': self.questionnaire.pk,
                'response_id': self.response.pk,
            }),
            {
                'question_type': 'text',
                'prompt': 'A one-off just for this member',
                'order': '99',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self.response.response_questions.count(), before + 1,
        )
        rq = self.response.response_questions.get(
            prompt='A one-off just for this member',
        )
        self.assertIsNone(rq.source_question_id)
        # Base questionnaire is unchanged.
        self.assertFalse(
            Question.objects.filter(
                questionnaire=self.questionnaire,
                prompt='A one-off just for this member',
            ).exists(),
        )
        # Other member's response is unaffected.
        self.assertFalse(
            self.other_response.response_questions.filter(
                prompt='A one-off just for this member',
            ).exists(),
        )

    def test_add_choice_question_with_options(self):
        self.client.post(
            reverse('studio_response_question_create', kwargs={
                'questionnaire_id': self.questionnaire.pk,
                'response_id': self.response.pk,
            }),
            {
                'question_type': 'single_choice',
                'prompt': 'Pick one custom',
                'options': 'Red\nGreen [free text]\nBlue',
                'order': '50',
            },
        )
        rq = self.response.response_questions.get(prompt='Pick one custom')
        self.assertEqual(list(rq.options.values_list(
            'label', 'allows_free_text', 'order',
        )), [
            ('Red', False, 0),
            ('Green', True, 1),
            ('Blue', False, 2),
        ])
        self.assertEqual(
            set(rq.options.values_list('response_question_id', flat=True)),
            {rq.pk},
        )

    def test_edit_choice_question_preserves_answered_option_identity(self):
        rq = ResponseQuestion.objects.create(
            response=self.response,
            question_type='multiple_choice',
            prompt='Tools?',
            order=50,
        )
        retained = ResponseQuestionOption.objects.create(
            response_question=rq,
            label='Other',
            allows_free_text=True,
            order=0,
        )
        removed = ResponseQuestionOption.objects.create(
            response_question=rq,
            label='Old',
            order=1,
        )
        answer = Answer.objects.create(response=self.response, question=rq)
        answer.selected_options.add(retained)
        option_text = AnswerOptionText.objects.create(
            answer=answer,
            selected_option=retained,
            text_value='My existing answer',
        )

        self.client.post(
            reverse('studio_response_question_edit', kwargs={
                'questionnaire_id': self.questionnaire.pk,
                'response_id': self.response.pk,
                'rq_id': rq.pk,
            }),
            {
                'question_type': 'multiple_choice',
                'prompt': 'Tools?',
                'order': '50',
                'options': 'New\nOther [free text]',
            },
        )

        retained.refresh_from_db()
        option_text.refresh_from_db()
        self.assertEqual(retained.order, 1)
        self.assertTrue(retained.allows_free_text)
        self.assertEqual(option_text.selected_option_id, retained.pk)
        self.assertEqual(option_text.text_value, 'My existing answer')
        self.assertEqual(
            list(answer.selected_options.values_list('pk', flat=True)),
            [retained.pk],
        )
        self.assertFalse(ResponseQuestionOption.objects.filter(pk=removed.pk).exists())
        self.assertEqual(list(rq.options.values_list('label', 'order')), [
            ('New', 0),
            ('Other', 1),
        ])

    def test_edit_question_does_not_mutate_base_or_other_member(self):
        rq = self.response.response_questions.filter(
            source_question__isnull=False,
        ).first()
        base_question = rq.source_question
        original_base_prompt = base_question.prompt
        # The other member has the same base question materialized.
        other_rq = self.other_response.response_questions.get(
            source_question=base_question,
        )
        original_other_prompt = other_rq.prompt

        self.client.post(
            reverse('studio_response_question_edit', kwargs={
                'questionnaire_id': self.questionnaire.pk,
                'response_id': self.response.pk,
                'rq_id': rq.pk,
            }),
            {
                'question_type': rq.question_type,
                'prompt': 'Edited prompt for member 1 only',
                'order': str(rq.order),
                'options': '\n'.join(o.label for o in rq.options.all()),
            },
        )
        rq.refresh_from_db()
        base_question.refresh_from_db()
        other_rq.refresh_from_db()
        self.assertEqual(rq.prompt, 'Edited prompt for member 1 only')
        self.assertEqual(base_question.prompt, original_base_prompt)
        self.assertEqual(other_rq.prompt, original_other_prompt)

    def test_remove_question_deletes_only_this_members_row(self):
        rq = self.response.response_questions.filter(
            source_question__isnull=False,
        ).first()
        base_question = rq.source_question
        rq_id = rq.pk
        self.client.post(
            reverse('studio_response_question_delete', kwargs={
                'questionnaire_id': self.questionnaire.pk,
                'response_id': self.response.pk,
                'rq_id': rq.pk,
            }),
        )
        self.assertFalse(ResponseQuestion.objects.filter(pk=rq_id).exists())
        # Base question still present; other member keeps the question.
        self.assertTrue(Question.objects.filter(pk=base_question.pk).exists())
        self.assertTrue(
            self.other_response.response_questions.filter(
                source_question=base_question,
            ).exists(),
        )

    def test_detail_page_has_customization_controls(self):
        resp = self.client.get(self._detail_url())
        self.assertContains(resp, 'data-testid="response-detail-add-question"')
        self.assertContains(resp, 'data-testid="response-detail-edit-question"')
        self.assertContains(resp, 'data-testid="response-detail-remove-question"')


class ResponseQuestionAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='main@test.com', password='pw',
        )
        cls.questionnaire = Questionnaire.objects.get(
            slug=GENERIC_ONBOARDING_SLUG,
        )
        cls.response = Response.objects.create(
            questionnaire=cls.questionnaire,
            respondent=User.objects.create_user(
                email='resp-owner@test.com', password='pw',
            ),
        )
        build_response_questions(cls.response)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(
            reverse('studio_response_question_create', kwargs={
                'questionnaire_id': self.questionnaire.pk,
                'response_id': self.response.pk,
            }),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_non_staff_gets_403_and_no_mutation(self):
        self.client.force_login(self.member)
        before = self.response.response_questions.count()
        resp = self.client.post(
            reverse('studio_response_question_create', kwargs={
                'questionnaire_id': self.questionnaire.pk,
                'response_id': self.response.pk,
            }),
            {'question_type': 'text', 'prompt': 'sneaky', 'order': '0'},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.response.response_questions.count(), before)


class CrmPersonaAssignmentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff2@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='crmmember@test.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(user=cls.member)
        cls.persona = Persona.objects.filter(is_active=True).first()

    def setUp(self):
        self.client.force_login(self.staff)

    def test_detail_shows_persona_dropdown_with_display_label(self):
        resp = self.client.get(
            reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk}),
        )
        self.assertContains(resp, 'data-testid="crm-persona-ref-select"')
        self.assertContains(resp, self.persona.display_label)

    def test_assign_persona_ref_saves(self):
        self.client.post(
            reverse('studio_crm_edit', kwargs={'crm_id': self.record.pk}),
            {'persona': '', 'summary': '', 'next_steps': '',
             'persona_ref': str(self.persona.pk)},
        )
        self.record.refresh_from_db()
        self.assertEqual(self.record.persona_ref_id, self.persona.pk)

    def test_clear_persona_ref(self):
        self.record.persona_ref = self.persona
        self.record.save()
        self.client.post(
            reverse('studio_crm_edit', kwargs={'crm_id': self.record.pk}),
            {'persona': '', 'summary': '', 'next_steps': '', 'persona_ref': ''},
        )
        self.record.refresh_from_db()
        self.assertIsNone(self.record.persona_ref_id)
