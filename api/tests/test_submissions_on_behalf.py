"""``POST /api/submissions/on-behalf`` and ``GET /api/courses/<slug>/homework``.

Staff record a homework or project submission for a member (e.g. answers
that arrived by email): scored like a learner's own submit, marked as
recorded on the member's behalf, audited with the acting staff user, never
overwriting an existing submission without ``replace``, and silent toward
the member unless ``notify``.
"""

import datetime
import json

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias, Token
from community.models import CommunityAuditLog
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseProject,
    Module,
    ProjectSubmission,
    Unit,
    UserCourseProgress,
)
from content.models.homework import (
    AnswerType,
    Homework,
    HomeworkState,
    Question,
    QuestionType,
    Submission,
)
from content.services.homework_submissions import save_submission
from email_app.hooks import _resolve_submission_recorded_context
from email_app.services.email_rendering import render_template_parts
from integrations.config import site_base_url
from notifications.models import Notification

User = get_user_model()

URL = '/api/submissions/on-behalf'
BACKDATE = '2026-09-01T09:30:00+00:00'


def audit_rows():
    return CommunityAuditLog.objects.filter(action='submission_on_behalf')


class OnBehalfBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='ops-laptop')
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw', first_name='Ada',
        )
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(course=cls.course, title='Week 2', slug='week-2')
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 1', external_key='1',
            start_date=today - datetime.timedelta(days=40),
            end_date=today + datetime.timedelta(days=40),
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Week 2 homework', slug='homework',
            sort_order=1, content_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            kind='homework',
        )
        # Closed on purpose: recording a late/retroactive answer is the
        # point of the endpoint, so the state must not block it.
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='week-2-homework', title='Week 2 homework',
            content_id=cls.unit.content_id, state=HomeworkState.CLOSED,
        )
        cls.mc = Question.objects.create(
            homework=cls.homework, source_question_id='q-model',
            text='Which model?', question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='small\nmedium\nlarge', correct_answer='2',
        )
        cls.idea = Question.objects.create(
            homework=cls.homework, source_question_id='project-idea',
            text='Describe your project idea',
            question_type=QuestionType.FREE_FORM_LONG, answer_type=AnswerType.ANY,
        )

    def auth(self, token=None):
        return {'HTTP_AUTHORIZATION': f'Token {(token or self.staff_token).key}'}

    def post(self, body, token=None):
        return self.client.post(
            URL, data=json.dumps(body), content_type='application/json',
            **self.auth(token),
        )

    def homework_body(self, **overrides):
        body = {
            'kind': 'homework',
            'user': 'member@test.com',
            'course': 'ai-buildcamp',
            'homework': 'week-2-homework',
            'answers': {'q-model': 'medium', 'project-idea': 'A RAG bot for invoices'},
            'note': 'Sent by email on 2026-09-01',
        }
        body.update(overrides)
        return body


