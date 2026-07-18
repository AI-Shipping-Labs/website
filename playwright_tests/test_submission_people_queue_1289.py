"""End-to-end operator journeys for submission people linkage and review (#1289)."""

import os
import uuid

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
]


def _suffix():
    return uuid.uuid4().hex[:8]


def _staff_page(browser, suffix, *, viewport=None):
    email = f'queue-staff-{suffix}@test.com'
    staff = create_staff_user(email)
    context = auth_context(browser, email)
    if viewport:
        context.close()
        from playwright_tests.conftest import create_session_for_user

        context = browser.new_context(viewport=viewport)
        context.add_cookies([{
            'name': 'sessionid', 'value': create_session_for_user(email),
            'domain': '127.0.0.1', 'path': '/',
        }])
    return staff, context, context.new_page()


def _response(suffix, *, purpose='onboarding', status='submitted', crm=False):
    from crm.models import CRMRecord
    from questionnaires.models import Questionnaire, Response, ResponseQuestion

    questionnaire = Questionnaire.objects.create(
        title=f'{purpose.title()} queue {suffix}',
        slug=f'{purpose}-queue-{suffix}',
        purpose=purpose,
    )
    user = create_user(
        f'long.respondent.{suffix}@example.test', first_name='Queue',
    )
    response = Response.objects.create(
        questionnaire=questionnaire, respondent=user,
    )
    ResponseQuestion.objects.create(
        response=response, question_type='long_text',
        prompt='What are you planning to ship?',
    )
    if status == 'submitted':
        response.mark_submitted()
    if crm:
        CRMRecord.objects.create(user=user)
    connection.close()
    return questionnaire, user, response


def test_onboarding_operator_reaches_respondent_and_crm_in_one_step(
    django_server, browser,
):
    suffix = _suffix()
    questionnaire, user, response = _response(suffix, crm=True)
    _staff, context, page = _staff_page(browser, suffix)
    page.goto(f'{django_server}/studio/questionnaires/{questionnaire.pk}/responses/')
    page.get_by_role('link', name=f'Open Studio profile for {user.email}').click()
    page.wait_for_url(f'**/studio/users/{user.pk}/')
    page.go_back()
    crm_link = page.get_by_role('link', name=f'Open CRM record for {user.email}')
    crm_link.click()
    expect(page).to_have_url(f'{django_server}/studio/crm/{user.crm_record.pk}/')
    page.goto(
        f'{django_server}/studio/questionnaires/{questionnaire.pk}/responses/'
        f'{response.pk}/'
    )
    expect(page.get_by_role('link', name=f'Open Studio profile for {user.email}')).to_be_visible()
    expect(page.get_by_role('link', name=f'Open CRM record for {user.email}')).to_be_visible()
    context.close()


def test_respondent_without_crm_has_profile_and_cross_parent_is_404(
    django_server, browser,
):
    suffix = _suffix()
    questionnaire, user, response = _response(suffix)
    other, _user, _response_row = _response(f'other-{suffix}', purpose='general')
    _staff, context, page = _staff_page(browser, suffix)
    page.goto(f'{django_server}/studio/questionnaires/{questionnaire.pk}/responses/')
    expect(page.get_by_role('link', name=f'Open Studio profile for {user.email}')).to_be_visible()
    expect(
        page.get_by_role('link', name=f'Open CRM record for {user.email}'),
    ).to_have_count(0)
    wrong = page.request.get(
        f'{django_server}/studio/questionnaires/{other.pk}/responses/{response.pk}/'
    )
    assert wrong.status == 404
    context.close()


def test_project_reviewer_uses_submitter_then_canonical_alias(
    django_server, browser,
):
    from accounts.models import EmailAlias
    from content.models import Project

    suffix = _suffix()
    submitter = create_user(f'author-{suffix}@test.com')
    project = Project.objects.create(
        title=f'Author project {suffix}', slug=f'author-project-{suffix}',
        date=timezone.localdate(), author='Different public byline',
        submitter=submitter, status='pending_review',
    )
    alias_owner = create_user(f'canonical-{suffix}@test.com')
    alias = f'legacy-{suffix}@test.com'
    EmailAlias.objects.create(user=alias_owner, email=alias)
    legacy = Project.objects.create(
        title=f'Legacy project {suffix}', slug=f'legacy-project-{suffix}',
        date=timezone.localdate(), author=alias, status='pending_review',
    )
    connection.close()
    _staff, context, page = _staff_page(browser, suffix)
    page.goto(f'{django_server}/studio/projects/{project.pk}/review')
    page.get_by_test_id('project-review-author-profile').click()
    page.wait_for_url(f'**/studio/users/{submitter.pk}/')
    page.goto(f'{django_server}/studio/projects/{legacy.pk}/review')
    page.get_by_test_id('project-review-author-profile').click()
    page.wait_for_url(f'**/studio/users/{alias_owner.pk}/')
    context.close()


