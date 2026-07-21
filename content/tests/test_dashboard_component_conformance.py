"""Dashboard design-system conformance regressions (#1310, #1311, #1312).

Covers the member dashboard surfaces that drifted from their owning
components: the guidance grid layout for the Sprints and cohorts section,
the light-theme contrast of the checkout banner and the Free activation
checklist, and the badge/empty-state component owners.
"""

import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.access import LEVEL_MAIN, LEVEL_OPEN
from content.models import Course, Enrollment
from plans.models import Sprint
from tests.fixtures import TierSetupMixin

User = get_user_model()

SPRINT_SECTION_OPEN_TAG = '<section class="lg:odd:last:col-span-2">'
SPRINT_CARD_TRACK = 'grid-cols-[repeat(auto-fit,minmax(min(100%,17rem),1fr))]'


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
    """#1310: the tall sprint section must not leave a blank grid column."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='layout@test.com', password='pw', tier=self.free_tier,
        )
        self.client.login(email='layout@test.com', password='pw')

    def test_sprint_section_spans_both_columns_when_alone_in_its_row(self):
        _active_sprint('Free Open Sprint', 'free-open-sprint', LEVEL_OPEN)

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        # The guidance grid is still two columns at lg...
        self.assertContains(
            response,
            'class="grid gap-6 lg:grid-cols-2" '
            'data-testid="dashboard-secondary-guidance"',
        )
        # ...and the sprint section opts into spanning both of them when it
        # is an odd last child, which is the case that used to leave a blank
        # column half a page tall.
        self.assertContains(response, SPRINT_SECTION_OPEN_TAG)

    def test_sprint_cards_track_sizes_off_the_section_not_the_viewport(self):
        _active_sprint('Free Open Sprint', 'free-open-sprint', LEVEL_OPEN)

        response = self.client.get('/')

        self.assertContains(response, SPRINT_CARD_TRACK)
        # The viewport-keyed column utilities cannot express the widened
        # section, so they must be gone.
        self.assertNotContains(
            response,
            'class="grid gap-3 sm:grid-cols-2 lg:grid-cols-1"',
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

    def test_free_open_sprint_chip_uses_the_accent_badge_tone(self):
        _active_sprint('Free Open Sprint', 'free-open-sprint', LEVEL_OPEN)

        response = self.client.get('/')

        chip = self._badge(response, 'dashboard-active-sprint-tier')
        self.assertIn('data-component="member-badge"', chip)
        self.assertIn('bg-accent/10 text-accent', chip)
        self.assertNotIn('bg-secondary', chip)
        self.assertIn('Free/open', chip)

    def test_paid_sprint_chip_keeps_the_required_tier_label(self):
        self.user.tier = self.main_tier
        self.user.save(update_fields=['tier'])
        _active_sprint('Main Sprint', 'main-sprint', LEVEL_MAIN)

        response = self.client.get('/')

        chip = self._badge(response, 'dashboard-active-sprint-tier')
        self.assertIn('data-component="member-badge"', chip)
        self.assertIn('Main', chip)
        self.assertNotIn('Free/open', chip)


class DashboardEmptyStateOwnerTest(TierSetupMixin, TestCase):
    """#1312 item 3: both stragglers render the shared empty state."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='empty@test.com', password='pw', tier=self.free_tier,
        )
        self.client.login(email='empty@test.com', password='pw')

    def _empty_states(self, response):
        """Return the markup of each shared empty-state card on the page."""
        content = response.content.decode()
        marker = (
            '<div class="rounded-lg border border-border bg-card '
            'p-8 text-center sm:p-10"'
        )
        starts = [m.start() for m in re.finditer(re.escape(marker), content)]
        bounds = starts[1:] + [len(content)]
        return [content[start:end] for start, end in zip(starts, bounds)]

    def test_polls_empty_state_uses_the_component_and_keeps_its_cta(self):
        response = self.client.get('/')

        blocks = [b for b in self._empty_states(response)
                  if 'No active polls right now' in b]
        self.assertEqual(len(blocks), 1, 'polls empty state not rendered')
        self.assertIn('data-lucide="bar-chart-3"', blocks[0])
        self.assertIn('href="/vote"', blocks[0])
        self.assertIn('View past polls', blocks[0])
        # The hand-rolled icon-plus-copy card must be gone.
        self.assertNotContains(
            response, 'rounded-lg border border-border bg-card p-6 text-center',
        )

    def test_sprint_empty_state_uses_the_component_and_keeps_its_cta(self):
        response = self.client.get('/')

        blocks = [b for b in self._empty_states(response)
                  if 'No active sprint openings for your tier' in b]
        self.assertEqual(len(blocks), 1, 'sprint empty state not rendered')
        self.assertIn('data-lucide="users"', blocks[0])
        self.assertIn('href="/activities"', blocks[0])
        self.assertIn('Browse activities', blocks[0])


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
            response, 'data-testid="free-activation-complete-ai-hero"',
        )
        checklist = re.search(
            r'data-testid="free-activation-checklist".*?'
            r'data-testid="free-plan-teaser"',
            content, re.S,
        ).group(0)
        self.assertEqual(
            checklist.count('text-emerald-800 dark:text-emerald-300'), 2,
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
            '<p class="text-sm font-medium uppercase tracking-widest '
            'text-accent">Getting started</p>',
            html=False,
        )
        self.assertContains(
            response,
            '<h2 class="mt-1 text-xl font-semibold tracking-tight '
            'text-foreground">Start building with your Free account</h2>',
            html=False,
        )
