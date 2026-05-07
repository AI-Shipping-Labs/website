"""Tests for ``Unit.effective_required_level`` (issue #465).

Acceptance criteria:

- Returns the unit's own ``required_level`` when set
- Falls back to ``Course.default_unit_required_level`` when the unit
  override is null
- Falls back to ``Course.required_level`` when both unit override and
  course default are null
- The course detail / catalog still reads ``Course.required_level`` (not
  the new field) for catalog gating, individual purchase, discussion
  link visibility, etc. — covered by the view tests separately.
"""

from django.test import TestCase, tag

from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
    LEVEL_REGISTERED,
)
from content.models import Course, Module, Unit


@tag('core')
class UnitEffectiveRequiredLevelTest(TestCase):
    """Resolution order: unit > course default > course required_level."""

    def _make_unit(
        self, *, course_required, course_default, unit_override,
    ):
        course = Course.objects.create(
            title='C', slug=f'c-{course_required}-{course_default}-{unit_override}',
            status='published', required_level=course_required,
            default_unit_required_level=course_default,
        )
        module = Module.objects.create(
            course=course, title='M', slug='m', sort_order=1,
        )
        unit = Unit.objects.create(
            module=module, title='U', slug='u',
            body='b', sort_order=1, required_level=unit_override,
        )
        return unit

    def test_falls_back_to_course_required_level_when_both_null(self):
        unit = self._make_unit(
            course_required=LEVEL_MAIN,
            course_default=None,
            unit_override=None,
        )
        self.assertEqual(unit.effective_required_level, LEVEL_MAIN)

    def test_uses_course_default_when_set(self):
        unit = self._make_unit(
            course_required=LEVEL_OPEN,
            course_default=LEVEL_REGISTERED,
            unit_override=None,
        )
        self.assertEqual(unit.effective_required_level, LEVEL_REGISTERED)

    def test_unit_override_wins_over_course_default(self):
        unit = self._make_unit(
            course_required=LEVEL_BASIC,
            course_default=LEVEL_REGISTERED,
            unit_override=LEVEL_OPEN,
        )
        self.assertEqual(unit.effective_required_level, LEVEL_OPEN)

    def test_unit_override_can_raise_above_course_default(self):
        unit = self._make_unit(
            course_required=LEVEL_OPEN,
            course_default=LEVEL_REGISTERED,
            unit_override=LEVEL_PREMIUM,
        )
        self.assertEqual(unit.effective_required_level, LEVEL_PREMIUM)

    def test_zero_unit_override_is_distinct_from_null(self):
        # ``required_level=0`` is a valid override (LEVEL_OPEN);
        # ``required_level=None`` triggers fallback. Without the
        # explicit ``is not None`` check in effective_required_level,
        # falsy 0 would slip through to the course default.
        unit = self._make_unit(
            course_required=LEVEL_BASIC,
            course_default=LEVEL_MAIN,
            unit_override=0,
        )
        self.assertEqual(unit.effective_required_level, 0)


@tag('core')
class CourseRequiredLevelDecouplingTest(TestCase):
    """``Course.required_level`` must stay independent of unit gating.

    A course flagged ``required_level=LEVEL_BASIC`` with
    ``default_unit_required_level=LEVEL_REGISTERED`` still surfaces as a
    Basic-tier perk in the catalog (``course.required_level`` drives the
    badge), but a free logged-in user can read the lessons because the
    unit gate is REGISTERED. This is the explicit "decouple catalog from
    unit gating" use case from the spec.
    """

    def test_course_required_level_unchanged_by_default_unit_field(self):
        course = Course.objects.create(
            title='C', slug='decoupling-c',
            status='published', required_level=LEVEL_BASIC,
            default_unit_required_level=LEVEL_REGISTERED,
        )
        # The catalog tier badge resolves from course.required_level,
        # not effective_required_level — assert the field on the course
        # itself is still BASIC.
        self.assertEqual(course.required_level, LEVEL_BASIC)
        self.assertEqual(
            course.default_unit_required_level, LEVEL_REGISTERED,
        )
