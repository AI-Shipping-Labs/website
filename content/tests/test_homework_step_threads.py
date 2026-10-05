"""Homework stepper Q&A thread identity (issue #1897).

Thread UUIDs are derived deterministically from (unit content identity,
public step slug), so re-syncs and cohorts never fork a thread; the map the
course unit view builds mounts the unit thread on ``intro`` and one derived
thread per question/learning-in-public/review step.
"""

import datetime
import uuid

from django.test import TestCase
from django.utils import timezone

from comments.threads import thread_owners
from content.models import Course, Module, Unit
from content.models.cohort import Cohort
from content.models.homework import (
    Homework,
    HomeworkStepThread,
    Question,
    QuestionType,
)
from content.services.homework_step_threads import (
    INTRO_STEP,
    LEARNING_IN_PUBLIC_SIDEBAR_TITLE,
    REVIEW_SIDEBAR_TITLE,
    REVIEW_STEP,
    ensure_homework_step_threads,
    homework_step_sidebar_title,
    mounted_content_id,
    step_page_url,
    step_slugs,
    step_thread_content_id,
    unit_has_stepper_homework,
)

UNIT_CONTENT_ID = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')


class StepThreadIdentityTest(TestCase):
    """Pure derivation rules: stable, per-step, cohort-free."""

    def test_same_unit_and_step_derive_the_same_uuid(self):
        first = step_thread_content_id(UNIT_CONTENT_ID, 'q2-reflect')
        second = step_thread_content_id(UNIT_CONTENT_ID, 'q2-reflect')
        self.assertEqual(first, second)
        self.assertEqual(first, step_thread_content_id(str(UNIT_CONTENT_ID), 'q2-reflect'))

    def test_different_steps_and_units_derive_different_uuids(self):
        reflect = step_thread_content_id(UNIT_CONTENT_ID, 'q2-reflect')
        review = step_thread_content_id(UNIT_CONTENT_ID, REVIEW_STEP)
        other_unit = step_thread_content_id(uuid.uuid4(), 'q2-reflect')
        self.assertEqual(len({reflect, review, other_unit}), 3)

    def test_intro_mounts_the_unit_thread(self):
        self.assertEqual(
            mounted_content_id(UNIT_CONTENT_ID, INTRO_STEP), UNIT_CONTENT_ID,
        )

    def test_question_step_mounts_its_derived_thread(self):
        self.assertEqual(
            mounted_content_id(UNIT_CONTENT_ID, 'q2-reflect'),
            step_thread_content_id(UNIT_CONTENT_ID, 'q2-reflect'),
        )


class StepperThreadSetupMixin:
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-threads-1897',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1',
        )
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Homework', slug='hw1', sort_order=1,
            kind='homework', content_id=UNIT_CONTENT_ID,
            homework='Intro prose.\n\n## Question 1. One\nAnswer one.\n'
                     '## Question 2. Two\nAnswer two.',
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='hw1', title='Homework',
            content_id=UNIT_CONTENT_ID, stepper_enabled=True,
            learning_in_public_cap=3,
        )
        Question.objects.create(
            homework=cls.homework, source_question_id='q1-first', text='First',
            question_type=QuestionType.FREE_FORM, answer_type='ANY',
        )
        Question.objects.create(
            homework=cls.homework, source_question_id='q2-second', text='Second',
            question_type=QuestionType.FREE_FORM, answer_type='ANY',
        )


class StepSlugsTest(StepperThreadSetupMixin, TestCase):
    def test_slugs_cover_questions_learning_in_public_and_review(self):
        self.assertEqual(
            step_slugs(self.homework),
            ['q1-first', 'q2-second', 'learning-in-public', REVIEW_STEP],
        )

    def test_learning_in_public_omitted_without_cap(self):
        self.homework.learning_in_public_cap = 0
        self.assertEqual(
            step_slugs(self.homework),
            ['q1-first', 'q2-second', REVIEW_STEP],
        )


