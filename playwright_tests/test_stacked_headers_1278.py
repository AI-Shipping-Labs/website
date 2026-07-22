"""Core user journeys for public/member stacked headers (issue #1278)."""

import datetime
import os
import re
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user, ensure_site_config_tiers, ensure_tiers

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
]

SCREENSHOT_DIR = Path('.tmp/screenshots/1278')
DESKTOP = {'width': 1280, 'height': 900}
PIXEL_7 = {'width': 393, 'height': 851}


def _seed_representative_state():
    from allauth.socialaccount.models import SocialApp
    from django.db import connection

    from content.models import Article, CuratedLink, Project, Workshop
    from events.models import Event, EventRegistration
    from notifications.models import Notification
    from plans.models import Sprint

    ensure_tiers()
    ensure_site_config_tiers()
    SocialApp.objects.all().delete()

    event = Event.objects.create(
        title='Stacked header building session',
        slug='stacked-header-building-session-1278',
        description='A representative upcoming community session.',
        start_datetime=timezone.now() + datetime.timedelta(days=3),
        end_datetime=timezone.now() + datetime.timedelta(days=3, hours=1),
        status='upcoming',
        published=True,
        required_level=0,
    )
    Workshop.objects.create(
        title='Production agent systems',
        slug='production-agent-systems-1278',
        description='Build and evaluate a production-ready agent system.',
        date=timezone.localdate() - datetime.timedelta(days=2),
        status='published',
        tags=['agents', 'evaluation'],
        skill_level='beginner',
        core_tools=['Python', 'OpenAI'],
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
    )
    Workshop.objects.create(
        title='Advanced retrieval systems',
        slug='advanced-retrieval-systems-1278',
        description='Build robust retrieval pipelines.',
        date=timezone.localdate() - datetime.timedelta(days=5),
        status='published',
        tags=['rag'],
        skill_level='advanced',
        core_tools=['Python'],
        landing_required_level=10,
        pages_required_level=10,
        recording_required_level=20,
    )
    Article.objects.create(
        title='Shipping reliable agents',
        slug='shipping-reliable-agents-1278',
        description='A practical field guide.',
        date=timezone.localdate(),
        status='published',
        published=True,
        required_level=0,
    )
    Project.objects.create(
        title='Agent observability dashboard',
        slug='agent-observability-dashboard-1278',
        description='A representative portfolio project.',
        date=timezone.localdate(),
        status='published',
        published=True,
        required_level=0,
    )
    CuratedLink.objects.create(
        item_id='stacked-header-resource-1278',
        title='Agent engineering reference',
        description='A representative curated resource.',
        url='https://example.com/agent-engineering',
        category='tools',
        published=True,
    )
    Sprint.objects.create(
        name='Stacked header shipping sprint',
        slug='stacked-header-shipping-sprint-1278',
        start_date=timezone.localdate(),
        duration_weeks=4,
        status='active',
        min_tier_level=0,
    )
    free_user = create_user(
        'stacked-free-1278@example.com',
        tier_slug='free',
        first_name='Free',
    )
    main_user = create_user(
        'stacked-main-1278@example.com',
        tier_slug='main',
        first_name='Main',
    )
    EventRegistration.objects.create(user=free_user, event=event)
    EventRegistration.objects.create(user=main_user, event=event)
    for index in range(8):
        Notification.objects.create(
            user=free_user,
            title=f'Stacked notification {index + 1}',
            body='Representative unread notification.',
            url='/blog/shipping-reliable-agents-1278',
            read=False,
        )
    connection.close()


def _context(browser, viewport, email=None):
    if email:
        context = auth_context(browser, email)
        page = context.new_page()
        page.set_viewport_size(viewport)
        return context, page
    context = browser.new_context(viewport=viewport)
    return context, context.new_page()


def _set_theme(context, theme):
    context.add_init_script(
        f"window.localStorage.setItem('theme', '{theme}');"
    )


def _assert_no_overflow(page):
    dimensions = page.evaluate(
        """() => ({
          scroll: document.documentElement.scrollWidth,
          client: document.documentElement.clientWidth
        })"""
    )
    assert dimensions['scroll'] <= dimensions['client'] + 2, dimensions


