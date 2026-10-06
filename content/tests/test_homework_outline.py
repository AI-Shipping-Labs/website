"""Virtual homework outline in the syllabus and its builder (issue #1794).

An activated stepped homework stays one ``Unit``/``Homework`` pair; the
syllabus only expands a display outline (Introduction, the active
questions, Review & submit) for a learner whose own cohort owns that
homework. Anonymous, unentitled and preview viewers keep the single
ordinary homework row, and no private answer material may leak through
the outline.
"""

import datetime
import uuid
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course, Module, Unit
from content.models.homework import (
    AnswerType,
    Homework,
    Question,
    QuestionType,
)
from content.services.homework_outline import (
    annotate_homework_outlines,
    homework_outline_steps,
)

User = get_user_model()

ANSWER_CANARY = 'outline-answer-leak-canary-b41c92'


class OutlineParser(HTMLParser):
    """Collect one homework group's step links and its extent."""

    def __init__(self, group_testid='syllabus-homework-group'):
        super().__init__()
        self.group_testid = group_testid
        self.group_depth = 0
        self.step_hrefs = []
        self.group_hrefs = []
        self.inside_group = False
        self.in_step_link = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        testid = attributes.get('data-testid', '')
        if testid == self.group_testid:
            self.inside_group = True
            self.group_depth = 1
        elif self.inside_group and tag == 'details':
            self.group_depth += 1
        if not self.inside_group:
            return
        if tag == 'a':
            href = attributes.get('href', '')
            self.group_hrefs.append(href)
            if testid == 'syllabus-homework-step':
                self.step_hrefs.append(href)
                self.in_step_link = True

    def handle_endtag(self, tag):
        if not self.inside_group:
            return
        if tag == 'details':
            self.group_depth -= 1
            if self.group_depth == 0:
                self.inside_group = False