class PermissionTest(OnBehalfBase):
    def test_missing_token_is_rejected_and_writes_nothing(self):
        response = self.client.post(
            URL, data=json.dumps(self.homework_body()), content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(Submission.objects.exists())

    def test_non_staff_token_is_rejected_and_writes_nothing(self):
        # Tokens can only be minted for staff; a demoted owner's token
        # must stop working.
        former = User.objects.create_user(email='former@test.com', is_staff=True)
        former_token = Token.objects.create(user=former, name='former')
        User.objects.filter(pk=former.pk).update(is_staff=False)
        response = self.post(self.homework_body(), token=former_token)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(audit_rows().exists())

    def test_get_is_not_allowed(self):
        response = self.client.get(URL, **self.auth())
        self.assertEqual(response.status_code, 405)

    def test_homework_list_requires_staff_token(self):
        response = self.client.get('/api/courses/ai-buildcamp/homework')
        self.assertEqual(response.status_code, 401)


class HomeworkCreateTest(OnBehalfBase):
    def test_creates_scored_submission_marked_on_behalf(self):
        response = self.post(self.homework_body(submitted_at=BACKDATE))

        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data['status'], 'created')
        submission = Submission.objects.get(homework=self.homework, student=self.member)
        self.assertEqual(submission.submitted_on_behalf_by, self.staff)
        self.assertEqual(submission.on_behalf_note, 'Sent by email on 2026-09-01')
        self.assertEqual(submission.submitted_at.isoformat(), BACKDATE)
        answers = {a.question_id: (a.answer_text, a.is_correct) for a in submission.answers.all()}
        # The option label is stored as its 1-based index, then scored.
        self.assertEqual(answers[self.mc.pk], ('2', True))
        self.assertEqual(answers[self.idea.pk], ('A RAG bot for invoices', True))
        self.assertEqual(submission.total_score, 2)
        self.assertEqual(data['submission']['total_score'], 2)
        self.assertEqual(data['homework']['accepting_submissions'], False)

    def test_writes_one_audit_row_naming_actor_and_token(self):
        response = self.post(self.homework_body())

        row = audit_rows().get()
        self.assertEqual(row.user, self.member)
        details = json.loads(row.details)
        submission = Submission.objects.get()
        self.assertEqual(details['actor_email'], 'staff@test.com')
        self.assertEqual(details['actor_token'], 'ops-laptop')
        self.assertEqual(details['submission_id'], submission.pk)
        self.assertFalse(details['replaced'])
        self.assertEqual(details['note'], 'Sent by email on 2026-09-01')
        self.assertEqual(response.json()['audit_log_id'], row.pk)

    def test_auto_enrolls_member_in_cohort_like_a_learner_submit(self):
        response = self.post(self.homework_body())

        self.assertTrue(response.json()['cohort_enrollment_created'])
        self.assertTrue(
            CohortEnrollment.objects.filter(user=self.member, cohort=self.cohort).exists()
        )

    def test_does_not_notify_member_by_default(self):
        self.post(self.homework_body())

        self.assertFalse(Notification.objects.filter(user=self.member).exists())
        self.assertFalse(EmailDelivery.objects.filter(purpose='submission_recorded').exists())

    def test_resolves_member_by_id_and_by_alias(self):
        EmailAlias.objects.create(user=self.member, email='old@test.com')
        response = self.post(self.homework_body(user='old@test.com'))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['user']['id'], self.member.pk)

        response = self.post(self.homework_body(user=str(self.member.pk), replace=True))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['user']['email'], 'member@test.com')

    def test_accepts_homework_id_and_numeric_choice_index(self):
        response = self.post({
            'kind': 'homework', 'user': 'member@test.com',
            'homework_id': self.homework.pk, 'answers': {str(self.mc.pk): 3},
        })
        self.assertEqual(response.status_code, 201, response.content)
        answer = Submission.objects.get().answers.get()
        self.assertEqual((answer.answer_text, answer.is_correct), ('3', False))

    def test_stepper_homework_marks_the_unit_complete(self):
        Homework.objects.filter(pk=self.homework.pk).update(stepper_enabled=True)
        self.post(self.homework_body())
        self.assertTrue(
            UserCourseProgress.objects.filter(
                user=self.member, unit=self.unit, completed_at__isnull=False,
            ).exists()
        )

    def test_learner_resubmit_clears_on_behalf_marker(self):
        self.post(self.homework_body())
        save_submission(
            self.homework, self.member, homework_link='',
            answers_by_question_id={self.idea.pk: 'My own words'},
        )
        submission = Submission.objects.get()
        self.assertIsNone(submission.submitted_on_behalf_by)
        self.assertEqual(submission.on_behalf_note, '')


class HomeworkReplaceTest(OnBehalfBase):
    def setUp(self):
        save_submission(
            self.homework, self.member, homework_link='https://github.com/m/old',
            answers_by_question_id={self.mc.pk: '1', self.idea.pk: 'Old idea'},
        )
        self.original = Submission.objects.get()

    def test_existing_submission_is_refused_without_replace(self):
        response = self.post(self.homework_body())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'submission_exists')
        existing = response.json()['details']['existing_submission']
        self.assertEqual(existing['id'], self.original.pk)
        submission = Submission.objects.get()
        self.assertIsNone(submission.submitted_on_behalf_by)
        self.assertEqual(submission.answers.get(question=self.idea).answer_text, 'Old idea')
        self.assertFalse(audit_rows().exists())

    def test_replace_overwrites_the_full_answer_set(self):
        response = self.post(self.homework_body(
            replace=True, answers={'project-idea': 'New idea'},
        ))

        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data['status'], 'replaced')
        self.assertEqual(data['replaced_submission']['homework_link'], 'https://github.com/m/old')
        submission = Submission.objects.get()
        self.assertEqual(submission.pk, self.original.pk)
        self.assertEqual(
            list(submission.answers.values_list('answer_text', flat=True)), ['New idea'],
        )
        self.assertIsNone(submission.homework_link)
        self.assertEqual(submission.submitted_on_behalf_by, self.staff)
        self.assertTrue(json.loads(audit_rows().get().details)['replaced'])


