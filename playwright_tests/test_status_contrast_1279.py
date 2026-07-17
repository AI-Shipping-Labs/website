"""Computed contrast and real-page screenshot evidence for issue #1279."""

import datetime
import os
import re
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]

SCREENSHOT_DIR = Path('.tmp/screenshots/issue-1279')
DESKTOP = {'width': 1280, 'height': 900}
MOBILE = {'width': 393, 'height': 851}


def _set_theme(page, theme):
    page.evaluate(
        """theme => {
            localStorage.setItem('theme', theme);
            document.documentElement.classList.toggle('dark', theme === 'dark');
        }""",
        theme,
    )
    expected = re.compile(r'(^|\s)dark(\s|$)') if theme == 'dark' else re.compile(r'^(?!.*\bdark\b).*$')
    expect(page.locator('html')).to_have_class(expected)
    expected_background = 'rgb(10, 10, 10)' if theme == 'dark' else 'rgb(255, 255, 255)'
    page.wait_for_function(
        "expected => getComputedStyle(document.body).backgroundColor === expected",
        arg=expected_background,
    )
    page.wait_for_function(
        "() => document.getAnimations().every(animation => animation.playState === 'finished')"
    )


def _contrast_result(locator):
    return locator.evaluate(
        """el => {
            const parse = value => {
                const parts = (value.match(/[0-9.]+/g) || []).map(Number);
                return {r: parts[0] || 0, g: parts[1] || 0, b: parts[2] || 0,
                        a: parts.length > 3 ? parts[3] : 1};
            };
            const over = (front, back) => {
                const a = front.a + back.a * (1 - front.a);
                if (!a) return {r: 0, g: 0, b: 0, a: 0};
                return {
                    r: (front.r * front.a + back.r * back.a * (1 - front.a)) / a,
                    g: (front.g * front.a + back.g * back.a * (1 - front.a)) / a,
                    b: (front.b * front.a + back.b * back.a * (1 - front.a)) / a,
                    a,
                };
            };
            const layers = [];
            for (let node = el; node; node = node.parentElement) {
                layers.push(parse(getComputedStyle(node).backgroundColor));
            }
            let background = {r: 255, g: 255, b: 255, a: 1};
            for (let i = layers.length - 1; i >= 0; i -= 1) {
                background = over(layers[i], background);
            }
            const foreground = over(parse(getComputedStyle(el).color), background);
            const linear = c => {
                c /= 255;
                return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
            };
            const luminance = c => 0.2126 * linear(c.r) + 0.7152 * linear(c.g) + 0.0722 * linear(c.b);
            const fgLum = luminance(foreground);
            const bgLum = luminance(background);
            return {
                ratio: (Math.max(fgLum, bgLum) + 0.05) / (Math.min(fgLum, bgLum) + 0.05),
                foreground, background,
                computedColor: getComputedStyle(el).color,
                computedBackground: getComputedStyle(el).backgroundColor,
            };
        }"""
    )


def _assert_contrast(locator, minimum, label):
    locator.wait_for(state='visible')
    result = _contrast_result(locator)
    assert result['ratio'] >= minimum, (
        f'{label} effective contrast {result["ratio"]:.3f}:1 < {minimum}:1; '
        f'computed fg={result["computedColor"]}, bg={result["computedBackground"]}, '
        f'composited fg={result["foreground"]}, bg={result["background"]}'
    )


def _assert_all_contrast(locator, minimum, label):
    assert locator.count() > 0, f'{label}: expected at least one rendered element'
    for index in range(locator.count()):
        _assert_contrast(locator.nth(index), minimum, f'{label}[{index}]')


def _assert_no_horizontal_overflow(page, label):
    dimensions = page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
    )
    assert dimensions['scroll'] <= dimensions['client'] + 1, f'{label}: {dimensions}'