class OutlineSetupMixin:
    """A one-week course whose homework has an activated stepper."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Outline course', slug='outline-course-1794',
            status='published', required_level=0,
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            external_key='cohort-4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.other_cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 5',
            external_key='cohort-5',
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=90),
        )
        cls.unit = Unit.objects.create(
            module=cls.week,
            title='Module 1 Homework: Document Processing with AI',
            slug='document-processing', sort_order=1,
            kind='homework', content_id=uuid.uuid4(),
            homework='## Question 1. First\nDescribe your approach.',
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='document-processing',
            title=cls.unit.title, content_id=cls.unit.content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
            stepper_enabled=True,
        )
        cls.q1 = Question.objects.create(
            homework=cls.homework, source_question_id='q1-intro-docs',
            text='Which documents?', question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='PDF\nDOCX', correct_answer='1',
        )
        cls.q2 = Question.objects.create(
            homework=cls.homework, source_question_id='q2-pipeline',
            text='Describe the pipeline.', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY, correct_answer=ANSWER_CANARY,
        )
        cls.capstone = Unit.objects.create(
            module=cls.week,
            title='Module 1 Capstone: Your AI Project',
            slug='capstone-ai-project', sort_order=2,
            kind='homework', content_id=uuid.uuid4(),
            homework='Ship your project.',
        )
        cls.lesson = Unit.objects.create(
            module=cls.week, title='Lesson one', slug='lesson-one',
            sort_order=0, body='Read this.',
        )
        cls.syllabus_url = cls.course.get_absolute_url()

    @classmethod
    def _make_learner(cls, email):
        learner = User.objects.create_user(email=email, password='pw')
        CohortEnrollment.objects.create(user=learner, cohort=cls.cohort)
        return learner


class HomeworkOutlineStepsTest(OutlineSetupMixin, TestCase):
    def test_steps_follow_question_count_and_order(self):
        steps = homework_outline_steps(self.unit, self.homework)

        self.assertEqual(
            [title for title, url in steps],
            ['Introduction', 'Question 1', 'Question 2', 'Review & submit'],
        )
        self.assertEqual(steps[0][1], f'{self.unit.get_absolute_url()}/intro')
        self.assertEqual(
            steps[1][1],
            f'{self.unit.get_absolute_url()}/q1-intro-docs',
        )
        self.assertEqual(
            steps[2][1],
            f'{self.unit.get_absolute_url()}/q2-pipeline',
        )
        self.assertEqual(steps[3][1], f'{self.unit.get_absolute_url()}/review')

    def test_learning_in_public_cap_adds_its_stepper_stop(self):
        self.homework.learning_in_public_cap = 1
        steps = homework_outline_steps(self.unit, self.homework)

        self.assertEqual(
            [title for title, url in steps],
            [
                'Introduction', 'Question 1', 'Question 2',
                'Learning in Public', 'Review & submit',
            ],
        )
        self.assertEqual(
            steps[3][1],
            f'{self.unit.get_absolute_url()}/learning-in-public',
        )

    def test_cohort_key_rides_encoded_beside_the_step_key(self):
        steps = homework_outline_steps(
            self.unit, self.homework, 'cohort four&co',
        )

        for title, url in steps:
            query = parse_qs(urlparse(url).query)
            self.assertEqual(query, {'cohort': ['cohort four&co']})


class AnnotateHomeworkOutlinesTest(OutlineSetupMixin, TestCase):
    def setUp(self):
        self.modules = self.course.get_syllabus()

    def test_entitled_learner_gets_the_outline(self):
        learner = self._make_learner('outline-entitled@test.com')

        annotate_homework_outlines(self.modules, learner, self.cohort)

        annotated = [unit for module in self.modules for unit in module.units.all()]
        homework_row = next(u for u in annotated if u.pk == self.unit.pk)
        self.assertEqual(
            [title for title, url in homework_row.homework_outline_steps],
            ['Introduction', 'Question 1', 'Question 2', 'Review & submit'],
        )
        capstone_row = next(u for u in annotated if u.pk == self.capstone.pk)
        self.assertFalse(getattr(capstone_row, 'homework_outline_steps', None))

    def test_inactive_stepper_homework_stays_plain(self):
        learner = self._make_learner('outline-inactive@test.com')
        self.homework.stepper_enabled = False
        self.homework.save(update_fields=['stepper_enabled'])

        annotate_homework_outlines(self.modules, learner, self.cohort)

        annotated = [unit for module in self.modules for unit in module.units.all()]
        homework_row = next(u for u in annotated if u.pk == self.unit.pk)
        self.assertFalse(getattr(homework_row, 'homework_outline_steps', None))

    def test_unenrolled_viewer_gets_no_outline(self):
        outsider = User.objects.create_user(email='outline-outsider@test.com')

        annotate_homework_outlines(self.modules, outsider, self.cohort)

        annotated = [unit for module in self.modules for unit in module.units.all()]
        homework_row = next(u for u in annotated if u.pk == self.unit.pk)
        self.assertFalse(getattr(homework_row, 'homework_outline_steps', None))

    def test_anonymous_viewer_gets_no_outline(self):
        from django.contrib.auth.models import AnonymousUser

        annotate_homework_outlines(self.modules, AnonymousUser(), self.cohort)

        annotated = [unit for module in self.modules for unit in module.units.all()]
        homework_row = next(u for u in annotated if u.pk == self.unit.pk)
        self.assertFalse(getattr(homework_row, 'homework_outline_steps', None))

    def test_viewer_enrolled_elsewhere_gets_no_outline(self):
        """Another cohort's steps are not disclosed through the outline."""
        learner = self._make_learner('outline-other-cohort@test.com')

        annotate_homework_outlines(self.modules, learner, self.other_cohort)

        annotated = [unit for module in self.modules for unit in module.units.all()]
        homework_row = next(u for u in annotated if u.pk == self.unit.pk)
        self.assertFalse(getattr(homework_row, 'homework_outline_steps', None))

    def test_step_path_marks_its_own_step_current(self):
        learner = self._make_learner('outline-path-step@test.com')

        annotate_homework_outlines(
            self.modules, learner, self.cohort,
            current_path=f'{self.unit.get_absolute_url()}/q2-pipeline',
        )

        homework_row = next(
            u for module in self.modules for u in module.units.all()
            if u.pk == self.unit.pk
        )
        self.assertEqual(
            homework_row.homework_outline_current_href,
            f'{self.unit.get_absolute_url()}/q2-pipeline?cohort=cohort-4',
        )

    def test_canonical_unit_path_marks_the_intro_step_current(self):
        learner = self._make_learner('outline-path-canonical@test.com')

        annotate_homework_outlines(
            self.modules, learner, self.cohort,
            current_path=self.unit.get_absolute_url(),
        )

        homework_row = next(
            u for module in self.modules for u in module.units.all()
            if u.pk == self.unit.pk
        )
        self.assertEqual(
            homework_row.homework_outline_current_href,
            f'{self.unit.get_absolute_url()}/intro?cohort=cohort-4',
        )

    def test_elsewhere_path_marks_no_step_current(self):
        learner = self._make_learner('outline-path-elsewhere@test.com')

        annotate_homework_outlines(
            self.modules, learner, self.cohort,
            current_path='/courses/other-course/week-2/elsewhere',
        )

        homework_row = next(
            u for module in self.modules for u in module.units.all()
            if u.pk == self.unit.pk
        )
        self.assertEqual(homework_row.homework_outline_current_href, '')


