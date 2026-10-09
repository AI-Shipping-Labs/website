"""Per-step Q&A thread binding on homework stepper pages (#1897, #1925).

The Q&A partial mounts one ``data-content-id`` per page: one derived step
thread on ``intro`` and on each question page; nothing on
``learning-in-public``; on ``review`` only the read-only unit-thread archive
when it has comments. Non-stepper surfaces keep the unit's own thread.
"""

import datetime
import uuid
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from comments.models import Comment
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
        self.read_only_flags = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if 'qa-thread' in (attributes.get('class') or ''):
            content_id = attributes.get('data-content-id')
            if content_id:
                self.content_ids.append(content_id)
                self.read_only_flags.append(attributes.get('data-read-only'))


def mounted_qa_content_ids(response):
    parser = QaContentIdParser()
    parser.feed(response.content.decode())
    return parser.content_ids


def mounted_qa_read_only_flags(response):
    parser = QaContentIdParser()
    parser.feed(response.content.decode())
    return parser.read_only_flags


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
        cls.step_intro_id = str(step_thread_content_id(UNIT_CONTENT_ID, 'intro'))
        cls.step_q1_id = str(step_thread_content_id(UNIT_CONTENT_ID, 'q1-first'))
        cls.step_q2_id = str(step_thread_content_id(UNIT_CONTENT_ID, 'q2-second'))
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
    """Issue #1925: intro owns a thread; review/LIP mount no live thread."""

    def test_intro_mounts_its_derived_thread_not_the_unit_thread(self):
        self.threads_ready()
        self.client.force_login(self.member)
        response = self.client.get(f'{self.base_path}/intro')

        self.assertEqual(mounted_qa_content_ids(response), [self.step_intro_id])
        self.assertContains(response, 'id="qa-new-question"')

    def test_bare_unit_url_lands_on_intro_and_mounts_the_intro_thread(self):
        """First visit lands on intro; the unit thread (pre-#1897 spoilers)
        is not mounted there."""
        self.client.force_login(self.member)
        response = self.client.get(self.base_path)

        self.assertEqual(mounted_qa_content_ids(response), [self.step_intro_id])

    def test_cohort_and_legacy_query_forms_mount_the_same_intro_thread(self):
        self.threads_ready()
        self.client.force_login(self.member)
        for url in (
            f'{self.base_path}/intro?cohort=4',
            f'{self.base_path}?homework_step=intro',
            f'{self.base_path}?homework_step=intro&cohort=4',
        ):
            response = self.client.get(url)
            self.assertEqual(
                mounted_qa_content_ids(response), [self.step_intro_id], url,
            )

    def test_learning_in_public_renders_no_qa_at_all(self):
        self.threads_ready()
        Comment.objects.create(
            content_id=UNIT_CONTENT_ID, user=self.member, body='Unit spoiler',
        )
        self.client.force_login(self.member)
        response = self.client.get(f'{self.base_path}/learning-in-public')

        self.assertContains(response, 'data-testid="homework-stepper"')
        self.assertEqual(mounted_qa_content_ids(response), [])
        self.assertNotContains(response, 'id="qa-section"')
        self.assertNotContains(response, 'Questions &amp; Answers')
        self.assertNotContains(response, 'id="qa-new-question')
        self.assertNotContains(response, 'data-testid="homework-qa-archive"')

    def test_question_steps_mount_their_own_threads(self):
        self.threads_ready()
        self.client.force_login(self.member)
        for step, expected in (
            ('q1-first', self.step_q1_id),
            ('q2-second', self.step_q2_id),
        ):
            response = self.client.get(f'{self.base_path}/{step}')
            self.assertEqual(
                mounted_qa_content_ids(response), [expected], f'step {step}',
            )
            self.assertContains(response, 'id="qa-new-question"')

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