def _shot(page, name):
    analytics_off = page.get_by_role('button', name='Keep analytics off')
    if analytics_off.is_visible():
        # Saving consent reloads the page.  Waiting only for the click lets the
        # screenshot race either the still-visible panel or the blank reload
        # document, so make that navigation part of the evidence contract.
        with page.expect_navigation(wait_until='domcontentloaded'):
            analytics_off.click()

    expect(page.locator('[data-testid="analytics-consent-panel"]')).to_be_hidden()
    main = page.locator('main')
    expect(main).to_be_visible()
    page.wait_for_function(
        """() => {
            const main = document.querySelector('main');
            return document.readyState === 'complete'
                && main
                && main.getBoundingClientRect().height > 0
                && main.innerText.trim().length > 0;
        }"""
    )
    page.evaluate("async () => { if (document.fonts) await document.fonts.ready; }")
    page.wait_for_function("() => Array.from(document.images).every(image => image.complete)")
    page.wait_for_function(
        "() => document.getAnimations().every(animation => animation.playState === 'finished')"
    )
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f'{name}.png', full_page=True)


def _seed_projects(prefix='contrast-1279'):
    from content.models import Project

    Project.objects.filter(slug__startswith=prefix).delete()
    projects = []
    for difficulty in ('beginner', 'intermediate', 'advanced'):
        projects.append(Project.objects.create(
            title=f'{difficulty.title()} project with a deliberately long translated-equivalent fixture title',
            slug=f'{prefix}-{difficulty}',
            description='Readable status badge fixture.',
            date=datetime.date.today(),
            difficulty=difficulty,
            tags=['contrast-1279'],
            published=True,
            required_level=0,
        ))
    connection.close()
    return projects


def _seed_course(email='course-1279@test.com', slug='contrast-course-1279'):
    from content.models import Course, Enrollment, Module, Unit

    user = create_user(email, tier_slug='main', email_verified=True)
    course, _ = Course.objects.update_or_create(
        slug=slug,
        defaults={
            'title': 'Accessible status course',
            'description': 'Course state contrast fixture.',
            'status': 'published',
            'required_level': 0,
            'peer_review_enabled': True,
            'peer_review_count': 2,
        },
    )
    module, _ = Module.objects.get_or_create(
        course=course, slug='start', defaults={'title': 'Start', 'sort_order': 1},
    )
    unit, _ = Unit.objects.get_or_create(
        module=module, slug='lesson',
        defaults={'title': 'Status lesson', 'sort_order': 1, 'body': 'Complete this lesson.'},
    )
    Enrollment.objects.get_or_create(user=user, course=course)
    connection.close()
    return user, course, unit


def _seed_event_series(user, prefix='contrast-series-1279'):
    from events.models import Event, EventRegistration, EventSeries, SeriesRegistration

    series = EventSeries.objects.create(
        name='Accessible event series with a long fixture title',
        slug=prefix,
        start_time=datetime.time(18, 0),
        timezone='UTC',
    )
    upcoming = Event.objects.create(
        title='Registered occurrence with a long translated-equivalent title',
        slug=f'{prefix}-registered',
        start_datetime=timezone.now() + datetime.timedelta(days=7),
        status='upcoming', origin='studio', timezone='UTC', required_level=0,
        event_series=series, series_position=1, tags=['contrast-1279'],
    )
    cancelled = Event.objects.create(
        title='Cancelled occurrence with a long translated-equivalent title',
        slug=f'{prefix}-cancelled',
        start_datetime=timezone.now() + datetime.timedelta(days=14),
        status='cancelled', origin='studio', timezone='UTC', required_level=0,
        event_series=series, series_position=2,
    )
    SeriesRegistration.objects.create(series=series, user=user)
    EventRegistration.objects.create(event=upcoming, user=user)
    connection.close()
    return series, upcoming, cancelled