class HomeworkDryRunTest(OnBehalfBase):
    def test_dry_run_scores_but_writes_nothing(self):
        response = self.post(self.homework_body(dry_run=True, notify=True))

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data['status'], 'would_create')
        self.assertTrue(data['dry_run'])
        self.assertEqual(data['submission']['total_score'], 2)
        self.assertIsNone(data['audit_log_id'])
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(CohortEnrollment.objects.exists())
        self.assertFalse(audit_rows().exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(EmailDelivery.objects.filter(purpose='submission_recorded').exists())

    def test_dry_run_reports_would_replace_and_keeps_existing(self):
        save_submission(
            self.homework, self.member, homework_link='',
            answers_by_question_id={self.idea.pk: 'Old idea'},
        )
        response = self.post(self.homework_body(dry_run=True, replace=True))

        self.assertEqual(response.json()['status'], 'would_replace')
        self.assertEqual(
            Submission.objects.get().answers.get().answer_text, 'Old idea',
        )

    def test_dry_run_fails_like_the_real_call_on_existing_submission(self):
        save_submission(
            self.homework, self.member, homework_link='',
            answers_by_question_id={self.idea.pk: 'Old idea'},
        )
        response = self.post(self.homework_body(dry_run=True))
        self.assertEqual(response.status_code, 409)


class NotifyTest(OnBehalfBase):
    def test_notify_creates_bell_and_email_with_learner_link(self):
        response = self.post(self.homework_body(notify=True))

        self.assertEqual(response.status_code, 201, response.content)
        bell = Notification.objects.get(user=self.member)
        self.assertEqual(bell.notification_type, 'submission_recorded')
        self.assertEqual(bell.url, f'{self.unit.get_absolute_url()}?cohort=1')
        delivery = EmailDelivery.objects.get(purpose='submission_recorded')
        self.assertEqual(delivery.recipient_email, 'member@test.com')
        self.assertEqual(response.json()['notification']['email_delivery_id'], str(delivery.pk))

        context = dict(delivery.context_data)
        _resolve_submission_recorded_context(delivery, context)
        self.assertEqual(
            context['submission_url'],
            f"{site_base_url().rstrip('/')}{self.unit.get_absolute_url()}?cohort=1",
        )
        subject, body_markdown, _html, _footer = render_template_parts(
            'submission_recorded', self.member, context,
        )
        self.assertEqual(subject, 'Your submission for Week 2 homework was recorded')
        self.assertIn('Hi Ada', body_markdown)
        self.assertIn('View your submission', body_markdown)


class ValidationTest(OnBehalfBase):
    def test_unknown_user_is_404(self):
        response = self.post(self.homework_body(user='ghost@test.com'))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'user_not_found')

    def test_unknown_question_lists_allowed_keys(self):
        response = self.post(self.homework_body(answers={'nope': 'x'}))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'unknown_question')
        self.assertEqual(
            response.json()['details']['allowed'], ['q-model', 'project-idea'],
        )

    def test_out_of_range_choice_is_refused(self):
        response = self.post(self.homework_body(answers={'q-model': 7}))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'invalid_answer')
        self.assertFalse(Submission.objects.exists())

    def test_future_submitted_at_is_refused(self):
        future = (timezone.now() + datetime.timedelta(days=2)).isoformat()
        response = self.post(self.homework_body(submitted_at=future))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['details']['field'], 'submitted_at')

    def test_project_fields_are_refused_for_homework(self):
        response = self.post(self.homework_body(project_url='https://x.test'))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'unknown_field')

    def test_ambiguous_slug_across_cohorts_needs_cohort(self):
        cohort2 = Cohort.objects.create(
            course=self.course, name='Cohort 2', external_key='2',
            start_date=timezone.now().date(),
        )
        Homework.objects.create(
            cohort=cohort2, slug='week-2-homework', title='Week 2 homework',
        )
        response = self.post(self.homework_body())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'ambiguous_homework')

        response = self.post(self.homework_body(cohort='1'))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Submission.objects.get().homework, self.homework)

    def test_member_enrollment_breaks_a_cohort_tie(self):
        cohort2 = Cohort.objects.create(
            course=self.course, name='Cohort 2', external_key='2',
            start_date=timezone.now().date(),
        )
        other = Homework.objects.create(
            cohort=cohort2, slug='week-2-homework', title='Week 2 homework',
        )
        CohortEnrollment.objects.create(user=self.member, cohort=cohort2)
        response = self.post(self.homework_body(answers={}))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Submission.objects.get().homework, other)