class ReviewArchiveTest(StepperQaBindingSetupMixin, TestCase):
    """Review & submit: no live Q&A, read-only unit-thread archive."""

    def _review(self):
        self.client.force_login(self.member)
        return self.client.get(f'{self.base_path}/review')

    def test_no_archive_markup_when_unit_thread_has_no_visible_comments(self):
        self.threads_ready()
        Comment.objects.create(
            content_id=UNIT_CONTENT_ID, user=self.member, body='Hidden',
            hidden_at=timezone.now(),
        )
        # A comment on the review step thread UUID is not the unit thread.
        Comment.objects.create(
            content_id=self.step_review_id, user=self.member, body='Stranded',
        )
        response = self._review()

        self.assertContains(response, 'data-testid="homework-stepper"')
        self.assertEqual(mounted_qa_content_ids(response), [])
        self.assertNotContains(response, 'id="qa-section"')
        self.assertNotContains(response, 'Earlier homework discussion')
        self.assertNotContains(response, 'Questions &amp; Answers')
        self.assertNotContains(response, 'id="qa-new-question')

    def test_archive_lists_unit_thread_read_only_with_visible_count(self):
        self.threads_ready()
        first = Comment.objects.create(
            content_id=UNIT_CONTENT_ID, user=self.member, body='Q5 is 42?',
        )
        Comment.objects.create(
            content_id=UNIT_CONTENT_ID, user=self.member, body='Yes',
            parent=first,
        )
        Comment.objects.create(
            content_id=UNIT_CONTENT_ID, user=self.member, body='Q6 is 7?',
        )
        Comment.objects.create(
            content_id=UNIT_CONTENT_ID, user=self.member, body='Hidden one',
            hidden_at=timezone.now(),
        )
        response = self._review()

        archive = ArchiveParser.parse(response)
        self.assertEqual(archive.wrapper_id, 'qa-section')
        self.assertEqual(archive.summary, 'Earlier homework discussion (2)')
        # Collapsed by default, rendered through the accordion owner.
        self.assertFalse(archive.details_open)
        self.assertEqual(
            archive.thread_attrs.get('data-content-id'), str(UNIT_CONTENT_ID),
        )
        self.assertEqual(archive.thread_attrs.get('data-read-only'), 'true')
        self.assertEqual(archive.thread_attrs.get('id'), 'qa-section-archive')
        self.assertIn(
            'Questions posted before each step had its own Q&A.',
            archive.helper_text,
        )
        # Exactly one thread on the page, and it is the read-only archive.
        self.assertEqual(mounted_qa_content_ids(response), [str(UNIT_CONTENT_ID)])
        self.assertNotContains(response, 'id="qa-new-question')
        self.assertNotContains(response, 'id="qa-post-btn')
        self.assertNotContains(response, 'Questions &amp; Answers')

    def test_archive_adds_exactly_one_comment_count_query(self):
        self.threads_ready()
        Comment.objects.create(
            content_id=UNIT_CONTENT_ID, user=self.member, body='Old one',
        )
        self.client.force_login(self.member)
        # Warm-up so one-time lookups never count against the review page.
        self.client.get(f'{self.base_path}/review')
        with CaptureQueriesContext(connection) as review_queries:
            self.client.get(f'{self.base_path}/review')
        with CaptureQueriesContext(connection) as question_queries:
            self.client.get(f'{self.base_path}/q1-first')

        def comment_queries(captured):
            return [
                query['sql'] for query in captured.captured_queries
                if 'comments_comment' in query['sql']
            ]

        review_comment_sql = comment_queries(review_queries)
        self.assertEqual(len(review_comment_sql), 1, review_comment_sql)
        self.assertIn('COUNT(', review_comment_sql[0].upper())
        self.assertEqual(comment_queries(question_queries), [])


class ArchiveParser(HTMLParser):
    """Extract the review archive wrapper, accordion and read-only thread."""

    def __init__(self):
        super().__init__()
        self.wrapper_id = None
        self.details_open = None
        self.summary = ''
        self.helper_text = ''
        self.thread_attrs = {}
        self._in_archive = False
        self._capture = None

    @classmethod
    def parse(cls, response):
        parser = cls()
        parser.feed(response.content.decode())
        return parser

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get('data-testid') == 'homework-qa-archive':
            self._in_archive = True
            self.wrapper_id = attributes.get('id')
            return
        if not self._in_archive:
            return
        if attributes.get('data-testid') == 'homework-qa-archive-details':
            self.details_open = 'open' in attributes
        elif tag == 'summary':
            self._capture = 'summary'
        elif attributes.get('data-testid') == 'homework-qa-archive-helper':
            self._capture = 'helper_text'
        elif 'qa-thread' in (attributes.get('class') or ''):
            self.thread_attrs = attributes

    def handle_endtag(self, tag):
        if tag in ('summary', 'p'):
            self._capture = None

    def handle_data(self, data):
        if self._capture:
            current = getattr(self, self._capture)
            setattr(self, self._capture, (current + ' ' + data.strip()).strip())


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
        # Live thread with its composer, never the read-only archive.
        self.assertContains(response, 'id="qa-new-question"')
        self.assertEqual(mounted_qa_read_only_flags(response), [None])
        self.assertNotContains(response, 'Earlier homework discussion')

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
        self.assertContains(response, 'Questions &amp; Answers')
        self.assertContains(response, 'id="qa-new-question"')
        self.assertEqual(mounted_qa_read_only_flags(response), [None])


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