def _assert_control_below(
    page,
    section_selector,
    heading_selector,
    control_selector,
    *,
    require_focus=True,
):
    section = page.locator(section_selector)
    heading = section.locator(heading_selector).first
    control = section.locator(control_selector).first
    expect(heading).to_be_visible()
    expect(control).to_be_visible()
    heading_box = heading.bounding_box()
    control_box = control.bounding_box()
    assert heading_box is not None
    assert control_box is not None
    assert control_box['y'] >= heading_box['y'] + heading_box['height'] - 1
    if require_focus:
        focus_target = control
        if control.evaluate("el => !['A', 'BUTTON'].includes(el.tagName)"):
            focus_target = control.locator('a, button').first
        expect(focus_target).to_be_visible()
        assert focus_target.evaluate(
            "el => getComputedStyle(el).display !== 'none' && el.tabIndex >= 0"
        )


def _assert_route_headers(page, route):
    if route == '/':
        if page.get_by_test_id('dashboard-heading').count():
            _assert_control_below(
                page,
                '[data-testid="dashboard-continue-learning-section"]',
                'h2',
                'a[href="/courses"]',
            )
            _assert_control_below(
                page,
                '[data-testid="dashboard-upcoming-events-section"]',
                'h2',
                'a[href="/events"]',
            )
            _assert_control_below(
                page,
                '[data-testid="dashboard-discovery-sections"] section:first-child',
                'h2',
                'a[href="/blog"]',
            )
            checklist = page.get_by_test_id('free-activation-checklist')
            expect(checklist).to_be_visible()
            assert checklist.locator('p').nth(1).bounding_box()['y'] > checklist.locator('h2').bounding_box()['y']
            sprints = page.locator('section:has(h2:has-text("Sprints and cohorts"))')
            _assert_control_below(
                page,
                'section:has(h2:has-text("Sprints and cohorts"))',
                'h2',
                'a[href="/activities"]',
            )
            expect(sprints).to_be_visible()
        else:
            for section, heading, control in (
                ('#activities', 'h2', '[data-testid="home-activities-tier-link"]'),
                ('#sprint-story', 'h2', '[data-testid="home-sprints-index-link"]'),
                ('#upcoming-events', 'h2', '[data-testid="home-upcoming-events-link"]'),
                ('#blog', 'h2', 'a[href="/blog"]'),
                ('#projects', 'h2', 'a[href="/projects"]'),
                ('#collection', 'h2', 'a[href="/resources"]'),
            ):
                _assert_control_below(page, section, heading, control)
    elif route == '/activities':
        _assert_control_below(
            page,
            '[data-testid="activities-live-events-section"]',
            'h2',
            '[data-testid="activities-view-all-events"]',
        )
        _assert_control_below(
            page,
            '[data-testid="activities-workshops-section"]',
            'h2',
            '[data-testid="activities-view-all-workshops"]',
        )
    elif route == '/workshops/catalog':
        _assert_control_below(
            page,
            '[data-testid="workshop-catalog"]',
            'h2',
            '[data-testid="workshop-access-filters"]',
        )
        _assert_control_below(
            page,
            '[data-testid="workshop-facet-topic"]',
            'h3',
            '[data-testid="workshop-topic-summary"]',
            require_focus=False,
        )
    elif route == '/notifications':
        _assert_control_below(page, 'main > div', 'h1', '#mark-all-btn')


