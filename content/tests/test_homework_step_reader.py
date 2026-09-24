"""AISL homework reader integration on an explicitly activated assignment."""

import datetime
from html.parser import HTMLParser

from community_base.homework_steps.models import HomeworkDraft
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from content.models.cohort import Cohort, CohortEnrollment
from content.models.homework import Answer, Homework, HomeworkState, Question, Submission
from content.services import completion as completion_service
from content.services.homework_submissions import save_submission
from content.services.homework_step_reader import (
    LEARNING_IN_PUBLIC_KEY,
    build_assignment,
    option_key,
)
from content.services.homework_step_sections import (
    split_out_named_section,
    validate_question_bindings,
)
from content.tests.test_homework_submission_view import HomeworkUnitSetupMixin


class CheckedRadioParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'input' and attributes.get('type') == 'radio' and 'checked' in attributes:
            self.values.add(attributes.get('value', ''))


def checked_radio_values(response):
    parser = CheckedRadioParser()
    parser.feed(response.content.decode())
    return parser.values


class HomeworkStepBindingsTest(SimpleTestCase):
    def test_rich_prompts_bind_to_stable_ids_and_keep_intro_and_closing(self):
        intro, prompts, closing = validate_question_bindings(
            'Download the books.\n\n## Question 1. First\nUse `wc -l`.\n'
            '```python\n## Question 99. Example only\n```\n'
            '## Question 2. Second\nRead [the guide](https://example.com).\n'
            '## AI Assistants\nAsk for help.',
            ['q1-lines', 'q2-reflect'], 'course/homework.md',
        )
        self.assertIn('Download the books', intro)
        self.assertIn('Use `wc -l`', prompts['q1-lines'])
        self.assertIn('Question 99. Example only', prompts['q1-lines'])
        self.assertIn('Read [the guide]', prompts['q2-reflect'])
        self.assertIn('Ask for help.', closing)

    def test_reordered_or_duplicate_bindings_name_source_file(self):
        markdown = '## Question 1. First\nA\n## Question 2. Second\nB'
        for ids in (['q2-reflect', 'q1-lines'], ['q1-lines', 'q1-lines']):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'course/homework.md'):
                validate_question_bindings(markdown, ids, 'course/homework.md')

    def test_learning_in_public_section_splits_from_other_closing_guidance(self):
        remaining, guidance = split_out_named_section(
            '## Submission\nAdd your URL.\n'
            '## Learning in Public\nShare a demo.\n'
            '```markdown\n## Learning in Public\nnot a new section\n```\n'
            '## FAQ\nRead the FAQ.\n',
            'Learning in Public',
        )

        self.assertIn('Add your URL.', remaining)
        self.assertIn('## FAQ', remaining)
        self.assertNotIn('## Learning in Public', remaining)
        self.assertIn('Share a demo.', guidance)
        self.assertIn('not a new section', guidance)


