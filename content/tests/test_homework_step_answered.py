"""Answered-question checks in the homework step navigation (issue #1924)."""

from html.parser import HTMLParser

from community_base.homework_steps.models import HomeworkDraft
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from content.models.cohort import CohortEnrollment
from content.models.homework import (
    HomeworkState,
    Question,
    QuestionType,
)
from content.services.homework_step_reader import LEARNING_IN_PUBLIC_KEY, option_key
from content.services.homework_submissions import save_submission
from content.tests.test_homework_submission_view import HomeworkUnitSetupMixin


class StepNavParser(HTMLParser):
    """Collect each sidebar step link and each mobile step pill.

    Each link: href, anchor attrs, visible text, accessible ``name``
    (``aria-label`` when set, else the visible text), and the first
    ``data-lucide`` glyph (the sidebar marker, or a pill's check) with its
    attributes.
    """

    def __init__(self):
        super().__init__()
        self.steps = []
        self.pills = []
        self._region = None
        self._depth = 0
        self._link = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        testid = attributes.get('data-testid')
        if self._region is None and testid in ('homework-step-nav', 'homework-step-pills'):
            self._region = 'steps' if testid == 'homework-step-nav' else 'pills'
            self._region_tag = tag
            self._depth = 1
            return
        if self._region is None:
            return
        if tag == self._region_tag:
            self._depth += 1
        if tag == 'a':
            self._link = {'href': attributes.get('href'), 'attrs': attributes,
                          'text': '', 'marker': None, 'marker_attrs': {}}
        elif tag == 'i' and self._link is not None and self._link['marker'] is None:
            self._link['marker'] = attributes.get('data-lucide')
            self._link['marker_attrs'] = attributes

    def handle_endtag(self, tag):
        if self._region is None:
            return
        if tag == 'a' and self._link is not None:
            self._link['text'] = ' '.join(self._link['text'].split())
            self._link['name'] = self._link['attrs'].get('aria-label') or self._link['text']
            (self.steps if self._region == 'steps' else self.pills).append(self._link)
            self._link = None
        elif tag == self._region_tag:
            self._depth -= 1
            if self._depth == 0:
                self._region = None

    def handle_data(self, data):
        if self._link is not None:
            self._link['text'] += data


def parse_nav(response):
    parser = StepNavParser()
    parser.feed(response.content.decode())
    return parser


