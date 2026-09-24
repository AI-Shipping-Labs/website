"""Authenticated learner flow for configurable public progress links."""

import datetime
import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


def _create_assignment(email):
    from django.db import connection
    from django.utils import timezone

    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort
    from content.models.homework import Homework, Question, QuestionType

    create_user(email)
    course = Course.objects.create(
        title='Learning in Public form test', slug='learning-public-form-test',
        status='published', required_level=0,
    )
    module = Module.objects.create(course=course, title='Module 1', slug='module-1')
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Form test cohort',
        start_date=today - datetime.timedelta(days=1),
        end_date=today + datetime.timedelta(days=30),
    )
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=module, title='Project homework', slug='project-homework', kind='homework',
        content_id=content_id,
        homework=(
            'Build your project.\n\n## Question 1. Project\nDescribe your project.\n'
            '## Learning in Public\nShare a small win, a useful lesson, or a demo.\n\n'
            'For example, link to a project update, a short demo, or a post about a problem you solved.'
        ),
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='project-homework', title='Project homework',
        content_id=content_id,
        due_date=timezone.now() + datetime.timedelta(days=7),
        stepper_enabled=True,
        learning_in_public_cap=3,
        homework_url_field=True,
        time_spent_lectures_field=False,
        time_spent_homework_field=False,
    )
    Question.objects.create(
        homework=homework, source_question_id='q1-project', text='Describe your project.',
        question_type=QuestionType.FREE_FORM_LONG,
    )
    connection.close()
    return unit, homework


def _learner_context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


@pytest.mark.core
@browser_journey
def test_public_links_are_configurable_saved_and_submitted(django_server, browser):
    from accounts.models import User
    from content.models.homework import Submission

    email = 'learning-public-form@test.com'
    unit, homework = _create_assignment(email)
    context = _learner_context(browser, email)
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'
    page.goto(f'{unit_url}?homework_step=learning-in-public', wait_until='domcontentloaded')

    step_nav = page.get_by_role('group', name='Homework steps')
    expect(step_nav.get_by_role('link', name='Learning in Public')).to_be_visible()
    expect(page.get_by_test_id('learning-public-link-guidance')).to_contain_text('up to 3 optional public links')
    inputs = page.locator('[data-public-link-input]')
    expect(inputs).to_have_count(1)
    page.screenshot(path='.tmp/learning-in-public-desktop.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path='.tmp/learning-in-public-mobile.png', full_page=True)
    page.set_viewport_size({'width': 1280, 'height': 800})

    page.get_by_label('Public link 1 (optional)').fill('https://github.com/student/project')
    page.get_by_role('button', name='Add another link').click()
    page.get_by_label('Public link 2 (optional)').fill('https://example.com/demo')
    page.get_by_role('button', name='Add another link').click()
    page.get_by_label('Public link 3 (optional)').fill('https://example.com/learning')
    expect(inputs).to_have_count(3)
    expect(page.get_by_role('button', name='Add another link')).to_be_hidden()
    expect(page.locator('[data-save-status]')).to_contain_text('Saved')

    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}?homework_step=review')
    expect(page.get_by_label('Homework URL (optional)')).to_be_visible()
    expect(page.get_by_label('Time spent on lectures (hours) (optional)')).to_have_count(0)
    expect(page.get_by_label('Time spent on homework (hours) (optional)')).to_have_count(0)
    page.get_by_label('Homework URL (optional)').fill('https://github.com/student/project')
    page.get_by_role('button', name='Submit homework').click()

    expect(page.get_by_test_id('homework-submitted-status')).to_be_visible()
    submission = Submission.objects.get(homework=homework, student=User.objects.get(email=email))
    assert submission.learning_in_public_links == [
        'https://github.com/student/project',
        'https://example.com/demo',
        'https://example.com/learning',
    ]
    assert submission.homework_link == 'https://github.com/student/project'
    assert submission.time_spent_lectures is None
    assert submission.time_spent_homework is None
    context.close()
