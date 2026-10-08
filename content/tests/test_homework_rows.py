"""Homework and capstone rows render as ordinary unit rows (issue #1916).

They replace the #1794 expandable outline: no disclosure, chevron or child
steps in the syllabus list or the reader sidebar. Syllabus rows show the
cohort homework's question count, and both surfaces show the learner's
shared ``community_base`` ``LearnerHomeworkState`` label for a learner in
their own cohort.
"""

import datetime
import uuid
from html.parser import HTMLParser

from community_base.homework_steps.models import HomeworkDraft
from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course, Module, Unit
from content.models.homework import (
    AnswerType,
    Homework,
    HomeworkState,
    Question,
    QuestionType,
)
from content.services.homework_rows import annotate_homework_rows
from content.services.homework_step_reader import assignment_key, option_key
from content.services.homework_submissions import save_submission

User = get_user_model()


class SyllabusRowParser(HTMLParser):
    """Collect each syllabus unit row anchor and the meta inside it."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._capture = []
        self._sr_depth = 0
        self.testids = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        testid = attributes.get('data-testid', '')
        if testid:
            self.testids.append(testid)
        if tag == 'a' and 'data-syllabus-unit-row' in attributes:
            self._row = {
                'href': attributes.get('href', ''),
                'class': attributes.get('class', ''),
                'testid': testid,
                'tags': [],
                'texts': {},
                'attrs': {},
                'sr': '',
                'chevron': False,
            }
            self.rows.append(self._row)
            return
        if self._row is None:
            return
        self._row['tags'].append(tag)
        if attributes.get('data-lucide') == 'chevron-right':
            self._row['chevron'] = True
        if 'sr-only' in attributes.get('class', '').split():
            self._sr_depth += 1
        elif self._sr_depth:
            self._sr_depth += 1
        if tag == 'span':
            self._capture.append(testid)
            if testid:
                self._row['texts'].setdefault(testid, '')
                self._row['attrs'][testid] = attributes

    def handle_endtag(self, tag):
        if self._row is None:
            return
        if tag == 'a':
            self._row = None
            self._capture = []
            self._sr_depth = 0
            return
        if self._sr_depth:
            self._sr_depth -= 1
        if tag == 'span' and self._capture:
            self._capture.pop()

    def handle_data(self, data):
        if self._row is None:
            return
        if self._sr_depth:
            self._row['sr'] += data
        for testid in self._capture:
            if testid:
                self._row['texts'][testid] += data

    def row(self, href):
        return next(row for row in self.rows if row['href'] == href)


def parse_rows(response):
    parser = SyllabusRowParser()
    parser.feed(response.content.decode())
    return parser


class SidebarRowParser(HTMLParser):
    """Collect the reader sidebar's unit rows (``a.reader-list-row``)."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.testids = []
        self._in_nav = False
        self._row = None
        self._status = False
        self._sr = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get('id') == 'sidebar-nav':
            self._in_nav = True
        if not self._in_nav:
            return
        if attributes.get('data-testid'):
            self.testids.append(attributes['data-testid'])
        if tag == 'a' and 'reader-list-row' in attributes.get('class', ''):
            self._row = {
                'href': attributes.get('href', ''),
                'attrs': attributes,
                'status': None,
                'status_attrs': None,
                'sr': '',
                'chevron': False,
            }
            self.rows.append(self._row)
        elif self._row is not None:
            if attributes.get('data-testid') == 'homework-row-status':
                self._status = True
                self._row['status'] = ''
                self._row['status_attrs'] = attributes
            if attributes.get('class') == 'sr-only':
                self._sr = True
            if attributes.get('data-lucide') == 'chevron-right':
                self._row['chevron'] = True

    def handle_endtag(self, tag):
        if tag == 'nav':
            self._in_nav = False
        if tag == 'a':
            self._row = None
        if tag == 'span':
            self._status = False
            self._sr = False

    def handle_data(self, data):
        if self._row is None:
            return
        if self._status:
            self._row['status'] += data
        if self._sr:
            self._row['sr'] += data

    def row(self, href):
        return next(row for row in self.rows if row['href'] == href)


