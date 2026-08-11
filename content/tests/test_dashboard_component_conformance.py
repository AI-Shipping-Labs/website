"""Dashboard design-system conformance regressions (#1310, #1311, #1312)."""

import datetime
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bookclub.models import BOOK_STATUS_CURRENT, Book
from content.access import LEVEL_MAIN, LEVEL_OPEN
from content.models import Course, Enrollment, Workshop
from events.models import Event
from plans.models import Sprint
from tests.fixtures import TierSetupMixin

User = get_user_model()

CANONICAL_FOCUS_CLASSES = {
    'focus-visible:outline-none',
    'focus-visible:ring-2',
    'focus-visible:ring-accent',
    'focus-visible:ring-offset-2',
    'focus-visible:ring-offset-background',
}

def _active_sprint(name, slug, min_tier_level):
    return Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=datetime.date.today() - datetime.timedelta(days=7),
        duration_weeks=4,
        status='active',
        min_tier_level=min_tier_level,
    )


class DashboardGuidanceGridLayoutTest(TierSetupMixin, TestCase):
    """Sprint discovery uses the single-column home feed."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='layout@test.com', password='pw', tier=self.free_tier,
        )
        self.client.login(email='layout@test.com', password='pw')

    def test_sprint_opportunity_is_a_home_feed_row(self):
        _active_sprint('Free Open Sprint', 'free-open-sprint', LEVEL_OPEN)

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="dashboard-home-feed"')
        self.assertContains(response, 'data-feed-kind="sprint"')
        self.assertContains(response, 'Free Open Sprint')
        self.assertNotContains(response, 'dashboard-secondary-guidance')

    def test_feed_uses_divider_rows_instead_of_a_card_grid(self):
        _active_sprint('Free Open Sprint', 'free-open-sprint', LEVEL_OPEN)

        response = self.client.get('/')

        self.assertContains(
            response,
            'class="border-t border-border/70" '
            'data-testid="dashboard-feed-list"',
        )
        self.assertNotContains(
            response,
            'grid-cols-[repeat(auto-fit,minmax(min(100%,17rem),1fr))]',
        )


class DashboardBadgeOwnerTest(TierSetupMixin, TestCase):
    """#1312 items 1 and 2: badges come from the member_badges owner."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='badges@test.com', password='pw', tier=self.free_tier,
        )
        self.client.login(email='badges@test.com', password='pw')

    def _badge(self, response, testid):
        match = re.search(
            r'<span[^>]*data-testid="%s"[^>]*>(.*?)</span>' % testid,
            response.content.decode(),
            re.S,
        )
        self.assertIsNotNone(match, f'{testid} badge missing')
        return match.group(0)

    def test_header_tier_pill_renders_through_the_badge_owner(self):
        response = self.client.get('/')

        pill = self._badge(response, 'dashboard-tier-pill')
        self.assertIn('data-component="member-badge"', pill)
        self.assertIn('border border-accent/40 bg-accent/10 text-accent', pill)
        self.assertIn('Free', pill)

    def test_free_open_sprint_is_an_accessible_feed_entry(self):
        _active_sprint('Free Open Sprint', 'free-open-sprint', LEVEL_OPEN)

        response = self.client.get('/')

        self.assertContains(response, 'data-feed-kind="sprint"')
        self.assertContains(response, 'data-feed-locked="false"')
        self.assertContains(response, 'Active sprint')
        self.assertContains(response, 'Free Open Sprint')

    def test_paid_member_sprint_is_an_accessible_feed_entry(self):
        self.user.tier = self.main_tier
        self.user.save(update_fields=['tier'])
        _active_sprint('Main Sprint', 'main-sprint', LEVEL_MAIN)

        response = self.client.get('/')

        self.assertContains(response, 'data-feed-kind="sprint"')
        self.assertContains(response, 'data-feed-locked="false"')
        self.assertContains(response, 'Active sprint')
        self.assertContains(response, 'Main Sprint')


