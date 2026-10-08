"""Learner journeys when a homework deadline has passed (issue #1917).

The deadline informs ("Was due <date>") but never blocks: only an operator
flipping ``Homework.state`` to CLOSED/SCORED closes a form, matching
community-base (cmp) gating. Legacy-form POSTs and commitments rows stay
authoritative as Django tests (see the issue's layer mapping); these journeys
cover the stepper: the reported blocker (a cohort learner submitting past her
deadline), draft autosave past due, resubmission, the operator close path,
and unchanged self-paced behavior.
"""

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


def _seed_cohort_course(email, *, due_offset_days=-1, state='OP'):
    """A dated cohort course whose stepper homework is due in the past."""
    from django.db import connection
    from django.utils import timezone

    from accounts.models import User
    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort, CohortEnrollment
    from content.models.homework import Homework, Question, QuestionType

    create_user(email)
    course = Course.objects.create(
        title='Late homework test', slug='late-homework-1917',
        status='published', required_level=0,
    )
    module = Module.objects.create(course=course, title='Module 1', slug='module-1')
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4',
        start_date=today - datetime.timedelta(days=10),
        end_date=today + datetime.timedelta(days=60),
    )
    CohortEnrollment.objects.create(user=User.objects.get(email=email), cohort=cohort)
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
        due_date=timezone.now() + datetime.timedelta(days=due_offset_days),
        stepper_enabled=True, state=state,
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


def _submit_for(user, homework):
    """Seed an accepted pre-deadline submission through the service."""
    from django.db import connection

    from content.services.homework_submissions import save_submission

    save_submission(
        homework, user, homework_link='https://github.com/example/first',
        answers_by_question_id={},
    )
    connection.close()


@pytest.mark.core
@browser_journey
def test_cohort_learner_submits_after_the_deadline(django_server, browser):
    """The reported blocker: deadline passed, state OPEN, no submission --
    the stepper stays editable and the submit succeeds."""
    from accounts.models import User
    from content.models.homework import Submission

    email = 'late-submit-1917@test.com'
    unit, homework = _seed_cohort_course(email)
    context = _learner_context(browser, email)
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'
    page.goto(unit_url, wait_until='domcontentloaded')

    header = page.get_by_test_id('homework-due-date')
    expect(header).to_contain_text('Was due')
    expect(page.get_by_test_id('homework-closed-banner')).to_have_count(0)
    expect(page.get_by_test_id('homework-page-state').locator(
        '[data-homework-state="not_submitted"]',
    )).to_be_visible()
    page.get_by_role('link', name='Start questions').click()
    expect(page).to_have_url(f'{unit_url}/q1-first')
    radio = page.get_by_role('radio', name='Beta')
    expect(radio).to_be_enabled()
    radio.check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    page.get_by_role('radio', name='No').check()
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/late')
    page.get_by_role('button', name='Submit homework').click()

    expect(page).to_have_url(re.compile(re.escape(unit_url) + r'/review\?receipt=.+'))
    expect(page.get_by_text('Your homework was submitted.', exact=False)).to_be_visible()
    user = User.objects.get(email=email)
    submission = Submission.objects.get(homework=homework, student=user)
    assert submission.homework_link == 'https://github.com/example/late'
    assert submission.answers.count() == 2
    context.close()


@pytest.mark.core
@browser_journey
def test_draft_autosave_works_after_the_deadline(django_server, browser):
    """Answers keep autosaving past the deadline and survive a revisit."""
    from community_base.homework_steps.models import HomeworkDraft

    from accounts.models import User
    from content.models.homework import Submission
    from content.services.homework_step_reader import option_key

    email = 'late-autosave-1917@test.com'
    unit, homework = _seed_cohort_course(email)
    context = _learner_context(browser, email)
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'
    page.goto(f'{unit_url}/q1-first', wait_until='domcontentloaded')
    page.get_by_role('radio', name='Beta').check()
    expect(page.locator('[data-save-status]')).to_contain_text('Saved')

    page.goto(unit_url, wait_until='domcontentloaded')
    page.goto(f'{unit_url}/q1-first', wait_until='domcontentloaded')
    expect(page.get_by_role('radio', name='Beta')).to_be_checked()
    user = User.objects.get(email=email)
    draft = HomeworkDraft.objects.get(
        user=user, assignment_key=f'aisl:homework:{homework.pk}',
    )
    assert draft.answers == {'q1-first': option_key('Beta')}

    page.goto(f'{unit_url}/q2-second', wait_until='domcontentloaded')
    page.get_by_role('radio', name='No').check()
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/draft')
    page.get_by_role('button', name='Submit homework').click()
    expect(page.get_by_text('Your homework was submitted.', exact=False)).to_be_visible()
    assert Submission.objects.filter(homework=homework, student=user).exists()
    context.close()


