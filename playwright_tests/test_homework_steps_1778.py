"""Learner journeys for AISL's in-reader homework steps."""

import datetime
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


def _learner_context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


def _create_assignment(email):
    from django.db import connection
    from django.utils import timezone

    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort
    from content.models.homework import Homework, Question, QuestionType

    create_user(email)
    course = Course.objects.create(
        title='Homework step save test', slug='homework-step-save-1778',
        status='published', required_level=0,
    )
    module = Module.objects.create(course=course, title='Module 1', slug='module-1')
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Current cohort',
        start_date=today - datetime.timedelta(days=2),
        end_date=today + datetime.timedelta(days=30),
    )
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=module, title='Homework', slug='homework', kind='homework',
        content_id=content_id,
        homework=(
            'Read this first.\n\n## Question 1. First\nChoose an answer.\n'
            '## Question 2. Second\nChoose another answer.'
        ),
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='homework', title='Homework', content_id=content_id,
        due_date=timezone.now() + datetime.timedelta(days=7),
        stepper_enabled=True,
    )
    Question.objects.create(
        homework=homework, source_question_id='q1-first', text='First',
        question_type=QuestionType.MULTIPLE_CHOICE, possible_answers='Alpha\nBeta',
        correct_answer='1',
    )
    Question.objects.create(
        homework=homework, source_question_id='q2-second', text='Second',
        question_type=QuestionType.MULTIPLE_CHOICE, possible_answers='Yes\nNo',
        correct_answer='1',
    )
    connection.close()
    return unit, homework


@pytest.mark.core
@browser_journey
@pytest.mark.parametrize(
    ('failure_status', 'expected_error'),
    [
        # The save-failure wording differs between community-base v0.5.5 and
        # the pending release; both must surface an explicit recovery hint.
        (503, re.compile(r'retry before leaving|Save failed')),
        (409, re.compile(r'Reload this page|Changed in another tab')),
    ],
)
def test_failed_autosave_blocks_step_link_without_losing_choice(
    django_server, browser, failure_status, expected_error,
):
    from content.models.homework import Submission

    email = f'homework-step-failure-{failure_status}@test.com'
    unit, homework = _create_assignment(email)

    context = _learner_context(browser, email)
    page = context.new_page()
    page.route(
        '**/api/homework-reader/drafts/**',
        lambda route: route.fulfill(status=failure_status, body='save unavailable'),
    )
    url = f'{django_server}{unit.get_absolute_url()}?homework_step=q1-first'
    page.goto(url, wait_until='domcontentloaded')
    page.get_by_role('radio', name='Alpha').check()
    expect(page.locator('[data-save-status]')).to_contain_text(expected_error)
    step_link = page.get_by_role('group', name='Homework steps').get_by_role(
        'link', name='Question 2',
    )
    with context.expect_page() as new_page_info:
        step_link.click(modifiers=['Control'])
    new_page = new_page_info.value
    expect(new_page).to_have_url(f'{django_server}{unit.get_absolute_url()}/q2-second')
    new_page.close()
    step_link.click()

    expect(page).to_have_url(url)
    expect(page.get_by_role('radio', name='Alpha')).to_be_checked()
    expect(page.locator('[data-save-status]')).to_contain_text(expected_error)
    dialogs = []

    def dismiss_unload(dialog):
        dialogs.append(dialog.type)
        dialog.dismiss()

    page.on('dialog', dismiss_unload)
    page.evaluate('history.back()')
    expect(page).to_have_url(url)
    assert dialogs == ['beforeunload']
    expect(page.get_by_role('radio', name='Alpha')).to_be_checked()
    assert not Submission.objects.filter(homework=homework).exists()
    context.close()


@pytest.mark.core
@browser_journey
def test_sidebar_exit_saves_latest_dirty_choice_before_navigation(django_server, browser):
    from community_base.homework_steps.models import HomeworkDraft

    from accounts.models import User
    from content.services.homework_step_reader import option_key

    email = 'homework-step-sidebar-flush@test.com'
    unit, homework = _create_assignment(email)
    context = _learner_context(browser, email)
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'
    page.goto(f'{unit_url}?homework_step=q1-first', wait_until='domcontentloaded')
    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('radio', name='Beta').check()
    page.get_by_role('group', name='Homework steps').get_by_role(
        'link', name='Question 2',
    ).click()

    expect(page).to_have_url(f'{unit_url}/q2-second')
    user = User.objects.get(email=email)
    draft = HomeworkDraft.objects.get(
        user=user, assignment_key=f'aisl:homework:{homework.pk}',
    )
    assert draft.answers == {'q1-first': option_key('Beta')}
    context.close()