class EnsureHomeworkStepThreadsTest(StepperThreadSetupMixin, TestCase):
    def test_maps_every_step_and_persists_one_row_per_step(self):
        mounted = ensure_homework_step_threads(self.unit, self.homework)

        self.assertEqual(
            list(mounted),
            ['q1-first', 'q2-second', 'learning-in-public', REVIEW_STEP, INTRO_STEP],
        )
        self.assertEqual(mounted[INTRO_STEP], str(UNIT_CONTENT_ID))
        self.assertEqual(
            mounted['q1-first'],
            str(step_thread_content_id(UNIT_CONTENT_ID, 'q1-first')),
        )
        self.assertEqual(HomeworkStepThread.objects.count(), 4)
        for slug in ('q1-first', 'q2-second', 'learning-in-public', REVIEW_STEP):
            self.assertTrue(
                HomeworkStepThread.objects.filter(
                    unit_content_id=UNIT_CONTENT_ID, step_slug=slug,
                ).exists(),
                slug,
            )

    def test_all_mounted_values_are_uuid_strings(self):
        """The map feeds a template attribute: every value must be a UUID
        string, including on the first render that creates the rows."""
        mounted = ensure_homework_step_threads(self.unit, self.homework)
        for slug, value in mounted.items():
            self.assertIsInstance(value, str, slug)
            uuid.UUID(value)

    def test_second_call_is_idempotent_and_keeps_uuids(self):
        first = ensure_homework_step_threads(self.unit, self.homework)
        second = ensure_homework_step_threads(self.unit, self.homework)
        self.assertEqual(first, second)
        self.assertEqual(HomeworkStepThread.objects.count(), 4)

    def test_removed_question_keeps_its_stored_thread(self):
        ensure_homework_step_threads(self.unit, self.homework)
        removed_slug = 'q1-first'
        question = Question.objects.get(
            homework=self.homework, source_question_id=removed_slug,
        )
        question.delete()

        ensure_homework_step_threads(self.unit, self.homework)

        self.assertTrue(
            HomeworkStepThread.objects.filter(
                unit_content_id=UNIT_CONTENT_ID, step_slug=removed_slug,
            ).exists(),
        )
        self.assertNotIn(removed_slug, ensure_homework_step_threads(
            self.unit, self.homework,
        ))

    def test_unit_without_content_id_maps_nothing(self):
        orphan = Unit.objects.create(
            module=self.module, title='No id homework', slug='hw-no-id',
            sort_order=2, kind='homework',
        )
        self.assertEqual(
            ensure_homework_step_threads(orphan, self.homework), {},
        )


class UnitHasStepperHomeworkTest(StepperThreadSetupMixin, TestCase):
    def test_true_for_stepper_homework_with_questions(self):
        self.assertTrue(unit_has_stepper_homework(self.unit))

    def test_false_when_no_homework_uses_the_stepper(self):
        other = Unit.objects.create(
            module=self.module, title='Other homework', slug='hw2',
            sort_order=2, kind='homework', content_id=uuid.uuid4(),
        )
        Homework.objects.create(
            cohort=self.cohort, slug='hw2', title='Other homework',
            content_id=other.content_id, stepper_enabled=False,
        )
        self.assertFalse(unit_has_stepper_homework(other))

    def test_false_for_non_homework_unit(self):
        lesson = Unit.objects.create(
            module=self.module, title='Lesson', slug='lesson-1',
            sort_order=3, kind='lesson', content_id=uuid.uuid4(),
        )
        self.assertFalse(unit_has_stepper_homework(lesson))


class SidebarTitleTest(StepperThreadSetupMixin, TestCase):
    def test_question_steps_use_their_authored_position(self):
        self.assertEqual(
            homework_step_sidebar_title(self.unit, 'q1-first',
                                        homework=self.homework),
            'Question 1',
        )
        self.assertEqual(
            homework_step_sidebar_title(self.unit, 'q2-second',
                                        homework=self.homework),
            'Question 2',
        )

    def test_fixed_step_labels(self):
        self.assertEqual(
            homework_step_sidebar_title(self.unit, 'learning-in-public',
                                        homework=self.homework),
            LEARNING_IN_PUBLIC_SIDEBAR_TITLE,
        )
        self.assertEqual(
            homework_step_sidebar_title(self.unit, REVIEW_STEP,
                                        homework=self.homework),
            REVIEW_SIDEBAR_TITLE,
        )
        self.assertEqual(
            homework_step_sidebar_title(self.unit, INTRO_STEP), 'Introduction',
        )

    def test_unknown_step_falls_back_to_the_slug(self):
        self.assertEqual(
            homework_step_sidebar_title(self.unit, 'q9-gone',
                                        homework=self.homework),
            'q9-gone',
        )


class StepPageUrlTest(StepperThreadSetupMixin, TestCase):
    def test_step_url_appends_the_slug_to_the_unit_path(self):
        self.assertEqual(
            step_page_url(self.unit, 'q1-first'),
            f'{self.unit.get_absolute_url()}/q1-first',
        )


class ThreadOwnerRegistrationTest(StepperThreadSetupMixin, TestCase):
    def test_step_threads_are_registered_non_cascade_owners(self):
        owner = next(
            item for item in thread_owners()
            if item.model is HomeworkStepThread
        )
        self.assertEqual(owner.content_id_field, 'content_id')
        self.assertFalse(owner.cascade_thread_delete)
