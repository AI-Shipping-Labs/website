"""Tests for curriculum nesting: Module.parent, Unit.kind, is_bonus (issue #1674).

Covers the model-level invariants and the reading-order/progress helpers
that don't require the sync pipeline or an HTTP client:

- Module.clean(): self-parent, depth cap, same-course, mixed content.
- Unit.clean(): kind='event' requires session_position; module-has
  -children rejection.
- non_bonus_units()/Course.total_units()/completed_units(): event units
  count, bonus modules/units are excluded from the denominator.
- Course.get_syllabus() / get_all_units_ordered(): depth-first order.
- Backward compatibility: an existing two-level course is unaffected.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from content.models import Course, Module, Unit, UserCourseProgress
from content.models.course import non_bonus_units
from content.services.course_units import (
    get_all_units_ordered,
    get_next_unit,
    get_prev_unit,
)

User = get_user_model()


class ModuleParentValidationTest(TestCase):
    """Module.clean() invariants (self-parent, depth cap, same-course)."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='course', status='published',
        )
        cls.other_course = Course.objects.create(
            title='Other', slug='other-course', status='published',
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )

    def test_self_parent_rejected(self):
        module = Module.objects.create(
            course=self.course, title='Solo', slug='solo', sort_order=2,
        )
        module.parent = module
        with self.assertRaises(ValidationError):
            module.full_clean()

    def test_three_level_depth_rejected(self):
        submodule = Module.objects.create(
            course=self.course, title='Sub', slug='sub', sort_order=1,
            parent=self.week,
        )
        grandchild = Module.objects.create(
            course=self.course, title='Grandchild', slug='grandchild',
            sort_order=1,
        )
        grandchild.parent = submodule
        with self.assertRaises(ValidationError):
            grandchild.full_clean()

    def test_parent_from_different_course_rejected(self):
        module = Module.objects.create(
            course=self.other_course, title='Foreign', slug='foreign',
            sort_order=1,
        )
        module.parent = self.week
        with self.assertRaises(ValidationError):
            module.full_clean()

    def test_valid_submodule_passes(self):
        submodule = Module(
            course=self.course, title='Foundations', slug='foundations',
            sort_order=1, parent=self.week,
        )
        submodule.full_clean()  # should not raise


class ModuleSlugSiblingScopedUniquenessTest(TestCase):
    """Slug uniqueness is per sibling group, not course-wide (issue #1674,
    corrected during #1675 grooming: real Maven content repeats submodule
    slugs like "homework" across weeks)."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='slug-scope-course', status='published',
        )

    def test_two_top_level_modules_cannot_share_a_slug(self):
        Module.objects.create(
            course=self.course, title='Week 1', slug='week', sort_order=1,
        )
        with self.assertRaises(ValidationError):
            Module(
                course=self.course, title='Week 1 again', slug='week', sort_order=2,
            ).full_clean()

    def test_two_submodules_under_the_same_parent_cannot_share_a_slug(self):
        parent = Module.objects.create(
            course=self.course, title='Week 1', slug='week-1', sort_order=1,
        )
        Module.objects.create(
            course=self.course, title='Homework', slug='homework', sort_order=1,
            parent=parent,
        )
        with self.assertRaises(ValidationError):
            Module(
                course=self.course, title='Homework again', slug='homework',
                sort_order=2, parent=parent,
            ).full_clean()

    def test_submodules_under_different_parents_may_share_a_slug(self):
        """The real-world case this correction unblocks: "Homework" (or
        "(Overview)") repeated as a submodule slug under multiple weeks."""
        week1 = Module.objects.create(
            course=self.course, title='Week 1', slug='week-1', sort_order=1,
        )
        week3 = Module.objects.create(
            course=self.course, title='Week 3', slug='week-3', sort_order=2,
        )
        hw1 = Module(
            course=self.course, title='Homework', slug='homework', sort_order=1,
            parent=week1,
        )
        hw1.full_clean()
        hw1.save()
        hw3 = Module(
            course=self.course, title='Homework', slug='homework', sort_order=1,
            parent=week3,
        )
        hw3.full_clean()  # should not raise
        hw3.save()
        self.assertNotEqual(hw1.pk, hw3.pk)

    def test_top_level_module_and_submodule_may_share_a_slug(self):
        """A top-level module's slug lives in a different sibling group
        (parent IS NULL) than any submodule's, so no collision."""
        top_level = Module.objects.create(
            course=self.course, title='Homework', slug='homework', sort_order=1,
        )
        week1 = Module.objects.create(
            course=self.course, title='Week 1', slug='week-1', sort_order=2,
        )
        submodule = Module(
            course=self.course, title='Homework', slug='homework', sort_order=1,
            parent=week1,
        )
        submodule.full_clean()  # should not raise
        submodule.save()
        self.assertNotEqual(top_level.pk, submodule.pk)


