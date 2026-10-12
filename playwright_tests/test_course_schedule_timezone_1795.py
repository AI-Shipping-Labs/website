"""Issue #1795: Buildcamp schedule times follow the viewer's timezone.

The server HTML always renders a truthful explicit fallback zone
(``Europe/Berlin`` for Buildcamp) unless a valid saved preference exists.
On a first visit, ``static/js/course-schedule.js`` rewrites every schedule
instant and the zone label together to the browser zone, with no reload.
These journeys pin the viewer states from the spec: first visit (with and
without a successful passive backfill), saved preference, anonymous
preview, and no-JS / invalid browser detection.
"""

import os
import uuid
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT
from playwright_tests.conftest import create_session_for_user as _create_session
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: seeds the DB and injects session cookies.
pytestmark = pytest.mark.local_only

COURSE_URL = '/courses/ai-buildcamp?view=overview'
PREFERENCE_API = '/account/api/timezone-preference'
CSRF_TOKEN = 'a' * 32
EMAIL_SUFFIX = '@schedule-tz.test'


def _local_label(instant, zone):
    """Mirror schedule_tags.schedule_datetime_local ("Oct 6, 2026 01:59")."""
    local = instant.astimezone(ZoneInfo(zone))
    return f'{local:%b} {local.day}, {local.year} {local:%H}:{local:%M}'


def _seed_schedule(email=None, preferred_timezone='', submitted=False, assign_review=False):
    """Seed one Buildcamp cohort with a dated homework and project attempt.

    The due instant sits at 03:30 UTC, so it is the previous calendar day
    in New York and the same day in Berlin/Tokyo (a local date rollover).
    """
    from django.contrib.auth import get_user_model
    from django.db import connection

    from content.models import (
        Cohort,
        CohortEnrollment,
        Course,
        Homework,
        Module,
        Unit,
    )
    from content.models.peer_review import CourseProject, PeerReview, ProjectSubmission

    User = get_user_model()
    User.objects.filter(email__endswith=EMAIL_SUFFIX).delete()
    Course.objects.filter(slug='ai-buildcamp').delete()
    course = Course.objects.create(
        title='Buildcamp', slug='ai-buildcamp', status='published',
        required_level=0, peer_review_enabled=True,
    )
    week = Module.objects.create(
        course=course, title='Foundation', slug='foundation', sort_order=1,
        available_after_days=0,
    )
    topic = Module.objects.create(
        course=course, parent=week, title='First topic', slug='first-topic',
        sort_order=1,
    )
    unit = Unit.objects.create(
        module=topic, title='Homework 1', slug='homework-1', sort_order=1,
        kind='homework', content_id=uuid.uuid4(),
    )
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='4',
        start_date=date.today() - timedelta(days=14),
        end_date=date.today() + timedelta(days=40),
    )
    due = datetime.now(dt_timezone.utc).replace(
        hour=3, minute=30, second=0, microsecond=0,
    ) + timedelta(days=3)
    Homework.objects.create(
        cohort=cohort, content_id=unit.content_id, slug='hw1',
        title='Homework 1', due_date=due,
    )
    project = CourseProject.objects.create(
        course=course, cohort=cohort, module=topic, slug='attempt-4',
        title='Attempt 4', submission_due_at=due, review_due_at=due,
    )
    if email:
        user, _ = User.objects.get_or_create(email=email)
        user.set_password(DEFAULT_PASSWORD)
        user.email_verified = True
        user.preferred_timezone = preferred_timezone
        user.save()
        CohortEnrollment.objects.create(user=user, cohort=cohort)
        if submitted:
            # A submitted learner also sees the "Reviews by" date.
            ProjectSubmission.objects.create(
                user=user, course=course, course_project=project,
                cohort=cohort, project_url='https://github.com/example/attempt',
            )
    unit_url = unit.get_absolute_url()
    review_url = ''
    if email and assign_review:
        # Another learner's submission assigned to ``email`` for review.
        author, _ = User.objects.get_or_create(email=f'author{EMAIL_SUFFIX}')
        CohortEnrollment.objects.create(user=author, cohort=cohort)
        authored = ProjectSubmission.objects.create(
            user=author, course=course, course_project=project,
            cohort=cohort, project_url='https://github.com/example/authored',
        )
        PeerReview.objects.create(submission=authored, reviewer=user)
        review_url = (
            f'/courses/ai-buildcamp/projects/attempt-4/reviews/{authored.pk}'
        )
    connection.close()
    if assign_review:
        return due, unit_url, review_url
    return due, unit_url


