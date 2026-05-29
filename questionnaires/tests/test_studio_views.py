"""Studio CRUD + response-viewing tests for questionnaires (issue #800)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from questionnaires.models import (
    Answer,
    Question,
    Questionnaire,
    QuestionOption,
    Response,
    ResponseQuestion,
)
from tests.fixtures import StaffUserMixin

User = get_user_model()


@tag('core')
class QuestionnaireStudioAccessTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw', is_staff=False,
        )
        cls.questionnaire = Questionnaire.objects.create(title='Intake')

    def test_anonymous_redirected_to_login(self):
        response = self.client.get('/studio/questionnaires/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertIn('next=/studio/questionnaires/', response.url)

    def test_non_staff_forbidden_and_no_data_leak(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/studio/questionnaires/')
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'Intake', status_code=403)

    def test_staff_can_view_list(self):
        self.client.login(**self.staff_credentials)
        response = self.client.get('/studio/questionnaires/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Intake')


class QuestionnaireCrudTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_list_shows_counts_and_purpose(self):
        q = Questionnaire.objects.create(title='Counts QC', purpose='feedback')
        Question.objects.create(questionnaire=q, question_type='text', prompt='A')
        member = User.objects.create_user(email='r@test.com', password='pw')
        Response.objects.create(questionnaire=q, respondent=member)
        response = self.client.get('/studio/questionnaires/')
        self.assertContains(response, 'Counts QC')
        self.assertContains(response, 'Feedback')

    def test_empty_state_shown_when_no_questionnaires(self):
        response = self.client.get('/studio/questionnaires/')
        self.assertContains(response, 'studio-empty-state-fresh')

    def test_create_redirects_to_detail_with_message(self):
        response = self.client.post('/studio/questionnaires/new', {
            'title': 'Onboarding Intake',
            'purpose': 'onboarding',
            'is_active': 'on',
        })
        created = Questionnaire.objects.get(title='Onboarding Intake')
        self.assertRedirects(response, f'/studio/questionnaires/{created.pk}/')
        self.assertEqual(created.purpose, 'onboarding')
        self.assertEqual(created.slug, 'onboarding-intake')

        follow = self.client.get(f'/studio/questionnaires/{created.pk}/')
        self.assertContains(follow, 'created')

    def test_create_missing_title_returns_400_no_row(self):
        response = self.client.post('/studio/questionnaires/new', {
            'title': '',
            'purpose': 'general',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Title is required.', status_code=400)
        self.assertFalse(Questionnaire.objects.exists())

    def test_edit_updates_metadata(self):
        q = Questionnaire.objects.create(title='Draft Feedback', purpose='general')
        response = self.client.post(f'/studio/questionnaires/{q.pk}/edit', {
            'title': 'May Sprint Feedback',
            'slug': q.slug,
            'purpose': 'feedback',
            'is_active': 'on',
        })
        self.assertRedirects(response, f'/studio/questionnaires/{q.pk}/')
        q.refresh_from_db()
        self.assertEqual(q.title, 'May Sprint Feedback')
        self.assertEqual(q.purpose, 'feedback')

    def test_detail_lists_questions_and_links_to_responses(self):
        q = Questionnaire.objects.create(title='Detail Q')
        Question.objects.create(
            questionnaire=q, question_type='long_text', prompt='Your goals?',
        )
        response = self.client.get(f'/studio/questionnaires/{q.pk}/')
        self.assertContains(response, 'Your goals?')
        self.assertContains(response, f'/studio/questionnaires/{q.pk}/responses/')


class QuestionCrudTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.questionnaire = Questionnaire.objects.create(title='Q')

    def test_add_text_question(self):
        response = self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/new', {
                'question_type': 'long_text',
                'prompt': 'What do you hope to achieve?',
                'is_required': 'on',
                'order': '0',
            },
        )
        self.assertRedirects(response, f'/studio/questionnaires/{self.questionnaire.pk}/')
        question = self.questionnaire.questions.get()
        self.assertEqual(question.question_type, 'long_text')
        self.assertTrue(question.is_required)

    def test_add_number_question_without_options(self):
        response = self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/new', {
                'question_type': 'number',
                'prompt': 'How many hours per week?',
                'order': '0',
            },
        )
        self.assertRedirects(response, f'/studio/questionnaires/{self.questionnaire.pk}/')
        question = self.questionnaire.questions.get()
        self.assertEqual(question.options.count(), 0)

    def test_add_multiple_choice_question_with_options(self):
        response = self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/new', {
                'question_type': 'multiple_choice',
                'prompt': 'Which areas?',
                'order': '0',
                'options': 'RAG\nAgents\nDeployment\nEvaluation',
            },
        )
        self.assertRedirects(response, f'/studio/questionnaires/{self.questionnaire.pk}/')
        question = self.questionnaire.questions.get()
        labels = list(question.options.values_list('label', flat=True))
        self.assertEqual(labels, ['RAG', 'Agents', 'Deployment', 'Evaluation'])

    def test_choice_question_without_options_returns_400(self):
        response = self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/new', {
                'question_type': 'single_choice',
                'prompt': 'Pick one',
                'order': '0',
                'options': '',
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.questionnaire.questions.exists())

    def test_edit_question_replaces_options(self):
        question = Question.objects.create(
            questionnaire=self.questionnaire, question_type='multiple_choice',
            prompt='Which?',
        )
        QuestionOption.objects.create(question=question, label='RAG', order=0)
        QuestionOption.objects.create(question=question, label='Deployment', order=1)

        self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/{question.pk}/edit', {
                'question_type': 'multiple_choice',
                'prompt': 'Which?',
                'order': '0',
                'options': 'RAG\nAgents',
            },
        )
        labels = list(question.options.values_list('label', flat=True))
        self.assertEqual(labels, ['RAG', 'Agents'])
        self.assertNotIn('Deployment', labels)

    def test_delete_question_post_only(self):
        question = Question.objects.create(
            questionnaire=self.questionnaire, question_type='text', prompt='A',
        )
        get_response = self.client.get(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/{question.pk}/delete',
        )
        self.assertEqual(get_response.status_code, 405)

        post_response = self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/{question.pk}/delete',
        )
        self.assertRedirects(post_response, f'/studio/questionnaires/{self.questionnaire.pk}/')
        self.assertFalse(self.questionnaire.questions.exists())


class ResponseViewingTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.questionnaire = Questionnaire.objects.create(title='Feedback')
        cls.member = User.objects.create_user(email='member@test.com', password='pw')
        cls.response = Response.objects.create(
            questionnaire=cls.questionnaire, respondent=cls.member,
        )
        cls.response.mark_submitted()

        # Two base questions back the standard response questions so only
        # the third (no source_question) is flagged custom.
        base_well = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='long_text',
            prompt='What went well?', order=0,
        )
        base_else = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='text',
            prompt='Anything else?', order=1,
        )
        cls.answered_rq = ResponseQuestion.objects.create(
            response=cls.response, source_question=base_well,
            question_type='long_text', prompt='What went well?', order=0,
        )
        cls.blank_rq = ResponseQuestion.objects.create(
            response=cls.response, source_question=base_else,
            question_type='text', prompt='Anything else?', order=1,
        )
        cls.custom_rq = ResponseQuestion.objects.create(
            response=cls.response, source_question=None, question_type='text',
            prompt='Custom for this member', order=2,
        )
        Answer.objects.create(
            response=cls.response, question=cls.answered_rq, text_value='The pairing',
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_responses_list_shows_respondent_and_status(self):
        response = self.client.get(
            f'/studio/questionnaires/{self.questionnaire.pk}/responses/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'member@test.com')
        self.assertContains(response, 'Submitted')

    def test_response_detail_renders_answer_and_blank_marker(self):
        response = self.client.get(
            f'/studio/questionnaires/{self.questionnaire.pk}/responses/{self.response.pk}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'What went well?')
        self.assertContains(response, 'The pairing')
        # The unanswered question is shown with an explicit blank marker.
        self.assertContains(response, 'Anything else?')
        self.assertContains(response, 'No answer')

    def test_response_detail_flags_custom_question(self):
        response = self.client.get(
            f'/studio/questionnaires/{self.questionnaire.pk}/responses/{self.response.pk}/',
        )
        self.assertContains(response, 'Custom for this member')
        self.assertContains(response, 'response-detail-custom-flag')

    def test_responses_anonymous_redirected(self):
        self.client.logout()
        path = f'/studio/questionnaires/{self.questionnaire.pk}/responses/'
        response = self.client.get(path)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