def test_project_reviewer_never_guesses_plain_or_blank_author(
    django_server, browser,
):
    from content.models import Project

    suffix = _suffix()
    projects = [
        Project.objects.create(
            title=f'Plain {index} {suffix}', slug=f'plain-{index}-{suffix}',
            date=timezone.localdate(), author=author, status='pending_review',
        )
        for index, author in enumerate(('Same Name', 'not-an-email', '', '<b>Unsafe</b>'))
    ]
    connection.close()
    _staff, context, page = _staff_page(browser, suffix)
    for project in projects:
        page.goto(f'{django_server}/studio/projects/{project.pk}/review')
        expect(page.get_by_test_id('project-review-author-profile')).to_have_count(0)
        expect(page.get_by_test_id('project-review-author')).to_be_visible()
    context.close()


def test_dashboard_opens_only_real_onboarding_backlog_newest_first(
    django_server, browser,
):
    suffix = _suffix()
    _q1, _u1, older = _response(f'old-{suffix}')
    _q2, _u2, newer = _response(f'new-{suffix}')
    _q3, _u3, _draft = _response(f'draft-{suffix}', status='draft')
    _q4, _u4, feedback = _response(f'feedback-{suffix}', purpose='feedback')
    from questionnaires.models import Response

    Response.objects.filter(pk=feedback.pk).update(reviewed_at=timezone.now())
    connection.close()
    _staff, context, page = _staff_page(browser, suffix)
    page.goto(f'{django_server}/studio/')
    attention = page.get_by_role('link', name='2 onboarding responses awaiting review')
    expect(attention).to_be_visible()
    attention.click()
    page.wait_for_url('**/studio/questionnaire-responses/?status=submitted&review=awaiting&purpose=onboarding')
    rows = page.get_by_test_id('questionnaire-response-queue-row')
    assert rows.count() == 2
    assert newer.respondent.email in rows.nth(0).inner_text()
    assert older.respondent.email in rows.nth(1).inner_text()
    context.close()


def test_operator_reviews_and_reopens_response_with_feedback(
    django_server, browser,
):
    suffix = _suffix()
    questionnaire, _user, response = _response(suffix)
    _staff, context, page = _staff_page(browser, suffix)
    page.goto(
        f'{django_server}/studio/questionnaire-responses/'
        f'?status=submitted&review=awaiting&questionnaire={questionnaire.pk}'
    )
    page.get_by_test_id('questionnaire-response-mark-reviewed').click()
    expect(page.get_by_text('Response marked reviewed.')).to_be_visible()
    expect(page.get_by_test_id('questionnaire-response-queue-row')).to_have_count(0)
    page.goto(
        f'{django_server}/studio/questionnaires/{questionnaire.pk}/responses/'
        f'{response.pk}/'
    )
    page.get_by_test_id('response-detail-reopen').click()
    expect(page.get_by_text('Response marked awaiting review.')).to_be_visible()
    context.close()


def test_questionnaire_owner_drills_from_total_and_submitted_counts(
    django_server, browser,
):
    from questionnaires.models import Response

    suffix = _suffix()
    questionnaire, _user, _first = _response(suffix)
    for index, status in enumerate(('draft', 'draft', 'submitted', 'submitted')):
        user = create_user(f'count-{index}-{suffix}@test.com')
        row = Response.objects.create(questionnaire=questionnaire, respondent=user)
        if status == 'submitted':
            row.mark_submitted()
    connection.close()
    _staff, context, page = _staff_page(browser, suffix)
    page.goto(f'{django_server}/studio/questionnaires/?q={suffix}')
    row = page.get_by_test_id('questionnaire-row')
    expect(row.get_by_test_id('questionnaire-response-count')).to_have_text('5')
    expect(row.get_by_test_id('questionnaire-submitted-count')).to_have_text('3')
    row.get_by_test_id('questionnaire-submitted-count').get_by_role('link').click()
    expect(page.get_by_test_id('questionnaire-response-queue-row')).to_have_count(3)
    context.close()


def test_operator_composes_queue_filters_and_preserves_pager_context(
    django_server, browser,
):
    suffix = _suffix()
    questionnaire, _user, _response_row = _response(suffix)
    from questionnaires.models import Response

    for index in range(51):
        member = create_user(f'page-{index:02d}-{suffix}@test.com')
        row = Response.objects.create(questionnaire=questionnaire, respondent=member)
        row.mark_submitted()
    connection.close()
    _staff, context, page = _staff_page(browser, suffix)
    page.goto(
        f'{django_server}/studio/questionnaire-responses/?status=submitted'
        f'&review=awaiting&purpose=onboarding&questionnaire={questionnaire.pk}&q={suffix}'
    )
    expect(page.get_by_test_id('questionnaire-response-queue-row')).to_have_count(50)
    next_link = page.get_by_test_id('questionnaire-response-queue-pager-next')
    href = next_link.get_attribute('href')
    assert 'purpose=onboarding' in href and f'questionnaire={questionnaire.pk}' in href
    next_link.click()
    expect(page.get_by_test_id('questionnaire-response-queue-row')).to_have_count(2)
    context.close()