@pytest.mark.parametrize(
    ('route', 'name', 'email'),
    [
        ('/', 'home', None),
        ('/activities', 'activities', None),
        ('/workshops/catalog?tag=agents', 'workshops-catalog', None),
        ('/', 'free-dashboard', 'stacked-free-1278@example.com'),
        ('/notifications', 'notifications-unread', 'stacked-free-1278@example.com'),
    ],
)
@pytest.mark.parametrize(('viewport_name', 'viewport'), [('desktop', DESKTOP), ('pixel7', PIXEL_7)])
@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.manual_visual
def test_stacked_header_visual_matrix(
    django_server,
    browser,
    django_db_blocker,
    route,
    name,
    email,
    viewport_name,
    viewport,
    theme,
):
    with django_db_blocker.unblock():
        _seed_representative_state()
    context, page = _context(browser, viewport, email)
    _set_theme(context, theme)
    try:
        page.goto(f'{django_server}{route}', wait_until='domcontentloaded')
        expect(page.locator('main')).to_be_visible()
        analytics_off = page.get_by_role('button', name='Keep analytics off')
        if analytics_off.is_visible():
            analytics_off.click()
            expect(analytics_off).to_be_hidden()
        assert page.locator('text=Server Error').count() == 0
        assert ('dark' in (page.locator('html').get_attribute('class') or '').split()) is (theme == 'dark')
        canonical_route = route.split('?', maxsplit=1)[0]
        _assert_route_headers(page, canonical_route)
        _assert_no_overflow(page)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / f'{name}-{viewport_name}-{theme}.png'),
            full_page=True,
        )
    finally:
        context.close()


