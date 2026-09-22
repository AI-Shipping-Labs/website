"""Course home commitments, assignment handoff, and responsive evidence."""

import datetime
import os
import uuid
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


@pytest.mark.core
@browser_journey
def test_course_home_commitments_to_homework_event_and_reviews(django_server, browser):
    from content.models import Cohort, CohortEnrollment, Course, Module, PeerReview, ProjectSubmission, Unit
    from content.models.homework import Homework, Question
    from content.models.peer_review import CourseProject
    from events.models import Event, EventSeries

    user = create_user('course-home-commitments@test.com')
    user.preferred_timezone = 'Europe/Berlin'
    user.save(update_fields=['preferred_timezone'])
    peer = create_user('course-home-review-peer@test.com')
    course = Course.objects.create(
        title='Build and review a useful project', slug='course-home-commitments',
        status='published', required_level=0,
        peer_review_enabled=True, peer_review_count=1,
    )
    module = Module.objects.create(
        course=course, title='Foundations', slug='foundations', sort_order=1,
    )
    Unit.objects.create(module=module, title='First lesson', slug='first-lesson')
    content_id = uuid.uuid4()
    homework_unit = Unit.objects.create(
        module=module, title='Practice assignment', slug='practice-assignment',
        kind='homework', content_id=content_id,
    )
    now = timezone.now()
    series = EventSeries.objects.create(name='Live cohort sessions', slug='home-commitment-series')
    cohort = Cohort.objects.create(
        course=course, name='Current cohort', external_key='current',
        start_date=now.date() - datetime.timedelta(days=2),
        end_date=now.date() + datetime.timedelta(days=60),
        event_series=series,
    )
    CohortEnrollment.objects.create(user=user, cohort=cohort)
    homework = Homework.objects.create(
        cohort=cohort, content_id=content_id, slug='practice',
        title='Practice assignment', due_date=now + datetime.timedelta(days=2),
    )
    question = Question.objects.create(
        homework=homework, text='What did you build?', question_type='FF',
    )
    event = Event.objects.create(
        event_series=series, slug='upcoming-practice-session',
        title='Upcoming practice session', status='upcoming',
        start_datetime=now + datetime.timedelta(days=1),
        end_datetime=now + datetime.timedelta(days=1, hours=1),
    )
    project = CourseProject.objects.create(
        course=course, cohort=cohort, slug='first-attempt',
        title='Project attempt',
        submission_due_at=now + datetime.timedelta(days=7),
        review_due_at=now + datetime.timedelta(days=14),
        peer_review_count=1,
    )
    ProjectSubmission.objects.create(
        user=user, course=course, course_project=project, cohort=cohort,
        project_url='https://example.com/my-project',
    )
    peer_submission = ProjectSubmission.objects.create(
        user=peer, course=course, course_project=project, cohort=cohort,
        project_url='https://example.com/peer-project',
    )
    PeerReview.objects.create(submission=peer_submission, reviewer=user)
    connection.close()

    context = auth_context(browser, user.email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    page = context.new_page()
    home_url = f'{django_server}/courses/{course.slug}/home?cohort=current'
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.goto(home_url, wait_until='domcontentloaded')
    expect(page.locator('[data-testid="course-home-coming-up"]')).to_contain_text(event.title)
    expect(page.locator('[data-testid="course-home-coming-up"]')).to_contain_text(homework.title)
    expect(page.locator('[data-testid="course-home-assignments"]')).to_contain_text('Continue reviews')
    screenshots = Path('.tmp/screenshots/course-home-1784')
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / 'desktop-light.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('[data-testid="course-home-open-lesson"]').bounding_box()['y'] < 844
    page.screenshot(path=str(screenshots / 'mobile-light.png'), full_page=True)
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until='domcontentloaded')
    page.screenshot(path=str(screenshots / 'mobile-dark.png'), full_page=True)
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.screenshot(path=str(screenshots / 'desktop-dark.png'), full_page=True)

    page.locator('[data-testid="course-home-coming-up"]').get_by_role(
        'link', name='View session',
    ).click()
    expect(page).to_have_url(f'{django_server}{event.get_absolute_url()}')
    page.goto(home_url, wait_until='domcontentloaded')
    page.locator('[data-testid="course-home-assignments"]').get_by_role(
        'link', name='Start homework',
    ).click()
    expect(page).to_have_url(f'{django_server}{homework_unit.get_absolute_url()}?cohort=current')
    page.locator(f'[name="answer_{question.pk}"]').fill('A working prototype')
    page.get_by_role('button', name='Submit homework').click()
    page.goto(home_url, wait_until='domcontentloaded')
    expect(page.locator('[data-testid="course-home-assignments"]')).not_to_contain_text(
        'Start homework',
    )
    page.locator('[data-testid="course-home-completed-assignments"] summary').click()
    expect(page.locator('[data-testid="course-home-completed-assignments"]')).to_contain_text(
        'Submitted',
    )
    page.locator('[data-testid="course-home-assignments"]').get_by_role(
        'link', name='Continue reviews',
    ).click()
    expect(page).to_have_url(
        f'{django_server}/courses/{course.slug}/projects/{project.slug}/reviews',
    )
    context.close()