class HomeworkRowsSetupMixin:
    """A one-week course: a lesson, a stepped homework with two questions,
    a single-form capstone with one question, and a homework without a
    cohort ``Homework`` row."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Rows course', slug='rows-course-1916',
            status='published', required_level=0,
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='cohort-4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.lesson = Unit.objects.create(
            module=cls.week, title='Lesson one', slug='lesson-one',
            sort_order=0, body='Read this.',
        )
        cls.unit = Unit.objects.create(
            module=cls.week,
            title='Homework: Document Processing with AI',
            slug='document-processing', sort_order=1,
            kind='homework', content_id=uuid.uuid4(),
            homework='## Question 1. First\nAnswer.\n\n## Question 2. Second\nMore.',
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='document-processing',
            title=cls.unit.title, content_id=cls.unit.content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
            stepper_enabled=True,
        )
        cls.q1 = Question.objects.create(
            homework=cls.homework, source_question_id='q1-first',
            text='First?', question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='Alpha\nBeta', correct_answer='1',
        )
        cls.q2 = Question.objects.create(
            homework=cls.homework, source_question_id='q2-second',
            text='Second?', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY,
        )
        cls.capstone = Unit.objects.create(
            module=cls.week, title='Capstone: Your AI Project',
            slug='capstone-ai-project', sort_order=2,
            kind='homework', content_id=uuid.uuid4(),
            homework='Ship your project.',
        )
        cls.capstone_homework = Homework.objects.create(
            cohort=cls.cohort, slug='capstone', title=cls.capstone.title,
            content_id=cls.capstone.content_id,
            due_date=timezone.now() + datetime.timedelta(days=14),
        )
        Question.objects.create(
            homework=cls.capstone_homework, source_question_id='c1',
            text='Repo?', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY,
        )
        cls.unbacked = Unit.objects.create(
            module=cls.week, title='Homework: Not authored yet',
            slug='not-authored', sort_order=3,
            kind='homework', content_id=uuid.uuid4(),
            homework='Coming soon.',
        )
        cls.syllabus_url = f'/courses/{cls.course.slug}/home/syllabus'

    @classmethod
    def make_learner(cls, email):
        learner = User.objects.create_user(email=email, password='pw')
        CohortEnrollment.objects.create(user=learner, cohort=cls.cohort)
        return learner


class SyllabusHomeworkRowTest(HomeworkRowsSetupMixin, TestCase):
    def _syllabus(self, user, query=''):
        self.client.force_login(user)
        return self.client.get(self.syllabus_url + query)

    def test_homework_rows_render_as_ordinary_rows_for_every_viewer(self):
        staff = User.objects.create_user(
            email='rows-staff@test.com', password='pw', is_staff=True,
        )
        viewers = {
            'learner': lambda: self._syllabus(self.make_learner('rows-plain@test.com')),
            'unenrolled': lambda: self._syllabus(
                User.objects.create_user(email='rows-outsider@test.com'),
            ),
            'staff': lambda: self._syllabus(staff),
            'anonymous': lambda: (
                self.client.logout(),
                self.client.get(f'{self.course.get_absolute_url()}?cohort=cohort-4'),
            )[1],
        }
        for viewer, fetch in viewers.items():
            with self.subTest(viewer=viewer):
                parser = parse_rows(fetch())
                for retired in (
                    'syllabus-homework-group', 'syllabus-homework-steps',
                    'syllabus-homework-step',
                ):
                    self.assertNotIn(retired, parser.testids)
                homework_row = parser.row(self.unit.get_absolute_url())
                capstone_row = parser.row(self.capstone.get_absolute_url())
                lesson_row = parser.row(self.lesson.get_absolute_url())
                for row in (homework_row, capstone_row):
                    self.assertFalse(row['chevron'])
                    self.assertNotIn('details', row['tags'])
                    self.assertEqual(row['class'], lesson_row['class'])
                # The activated stepped homework and the single-form
                # capstone are the same row: same anchor, same testid.
                self.assertEqual(homework_row['testid'], 'syllabus-unit-row')
                self.assertEqual(capstone_row['testid'], 'syllabus-unit-row')
                self.assertNotIn(
                    f'{self.unit.get_absolute_url()}/q1-first',
                    [row['href'] for row in parser.rows],
                )

    def test_rows_show_question_count_with_singular_and_omit_it_without_homework(self):
        parser = parse_rows(self._syllabus(self.make_learner('rows-count@test.com')))

        homework_row = parser.row(self.unit.get_absolute_url())
        capstone_row = parser.row(self.capstone.get_absolute_url())
        unbacked_row = parser.row(self.unbacked.get_absolute_url())
        self.assertEqual(
            homework_row['texts']['syllabus-homework-question-count'], '2 questions',
        )
        self.assertEqual(
            capstone_row['texts']['syllabus-homework-question-count'], '1 question',
        )
        self.assertNotIn('syllabus-homework-question-count', unbacked_row['texts'])
        self.assertNotIn('syllabus-homework-meta', unbacked_row['texts'])
        self.assertNotIn('homework-row-status', unbacked_row['texts'])

    def test_zero_question_homework_shows_status_without_count(self):
        Question.objects.filter(homework=self.capstone_homework).delete()

        parser = parse_rows(self._syllabus(self.make_learner('rows-zero@test.com')))

        capstone_row = parser.row(self.capstone.get_absolute_url())
        self.assertNotIn('syllabus-homework-question-count', capstone_row['texts'])
        self.assertEqual(
            capstone_row['texts']['syllabus-homework-meta'], 'Not submitted',
        )

    def test_count_and_status_join_in_the_right_meta(self):
        learner = self.make_learner('rows-joined@test.com')
        save_submission(
            self.homework, learner, homework_link='',
            answers_by_question_id={self.q1.pk: '1'},
        )

        parser = parse_rows(self._syllabus(learner))

        row = parser.row(self.unit.get_absolute_url())
        self.assertEqual(
            row['texts']['syllabus-homework-meta'], '2 questions · Submitted',
        )
        status = row['attrs']['homework-row-status']
        self.assertEqual(status['data-homework-state'], 'submitted')
        self.assertEqual(status['aria-hidden'], 'true')
        self.assertNotIn('role', status)
        self.assertIn('Homework status: Submitted', row['sr'])
        self.assertEqual(
            parser.row(self.capstone.get_absolute_url())['texts']['syllabus-homework-meta'],
            '1 question · Not submitted',
        )

    def test_each_shared_state_renders_its_shared_label(self):
        learner = self.make_learner('rows-states@test.com')
        key = assignment_key(self.homework)

        def row_state():
            row = parse_rows(self._syllabus(learner)).row(self.unit.get_absolute_url())
            return (
                row['attrs']['homework-row-status']['data-homework-state'],
                row['texts']['homework-row-status'],
            )

        self.assertEqual(row_state(), ('not_submitted', 'Not submitted'))
        draft = HomeworkDraft.objects.create(
            user=learner, assignment_key=key,
            answers={'q1-first': option_key('Alpha')},
        )
        self.assertEqual(row_state(), ('draft', 'Draft'))
        self.homework.state = HomeworkState.CLOSED
        self.homework.save(update_fields=['state'])
        self.assertEqual(
            row_state(), ('closed_not_submitted', 'Closed — not submitted'),
        )
        self.homework.state = HomeworkState.OPEN
        self.homework.save(update_fields=['state'])
        save_submission(
            self.homework, learner, homework_link='',
            answers_by_question_id={self.q1.pk: '1'},
        )
        self.assertEqual(row_state(), ('submitted', 'Submitted'))
        draft.answers = {'q1-first': option_key('Beta')}
        draft.save(update_fields=['answers'])
        self.assertEqual(
            row_state(), ('unsubmitted_changes', 'Unsubmitted changes'),
        )
        self.homework.state = HomeworkState.SCORED
        self.homework.save(update_fields=['state'])
        self.assertEqual(row_state(), ('scored', 'Scored'))

    def test_past_due_open_homework_stays_open_until_the_operator_closes_it(self):
        """Issue #1917: acceptance follows ``state`` only. A passed deadline
        keeps an ``OPEN`` homework open; closing comes from ``state=CLOSED``."""
        self.homework.due_date = timezone.now() - datetime.timedelta(days=1)
        self.homework.save(update_fields=['due_date'])
        missed = self.make_learner('rows-missed@test.com')
        submitted = self.make_learner('rows-ontime@test.com')
        save_submission(
            self.homework, submitted, homework_link='',
            answers_by_question_id={self.q1.pk: '1'},
        )

        def meta(learner):
            row = parse_rows(self._syllabus(learner)).row(self.unit.get_absolute_url())
            return row['texts']['syllabus-homework-meta']

        self.assertEqual(meta(missed), '2 questions · Not submitted')
        self.assertEqual(meta(submitted), '2 questions · Submitted')

        self.homework.state = HomeworkState.CLOSED
        self.homework.save(update_fields=['state'])

        self.assertEqual(meta(missed), '2 questions · Closed — not submitted')
        self.assertEqual(meta(submitted), '2 questions · Submitted')

    def test_status_is_hidden_from_viewers_outside_their_own_cohort(self):
        staff = User.objects.create_user(
            email='rows-preview-staff@test.com', password='pw', is_staff=True,
        )
        cases = {
            'unenrolled member': lambda: self._syllabus(
                User.objects.create_user(email='rows-hidden-outsider@test.com'),
            ),
            'staff cohort preview': lambda: self._syllabus(staff, '?cohort=cohort-4'),
            'anonymous preview': lambda: (
                self.client.logout(),
                self.client.get(f'{self.course.get_absolute_url()}?cohort=cohort-4'),
            )[1],
        }
        for viewer, fetch in cases.items():
            with self.subTest(viewer=viewer):
                parser = parse_rows(fetch())
                self.assertNotIn('homework-row-status', parser.testids)
                row = parser.row(self.unit.get_absolute_url())
                self.assertNotIn('Homework status', row['sr'])
        # A preview still shows the non-sensitive question count.
        preview_row = parse_rows(
            self._syllabus(staff, '?cohort=cohort-4'),
        ).row(self.unit.get_absolute_url())
        self.assertEqual(
            preview_row['texts']['syllabus-homework-meta'], '2 questions',
        )

    def test_no_cohort_shows_neither_count_nor_status(self):
        self.client.logout()

        parser = parse_rows(self.client.get(self.course.get_absolute_url()))

        self.assertNotIn('syllabus-homework-meta', parser.testids)
        self.assertNotIn('homework-row-status', parser.testids)
        self.assertEqual(
            parser.row(self.unit.get_absolute_url())['testid'], 'syllabus-unit-row',
        )


class ReaderSidebarHomeworkRowTest(TestCase):
    """A Buildcamp-shaped week: the `Homework` wrapper submodule holds the
    stepped homework and the capstone, shown inline by the reader sidebar."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Engineering Buildcamp rows', slug='ai-buildcamp',
            status='published', required_level=0,
            reader_navigation_scope='module',
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.overview = Module.objects.create(
            course=cls.course, parent=cls.week, title='Overview',
            slug='overview', sort_order=0,
        )
        cls.lesson = Unit.objects.create(
            module=cls.overview, title='Welcome', slug='welcome',
            sort_order=1, body='Read this.',
        )
        cls.folder = Module.objects.create(
            course=cls.course, parent=cls.week, title='Homework',
            slug='homework', sort_order=90,
        )
        cls.unit = Unit.objects.create(
            module=cls.folder, title='Homework: Document Processing with AI',
            slug='document-processing', sort_order=1,
            kind='homework', content_id=uuid.uuid4(),
            homework='## Question 1. First\nAnswer it.\n\n## Question 2. Second\nMore.',
        )
        cls.capstone = Unit.objects.create(
            module=cls.folder, title='Capstone: Your AI Project',
            slug='capstone-ai-project', sort_order=2,
            kind='homework', content_id=uuid.uuid4(),
            homework='Ship your project.',
        )
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='cohort-4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='document-processing',
            title=cls.unit.title, content_id=cls.unit.content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
            stepper_enabled=True,
        )
        cls.q1 = Question.objects.create(
            homework=cls.homework, source_question_id='q1-first',
            text='First?', question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='Alpha\nBeta', correct_answer='1',
        )
        Question.objects.create(
            homework=cls.homework, source_question_id='q2-second',
            text='Second?', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY,
        )
        cls.capstone_homework = Homework.objects.create(
            cohort=cls.cohort, slug='capstone', title=cls.capstone.title,
            content_id=cls.capstone.content_id,
            due_date=timezone.now() + datetime.timedelta(days=14),
        )
        Question.objects.create(
            homework=cls.capstone_homework, source_question_id='c1',
            text='Repo?', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY,
        )

    def setUp(self):
        self.learner = User.objects.create_user(
            email=f'sidebar-{uuid.uuid4().hex[:8]}@test.com', password='pw',
        )
        CohortEnrollment.objects.create(user=self.learner, cohort=self.cohort)
        self.client.force_login(self.learner)

    def _sidebar(self, url):
        response = self.client.get(url, {'cohort': 'cohort-4'})
        parser = SidebarRowParser()
        parser.feed(response.content.decode())
        return response, parser

    def _href(self, unit):
        return f'{unit.get_absolute_url()}?cohort=cohort-4'

    def test_step_page_marks_homework_row_current_with_step_nav_beneath(self):
        response, parser = self._sidebar(f'{self.unit.get_absolute_url()}/q2-second')

        self.assertNotIn('reader-homework-group', parser.testids)
        homework_row = parser.row(self._href(self.unit))
        self.assertEqual(homework_row['attrs'].get('aria-current'), 'page')
        self.assertFalse(homework_row['chevron'])
        self.assertIn('homework-step-nav', parser.testids)
        current_steps = [
            row for row in parser.rows if row['attrs'].get('aria-current') == 'step'
        ]
        self.assertEqual(len(current_steps), 1)
        self.assertEqual(
            current_steps[0]['attrs'].get('data-testid'), 'homework-step-current',
        )
        self.assertEqual(
            current_steps[0]['href'].split('?')[0],
            f'{self.unit.get_absolute_url()}/q2-second',
        )
        # The step nav follows the homework row, before the capstone row.
        hrefs = [row['href'] for row in parser.rows]
        step_index = hrefs.index(current_steps[0]['href'])
        self.assertLess(hrefs.index(self._href(self.unit)), step_index)
        self.assertLess(step_index, hrefs.index(self._href(self.capstone)))

    def test_other_unit_shows_no_homework_steps_and_plain_homework_rows(self):
        response, parser = self._sidebar(self.lesson.get_absolute_url())

        self.assertNotIn('homework-step-nav', parser.testids)
        self.assertNotIn('reader-homework-group', parser.testids)
        for unit in (self.unit, self.capstone):
            row = parser.row(self._href(unit))
            self.assertNotIn('aria-current', row['attrs'])
            self.assertFalse(row['chevron'])
        self.assertNotIn(
            f'{self.unit.get_absolute_url()}/q1-first',
            ' '.join(row['href'] for row in parser.rows),
        )

    def test_sidebar_rows_show_status_but_no_question_count(self):
        save_submission(
            self.homework, self.learner, homework_link='',
            answers_by_question_id={self.q1.pk: '1'},
        )

        response, parser = self._sidebar(self.lesson.get_absolute_url())

        homework_row = parser.row(self._href(self.unit))
        self.assertEqual(homework_row['status'], 'Submitted')
        self.assertEqual(
            homework_row['status_attrs']['data-homework-state'], 'submitted',
        )
        self.assertEqual(homework_row['status_attrs']['aria-hidden'], 'true')
        self.assertIn('Homework status: Submitted', homework_row['sr'])
        self.assertEqual(
            parser.row(self._href(self.capstone))['status'], 'Not submitted',
        )
        self.assertIsNone(parser.row(self._href(self.lesson))['status'])
        self.assertNotIn('syllabus-homework-question-count', parser.testids)
        self.assertNotIn('question', homework_row['sr'])

    def test_current_homework_row_shows_the_same_status(self):
        _, step_parser = self._sidebar(f'{self.unit.get_absolute_url()}/q1-first')
        _, lesson_parser = self._sidebar(self.lesson.get_absolute_url())

        current = step_parser.row(self._href(self.unit))
        other = lesson_parser.row(self._href(self.unit))
        self.assertEqual(current['status'], 'Not submitted')
        self.assertEqual(current['status'], other['status'])
        self.assertEqual(current['status_attrs'], other['status_attrs'])

    def test_unenrolled_reader_sees_no_status(self):
        outsider = User.objects.create_user(email='sidebar-outsider@test.com')
        self.client.force_login(outsider)

        response = self.client.get(self.lesson.get_absolute_url())

        parser = SidebarRowParser()
        parser.feed(response.content.decode())
        self.assertNotIn('homework-row-status', parser.testids)