class HomeworkStepAnsweredTest(HomeworkUnitSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.homework.stepper_enabled = True
        cls.homework.learning_in_public_cap = 2
        cls.homework.save(update_fields=['stepper_enabled', 'learning_in_public_cap'])
        cls.checkbox_question = Question.objects.create(
            homework=cls.homework, source_question_id='q4-tools',
            text='Which tools did you use?', question_type=QuestionType.CHECKBOXES,
            possible_answers='Python\nSQL\nBash',
        )
        cls.long_question = Question.objects.create(
            homework=cls.homework, source_question_id='q5-essay',
            text='Describe your pipeline.', question_type=QuestionType.FREE_FORM_LONG,
        )

    def setUp(self):
        super().setUp()
        CohortEnrollment.objects.create(user=self.student, cohort=self.cohort)
        self.client.force_login(self.student)
        # The first GET creates the learner's draft.
        self.client.get(self.unit_url)

    def set_draft(self, answers):
        draft = HomeworkDraft.objects.get(user=self.student)
        draft.answers = answers
        draft.revision += 1
        draft.save()

    def get_step(self, step='review'):
        return self.client.get(f'{self.unit_url}?homework_step={step}')

    def answered_titles(self, response):
        return [
            step['name'].removesuffix(', answered')
            for step in parse_nav(response).steps
            if step['attrs'].get('data-homework-step-answered') == 'true'
        ]

    def test_nothing_saved_keeps_type_icons_and_plain_names(self):
        nav = parse_nav(self.get_step('intro'))

        self.assertEqual(
            [(step['name'], step['marker']) for step in nav.steps],
            [
                ('Introduction', 'book-open'),
                ('Question 1', 'help-circle'),
                ('Question 2', 'help-circle'),
                ('Question 3', 'help-circle'),
                ('Question 4', 'help-circle'),
                ('Question 5', 'help-circle'),
                ('Learning in Public', 'help-circle'),
                ('Review & submit', 'clipboard-check'),
            ],
        )
        for step in nav.steps:
            self.assertNotIn('data-homework-step-answered', step['attrs'])
        for pill in nav.pills:
            self.assertNotIn('data-homework-step-answered', pill['attrs'])
            self.assertIsNone(pill['marker'])

    def test_answered_rules_per_question_type(self):
        self.set_draft({
            'q1-lines': option_key('14'),
            'q2-reflect': '   \n ',
            'q3-canary': 'pk',
            'q4-tools': [],
            'q5-essay': 'Extract, load, transform.',
            LEARNING_IN_PUBLIC_KEY: '\n  \n',
        })
        response = self.get_step()
        self.assertEqual(response.context['homework_state'].value, 'draft')
        self.assertEqual(
            self.answered_titles(response), ['Question 1', 'Question 3', 'Question 5'],
        )

        self.set_draft({
            'q4-tools': [option_key('SQL')],
            LEARNING_IN_PUBLIC_KEY: 'https://example.com/post',
        })
        self.assertEqual(
            self.answered_titles(self.get_step()), ['Question 4', 'Learning in Public'],
        )

    def test_answered_row_uses_completion_marker_and_answered_name(self):
        self.set_draft({'q1-lines': option_key('14')})
        nav = parse_nav(self.get_step('intro'))
        answered = nav.steps[1]
        unanswered = nav.steps[2]

        self.assertEqual(answered['marker'], 'check-circle-2')
        self.assertEqual(answered['marker_attrs']['data-testid'], 'homework-step-answered')
        self.assertEqual(answered['marker_attrs']['aria-hidden'], 'true')
        self.assertEqual(answered['name'], 'Question 1, answered')
        self.assertEqual(answered['text'], 'Question 1')
        self.assertEqual(answered['attrs']['data-homework-step-answered'], 'true')
        self.assertEqual(unanswered['marker'], 'help-circle')
        self.assertEqual(unanswered['name'], 'Question 2')
        self.assertNotIn('aria-label', unanswered['attrs'])
        self.assertNotIn('data-testid', unanswered['marker_attrs'])

    def test_current_answered_step_keeps_current_highlight(self):
        self.set_draft({'q2-reflect': 'Chunking by headings'})
        nav = parse_nav(self.get_step('q2-reflect'))
        current = nav.steps[2]

        self.assertEqual(current['marker'], 'check-circle-2')
        self.assertEqual(current['name'], 'Question 2, answered')
        self.assertEqual(current['attrs']['aria-current'], 'step')
        self.assertEqual(current['attrs']['data-testid'], 'homework-step-current')

    def test_intro_and_review_never_checked_even_when_submitted(self):
        self.set_draft({'q1-lines': option_key('14'), 'q2-reflect': 'Done'})
        self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(HomeworkDraft.objects.get(user=self.student).token),
            'homework_step': 'review',
            'revision': str(HomeworkDraft.objects.get(user=self.student).revision),
            'intent': 'submit',
            'final_homework_link': 'https://github.com/student/project',
        })
        response = self.get_step('intro')
        self.assertEqual(response.context['homework_state'].value, 'submitted')
        nav = parse_nav(response)

        self.assertEqual(
            (nav.steps[0]['name'], nav.steps[0]['marker']), ('Introduction', 'book-open'),
        )
        self.assertEqual(
            (nav.steps[-1]['name'], nav.steps[-1]['marker']),
            ('Review & submit', 'clipboard-check'),
        )
        self.assertEqual(self.answered_titles(response), ['Question 1', 'Question 2'])

    def test_clearing_an_answer_removes_the_check(self):
        self.set_draft({'q2-reflect': 'A detail'})
        self.assertEqual(self.answered_titles(self.get_step()), ['Question 2'])
        draft = HomeworkDraft.objects.get(user=self.student)
        cleared = self.client.post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/q2-reflect',
            {'draft_token': str(draft.token), 'revision': str(draft.revision), 'answer': ''},
        )
        self.assertEqual(cleared.json()['saved'], True)

        nav = parse_nav(self.get_step('q3-canary'))
        self.assertEqual(nav.steps[2]['name'], 'Question 2')
        self.assertEqual(nav.steps[2]['marker'], 'help-circle')

    def test_closed_homework_checks_follow_accepted_submission_not_later_draft(self):
        save_submission(
            self.homework, self.student, homework_link='',
            answers_by_question_id={
                self.mc_question.pk: '2', self.ff_question.pk: 'Accepted',
            },
        )
        # A later unsubmitted draft answers Question 3 instead.
        self.set_draft({'q3-canary': 'Draft only'})
        self.homework.state = HomeworkState.CLOSED
        self.homework.save(update_fields=['state'])

        response = self.get_step()
        review_answered = [
            row['question_number'] for row in response.context['stepper']['review_display_rows']
            if str(row['answer']).strip()
        ]

        self.assertEqual(self.answered_titles(response), ['Question 1', 'Question 2'])
        self.assertEqual(review_answered, [1, 2])

    def test_mobile_pills_mark_answered_steps(self):
        self.set_draft({'q1-lines': option_key('14'), 'q3-canary': 'pk'})
        nav = parse_nav(self.get_step('q3-canary'))
        pills = nav.pills

        self.assertEqual([pill['text'] for pill in pills], [str(n) for n in range(1, 9)])
        self.assertEqual(pills[1]['attrs']['aria-label'], 'Step 2: Question 1, answered')
        self.assertEqual(pills[1]['attrs']['data-homework-step-answered'], 'true')
        self.assertEqual(pills[1]['marker'], 'check')
        self.assertEqual(pills[1]['marker_attrs']['aria-hidden'], 'true')
        self.assertEqual(pills[2]['attrs']['aria-label'], 'Step 3: Question 2')
        self.assertNotIn('data-homework-step-answered', pills[2]['attrs'])
        self.assertIsNone(pills[2]['marker'])
        current = pills[3]
        self.assertEqual(current['attrs']['aria-current'], 'step')
        self.assertEqual(current['attrs']['aria-label'], 'Step 4: Question 3, answered')
        self.assertEqual(current['marker'], 'check')
        self.assertEqual(pills[0]['attrs']['aria-label'], 'Step 1: Introduction')
        self.assertEqual(pills[-1]['attrs']['aria-label'], 'Step 8: Review & submit')


