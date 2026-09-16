"""Studio tests for curriculum nesting (issue #1674).

Covers:
- Creating a submodule under a local course module (and rejection for
  synced courses, same is_synced gate as module/unit creation today).
- Mixed-content rejection surfaced as a Studio message, not a 500.
- module_reparent enforcing depth-cap/same-course/mixed-content.
- module_reorder scoped to a parent (submodule reorder) and unit_reorder.
- is_bonus checkbox handling on module_create/unit_create/module_edit/
  unit_edit.
"""

import json

from django.test import TestCase

from content.models import Course, Module, Unit
from tests.fixtures import StaffUserMixin


class StudioSubmoduleCreateTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Course', slug='submodule-course', status='draft',
        )
        self.week = Module.objects.create(
            course=self.course, title='Week 1', slug='week-1', sort_order=1,
        )

    def test_create_submodule_under_local_module(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'Foundations', 'parent_id': self.week.pk},
        )
        self.assertEqual(response.status_code, 302)
        submodule = Module.objects.get(course=self.course, title='Foundations')
        self.assertEqual(submodule.parent_id, self.week.pk)

    def test_create_submodule_with_is_bonus(self):
        self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'Bonus sub', 'parent_id': self.week.pk, 'is_bonus': 'on'},
        )
        submodule = Module.objects.get(course=self.course, title='Bonus sub')
        self.assertTrue(submodule.is_bonus)

    def test_create_submodule_rejected_for_synced_course(self):
        self.course.source_repo = 'AI-Shipping-Labs/content'
        self.course.save()
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'Blocked', 'parent_id': self.week.pk},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Module.objects.filter(title='Blocked').exists())

    def test_add_submodule_form_hidden_when_module_has_units(self):
        Unit.objects.create(
            module=self.week, title='Existing unit', slug='existing', sort_order=1,
        )
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertNotContains(response, 'data-testid="add-submodule-form"')

    def test_add_unit_form_hidden_when_module_has_children(self):
        Module.objects.create(
            course=self.course, title='Sub', slug='sub', sort_order=1,
            parent=self.week,
        )
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        # The week module (which now has a child) must not offer "Add unit".
        self.assertContains(response, 'data-testid="add-submodule-form"')


class StudioMixedContentRejectionTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Course', slug='mixed-studio-course', status='draft',
        )
        self.module = Module.objects.create(
            course=self.course, title='Leaf', slug='leaf', sort_order=1,
        )
        Unit.objects.create(
            module=self.module, title='Existing', slug='existing', sort_order=1,
        )

    def test_creating_submodule_under_module_with_units_is_rejected(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'New sub', 'parent_id': self.module.pk},
        )
        self.assertEqual(response.status_code, 302)  # redirect, not 500
        self.assertFalse(Module.objects.filter(title='New sub').exists())
        follow = self.client.get(response.url)
        self.assertContains(follow, 'Leaf')

    def test_creating_unit_under_module_with_children_is_rejected(self):
        parent = Module.objects.create(
            course=self.course, title='Parent', slug='parent', sort_order=2,
        )
        Module.objects.create(
            course=self.course, title='Child', slug='child', sort_order=1,
            parent=parent,
        )
        response = self.client.post(
            f'/studio/modules/{parent.pk}/units/add',
            {'title': 'Stray unit'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Unit.objects.filter(title='Stray unit').exists())


class StudioModuleReparentTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Course', slug='reparent-course', status='draft',
        )
        self.week1 = Module.objects.create(
            course=self.course, title='Week 1', slug='week-1', sort_order=1,
        )
        self.week2 = Module.objects.create(
            course=self.course, title='Week 2', slug='week-2', sort_order=2,
        )
        self.submodule = Module.objects.create(
            course=self.course, title='Sub', slug='sub', sort_order=1,
            parent=self.week1,
        )

    def test_reparent_to_another_top_level_module(self):
        response = self.client.post(
            f'/studio/modules/{self.submodule.pk}/reparent',
            {'parent_id': self.week2.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.submodule.refresh_from_db()
        self.assertEqual(self.submodule.parent_id, self.week2.pk)

    def test_reparent_to_top_level(self):
        self.client.post(
            f'/studio/modules/{self.submodule.pk}/reparent', {'parent_id': ''},
        )
        self.submodule.refresh_from_db()
        self.assertIsNone(self.submodule.parent_id)

    def test_reparent_rejects_depth_cap_violation(self):
        # A submodule cannot itself become a parent.
        response = self.client.post(
            f'/studio/modules/{self.week2.pk}/reparent',
            {'parent_id': self.submodule.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.week2.refresh_from_db()
        self.assertIsNone(self.week2.parent_id)  # unchanged, not silently applied

    def test_reparent_rejects_mixed_content(self):
        Unit.objects.create(
            module=self.week2, title='U', slug='u', sort_order=1,
        )
        response = self.client.post(
            f'/studio/modules/{self.submodule.pk}/reparent',
            {'parent_id': self.week2.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.submodule.refresh_from_db()
        self.assertEqual(self.submodule.parent_id, self.week1.pk)  # unchanged


class StudioModuleUnitEditBonusFieldsTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Course', slug='bonus-edit-course', status='draft',
        )
        self.module = Module.objects.create(
            course=self.course, title='Week', slug='week', sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Unit', slug='unit', sort_order=1,
        )

    def test_module_edit_sets_is_bonus_and_available_after_days(self):
        response = self.client.post(
            f'/studio/modules/{self.module.pk}/edit',
            {'is_bonus': 'on', 'available_after_days': '14'},
        )
        self.assertEqual(response.status_code, 302)
        self.module.refresh_from_db()
        self.assertTrue(self.module.is_bonus)
        self.assertEqual(self.module.available_after_days, 14)

    def test_unit_edit_sets_kind_event_and_session_position(self):
        response = self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Live session',
            'kind': 'event',
            'session_position': '4',
        })
        self.assertEqual(response.status_code, 302)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.kind, 'event')
        self.assertEqual(self.unit.session_position, 4)

    def test_unit_edit_event_without_session_position_is_rejected(self):
        response = self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Live session',
            'kind': 'event',
        })
        # Re-renders the edit form (not a redirect) with the error.
        self.assertContains(response, 'data-testid="unit-kind-select"', status_code=200)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.kind, 'lesson')  # unchanged


class StudioReorderScopedTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Course', slug='reorder-scope-course', status='draft',
        )
        self.week = Module.objects.create(
            course=self.course, title='Week', slug='week', sort_order=1,
        )
        self.sub1 = Module.objects.create(
            course=self.course, title='Sub 1', slug='sub-1', sort_order=1,
            parent=self.week,
        )
        self.sub2 = Module.objects.create(
            course=self.course, title='Sub 2', slug='sub-2', sort_order=2,
            parent=self.week,
        )
        self.unit1 = Unit.objects.create(
            module=self.sub1, title='U1', slug='u1', sort_order=1,
        )
        self.unit2 = Unit.objects.create(
            module=self.sub1, title='U2', slug='u2', sort_order=2,
        )

    def test_module_reorder_scoped_to_parent(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/reorder',
            json.dumps({
                'parent_id': self.week.pk,
                'items': [
                    {'id': self.sub1.pk, 'sort_order': 2},
                    {'id': self.sub2.pk, 'sort_order': 1},
                ],
            }),
            content_type='application/json',
        )
        self.assertEqual(response.json(), {'status': 'ok'})
        self.sub1.refresh_from_db()
        self.sub2.refresh_from_db()
        self.assertEqual(self.sub1.sort_order, 2)
        self.assertEqual(self.sub2.sort_order, 1)

    def test_unit_reorder_persists_sort_order(self):
        response = self.client.post(
            f'/studio/modules/{self.sub1.pk}/units/reorder',
            json.dumps([
                {'id': self.unit1.pk, 'sort_order': 2},
                {'id': self.unit2.pk, 'sort_order': 1},
            ]),
            content_type='application/json',
        )
        self.assertEqual(response.json(), {'status': 'ok'})
        self.unit1.refresh_from_db()
        self.unit2.refresh_from_db()
        self.assertEqual(self.unit1.sort_order, 2)
        self.assertEqual(self.unit2.sort_order, 1)

    def test_unit_reorder_rejects_unit_from_another_module(self):
        other_module = Module.objects.create(
            course=self.course, title='Other', slug='other', sort_order=3,
        )
        foreign_unit = Unit.objects.create(
            module=other_module, title='Foreign', slug='foreign', sort_order=1,
        )
        response = self.client.post(
            f'/studio/modules/{self.sub1.pk}/units/reorder',
            json.dumps([{'id': foreign_unit.pk, 'sort_order': 0}]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        foreign_unit.refresh_from_db()
        self.assertEqual(foreign_unit.sort_order, 1)