class SyllabusHomeworkOutlineViewTest(OutlineSetupMixin, TestCase):
    def _steps_response(self, learner):
        self.client.force_login(learner)
        return self.client.get(self.syllabus_url)

    def test_syllabus_expands_activated_homework_for_entitled_learner(self):
        learner = self._make_learner('outline-view-entitled@test.com')

        response = self._steps_response(learner)

        parser = OutlineParser()
        parser.feed(response.content.decode())
        self.assertEqual(len(parser.step_hrefs), 4)
        expected_suffixes = (
            '/intro', '/q1-intro-docs', '/q2-pipeline', '/review',
        )
        for href, suffix in zip(parser.step_hrefs, expected_suffixes):
            path = urlparse(href).path
            self.assertTrue(
                path.startswith(self.unit.get_absolute_url()),
                f'{href} does not deep-link the homework unit',
            )
            self.assertTrue(path.endswith(suffix), href)
            self.assertEqual(
                parse_qs(urlparse(href).query), {'cohort': ['cohort-4']},
            )
        # The capstone stays a separate sibling row: it is linked in the
        # syllabus but never inside the homework group's children.
        self.assertContains(response, self.capstone.get_absolute_url())
        self.assertNotIn(
            self.capstone.get_absolute_url(), parser.group_hrefs,
        )
        self.assertNotIn(
            self.capstone.get_absolute_url(), parser.step_hrefs,
        )

    def test_anonymous_syllabus_keeps_single_homework_row(self):
        response = self.client.get(self.syllabus_url)

        self.assertNotContains(response, 'syllabus-homework-group')
        self.assertNotContains(response, 'syllabus-homework-step')
        self.assertContains(response, self.unit.get_absolute_url())

    def test_unentitled_learner_keeps_single_row(self):
        outsider = User.objects.create_user(email='outline-view-outsider@test.com')

        response = self._steps_response(outsider)

        self.assertNotContains(response, 'syllabus-homework-group')
        self.assertNotContains(response, 'syllabus-homework-step')

    def test_forged_other_cohort_query_shows_no_outline(self):
        """An unowned cohort key must not unlock another cohort's steps."""
        learner = self._make_learner('outline-view-forged@test.com')
        self.client.force_login(learner)

        response = self.client.get(f'{self.syllabus_url}?cohort=cohort-5')

        self.assertNotContains(response, 'syllabus-homework-group')
        self.assertNotContains(response, 'syllabus-homework-step')

    def test_correct_answers_never_reach_syllabus_markup(self):
        learner = self._make_learner('outline-view-canary@test.com')

        response = self._steps_response(learner)

        self.assertNotContains(response, ANSWER_CANARY)

    def test_outline_navigation_creates_no_rows(self):
        learner = self._make_learner('outline-view-no-side-effects@test.com')
        unit_count = Unit.objects.count()
        question_count = Question.objects.count()

        self._steps_response(learner)

        self.assertEqual(Unit.objects.count(), unit_count)
        self.assertEqual(Question.objects.count(), question_count)

    def test_course_home_syllabus_also_expands_the_outline(self):
        learner = self._make_learner('outline-view-home@test.com')
        self.client.force_login(learner)

        response = self.client.get(f'/courses/{self.course.slug}/home/syllabus')

        self.assertContains(response, 'syllabus-homework-group')
        self.assertContains(response, f'{self.unit.get_absolute_url()}/q1-intro-docs')