class MixedContentValidationTest(TestCase):
    """A module holds either child modules or direct units, never both."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='mixed-course', status='published',
        )

    def test_unit_rejected_under_module_with_children(self):
        parent = Module.objects.create(
            course=self.course, title='Week', slug='week', sort_order=1,
        )
        Module.objects.create(
            course=self.course, title='Sub', slug='sub', sort_order=1,
            parent=parent,
        )
        unit = Unit(module=parent, title='Stray', slug='stray', sort_order=1)
        with self.assertRaises(ValidationError) as ctx:
            unit.full_clean()
        self.assertIn('Week', str(ctx.exception))

    def test_child_module_rejected_under_module_with_units(self):
        parent = Module.objects.create(
            course=self.course, title='Leaf', slug='leaf', sort_order=1,
        )
        Unit.objects.create(
            module=parent, title='Existing', slug='existing', sort_order=1,
        )
        child = Module(
            course=self.course, title='New sub', slug='new-sub',
            sort_order=1, parent=parent,
        )
        with self.assertRaises(ValidationError) as ctx:
            child.full_clean()
        self.assertIn('Leaf', str(ctx.exception))


class UnitKindValidationTest(TestCase):
    """Unit.clean(): kind='event' requires a positive session_position."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='kind-course', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )

    def test_event_kind_without_session_position_rejected(self):
        unit = Unit(
            module=self.module, title='Session', slug='session',
            sort_order=1, kind='event', session_position=None,
        )
        with self.assertRaises(ValidationError):
            unit.full_clean()

    def test_event_kind_with_positive_session_position_passes(self):
        unit = Unit(
            module=self.module, title='Session', slug='session',
            sort_order=1, kind='event', session_position=4,
        )
        unit.full_clean()  # should not raise

    def test_lesson_kind_is_default(self):
        unit = Unit.objects.create(
            module=self.module, title='Lesson', slug='lesson', sort_order=1,
        )
        self.assertEqual(unit.kind, 'lesson')
        self.assertFalse(unit.is_bonus)


