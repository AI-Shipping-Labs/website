"""Homework time-spent fields keep "not provided" distinct from 0 hours (#1923)."""

import importlib
import sys
from html.parser import HTMLParser

from community_base.homework_steps.models import HomeworkDraft
from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase

from content.models.homework import HomeworkState, Submission
from content.services.homework_submissions import save_submission
from content.tests.test_homework_submission_view import HomeworkUnitSetupMixin
from content.utils.hours import format_hours, normalize_hours_text

_MIGRATION = importlib.import_module('content.migrations.0085_normalize_homework_draft_hours')

LECTURES_LABEL = 'Time spent on lectures (hours) (optional)'
HOMEWORK_LABEL = 'Time spent on homework (hours) (optional)'


class InputAttrsParser(HTMLParser):
    """Collect each ``<input>``'s attributes, keyed by its ``id``."""

    def __init__(self):
        super().__init__()
        self.inputs = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id'):
            self.inputs[attrs['id']] = attrs


def time_inputs(response):
    parser = InputAttrsParser()
    parser.feed(response.content.decode())
    return (
        parser.inputs['final-time_spent_lectures'],
        parser.inputs['final-time_spent_homework'],
    )


class FormatHoursTest(SimpleTestCase):
    def test_none_stays_empty(self):
        self.assertEqual(format_hours(None), '')

    def test_whole_numbers_drop_the_decimal_part(self):
        self.assertEqual(format_hours(0.0), '0')
        self.assertEqual(format_hours(-0.0), '0')
        self.assertEqual(format_hours(2.0), '2')
        self.assertEqual(format_hours(1000000.0), '1000000')
        self.assertEqual(format_hours(1e20), '100000000000000000000')

    def test_largest_float_formats_without_raising(self):
        formatted = format_hours(sys.float_info.max)

        self.assertEqual(len(formatted), 309)
        self.assertTrue(formatted.isdigit())

    def test_fractions_keep_exact_digits_without_trailing_zeros(self):
        self.assertEqual(format_hours(1.5), '1.5')
        self.assertEqual(format_hours(0.25), '0.25')
        self.assertEqual(format_hours(0.1), '0.1')
        self.assertEqual(format_hours(1e-05), '0.00001')

    def test_normalize_rewrites_numeric_strings_only(self):
        self.assertEqual(normalize_hours_text('2.0'), '2')
        self.assertEqual(normalize_hours_text('0.0'), '0')
        self.assertEqual(normalize_hours_text('1.50'), '1.5')
        self.assertEqual(normalize_hours_text(' 3 '), '3')
        self.assertEqual(normalize_hours_text('2'), '2')

    def test_normalize_leaves_blank_invalid_and_negative_values(self):
        for raw in (
            '', '   ', 'abc', '-1.0', 'NaN', 'Infinity', None, 2.0,
            '1e400', '1E400', '1e4300', '1e999999999',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_hours_text(raw), raw)