class ReaderSidebarSetupMixin:
    """A Buildcamp-shaped week: the `Homework` wrapper submodule holds the
    scored homework and the capstone as sibling units, exposed inline by
    the reader sidebar's `inline_course_units` branch."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Engineering Buildcamp outline', slug='ai-buildcamp',
            status='published', required_level=0,
            reader_navigation_scope='module',
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.folder = Module.objects.create(
            course=cls.course, parent=cls.week, title='Homework',
            slug='homework', sort_order=90,
        )
        cls.unit = Unit.objects.create(
            module=cls.folder,
            title='Module 1 Homework: Document Processing with AI',
            slug='document-processing', sort_order=1,
            kind='homework', content_id=uuid.uuid4(),
            homework=(
                '## Question 1. First\nAnswer it.\n\n'
                '## Question 2. Second\nAnd this one.'
            ),
        )
        cls.capstone = Unit.objects.create(
            module=cls.folder, title='Module 1 Capstone: Your AI Project',
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
        cls.q2 = Question.objects.create(
            homework=cls.homework, source_question_id='q2-second',
            text='Second?', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY, correct_answer=ANSWER_CANARY,
        )
        cls.overview = Module.objects.create(
            course=cls.course, parent=cls.week, title='Overview',
            slug='overview', sort_order=0,
        )
        cls.lesson = Unit.objects.create(
            module=cls.overview, title='Welcome', slug='welcome',
            sort_order=1, body='Read this.', is_preview=True,
        )

    @classmethod
    def _make_learner(cls, email):
        learner = User.objects.create_user(email=email, password='pw')
        CohortEnrollment.objects.create(user=learner, cohort=cls.cohort)
        return learner


class ReaderSidebarHomeworkOutlineTest(ReaderSidebarSetupMixin, TestCase):
    def _step_response(self, learner, step_key):
        self.client.force_login(learner)
        return self.client.get(
            f'{self.unit.get_absolute_url()}/{step_key}',
            {'cohort': 'cohort-4'},
        )

    def test_step_page_sidebar_expands_the_outline(self):
        learner = self._make_learner('sidebar-step@test.com')

        response = self._step_response(learner, 'q1-first')

        # The group is open because the rendered page is one of its steps.
        self.assertContains(response, 'data-testid="reader-homework-group" open')
        parser = OutlineParser(group_testid='reader-homework-group')
        parser.feed(response.content.decode())
        self.assertEqual(
            parser.group_hrefs,
            [
                f'{self.unit.get_absolute_url()}?cohort=cohort-4',
                f'{self.unit.get_absolute_url()}/intro?cohort=cohort-4',
                f'{self.unit.get_absolute_url()}/q1-first?cohort=cohort-4',
                f'{self.unit.get_absolute_url()}/q2-second?cohort=cohort-4',
                f'{self.unit.get_absolute_url()}/review?cohort=cohort-4',
            ],
        )
        # Only the rendered step carries the current state, once.
        content = response.content.decode()
        self.assertEqual(content.count('data-testid="homework-step-current"'), 1)
        self.assertContains(response, 'aria-current="page" data-reader-current')
        # The capstone stays a sibling row outside the homework group.
        self.assertContains(response, self.capstone.get_absolute_url())
        self.assertNotIn(
            self.capstone.get_absolute_url(), parser.group_hrefs,
        )

    def test_sidebar_group_stays_closed_off_its_steps(self):
        learner = self._make_learner('sidebar-elsewhere@test.com')
        self.client.force_login(learner)

        response = self.client.get(
            self.capstone.get_absolute_url(), {'cohort': 'cohort-4'},
        )

        self.assertContains(response, 'data-testid="reader-homework-group"')
        self.assertNotContains(
            response, 'data-testid="reader-homework-group" open',
        )
        self.assertNotContains(response, 'data-testid="homework-step-current"')

    def test_anonymous_reader_keeps_plain_homework_rows(self):
        # Homework pages gate anonymous visitors, so the lesson page carries
        # the sidebar here; without an owned cohort it renders plain rows.
        response = self.client.get(self.lesson.get_absolute_url())

        self.assertNotContains(response, 'data-testid="reader-homework-group"')
        self.assertNotContains(response, 'data-testid="homework-step-nav"')
        self.assertContains(response, self.capstone.get_absolute_url())