class ThreeLevelFixtureMixin:
    """A three-level course: Week 1 -> {Foundations, Bonus topic} -> units,
    plus a two-level Week 2 with a lesson and an event unit."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp', status='published',
            required_level=0,
        )
        cls.week1 = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
            available_after_days=0,
        )
        cls.foundations = Module.objects.create(
            course=cls.course, title='Foundations', slug='foundations',
            sort_order=1, parent=cls.week1,
        )
        cls.bonus_sub = Module.objects.create(
            course=cls.course, title='Bonus topic', slug='bonus-topic',
            sort_order=2, parent=cls.week1, is_bonus=True,
        )
        cls.u_intro = Unit.objects.create(
            module=cls.foundations, title='Intro', slug='intro', sort_order=1,
        )
        cls.u_deep_dive = Unit.objects.create(
            module=cls.foundations, title='Deep dive', slug='deep-dive',
            sort_order=2, is_bonus=True,
        )
        cls.u_extra = Unit.objects.create(
            module=cls.bonus_sub, title='Extra', slug='extra', sort_order=1,
        )
        cls.week2 = Module.objects.create(
            course=cls.course, title='Week 2', slug='week-2', sort_order=2,
            available_after_days=7,
        )
        cls.u_solo = Unit.objects.create(
            module=cls.week2, title='Solo lesson', slug='solo-lesson',
            sort_order=1,
        )
        cls.u_event = Unit.objects.create(
            module=cls.week2, title='Live Q&A', slug='live-qa', sort_order=2,
            kind='event', session_position=4,
        )


class ProgressDenominatorTest(ThreeLevelFixtureMixin, TestCase):
    """total_units()/completed_units() include events, exclude bonus."""

    def test_total_units_excludes_bonus_module_and_bonus_unit(self):
        # Units: intro, deep-dive(bonus), extra(under bonus module),
        # solo-lesson, live-qa(event). Non-bonus: intro, solo-lesson,
        # live-qa = 3.
        self.assertEqual(self.course.total_units(), 3)

    def test_non_bonus_units_filter_excludes_bonus_event_unit(self):
        bonus_event = Unit.objects.create(
            module=self.bonus_sub, title='Bonus session', slug='bonus-session',
            sort_order=2, kind='event', session_position=9,
        )
        qs = non_bonus_units(Unit.objects.filter(module__course=self.course))
        self.assertNotIn(bonus_event, list(qs))

    def test_completing_all_required_units_reaches_full_progress(self):
        user = User.objects.create_user(email='learner@test.com', password='pw')
        now = timezone.now()
        for unit in (self.u_intro, self.u_solo, self.u_event):
            UserCourseProgress.objects.create(user=user, unit=unit, completed_at=now)
        self.assertEqual(self.course.completed_units(user), 3)
        self.assertEqual(self.course.completed_units(user), self.course.total_units())

    def test_completing_only_bonus_units_does_not_count(self):
        user = User.objects.create_user(email='learner2@test.com', password='pw')
        UserCourseProgress.objects.create(
            user=user, unit=self.u_deep_dive, completed_at=timezone.now(),
        )
        self.assertEqual(self.course.completed_units(user), 0)


class ReadingOrderTest(ThreeLevelFixtureMixin, TestCase):
    """Depth-first order: top-level modules, leaf units or child units."""

    def test_get_all_units_ordered_is_depth_first(self):
        ordered = get_all_units_ordered(self.course)
        self.assertEqual(
            [u.pk for u in ordered],
            [
                self.u_intro.pk, self.u_deep_dive.pk, self.u_extra.pk,
                self.u_solo.pk, self.u_event.pk,
            ],
        )

    def test_next_prev_cross_submodule_boundary(self):
        # Last unit of Foundations submodule -> first unit of the Bonus
        # topic submodule (both children of Week 1).
        self.assertEqual(get_next_unit(self.course, self.u_deep_dive), self.u_extra)
        self.assertEqual(get_prev_unit(self.course, self.u_extra), self.u_deep_dive)

    def test_bonus_lesson_follows_required_lesson_even_when_its_sort_order_is_earlier(self):
        later_required = Unit.objects.create(
            module=self.foundations, title='Visual summary', slug='visual-summary',
            sort_order=3,
        )
        self.assertEqual(get_next_unit(self.course, self.u_intro), later_required)
        self.assertEqual(get_next_unit(self.course, later_required), self.u_deep_dive)
        self.assertEqual(get_prev_unit(self.course, self.u_deep_dive), later_required)

    def test_next_crosses_top_level_module_boundary(self):
        self.assertEqual(get_next_unit(self.course, self.u_extra), self.u_solo)

    def test_last_unit_has_no_next(self):
        self.assertIsNone(get_next_unit(self.course, self.u_event))

    def test_get_syllabus_prefetches_tree_in_fixed_query_count(self):
        # One query per level (top-level modules, their children, the
        # children's units, the top-level leaves' own units) regardless
        # of how many modules/units the tree has — not one per module.
        with self.assertNumQueries(4):
            modules = list(self.course.get_syllabus())
            for module in modules:
                list(module.children.all())
                for child in module.children.all():
                    list(child.units.all())
                list(module.units.all())


class BackwardCompatibilityTest(TestCase):
    """Every existing (pre-#1674-shape) course renders/behaves identically."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Legacy Course', slug='legacy-course', status='published',
            required_level=0,
        )
        cls.module1 = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        cls.module2 = Module.objects.create(
            course=cls.course, title='Module 2', slug='module-2', sort_order=2,
        )
        cls.u1 = Unit.objects.create(
            module=cls.module1, title='Lesson 1', slug='lesson-1', sort_order=1,
        )
        cls.u2 = Unit.objects.create(
            module=cls.module1, title='Lesson 2', slug='lesson-2', sort_order=2,
        )
        cls.u3 = Unit.objects.create(
            module=cls.module2, title='Lesson 3', slug='lesson-3', sort_order=1,
        )

    def test_every_row_has_null_parent_and_default_kind(self):
        for module in (self.module1, self.module2):
            self.assertIsNone(module.parent_id)
            self.assertFalse(module.is_bonus)
            self.assertIsNone(module.available_after_days)
        for unit in (self.u1, self.u2, self.u3):
            self.assertEqual(unit.kind, 'lesson')
            self.assertFalse(unit.is_bonus)
            self.assertIsNone(unit.session_position)

    def test_total_units_unaffected(self):
        self.assertEqual(self.course.total_units(), 3)

    def test_reading_order_unaffected(self):
        ordered = get_all_units_ordered(self.course)
        self.assertEqual(
            [u.pk for u in ordered], [self.u1.pk, self.u2.pk, self.u3.pk],
        )

    def test_get_syllabus_returns_flat_two_level_tree(self):
        modules = list(self.course.get_syllabus())
        self.assertEqual([m.pk for m in modules], [self.module1.pk, self.module2.pk])
        for module in modules:
            self.assertEqual(list(module.children.all()), [])

    def test_get_next_unit_for_matches_pre_1674_behaviour(self):
        user = User.objects.create_user(email='legacy@test.com', password='pw')
        self.assertEqual(self.course.get_next_unit_for(user), self.u1)
        UserCourseProgress.objects.create(
            user=user, unit=self.u1, completed_at=timezone.now(),
        )
        self.assertEqual(self.course.get_next_unit_for(user), self.u2)