def test_visitor_distinguishes_project_difficulties_in_both_themes(django_server, browser):
    _seed_projects()
    context = browser.new_context()
    page = context.new_page()
    for theme in ('light', 'dark'):
        for viewport_name, viewport in (('desktop', DESKTOP), ('mobile', MOBILE)):
            page.set_viewport_size(viewport)
            page.goto(f'{django_server}/projects', wait_until='domcontentloaded')
            _set_theme(page, theme)
            for difficulty in ('beginner', 'intermediate', 'advanced'):
                badge = page.locator('[data-testid="project-card"] span').filter(has_text=difficulty).first
                expect(badge).to_have_text(difficulty)
                _assert_contrast(badge, 4.5, f'{theme} {difficulty}')
            _assert_no_horizontal_overflow(page, f'projects {theme} {viewport_name}')
            _shot(page, f'projects-{theme}-{viewport_name}')
    context.close()


def test_member_understands_free_and_enrolled_course_state(django_server, browser):
    user, course, _ = _seed_course()
    context = auth_context(browser, user.email)
    page = context.new_page()
    page.set_viewport_size(DESKTOP)
    for theme in ('light', 'dark'):
        page.goto(f'{django_server}{course.get_absolute_url()}', wait_until='domcontentloaded')
        _set_theme(page, theme)
        free = page.locator('[data-component="member-badge"]', has_text='Free').first
        enrolled = page.locator('[data-component="member-badge"]', has_text='Enrolled').first
        _assert_contrast(free, 4.5, f'{theme} Free')
        _assert_contrast(enrolled, 4.5, f'{theme} Enrolled')
        expect(enrolled.locator('[data-lucide="check-circle-2"]')).to_be_attached()
        expect(page.locator('[data-testid="continue-button"]')).to_be_visible()
        _shot(page, f'course-state-{theme}-desktop')
    context.close()


def test_peer_review_participant_tracks_every_status(django_server, browser):
    from content.models import CourseCertificate, PeerReview, ProjectSubmission

    user, course, _ = _seed_course('peer-1279@test.com', 'peer-course-1279')
    submission = ProjectSubmission.objects.create(
        user=user, course=course, project_url='https://example.com/member-project',
    )
    for index, complete in enumerate((False, True), 1):
        reviewer = create_user(f'peer-target-{index}-1279@test.com')
        other = ProjectSubmission.objects.create(
            user=reviewer, course=course,
            project_url=f'https://example.com/target-{index}', status='in_review',
        )
        PeerReview.objects.create(
            submission=other, reviewer=user, is_complete=complete,
            completed_at=timezone.now() if complete else None,
        )
    CourseCertificate.objects.create(user=user, course=course, submission=submission)
    connection.close()

    context = auth_context(browser, user.email)
    page = context.new_page()
    page.set_viewport_size(DESKTOP)
    for theme in ('light', 'dark'):
        for status, label in (
            ('submitted', 'Submitted'), ('in_review', 'In Review'),
            ('review_complete', 'Review Complete'), ('certified', 'Certified'),
        ):
            ProjectSubmission.objects.filter(pk=submission.pk).update(status=status)
            connection.close()
            paths = ('reviews',) if status == 'submitted' else ('reviews', 'submit')
            for path in paths:
                page.goto(f'{django_server}/courses/{course.slug}/{path}', wait_until='domcontentloaded')
                _set_theme(page, theme)
                badge = page.locator('[data-testid="peer-submission-status"]')
                expect(badge).to_have_text(label)
                _assert_contrast(badge, 4.5, f'{theme} peer {status} {path}')
        page.goto(f'{django_server}/courses/{course.slug}/reviews', wait_until='domcontentloaded')
        _set_theme(page, theme)
        review_badges = page.locator('[data-testid="peer-review-status"]')
        _assert_all_contrast(review_badges, 4.5, f'{theme} review assignment')
        assert set(review_badges.all_inner_texts()) == {'Pending', 'Completed'}
        _shot(page, f'peer-dashboard-{theme}-desktop')
        page.goto(f'{django_server}/courses/{course.slug}/submit', wait_until='domcontentloaded')
        _set_theme(page, theme)
        _shot(page, f'peer-submit-{theme}-desktop')
    context.close()