class DashboardEmptyStateOwnerTest(TierSetupMixin, TestCase):
    """Empty feed lanes stay hidden while destinations remain available."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='empty@test.com', password='pw', tier=self.free_tier,
        )
        self.client.login(email='empty@test.com', password='pw')

    def test_empty_poll_lane_is_hidden_and_destination_remains(self):
        response = self.client.get('/')

        self.assertNotContains(response, 'No active polls right now')
        self.assertContains(response, 'href="/vote"')
        self.assertContains(response, '>Polls <')

    def test_empty_sprint_lane_is_hidden_and_destination_remains(self):
        response = self.client.get('/')

        self.assertNotContains(response, 'No active sprint openings for your tier')
        self.assertContains(response, 'href="/sprints"')
        self.assertContains(response, '>Sprints <')


class DashboardInteractiveLinkContractTest(TierSetupMixin, TestCase):
    """Dashboard-specific links keep the canonical keyboard/tap contract."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='dashboard-links@test.com', password='pw', tier=self.free_tier,
        )
        self.client.login(email=self.user.email, password='pw')

    def assert_focus_classes(self, class_value):
        self.assertTrue(
            CANONICAL_FOCUS_CLASSES.issubset(set(class_value.split())),
            class_value,
        )

    def test_inline_learning_and_destination_links_use_canonical_focus_classes(self):
        source = Path(
            'templates/content/_dashboard_commitment_zones.html',
        ).read_text(encoding='utf-8')

        more = re.search(
            r'data-testid="continue-learning-more".*?'
            r'<a href="/courses" class="([^"]+)"',
            source,
        )
        self.assertIsNotNone(more)
        self.assert_focus_classes(more.group(1))
        self.assertNotIn('min-h-[44px]', more.group(1))

        destinations = re.findall(
            r'<a [^>]*class="([^"]+)" '
            r'data-testid="dashboard-feed-destination"',
            source,
        )
        self.assertEqual(len(destinations), 5)
        for classes in destinations:
            self.assertIn('min-h-[44px]', classes)
            self.assert_focus_classes(classes)

    def test_unlock_links_are_scoped_by_href_and_keep_tap_focus_contract(self):
        sprint = _active_sprint('Paid Sprint', 'paid-sprint', LEVEL_MAIN)
        book = Book.objects.create(
            title='Paid Book', slug='paid-book', author='Test Author',
            status=BOOK_STATUS_CURRENT, required_level=LEVEL_MAIN,
            start_date=timezone.localdate(),
        )
        Workshop.objects.create(
            title='Paid Workshop', slug='paid-workshop', status='published',
            date=timezone.localdate(), pages_required_level=LEVEL_MAIN,
        )
        Event.objects.create(
            title='Paid Event', slug='paid-event', status='upcoming',
            published=True, required_level=LEVEL_MAIN,
            start_datetime=timezone.now() + datetime.timedelta(days=3),
        )

        response = self.client.get('/')
        content = response.content.decode()
        teaser_start = content.index('data-testid="free-plan-teaser"')
        teaser_end = content.index('</ul>', teaser_start)
        teaser = content[teaser_start:teaser_end]
        anchors = {
            href: classes
            for href, classes in re.findall(
                r'<a href="([^"]+)" class="([^"]+)"', teaser,
            )
        }

        self.assertEqual(
            set(anchors),
            {
                sprint.get_absolute_url(),
                book.get_absolute_url(),
                '/workshops',
                '/events',
            },
        )
        for classes in anchors.values():
            self.assertIn('min-h-[44px]', classes)
            self.assert_focus_classes(classes)


class DashboardLightThemeContrastTest(TierSetupMixin, TestCase):
    """#1311: green/emerald copy needs an explicit light-theme value."""

    def _light_only_pale_tokens(self, markup, palette):
        """Return pale foreground classes that are not dark-theme scoped."""
        tokens = re.findall(
            r'(?:^|[\s"])((?:[a-z-]+:)*text-%s-(?:300|400))' % palette,
            markup,
        )
        return [token for token in tokens if not token.startswith('dark:')]

    def setUp(self):
        self.user = User.objects.create_user(
            email='contrast@test.com', password='pw', tier=self.free_tier,
        )
        self.client.login(email='contrast@test.com', password='pw')

    def test_checkout_success_banner_has_a_light_theme_foreground(self):
        response = self.client.get('/')
        content = response.content.decode()

        banner = re.search(
            r'<div id="checkout-success-banner".*?\n    </div>',
            content, re.S,
        ).group(0)
        self.assertIn('text-green-800 dark:text-green-300', banner)
        self.assertIn('text-green-700 dark:text-green-400', banner)
        # Nothing inside the banner may set a light green foreground that
        # only works on the dark background.
        self.assertEqual(self._light_only_pale_tokens(banner, 'green'), [])

    def test_completed_checklist_item_has_a_light_theme_foreground(self):
        course = Course.objects.create(
            title='AI Hero', slug='aihero', description='Ship AI products.',
        )
        Enrollment.objects.create(user=self.user, course=course)

        response = self.client.get('/')
        content = response.content.decode()

        self.assertContains(
            response, 'data-testid="free-activation-item-ai-hero"',
        )
        checklist = re.search(
            r'data-testid="free-activation-checklist".*?'
            r'data-testid="free-plan-teaser"',
            content, re.S,
        ).group(0)
        self.assertEqual(
            checklist.count('text-emerald-800 dark:text-emerald-300'), 1,
        )
        self.assertEqual(
            self._light_only_pale_tokens(checklist, 'emerald'), [],
        )


class DashboardTypographyTest(TierSetupMixin, TestCase):
    """#1312 items 4 and 5: eyebrow and section-heading scale."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='type@test.com', password='pw', tier=self.free_tier,
        )
        self.client.login(email='type@test.com', password='pw')

    def test_checklist_eyebrow_and_heading_match_the_dashboard_contract(self):
        response = self.client.get('/')

        self.assertContains(
            response,
            '<p class="text-xs font-medium uppercase tracking-widest '
            'text-accent">Getting started</p>',
            html=False,
        )
        self.assertContains(
            response,
            '<h3 class="text-lg font-semibold tracking-tight '
            'text-foreground">Set up your account</h3>',
            html=False,
        )