class SelfPacedReaderStatusTest(TestCase):
    """A self-paced-only learner has no dated cohort to select; the reader
    sidebar falls back to their self-paced cohort, as course Home does."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Self-paced rows', slug='self-paced-rows-1916',
            status='published', required_level=0,
        )
        module = Module.objects.create(course=cls.course, title='Module 1', slug='module-1')
        cls.lesson = Unit.objects.create(
            module=module, title='Lesson', slug='lesson', sort_order=0, body='Read.',
        )
        cls.unit = Unit.objects.create(
            module=module, title='Homework', slug='homework', sort_order=1,
            kind='homework', content_id=uuid.uuid4(), homework='Do it.',
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )
        homework = Homework.objects.create(
            cohort=cls.cohort, slug='homework', title='Homework',
            content_id=cls.unit.content_id,
        )
        Question.objects.create(
            homework=homework, source_question_id='q1', text='Why?',
            question_type=QuestionType.FREE_FORM, answer_type=AnswerType.ANY,
        )

    def test_self_paced_learner_sees_status_in_the_sidebar(self):
        learner = User.objects.create_user(email='self-paced-rows@test.com')
        CohortEnrollment.objects.create(user=learner, cohort=self.cohort)
        self.client.force_login(learner)

        response = self.client.get(self.lesson.get_absolute_url())

        parser = SidebarRowParser()
        parser.feed(response.content.decode())
        row = parser.row(self.unit.get_absolute_url())
        self.assertEqual(row['status'], 'Not submitted')
        self.assertEqual(row['status_attrs']['data-homework-state'], 'not_submitted')


class ListRowMetaSlotTest(TestCase):
    def test_row_without_status_meta_renders_no_status_slot(self):
        html = render_to_string('includes/_list_row.html', {
            'href': '/workshops/w/page', 'title': 'Page', 'marker_kind': 'circle',
            'trailing_icon': '',
        })

        self.assertNotIn('homework-row-status', html)
        self.assertNotIn('Homework status', html)


class HomeworkRowQueryCountTest(HomeworkRowsSetupMixin, TestCase):
    """Row meta is batch-loaded: more homework units add no queries."""

    def _add_homework_units(self, learner, count):
        for index in range(count):
            unit = Unit.objects.create(
                module=self.week, title=f'Extra homework {index}',
                slug=f'extra-homework-{index}', sort_order=10 + index,
                kind='homework', content_id=uuid.uuid4(),
                homework='Extra.',
            )
            homework = Homework.objects.create(
                cohort=self.cohort, slug=f'extra-{index}', title=unit.title,
                content_id=unit.content_id,
                due_date=timezone.now() + datetime.timedelta(days=7),
                stepper_enabled=True,
            )
            question = Question.objects.create(
                homework=homework, source_question_id=f'x{index}',
                text='Extra?', question_type=QuestionType.FREE_FORM,
                answer_type=AnswerType.ANY,
            )
            save_submission(
                homework, learner, homework_link='',
                answers_by_question_id={question.pk: 'yes'},
            )
            HomeworkDraft.objects.create(
                user=learner, assignment_key=assignment_key(homework),
                answers={f'x{index}': 'changed'},
            )

    def _assert_constant_queries(self, url):
        learner = self.make_learner(f'rows-queries-{uuid.uuid4().hex[:6]}@test.com')
        save_submission(
            self.homework, learner, homework_link='',
            answers_by_question_id={self.q1.pk: '1'},
        )
        self.client.force_login(learner)
        self.client.get(url)  # warm per-process caches
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(url)
        self._add_homework_units(learner, 2)

        with self.assertNumQueries(len(baseline.captured_queries)):
            response = self.client.get(url)

        # One homework row before, three after: every row carries status.
        parser = SidebarRowParser() if '/week-1/' in url else SyllabusRowParser()
        parser.feed(response.content.decode())
        self.assertEqual(parser.testids.count('homework-row-status'), 3)

    def test_syllabus_query_count_does_not_grow_with_homework_units(self):
        Homework.objects.filter(pk=self.capstone_homework.pk).delete()
        self._assert_constant_queries(f'{self.course.get_absolute_url()}?view=overview')

    def test_course_home_row_annotation_query_count_is_constant(self):
        """Course Home's own row annotation is batched too.

        Home's other sections (commitments, due-next) are measured
        elsewhere, so this pins the queries this feature adds there.
        """
        Homework.objects.filter(pk=self.capstone_homework.pk).delete()
        learner = self.make_learner('rows-annotate-queries@test.com')
        save_submission(
            self.homework, learner, homework_link='',
            answers_by_question_id={self.q1.pk: '1'},
        )

        def annotate():
            modules = self.course.get_syllabus()
            list(modules)
            with CaptureQueriesContext(connection) as queries:
                annotate_homework_rows(
                    modules, learner, self.cohort, include_status=True,
                )
            return len(queries.captured_queries), modules

        before, _ = annotate()
        self._add_homework_units(learner, 2)
        after, modules = annotate()

        self.assertEqual(after, before)
        annotated = [
            unit for module in modules for unit in module.units.all()
            if getattr(unit, 'homework_row_state', None)
        ]
        self.assertEqual(len(annotated), 3)

    def test_reader_query_count_does_not_grow_with_homework_units(self):
        Homework.objects.filter(pk=self.capstone_homework.pk).delete()
        self._assert_constant_queries(self.lesson.get_absolute_url())