def test_series_registrant_reads_registered_and_cancelled_states(django_server, browser):
    user = create_user('series-1279@test.com', tier_slug='main', is_staff=True)
    series, _, _ = _seed_event_series(user)
    context = auth_context(browser, user.email)
    page = context.new_page()
    for theme in ('light', 'dark'):
        for viewport_name, viewport in (('desktop', DESKTOP), ('mobile', MOBILE)):
            page.set_viewport_size(viewport)
            page.goto(f'{django_server}{series.get_absolute_url()}', wait_until='domcontentloaded')
            _set_theme(page, theme)
            for testid in (
                'series-registered-state', 'series-event-state-registered',
                'series-event-state-cancelled',
            ):
                _assert_contrast(page.locator(f'[data-testid="{testid}"]'), 4.5, f'{theme} {testid}')
            expect(page.locator('[data-testid="series-event-state-registered"] [data-lucide="check"]')).to_be_attached()
            _assert_no_horizontal_overflow(page, f'event series {theme} {viewport_name}')
            _shot(page, f'event-series-{theme}-{viewport_name}')
    context.close()


def test_visitor_browses_five_tag_result_types(django_server, browser):
    from content.models import Article, Course, Download, Project
    from events.models import Event

    tag = 'contrast-1279-types'
    today = datetime.date.today()
    Article.objects.create(title='Contrast article', slug='contrast-article-1279', date=today, tags=[tag], published=True)
    Project.objects.create(title='Contrast project', slug='contrast-project-tag-1279', date=today, tags=[tag], published=True)
    Course.objects.create(title='Contrast course', slug='contrast-course-tag-1279', status='published', tags=[tag])
    Download.objects.create(title='Contrast download', slug='contrast-download-1279', tags=[tag], published=True)
    Event.objects.create(
        title='Contrast event', slug='contrast-event-tag-1279', tags=[tag],
        start_datetime=timezone.now() + datetime.timedelta(days=2), status='upcoming',
    )
    connection.close()

    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    for theme in ('light', 'dark'):
        page.goto(f'{django_server}/tags/{tag}', wait_until='domcontentloaded')
        _set_theme(page, theme)
        for label in ('Article', 'Project', 'Course', 'Download', 'Event'):
            badge = page.locator('article span').filter(has_text=label).first
            expect(badge).to_have_text(label)
            _assert_contrast(badge, 4.5, f'{theme} tag type {label}')
            expect(badge.locator('xpath=ancestor::a[1]')).to_have_count(1)
    context.close()


def test_member_reads_shared_status_badges_across_four_journeys(django_server, browser):
    from content.models import Enrollment
    from events.models import Event, EventRegistration
    from plans.models import Sprint
    from voting.models import Poll, PollOption

    user, course, _ = _seed_course('shared-1279@test.com', 'shared-course-1279')
    Enrollment.objects.get_or_create(user=user, course=course)
    event = Event.objects.create(
        title='Shared badge event', slug='shared-event-1279',
        start_datetime=timezone.now() + datetime.timedelta(days=3), status='upcoming',
    )
    EventRegistration.objects.create(event=event, user=user)
    Sprint.objects.create(
        name='Shared badge sprint', slug='shared-sprint-1279',
        start_date=datetime.date.today(), duration_weeks=2, status='active', min_tier_level=20,
    )
    poll = Poll.objects.create(title='Shared badge poll', status='open', allow_proposals=True)
    PollOption.objects.create(poll=poll, title='Accessible option')
    connection.close()

    context = auth_context(browser, user.email)
    page = context.new_page()
    journeys = (
        ('/courses', '[data-testid="enrolled-badge"]'),
        ('/events', '[data-testid="upcoming-event-card"] [data-component="member-badge"]'),
        ('/sprints', '[data-testid="sprints-sprint-status"]'),
        ('/vote', '[data-component="member-badge"]'),
    )
    for theme in ('light', 'dark'):
        for path, selector in journeys:
            page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
            _set_theme(page, theme)
            _assert_all_contrast(page.locator(selector), 4.5, f'{theme} shared {path}')
    context.close()