class TimeSpentStepperTest(HomeworkUnitSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.homework.stepper_enabled = True
        self.homework.homework_url_field = True
        self.homework.time_spent_lectures_field = True
        self.homework.time_spent_homework_field = True
        self.homework.save(update_fields=[
            'stepper_enabled', 'homework_url_field',
            'time_spent_lectures_field', 'time_spent_homework_field',
        ])
        self.client.force_login(self.student)
        self.review_url = f'{self.unit_url}?homework_step=review'

    def submit(self, **final_fields):
        self.client.get(self.unit_url)
        draft = HomeworkDraft.objects.get(user=self.student)
        data = {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token), 'homework_step': 'review',
            'revision': str(draft.revision), 'intent': 'submit',
            'final_homework_link': 'https://github.com/student/project',
        }
        data.update({f'final_{key}': value for key, value in final_fields.items()})
        return self.client.post(self.unit_url, data)

    def accept(self, lectures, homework):
        return save_submission(
            self.homework, self.student,
            homework_link='https://github.com/student/project',
            time_spent_lectures=lectures,
            time_spent_homework=homework,
            answers_by_question_id={},
        )

    def close_homework(self):
        self.homework.state = HomeworkState.CLOSED
        self.homework.save(update_fields=['state'])

    def accepted_rows(self, response):
        return dict(
            (field.label, value)
            for field, value in response.context['stepper']['accepted_field_rows']
        )

    def test_first_visit_inputs_are_empty_without_autofill_or_placeholder(self):
        lectures, homework = time_inputs(self.client.get(self.review_url))

        for attrs in (lectures, homework):
            self.assertEqual(attrs['value'], '')
            self.assertEqual(attrs['autocomplete'], 'off')
            self.assertNotIn('placeholder', attrs)

    def test_null_submission_renders_empty_inputs(self):
        self.accept(None, None)

        lectures, homework = time_inputs(self.client.get(self.review_url))

        self.assertEqual((lectures['value'], homework['value']), ('', ''))

    def test_zero_and_whole_hours_render_without_decimal_part(self):
        self.accept(0.0, 2.0)

        lectures, homework = time_inputs(self.client.get(self.review_url))

        self.assertEqual((lectures['value'], homework['value']), ('0', '2'))

    def test_fractional_and_large_hours_render_canonically(self):
        self.accept(0.25, 1000000.0)

        lectures, homework = time_inputs(self.client.get(self.review_url))

        self.assertEqual((lectures['value'], homework['value']), ('0.25', '1000000'))

    def test_closed_null_submission_rows_say_no_answer_saved(self):
        self.accept(None, None)
        self.close_homework()

        response = self.client.get(self.review_url)

        self.assertEqual(self.accepted_rows(response), {
            'Homework URL': 'https://github.com/student/project',
            LECTURES_LABEL: '',
            HOMEWORK_LABEL: '',
        })
        self.assertContains(
            response, f'<span class="font-medium">{LECTURES_LABEL}:</span> No answer saved',
        )
        self.assertContains(
            response, f'<span class="font-medium">{HOMEWORK_LABEL}:</span> No answer saved',
        )

    def test_closed_zero_submission_row_shows_zero(self):
        self.accept(0.0, 1.5)
        self.close_homework()

        response = self.client.get(self.review_url)

        self.assertContains(response, f'<span class="font-medium">{LECTURES_LABEL}:</span> 0</p>')
        self.assertContains(response, f'<span class="font-medium">{HOMEWORK_LABEL}:</span> 1.5</p>')
        self.assertFalse(response.context['stepper']['has_pending_changes'])

    def test_blank_fields_submit_and_save_null(self):
        response = self.submit(time_spent_lectures='', time_spent_homework='')

        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertIsNone(submission.time_spent_lectures)
        self.assertIsNone(submission.time_spent_homework)

    def test_explicit_zero_saves_zero_not_null(self):
        response = self.submit(time_spent_lectures='0', time_spent_homework='3')

        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.time_spent_lectures, 0.0)
        self.assertIsNotNone(submission.time_spent_lectures)
        self.assertEqual(submission.time_spent_homework, 3.0)
        lectures, homework = time_inputs(self.client.get(self.review_url))
        self.assertEqual((lectures['value'], homework['value']), ('0', '3'))

    def test_clearing_a_saved_value_and_resubmitting_saves_null(self):
        self.accept(None, 2.0)
        lectures, homework = time_inputs(self.client.get(self.review_url))
        self.assertEqual(homework['value'], '2')

        response = self.submit(time_spent_lectures='', time_spent_homework='')

        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertIsNone(submission.time_spent_homework)
        lectures, homework = time_inputs(self.client.get(self.review_url))
        self.assertEqual(homework['value'], '')

    def test_non_numeric_value_is_rejected_and_nothing_saved(self):
        response = self.submit(time_spent_lectures='', time_spent_homework='abc')

        self.assertContains(
            response, 'Time spent on homework must be a non-negative number of hours.',
            status_code=400,
        )
        self.assertFalse(Submission.objects.filter(homework=self.homework).exists())


class NormalizeDraftHoursMigrationTest(HomeworkUnitSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.homework.stepper_enabled = True
        self.homework.homework_url_field = True
        self.homework.time_spent_lectures_field = True
        self.homework.time_spent_homework_field = True
        self.homework.save(update_fields=[
            'stepper_enabled', 'homework_url_field',
            'time_spent_lectures_field', 'time_spent_homework_field',
        ])
        self.key = f'aisl:homework:{self.homework.pk}'

    def test_old_format_draft_no_longer_shows_pending_changes(self):
        save_submission(
            self.homework, self.student,
            homework_link='https://github.com/student/project',
            time_spent_lectures=None, time_spent_homework=2.0,
            answers_by_question_id={},
        )
        HomeworkDraft.objects.create(
            user=self.student, assignment_key=self.key, answers={},
            final_fields={
                'homework_link': 'https://github.com/student/project',
                'time_spent_lectures': '',
                'time_spent_homework': '2.0',
            },
        )
        self.client.force_login(self.student)
        review_url = f'{self.unit_url}?homework_step=review'
        before = self.client.get(review_url)
        self.assertTrue(before.context['stepper']['has_pending_changes'])

        _MIGRATION.normalize_draft_hours(django_apps, None)

        after = self.client.get(review_url)
        self.assertFalse(after.context['stepper']['has_pending_changes'])
        self.assertNotContains(after, 'data-testid="homework-pending-draft-status"')

    def test_only_numeric_aisl_homework_values_change_and_rerun_is_noop(self):
        aisl = HomeworkDraft.objects.create(
            user=self.student, assignment_key=self.key, answers={},
            final_fields={
                'homework_link': '2.0',
                'time_spent_lectures': 'abc',
                'time_spent_homework': '0.0',
            },
        )
        blank = HomeworkDraft.objects.create(
            user=self.student, assignment_key='aisl:homework:999999', answers={},
            final_fields={'time_spent_lectures': '1e4300', 'time_spent_homework': '-1.0'},
        )
        other = HomeworkDraft.objects.create(
            user=self.student, assignment_key='dtc:homework:1', answers={},
            final_fields={'time_spent_lectures': '2.0'},
        )

        _MIGRATION.normalize_draft_hours(django_apps, None)
        _MIGRATION.normalize_draft_hours(django_apps, None)

        aisl.refresh_from_db()
        blank.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(aisl.final_fields, {
            'homework_link': '2.0',
            'time_spent_lectures': 'abc',
            'time_spent_homework': '0',
        })
        self.assertEqual(
            blank.final_fields, {'time_spent_lectures': '1e4300', 'time_spent_homework': '-1.0'},
        )
        self.assertEqual(other.final_fields, {'time_spent_lectures': '2.0'})