class HomeworkStepAnsweredQueryCountTest(HomeworkUnitSetupMixin, TestCase):
    def test_stepper_query_count_does_not_grow_with_answered_questions(self):
        self.homework.stepper_enabled = True
        self.homework.save(update_fields=['stepper_enabled'])
        Question.objects.filter(homework=self.homework, source_question_id='q3-canary').delete()
        self.client.force_login(self.student)
        url = f'{self.unit_url}?homework_step=review'
        self.client.get(url)
        draft = HomeworkDraft.objects.get(user=self.student)
        draft.answers = {'q1-lines': option_key('14'), 'q2-reflect': 'One'}
        draft.save()
        self.client.get(url)
        with CaptureQueriesContext(connection) as two_questions:
            response = self.client.get(url)
        # Read the count now: the next request resets connection.queries.
        two_question_count = len(two_questions)
        self.assertEqual(len(response.context['stepper']['review_display_rows']), 2)

        for number in range(3, 7):
            Question.objects.create(
                homework=self.homework, source_question_id=f'q{number}-extra',
                text=f'Extra {number}', question_type=QuestionType.FREE_FORM,
            )
        draft.answers.update({f'q{number}-extra': 'Yes' for number in range(3, 7)})
        draft.save()
        self.client.get(url)
        with self.assertNumQueries(two_question_count):
            response = self.client.get(url)
        self.assertEqual(len(response.context['stepper']['review_display_rows']), 6)
        self.assertEqual(len([
            step for step in parse_nav(response).steps
            if step['attrs'].get('data-homework-step-answered') == 'true'
        ]), 6)