def test_historical_review_label_does_not_raise_dashboard_alarm(
    django_server, browser,
):
    from questionnaires.models import Response

    suffix = _suffix()
    questionnaire, user, response = _response(suffix)
    Response.objects.filter(pk=response.pk).update(
        reviewed_at=response.submitted_at, reviewed_by=None,
    )
    connection.close()
    _staff, context, page = _staff_page(browser, suffix)
    page.goto(
        f'{django_server}/studio/questionnaire-responses/?status=submitted'
        f'&review=reviewed&questionnaire={questionnaire.pk}'
    )
    expect(page.get_by_text('Reviewed before queue launch')).to_be_visible()
    expect(page.get_by_text(user.email)).to_be_visible()
    context.close()


def test_outsiders_cannot_enumerate_or_review_submissions(
    django_server, browser,
):
    suffix = _suffix()
    questionnaire, user, response = _response(suffix)
    anonymous = browser.new_page()
    anonymous.goto(f'{django_server}/studio/questionnaire-responses/')
    expect(anonymous).to_have_url(
        f'{django_server}/accounts/login/?next=/studio/questionnaire-responses/',
    )
    anonymous.close()

    nonstaff_email = f'nonstaff-{suffix}@test.com'
    create_user(nonstaff_email)
    context = auth_context(browser, nonstaff_email)
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/questionnaires/{questionnaire.pk}/responses/'
        f'{response.pk}/'
    )
    assert page.locator('body').inner_text() == 'Staff access required'
    assert user.email not in page.content()
    context.close()


@pytest.mark.visual_regression
def test_submission_triage_phone_layout_and_screenshot_matrix(
    django_server, browser, tmp_path,
):
    from content.models import Project
    from questionnaires.models import Response

    suffix = _suffix()
    questionnaire, user, response = _response(suffix, crm=True)
    no_crm_questionnaire, _no_crm_user, no_crm_response = _response(
        f'no-crm-{suffix}', purpose='general',
    )
    linked_project = Project.objects.create(
        title=f'Linked visual project {suffix}',
        slug=f'linked-visual-project-{suffix}',
        date=timezone.localdate(), author='Linked community author',
        submitter=user, status='pending_review',
    )
    unlinked_project = Project.objects.create(
        title=f'Unlinked visual project {suffix}',
        slug=f'unlinked-visual-project-{suffix}',
        date=timezone.localdate(), author='<Long unlinked author byline>',
        status='pending_review',
    )
    connection.close()
    _staff, context, page = _staff_page(
        browser, suffix, viewport={'width': 393, 'height': 852},
    )
    paths = {
        'questionnaire-list': f'/studio/questionnaires/?q={suffix}',
        'queue': (
            f'/studio/questionnaire-responses/?status=submitted&review=awaiting'
            f'&questionnaire={questionnaire.pk}'
        ),
        'response-list-with-crm': (
            f'/studio/questionnaires/{questionnaire.pk}/responses/'
        ),
        'response-detail-with-crm': (
            f'/studio/questionnaires/{questionnaire.pk}/responses/{response.pk}/'
        ),
        'response-list-without-crm': (
            f'/studio/questionnaires/{no_crm_questionnaire.pk}/responses/'
        ),
        'response-detail-without-crm': (
            f'/studio/questionnaires/{no_crm_questionnaire.pk}/responses/'
            f'{no_crm_response.pk}/'
        ),
        'project-linked': f'/studio/projects/{linked_project.pk}/review',
        'project-unlinked': f'/studio/projects/{unlinked_project.pk}/review',
        'dashboard-attention': '/studio/',
    }
    page.goto(f'{django_server}/studio/', wait_until='domcontentloaded')
    consent = page.get_by_test_id('analytics-consent-deny')
    if consent.is_visible():
        with page.expect_navigation(wait_until='domcontentloaded'):
            consent.click()
        assert page.get_by_test_id('analytics-consent-panel').is_hidden()

    def capture_matrix(matrix_paths):
        for width, height, viewport_label in (
            (1280, 900, 'desktop'), (393, 852, '393'),
        ):
            page.set_viewport_size({'width': width, 'height': height})
            for theme in ('light', 'dark'):
                page.evaluate(
                    "theme => localStorage.setItem('theme', theme)", theme,
                )
                for label, path in matrix_paths.items():
                    page.goto(
                        f'{django_server}{path}', wait_until='domcontentloaded',
                    )
                    assert page.locator('h1').count() > 0
                    assert 'Page not found' not in page.locator('body').inner_text()
                    assert page.evaluate(
                        'document.documentElement.scrollWidth <= '
                        'document.documentElement.clientWidth + 2'
                    )
                    page.screenshot(
                        path=str(
                            tmp_path
                            / f'issue-1289-{label}-{viewport_label}-{theme}.png'
                        ),
                        full_page=True,
                    )

    capture_matrix(paths)
    Response.objects.filter(pk=response.pk).update(
        reviewed_at=timezone.now(), reviewed_by=None,
    )
    connection.close()
    capture_matrix({'dashboard-zero': '/studio/'})
    context.close()
