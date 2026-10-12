"""Browser coverage for Studio homework presentation mode (issue #1944)."""

import datetime
import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

STAFF_EMAIL = 'staff-1944@test.com'


def _close():
    connection.close()


def _course(slug):
    from content.models import Course

    return Course.objects.create(
        title=slug, slug=slug, status='published', required_level=0,
    )


def _cohort(course, name='Current'):
    from django.utils import timezone

    from content.models import Cohort

    today = timezone.localdate()
    return Cohort.objects.create(
        course=course, name=name,
        start_date=today - datetime.timedelta(days=3),
        end_date=today + datetime.timedelta(days=20),
    )


def _homework(cohort, slug, title, *, content_id=None):
    from django.utils import timezone

    from content.models.homework import Homework

    return Homework.objects.create(
        cohort=cohort, slug=slug, title=title,
        content_id=content_id or uuid.uuid4(),
        due_date=timezone.now() + datetime.timedelta(days=7),
        stepper_enabled=True,
    )


def _question(homework, source_id, text, qtype, **kwargs):
    from content.models.homework import Question

    return Question.objects.create(
        homework=homework, source_question_id=source_id, text=text,
        question_type=qtype, **kwargs,
    )


def _submit(homework, user, answers, *, homework_link=''):
    from content.services.homework_submissions import save_submission

    return save_submission(
        homework, user, homework_link=homework_link,
        answers_by_question_id=answers,
    )