def _stored_timezone(email):
    from django.contrib.auth import get_user_model
    from django.db import connection

    value = get_user_model().objects.get(email=email).preferred_timezone
    connection.close()
    return value


def _authed_context(browser, email, django_db_blocker, timezone_id):
    from django.db import connection

    with django_db_blocker.unblock():
        session_key = _create_session(email)
    connection.close()
    context = browser.new_context(viewport=VIEWPORT, timezone_id=timezone_id)
    context.add_cookies([
        {'name': 'sessionid', 'value': session_key, 'domain': '127.0.0.1', 'path': '/'},
        {'name': 'csrftoken', 'value': CSRF_TOKEN, 'domain': '127.0.0.1', 'path': '/'},
    ])
    return context


def _expand_first_week(page):
    week = page.locator('.syllabus-week').first
    week.locator(':scope > summary').click()
    topic = week.locator('.syllabus-topic').first
    topic.locator(':scope > summary').click()
    expect(topic).to_have_attribute('open', '')


def _expect_schedule_in(page, due, zone, *, reviews=True):
    """Every syllabus instant uses ``zone`` (zone note asserted by callers)."""
    label = _local_label(due, zone)
    expect(page.get_by_test_id('syllabus-homework-deadline')).to_have_text(f'Due {label}')
    expect(page.get_by_test_id('syllabus-module-deadline').first).to_have_text(f'Due {label}')
    instants = page.locator('#syllabus time[data-schedule-instant]')
    # Week summary, topic summary, homework due, project submit (+ review).
    count = 5 if reviews else 4
    expect(instants).to_have_count(count)
    expect(instants).to_have_text([label] * count)
    project_row = page.locator('#syllabus a[href$="/projects/attempt-4/submit"]')
    expect(project_row).to_contain_text(f'Submit by {label}', use_inner_text=True)
    if reviews:
        expect(project_row).to_contain_text(f'Reviews by {label}', use_inner_text=True)