class ActivatedHomeworkReaderTest(HomeworkUnitSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.unit.homework = (
            'Download the books first.\n\n'
            '## Question 1. Lines\nUse `wc -l` and [the file](https://example.com).\n'
            '## Question 2. Reflect\nWhat was difficult?\n'
            '## Question 3. Primary key\nName it.\n'
            '## Learning in Public\nShare your work.'
        )
        self.unit.save(update_fields=['homework'])
        self.homework.stepper_enabled = True
        self.homework.save(update_fields=['stepper_enabled'])
        self.client.force_login(self.student)

    def save_answer(self, key, revision, answer, *, client=None):
        draft = HomeworkDraft.objects.get(user=self.student)
        if key == self.mc_question.source_question_id and answer in ('1', '2', '3'):
            answer = option_key(self.mc_question.options_list[int(answer) - 1])
        return (client or self.client).post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/{key}',
            {
                'draft_token': str(draft.token), 'revision': str(revision),
                'answer': answer,
            },
        )

    def test_reader_preserves_unit_url_and_renders_one_rich_prompt(self):
        intro = self.client.get(self.unit_url)
        self.assertContains(intro, 'homework-stepper')
        self.assertContains(intro, 'Download the books first.')
        self.assertNotContains(intro, 'What was difficult?')
        self.assertNotContains(intro, 'homework-submission-form')
        question = self.client.get(f'{self.unit_url}?homework_step=q1-lines')
        self.assertContains(question, 'Use <code>wc -l</code>')
        self.assertContains(question, 'the file')
        self.assertNotContains(question, 'What was difficult?')
        self.assertContains(question, 'answer')
        self.assertNotContains(question, self.canary_question.correct_answer)

    def test_positive_public_link_cap_adds_a_distinct_authored_step(self):
        self.homework.learning_in_public_cap = 3
        self.homework.time_spent_lectures_field = True
        self.homework.time_spent_homework_field = True
        self.homework.save(update_fields=[
            'learning_in_public_cap', 'time_spent_lectures_field',
            'time_spent_homework_field',
        ])

        assignment = build_assignment(self.homework, self.unit, self.student)
        response = self.client.get(f'{self.unit_url}?homework_step={LEARNING_IN_PUBLIC_KEY}')

        self.assertEqual(assignment.questions[-1].key, LEARNING_IN_PUBLIC_KEY)
        self.assertIn('Learning in Public', assignment.questions[-1].prompt)
        self.assertEqual(assignment.context['learning_in_public_cap'], 3)
        self.assertEqual(
            [field.key for field in assignment.final_fields],
            ['homework_link', 'time_spent_lectures', 'time_spent_homework'],
        )
        self.assertEqual(response.context['stepper']['step'], LEARNING_IN_PUBLIC_KEY)
        self.assertContains(response, 'Share your work.')
        self.assertContains(response, 'data-learning-public-links')
        self.assertContains(response, 'data-max-links="3"')
        self.assertContains(response, 'data-public-link-slots')
        self.assertContains(response, 'data-testid="homework-step-current"')
        self.assertContains(response, f'href="{self.unit_url}?homework_step={LEARNING_IN_PUBLIC_KEY}"')

    def test_zero_public_link_cap_hides_step_and_keeps_existing_guidance(self):
        self.homework.learning_in_public_cap = 0
        self.homework.save(update_fields=['learning_in_public_cap'])

        assignment = build_assignment(self.homework, self.unit, self.student)

        self.assertNotIn(LEARNING_IN_PUBLIC_KEY, [q.key for q in assignment.questions])
        self.assertIn('Learning in Public', assignment.instructions)
        response = self.client.get(self.unit_url)
        self.assertNotContains(response, 'data-learning-public-links')

    def test_capstone_form_shape_has_three_links_and_only_homework_url(self):
        self.homework.learning_in_public_cap = 3
        self.homework.homework_url_field = True
        self.homework.time_spent_lectures_field = False
        self.homework.time_spent_homework_field = False
        self.homework.save(update_fields=[
            'learning_in_public_cap', 'homework_url_field',
            'time_spent_lectures_field', 'time_spent_homework_field',
        ])

        assignment = build_assignment(self.homework, self.unit, self.student)

        self.assertEqual(assignment.context['learning_in_public_cap'], 3)
        self.assertEqual([field.key for field in assignment.final_fields], ['homework_link'])

    def test_review_submit_persists_optional_links_and_time_spent_fields(self):
        self.homework.learning_in_public_cap = 3
        self.homework.time_spent_lectures_field = True
        self.homework.time_spent_homework_field = True
        self.homework.save(update_fields=[
            'learning_in_public_cap', 'time_spent_lectures_field',
            'time_spent_homework_field',
        ])
        self.client.get(self.unit_url)
        draft = HomeworkDraft.objects.get(user=self.student)
        public_links = 'https://example.com/progress\nhttps://github.com/student/demo'
        saved = self.save_answer(LEARNING_IN_PUBLIC_KEY, draft.revision, public_links)
        self.assertEqual(saved.json(), {'revision': draft.revision + 1, 'saved': True})
        draft.refresh_from_db()

        response = self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token), 'homework_step': 'review',
            'revision': str(draft.revision), 'intent': 'submit',
            'final_homework_link': 'https://github.com/student/project',
            'final_time_spent_lectures': '1.5',
            'final_time_spent_homework': '2',
        })

        self.assertEqual(response.status_code, 302)
        review = self.client.get(f'{self.unit_url}?homework_step=review')
        self.assertContains(review, 'Homework URL (optional)')
        self.assertContains(review, 'type="number"')
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(
            submission.learning_in_public_links,
            ['https://example.com/progress', 'https://github.com/student/demo'],
        )
        self.assertEqual(submission.homework_link, 'https://github.com/student/project')
        self.assertEqual(submission.time_spent_lectures, 1.5)
        self.assertEqual(submission.time_spent_homework, 2.0)

    def test_invalid_public_link_submission_keeps_the_draft(self):
        self.homework.learning_in_public_cap = 1
        self.homework.save(update_fields=['learning_in_public_cap'])
        self.client.get(self.unit_url)
        draft = HomeworkDraft.objects.get(user=self.student)
        self.save_answer(
            LEARNING_IN_PUBLIC_KEY, draft.revision,
            'https://example.com/one\nhttps://example.com/two',
        )
        draft.refresh_from_db()
        response = self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token), 'homework_step': 'review',
            'revision': str(draft.revision), 'intent': 'submit',
            'final_homework_link': '',
        })

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Add no more than 1 public link.', status_code=400)
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers[LEARNING_IN_PUBLIC_KEY],
            'https://example.com/one\nhttps://example.com/two',
        )
        self.assertFalse(Submission.objects.filter(homework=self.homework).exists())

    def test_disabled_submission_fields_preserve_previous_values(self):
        previous = save_submission(
            self.homework, self.student,
            homework_link='https://example.com/project',
            learning_in_public_links=['https://example.com/progress'],
            time_spent_lectures=1.5,
            time_spent_homework=2.0,
            answers_by_question_id={},
        )
        self.homework.learning_in_public_cap = 0
        self.homework.homework_url_field = False
        self.homework.time_spent_lectures_field = False
        self.homework.time_spent_homework_field = False
        self.homework.save(update_fields=[
            'learning_in_public_cap', 'homework_url_field',
            'time_spent_lectures_field', 'time_spent_homework_field',
        ])
        self.client.get(self.unit_url)
        draft = HomeworkDraft.objects.get(user=self.student)

        response = self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token), 'homework_step': 'review',
            'revision': str(draft.revision), 'intent': 'submit',
        })

        self.assertEqual(response.status_code, 302)
        previous.refresh_from_db()
        self.assertEqual(previous.homework_link, 'https://example.com/project')
        self.assertEqual(previous.learning_in_public_links, ['https://example.com/progress'])
        self.assertEqual(previous.time_spent_lectures, 1.5)
        self.assertEqual(previous.time_spent_homework, 2.0)

    def test_negative_time_value_keeps_draft_and_does_not_submit(self):
        self.homework.time_spent_lectures_field = True
        self.homework.save(update_fields=['time_spent_lectures_field'])
        self.client.get(self.unit_url)
        draft = HomeworkDraft.objects.get(user=self.student)

        response = self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token), 'homework_step': 'review',
            'revision': str(draft.revision), 'intent': 'submit',
            'final_time_spent_lectures': '-0.5',
        })

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'non-negative number of hours', status_code=400)
        self.assertFalse(Submission.objects.filter(homework=self.homework).exists())

    def test_no_deadline_homework_renders_without_date_metadata(self):
        self.homework.due_date = None
        self.homework.save(update_fields=['due_date'])

        response = self.client.get(self.unit_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['homework_due_date_display'], '')
        self.assertNotContains(response, 'data-testid="homework-due-date"')

    def test_closed_no_deadline_homework_uses_closed_copy(self):
        self.homework.due_date = None
        self.homework.state = HomeworkState.CLOSED
        self.homework.save(update_fields=['due_date', 'state'])

        response = self.client.get(self.unit_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This homework is closed. Your saved answers are still available.')
        self.assertNotContains(response, 'deadline has passed')

    def test_one_answer_saves_without_submission_and_resumes(self):
        self.client.get(self.unit_url)
        self.assertFalse(completion_service.is_completed(self.student, self.unit))
        response = self.save_answer(self.mc_question.source_question_id, 0, '2')
        self.assertEqual(response.json(), {'revision': 1, 'saved': True})
        self.assertFalse(Submission.objects.filter(homework=self.homework).exists())
        self.assertFalse(completion_service.is_completed(self.student, self.unit))
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers,
            {'q1-lines': option_key('14')},
        )
        resumed = self.client.get(self.unit_url)
        self.assertEqual(resumed.context['stepper']['step'], 'q2-reflect')

    def test_step_navigation_preserves_cohort_query_on_unit_url(self):
        self.cohort.external_key = 'cohort-4'
        self.cohort.save(update_fields=['external_key'])
        CohortEnrollment.objects.create(user=self.student, cohort=self.cohort)
        page = self.client.get(f'{self.unit_url}?cohort=cohort-4&homework_step=q1-lines')
        self.assertEqual(
            page.context['stepper']['nav_steps'][2][1],
            f'{self.unit_url}?cohort=cohort-4&homework_step=q2-reflect',
        )
        draft = HomeworkDraft.objects.get(user=self.student)
        response = self.client.post(
            f'{self.unit_url}?cohort=cohort-4',
            {
                'assignment_key': f'aisl:homework:{self.homework.pk}',
                'draft_token': str(draft.token), 'homework_step': 'q1-lines',
                'revision': '0', 'answer': option_key('14'), 'next_step': 'q2-reflect',
            },
        )
        self.assertEqual(
            response['Location'],
            f'{self.unit_url}?cohort=cohort-4&homework_step=q2-reflect',
        )

    def test_selected_second_cohort_uses_its_own_stepper_draft_and_submission(self):
        self.cohort.external_key = 'older'
        self.cohort.save(update_fields=['external_key'])
        CohortEnrollment.objects.create(user=self.student, cohort=self.cohort)
        today = timezone.localdate()
        selected = Cohort.objects.create(
            course=self.course, name='Selected cohort', external_key='selected',
            start_date=today - datetime.timedelta(days=2),
            end_date=today + datetime.timedelta(days=60),
        )
        CohortEnrollment.objects.create(user=self.student, cohort=selected)
        second_homework = Homework.objects.create(
            cohort=selected, slug='hw1', title='Selected cohort homework',
            content_id=self.unit.content_id, stepper_enabled=True,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        for question in self.homework.questions.all():
            Question.objects.create(
                homework=second_homework,
                source_question_id=question.source_question_id,
                text=question.text, question_type=question.question_type,
                answer_type=question.answer_type,
                possible_answers=question.possible_answers,
                correct_answer=question.correct_answer,
            )

        selected_url = f'{self.unit_url}?cohort=selected'
        response = self.client.get(selected_url)
        self.assertEqual(response.context['homework'], second_homework)
        draft = HomeworkDraft.objects.get(
            user=self.student, assignment_key=f'aisl:homework:{second_homework.pk}',
        )
        saved = self.client.post(
            f'/api/homework-reader/drafts/{second_homework.pk}/questions/q1-lines',
            {'draft_token': str(draft.token), 'revision': '0',
             'answer': option_key('14')},
        )
        self.assertEqual(saved.json(), {'revision': 1, 'saved': True})
        submitted = self.client.post(selected_url, {
            'assignment_key': f'aisl:homework:{second_homework.pk}',
            'draft_token': str(draft.token), 'homework_step': 'review',
            'revision': '1', 'intent': 'submit', 'final_homework_link': '',
        })
        self.assertEqual(submitted.status_code, 302)
        self.assertTrue(Submission.objects.filter(
            homework=second_homework, student=self.student,
        ).exists())
        self.assertFalse(Submission.objects.filter(
            homework=self.homework, student=self.student,
        ).exists())

    def test_unowned_cohort_query_cannot_open_or_seed_private_homework(self):
        today = timezone.localdate()
        unowned = Cohort.objects.create(
            course=self.course, name='Private cohort', external_key='unowned',
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=30),
        )
        private_homework = Homework.objects.create(
            cohort=unowned, slug='private-homework', title='Private homework',
            content_id=self.unit.content_id, stepper_enabled=True,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        Question.objects.create(
            homework=private_homework, source_question_id='private-answer',
            text='Private question', question_type='FF',
        )

        response = self.client.get(f'{self.unit_url}?cohort=unowned')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Private question', response.content.decode())
        self.assertFalse(HomeworkDraft.objects.filter(
            user=self.student, assignment_key=f'aisl:homework:{private_homework.pk}',
        ).exists())
        post = self.client.post(f'{self.unit_url}?cohort=unowned', {
            'assignment_key': f'aisl:homework:{private_homework.pk}',
            'homework_step': 'private-answer', 'answer': 'secret',
        })
        self.assertEqual(post.status_code, 404)
        self.assertFalse(Submission.objects.filter(
            homework=private_homework, student=self.student,
        ).exists())

    def test_save_rejects_stale_revision_and_question_from_another_assignment(self):
        self.client.get(self.unit_url)
        self.assertEqual(self.save_answer('q1-lines', 0, '1').json()['revision'], 1)
        self.assertEqual(self.save_answer('q1-lines', 0, '2').status_code, 409)
        wrong = self.client.post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/q999-other',
            {'revision': '1', 'answer': '1'},
        )
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers['q1-lines'], option_key('12'),
        )

    def test_clear_one_answer_keeps_other_saved_answer(self):
        self.client.get(self.unit_url)
        self.save_answer('q1-lines', 0, '2')
        self.save_answer('q2-reflect', 1, 'A detail')
        cleared = self.save_answer('q1-lines', 2, '')
        self.assertEqual(cleared.json()['revision'], 3)
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers,
            {'q2-reflect': 'A detail'},
        )

    def test_save_is_csrf_protected_and_deadline_gated(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.student)
        url = f'/api/homework-reader/drafts/{self.homework.pk}/questions/q1-lines'
        self.assertEqual(csrf_client.post(url, {'revision': '0', 'answer': '2'}).status_code, 403)
        self.homework.due_date = timezone.now() - datetime.timedelta(minutes=1)
        self.homework.save(update_fields=['due_date'])
        response = self.client.post(url, {'revision': '0', 'answer': '2'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(HomeworkDraft.objects.filter(user=self.student).exists())

    def test_review_submits_complete_snapshot_through_existing_service(self):
        self.client.get(self.unit_url)
        self.save_answer('q1-lines', 0, '2')
        old_token = str(HomeworkDraft.objects.get(user=self.student).token)
        review = self.client.get(f'{self.unit_url}?homework_step=review')
        self.assertContains(review, 'Share your work.')
        self.assertContains(review, 'No answer saved')
        response = self.client.post(
            self.unit_url,
            {
                'assignment_key': f'aisl:homework:{self.homework.pk}',
                'draft_token': str(HomeworkDraft.objects.get(user=self.student).token),
                'homework_step': 'review', 'revision': '1',
                'intent': 'submit',
                'final_homework_link': 'https://github.com/example/solution',
            },
        )
        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.homework_link, 'https://github.com/example/solution')
        self.assertEqual(submission.answers.get(question=self.mc_question).answer_text, '2')
        self.assertFalse(HomeworkDraft.objects.filter(user=self.student).exists())
        stale = self.client.post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/q1-lines',
            {'draft_token': old_token, 'revision': '0', 'answer': '1'},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(HomeworkDraft.objects.filter(user=self.student).exists())

    def test_rejected_submit_keeps_draft_without_creating_submission(self):
        self.client.get(self.unit_url)
        self.save_answer('q1-lines', 0, '2')
        self.homework.due_date = timezone.now() - datetime.timedelta(minutes=1)
        self.homework.save(update_fields=['due_date'])
        response = self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(HomeworkDraft.objects.get(user=self.student).token),
            'homework_step': 'review', 'revision': '1', 'intent': 'submit',
            'final_homework_link': '',
        })
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'deadline', status_code=403)
        self.assertEqual(
            HomeworkDraft.objects.get(user=self.student).answers,
            {'q1-lines': option_key('14')},
        )
        self.assertFalse(Submission.objects.filter(homework=self.homework).exists())

    def test_existing_submission_seeds_draft_without_changing_submission(self):
        self.client.get(self.unit_url)
        self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(HomeworkDraft.objects.get(user=self.student).token),
            'homework_step': 'review', 'revision': '0', 'intent': 'submit',
            'final_homework_link': '',
        })
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        Answer.objects.create(
            submission=submission, question=self.mc_question, answer_text='1',
        )
        opened = self.client.get(f'{self.unit_url}?homework_step=q1-lines')
        self.assertIn(option_key('12'), checked_radio_values(opened))
        self.assertEqual(submission.answers.get(question=self.mc_question).answer_text, '1')

    def test_stale_all_in_one_post_still_submits_and_clears_stepper_draft(self):
        self.client.get(self.unit_url)
        self.save_answer('q1-lines', 0, '1')
        self.assertFalse(completion_service.is_completed(self.student, self.unit))

        response = self.client.post(self.unit_url, {
            f'answer_{self.mc_question.pk}': '2',
            'homework_link': 'https://github.com/example/legacy',
        })

        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.answers.get(question=self.mc_question).answer_text, '2')
        self.assertEqual(submission.homework_link, 'https://github.com/example/legacy')
        self.assertFalse(HomeworkDraft.objects.filter(user=self.student).exists())
        self.assertTrue(completion_service.is_completed(self.student, self.unit))

    def test_reordered_options_preserve_draft_choice_and_submit_current_index(self):
        self.client.get(self.unit_url)
        self.save_answer('q1-lines', 0, '2')
        saved_key = HomeworkDraft.objects.get(user=self.student).answers['q1-lines']

        self.mc_question.possible_answers = '16\n12\n14'
        self.mc_question.save(update_fields=['possible_answers'])

        reopened = self.client.get(f'{self.unit_url}?homework_step=q1-lines')
        self.assertIn(saved_key, checked_radio_values(reopened))
        self.assertEqual(HomeworkDraft.objects.get(user=self.student).answers['q1-lines'], saved_key)
        response = self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(HomeworkDraft.objects.get(user=self.student).token),
            'homework_step': 'review', 'revision': '1', 'intent': 'submit',
            'final_homework_link': '',
        })
        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.answers.get(question=self.mc_question).answer_text, '3')
