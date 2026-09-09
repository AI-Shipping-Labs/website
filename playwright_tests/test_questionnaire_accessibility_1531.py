"""Browser journeys for labelled questionnaire controls (#1531)."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = (
    Path(__file__).parent.parent / '.tmp' / 'aisl-issue-1531-screenshots'
)


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f'{name}.png', full_page=True)


def _feedback_response(email, slug, question_specs):
    from accounts.models import User
    from plans.models import Sprint, SprintEnrollment, SprintFeedbackRequest
    from questionnaires.models import Question, Questionnaire, QuestionOption, Response
    from questionnaires.services import build_response_questions

    _ensure_tiers()
    _create_user(email, tier_slug='main', email_verified=True)
    member = User.objects.get(email=email)
    sprint = Sprint.objects.create(
        name=f'Accessible sprint {slug}',
        slug=slug,
        start_date=timezone.localdate(),
        status='active',
        min_tier_level=0,
    )
    SprintEnrollment.objects.create(sprint=sprint, user=member)
    questionnaire = Questionnaire.objects.create(
        title=f'Accessible feedback {slug}',
        slug=f'{slug}-feedback',
        purpose='feedback',
    )
    for order, spec in enumerate(question_specs):
        question = Question.objects.create(
            questionnaire=questionnaire,
            question_type=spec['type'],
            prompt=spec['prompt'],
            help_text=spec.get('help_text', ''),
            is_required=spec.get('required', False),
            order=order,
        )
        for option_order, option in enumerate(spec.get('options', ())):
            label, allows_free_text = (
                option if isinstance(option, tuple) else (option, False)
            )
            QuestionOption.objects.create(
                question=question,
                label=label,
                allows_free_text=allows_free_text,
                order=option_order,
            )
    SprintFeedbackRequest.objects.create(
        sprint=sprint,
        questionnaire=questionnaire,
    )
    response = Response.objects.create(
        questionnaire=questionnaire,
        respondent=member,
    )
    build_response_questions(response)
    response_questions = {
        question.prompt: question
        for question in response.response_questions.prefetch_related('options')
    }
    connection.close()
    return sprint, response, response_questions


def _feedback_page(browser, django_server, email, sprint, response):
    context = _auth_context(browser, email)
    page = context.new_page()
    page.goto(
        f'{django_server}/sprints/{sprint.slug}/feedback/{response.pk}',
        wait_until='domcontentloaded',
    )
    return page


@browser_journey
def test_member_submits_a_single_choice_through_its_named_group(
    django_server, browser,
):
    prompt = 'Will you join the next sprint?'
    sprint, response, questions = _feedback_response(
        'choice-1531@test.com',
        'choice-1531',
        [{'type': 'single_choice', 'prompt': prompt, 'required': True,
          'options': ('Yes', 'No')}],
    )
    page = _feedback_page(
        browser, django_server, 'choice-1531@test.com', sprint, response,
    )

    group = page.get_by_role('group', name=prompt)
    expect(group).to_be_visible()
    assert page.locator(f'label[for="question_{questions[prompt].pk}"]').count() == 0
    group.get_by_role('radio', name='Yes', exact=True).check()
    page.get_by_role('button', name='Submit feedback').click()

    page.wait_for_url(f'{django_server}/sprints/{sprint.slug}')
    page.goto(
        f'{django_server}/sprints/{sprint.slug}/feedback/{response.pk}',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_text('Yes', exact=True)).to_be_visible()
    _shot(page, 'single_choice_submitted')


@browser_journey
def test_member_saves_labelled_other_details(django_server, browser):
    prompt = 'How did you find us?'
    sprint, response, _questions = _feedback_response(
        'other-1531@test.com',
        'other-1531',
        [{'type': 'single_choice', 'prompt': prompt,
          'options': ('Search', ('Other', True))}],
    )
    page = _feedback_page(
        browser, django_server, 'other-1531@test.com', sprint, response,
    )

    group = page.get_by_role('group', name=prompt)
    group.get_by_role('radio', name='Other', exact=True).check()
    details = page.get_by_label('Other details', exact=True)
    expect(details).to_be_visible()
    expect(details).to_have_attribute('placeholder', 'Please describe')
    details.fill('A friend told me')
    page.get_by_role('button', name='Save draft').click()
    page.wait_for_load_state('domcontentloaded')
    page.reload(wait_until='domcontentloaded')

    expect(page.get_by_role('radio', name='Other', exact=True)).to_be_checked()
    expect(page.get_by_label('Other details', exact=True)).to_have_value(
        'A friend told me',
    )
    _shot(page, 'other_details_restored')


@browser_journey
def test_required_question_error_names_the_blocked_control(django_server, browser):
    required_prompt = 'How did this sprint go for you?'
    sprint, response, _questions = _feedback_response(
        'required-1531@test.com',
        'required-1531',
        [
            {'type': 'long_text', 'prompt': required_prompt, 'required': True},
            {'type': 'single_choice', 'prompt': 'Was the pace useful?',
             'options': ('Yes', 'No')},
        ],
    )
    page = _feedback_page(
        browser, django_server, 'required-1531@test.com', sprint, response,
    )
    page.get_by_role('radio', name='Yes', exact=True).check()
    page.get_by_role('button', name='Submit feedback').click()

    field = page.get_by_role('textbox', name=required_prompt)
    expect(field).to_have_attribute('aria-invalid', 'true')
    error_id = field.get_attribute('aria-describedby')
    assert error_id
    expect(page.locator(f'#{error_id}')).to_have_text('This question is required.')
    _shot(page, 'required_error_association')

    field.fill('I shipped the planned prototype.')
    page.get_by_role('button', name='Submit feedback').click()
    page.wait_for_url(f'{django_server}/sprints/{sprint.slug}')
    page.goto(
        f'{django_server}/sprints/{sprint.slug}/feedback/{response.pk}',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_text('I shipped the planned prototype.', exact=True)).to_be_visible()
    _shot(page, 'required_error_recovered')


@browser_journey
def test_onboarding_text_help_describes_its_labelled_control(django_server, browser):
    from accounts.models import User
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting
    from questionnaires.models import Questionnaire, Response, ResponseQuestion

    _ensure_tiers()
    _create_user('onboarding-1531@test.com', tier_slug='main', email_verified=True)
    member = User.objects.get(email='onboarding-1531@test.com')
    questionnaire = Questionnaire.objects.create(
        title='Accessible onboarding 1531',
        slug='accessible-onboarding-1531',
        purpose='onboarding',
    )
    response = Response.objects.create(questionnaire=questionnaire, respondent=member)
    ResponseQuestion.objects.create(
        response=response,
        question_type='text',
        prompt='What is your current role?',
        help_text='Job title is enough',
        is_required=True,
    )
    IntegrationSetting.objects.update_or_create(
        key='ONBOARDING_AI_ENABLED',
        defaults={'value': 'false', 'is_secret': False, 'group': 'llm'},
    )
    clear_config_cache()
    connection.close()

    context = _auth_context(browser, 'onboarding-1531@test.com')
    page = context.new_page()
    page.goto(
        f'{django_server}/onboarding/{response.pk}',
        wait_until='domcontentloaded',
    )
    field = page.get_by_role('textbox', name='What is your current role?')
    description_id = field.get_attribute('aria-describedby')
    assert description_id
    expect(page.locator(f'#{description_id}')).to_have_text('Job title is enough')
    field.fill('Data engineer')
    page.get_by_role('button', name='Submit', exact=True).click()
    expect(page.get_by_test_id('onboarding-complete-title')).to_be_visible()


@browser_journey
def test_member_saves_multiple_choices_from_one_named_group(django_server, browser):
    prompt = 'Which topics do you want?'
    sprint, response, _questions = _feedback_response(
        'multiple-1531@test.com',
        'multiple-1531',
        [{'type': 'multiple_choice', 'prompt': prompt,
          'options': ('Agents', 'RAG', 'Evals')}],
    )
    page = _feedback_page(
        browser, django_server, 'multiple-1531@test.com', sprint, response,
    )
    group = page.get_by_role('group', name=prompt)
    group.get_by_role('checkbox', name='Agents', exact=True).check()
    group.get_by_role('checkbox', name='Evals', exact=True).check()
    page.get_by_role('button', name='Save draft').click()
    page.wait_for_load_state('domcontentloaded')
    page.reload(wait_until='domcontentloaded')

    expect(page.get_by_role('checkbox', name='Agents', exact=True)).to_be_checked()
    expect(page.get_by_role('checkbox', name='Evals', exact=True)).to_be_checked()
    expect(page.get_by_role('checkbox', name='RAG', exact=True)).not_to_be_checked()


@browser_journey
def test_staff_creates_questionnaire_with_labelled_fields_and_title_error(
    django_server, browser,
):
    _ensure_tiers()
    _create_staff_user('admin-questionnaire-1531@test.com')
    context = _auth_context(browser, 'admin-questionnaire-1531@test.com')
    page = context.new_page()
    page.goto(f'{django_server}/studio/questionnaires/new', wait_until='domcontentloaded')

    for label in ('Title', 'Slug', 'Purpose', 'Description', 'Active'):
        expect(page.get_by_label(label, exact=True)).to_be_visible()
    _shot(page, 'studio_questionnaire_labels')
    page.get_by_role('button', name='Create questionnaire').click()
    alert = page.get_by_test_id('questionnaire-form-error')
    expect(alert).to_contain_text('Title is required.')
    expect(page.get_by_label('Title', exact=True)).to_have_attribute(
        'aria-invalid', 'true',
    )
    _shot(page, 'studio_title_error')

    page.get_by_label('Title', exact=True).fill('May Sprint Feedback')
    page.get_by_label('Purpose', exact=True).select_option('feedback')
    page.get_by_label('Active', exact=True).check()
    page.get_by_role('button', name='Create questionnaire').click()
    expect(page.get_by_test_id('questionnaire-detail-title')).to_have_text(
        'May Sprint Feedback',
    )
    _shot(page, 'studio_questionnaire_created')


@browser_journey
def test_staff_authors_scale_question_with_named_min_and_max(django_server, browser):
    from questionnaires.models import Questionnaire

    _ensure_tiers()
    _create_staff_user('admin-scale-1531@test.com')
    questionnaire = Questionnaire.objects.create(
        title='Scale authoring 1531', slug='scale-authoring-1531',
    )
    connection.close()
    context = _auth_context(browser, 'admin-scale-1531@test.com')
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/questionnaires/{questionnaire.pk}/questions/new',
        wait_until='domcontentloaded',
    )

    for label in (
        'Type', 'Prompt', 'Help text', 'Required', 'Order', 'Scale min',
        'Scale max', 'Choice options',
    ):
        expect(page.get_by_label(label, exact=True)).to_be_visible()
    page.get_by_label('Type', exact=True).select_option('scale')
    page.get_by_label('Prompt', exact=True).fill('How confident are you?')
    page.get_by_label('Scale min', exact=True).fill('1')
    page.get_by_label('Scale max', exact=True).fill('5')
    _shot(page, 'studio_scale_labels')
    page.get_by_role('button', name='Add question').click()

    expect(page.get_by_text('How confident are you?', exact=True)).to_be_visible()
    _shot(page, 'studio_scale_question')


@browser_journey
def test_staff_adds_labelled_custom_question_to_only_one_member(
    django_server, browser,
):
    from accounts.models import User
    from questionnaires.models import Questionnaire, Response

    _ensure_tiers()
    _create_staff_user('admin-custom-1531@test.com')
    _create_user('custom-one-1531@test.com', tier_slug='main', email_verified=True)
    _create_user('custom-two-1531@test.com', tier_slug='main', email_verified=True)
    questionnaire = Questionnaire.objects.create(
        title='Custom questions 1531', slug='custom-questions-1531',
    )
    first = Response.objects.create(
        questionnaire=questionnaire,
        respondent=User.objects.get(email='custom-one-1531@test.com'),
    )
    second = Response.objects.create(
        questionnaire=questionnaire,
        respondent=User.objects.get(email='custom-two-1531@test.com'),
    )
    connection.close()

    context = _auth_context(browser, 'admin-custom-1531@test.com')
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/questionnaires/{questionnaire.pk}/responses/'
        f'{first.pk}/questions/new',
        wait_until='domcontentloaded',
    )
    for label in (
        'Type', 'Prompt', 'Help text', 'Required', 'Order', 'Scale min',
        'Scale max', 'Choice options',
    ):
        expect(page.get_by_label(label, exact=True)).to_be_visible()
    page.get_by_label('Prompt', exact=True).fill('What blocked you this week?')
    page.get_by_label('Required', exact=True).check()
    page.get_by_role('button', name='Add question').click()
    expect(page.get_by_text('What blocked you this week?', exact=True)).to_be_visible()

    page.goto(
        f'{django_server}/studio/questionnaires/{questionnaire.pk}/responses/'
        f'{second.pk}/',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_text('What blocked you this week?', exact=True)).to_have_count(0)


@browser_journey
def test_keyboard_user_selects_choice_and_submits(django_server, browser):
    prompt = 'Will you join the next sprint?'
    sprint, response, _questions = _feedback_response(
        'keyboard-1531@test.com',
        'keyboard-1531',
        [{'type': 'single_choice', 'prompt': prompt, 'required': True,
          'options': ('Yes', 'No')}],
    )
    page = _feedback_page(
        browser, django_server, 'keyboard-1531@test.com', sprint, response,
    )
    first_radio = page.get_by_role('radio', name='Yes', exact=True)
    for _attempt in range(100):
        page.keyboard.press('Tab')
        if first_radio.evaluate('element => document.activeElement === element'):
            break
    assert first_radio.evaluate('element => document.activeElement === element')
    page.keyboard.press('ArrowDown')
    expect(page.get_by_role('radio', name='No', exact=True)).to_be_checked()

    page.keyboard.press('Tab')
    page.keyboard.press('Tab')
    assert page.get_by_role('button', name='Submit feedback').evaluate(
        'element => document.activeElement === element',
    )
    page.keyboard.press('Enter')
    page.wait_for_url(f'{django_server}/sprints/{sprint.slug}')
    page.goto(
        f'{django_server}/sprints/{sprint.slug}/feedback/{response.pk}',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_text('No', exact=True)).to_be_visible()
