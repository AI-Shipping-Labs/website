"""AISL-only Unit validation across shared curriculum versions."""

from django.core.exceptions import ValidationError
from django.test import TestCase

from content.models import Course, Module, Unit


class MixedUnitValidationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(title='Course', slug='mixed-unit-1850')
        cls.parent = Module.objects.create(
            course=course, title='Week', slug='week', sort_order=1,
        )
        Module.objects.create(
            course=course, title='Child', slug='child', sort_order=1,
            parent=cls.parent,
        )

    def test_mixed_unit_error_precedes_event_kind_error(self):
        unit = Unit(
            module=self.parent, title='Event', slug='event', sort_order=1,
            kind='event', session_position=None,
        )

        with self.assertRaises(ValidationError) as caught:
            unit.full_clean()

        self.assertEqual(
            caught.exception.message_dict,
            {'module': [
                "Module 'Week' has child modules; it cannot also have direct units.",
            ]},
        )