def _email_style(locator):
    return locator.evaluate(
        """el => ({
            text: el.textContent,
            filter: getComputedStyle(el).filter,
            userSelect: getComputedStyle(el).userSelect,
        })"""
    )


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_walks_free_text_answers_one_by_one(django_server, browser):
    from content.models.homework import AnswerType, QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-walk-1944')
    cohort = _cohort(course)
    homework = _homework(cohort, 'walk', 'Walk homework')
    ff = _question(
        homework, 'q1-writeup', 'Describe it', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    mc = _question(
        homework, 'q2-pick', 'Pick one', QuestionType.MULTIPLE_CHOICE,
        possible_answers='Alpha\nBeta', correct_answer='1',
    )
    newer = create_user('newer-1944@test.com')
    older = create_user('older-1944@test.com')
    older_sub = _submit(
        homework, older, {ff.pk: 'Older writeup about shipping', mc.pk: '1'},
    )
    older_sub.submitted_at = older_sub.submitted_at - datetime.timedelta(minutes=5)
    older_sub.save(update_fields=['submitted_at'])
    _submit(homework, newer, {ff.pk: 'Newer writeup about agents', mc.pk: '2'})
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework.pk}/submissions',
        wait_until='domcontentloaded',
    )
    page.get_by_role('button', name='Presentation mode').click()
    card = page.get_by_test_id('homework-presentation-card')
    expect(card).to_be_visible()
    expect(page.get_by_test_id('homework-presentation-heading')).to_contain_text('Question 1')
    expect(card).to_contain_text('Newer writeup about agents')
    expect(card).not_to_contain_text('Alpha')
    expect(page.get_by_test_id('homework-presentation-position')).to_have_text('1 of 2')
    expect(page.get_by_test_id('homework-presentation-prev')).to_be_disabled()
    page.get_by_role('button', name='Next').click()
    expect(card).to_contain_text('Older writeup about shipping')
    expect(page.get_by_test_id('homework-presentation-position')).to_have_text('2 of 2')
    expect(page.get_by_test_id('homework-presentation-next')).to_be_disabled()
    page.keyboard.press('ArrowLeft')
    expect(card).to_contain_text('Newer writeup about agents')
    expect(page.get_by_test_id('homework-presentation-position')).to_have_text('1 of 2')
    page.keyboard.press('ArrowRight')
    expect(card).to_contain_text('Older writeup about shipping')
    page.get_by_role('button', name='Exit presentation').click()
    expect(page.get_by_test_id('homework-presentation')).to_be_hidden()
    expect(page.get_by_test_id('homework-submission-row')).to_have_count(2)
    expect(page.get_by_test_id('homework-submissions-list')).to_contain_text(
        'Newer writeup about agents',
    )
    expect(page.get_by_test_id('homework-submissions-list')).to_contain_text(
        'Older writeup about shipping',
    )
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_presentation_keeps_learner_emails_blurred(django_server, browser):
    from content.models.homework import AnswerType, QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-privacy-1944')
    cohort = _cohort(course)
    homework = _homework(cohort, 'privacy', 'Privacy homework')
    question = _question(
        homework, 'q1-writeup', 'Describe it', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    learner = create_user('visible-1944@test.com')
    _submit(homework, learner, {question.pk: 'A named student writeup'})
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework.pk}/submissions',
        wait_until='domcontentloaded',
    )
    email = page.get_by_test_id('homework-submission-student')
    shown = _email_style(email)
    assert shown['text'] == 'visible-1944@test.com'
    assert 'blur' not in shown['filter']
    expect(page.get_by_role('button', name='Hide emails')).to_have_count(1)
    page.get_by_role('button', name='Presentation mode').click()
    presented = page.locator(
        '[data-testid="homework-presentation"] [data-testid="homework-submission-student"]',
    )
    hidden = _email_style(presented)
    assert hidden['text'] == 'visible-1944@test.com'
    assert 'blur' in hidden['filter']
    assert hidden['userSelect'] == 'none'
    show = page.get_by_role('button', name='Show emails')
    if show.count():
        show.click()
        still = _email_style(presented)
        assert 'blur' in still['filter']
        assert still['userSelect'] == 'none'
    page.get_by_role('button', name='Exit presentation').click()
    restored = _email_style(page.get_by_test_id('homework-submission-student'))
    assert restored['text'] == 'visible-1944@test.com'
    assert 'blur' not in restored['filter']
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_talked_about_persists_and_skips_the_marked_answer(django_server, browser):
    from content.models.homework import AnswerType, QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-talked-1944')
    cohort = _cohort(course)
    homework = _homework(cohort, 'talked', 'Talked homework')
    first_q = _question(
        homework, 'q1-first', 'First', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    second_q = _question(
        homework, 'q2-second', 'Second', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    learner = create_user('talked-1944@test.com')
    submission = _submit(
        homework, learner,
        {first_q.pk: 'First free-text answer', second_q.pk: 'Second free-text answer'},
    )
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework.pk}/submissions',
        wait_until='domcontentloaded',
    )
    page.get_by_role('button', name='Presentation mode').click()
    expect(page.get_by_test_id('homework-presentation-card')).to_contain_text(
        'First free-text answer',
    )
    page.get_by_role('button', name='Mark as talked about').click()
    expect(page.get_by_test_id('homework-presentation-talked')).to_have_text('Talked about')
    stored = page.evaluate(
        f"() => JSON.parse(localStorage.getItem('studio-homework-talked:{homework.pk}'))"
    )
    assert f'{submission.pk}:{first_q.pk}' in stored
    page.get_by_role('button', name='Exit presentation').click()
    page.reload(wait_until='domcontentloaded')
    page.locator('[data-testid="homework-submission-row"] summary').click()
    visible_badge = page.locator(
        '[data-testid="homework-talked-toggle"]:not([hidden])',
    )
    expect(page.get_by_test_id('homework-talked-toggle')).to_have_count(2)
    expect(visible_badge).to_have_count(1)
    expect(visible_badge).to_contain_text('Talked about')
    page.get_by_role('button', name='Presentation mode').click()
    expect(page.get_by_test_id('homework-presentation-card')).to_contain_text(
        'Second free-text answer',
    )
    expect(page.get_by_test_id('homework-presentation-position')).to_have_text('2 of 2')
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_accordion_can_unmark_talked_about(django_server, browser):
    from content.models.homework import AnswerType, QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-unmark-1944')
    cohort = _cohort(course)
    homework = _homework(cohort, 'unmark', 'Unmark homework')
    question = _question(
        homework, 'q1-writeup', 'Describe it', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    learner = create_user('unmark-1944@test.com')
    submission = _submit(homework, learner, {question.pk: 'Only free-text answer'})
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework.pk}/submissions',
        wait_until='domcontentloaded',
    )
    page.evaluate(
        f"() => localStorage.setItem('studio-homework-talked:{homework.pk}', "
        f"JSON.stringify(['{submission.pk}:{question.pk}']))"
    )
    page.reload(wait_until='domcontentloaded')
    page.locator('[data-testid="homework-submission-row"] summary').click()
    badge = page.get_by_test_id('homework-talked-toggle')
    expect(badge).to_be_visible()
    badge.click()
    expect(badge).to_be_hidden()
    stored = page.evaluate(
        f"() => JSON.parse(localStorage.getItem('studio-homework-talked:{homework.pk}') || '[]')"
    )
    assert f'{submission.pk}:{question.pk}' not in stored
    page.get_by_role('button', name='Presentation mode').click()
    expect(page.get_by_test_id('homework-presentation-card')).to_contain_text(
        'Only free-text answer',
    )
    expect(page.get_by_test_id('homework-presentation-talked')).to_have_text(
        'Mark as talked about',
    )
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_new_submission_appears_without_reload_and_keeps_current_item(
    django_server, browser,
):
    from content.models.homework import AnswerType, QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-refresh-1944')
    cohort = _cohort(course)
    homework = _homework(cohort, 'refresh', 'Refresh homework')
    question = _question(
        homework, 'q1-writeup', 'Describe it', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    first = create_user('refresh-one-1944@test.com')
    _submit(homework, first, {question.pk: 'Original answer on screen'})
    homework_id = homework.pk
    question_id = question.pk
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework_id}/submissions',
        wait_until='domcontentloaded',
    )
    page.get_by_role('button', name='Presentation mode').click()
    expect(page.get_by_test_id('homework-presentation-card')).to_contain_text(
        'Original answer on screen',
    )
    expect(page.get_by_test_id('homework-presentation-position')).to_have_text('1 of 1')

    late = create_user('refresh-two-1944@test.com')
    from content.models.homework import Homework, Question

    homework = Homework.objects.get(pk=homework_id)
    question = Question.objects.get(pk=question_id)
    _submit(homework, late, {question.pk: 'Arrived during office hours'})
    _close()

    expect(page.get_by_test_id('homework-submissions-count')).to_contain_text(
        '2 submission', timeout=10000,
    )
    expect(page.get_by_test_id('homework-presentation-card')).to_contain_text(
        'Original answer on screen',
    )
    expect(page.get_by_test_id('homework-presentation-position')).to_have_text('2 of 2')
    page.get_by_role('button', name='Previous').click()
    expect(page.get_by_test_id('homework-presentation-card')).to_contain_text(
        'Arrived during office hours',
    )
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_markdown_renders_in_studio_accordion_and_presentation(django_server, browser):
    from content.models.homework import AnswerType, QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-md-1944')
    cohort = _cohort(course)
    homework = _homework(cohort, 'markdown', 'Markdown homework')
    question = _question(
        homework, 'q1-writeup', 'Describe it', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    learner = create_user('markdown-1944@test.com')
    _submit(
        homework, learner,
        {question.pk: 'I **shipped** it.\n\n- demo\n- notes\n\n[docs](https://example.com/guide)'},
    )
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework.pk}/submissions',
        wait_until='domcontentloaded',
    )
    page.locator('[data-testid="homework-submission-row"] summary').click()
    answer = page.get_by_test_id('homework-submission-answer-text')
    expect(answer.locator('strong')).to_have_text('shipped')
    expect(answer.locator('li')).to_have_count(2)
    expect(answer.locator('a')).to_have_attribute('href', 'https://example.com/guide')
    expect(answer).not_to_contain_text('I **shipped** it.')
    page.get_by_role('button', name='Presentation mode').click()
    card = page.get_by_test_id('homework-presentation-answer')
    expect(card.locator('strong')).to_have_text('shipped')
    expect(card.locator('li')).to_have_count(2)
    expect(card.locator('a')).to_have_attribute('href', 'https://example.com/guide')
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_member_review_preview_renders_markdown(django_server, browser):
    from content.models import Module, Unit
    from content.models.homework import AnswerType, QuestionType

    email = 'member-md-1944@test.com'
    member = create_user(email)
    course = _course('member-md-1944')
    cohort = _cohort(course)
    module = Module.objects.create(course=course, title='Module', slug='module')
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=module, title='Homework', slug='homework', kind='homework',
        content_id=content_id,
        homework='Intro\n\n## Question 1. Build\nDescribe what you shipped.\n',
    )
    homework = _homework(cohort, 'homework', 'Homework', content_id=content_id)
    question = _question(
        homework, 'q1-build', 'Describe what you shipped.',
        QuestionType.FREE_FORM_LONG, answer_type=AnswerType.ANY,
    )
    other = create_user('other-md-1944@test.com')
    _submit(
        homework, member,
        {question.pk: 'I **shipped** it.\n\n- demo\n- writeup'},
        homework_link='https://github.com/member/ship',
    )
    _submit(
        homework, other,
        {question.pk: 'Secret other-student answer xyzzy-1944'},
        homework_link='https://github.com/other/ship',
    )
    unit_url = unit.get_absolute_url()
    _close()

    context = auth_context(browser, email)
    page = context.new_page()
    page.goto(f'{django_server}{unit_url}/review', wait_until='domcontentloaded')
    review = page.get_by_test_id('homework-review-summary')
    expect(review.locator('.prose strong')).to_have_text('shipped')
    expect(review.locator('.prose li')).to_have_count(2)
    expect(review).not_to_contain_text('I **shipped** it.')
    expect(review).not_to_contain_text('Secret other-student answer xyzzy-1944')
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_multiple_choice_homework_shows_empty_presentation(django_server, browser):
    from content.models.homework import QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-mc-1944')
    cohort = _cohort(course)
    homework = _homework(cohort, 'mc', 'MC homework')
    question = _question(
        homework, 'q1-pick', 'Pick one', QuestionType.MULTIPLE_CHOICE,
        possible_answers='Alpha\nBeta', correct_answer='1',
    )
    one = create_user('mc-one-1944@test.com')
    two = create_user('mc-two-1944@test.com')
    _submit(homework, one, {question.pk: '1'})
    _submit(homework, two, {question.pk: '2'})
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework.pk}/submissions',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_test_id('homework-submission-row')).to_have_count(2)
    page.locator('[data-testid="homework-submission-row"] summary').first.click()
    expect(page.get_by_test_id('homework-submission-answer-text').first).to_be_visible()
    page.get_by_role('button', name='Presentation mode').click()
    expect(page.get_by_test_id('homework-presentation-empty')).to_have_text(
        'No free-text answers to present yet.',
    )
    expect(page.get_by_test_id('homework-presentation-card')).to_be_hidden()
    page.get_by_role('button', name='Exit presentation').click()
    expect(page.get_by_test_id('homework-submission-row')).to_have_count(2)
    expect(page.get_by_text('Re-score')).to_have_count(0)
    expect(page.get_by_text('Export')).to_have_count(0)
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_talked_marks_are_per_homework(django_server, browser):
    from content.models.homework import AnswerType, QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-split-1944')
    cohort = _cohort(course)
    homework_a = _homework(cohort, 'a', 'Homework A')
    homework_b = _homework(cohort, 'b', 'Homework B')
    question_a = _question(
        homework_a, 'q1-writeup', 'A', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    question_b = _question(
        homework_b, 'q1-writeup', 'B', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    learner = create_user('split-1944@test.com')
    submission_a = _submit(homework_a, learner, {question_a.pk: 'Answer A'})
    _submit(homework_b, learner, {question_b.pk: 'Answer B'})
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework_b.pk}/submissions',
        wait_until='domcontentloaded',
    )
    page.evaluate(
        f"() => localStorage.setItem('studio-homework-talked:{homework_a.pk}', "
        f"JSON.stringify(['{submission_a.pk}:{question_a.pk}']))"
    )
    page.reload(wait_until='domcontentloaded')
    page.locator('[data-testid="homework-submission-row"] summary').click()
    expect(page.get_by_test_id('homework-talked-toggle')).to_be_hidden()
    page.get_by_role('button', name='Presentation mode').click()
    expect(page.get_by_test_id('homework-presentation-talked')).to_have_text(
        'Mark as talked about',
    )
    page.get_by_role('button', name='Mark as talked about').click()
    stored_b = page.evaluate(
        f"() => JSON.parse(localStorage.getItem('studio-homework-talked:{homework_b.pk}'))"
    )
    stored_a = page.evaluate(
        f"() => JSON.parse(localStorage.getItem('studio-homework-talked:{homework_a.pk}'))"
    )
    assert stored_b
    assert f'{submission_a.pk}:{question_a.pk}' in stored_a
    page.get_by_role('button', name='Exit presentation').click()
    page.goto(
        f'{django_server}/studio/homeworks/{homework_a.pk}/submissions',
        wait_until='domcontentloaded',
    )
    page.locator('[data-testid="homework-submission-row"] summary').click()
    expect(page.get_by_test_id('homework-talked-toggle')).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_empty_submissions_wait_for_the_first_free_text(django_server, browser):
    from content.models.homework import AnswerType, Homework, Question, QuestionType

    create_staff_user(email=STAFF_EMAIL)
    course = _course('present-empty-1944')
    cohort = _cohort(course)
    homework = _homework(cohort, 'empty', 'Empty homework')
    question = _question(
        homework, 'q1-writeup', 'Describe it', QuestionType.FREE_FORM_LONG,
        answer_type=AnswerType.ANY,
    )
    homework_id = homework.pk
    question_id = question.pk
    _close()

    context = auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/homeworks/{homework_id}/submissions',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_text('No submissions for this homework yet.')).to_be_visible()
    page.get_by_role('button', name='Presentation mode').click()
    expect(page.get_by_test_id('homework-presentation-empty')).to_have_text(
        'No free-text answers to present yet.',
    )

    learner = create_user('empty-first-1944@test.com')
    homework = Homework.objects.get(pk=homework_id)
    question = Question.objects.get(pk=question_id)
    _submit(homework, learner, {question.pk: 'First arriving writeup'})
    _close()

    expect(page.get_by_test_id('homework-presentation-card')).to_contain_text(
        'First arriving writeup', timeout=10000,
    )
    expect(page.get_by_test_id('homework-presentation-position')).to_have_text('1 of 1')
    context.close()
