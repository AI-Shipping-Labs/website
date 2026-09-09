"""Regression tests for canonical worker task formatting (issue #1537)."""

from unittest.mock import patch

from django.test import SimpleTestCase

from jobs import task_format


class TaskValueFormattingTest(SimpleTestCase):
    def test_none_and_strings_are_returned_without_pretty_printing(self):
        self.assertEqual(task_format.format_task_value(None), "")
        self.assertEqual(task_format.format_task_value("line one\nline two"), "line one\nline two")

    def test_other_values_use_the_existing_pretty_print_contract(self):
        value = {"second": 2, "first": 1}
        with patch("jobs.task_format.pprint.pformat", return_value="formatted") as pformat:
            result = task_format.format_task_value(value)

        self.assertEqual(result, "formatted")
        self.assertEqual(pformat.call_count, 1)
        self.assertEqual(pformat.call_args.args, (value,))
        self.assertEqual(
            pformat.call_args.kwargs,
            {"width": 100, "sort_dicts": False},
        )

    def test_pretty_print_failure_falls_back_to_repr(self):
        value = {"task": "value"}
        with patch("jobs.task_format.pprint.pformat", side_effect=ValueError("bad value")):
            result = task_format.format_task_value(value)

        self.assertEqual(result, repr(value))


class TracebackDetectionTest(SimpleTestCase):
    def test_detects_leading_and_embedded_tracebacks(self):
        self.assertTrue(task_format.looks_like_traceback("Traceback (most recent call last):\nRuntimeError: leading"))
        self.assertTrue(
            task_format.looks_like_traceback(
                "worker preamble\nTraceback (most recent call last):\nRuntimeError: embedded"
            )
        )

    def test_rejects_plain_text_and_non_strings(self):
        self.assertFalse(task_format.looks_like_traceback("RuntimeError: plain"))
        self.assertFalse(task_format.looks_like_traceback(None))
        self.assertFalse(task_format.looks_like_traceback({"error": "plain"}))


class ErrorSummaryTest(SimpleTestCase):
    def test_traceback_uses_last_nonblank_line(self):
        text = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n\nRuntimeError: nope'
        self.assertEqual(task_format.extract_error_summary(text), "RuntimeError: nope")

    def test_plain_error_uses_first_nonblank_line(self):
        self.assertEqual(
            task_format.extract_error_summary("  first line  \nsecond line"),
            "first line",
        )

    def test_empty_values_use_the_canonical_placeholder(self):
        expected = task_format.NO_ERROR_DETAILS_PLACEHOLDER
        self.assertEqual(task_format.extract_error_summary(None), expected)
        self.assertEqual(task_format.extract_error_summary(""), expected)
        self.assertEqual(task_format.extract_error_summary(" \n\t"), expected)

    def test_long_summary_is_exactly_160_characters_with_ellipsis(self):
        expected = "X" * 157 + "..."
        self.assertEqual(task_format.extract_error_summary("X" * 500), expected)
        self.assertEqual(len(expected), task_format.ERROR_SUMMARY_MAX_LENGTH)
