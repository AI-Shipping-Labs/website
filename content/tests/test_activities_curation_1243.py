import datetime
import re

from django.test import TestCase
from django.utils import timezone

from content.models import SiteConfig, Workshop
from events.models import Event

CURATED_SLUGS = [
    "community-sprints",
    "live-events",
    "workshops",
    "slack-community",
    "personal-plans",
    "exclusive-content",
    "courses",
]


class ActivitiesCuration1243Test(TestCase):
    def _card_markup(self, response, slug):
        content = response.content.decode()
        marker = f'data-activity="{slug}"'
        marker_index = content.index(marker)
        card_start = content.rfind("<article", 0, marker_index)
        return content[card_start : content.index("</article>", marker_index)]

    def test_page_uses_exact_curated_list_independent_of_site_config(self):
        SiteConfig.objects.create(
            key="tiers",
            data=[
                {
                    "name": "Basic",
                    "stripe_key": "basic",
                    "activities": [
                        {
                            "title": "Community Hackathons",
                            "icon": "x",
                            "description": "Stale configuration.",
                        }
                    ],
                }
            ],
        )

        response = self.client.get("/activities")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [activity["slug"] for activity in response.context["activities"]],
            CURATED_SLUGS,
        )
        self.assertContains(response, 'data-testid="activity-card"', count=7)
        for slug in CURATED_SLUGS:
            self.assertContains(response, f'data-activity="{slug}"', count=1)
        for stale_title in [
            "Community Hackathons",
            "Personal Brand Development",
            "Developer Productivity Tips",
            "Curated Social Content Collection",
            "Behind-the-Scenes Research",
            "Exclusive Substack Content",
            "Profile Teardowns",
        ]:
            self.assertNotContains(response, stale_title)

    def test_card_badges_and_comparison_match_exact_tier_mapping(self):
        response = self.client.get("/activities")
        expected = {
            "community-sprints": (False, True, True),
            "live-events": (False, True, True),
            "workshops": (False, True, True),
            "slack-community": (False, True, True),
            "personal-plans": (False, True, True),
            "exclusive-content": (True, True, True),
            "courses": (False, False, True),
        }

        for slug, inclusions in expected.items():
            card = self._card_markup(response, slug)
            for tier, included in zip(
                ("basic", "main", "premium"),
                inclusions,
                strict=True,
            ):
                self.assertIn(
                    f'data-tier="{tier}" data-included="{str(included).lower()}"',
                    card,
                )

        self.assertEqual(response.context["basic_count"], 1)
        self.assertEqual(response.context["main_count"], 6)
        self.assertEqual(response.context["premium_count"], 7)
        self.assertEqual(
            [item["slug"] for item in response.context["basic_activities"]],
            ["exclusive-content"],
        )

    def test_intro_navigation_is_exactly_three_same_page_anchors(self):
        response = self.client.get("/activities")
        content = response.content.decode()
        intro = content[
            content.index('data-testid="activities-access-by-tier-intro"') : content.index(
                'data-testid="activities-grid"'
            )
        ]

        nav = re.search(
            r'<nav[^>]*data-testid="activities-anchor-nav"[^>]*>(.*?)</nav>',
            intro,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(nav)
        hrefs = re.findall(r'href="([^"]+)"', nav.group(1))
        self.assertEqual(
            hrefs,
            ["#community-sprints", "#live-events", "#workshops"],
        )
        for fragment in hrefs:
            self.assertIn(f'id="{fragment[1:]}"', content)
        self.assertEqual(re.findall(r'href="([^"]+)"', intro), hrefs)
        self.assertNotIn("Compare pricing", nav.group(1))

    def test_obsolete_filters_empty_branch_and_secondary_nav_are_removed(self):
        response = self.client.get("/activities")

        self.assertNotContains(response, 'data-testid="activities-tier-filter"')
        self.assertNotContains(response, "filterActivities")
        self.assertNotContains(response, 'data-testid="activities-tier-empty"')
        self.assertNotContains(response, 'data-testid="activities-secondary-nav"')
        self.assertContains(response, 'data-testid="activities-pricing-cta"')
        self.assertContains(response, 'href="/pricing"')


class ActivitiesPreviewSections1243Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.events = []
        for offset in (4, 1, 3, 2):
            cls.events.append(
                Event.objects.create(
                    title=f"Published event {offset}",
                    slug=f"published-event-{offset}",
                    start_datetime=now + datetime.timedelta(days=offset),
                    end_datetime=now + datetime.timedelta(days=offset, hours=1),
                    status="upcoming",
                    published=True,
                )
            )
        Event.objects.create(
            title="Unpublished event",
            slug="unpublished-event",
            start_datetime=now + datetime.timedelta(hours=12),
            end_datetime=now + datetime.timedelta(hours=13),
            status="upcoming",
            published=False,
        )

        cls.workshops = []
        for day in (1, 4, 2, 3):
            cls.workshops.append(
                Workshop.objects.create(
                    title=f"Published workshop {day}",
                    slug=f"published-workshop-{day}",
                    date=datetime.date(2026, 7, day),
                    status="published",
                )
            )
        Workshop.objects.create(
            title="Draft workshop",
            slug="draft-workshop",
            date=datetime.date(2026, 7, 5),
            status="draft",
        )

    def test_previews_are_public_published_ordered_and_limited_to_three(self):
        response = self.client.get("/activities")

        self.assertEqual(
            [event.slug for event in response.context["upcoming_events"]],
            ["published-event-1", "published-event-2", "published-event-3"],
        )
        self.assertEqual(
            [workshop.slug for workshop in response.context["recent_workshops"]],
            [
                "published-workshop-4",
                "published-workshop-3",
                "published-workshop-2",
            ],
        )
        self.assertContains(
            response,
            'data-testid="activities-live-event-card"',
            count=3,
        )
        self.assertContains(
            response,
            'data-testid="activities-workshop-card"',
            count=3,
        )
        self.assertNotContains(response, "Published event 4")
        self.assertNotContains(response, "Unpublished event")
        self.assertNotContains(response, "Published workshop 1")
        self.assertNotContains(response, "Draft workshop")
        for event_slug in ("published-event-1", "published-event-2", "published-event-3"):
            event = Event.objects.get(slug=event_slug)
            self.assertContains(response, f'href="{event.get_absolute_url()}"')
        for workshop_slug in (
            "published-workshop-4",
            "published-workshop-3",
            "published-workshop-2",
        ):
            self.assertContains(response, f'href="/workshops/{workshop_slug}"')
        self.assertContains(response, 'data-testid="activities-view-all-events"')
        self.assertContains(response, 'data-testid="activities-view-all-workshops"')


class ActivitiesPreviewEmptyStates1243Test(TestCase):
    def test_empty_previews_render_friendly_shared_empty_states(self):
        response = self.client.get("/activities")

        self.assertContains(response, 'data-testid="activities-live-events-empty"')
        self.assertContains(response, "No live events scheduled yet")
        self.assertContains(response, 'data-testid="activities-workshops-empty"')
        self.assertContains(response, "No workshops published yet")
        self.assertContains(response, 'data-testid="member-empty-state"', count=3)
        self.assertContains(response, 'href="/events"')
        self.assertContains(response, 'href="/workshops"')
