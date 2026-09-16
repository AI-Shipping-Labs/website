"""Homework submission form on the unit detail page -- issue #1683, tranche 1.

The absolute requirement under test throughout: a submitted answer is
never lost and never silently rejected, and a correct answer / score is
never present in a student-facing response.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Course, Module, Unit
from content.models.cohort import Cohort, CohortEnrollment
from content.models.homework import (
    AnswerType,
    Homework,
    HomeworkState,
    Question,
    QuestionType,
    Submission,
)

User = get_user_model()


class HomeworkUnitSetupMixin:
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-view-1683',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(course=cls.course, title='Module 1', slug='module-1')
        # A currently-running dated cohort -- relative to `timezone.now()`
        # so resolution works regardless of the actual wall-clock date the
        # test suite runs on (issue #1674's cohort-resolution fallback
        # requires a cohort that has already started).
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Module 1 Homework', slug='hw1', sort_order=1,
            homework='Read the intro and answer the questions below.',
            content_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            kind='homework',
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='hw1', title='Module 1 Homework',
            content_id=cls.unit.content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        cls.mc_question = Question.objects.create(
            homework=cls.homework, source_question_id='q1-lines',
            text='How many lines?', question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='12\n14\n16', correct_answer='2',
        )
        cls.ff_question = Question.objects.create(
            homework=cls.homework, source_question_id='q2-reflect',
            text='What was the hardest part?', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY,
        )
        # A distinctive, guaranteed-unique correct answer -- a canary
        # string that could only appear in a rendered response if the
        # template leaked Question.correct_answer directly.
        cls.canary_question = Question.objects.create(
            homework=cls.homework, source_question_id='q3-canary',
            text='Name the primary key.', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.EXACT_STRING,
            correct_answer='answer-leak-canary-c7f1e9',
        )
        cls.unit_url = cls.unit.get_absolute_url()

    def setUp(self):
        self.student = User.objects.create_user(email=f'student-{id(self)}@test.com')


class HomeworkFormRenderTest(HomeworkUnitSetupMixin, TestCase):
    def test_first_visit_renders_empty_prefilled_form(self):
        self.client.force_login(self.student)
        response = self.client.get(self.unit_url)

        self.assertContains(response, 'homework-submission-form')
        self.assertContains(response, self.mc_question.text)
        self.assertContains(response, self.ff_question.text)

    def test_unit_with_no_homework_row_renders_unchanged(self):
        """Backward compatibility: an is_homework unit with no matching
        Homework row keeps rendering prose-only, no form, no error."""
        plain_unit = Unit.objects.create(
            module=self.module, title='Plain homework', slug='hw-plain', sort_order=2,
            homework='Just prose, no structured questions yet.',
            content_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            kind='homework',
        )
        self.client.force_login(self.student)
        response = self.client.get(plain_unit.get_absolute_url())

        self.assertContains(response, 'Just prose, no structured questions yet.')
        self.assertNotContains(response, 'homework-submission-form')

    def test_anonymous_sees_login_prompt_not_a_form(self):
        self.unit.is_preview = True
        self.unit.save(update_fields=['is_preview'])

        response = self.client.get(self.unit_url)

        self.assertContains(response, 'homework-login-prompt')
        self.assertNotContains(response, 'homework-submission-form')
        self.assertNotContains(response, '<input type="radio"')
        self.assertContains(response, '/accounts/login/?next=')


class HomeworkAnswerLeakTest(HomeworkUnitSetupMixin, TestCase):
    """The single non-negotiable requirement: correct answers and scores
    never reach a student-facing response."""

    def test_canary_correct_answer_never_appears_in_rendered_page(self):
        """The answer text is absent from the rendered page even when the
        student submits an incorrect answer to the canary question -- the
        only way that exact string could appear is if the template
        rendered Question.correct_answer directly."""
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '1',  # wrong on purpose
            f'answer_{self.ff_question.pk}': 'It was hard.',
            f'answer_{self.canary_question.pk}': 'a wrong guess',
            'homework_link': '',
        })
        response = self.client.get(self.unit_url)

        self.assertNotContains(response, self.canary_question.correct_answer)
        self.assertNotContains(response, 'is_correct')
        self.assertNotContains(response, 'questions_score')
        self.assertNotContains(response, 'total_score')

    def test_score_and_grading_vocabulary_never_shown_to_student(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '2',  # correct
            f'answer_{self.ff_question.pk}': 'Reflection text.',
            f'answer_{self.canary_question.pk}': self.canary_question.correct_answer,
        })
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertGreater(submission.questions_score, 0)

        response = self.client.get(self.unit_url)
        for banned in ('Score:', 'Correct', 'Incorrect', 'grade', 'Grade'):
            self.assertNotContains(response, banned)


class HomeworkSubmitTest(HomeworkUnitSetupMixin, TestCase):
    def test_submit_creates_submission_and_redirects_with_success_message(self):
        self.client.force_login(self.student)
        response = self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '2',
            f'answer_{self.ff_question.pk}': 'Debugging the pipeline.',
            'homework_link': 'https://github.com/student/hw1',
        }, follow=True)

        self.assertContains(response, 'Your homework was submitted')
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.homework_link, 'https://github.com/student/hw1')
        self.assertEqual(submission.answers.count(), 2)

    def test_resubmit_updates_same_submission_no_duplicate_rows(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {f'answer_{self.mc_question.pk}': '1'})
        self.client.post(self.unit_url, {f'answer_{self.mc_question.pk}': '2'})

        self.assertEqual(
            Submission.objects.filter(homework=self.homework, student=self.student).count(), 1,
        )
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.answers.get(question=self.mc_question).answer_text, '2')
        self.assertEqual(submission.answers.filter(question=self.mc_question).count(), 1)

    def test_partial_answer_succeeds_only_answered_questions_saved(self):
        self.client.force_login(self.student)
        response = self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '2',
            f'answer_{self.ff_question.pk}': '',
        }, follow=True)

        self.assertContains(response, 'Your homework was submitted')
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.answers.count(), 1)
        self.assertTrue(submission.answers.filter(question=self.mc_question).exists())
        self.assertFalse(submission.answers.filter(question=self.ff_question).exists())

    def test_blanking_a_previously_answered_question_on_resubmit_clears_it(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '2',
            f'answer_{self.ff_question.pk}': 'first answer',
        })
        self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '2',
            f'answer_{self.ff_question.pk}': '',
        })
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertFalse(submission.answers.filter(question=self.ff_question).exists())

    def test_homework_link_roundtrips_across_submits(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {'homework_link': 'https://github.com/student/v1'})
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'https://github.com/student/v1')

        self.client.post(self.unit_url, {'homework_link': 'https://github.com/student/v2'})
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'https://github.com/student/v2')
        self.assertNotContains(response, 'https://github.com/student/v1')

    def test_auto_scoring_marks_correct_and_incorrect_answers(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '2',  # correct
            f'answer_{self.ff_question.pk}': 'anything',  # correct (ANY)
        })
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertTrue(submission.answers.get(question=self.mc_question).is_correct)
        self.assertTrue(submission.answers.get(question=self.ff_question).is_correct)
        self.assertEqual(submission.questions_score, 2)
        self.assertEqual(submission.total_score, 2)

    def test_incorrect_answer_scores_zero_but_still_saves(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {f'answer_{self.mc_question.pk}': '1'})
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        answer = submission.answers.get(question=self.mc_question)
        self.assertEqual(answer.answer_text, '1')
        self.assertFalse(answer.is_correct)
        self.assertEqual(submission.questions_score, 0)

    def test_cohort_enrollment_auto_created_on_first_submit(self):
        self.assertFalse(
            CohortEnrollment.objects.filter(user=self.student, cohort=self.cohort).exists()
        )
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {f'answer_{self.mc_question.pk}': '2'})

        self.assertTrue(
            CohortEnrollment.objects.filter(user=self.student, cohort=self.cohort).exists()
        )
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.enrollment.cohort_id, self.cohort.pk)


class HomeworkDeadlineTest(HomeworkUnitSetupMixin, TestCase):
    def test_form_editable_before_deadline(self):
        self.client.force_login(self.student)
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'homework-submit-button')
        self.assertNotContains(response, 'homework-deadline-passed-banner')

    def test_get_after_deadline_shows_disabled_form_with_saved_answers_and_banner(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {f'answer_{self.mc_question.pk}': '2'})

        self.homework.due_date = timezone.now() - datetime.timedelta(days=1)
        self.homework.save(update_fields=['due_date'])

        response = self.client.get(self.unit_url)
        self.assertContains(response, 'homework-deadline-passed-banner')
        self.assertContains(response, 'The deadline for this homework passed on')
        self.assertNotContains(response, 'homework-submit-button')
        # The previously-saved answer is still shown, not hidden.
        self.assertContains(response, 'checked')

    def test_post_after_deadline_is_rejected_and_does_not_alter_saved_submission(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {f'answer_{self.mc_question.pk}': '2'})

        self.homework.due_date = timezone.now() - datetime.timedelta(days=1)
        self.homework.save(update_fields=['due_date'])

        response = self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '1',
        }, follow=True)

        self.assertContains(response, 'The deadline for this homework has passed')
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.answers.get(question=self.mc_question).answer_text, '2')

    def test_post_after_deadline_with_no_prior_submission_saves_nothing(self):
        self.homework.due_date = timezone.now() - datetime.timedelta(days=1)
        self.homework.save(update_fields=['due_date'])

        self.client.force_login(self.student)
        self.client.post(self.unit_url, {f'answer_{self.mc_question.pk}': '2'})

        self.assertFalse(
            Submission.objects.filter(homework=self.homework, student=self.student).exists()
        )


class HomeworkTimezoneDisplayTest(HomeworkUnitSetupMixin, TestCase):
    def test_due_date_rendered_in_viewer_account_timezone(self):
        self.student.preferred_timezone = 'America/New_York'
        self.student.save(update_fields=['preferred_timezone'])
        self.client.force_login(self.student)

        response = self.client.get(self.unit_url)
        self.assertContains(response, 'America/New_York')

    def test_due_date_falls_back_to_site_default_timezone_without_a_preference(self):
        self.client.force_login(self.student)
        response = self.client.get(self.unit_url)
        # Default fallback is Europe/Berlin (EVENT_DISPLAY_TIMEZONE default).
        self.assertContains(response, 'Europe/Berlin')


class HomeworkSubmoduleUnitTest(TestCase):
    """A homework unit whose parent MODULE is itself a submodule (issue
    #1674 curriculum nesting) -- what real re-imported buildcamp content
    looks like. Exercises the four-segment
    ``/courses/<course>/<parent>/<submodule>/<unit>`` route
    (``course_submodule_unit_detail``), not the two-segment route the
    rest of this file covers.
    """

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-submodule-1683',
            status='published', required_level=0,
        )
        cls.week1 = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.homework_submodule = Module.objects.create(
            course=cls.course, title='Homework', slug='homework',
            sort_order=2, parent=cls.week1,
        )
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.unit = Unit.objects.create(
            module=cls.homework_submodule, title='Module 1 Homework', slug='hw1',
            sort_order=1, homework='Read the intro and answer the questions below.',
            content_id='cccccccc-cccc-cccc-cccc-cccccccccccc', kind='homework',
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='hw1', title='Module 1 Homework',
            content_id=cls.unit.content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        cls.question = Question.objects.create(
            homework=cls.homework, source_question_id='q1',
            text='How many lines?', question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='12\n14\n16', correct_answer='2',
        )
        cls.unit_url = cls.unit.get_absolute_url()

    def setUp(self):
        self.student = User.objects.create_user(email=f'submodule-student-{id(self)}@test.com')

    def test_unit_url_is_the_four_segment_submodule_shape(self):
        self.assertEqual(
            self.unit_url,
            f'/courses/{self.course.slug}/{self.week1.slug}/{self.homework_submodule.slug}/{self.unit.slug}',
        )

    def test_get_renders_the_submission_form_on_the_submodule_unit_page(self):
        self.client.force_login(self.student)
        response = self.client.get(self.unit_url)

        self.assertContains(response, 'homework-submission-form')
        self.assertContains(response, self.question.text)

    def test_post_on_the_submodule_unit_page_creates_a_submission(self):
        self.client.force_login(self.student)
        response = self.client.post(self.unit_url, {
            f'answer_{self.question.pk}': '2',
        }, follow=True)

        self.assertContains(response, 'Your homework was submitted')
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertTrue(submission.answers.get(question=self.question).is_correct)

    def test_resubmit_on_the_submodule_unit_page_updates_the_same_row(self):
        self.client.force_login(self.student)
        self.client.post(self.unit_url, {f'answer_{self.question.pk}': '1'})
        self.client.post(self.unit_url, {f'answer_{self.question.pk}': '2'})

        self.assertEqual(
            Submission.objects.filter(homework=self.homework, student=self.student).count(), 1,
        )
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.answers.get(question=self.question).answer_text, '2')


class HomeworkSelfPacedCohortTest(TestCase):
    """Tester-confirmed bug fix: a self-paced learner (course/cohort with
    ``mode='self_paced'``) must be able to see and submit their homework
    form -- the resolver used to require ``mode='cohort'`` and silently
    resolved nothing for a self-paced-only student, permanently."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Self-Paced Buildcamp', slug='self-paced-buildcamp-1683',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(course=cls.course, title='Module 1', slug='module-1')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Module 1 Homework', slug='hw1', sort_order=1,
            homework='Read the intro and answer the questions below.',
            content_id='11111111-2222-3333-4444-555555555555', kind='homework',
        )
        # The stamped due_date is deliberately far in the past -- a
        # self-paced cohort never enforces it (see is_accepting_submissions).
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='hw1', title='Module 1 Homework',
            content_id=cls.unit.content_id,
            due_date=timezone.now() - datetime.timedelta(days=200),
        )
        cls.question = Question.objects.create(
            homework=cls.homework, source_question_id='q1',
            text='How many lines?', question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='12\n14\n16', correct_answer='2',
        )
        cls.unit_url = cls.unit.get_absolute_url()

    def setUp(self):
        self.student = User.objects.create_user(email=f'self-paced-student-{id(self)}@test.com')
        CohortEnrollment.objects.create(user=self.student, cohort=self.cohort)

    def test_get_renders_an_editable_submission_form_past_the_stamped_due_date(self):
        self.client.force_login(self.student)
        response = self.client.get(self.unit_url)

        self.assertContains(response, 'homework-submission-form')
        self.assertContains(response, 'homework-submit-button')
        self.assertNotContains(response, 'homework-deadline-passed-banner')

    def test_post_creates_a_submission_past_the_stamped_due_date(self):
        self.client.force_login(self.student)
        response = self.client.post(self.unit_url, {
            f'answer_{self.question.pk}': '2',
        }, follow=True)

        self.assertContains(response, 'Your homework was submitted')
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertTrue(submission.answers.get(question=self.question).is_correct)

    def test_get_after_close_shows_the_closed_not_deadline_banner(self):
        """Tester-confirmed copy bug: a self-paced homework closed via
        ``state`` has no deadline to have passed -- the banner must say
        the homework is closed, not invent a passed-deadline date."""
        self.homework.state = HomeworkState.CLOSED
        self.homework.save(update_fields=['state'])
        self.client.force_login(self.student)

        response = self.client.get(self.unit_url)

        self.assertContains(response, 'homework-deadline-passed-banner')
        self.assertContains(response, 'This homework is closed')
        self.assertNotContains(response, 'The deadline for this homework passed on')

    def test_post_after_close_is_rejected_with_the_closed_not_deadline_message(self):
        """Same copy bug, POST-rejection side: the flash message must not
        claim a deadline passed for a self-paced homework closed via
        ``state``."""
        self.homework.state = HomeworkState.CLOSED
        self.homework.save(update_fields=['state'])
        self.client.force_login(self.student)

        response = self.client.post(self.unit_url, {
            f'answer_{self.question.pk}': '2',
        }, follow=True)

        self.assertContains(response, 'This homework is closed; this answer was not saved.')
        self.assertNotContains(response, 'The deadline for this homework has passed')
        self.assertFalse(
            Submission.objects.filter(homework=self.homework, student=self.student).exists()
        )

    def test_anonymous_visitor_with_no_enrollment_also_resolves_via_fallback(self):
        """Backstop: even without a CohortEnrollment, the self-paced
        cohort is eligible via resolve_homework_for_unit's fallback path
        (not just the enrolled-viewer branch)."""
        self.unit.is_preview = True
        self.unit.save(update_fields=['is_preview'])

        response = self.client.get(self.unit_url)
        self.assertContains(response, 'homework-login-prompt')
