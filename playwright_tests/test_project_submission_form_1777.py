"""Learner-facing project submission must keep the shared form, minus FAQ (#1777).

The dated attempt route and the undated ``/courses/<slug>/submit`` slot both
embed ``coursework/_project_submission_form.html``. These journeys fail if
either page silently shrinks back to GitHub + commit ID.
"""

import os
from datetime import timedelta

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

GITHUB_URL = 'https://github.com/learner/project-1777'
COMMIT_ID = 'a1b2c3d'
TIME_SPENT = '6.5'
CERT_NAME = 'Learner A. Display'
PUBLIC_LINKS = (
    'https://www.linkedin.com/posts/project-1777-one',
    'https://www.linkedin.com/posts/project-1777-two',
)
SHARED_FIELD_ORDER = (
    'github_link',
    'commit_id',
    'learning_in_public_links',
    'time_spent',
    'description',
    'certificate_name',
)


def _seed_course(email, *, slug, dated):
    """A peer-review course with a cohort enrollment; dated adds one attempt."""
    from django.db import connection
    from django.utils import timezone

    from content.models import Cohort, Course
    from content.models.cohort import CohortEnrollment
    from content.models.peer_review import CourseProject

    user = create_user(email, first_name='Learner')
    course = Course.objects.create(
        title='Shared project form',
        slug=slug,
        status='published',
        required_level=0,
        peer_review_enabled=True,
        peer_review_count=2,
    )
    today = timezone.localdate()
    cohort = Cohort.objects.create(
        course=course,
        name='Form cohort',
        external_key=f'{slug}-cohort',
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=90),
    )
    CohortEnrollment.objects.create(cohort=cohort, user=user)
    attempt_id = None
    if dated:
        now = timezone.now()
        attempt = CourseProject.objects.create(
            course=course,
            cohort=cohort,
            slug='first',
            title='First attempt',
            submission_due_at=now + timedelta(days=1),
            review_due_at=now + timedelta(days=8),
        )
        attempt_id = attempt.pk
        submit_path = f'/courses/{slug}/projects/first/submit'
    else:
        submit_path = f'/courses/{slug}/submit'
    connection.close()
    return {
        'course_id': course.pk,
        'attempt_id': attempt_id,
        'submit_path': submit_path,
    }


def _learner_context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


def _form(page):
    return page.locator('[data-project-form]')


def _assert_shared_fields(page):
    form = _form(page)
    expect(form).to_be_visible()
    fields = form.locator('[data-project-field]')
    expect(fields).to_have_count(len(SHARED_FIELD_ORDER))
    for index, name in enumerate(SHARED_FIELD_ORDER):
        expect(fields.nth(index)).to_have_attribute('data-project-field', name)
        expect(form.locator(f'[name="{name}"]').first).to_be_visible()
    expect(form.locator('[data-commit-id-help]')).to_contain_text(
        'Where do I find the commit ID?',
    )
    expect(form.locator('[name="faq_contribution_url"]')).to_have_count(0)
    expect(page.locator('[data-project-field="faq_contribution_url"]')).to_have_count(0)
    expect(form.locator('[data-add-learning-in-public-link]')).to_be_visible()


def _fill_and_save(page):
    form = _form(page)
    form.locator('[data-project-field="github_link"] [name="github_link"]').fill(GITHUB_URL)
    form.locator('[data-project-field="commit_id"] [name="commit_id"]').fill(COMMIT_ID)
    link_inputs = form.locator(
        '[data-project-field="learning_in_public_links"] [name="learning_in_public_links"]',
    )
    expect(link_inputs).to_have_count(1)
    link_inputs.first.fill(PUBLIC_LINKS[0])
    form.locator('[data-add-learning-in-public-link]').click()
    expect(link_inputs).to_have_count(2)
    link_inputs.nth(1).fill(PUBLIC_LINKS[1])
    form.locator('[data-project-field="time_spent"] [name="time_spent"]').fill(TIME_SPENT)
    form.locator('[data-project-field="certificate_name"] [name="certificate_name"]').fill(
        CERT_NAME,
    )
    form.locator('[data-project-save]').click()
    expect(page.get_by_role('status')).to_contain_text('Your project has been submitted')


def _assert_saved_values(page):
    form = _form(page)
    expect(
        form.locator('[data-project-field="github_link"] [name="github_link"]'),
    ).to_have_value(GITHUB_URL)
    expect(
        form.locator('[data-project-field="commit_id"] [name="commit_id"]'),
    ).to_have_value(COMMIT_ID)
    link_inputs = form.locator(
        '[data-project-field="learning_in_public_links"] [name="learning_in_public_links"]',
    )
    expect(link_inputs.nth(0)).to_have_value(PUBLIC_LINKS[0])
    expect(link_inputs.nth(1)).to_have_value(PUBLIC_LINKS[1])
    expect(
        form.locator('[data-project-field="time_spent"] [name="time_spent"]'),
    ).to_have_value(TIME_SPENT)
    expect(
        form.locator('[data-project-field="certificate_name"] [name="certificate_name"]'),
    ).to_have_value(CERT_NAME)
    expect(form.locator('[name="faq_contribution_url"]')).to_have_count(0)


def _assert_persisted(email, *, course_id, attempt_id):
    from community_base.curriculum.models import Enrollment as CurriculumEnrollment
    from django.db import connection

    from accounts.models import User
    from content.models import ProjectSubmission

    user = User.objects.get(email=email)
    if attempt_id is None:
        submission = ProjectSubmission.objects.get(
            user=user, course_id=course_id, course_project__isnull=True,
        )
    else:
        submission = ProjectSubmission.objects.get(
            user=user, course_project_id=attempt_id,
        )
    assert submission.project_url == GITHUB_URL
    assert submission.commit_id == COMMIT_ID
    assert submission.learning_in_public_links == list(PUBLIC_LINKS)
    assert submission.time_spent == 6.5
    enrollment = CurriculumEnrollment.objects.get(
        user=user, unenrolled_at__isnull=True,
    )
    assert enrollment.certificate_name == CERT_NAME
    connection.close()


def _run_submit_journey(django_server, browser, *, email, slug, dated):
    seeded = _seed_course(email, slug=slug, dated=dated)
    context = _learner_context(browser, email)
    page = context.new_page()
    page.goto(
        f'{django_server}{seeded["submit_path"]}',
        wait_until='domcontentloaded',
    )
    _assert_shared_fields(page)
    _fill_and_save(page)
    _assert_saved_values(page)
    page.reload(wait_until='domcontentloaded')
    _assert_shared_fields(page)
    _assert_saved_values(page)
    _assert_persisted(
        email, course_id=seeded['course_id'], attempt_id=seeded['attempt_id'],
    )
    context.close()


@pytest.mark.core
@browser_journey
def test_dated_attempt_submit_shows_shared_form_and_round_trips(django_server, browser):
    _run_submit_journey(
        django_server, browser,
        email='project-form-dated-1777@test.com',
        slug='shared-form-dated-1777',
        dated=True,
    )


@pytest.mark.core
@browser_journey
def test_undated_submit_shows_shared_form_and_round_trips(django_server, browser):
    _run_submit_journey(
        django_server, browser,
        email='project-form-undated-1777@test.com',
        slug='shared-form-undated-1777',
        dated=False,
    )
