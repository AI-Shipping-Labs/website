"""Tests for shared Studio view utilities."""

from django.test import SimpleTestCase

from studio.utils import coerce_page_number


class CoercePageNumberTest(SimpleTestCase):
    """The shared pager clamp keeps stale Studio links renderable."""

    def test_missing_page_defaults_to_first(self):
        self.assertEqual(coerce_page_number(None, 3), 1)

    def test_non_integer_page_defaults_to_first(self):
        self.assertEqual(coerce_page_number('abc', 3), 1)

    def test_negative_page_clamps_to_first(self):
        self.assertEqual(coerce_page_number(-1, 3), 1)

    def test_zero_page_clamps_to_first(self):
        self.assertEqual(coerce_page_number(0, 3), 1)

    def test_valid_page_is_returned_unchanged(self):
        self.assertEqual(coerce_page_number(2, 3), 2)

    def test_overlarge_page_clamps_to_last(self):
        self.assertEqual(coerce_page_number(999, 3), 3)
