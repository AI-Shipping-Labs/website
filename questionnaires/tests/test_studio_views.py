"""Studio CRUD + response-viewing tests for questionnaires (issue #800)."""

import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.http import QueryDict
from django.test import Client, TestCase, tag
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from community.models import CommunityAuditLog
from crm.models import CRMRecord
from questionnaires.models import (
    Answer,
    Persona,
    Question,
    Questionnaire,
    QuestionOption,
    Response,
    ResponseQuestion,
    ResponseQuestionOption,
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

    def test_annotated_count_query_is_bounded_as_responses_scale(self):
        q = Questionnaire.objects.create(title='Bounded counts 1289')
        member = User.objects.create_user(email='bounded-count-0@test.com')
        Response.objects.create(questionnaire=q, respondent=member)
        with CaptureQueriesContext(connection) as baseline_queries:
            self.client.get('/studio/questionnaires/?q=Bounded+counts+1289')
        for index in range(1, 11):
            Response.objects.create(
                questionnaire=q,
                respondent=User.objects.create_user(
                    email=f'bounded-count-{index}@test.com',
                ),
            )
        with CaptureQueriesContext(connection) as scaled_queries:
            self.client.get('/studio/questionnaires/?q=Bounded+counts+1289')
        self.assertLessEqual(len(scaled_queries), len(baseline_queries))

    def test_empty_state_shown_when_no_questionnaires(self):
        # The #801 seed migration creates onboarding questionnaires; clear
        # them so the fresh empty state is exercised.
        Questionnaire.objects.all().delete()
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
        before = Questionnaire.objects.count()
        response = self.client.post('/studio/questionnaires/new', {
            'title': '',
            'purpose': 'general',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Title is required.', status_code=400)
        self.assertEqual(Questionnaire.objects.count(), before)

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
                'options': 'RAG\nAgents [free text]\nDeployment\nEvaluation',
            },
        )
        self.assertRedirects(response, f'/studio/questionnaires/{self.questionnaire.pk}/')
        question = self.questionnaire.questions.get()
        self.assertEqual(
            list(question.options.values_list(
                'label', 'allows_free_text', 'order',
            )),
            [
                ('RAG', False, 0),
                ('Agents', True, 1),
                ('Deployment', False, 2),
                ('Evaluation', False, 3),
            ],
        )
        self.assertEqual(
            set(question.options.values_list('question_id', flat=True)),
            {question.pk},
        )

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

        self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/{question.pk}/edit', {
                'question_type': 'text',
                'prompt': 'Which?',
                'order': '0',
                'options': 'Ignored',
            },
        )
        self.assertEqual(question.options.count(), 0)

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

    def test_profile_link_and_optional_descriptive_crm_link_on_list_and_detail(self):
        record = CRMRecord.objects.create(user=self.member)
        paths = [
            f'/studio/questionnaires/{self.questionnaire.pk}/responses/',
            (
                f'/studio/questionnaires/{self.questionnaire.pk}/responses/'
                f'{self.response.pk}/'
            ),
        ]
        for path in paths:
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertContains(page, f'/studio/users/{self.member.pk}/')
                self.assertContains(page, f'/studio/crm/{record.pk}/')
                self.assertContains(page, 'Open CRM record for member@test.com')

        record.delete()
        for path in paths:
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertContains(page, f'/studio/users/{self.member.pk}/')
                self.assertNotContains(page, 'Open CRM record for')

    def test_response_detail_keeps_questionnaire_scope(self):
        other = Questionnaire.objects.create(title='Wrong parent')
        page = self.client.get(
            f'/studio/questionnaires/{other.pk}/responses/{self.response.pk}/',
        )
        self.assertEqual(page.status_code, 404)


