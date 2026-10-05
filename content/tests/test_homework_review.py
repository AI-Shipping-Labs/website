"""Authored Question N order and Studio review titles -- issue #1893."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from content.services.homework_review import (
    format_review_heading,
    sort_authored_questions,
)
from content.services.homework_step_sections import question_heading_names


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