def test_learner_recognizes_plan_and_reader_completion(django_server, browser):
    from content.models import Enrollment
    from plans.models import Checkpoint, Plan, Sprint, SprintEnrollment, Week

    owner, course, unit = _seed_course('reader-owner-1279@test.com', 'reader-course-1279')
    teammate = create_user('reader-teammate-1279@test.com', tier_slug='main')
    Enrollment.objects.get_or_create(user=owner, course=course)
    sprint = Sprint.objects.create(
        name='Completion sprint', slug='completion-sprint-1279',
        start_date=datetime.date.today(), duration_weeks=2, status='active', min_tier_level=20,
    )
    SprintEnrollment.objects.get_or_create(sprint=sprint, user=owner)
    SprintEnrollment.objects.get_or_create(sprint=sprint, user=teammate)
    plan = Plan.objects.create(member=owner, sprint=sprint, visibility='cohort', goal='Ship visibly')
    week = Week.objects.create(plan=plan, week_number=1)
    Checkpoint.objects.create(week=week, description='Done checkpoint with visible context', done_at=timezone.now())
    connection.close()

    teammate_context = auth_context(browser, teammate.email)
    plan_page = teammate_context.new_page()
    plan_page.set_viewport_size(MOBILE)
    for theme in ('light', 'dark'):
        plan_page.goto(
            f'{django_server}/sprints/{sprint.slug}/plans/{plan.pk}',
            wait_until='domcontentloaded',
        )
        _set_theme(plan_page, theme)
        marker = plan_page.locator('[aria-label="Done"]').first
        _assert_contrast(marker, 3.0, f'{theme} Done marker')
        expect(plan_page.locator('[data-testid="checkpoint-text"]')).to_contain_text('Done checkpoint')
        _shot(plan_page, f'plan-completion-{theme}-mobile')
    teammate_context.close()

    owner_context = auth_context(browser, owner.email)
    reader = owner_context.new_page()
    reader.set_viewport_size(MOBILE)
    for theme in ('light', 'dark'):
        reader.goto(f'{django_server}{unit.get_absolute_url()}', wait_until='domcontentloaded')
        _set_theme(reader, theme)
        button = reader.locator('[data-completion-toggle]:visible').first
        expect(button).to_have_text('Mark as completed')
        button.click()
        expect(button).to_have_text('Completed')
        _assert_contrast(button, 4.5, f'{theme} runtime completed button')
        button.focus()
        reader.keyboard.press('Tab')
        reader.keyboard.press('Shift+Tab')
        assert button.evaluate("el => el.matches(':focus-visible')") is True
        ring = button.evaluate("el => getComputedStyle(el).boxShadow")
        assert ring != 'none', f'{theme} completed button lost visible focus ring'
        _shot(reader, f'reader-completion-{theme}-mobile')
        # Restore the incomplete precondition for the next theme.
        button.click()
        expect(button).to_have_text('Mark as completed')
    owner_context.close()


def test_theme_switch_does_not_render_unclassified_light_only_badges(django_server, browser):
    _seed_projects('leak-scan-1279')
    user, course, _ = _seed_course('leak-1279@test.com', 'leak-course-1279')
    series, _, _ = _seed_event_series(user, 'leak-series-1279')
    context = auth_context(browser, user.email)
    page = context.new_page()
    for path in ('/projects', course.get_absolute_url(), series.get_absolute_url()):
        page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
        _set_theme(page, 'light')
        violations = page.locator('[class]').evaluate_all(
            r"""elements => elements.flatMap(el => {
                const classes = Array.from(el.classList);
                const hasTint = classes.some(c => /^bg-(green|yellow|red|blue|purple|orange)-500\/(15|20)$/.test(c));
                const unsafe = classes.some(c => /^text-(green|yellow|red|blue|purple|orange)-400$/.test(c));
                return hasTint && unsafe ? [{tag: el.tagName, text: el.textContent.trim(), classes}] : [];
            })"""
        )
        assert violations == [], f'{path} rendered unclassified light-only badge recipes: {violations}'
    context.close()
