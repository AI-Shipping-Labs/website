"""Regression coverage for public/member stacked headers (issue #1278)."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from notifications.models import Notification
from tests.fixtures import TierSetupMixin

BASE_DIR = Path(settings.BASE_DIR)
HOME = BASE_DIR / 'templates' / 'home.html'
ACTIVITIES = BASE_DIR / 'templates' / 'content' / 'activities.html'
WORKSHOPS = BASE_DIR / 'templates' / 'content' / '_workshops_catalog.html'
DASHBOARD = BASE_DIR / 'templates' / 'content' / 'dashboard.html'
NOTIFICATIONS = BASE_DIR / 'templates' / 'notifications' / 'notification_list.html'

DISCOVERY_CLASSES = (
    'mt-2 inline-flex items-center gap-2 text-sm font-medium text-accent '
    'hover:underline focus-visible:outline-none focus-visible:ring-2 '
    'focus-visible:ring-accent focus-visible:ring-offset-2 '
    'focus-visible:ring-offset-background'
)

HOME_DISCOVERY = {
    'home-activities-tier-link': (
        'Build momentum with people who ship',
        '/activities#access-by-tier',
        'Compare activities by tier',
    ),
    # Sprint story is an explainer section (3 steps + one featured card),
    # not a collection of content cards, so its CTA is a discovery link
    # like activities — not a header/action-row button like
    # events/blog/workshops.
    'home-sprints-index-link': (
        'Plan &rarr; Sprint &rarr; Ship',
        '/sprints',
        'Explore sprints',
    ),
}

HOME_COLLECTION_ACTIONS = (
    ('home-upcoming-events-link', '/events?filter=upcoming'),
    ('home-workshops-link', '/workshops'),
    # Blog shows post cards and links to the full list, so it is a
    # collection section like events/workshops — not a narrative
    # discovery link. It rendered as a bare accent link until this was
    # aligned; see the header/action-row rule in _docs/design-system.md.
    ('home-blog-link', '/blog'),
)


def _source(path):
    return path.read_text(encoding='utf-8')


def _opening_anchor(source, *, testid=None, href=None):
    if testid:
        condition = rf'(?=[^>]*data-testid="{re.escape(testid)}")'
    else:
        condition = rf'(?=[^>]*href="{re.escape(href)}")'
    match = re.search(rf'<a\b{condition}[^>]*>', source, re.DOTALL)
    if not match:
        raise AssertionError(f'Anchor not found: {testid or href}')
    return match.group(0)


class PublicStackedHeaderStaticTest(TestCase):
    """Lock the exact five-template, 15-header-plus-topic inventory."""

    def test_homepage_headers_use_discovery_links_or_collection_buttons(self):
        source = _source(HOME)

        for testid, (heading, href, label) in HOME_DISCOVERY.items():
            with self.subTest(testid=testid):
                anchor = _opening_anchor(source, testid=testid)
                self.assertIn(f'href="{href}"', anchor)
                self.assertIn(f'class="{DISCOVERY_CLASSES}"', anchor)
                self.assertNotIn('button_classes', anchor)
                self.assertLess(source.index(heading), source.index(anchor))
                tail = source[source.index(anchor):source.index(anchor) + 500]
                self.assertIn(label, tail)
                self.assertRegex(
                    tail,
                    r'data-lucide="arrow-right" class="h-4 w-4" '
                    r'aria-hidden="true"',
                )

        for testid, href in HOME_COLLECTION_ACTIONS:
            with self.subTest(testid=testid):
                anchor = _opening_anchor(source, testid=testid)
                self.assertIn(f'href="{href}"', anchor)
                self.assertIn('button_classes', anchor)
                self.assertIn("extra='shrink-0'", anchor)

        self.assertGreaterEqual(source.count('sm:items-end sm:justify-between'), 3)

    def test_activities_two_headers_share_discovery_treatment(self):
        source = _source(ACTIVITIES)
        for testid, heading, href, label in (
            (
                'activities-view-all-events',
                'Upcoming community sessions',
                '/events',
                'View all events',
            ),
            (
                'activities-view-all-workshops',
                'Recent hands-on workshops',
                '/workshops',
                'View all workshops',
            ),
        ):
            with self.subTest(testid=testid):
                anchor = _opening_anchor(source, testid=testid)
                self.assertIn(f'href="{href}"', anchor)
                self.assertIn(f'class="{DISCOVERY_CLASSES}"', anchor)
                self.assertLess(source.index(heading), source.index(anchor))
                self.assertIn(label, source[source.index(anchor):source.index(anchor) + 400])
                self.assertNotIn('button_classes', anchor)

    def test_workshop_main_and_topic_headers_are_stacked(self):
        source = _source(WORKSHOPS)
        self.assertNotIn('lg:justify-between', source)
        self.assertNotIn('sm:items-end', source)
        self.assertIn('class="mt-4 flex flex-col items-start gap-3"', source)
        self.assertIn('class="mb-3 flex flex-col gap-1"', source)
        self.assertLess(source.index('{{ catalog_intro }}'), source.index('{% if show_catalog_filters %}'))
        self.assertLess(source.index('>Topics</h3>'), source.index('{{ selected_topic_summary }}'))

        for testid in (
            'workshop-access-filters',
            'workshop-skill-filters',
            'clear-workshop-filter',
            'view-all-workshops-preview-cta',
        ):
            self.assertIn(f'data-testid="{testid}"', source)
        self.assertEqual(source.count('min-h-[44px]'), 6)
        self.assertEqual(source.count('focus-visible:ring-2'), 7)

    def test_dashboard_five_headers_are_stacked_and_h1_is_unchanged(self):
        source = _source(DASHBOARD)
        self.assertIn(
            'header class="mb-8 flex flex-col gap-4 sm:flex-row '
            'sm:items-end sm:justify-between" data-testid="dashboard-header"',
            source,
        )
        self.assertIn('class="mt-1 flex flex-wrap gap-4 text-sm"', source)
        courses = _opening_anchor(source, href='/courses')
        workshops = _opening_anchor(source, href='/workshops')
        self.assertLess(source.index(courses), source.index(workshops))

        for heading, href, label in (
            ('Upcoming events', '/events', 'View all events'),
            ('{{ active_sprint_section_title }}', '{{ active_sprint_discovery_url }}', 'Activities'),
            ('Recent content', '/blog', 'Browse blog'),
        ):
            with self.subTest(heading=heading):
                heading_at = source.index(heading)
                anchor = _opening_anchor(source[heading_at:], href=href)
                self.assertIn('min-h-[44px]', anchor)
                self.assertIn('focus-visible:ring-2', anchor)
                self.assertNotIn('shrink-0', anchor)
                self.assertIn(label, source[heading_at:heading_at + 1600])

        checklist = source[source.index('data-testid="free-activation-checklist"'):]
        checklist_header = checklist[:checklist.index('<div class="grid gap-3')]
        self.assertLess(
            checklist_header.index('Getting started'),
            checklist_header.index('Start with open courses'),
        )
        self.assertNotIn('justify-between', checklist_header)

    def test_dashboard_justify_between_survivors_are_only_exempt_roles(self):
        source = _source(DASHBOARD)
        lines = [line.strip() for line in source.splitlines() if 'justify-between' in line]
        self.assertEqual(len(lines), 12)
        exempt_signatures = (
            'dismiss-success-banner',
            'dashboard-header',
            'free-activation-complete-',
            'free-plan-teaser',
            'continue-learning-workshop',
            'continue-learning-course',
            'continue-learning-more',
            'dashboard-upcoming-event-row',
            'onboarding-prompt',
            'dashboard-plan-preparing',
            'dashboard-active-sprint-tier',
            'mt-auto flex items-center justify-between',
        )
        for line_number, line in enumerate(source.splitlines()):
            if 'justify-between' not in line:
                continue
            surrounding = '\n'.join(source.splitlines()[max(0, line_number - 8):line_number + 9])
            self.assertTrue(
                any(signature in surrounding for signature in exempt_signatures),
                msg=f'Unclassified justify-between: {line.strip()}',
            )

    def test_notifications_header_is_stacked_but_rows_are_unchanged(self):
        source = _source(NOTIFICATIONS)
        before_feed = source[:source.index('id="notification-feed"')]
        self.assertNotIn('sm:justify-between', before_feed)
        self.assertLess(before_feed.index('Notifications</h1>'), before_feed.index('id="mark-all-btn"'))
        button = before_feed[before_feed.index('<button'):before_feed.index('</button>')]
        self.assertIn('mt-4', button)
        self.assertIn('min-h-[44px]', button)
        self.assertIn('focus-visible:ring-2', button)
        self.assertIn('id="mark-all-btn"', button)
        self.assertIn('onclick="markAllRead()"', button)
        self.assertIn('sm:justify-between', source[source.index('id="notification-feed"'):])


class StackedHeaderRenderedBehaviorTest(TierSetupMixin, TestCase):
    def test_auth_routes_and_conditional_home_event_header_are_preserved(self):
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'home.html')
        self.assertNotContains(response, 'data-testid="home-upcoming-events-section"')
        self.assertNotContains(response, 'data-testid="home-upcoming-events-link"')

        user = get_user_model().objects.create_user(
            email='stacked-1278@example.com',
            password='testpass',
            tier=self.free_tier,
        )
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'content/dashboard.html')
        self.assertContains(response, 'Continue learning')

        self.client.logout()
        response = self.client.get('/notifications')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login', response.url)

    def test_notification_mark_all_visibility_is_preserved(self):
        user = get_user_model().objects.create_user(
            email='notifications-1278@example.com',
            password='testpass',
            tier=self.free_tier,
        )
        self.client.force_login(user)
        response = self.client.get('/notifications')
        self.assertContains(response, 'id="mark-all-btn" hidden')

        Notification.objects.create(user=user, title='Unread')
        response = self.client.get('/notifications')
        self.assertContains(response, 'id="mark-all-btn"')
        self.assertNotContains(response, 'id="mark-all-btn" hidden')