class ProjectTest(OnBehalfBase):
    def project_body(self, **overrides):
        body = {
            'kind': 'project', 'user': 'member@test.com', 'course': 'ai-buildcamp',
            'project_url': 'https://github.com/member/invoice-bot',
            'description': 'Idea sent by email',
            'note': 'Emailed project idea',
        }
        body.update(overrides)
        return body

    def test_creates_backdated_legacy_project_submission(self):
        response = self.post(self.project_body(submitted_at=BACKDATE))

        self.assertEqual(response.status_code, 201, response.content)
        submission = ProjectSubmission.objects.get(user=self.member)
        self.assertIsNone(submission.course_project)
        self.assertEqual(submission.submitted_at.isoformat(), BACKDATE)
        self.assertEqual(submission.submitted_on_behalf_by, self.staff)
        self.assertEqual(submission.description, 'Idea sent by email')
        self.assertEqual(json.loads(audit_rows().get().details)['kind'], 'project')

    def test_existing_project_needs_replace(self):
        ProjectSubmission.objects.create(
            user=self.member, course=self.course, project_url='https://github.com/m/old',
        )
        self.assertEqual(self.post(self.project_body()).status_code, 409)

        response = self.post(self.project_body(replace=True))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            ProjectSubmission.objects.get().project_url,
            'https://github.com/member/invoice-bot',
        )

    def test_project_in_review_is_never_replaced(self):
        ProjectSubmission.objects.create(
            user=self.member, course=self.course,
            project_url='https://github.com/m/old', status='in_review',
        )
        response = self.post(self.project_body(replace=True))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'submission_locked')
        self.assertEqual(ProjectSubmission.objects.get().project_url, 'https://github.com/m/old')

    def test_dated_attempts_require_project_slug(self):
        due = timezone.now() + datetime.timedelta(days=5)
        CourseProject.objects.create(
            course=self.course, slug='attempt-1', title='Attempt 1',
            submission_due_at=due, review_due_at=due + datetime.timedelta(days=3),
        )
        response = self.post(self.project_body())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['details']['candidates'], ['attempt-1'])

        response = self.post(self.project_body(project='attempt-1'))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(ProjectSubmission.objects.get().course_project.slug, 'attempt-1')

    def test_project_dry_run_writes_nothing(self):
        response = self.post(self.project_body(dry_run=True))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'would_create')
        self.assertFalse(ProjectSubmission.objects.exists())
        self.assertFalse(audit_rows().exists())


class HomeworkListTest(OnBehalfBase):
    def test_lists_question_keys_without_correct_answers(self):
        response = self.client.get('/api/courses/ai-buildcamp/homework', **self.auth())

        self.assertEqual(response.status_code, 200)
        [homework] = response.json()['homework']
        self.assertEqual(homework['slug'], 'week-2-homework')
        self.assertEqual(homework['cohort'], '1')
        self.assertEqual(
            [(q['key'], q['options']) for q in homework['questions']],
            [('q-model', ['small', 'medium', 'large']), ('project-idea', [])],
        )
        self.assertNotIn('correct_answer', homework['questions'][0])
