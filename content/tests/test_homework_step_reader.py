"""AISL homework reader integration on an explicitly activated assignment."""

import datetime

from community_base.homework_steps.models import HomeworkDraft
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from content.models.homework import Answer, Submission
from content.services.homework_step_sections import validate_question_bindings
from content.tests.test_homework_submission_view import HomeworkUnitSetupMixin


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

    def test_one_answer_saves_without_submission_and_resumes(self):
        self.client.get(self.unit_url)
        response = self.save_answer(self.mc_question.source_question_id, 0, '2')
        self.assertEqual(response.json(), {'revision': 1, 'saved': True})
        self.assertFalse(Submission.objects.filter(homework=self.homework).exists())
        self.assertEqual(HomeworkDraft.objects.get(user=self.student).answers, {'q1-lines': '2'})
        resumed = self.client.get(self.unit_url)
        self.assertEqual(resumed.context['stepper']['step'], 'q2-reflect')

    def test_step_navigation_preserves_cohort_query_on_unit_url(self):
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
                'revision': '0', 'answer': '2', 'next_step': 'q2-reflect',
            },
        )
        self.assertEqual(
            response['Location'],
            f'{self.unit_url}?cohort=cohort-4&homework_step=q2-reflect',
        )

    def test_save_rejects_stale_revision_and_question_from_another_assignment(self):
        self.client.get(self.unit_url)
        self.assertEqual(self.save_answer('q1-lines', 0, '1').json()['revision'], 1)
        self.assertEqual(self.save_answer('q1-lines', 0, '2').status_code, 409)
        wrong = self.client.post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/q999-other',
            {'revision': '1', 'answer': '1'},
        )
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(HomeworkDraft.objects.get(user=self.student).answers['q1-lines'], '1')

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
        self.assertEqual(HomeworkDraft.objects.get(user=self.student).answers, {'q1-lines': '2'})
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
        self.assertContains(opened, 'value="1" checked')
        self.assertEqual(submission.answers.get(question=self.mc_question).answer_text, '1')