@pytest.mark.core
def test_homepage_discovery_paths_and_quiet_event_state(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_representative_state()

    page.goto(f'{django_server}/', wait_until='domcontentloaded')
    expected = (
        ('home-activities-tier-link', '/activities#access-by-tier'),
        ('home-sprints-index-link', '/sprints'),
        ('home-upcoming-events-link', '/events?filter=upcoming'),
        ('home-workshops-link', '/workshops'),
    )
    for testid, href in expected:
        link = page.get_by_test_id(testid)
        expect(link).to_have_attribute('href', href)
        assert link.locator('[data-lucide="arrow-right"][aria-hidden="true"]').count() == 1
    expect(
        page.locator('#blog').get_by_role('link', name='View all posts')
    ).to_have_attribute('href', '/blog')
    expect(page.locator('#workshops')).to_be_visible()
    expect(page.locator('#projects, #collection')).to_have_count(0)
    expect(page.get_by_text('Pet & Portfolio Project Ideas')).to_have_count(0)
    expect(page.get_by_text('Tools, Models & Courses')).to_have_count(0)

    with django_db_blocker.unblock():
        from django.db import connection

        from events.models import Event

        Event.objects.all().delete()
        connection.close()
    page.reload(wait_until='domcontentloaded')
    expect(page.get_by_test_id('home-upcoming-events-section')).to_have_count(0)
    expect(page.get_by_test_id('home-upcoming-events-link')).to_have_count(0)
    expect(page.get_by_test_id('home-activities-tier-link')).to_be_visible()
    expect(page.get_by_test_id('home-sprints-index-link')).to_be_visible()
    expect(page.get_by_test_id('home-workshops-link')).to_be_visible()


@pytest.mark.core
def test_activities_discovery_links_survive_populated_and_empty_previews(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_representative_state()
    page.goto(f'{django_server}/activities', wait_until='domcontentloaded')
    events = page.get_by_test_id('activities-view-all-events')
    workshops = page.get_by_test_id('activities-view-all-workshops')
    expect(events).to_have_attribute('href', '/events')
    expect(workshops).to_have_attribute('href', '/workshops')
    assert events.locator('[data-lucide="arrow-right"][aria-hidden="true"]').count() == 1
    assert workshops.locator('[data-lucide="arrow-right"][aria-hidden="true"]').count() == 1

    with django_db_blocker.unblock():
        from django.db import connection

        from content.models import Workshop
        from events.models import Event

        Workshop.objects.all().delete()
        Event.objects.all().delete()
        connection.close()
    page.reload(wait_until='domcontentloaded')
    expect(events).to_have_attribute('href', '/events')
    expect(workshops).to_have_attribute('href', '/workshops')
    expect(page.get_by_test_id('activities-live-events-empty')).to_be_visible()
    expect(page.get_by_test_id('activities-workshops-empty')).to_be_visible()


@pytest.mark.core
def test_both_workshop_branches_and_filters_keep_behavior(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_representative_state()

    page.goto(f'{django_server}/workshops', wait_until='domcontentloaded')
    _assert_no_overflow(page)
    expect(page.get_by_test_id('workshops-preview')).to_be_visible()
    expect(page.get_by_test_id('workshop-access-filters')).to_have_count(0)
    preview = page.get_by_test_id('view-all-workshops-preview-cta')
    expect(preview).to_have_attribute('href', '/workshops/catalog')
    preview.click()
    page.wait_for_url('**/workshops/catalog')
    expect(page.get_by_test_id('workshop-access-filters')).to_be_visible()

    page.get_by_test_id('workshop-access-filter-paid').click()
    page.wait_for_url('**/workshops/catalog?access=paid')
    expect(page.get_by_test_id('workshop-access-filter-paid')).to_have_attribute('aria-current', 'page')
    page.get_by_test_id('workshop-skill-filter-advanced').click()
    page.wait_for_url('**access=paid**skill_level=advanced**')
    expect(page.get_by_test_id('workshop-skill-filter-advanced')).to_have_attribute('aria-current', 'page')
    page.get_by_test_id('workshop-facet-topic').locator('summary').click()
    page.get_by_test_id('workshop-topic-option-rag').click()
    page.wait_for_url('**tag=rag**')
    expect(page.get_by_test_id('workshop-topic-option-rag')).to_have_attribute('aria-current', 'page')
    expect(page.get_by_test_id('workshop-topic-summary')).to_contain_text('rag')
    clear = page.get_by_test_id('clear-workshop-filter')
    expect(clear).to_have_attribute('href', '/workshops/catalog')
    clear.click()
    page.wait_for_url('**/workshops/catalog')


@pytest.mark.parametrize(
    ('email', 'tier'),
    [
        ('stacked-free-1278@example.com', 'Free'),
        ('stacked-main-1278@example.com', 'Main'),
    ],
)
@pytest.mark.core
def test_member_dashboard_header_links_keep_destinations_and_order(
    django_server, browser, django_db_blocker, email, tier
):
    with django_db_blocker.unblock():
        _seed_representative_state()
    context = auth_context(browser, email)
    page = context.new_page()
    try:
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        expect(page.get_by_test_id('dashboard-header')).to_contain_text(tier)
        learning = page.get_by_test_id('dashboard-continue-learning-section')
        links = learning.locator(':scope > div:first-child a')
        assert links.evaluate_all("nodes => nodes.map(node => node.getAttribute('href'))") == ['/courses', '/workshops']
        for label, href in (
            ('View all events', '/events'),
            ('Activities', '/activities'),
            ('Browse blog', '/blog'),
        ):
            link = page.get_by_role('link', name=label, exact=True).first
            expect(link).to_have_attribute('href', href)
            assert link.evaluate('el => el.getBoundingClientRect().height >= 44')
            assert 'focus-visible:ring-2' in (link.get_attribute('class') or '')
    finally:
        context.close()


@pytest.mark.core
def test_notification_stacked_read_all_and_anonymous_guard(
    django_server, browser, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_representative_state()
    page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
    page.wait_for_url('**/accounts/login/**')
    expect(page.locator('#mark-all-btn')).to_have_count(0)

    context = auth_context(browser, 'stacked-free-1278@example.com')
    member_page = context.new_page()
    try:
        member_page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
        button = member_page.locator('#mark-all-btn')
        expect(button).to_be_visible()
        assert button.evaluate('el => el.getBoundingClientRect().height >= 44')
        with member_page.expect_navigation(wait_until='domcontentloaded'):
            button.click()
        expect(member_page.get_by_text('No pending notifications')).to_be_visible()
        expect(member_page.locator('#mark-all-btn')).to_be_hidden()
        member_page.get_by_role('link', name='All').click()
        member_page.wait_for_url('**/notifications?filter=all')
        expect(member_page.locator('[data-notification-row]')).to_have_count(8)
        expect(member_page.locator('[data-mark-read-button]')).to_have_count(0)
        expect(member_page.locator('#notification-badge')).to_have_class(
            re.compile(r'.*hidden.*')
        )
    finally:
        context.close()