@tag('core')
class ResponseQueueStudioTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.onboarding = Questionnaire.objects.create(
            title='Onboarding 1289', purpose='onboarding',
        )
        cls.feedback = Questionnaire.objects.create(
            title='Feedback 1289', purpose='feedback',
        )
        cls.awaiting = []
        for index in range(2):
            member = User.objects.create_user(email=f'awaiting-{index}@test.com')
            response = Response.objects.create(
                questionnaire=cls.onboarding, respondent=member,
            )
            response.mark_submitted()
            cls.awaiting.append(response)
        cls.draft = Response.objects.create(
            questionnaire=cls.onboarding,
            respondent=User.objects.create_user(email='draft-1289@test.com'),
        )
        cls.reviewed = Response.objects.create(
            questionnaire=cls.onboarding,
            respondent=User.objects.create_user(email='reviewed-1289@test.com'),
            status='submitted', submitted_at=timezone.now(),
            reviewed_at=timezone.now(), reviewed_by=cls.staff,
        )
        cls.feedback_response = Response.objects.create(
            questionnaire=cls.feedback,
            respondent=User.objects.create_user(email='feedback-1289@test.com'),
        )
        cls.feedback_response.mark_submitted()

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_default_queue_and_composed_filters(self):
        page = self.client.get('/studio/questionnaire-responses/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'awaiting-0@test.com')
        self.assertContains(page, 'awaiting-1@test.com')
        self.assertContains(page, 'feedback-1289@test.com')
        self.assertNotContains(page, 'draft-1289@test.com')
        self.assertNotContains(page, 'reviewed-1289@test.com')

        filtered = self.client.get(
            '/studio/questionnaire-responses/',
            {
                'status': 'submitted', 'review': 'awaiting',
                'purpose': 'onboarding', 'questionnaire': self.onboarding.pk,
                'q': 'awaiting-1',
            },
        )
        self.assertContains(filtered, 'awaiting-1@test.com')
        self.assertNotContains(filtered, 'awaiting-0@test.com')
        self.assertNotContains(filtered, 'feedback-1289@test.com')

    def test_draft_plus_review_filter_is_truthfully_empty(self):
        page = self.client.get(
            '/studio/questionnaire-responses/?status=draft&review=awaiting',
        )
        self.assertContains(page, 'No responses match these filters.')
        self.assertNotContains(page, 'draft-1289@test.com')

    def test_review_action_idempotency_draft_error_and_safe_return(self):
        response = self.awaiting[0]
        url = (
            f'/studio/questionnaires/{self.onboarding.pk}/responses/'
            f'{response.pk}/review'
        )
        return_query = 'status=submitted&review=awaiting&purpose=onboarding&page=2'
        first = self.client.post(url, {
            'action': 'review', 'return_to': 'queue', 'return_query': return_query,
        })
        self.assertEqual(first.url.split('?', 1)[0], '/studio/questionnaire-responses/')
        self.assertEqual(
            QueryDict(first.url.split('?', 1)[1]), QueryDict(return_query),
        )
        response.refresh_from_db()
        original_at = response.reviewed_at
        second = self.client.post(url, {
            'action': 'review', 'return_to': 'queue',
            'return_query': 'next=https://attacker.test&status=submitted',
        })
        self.assertEqual(
            second.url, '/studio/questionnaire-responses/?status=submitted',
        )
        response.refresh_from_db()
        self.assertEqual(response.reviewed_at, original_at)
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                action='questionnaire_response_reviewed', user=response.respondent,
            ).count(),
            1,
        )

        draft_url = (
            f'/studio/questionnaires/{self.onboarding.pk}/responses/'
            f'{self.draft.pk}/review'
        )
        draft_result = self.client.post(draft_url, {
            'action': 'review', 'return_to': 'detail',
        }, follow=True)
        self.assertContains(
            draft_result, 'Only submitted responses can be reviewed.',
        )

    def test_review_route_is_post_only_and_staff_only(self):
        response = self.awaiting[0]
        url = (
            f'/studio/questionnaires/{self.onboarding.pk}/responses/'
            f'{response.pk}/review'
        )
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url, {'action': 'review'}).status_code, 302)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(**self.staff_credentials)
        self.assertEqual(
            csrf_client.post(url, {'action': 'review'}).status_code, 403,
        )

        nonstaff = User.objects.create_user(
            email='queue-nonstaff-1289@test.com', password='pw',
        )
        self.client.login(email=nonstaff.email, password='pw')
        denied = self.client.post(url, {'action': 'review'})
        self.assertEqual(denied.status_code, 403)
        self.assertNotContains(denied, response.respondent.email, status_code=403)

    def test_questionnaire_counts_and_per_questionnaire_status_order(self):
        page = self.client.get('/studio/questionnaires/?q=Onboarding+1289')
        row = next(q for q in page.context['questionnaires'] if q == self.onboarding)
        self.assertEqual(row.num_responses, 4)
        self.assertEqual(row.num_submitted, 3)
        self.assertContains(
            page,
            (
                '/studio/questionnaire-responses/?status=submitted&amp;review=all'
                f'&amp;questionnaire={self.onboarding.pk}'
            ),
        )
        drafts = self.client.get(
            f'/studio/questionnaires/{self.onboarding.pk}/responses/?status=draft',
        )
        self.assertContains(drafts, 'draft-1289@test.com')
        self.assertNotContains(drafts, 'awaiting-0@test.com')

    def test_dashboard_attention_count_scope_and_pluralization(self):
        page = self.client.get('/studio/')
        onboarding = next(
            item for item in page.context['attention_items']
            if item['label'] == 'onboarding responses awaiting review'
        )
        self.assertEqual(onboarding['count'], 2)
        self.assertContains(
            page,
            (
                '/studio/questionnaire-responses/?status=submitted&amp;'
                'review=awaiting&amp;purpose=onboarding'
            ),
        )


