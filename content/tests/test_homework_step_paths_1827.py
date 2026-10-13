"""Issue #1827: stepped homework uses canonical path URLs per step.

The bare homework Unit URL and legacy ``?homework_step=`` bookmarks
redirect to ``/courses/<course>/<unit-route>/<step>``; every generated
link and redirect uses that path form while drafts, cohorts, receipts and
the existing route taxonomy stay unchanged.
"""

import datetime
import re

from community_base.homework_steps.models import HomeworkDraft
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Course, Module, Unit
from content.models.cohort import Cohort, CohortEnrollment
from content.models.homework import AnswerType, Homework, Question, QuestionType
from content.services.homework_step_reader import option_key
from content.services.homework_submissions import save_submission
from content.tests.test_homework_submission_view import HomeworkUnitSetupMixin


def stepper_hrefs(response):
    """Every link and form action rendered inside the homework stepper."""
    html = response.content.decode()
    section = html.split('data-testid="homework-stepper"', 1)[1].split('</section>', 1)[0]
    return re.findall(r'(?:href|action)="([^"]*)"', section)


class HomeworkStepPathTestBase(HomeworkUnitSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.homework.stepper_enabled = True
        cls.homework.save(update_fields=['stepper_enabled'])

    def setUp(self):
        super().setUp()
        self.client.force_login(self.student)

    def step_url(self, step):
        return f'{self.unit_url}/{step}'

    def seed_draft(self):
        self.client.get(self.step_url('intro'))
        return HomeworkDraft.objects.get(user=self.student)

    def save_answer(self, key, answer, revision):
        draft = HomeworkDraft.objects.get(user=self.student)
        return self.client.post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/{key}',
            {'draft_token': str(draft.token), 'revision': str(revision), 'answer': answer},
        )

    def step_post(self, step, data, *, url=None):
        draft = HomeworkDraft.objects.get(user=self.student)
        return self.client.post(url or self.step_url(step), {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token), 'homework_step': step,
            'revision': str(draft.revision), **data,
        })


class BareUnitEntryRedirectTest(HomeworkStepPathTestBase):
    def test_first_visit_redirects_to_intro_without_creating_a_draft(self):
        response = self.client.get(self.unit_url)

        self.assertRedirects(
            response, self.step_url('intro'), fetch_redirect_response=False,
        )
        self.assertFalse(HomeworkDraft.objects.filter(user=self.student).exists())

    def test_existing_draft_resumes_at_first_unanswered_question(self):
        self.seed_draft()
        self.save_answer('q1-lines', option_key('14'), 0)

        response = self.client.get(self.unit_url, follow=True)

        self.assertEqual(
            response.redirect_chain, [(self.step_url('q2-reflect'), 302)],
        )
        self.assertEqual(response.context['stepper']['step'], 'q2-reflect')
        self.assertEqual(HomeworkDraft.objects.filter(user=self.student).count(), 1)
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers,
            {'q1-lines': option_key('14')},
        )

    def test_submitted_homework_opens_review(self):
        save_submission(
            self.homework, self.student, homework_link='',
            answers_by_question_id={self.mc_question.pk: '2'},
        )

        response = self.client.get(self.unit_url)

        self.assertRedirects(
            response, self.step_url('review'), fetch_redirect_response=False,
        )

    def test_owned_cohort_receipt_and_notice_query_survive_the_redirect(self):
        self.cohort.external_key = 'cohort-4'
        self.cohort.save(update_fields=['external_key'])
        CohortEnrollment.objects.create(user=self.student, cohort=self.cohort)

        response = self.client.get(
            f'{self.unit_url}?cohort=cohort-4&receipt=abc&notice=changed',
        )

        self.assertRedirects(
            response,
            f'{self.step_url("intro")}?cohort=cohort-4&receipt=abc&notice=changed',
            fetch_redirect_response=False,
        )