def _expect_attempt_pages_in(page, django_server, due, zone, review_url):
    """Reviews dashboard and review form agree with the attempt header."""
    label = _local_label(due, zone)
    note = f'Deadline times are shown in {zone}.'
    expect(page.locator('[data-timezone-notice-text]')).to_have_text(
        note, use_inner_text=True,
    )
    page.goto(
        f'{django_server}/courses/ai-buildcamp/projects/attempt-4/reviews',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_test_id('peer-review-meta')).to_have_text(
        f'Attempt 4 · Reviews due {label} {zone}', use_inner_text=True,
    )
    expect(page.locator('[data-timezone-notice-text]')).to_have_text(
        note, use_inner_text=True,
    )
    page.goto(f'{django_server}{review_url}', wait_until='domcontentloaded')
    expect(page.get_by_test_id('peer-review-meta')).to_have_text(
        f'Attempt 4 · Due {label} {zone}', use_inner_text=True,
    )
    expect(page.locator('[data-timezone-notice-text]')).to_have_text(
        note, use_inner_text=True,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
@browser_journey
def test_first_visit_localizes_in_place_and_matches_linked_pages(
    django_server, django_db_blocker, browser,
):
    """Empty preference, New York browser: syllabus, attempt and reader agree."""
    email = f'tz-first{EMAIL_SUFFIX}'
    with django_db_blocker.unblock():
        due, unit_url = _seed_schedule(email, submitted=True)
    context = _authed_context(browser, email, django_db_blocker, 'America/New_York')
    page = context.new_page()
    try:
        with page.expect_request(
            lambda r: r.url.endswith(PREFERENCE_API) and r.method == 'POST'
        ) as backfill:
            page.goto(f'{django_server}{COURSE_URL}', wait_until='domcontentloaded')
        page.evaluate("window.__scheduleTzProbe = 'alive'")
        _expand_first_week(page)
        _expect_schedule_in(page, due, 'America/New_York')
        expect(page.get_by_test_id('syllabus-timezone-note')).to_contain_text(
            'Deadline times are shown in America/New_York.',
            use_inner_text=True,
        )
        # Same document: localization never reloaded or navigated the page.
        assert page.evaluate('window.__scheduleTzProbe') == 'alive'
        assert backfill.value.response().ok

        # The passive backfill saved New York, so later pages render it
        # server-side; every linked surface shows the same local deadline.
        page.locator('#syllabus a[href$="/projects/attempt-4/submit"]').click()
        expect(page.get_by_test_id('peer-review-meta')).to_contain_text(
            f'Submit by {_local_label(due, "America/New_York")} America/New_York',
            use_inner_text=True,
        )
        page.goto(f'{django_server}{unit_url}', wait_until='domcontentloaded')
        expect(page.get_by_test_id('homework-due-date').first).to_contain_text(
            'America/New_York',
            use_inner_text=True,
        )
        page.goto(f'{django_server}{COURSE_URL}', wait_until='domcontentloaded')
        expect(page.locator('#syllabus')).to_have_attribute('data-localize', 'false')
        _expand_first_week(page)
        _expect_schedule_in(page, due, 'America/New_York')
        expect(page.get_by_test_id('syllabus-timezone-note')).to_contain_text(
            'Shown in your timezone.',
            use_inner_text=True,
        )
    finally:
        context.close()
    with django_db_blocker.unblock():
        assert _stored_timezone(email) == 'America/New_York'


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_failed_backfill_still_localizes_attempt_header_consistently(
    django_server, django_db_blocker, browser,
):
    """A failed passive write never leaves a Berlin label on a local value."""
    email = f'tz-fail{EMAIL_SUFFIX}'
    with django_db_blocker.unblock():
        due, _unit_url, review_url = _seed_schedule(
            email, submitted=True, assign_review=True,
        )
    context = _authed_context(browser, email, django_db_blocker, 'America/New_York')
    context.route(f'**{PREFERENCE_API}', lambda route: route.abort())
    page = context.new_page()
    try:
        page.goto(
            f'{django_server}/courses/ai-buildcamp/projects/attempt-4/submit',
            wait_until='domcontentloaded',
        )
        meta = page.get_by_test_id('peer-review-meta')
        expect(meta).to_contain_text(
            f'Submit by {_local_label(due, "America/New_York")} America/New_York',
            use_inner_text=True,
        )
        expect(meta).not_to_contain_text('Europe/Berlin', use_inner_text=True)
        expect(meta.locator('time')).to_have_attribute(
            'datetime', due.strftime('%Y-%m-%dT%H:%M:%SZ'),
        )
        _expect_attempt_pages_in(page, django_server, due, 'America/New_York', review_url)
    finally:
        context.close()
    with django_db_blocker.unblock():
        assert _stored_timezone(email) == ''


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_saved_preference_wins_over_browser_zone(
    django_server, django_db_blocker, browser,
):
    """Saved America/New_York renders from first paint, browser in Berlin."""
    email = f'tz-saved{EMAIL_SUFFIX}'
    with django_db_blocker.unblock():
        due, _unit_url = _seed_schedule(
            email, preferred_timezone='America/New_York', submitted=True,
        )
    context = _authed_context(browser, email, django_db_blocker, 'Europe/Berlin')
    page = context.new_page()
    requested = []
    page.on('request', lambda request: requested.append(request.url))
    try:
        page.goto(f'{django_server}{COURSE_URL}', wait_until='domcontentloaded')
        expect(page.locator('#syllabus')).to_have_attribute('data-localize', 'false')
        _expand_first_week(page)
        _expect_schedule_in(page, due, 'America/New_York')
        expect(page.get_by_test_id('syllabus-timezone-note')).to_contain_text(
            'Shown in your timezone.',
            use_inner_text=True,
        )
        assert not any(PREFERENCE_API in url for url in requested)
    finally:
        context.close()
    with django_db_blocker.unblock():
        assert _stored_timezone(email) == 'America/New_York'


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_anonymous_preview_localizes_without_preference_save(
    django_server, django_db_blocker, browser,
):
    """Anonymous Tokyo visitor: localized preview, zero preference traffic."""
    with django_db_blocker.unblock():
        due, _unit_url = _seed_schedule()
    context = browser.new_context(viewport={'width': 393, 'height': 852}, timezone_id='Asia/Tokyo')
    page = context.new_page()
    requested = []
    page.on('request', lambda request: requested.append(request.url))
    try:
        page.goto(f'{django_server}{COURSE_URL}', wait_until='domcontentloaded')
        _expand_first_week(page)
        label = _local_label(due, 'Asia/Tokyo')
        expect(page.get_by_test_id('syllabus-homework-deadline')).to_have_text(f'Due {label}')
        # Preview visitors see the project date without the attempt action.
        expect(page.get_by_test_id('syllabus-project-preview')).to_contain_text(
            f'Submit by {label}',
            use_inner_text=True,
        )
        expect(page.locator('#syllabus a[href$="/projects/attempt-4/submit"]')).to_have_count(0)
        expect(page.get_by_test_id('syllabus-timezone-note')).to_contain_text(
            'Deadline times are shown in Asia/Tokyo.',
            use_inner_text=True,
        )
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not any(PREFERENCE_API in url for url in requested)
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_no_javascript_or_invalid_detection_keeps_labeled_fallback(
    django_server, django_db_blocker, browser,
):
    """Without a usable browser zone the Berlin label matches every date."""
    with django_db_blocker.unblock():
        due, _unit_url = _seed_schedule()
    berlin = _local_label(due, 'Europe/Berlin')

    no_js = browser.new_context(viewport=VIEWPORT, java_script_enabled=False,
                                timezone_id='America/New_York')
    page = no_js.new_page()
    try:
        page.goto(f'{django_server}{COURSE_URL}', wait_until='domcontentloaded')
        expect(page.get_by_test_id('syllabus-homework-deadline')).to_have_text(f'Due {berlin}')
        expect(page.get_by_test_id('syllabus-timezone-note')).to_contain_text(
            'Deadline times are shown in Europe/Berlin.',
            use_inner_text=True,
        )
    finally:
        no_js.close()

    invalid = browser.new_context(viewport=VIEWPORT, timezone_id='America/New_York')
    # Simulate a browser that reports a zone Intl cannot use.
    invalid.add_init_script("""
        (function () {
          var original = Intl.DateTimeFormat.prototype.resolvedOptions;
          Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            var options = original.call(this);
            options.timeZone = 'Mars/Olympus';
            return options;
          };
        })();
    """)
    page = invalid.new_page()
    try:
        page.goto(f'{django_server}{COURSE_URL}', wait_until='domcontentloaded')
        _expand_first_week(page)
        expect(page.get_by_test_id('syllabus-homework-deadline')).to_have_text(f'Due {berlin}')
        expect(page.get_by_test_id('syllabus-timezone-note')).to_contain_text(
            'Deadline times are shown in Europe/Berlin.',
            use_inner_text=True,
        )
    finally:
        invalid.close()