# ---------------------------------------------------------------------------
# Drag-to-reorder endpoints (issue #836)
# ---------------------------------------------------------------------------


@tag('core')
class QuestionReorderTest(StaffUserMixin, TestCase):
    """The base-question reorder JSON endpoint."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.questionnaire = Questionnaire.objects.create(title='Intake QR')
        cls.qa = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='text',
            prompt='Question A', order=0,
        )
        cls.qb = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='text',
            prompt='Question B', order=1,
        )
        cls.other_questionnaire = Questionnaire.objects.create(title='Other QR')
        cls.qc = Question.objects.create(
            questionnaire=cls.other_questionnaire, question_type='text',
            prompt='Question C', order=0,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.url = (
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/reorder'
        )

    def _post(self, payload, url=None):
        return self.client.post(
            url or self.url, json.dumps(payload),
            content_type='application/json',
        )

    def test_reorder_persists_new_order(self):
        response = self._post([
            {'id': self.qa.pk, 'order': 1},
            {'id': self.qb.pk, 'order': 0},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.qa.refresh_from_db()
        self.qb.refresh_from_db()
        self.assertEqual(self.qa.order, 1)
        self.assertEqual(self.qb.order, 0)

    def test_get_returns_405(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.qa.refresh_from_db()
        self.assertEqual(self.qa.order, 0)

    def test_invalid_json_returns_400_no_writes(self):
        response = self.client.post(
            self.url, 'not-json', content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.qa.refresh_from_db()
        self.qb.refresh_from_db()
        self.assertEqual(self.qa.order, 0)
        self.assertEqual(self.qb.order, 1)

    def test_cross_parent_id_rejected_no_partial_write(self):
        # qc belongs to a different questionnaire -> 400, nothing changes.
        response = self._post([
            {'id': self.qa.pk, 'order': 5},
            {'id': self.qc.pk, 'order': 6},
        ])
        self.assertEqual(response.status_code, 400)
        self.qa.refresh_from_db()
        self.qc.refresh_from_db()
        self.assertEqual(self.qa.order, 0)
        self.assertEqual(self.qc.order, 0)

    def test_unknown_id_rejected(self):
        response = self._post([
            {'id': self.qa.pk, 'order': 0},
            {'id': 999999, 'order': 1},
        ])
        self.assertEqual(response.status_code, 400)
        self.qa.refresh_from_db()
        self.assertEqual(self.qa.order, 0)

    def test_negative_order_rejected_transaction_rolled_back(self):
        response = self._post([
            {'id': self.qa.pk, 'order': 0},
            {'id': self.qb.pk, 'order': -1},
        ])
        self.assertEqual(response.status_code, 400)
        self.qa.refresh_from_db()
        self.qb.refresh_from_db()
        self.assertEqual(self.qa.order, 0)
        self.assertEqual(self.qb.order, 1)

    def test_missing_order_key_rejected(self):
        response = self._post([{'id': self.qa.pk}])
        self.assertEqual(response.status_code, 400)

    def test_non_integer_id_rejected(self):
        response = self._post([{'id': 'abc', 'order': 0}])
        self.assertEqual(response.status_code, 400)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self._post([{'id': self.qa.pk, 'order': 0}])
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertIn('next=', response.url)

    def test_non_staff_forbidden(self):
        self.client.logout()
        User.objects.create_user(
            email='nonstaff@test.com', password='pw', is_staff=False,
        )
        self.client.login(email='nonstaff@test.com', password='pw')
        response = self._post([{'id': self.qa.pk, 'order': 0}])
        self.assertEqual(response.status_code, 403)


@tag('core')
class QuestionOptionReorderTest(StaffUserMixin, TestCase):
    """The option reorder JSON endpoint."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.questionnaire = Questionnaire.objects.create(title='Options QR')
        cls.question = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='single_choice',
            prompt='Pick one', order=0,
        )
        cls.ox = QuestionOption.objects.create(
            question=cls.question, label='X', order=0,
        )
        cls.oy = QuestionOption.objects.create(
            question=cls.question, label='Y', order=1,
        )
        cls.oz = QuestionOption.objects.create(
            question=cls.question, label='Z', order=2,
        )
        # An option belonging to a different question of the same questionnaire.
        cls.other_question = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='single_choice',
            prompt='Other', order=1,
        )
        cls.other_option = QuestionOption.objects.create(
            question=cls.other_question, label='Foreign', order=0,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.url = (
            f'/studio/questionnaires/{self.questionnaire.pk}/'
            f'questions/{self.question.pk}/options/reorder'
        )

    def _post(self, payload):
        return self.client.post(
            self.url, json.dumps(payload), content_type='application/json',
        )

    def test_reorder_persists_new_order(self):
        # Place Z first.
        response = self._post([
            {'id': self.oz.pk, 'order': 0},
            {'id': self.ox.pk, 'order': 1},
            {'id': self.oy.pk, 'order': 2},
        ])
        self.assertEqual(response.status_code, 200)
        self.oz.refresh_from_db()
        self.ox.refresh_from_db()
        self.oy.refresh_from_db()
        self.assertEqual(self.oz.order, 0)
        self.assertEqual(self.ox.order, 1)
        self.assertEqual(self.oy.order, 2)

    def test_get_returns_405(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            self.url, 'oops', content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.ox.refresh_from_db()
        self.assertEqual(self.ox.order, 0)

    def test_option_from_other_question_rejected_no_partial_write(self):
        response = self._post([
            {'id': self.ox.pk, 'order': 9},
            {'id': self.other_option.pk, 'order': 8},
        ])
        self.assertEqual(response.status_code, 400)
        self.ox.refresh_from_db()
        self.other_option.refresh_from_db()
        self.assertEqual(self.ox.order, 0)
        self.assertEqual(self.other_option.order, 0)

    def test_non_staff_forbidden(self):
        self.client.logout()
        User.objects.create_user(
            email='nonstaff2@test.com', password='pw', is_staff=False,
        )
        self.client.login(email='nonstaff2@test.com', password='pw')
        response = self._post([{'id': self.ox.pk, 'order': 0}])
        self.assertEqual(response.status_code, 403)


@tag('core')
class PersonaReorderTest(StaffUserMixin, TestCase):
    """The persona reorder JSON endpoint."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.p0 = Persona.objects.create(
            name='Alex', archetype='The Builder', slug='alex-qr', order=0,
        )
        cls.p1 = Persona.objects.create(
            name='Priya', archetype='The Engineer', slug='priya-qr', order=1,
        )
        cls.p2 = Persona.objects.create(
            name='Sam', archetype='The Analyst', slug='sam-qr', order=2,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.url = '/studio/personas/reorder'

    def _post(self, payload):
        return self.client.post(
            self.url, json.dumps(payload), content_type='application/json',
        )

    def test_reorder_persists_new_order(self):
        response = self._post([
            {'id': self.p2.pk, 'order': 0},
            {'id': self.p0.pk, 'order': 1},
            {'id': self.p1.pk, 'order': 2},
        ])
        self.assertEqual(response.status_code, 200)
        self.p2.refresh_from_db()
        self.p0.refresh_from_db()
        self.p1.refresh_from_db()
        self.assertEqual(self.p2.order, 0)
        self.assertEqual(self.p0.order, 1)
        self.assertEqual(self.p1.order, 2)

    def test_get_returns_405(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            self.url, 'nope', content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.p0.refresh_from_db()
        self.assertEqual(self.p0.order, 0)

    def test_unknown_id_rejected_no_partial_write(self):
        response = self._post([
            {'id': self.p0.pk, 'order': 5},
            {'id': 888888, 'order': 6},
        ])
        self.assertEqual(response.status_code, 400)
        self.p0.refresh_from_db()
        self.assertEqual(self.p0.order, 0)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self._post([{'id': self.p0.pk, 'order': 0}])
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_forbidden(self):
        self.client.logout()
        User.objects.create_user(
            email='nonstaff3@test.com', password='pw', is_staff=False,
        )
        self.client.login(email='nonstaff3@test.com', password='pw')
        response = self._post([{'id': self.p0.pk, 'order': 0}])
        self.assertEqual(response.status_code, 403)


@tag('core')
class ReorderSnapshotFrozenTest(StaffUserMixin, TestCase):
    """Reordering base questions/options must never touch response snapshots."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.questionnaire = Questionnaire.objects.create(title='Snapshot QR')
        cls.qa = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='single_choice',
            prompt='Base A', order=0,
        )
        cls.qb = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='text',
            prompt='Base B', order=1,
        )
        cls.opt_a1 = QuestionOption.objects.create(
            question=cls.qa, label='A1', order=0,
        )
        cls.opt_a2 = QuestionOption.objects.create(
            question=cls.qa, label='A2', order=1,
        )

        # A member's submitted response snapshots the questions in A, B order.
        cls.member = User.objects.create_user(
            email='snap@test.com', password='pw',
        )
        cls.response = Response.objects.create(
            questionnaire=cls.questionnaire, respondent=cls.member,
        )
        cls.response.mark_submitted()
        cls.rq_a = ResponseQuestion.objects.create(
            response=cls.response, source_question=cls.qa,
            question_type='single_choice', prompt='Base A', order=0,
        )
        cls.rq_b = ResponseQuestion.objects.create(
            response=cls.response, source_question=cls.qb,
            question_type='text', prompt='Base B', order=1,
        )
        cls.rqo_a1 = ResponseQuestionOption.objects.create(
            response_question=cls.rq_a, source_option=cls.opt_a1,
            label='A1', order=0,
        )
        cls.rqo_a2 = ResponseQuestionOption.objects.create(
            response_question=cls.rq_a, source_option=cls.opt_a2,
            label='A2', order=1,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def _snapshot_state(self):
        """Capture every snapshot-row field that a reorder could disturb."""
        rqs = {
            rq.pk: (rq.prompt, rq.order, rq.question_type, rq.source_question_id)
            for rq in self.response.response_questions.all()
        }
        rqos = {
            rqo.pk: (rqo.label, rqo.order, rqo.source_option_id)
            for rqo in ResponseQuestionOption.objects.filter(
                response_question__response=self.response,
            )
        }
        answers = {
            a.pk: (a.text_value, a.number_value)
            for a in self.response.answers.all()
        }
        return rqs, rqos, answers

    def test_question_reorder_leaves_snapshots_byte_identical(self):
        before = self._snapshot_state()
        response = self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/reorder',
            json.dumps([
                {'id': self.qa.pk, 'order': 1},
                {'id': self.qb.pk, 'order': 0},
            ]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        # The base questions DID move...
        self.qa.refresh_from_db()
        self.qb.refresh_from_db()
        self.assertEqual(self.qa.order, 1)
        self.assertEqual(self.qb.order, 0)
        # ...but the response snapshot rows are unchanged.
        self.assertEqual(self._snapshot_state(), before)

    def test_option_reorder_leaves_snapshots_byte_identical(self):
        before = self._snapshot_state()
        response = self.client.post(
            f'/studio/questionnaires/{self.questionnaire.pk}/'
            f'questions/{self.qa.pk}/options/reorder',
            json.dumps([
                {'id': self.opt_a1.pk, 'order': 1},
                {'id': self.opt_a2.pk, 'order': 0},
            ]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.opt_a1.refresh_from_db()
        self.opt_a2.refresh_from_db()
        self.assertEqual(self.opt_a1.order, 1)
        self.assertEqual(self.opt_a2.order, 0)
        self.assertEqual(self._snapshot_state(), before)