class LegacyQueryBookmarkTest(HomeworkStepPathTestBase):
    def test_valid_key_moves_into_path_and_keeps_cohort(self):
        self.cohort.external_key = 'cohort-4'
        self.cohort.save(update_fields=['external_key'])
        CohortEnrollment.objects.create(user=self.student, cohort=self.cohort)
        self.seed_draft()
        self.save_answer('q2-reflect', 'Saved reflection', 0)

        response = self.client.get(
            f'{self.unit_url}?homework_step=q2-reflect&cohort=cohort-4', follow=True,
        )

        self.assertEqual(
            response.redirect_chain,
            [(f'{self.step_url("q2-reflect")}?cohort=cohort-4', 302)],
        )
        self.assertEqual(response.context['stepper']['answer'], 'Saved reflection')
        for href in stepper_hrefs(response):
            self.assertNotIn('homework_step', href)

    def test_review_receipt_is_kept_when_the_step_moves(self):
        response = self.client.get(f'{self.unit_url}?homework_step=review&receipt=abc')

        self.assertRedirects(
            response, f'{self.step_url("review")}?receipt=abc',
            fetch_redirect_response=False,
        )

    def test_unknown_key_falls_back_to_resume_path_with_notice(self):
        self.seed_draft()
        self.save_answer('q1-lines', option_key('14'), 0)

        response = self.client.get(f'{self.unit_url}?homework_step=removed-q', follow=True)

        self.assertEqual(
            response.redirect_chain,
            [(f'{self.step_url("q2-reflect")}?notice=changed', 302)],
        )
        self.assertContains(response, 'That question changed. Your saved answers are still here.')
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers,
            {'q1-lines': option_key('14')},
        )

    def test_legacy_query_on_a_step_path_is_stripped(self):
        response = self.client.get(f'{self.step_url("q1-lines")}?homework_step=q2-reflect')

        self.assertRedirects(
            response, self.step_url('q1-lines'), fetch_redirect_response=False,
        )


class StepPathPageTest(HomeworkStepPathTestBase):
    def test_removed_step_path_falls_back_and_keeps_the_draft(self):
        self.seed_draft()
        self.save_answer('q1-lines', option_key('14'), 0)

        response = self.client.get(self.step_url('removed-q'), follow=True)

        self.assertEqual(
            response.redirect_chain,
            [(f'{self.step_url("q2-reflect")}?notice=changed', 302)],
        )
        self.assertContains(response, 'That question changed. Your saved answers are still here.')
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers,
            {'q1-lines': option_key('14')},
        )

    def test_every_generated_link_is_a_path_without_step_query(self):
        response = self.client.get(self.step_url('q2-reflect'))

        hrefs = stepper_hrefs(response)
        self.assertIn(self.step_url('intro'), hrefs)
        self.assertIn(self.step_url('q1-lines'), hrefs)
        self.assertIn(self.step_url('review'), hrefs)
        for href in hrefs:
            self.assertNotIn('homework_step', href)
            self.assertFalse(href.endswith('/'), href)
        self.assertEqual(response.context['stepper']['action'], self.step_url('q2-reflect'))

    def test_dated_homework_shows_due_date_without_step_counter(self):
        response = self.client.get(self.step_url('q1-lines'))

        self.assertContains(response, 'data-testid="homework-due-date"')
        self.assertNotContains(response, 'data-testid="homework-step-position"')
        self.assertIsNone(re.search(r'Step \d+ of \d+', ' '.join(
            re.sub(r'<[^>]+>', ' ', response.content.decode()).split(),
        )))

    def test_undated_homework_has_no_due_date_row(self):
        self.homework.due_date = None
        self.homework.save(update_fields=['due_date'])

        response = self.client.get(self.step_url('q1-lines'))

        self.assertNotContains(response, 'data-testid="homework-due-date"')

    def test_save_and_continue_and_previous_redirect_to_paths(self):
        self.seed_draft()
        forward = self.step_post('q1-lines', {
            'answer': option_key('14'), 'next_step': 'q2-reflect',
        })
        self.assertRedirects(
            forward, self.step_url('q2-reflect'), fetch_redirect_response=False,
        )
        back = self.step_post('q2-reflect', {
            'answer': 'Typed reflection', 'next_step': 'q1-lines',
        })
        self.assertRedirects(
            back, self.step_url('q1-lines'), fetch_redirect_response=False,
        )
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers,
            {'q1-lines': option_key('14'), 'q2-reflect': 'Typed reflection'},
        )

    def test_stale_token_keeps_learner_on_the_step_with_typed_answer(self):
        self.seed_draft()
        response = self.client.post(self.step_url('q2-reflect'), {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': 'stale-token', 'homework_step': 'q2-reflect',
            'revision': '0', 'answer': 'Unsaved words', 'next_step': 'q3-canary',
        })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.context['stepper']['action'], self.step_url('q2-reflect'))

    def test_final_submit_redirects_to_review_path_with_receipt(self):
        self.seed_draft()
        self.save_answer('q1-lines', option_key('14'), 0)

        response = self.step_post('review', {
            'intent': 'submit', 'final_homework_link': 'https://github.com/s/p',
        })

        self.assertEqual(response.status_code, 302)
        self.assertRegex(
            response['Location'], rf'^{re.escape(self.step_url("review"))}\?receipt=',
        )
        page = self.client.get(response['Location'])
        self.assertContains(page, 'data-testid="homework-submitted-status"')
        self.assertEqual(self.homework.submissions.filter(student=self.student).count(), 1)