@pytest.mark.core
@browser_journey
def test_learner_saves_resumes_and_submits_from_review(django_server, browser):
    from accounts.models import User
    from content.models.homework import Submission
    from content.services import completion as completion_service

    email = 'homework-step-success@test.com'
    unit, homework = _create_assignment(email)
    context = _learner_context(browser, email)
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'
    page.goto(unit_url, wait_until='domcontentloaded')
    user = User.objects.get(email=email)
    assert not completion_service.is_completed(user, unit)
    expect(page.locator('[data-testid="homework-introduction"]')).to_contain_text('Read this first.')
    expect(page.locator('[data-testid="reader-bottom-nav"]')).to_have_count(0)
    expect(page.locator('[data-testid="homework-stepper-container"] h2')).to_have_count(0)
    step_nav = page.get_by_role('group', name='Homework steps')
    intro_link = step_nav.get_by_role('link', name='Introduction')
    question_link = step_nav.get_by_role('link', name='Question 1')
    review_link = step_nav.get_by_role('link', name='Review & submit')
    expect(intro_link).to_have_count(1)
    expect(question_link).to_have_count(1)
    expect(review_link).to_have_count(1)
    expect(intro_link.locator('svg.lucide-book-open')).to_have_count(1)
    expect(question_link.locator('svg.lucide-help-circle')).to_have_count(1)
    expect(review_link.locator('svg.lucide-clipboard-check')).to_have_count(1)
    expect(question_link.locator('span.rounded-full')).to_have_count(0)
    page.screenshot(path='.tmp/astra-homework-intro-desktop.png', full_page=True)
    page.get_by_role('link', name='Start questions').click()
    expect(page).to_have_url(f'{unit_url}/q1-first')
    page.screenshot(path='.tmp/astra-homework-q1-desktop.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path='.tmp/astra-homework-q1-mobile.png', full_page=True)
    page.set_viewport_size({'width': 1280, 'height': 720})
    page.get_by_role('radio', name='Beta').check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    page.reload(wait_until='domcontentloaded')
    expect(page.locator('[data-testid="homework-question-prompt"]')).to_contain_text('Question 2')
    draft_help = page.get_by_test_id('homework-draft-status-help')
    draft_help_text = draft_help.locator('p')
    draft_help_summary = draft_help.locator('summary')
    expect(draft_help).to_be_visible()
    expect(draft_help_text).not_to_be_visible()
    draft_help_summary.hover()
    expect(draft_help_text).to_be_visible()
    page.screenshot(path='.tmp/astra-homework-draft-help-hover.png', full_page=True)
    page.locator('[data-testid="homework-question-prompt"]').hover()
    expect(draft_help_text).not_to_be_visible()
    draft_help_summary.focus()
    expect(draft_help_text).to_be_visible()
    draft_help_summary.click()
    expect(draft_help).to_have_attribute('open', '')
    page.locator('[data-testid="homework-question-prompt"]').hover()
    expect(draft_help_text).to_be_visible()
    page.get_by_role('radio', name='Yes').check()
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    expect(page.locator('[data-testid="homework-review-summary"]')).to_contain_text('Beta')
    expect(page.locator('[data-testid="homework-review-summary"]')).to_contain_text('Yes')
    assert not completion_service.is_completed(user, unit)
    page.screenshot(path='.tmp/astra-homework-review-desktop.png', full_page=True)
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/solution')
    page.get_by_role('button', name='Submit homework').click()
    expect(page).to_have_url(re.compile(re.escape(unit_url) + r'/review\?receipt=.+'))
    expect(page.get_by_text('Your homework was submitted.', exact=False)).to_be_visible()
    submission = Submission.objects.get(homework=homework, student=User.objects.get(email=email))
    assert submission.homework_link == 'https://github.com/example/solution'
    assert submission.answers.count() == 2
    assert completion_service.is_completed(user, unit)
    page.goto(f'{unit_url}?homework_step=review', wait_until='domcontentloaded')
    expect(page.locator('[data-testid="homework-submitted-status"]')).to_be_visible()
    expect(page.get_by_role('button', name='Update submission')).to_be_visible()
    expect(page.get_by_role('link', name='Back to course')).to_be_visible()
    context.close()
