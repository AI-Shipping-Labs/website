"""Unit tests for the ``paren_count`` template filter (issue #597).

The filter renders a parenthesized count suffix only when the count is a
positive integer. For zero, ``None``, or non-numeric input it returns an
empty string so the surrounding label reads cleanly without an empty
``(0)`` suffix.

Covers both the direct Python call and the rendered template output, so
we catch regressions whether someone calls the function or uses
``{{ value|paren_count }}`` in a template.
"""

from django.template import Context, Template
from django.test import SimpleTestCase

from content.templatetags.tag_filters import paren_count


class ParenCountFilterTest(SimpleTestCase):
    """Direct calls into the filter function."""

    def test_zero_returns_empty_string(self):
        self.assertEqual(paren_count(0), "")

    def test_positive_integer_returns_space_paren_n(self):
        self.assertEqual(paren_count(3), " (3)")

    def test_large_positive_integer(self):
        self.assertEqual(paren_count(1234), " (1234)")

    def test_one_returns_paren_one(self):
        self.assertEqual(paren_count(1), " (1)")

    def test_none_returns_empty_string(self):
        self.assertEqual(paren_count(None), "")

    def test_negative_integer_returns_empty_string(self):
        # Negatives are treated as "nothing to count" — never render
        # ``Label (-2)``.
        self.assertEqual(paren_count(-2), "")

    def test_non_numeric_string_returns_empty_string(self):
        self.assertEqual(paren_count("abc"), "")

    def test_empty_string_returns_empty_string(self):
        self.assertEqual(paren_count(""), "")

    def test_numeric_string_is_coerced(self):
        # Django filter inputs are often strings from query params.
        self.assertEqual(paren_count("5"), " (5)")

    def test_zero_string_returns_empty_string(self):
        self.assertEqual(paren_count("0"), "")

    def test_float_is_truncated(self):
        # ``int(3.7)`` is 3 — acceptable behavior for a counter filter.
        self.assertEqual(paren_count(3.7), " (3)")

    def test_object_that_is_not_numeric(self):
        self.assertEqual(paren_count(object()), "")


class ParenCountTemplateRenderTest(SimpleTestCase):
    """End-to-end render through Django's template engine."""

    def _render(self, value):
        tpl = Template(
            "{% load tag_filters %}Label{{ value|paren_count }}"
        )
        return tpl.render(Context({"value": value}))

    def test_template_renders_label_alone_when_zero(self):
        self.assertEqual(self._render(0), "Label")

    def test_template_renders_label_alone_when_none(self):
        self.assertEqual(self._render(None), "Label")

    def test_template_renders_label_with_paren_count_when_positive(self):
        self.assertEqual(self._render(4), "Label (4)")

    def test_template_renders_label_alone_when_non_numeric(self):
        self.assertEqual(self._render("not-a-number"), "Label")

    def test_template_handles_length_filter_chain(self):
        # The most common in-template usage: ``items|length|paren_count``.
        tpl = Template(
            "{% load tag_filters %}Enrolled{{ items|length|paren_count }}"
        )
        self.assertEqual(
            tpl.render(Context({"items": []})),
            "Enrolled",
        )
        self.assertEqual(
            tpl.render(Context({"items": ["a", "b", "c"]})),
            "Enrolled (3)",
        )