class StepPathAccessTest(HomeworkStepPathTestBase):
    def test_anonymous_unknown_step_is_404_and_valid_step_shows_no_draft(self):
        self.client.logout()

        unknown = self.client.get(self.step_url('removed-q'))
        valid = self.client.get(self.step_url('q1-lines'))

        self.assertEqual(unknown.status_code, 404)
        self.assertNotContains(valid, 'data-testid="homework-stepper"', status_code=valid.status_code)
        self.assertFalse(HomeworkDraft.objects.exists())

    def test_under_tier_learner_gets_the_gate_without_a_draft(self):
        self.course.required_level = 30
        self.course.save(update_fields=['required_level'])

        for url in (self.unit_url, self.step_url('q1-lines'), self.step_url('removed-q')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertNotContains(
                    response, 'data-testid="homework-stepper"', status_code=403,
                )
        self.assertFalse(HomeworkDraft.objects.exists())

    def test_unowned_cohort_step_path_is_404_without_a_draft(self):
        Cohort.objects.create(
            course=self.course, name='Private cohort', external_key='unowned',
            start_date=timezone.localdate() - datetime.timedelta(days=1),
            end_date=timezone.localdate() + datetime.timedelta(days=30),
        )

        response = self.client.get(f'{self.step_url("q1-lines")}?cohort=unowned')

        self.assertEqual(response.status_code, 404)
        self.assertFalse(HomeworkDraft.objects.exists())

    def test_prose_only_and_inactive_homework_keep_the_bare_unit_page(self):
        self.homework.stepper_enabled = False
        self.homework.save(update_fields=['stepper_enabled'])

        bare = self.client.get(self.unit_url)
        step = self.client.get(self.step_url('intro'))

        self.assertContains(bare, 'homework-submission-form')
        self.assertEqual(step.status_code, 404)


class NestedUnitCollisionTest(TestCase):
    """A real nested Unit wins over a same-shaped homework step path."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp', status='published',
            required_level=0,
        )
        week = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        wrapper = Module.objects.create(
            course=cls.course, parent=week, title='Homework', slug='homework',
            sort_order=1,
        )
        cls.homework_unit = Unit.objects.create(
            module=wrapper, title='Week 1 homework', slug='week-1-homework',
            sort_order=1, kind='homework',
            homework='Intro.\n\n## Question 1. Overlap\nAnswer it.',
            content_id='dddddddd-dddd-dddd-dddd-dddddddddddd',
        )
        cls.real_unit = Unit.objects.create(
            module=wrapper, title='Overlapping lesson', slug='q1-overlap',
            sort_order=2, kind='lesson', body='The real nested lesson.',
        )
        cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 1',
            start_date=timezone.localdate() - datetime.timedelta(days=2),
            end_date=timezone.localdate() + datetime.timedelta(days=30),
        )
        cls.homework = Homework.objects.create(
            cohort=cohort, slug='week-1-homework', title='Week 1 homework',
            content_id=cls.homework_unit.content_id, stepper_enabled=True,
        )
        Question.objects.create(
            homework=cls.homework, source_question_id='q1-overlap',
            text='Answer it.', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY,
        )
        cls.cohort = cohort

    def setUp(self):
        self.user = get_user_model().objects.create_user(email='nested-1827@test.com')
        CohortEnrollment.objects.create(user=self.user, cohort=self.cohort)
        self.client.force_login(self.user)

    def test_real_nested_unit_wins_and_other_steps_still_dispatch(self):
        homework_url = self.homework_unit.get_absolute_url()
        self.assertEqual(homework_url, '/courses/ai-buildcamp/week-1/homework')

        lesson = self.client.get(f'{homework_url}/q1-overlap')
        intro = self.client.get(f'{homework_url}/intro')

        self.assertContains(lesson, 'The real nested lesson.')
        self.assertNotContains(lesson, 'data-testid="homework-stepper"')
        self.assertEqual(intro.context['stepper']['step'], 'intro')
