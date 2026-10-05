"""Per-step Q&A thread binding on homework stepper pages (issue #1897).

The Q&A partial mounts one ``data-content-id`` per page: the unit's own
thread on ``intro`` (and on every non-stepper surface), one derived step
thread on each question, learning-in-public, and review page.
"""

import datetime
import uuid
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Course, Module, Unit
from content.models.cohort import Cohort, CohortEnrollment
from content.models.homework import Homework, Question, QuestionType
from content.services.homework_step_threads import (
    ensure_homework_step_threads,
    step_thread_content_id,
)

User = get_user_model()

UNIT_CONTENT_ID = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')


class QaContentIdParser(HTMLParser):
    """Collect the ``data-content-id`` of every rendered Q&A thread."""

    def __init__(self):
        super().__init__()
        self.content_ids = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if 'qa-thread' in (attributes.get('class') or ''):
            content_id = attributes.get('data-content-id')
            if content_id:
                self.content_ids.append(content_id)


def mounted_qa_content_ids(response):
    parser = QaContentIdParser()
    parser.feed(response.content.decode())
    return parser.content_ids


class StepperQaBindingSetupMixin:
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member-1897@test.com', password='pw',
        )
        cls.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-qa-1897',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1',
        )
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        CohortEnrollment.objects.create(user=cls.member, cohort=cls.cohort)
        cls.unit = Unit.objects.create(
            module=cls.module, title='Homework', slug='hw1', sort_order=1,
            kind='homework', content_id=UNIT_CONTENT_ID,
            homework='Intro prose.\n\n## Question 1. One\nAnswer one.\n'
                     '## Question 2. Two\nAnswer two.\n\n'
                     '## Learning in Public\nShare if you like.',
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
        cls.base_path = cls.unit.get_absolute_url()
        cls.step_q1_id = str(step_thread_content_id(UNIT_CONTENT_ID, 'q1-first'))
        cls.step_q2_id = str(step_thread_content_id(UNIT_CONTENT_ID, 'q2-second'))
        cls.step_lip_id = str(
            step_thread_content_id(UNIT_CONTENT_ID, 'learning-in-public'),
        )
        cls.step_review_id = str(
            step_thread_content_id(UNIT_CONTENT_ID, 'review'),
        )

    @classmethod
    def threads_ready(cls):
        # Pre-create the step-thread rows so assertions never depend on
        # which call first persisted them.
        ensure_homework_step_threads(cls.unit, cls.homework)


class QuestionStepMountsItsOwnThreadTest(StepperQaBindingSetupMixin, TestCase):
    def test_question_step_mounts_its_derived_thread(self):
        self.client.force_login(self.member)
        response = self.client.get(f'{self.base_path}/q1-first?cohort=4')

        self.assertContains(response, 'data-testid="homework-stepper"')
        self.assertEqual(
            mounted_qa_content_ids(response), [self.step_q1_id],
        )
        # The spoiler bug: the unit thread (with its historical comments)
        # must not ride along on question pages.
        self.assertNotIn(str(UNIT_CONTENT_ID), mounted_qa_content_ids(response))

    def test_query_param_step_mounts_the_same_thread_as_the_path(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f'{self.base_path}?homework_step=q1-first&cohort=4',
        )

        self.assertContains(response, 'data-testid="homework-stepper"')
        self.assertEqual(
            mounted_qa_content_ids(response), [self.step_q1_id],
        )

    def test_first_mounted_thread_is_a_uuid_string_not_a_model(self):
        """First render creates the rows; the attribute must still be a UUID."""
        self.client.force_login(self.member)
        response = self.client.get(f'{self.base_path}/q2-second')

        self.assertEqual(
            mounted_qa_content_ids(response), [self.step_q2_id],
        )


class IntroAndFixedStepsTest(StepperQaBindingSetupMixin, TestCase):
    def test_intro_mounts_the_unit_thread(self):
        self.threads_ready()
        self.client.force_login(self.member)
        response = self.client.get(f'{self.base_path}/intro')

        self.assertEqual(
            mounted_qa_content_ids(response), [str(UNIT_CONTENT_ID)],
        )

    def test_review_mounts_its_derived_thread(self):
        self.threads_ready()
        self.client.force_login(self.member)
        response = self.client.get(f'{self.base_path}/review')

        self.assertEqual(
            mounted_qa_content_ids(response), [self.step_review_id],
        )

    def test_learning_in_public_mounts_its_derived_thread(self):
        self.threads_ready()
        self.client.force_login(self.member)
        response = self.client.get(f'{self.base_path}/learning-in-public')

        self.assertEqual(
            mounted_qa_content_ids(response), [self.step_lip_id],
        )

    def test_bare_unit_url_mounts_the_thread_of_the_landed_step(self):
        """First visit lands on intro; its Q&A is the unit thread, not the
        union of every step thread."""
        self.client.force_login(self.member)
        response = self.client.get(self.base_path)

        self.assertEqual(
            mounted_qa_content_ids(response), [str(UNIT_CONTENT_ID)],
        )

    def test_every_step_mounts_exactly_one_thread(self):
        self.threads_ready()
        self.client.force_login(self.member)
        for step, expected in (
            ('intro', str(UNIT_CONTENT_ID)),
            ('q1-first', self.step_q1_id),
            ('q2-second', self.step_q2_id),
            ('learning-in-public', self.step_lip_id),
            ('review', self.step_review_id),
        ):
            response = self.client.get(f'{self.base_path}/{step}')
            self.assertEqual(
                mounted_qa_content_ids(response), [expected],
                f'step {step}',
            )

    def test_cohort_param_does_not_fork_the_thread(self):
        self.threads_ready()
        self.client.force_login(self.member)
        with_cohort = self.client.get(f'{self.base_path}/q1-first?cohort=4')
        without_cohort = self.client.get(f'{self.base_path}/q1-first')
        self.assertContains(with_cohort, 'id="qa-section"')
        self.assertContains(without_cohort, 'id="qa-section"')
        self.assertEqual(
            mounted_qa_content_ids(with_cohort),
            mounted_qa_content_ids(without_cohort),
        )


class NonStepperSurfacesKeepUnitThreadTest(StepperQaBindingSetupMixin, TestCase):
    def test_all_questions_homework_mounts_one_unit_thread(self):
        self.homework.stepper_enabled = False
        self.homework.save(update_fields=['stepper_enabled'])
        self.client.force_login(self.member)
        response = self.client.get(self.base_path)

        self.assertContains(response, 'id="qa-section"')
        self.assertEqual(
            mounted_qa_content_ids(response), [str(UNIT_CONTENT_ID)],
        )

    def test_lesson_unit_mounts_the_unit_thread(self):
        lesson = Unit.objects.create(
            module=self.module, title='Lesson', slug='lesson-1', sort_order=5,
            kind='lesson', content_id=uuid.uuid4(),
        )
        self.client.force_login(self.member)
        response = self.client.get(lesson.get_absolute_url())

        self.assertEqual(
            mounted_qa_content_ids(response), [str(lesson.content_id)],
        )


class StepThreadRoundTripTest(StepperQaBindingSetupMixin, TestCase):
    def test_comment_posted_on_a_step_lives_only_under_its_uuid(self):
        self.threads_ready()
        self.client.force_login(self.member)
        post = self.client.post(
            f'/api/comments/{self.step_q1_id}',
            data='{"body": "I got 7944 for unstructured"}',
            content_type='application/json',
        )
        self.assertEqual(post.status_code, 201)

        listed = self.client.get(f'/api/comments/{self.step_q1_id}')
        self.assertIn('I got 7944 for unstructured', listed.content.decode())
        for other_id in (str(UNIT_CONTENT_ID), self.step_q2_id):
            other = self.client.get(f'/api/comments/{other_id}')
            self.assertEqual(other.json()['comments'], [])

    def test_step_pages_do_not_mount_the_q1_thread(self):
        """Storage is keyed by content_id, so a comment stored on q1 is
        invisible on any page that mounts a different thread UUID."""
        self.threads_ready()
        self.client.force_login(self.member)
        post = self.client.post(
            f'/api/comments/{self.step_q1_id}',
            data='{"body": "q1 only"}',
            content_type='application/json',
        )
        self.assertEqual(post.status_code, 201)
        for step in ('intro', 'q2-second', 'review'):
            response = self.client.get(f'{self.base_path}/{step}')
            # The mounted thread id differs, so the page's Q&A fetch can
            # never return the q1 comment; assert the mount itself.
            self.assertNotEqual(
                mounted_qa_content_ids(response), [self.step_q1_id],
                f'step {step}',
            )
