"""Homework-only opt-in for the no-answer typing guideline (issue #1896).

The shared Q&A partial carries ``data-answer-guideline`` (the caption copy)
only when the course-unit include renders a ``kind`` homework unit for a
signed-in member. The typing behavior itself (caption appears on input,
hides on clear/post, reply composers) lives in
``playwright_tests/test_homework_answer_guideline_1896.py``. These tests own
the server-side decision of which threads opt in.
"""

import datetime
import uuid
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookclub.models import Book, Chapter, Note
from content.access import LEVEL_MAIN
from content.models import Course, Module, Unit, Workshop, WorkshopPage
from content.models.cohort import Cohort, CohortEnrollment
from content.models.homework import Homework, Question, QuestionType
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment
from tests.fixtures import set_membership

User = get_user_model()

GUIDELINE = (
    "Don't share homework answers here. If you have a question, discuss it "
    "in general terms without revealing the answer."
)


class _ThreadParser(HTMLParser):
    """Collect each rendered ``.qa-thread`` wrapper's guideline attribute."""

    def __init__(self):
        super().__init__()
        self.guidelines = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if 'qa-thread' in (attributes.get('class') or '').split():
            self.guidelines.append(attributes.get('data-answer-guideline'))


def thread_guidelines(response):
    parser = _ThreadParser()
    parser.feed(response.content.decode())
    return parser.guidelines


class CourseUnitGuidelineOptInTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member-1896@test.com', password='pw',
        )
        course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-1896',
            status='published', required_level=0,
        )
        module = Module.objects.create(
            course=course, title='Foundation', slug='foundation',
        )
        today = timezone.now().date()
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4', external_key='4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        CohortEnrollment.objects.create(user=cls.member, cohort=cohort)
        markdown = (
            'Intro prose.\n\n## Question 1. One\nHow many tokens?\n'
            '## Question 2. Two\nAnd now?\n'
        )
        cls.stepper_unit = Unit.objects.create(
            module=module, title='Homework', slug='homework', sort_order=1,
            kind='homework', content_id=uuid.uuid4(), homework=markdown,
        )
        stepper_homework = Homework.objects.create(
            cohort=cohort, slug='homework', title='Homework',
            content_id=cls.stepper_unit.content_id, stepper_enabled=True,
        )
        cls.form_unit = Unit.objects.create(
            module=module, title='Homework 2', slug='homework-2',
            sort_order=2, kind='homework', content_id=uuid.uuid4(),
            homework=markdown,
            # Preview so an anonymous visitor reaches the Q&A sign-up prompt.
            is_preview=True,
        )
        form_homework = Homework.objects.create(
            cohort=cohort, slug='homework-2', title='Homework 2',
            content_id=cls.form_unit.content_id, stepper_enabled=False,
        )
        for homework in (stepper_homework, form_homework):
            Question.objects.create(
                homework=homework, source_question_id='q1-first',
                text='First', question_type=QuestionType.FREE_FORM,
                answer_type='ANY',
            )
            Question.objects.create(
                homework=homework, source_question_id='q2-second',
                text='Second', question_type=QuestionType.FREE_FORM,
                answer_type='ANY',
            )
        # A lesson that still carries homework markdown must not opt in:
        # the decision keys off ``kind``, not ``homework_html``.
        cls.lesson = Unit.objects.create(
            module=module, title='Lesson', slug='lesson', sort_order=3,
            kind='lesson', content_id=uuid.uuid4(), body='Lesson body.',
            homework='## Question 1. Leftover\nHow many?\n',
        )

    def test_stepper_question_and_intro_steps_carry_the_guideline(self):
        self.client.force_login(self.member)
        base = self.stepper_unit.get_absolute_url()
        for step in ('intro', 'q1-first', 'q2-second'):
            response = self.client.get(f'{base}/{step}?cohort=4')
            self.assertContains(response, 'data-testid="homework-stepper"')
            self.assertEqual(thread_guidelines(response), [GUIDELINE], step)

    def test_all_questions_homework_carries_the_guideline(self):
        self.client.force_login(self.member)
        response = self.client.get(self.form_unit.get_absolute_url())

        self.assertNotContains(response, 'data-testid="homework-stepper"')
        self.assertEqual(thread_guidelines(response), [GUIDELINE])

    def test_lesson_with_homework_markdown_does_not_opt_in(self):
        self.client.force_login(self.member)
        response = self.client.get(self.lesson.get_absolute_url())

        self.assertEqual(thread_guidelines(response), [None])
        self.assertNotContains(response, "share homework answers")

    def test_anonymous_homework_visitor_gets_no_guideline(self):
        response = self.client.get(self.form_unit.get_absolute_url())

        self.assertEqual(thread_guidelines(response), [None])
        self.assertContains(response, 'href="/accounts/signup/?next=')
        self.assertNotContains(response, 'id="qa-new-question"')
        self.assertNotContains(response, "share homework answers")


class OtherSurfacesDoNotOptInTest(TestCase):
    """Workshops, plans, and Book Club notes reuse the partial without the
    homework opt-in, so a signed-in commenter never gets the caption."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='other-1896@test.com', password='pw',
        )
        set_membership(cls.member, tier=Tier.objects.get(slug='main'))
        cls.member.save()

        workshop = Workshop.objects.create(
            slug='ws-1896', title='Workshop', status='published',
            date=datetime.date(2026, 4, 21), landing_required_level=0,
            pages_required_level=0, recording_required_level=0,
            description='desc',
        )
        WorkshopPage.objects.create(
            workshop=workshop, slug='intro', title='Intro', sort_order=1,
            body='# Welcome', content_id=uuid.uuid4(),
        )

        sprint = Sprint.objects.create(
            name='Sprint 1896', slug='sprint-1896',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=sprint, visibility='cohort',
        )
        SprintEnrollment.objects.get_or_create(sprint=sprint, user=cls.member)

        book = Book.objects.create(
            title='Inference Engineering', slug='inference-1896',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current',
        )
        chapter = Chapter.objects.create(book=book, number=0, title='Intro')
        Note.objects.create(chapter=chapter, user=cls.member, body='My note.')

    def setUp(self):
        self.client.force_login(self.member)

    def assert_threads_without_guideline(self, response):
        guidelines = thread_guidelines(response)
        self.assertTrue(guidelines)
        self.assertEqual(set(guidelines), {None})
        self.assertContains(response, 'qa-new-question')
        self.assertNotContains(response, "share homework answers")

    def test_workshop_page(self):
        self.assert_threads_without_guideline(
            self.client.get('/workshops/ws-1896/intro'),
        )

    def test_plan_page(self):
        self.assert_threads_without_guideline(self.client.get(reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': 'sprint-1896', 'plan_id': self.plan.pk},
        )))

    def test_book_club_note(self):
        self.assert_threads_without_guideline(
            self.client.get('/books/inference-1896/chapters/0'),
        )
