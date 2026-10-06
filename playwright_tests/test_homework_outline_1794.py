"""Learner journeys for the syllabus homework outline (issue #1794).

An activated stepped homework expands in the course syllabus into its
virtual outline (Introduction, the questions, Review & submit); the
disclosure toggles by mouse and keyboard, and a step link deep-links the
existing reader stepper with the learner's cohort.
"""

import datetime
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


def _create_outline_course(email):
    from django.db import connection
    from django.utils import timezone

    from content.models import Cohort, CohortEnrollment, Course, Module, Unit
    from content.models.homework import (
        AnswerType,
        Homework,
        Question,
        QuestionType,
    )

    create_user(email)
    course = Course.objects.create(
        title='Syllabus outline course', slug='syllabus-outline-1794',
        status='published', required_level=0,
    )
    week = Module.objects.create(course=course, title='Week 1', slug='week-1')
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='cohort-4',
        start_date=today - datetime.timedelta(days=2),
        end_date=today + datetime.timedelta(days=30),
    )
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=week,
        title='Module 1 Homework: Document Processing with AI',
        slug='document-processing', sort_order=1, kind='homework',
        content_id=content_id,
        homework=(
            'Read this first.\n\n'
            '## Question 1. First\nAnswer it.\n\n'
            '## Question 2. Second\nAnd this one.'
        ),
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='document-processing', title=unit.title,
        content_id=content_id, stepper_enabled=True,
        due_date=timezone.now() + datetime.timedelta(days=7),
    )
    Question.objects.create(
        homework=homework, source_question_id='q1-first', text='First',
        question_type=QuestionType.MULTIPLE_CHOICE,
        possible_answers='Alpha\nBeta', correct_answer='1',
    )
    Question.objects.create(
        homework=homework, source_question_id='q2-second', text='Second',
        question_type=QuestionType.FREE_FORM, answer_type=AnswerType.ANY,
    )
    capstone = Unit.objects.create(
        module=week, title='Module 1 Capstone: Your AI Project',
        slug='capstone-ai-project', sort_order=2, kind='homework',
        content_id=uuid.uuid4(), homework='Ship your project.',
    )
    learner = create_user(f'learner-{email}')
    CohortEnrollment.objects.create(cohort=cohort, user=learner)
    connection.close()
    return course, cohort, unit, capstone


@pytest.mark.core
@browser_journey
def test_syllabus_outline_toggles_and_deep_links_the_stepper(
    django_server, browser,
):
    email = 'syllabus-outline-journey@test.com'
    course, cohort, unit, capstone = _create_outline_course(email)

    context = auth_context(browser, f'learner-{email}')
    page = context.new_page()
    page.goto(
        f'{django_server}/courses/{course.slug}/home/syllabus',
        wait_until='domcontentloaded',
    )

    # The week starts collapsed, like every syllabus module.
    week_summary = page.get_by_test_id('syllabus-module-summary')
    week_summary.focus()
    page.keyboard.press('Enter')

    group = page.get_by_test_id('syllabus-homework-group')
    expect(group).to_be_visible()
    steps = page.get_by_test_id('syllabus-homework-step')
    expect(steps).to_have_count(4)
    expect(steps.nth(0)).to_contain_text('Introduction')
    expect(steps.nth(1)).to_contain_text('Question 1')
    expect(steps.nth(3)).to_contain_text('Review & submit')
    # The capstone stays a separate sibling row, outside the homework group.
    capstone_row = page.locator(
        '[data-testid="syllabus-unit-row"]',
        has_text='Module 1 Capstone',
    )
    expect(capstone_row).to_be_visible()

    # The disclosure collapses and reopens from its summary by keyboard.
    summary = group.locator('summary')
    steps_list = page.get_by_test_id('syllabus-homework-steps')
    summary.focus()
    page.keyboard.press('Enter')
    expect(steps_list).not_to_be_visible()
    page.keyboard.press('Enter')
    expect(steps_list).to_be_visible()

    # A step link opens the existing reader stepper, cohort kept.
    steps.nth(1).click()
    expect(page).to_have_url(
        f'{django_server}{unit.get_absolute_url()}'
        f'/q1-first?cohort={cohort.external_key}'
    )
    expect(page.get_by_test_id('homework-step-nav')).to_be_visible()
    current = page.get_by_test_id('homework-step-current')
    expect(current).to_contain_text('Question 1')
    context.close()