@pytest.mark.core
@browser_journey
def test_submitted_learner_updates_submission_after_the_deadline(django_server, browser):
    """A learner who submitted before the deadline can still update it:
    the same Submission row is edited, never duplicated."""
    from accounts.models import User
    from content.models.homework import Submission

    email = 'late-update-1917@test.com'
    unit, homework = _seed_cohort_course(email)
    user = User.objects.get(email=email)
    _submit_for(user, homework)
    context = _learner_context(browser, email)
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'

    page.goto(f'{unit_url}/review', wait_until='domcontentloaded')
    expect(page.get_by_role('button', name='Update submission')).to_be_visible()
    expect(page.get_by_text('The submission window is closed.')).to_have_count(0)

    page.goto(f'{unit_url}/q1-first', wait_until='domcontentloaded')
    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    page.goto(f'{unit_url}/review', wait_until='domcontentloaded')
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/updated')
    page.get_by_role('button', name='Update submission').click()

    expect(page.get_by_text('Your homework was submitted.', exact=False)).to_be_visible()
    assert Submission.objects.filter(homework=homework, student=user).count() == 1
    submission = Submission.objects.get(homework=homework, student=user)
    assert submission.homework_link == 'https://github.com/example/updated'
    q1 = homework.questions.get(source_question_id='q1-first')
    assert submission.answers.get(question=q1).answer_text == '1'  # stepper submit stores the raw option value, not the draft hash
    context.close()


@pytest.mark.core
@browser_journey
def test_operator_closed_stepper_is_read_only_without_deadline_claim(
    django_server, browser,
):
    """Operator flips state to CLOSED after the deadline: a learner without
    a submission sees the read-only review with the closed copy -- no text
    claims a deadline passed, and no submit control is reachable."""
    email = 'closed-1917@test.com'
    unit, homework = _seed_cohort_course(email, state='CL')
    context = _learner_context(browser, email)
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'

    page.goto(f'{unit_url}?homework_step=review', wait_until='domcontentloaded')
    expect(page.get_by_test_id('homework-page-state').locator(
        '[data-homework-state="closed_not_submitted"]',
    )).to_be_visible()
    expect(page.get_by_text(
        'This homework is closed. Your saved answers are still available.',
    )).to_be_visible()
    expect(page.get_by_test_id('homework-due-date')).to_contain_text('Was due')
    stepper_text = page.get_by_test_id('homework-stepper').inner_text()
    assert 'deadline' not in stepper_text.lower()
    expect(page.get_by_test_id('homework-submit-button')).to_have_count(0)
    expect(page.get_by_test_id('homework-closed-banner')).to_be_visible()
    expect(page.get_by_test_id('homework-closed-banner')).to_contain_text(
        "Sorry, it's too late to submit",
    )
    page.screenshot(path='.tmp/homework-1917-closed-review-desktop.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path='.tmp/homework-1917-closed-review-mobile.png', full_page=True)
    page.set_viewport_size({'width': 1280, 'height': 720})
    context.close()


@pytest.mark.core
@browser_journey
def test_self_paced_homework_is_unchanged(django_server, browser):
    """Self-paced: submittable despite a stamped year-old due_date; after
    submitting, results reveal and resubmission is locked."""
    from django.db import connection
    from django.utils import timezone

    from accounts.models import User
    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort, CohortEnrollment
    from content.models.homework import Homework, Question, QuestionType, Submission

    email = 'late-self-paced-1917@test.com'
    create_user(email)
    course = Course.objects.create(
        title='Self-paced late test', slug='self-paced-late-1917',
        status='published', required_level=0,
    )
    module = Module.objects.create(course=course, title='Module 1', slug='module-1')
    cohort = Cohort.objects.create(
        course=course, name='Self-paced', mode='self_paced',
    )
    user = User.objects.get(email=email)
    CohortEnrollment.objects.create(user=user, cohort=cohort)
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=module, title='Homework', slug='homework', kind='homework',
        content_id=content_id,
        homework=(
            'Answer both.\n\n## Question 1. Lines\nHow many lines?\n'
            '## Question 2. Second\nChoose another answer.'
        ),
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='homework', title='Homework', content_id=content_id,
        due_date=timezone.now() - datetime.timedelta(days=365),
        stepper_enabled=True, homework_url_field=False,
    )
    Question.objects.create(
        homework=homework, source_question_id='q1-lines', text='Lines',
        question_type=QuestionType.MULTIPLE_CHOICE, possible_answers='12\n14\n16',
        correct_answer='2',
    )
    Question.objects.create(
        homework=homework, source_question_id='q2-second', text='Second',
        question_type=QuestionType.MULTIPLE_CHOICE, possible_answers='Yes\nNo',
        correct_answer='1',
    )
    connection.close()

    context = _learner_context(browser, email)
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'
    page.goto(f'{unit_url}/q1-lines', wait_until='domcontentloaded')
    # Self-paced homework never shows a deadline line.
    expect(page.get_by_test_id('homework-due-date')).to_have_count(0)
    radio = page.get_by_role('radio', name='14')
    expect(radio).to_be_enabled()
    radio.check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    page.get_by_role('radio', name='Yes').check()
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    page.get_by_role('button', name='Submit homework').click()

    expect(page).to_have_url(re.compile(re.escape(unit_url) + r'/review\?receipt=.+'))
    results = page.get_by_test_id('homework-review-summary').get_by_test_id(
        'homework-question-result',
    )
    expect(results).to_have_count(2)
    expect(page.get_by_test_id('homework-submit-button')).to_have_count(0)
    assert Submission.objects.filter(homework=homework, student=user).count() == 1

    page.goto(f'{unit_url}/q1-lines', wait_until='domcontentloaded')
    expect(page.get_by_role('radio', name='14')).to_be_disabled()
    expect(page.get_by_role('button', name='Save & continue')).to_have_count(0)
    context.close()
