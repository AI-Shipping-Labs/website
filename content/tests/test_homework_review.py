"""Authored Question N order, Studio review titles, and student-answer markdown."""

import datetime
import uuid
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from content.models import Course, Module, Unit
from content.models.cohort import Cohort
from content.models.homework import (
    AnswerType,
    Homework,
    Question,
    QuestionType,
)
from content.services.homework_review import (
    format_review_heading,
    is_presentable_answer,
    render_student_answer_html,
    sort_authored_questions,
)
from content.services.homework_step_sections import question_heading_names
from content.services.homework_submissions import save_submission

User = get_user_model()


class QuestionHeadingNamesTest(SimpleTestCase):
    def test_remainder_strips_question_prefix_and_trailing_points(self):
        names = question_heading_names(
            'Intro\n\n'
            '## Question 1. Your Project Idea (2 points)\n'
            'What project would you like to build?\n'
            '```\n## Question 99. Example only\n```\n'
            '## Question 2. Starter Project\n'
            'Customize the starter.\n'
            '## Question 3\n'
            'Only a number.\n'
        )

        self.assertEqual(names[1], 'Your Project Idea')
        self.assertEqual(names[2], 'Starter Project')
        self.assertEqual(names[3], '')
        self.assertNotIn(99, names)


class ReviewHeadingFormatTest(SimpleTestCase):
    def test_points_pluralization_and_no_double_prefix(self):
        cases = [
            (1, 'Your Project Idea', 2, 'Question 1: Your Project Idea (2 points)'),
            (2, 'Starter Project', 1, 'Question 2: Starter Project (1 point)'),
            (3, 'Optional', 0, 'Question 3: Optional (0 points)'),
            (1, 'How many lines?', 1, 'Question 1: How many lines? (1 point)'),
            (1, 'Question 1: How many lines?', 1, 'Question 1: How many lines? (1 point)'),
            (1, 'Question 1. How many lines?', 1, 'Question 1. How many lines? (1 point)'),
            (1, '', 1, 'Question 1 (1 point)'),
        ]
        for number, name, score, expected in cases:
            with self.subTest(name=name, score=score):
                self.assertEqual(format_review_heading(number, name, score), expected)


class AuthoredQuestionOrderTest(SimpleTestCase):
    def test_q_numbers_win_over_creation_pk(self):
        later = SimpleNamespace(pk=1, source_question_id='q2-starter')
        earlier = SimpleNamespace(pk=2, source_question_id='q1-project-idea')

        ordered = sort_authored_questions([later, earlier])

        self.assertEqual(
            [question.source_question_id for question in ordered],
            ['q1-project-idea', 'q2-starter'],
        )


class StudentAnswerMarkdownTest(SimpleTestCase):
    def test_renders_emphasis_lists_links_and_fenced_code(self):
        html = render_student_answer_html(
            'I **shipped** it.\n\n'
            '- first\n- second\n\n'
            '[docs](https://example.com/guide)\n\n'
            '```\nprint("hi")\n```'
        )

        self.assertIn('<strong>shipped</strong>', html)
        self.assertIn('<ul>', html)
        self.assertIn('first', html)
        self.assertIn('href="https://example.com/guide"', html)
        self.assertIn('<pre>', html)
        self.assertIn('print', html)

    def test_strips_script_tags_and_skips_mermaid_and_event_widgets(self):
        html = render_student_answer_html(
            'Safe **ok** <script>alert(1)</script>\n\n'
            '```mermaid\ngraph TD\nA-->B\n```\n\n'
            '```eventwidget\nslug: v0-claim\n```'
        )

        self.assertNotIn('<script', html.lower())
        self.assertNotIn('alert(1)', html)
        self.assertIn('<strong>ok</strong>', html)
        self.assertNotIn('class="mermaid"', html)
        self.assertNotIn('data-event-widget', html)

    def test_blank_text_is_empty(self):
        self.assertEqual(render_student_answer_html(''), '')
        self.assertEqual(render_student_answer_html('   '), '')

    def test_presentable_only_for_nonempty_free_text(self):
        ff = SimpleNamespace(question_type=QuestionType.FREE_FORM)
        fl = SimpleNamespace(question_type=QuestionType.FREE_FORM_LONG)
        mc = SimpleNamespace(question_type=QuestionType.MULTIPLE_CHOICE)
        filled = SimpleNamespace(answer_text='hello')
        blank = SimpleNamespace(answer_text='  ')

        self.assertTrue(is_presentable_answer(ff, filled))
        self.assertTrue(is_presentable_answer(fl, filled))
        self.assertFalse(is_presentable_answer(mc, filled))
        self.assertFalse(is_presentable_answer(ff, blank))
        self.assertFalse(is_presentable_answer(ff, None))
        self.assertFalse(is_presentable_answer(None, filled))


class MemberReviewMarkdownTest(TestCase):
    def test_review_summary_renders_markdown_and_not_other_students(self):
        slug = f'review-md-{uuid.uuid4().hex[:8]}'
        course = Course.objects.create(
            title='Review markdown', slug=slug, status='published', required_level=0,
        )
        module = Module.objects.create(course=course, title='Module', slug='module')
        today = timezone.now().date()
        cohort = Cohort.objects.create(
            course=course, name='Current',
            start_date=today - datetime.timedelta(days=2),
            end_date=today + datetime.timedelta(days=30),
        )
        content_id = uuid.uuid4()
        unit = Unit.objects.create(
            module=module, title='Homework', slug='homework', kind='homework',
            content_id=content_id,
            homework='Intro\n\n## Question 1. Build\nDescribe what you shipped.\n',
        )
        homework = Homework.objects.create(
            cohort=cohort, slug='homework', title='Homework',
            content_id=content_id, stepper_enabled=True,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        question = Question.objects.create(
            homework=homework, source_question_id='q1-build',
            text='Describe what you shipped.',
            question_type=QuestionType.FREE_FORM_LONG, answer_type=AnswerType.ANY,
        )
        student = User.objects.create_user(
            email=f'student-{slug}@test.com', password='testpass',
        )
        other = User.objects.create_user(
            email=f'other-{slug}@test.com', password='testpass',
        )
        save_submission(
            homework, student, homework_link='https://github.com/student/ship',
            answers_by_question_id={
                question.pk: 'I **shipped** it.\n\n- demo\n- writeup',
            },
        )
        save_submission(
            homework, other, homework_link='https://github.com/other/ship',
            answers_by_question_id={
                question.pk: 'Secret other-student answer xyzzy-1944',
            },
        )
        client = Client()
        client.force_login(student)

        response = client.get(f'{unit.get_absolute_url()}/review')

        self.assertContains(response, 'data-testid="homework-review-summary"')
        self.assertContains(response, '<strong>shipped</strong>')
        self.assertContains(response, '<ul>')
        self.assertNotContains(response, 'I **shipped** it.')
        self.assertNotContains(response, 'Secret other-student answer xyzzy-1944')
        self.assertNotContains(response, other.email)
