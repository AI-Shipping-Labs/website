"""Small factories for content tests."""

from types import SimpleNamespace

from content.models import Course, Module, Unit


def make_course_with_units(*, modules=None, **course_overrides):
    course_fields = {
        "title": "Test Course",
        "slug": "test-course",
        "status": "published",
    }
    course_fields.update(course_overrides)
    course = Course.objects.create(**course_fields)

    if modules is None:
        modules = [
            {
                "title": "Module 1",
                "slug": "module-1",
                "sort_order": 1,
                "units": [
                    {"title": "Lesson 1", "slug": "lesson-1", "sort_order": 1},
                ],
            },
        ]

    module_records = []
    unit_records = []
    modules_by_slug = {}
    units_by_slug = {}
    for module_spec in modules:
        module_fields = dict(module_spec)
        unit_specs = module_fields.pop("units", [])
        module = Module.objects.create(course=course, **module_fields)
        module_records.append(module)
        modules_by_slug[module.slug] = module
        for unit_spec in unit_specs:
            unit = Unit.objects.create(module=module, **unit_spec)
            unit_records.append(unit)
            units_by_slug[unit.slug] = unit

    return SimpleNamespace(
        course=course,
        modules=module_records,
        units=unit_records,
        modules_by_slug=modules_by_slug,
        units_by_slug=units_by_slug,
    )
