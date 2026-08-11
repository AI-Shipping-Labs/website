import datetime
from pathlib import Path

import yaml
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve
from django.utils import timezone

from content.models import Workshop
from events.models import Event, EventRegistration, EventSeries

User = get_user_model()

EXPLAINED_BENEFITS = [
    "Exclusive written content",
    "Workshop content",
    "Community sprints",
    "Live events",
    "Private Slack community",
    "Personalized onboarding plan",
    "Topic voting",
    "Courses",
]


class ActivitiesCuration1243Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixture_path = Path(__file__).parent / "fixtures" / "tiers.yaml"
        with open(fixture_path) as handle:
            tiers_data = yaml.safe_load(handle)
        from content.models import SiteConfig

        SiteConfig.objects.create(key="tiers", data=tiers_data)

    def _card_markup(self, response, title):
        content = response.content.decode()
        benefits_index = content.index('id="activities"')
        marker_index = content.index(title, benefits_index)
        card_start = content.rfind("<article", 0, marker_index)
        return content[card_start : content.index("</article>", marker_index)]

    def test_page_uses_membership_benefit_records(self):
        response = self.client.get("/membership")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [benefit["title"] for benefit in response.context["membership_benefits"]],
            EXPLAINED_BENEFITS,
        )
        self.assertContains(response, 'data-testid="membership-benefit-row"', count=8)

    def test_card_badges_match_the_benefit_owner_tier(self):
        response = self.client.get("/membership")
        expected = {
            "Exclusive written content": 10,
            "Workshop content": 10,
            "Community sprints": 20,
            "Personalized onboarding plan": 20,
            "Courses": 30,
        }

        for title, required_level in expected.items():
            card = self._card_markup(response, title)
            self.assertEqual(card.count('data-testid="membership-benefit-tier-badge"'), 1)
            self.assertIn(f'data-required-level="{required_level}"', card)

    def test_plans_come_first_then_centered_three_xl_benefit_rows(self):
        response = self.client.get("/membership")
        content = response.content.decode()

        benefits_section = content[content.index('id="activities"') :]
        self.assertIn('Member benefits', benefits_section)
        self.assertIn('mx-auto mt-10 max-w-3xl', benefits_section)
        self.assertIn('border-t border-border/70', benefits_section)
        self.assertLess(
            content.index('data-testid="pricing-tier-carousel"'),
            content.index('data-testid="membership-benefits-list"'),
        )

    def test_obsolete_filters_empty_branch_and_secondary_nav_are_removed(self):
        response = self.client.get("/membership")

        self.assertNotContains(response, 'data-testid="activities-tier-filter"')
        self.assertNotContains(response, "filterActivities")
        self.assertNotContains(response, 'data-testid="activities-tier-empty"')
        self.assertNotContains(response, 'data-testid="activities-secondary-nav"')
        self.assertNotContains(response, 'data-testid="activities-access-by-tier-section"')


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
            status="draft",
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

    def test_previews_reuse_first_canonical_event_row_and_three_workshops(self):
        response = self.client.get("/membership")
        events_response = self.client.get("/events")

        self.assertEqual(
            response.context["upcoming_rows"],
            events_response.context["upcoming_rows"][:1],
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
            'data-testid="upcoming-event-card"',
            count=1,
        )
        self.assertContains(
            response,
            'data-testid="workshop-card"',
            count=3,
        )
        self.assertNotContains(response, "Published event 4")
        self.assertNotContains(response, "Published event 2")
        self.assertNotContains(response, "Published event 3")
        self.assertNotContains(response, "Unpublished event")
        self.assertNotContains(response, "Published workshop 1")
        self.assertNotContains(response, "Draft workshop")
        for event_slug in ("published-event-1",):
            event = Event.objects.get(slug=event_slug)
            self.assertContains(response, f'href="{event.get_absolute_url()}"')
        for workshop_slug in (
            "published-workshop-4",
            "published-workshop-3",
            "published-workshop-2",
        ):
            self.assertContains(response, f'href="/workshops/{workshop_slug}"')
        self.assertContains(response, 'data-testid="membership-view-all-events"')
        self.assertContains(response, 'data-testid="membership-view-all-workshops"')
        self.assertContains(response, 'data-testid="membership-live-events-list"')
        self.assertContains(response, 'data-testid="events-timeline"')
        self.assertContains(response, 'data-testid="timeline-day-date"')
        self.assertContains(response, 'mx-auto max-w-3xl px-4 sm:px-6 lg:px-8')


class MembershipOwnershipAndTimeline1402Test(TestCase):
    def test_content_owns_membership_and_payments_keeps_checkout(self):
        membership_match = resolve('/membership')
        checkout_match = resolve('/payments/checkout/basic/monthly')
        response = self.client.get('/membership')

        self.assertEqual(
            membership_match.func.__module__,
            'content.views.membership',
        )
        self.assertEqual(
            checkout_match.func.__module__,
            'payments.views.pricing',
        )
        self.assertTemplateUsed(response, 'content/membership/page.html')
        self.assertNotIn(
            'payments/pricing.html',
            [template.name for template in response.templates],
        )

    def test_membership_template_includes_complete_timeline_owner(self):
        previews_path = (
            Path(__file__).parents[2]
            / 'templates/content/membership/_previews.html'
        )
        source = previews_path.read_text()

        self.assertIn('events/_events_timeline.html', source)
        self.assertNotIn('events/_timeline_listing_card.html', source)

    def test_series_is_collapsed_before_membership_preview_limit(self):
        series = EventSeries.objects.create(
            name='Canonical Membership Series',
            slug='canonical-membership-series',
        )
        now = timezone.now()
        for position, days in enumerate((1, 8), start=1):
            Event.objects.create(
                title=f'Series occurrence {position}',
                slug=f'membership-series-occurrence-{position}',
                start_datetime=now + datetime.timedelta(days=days),
                end_datetime=now + datetime.timedelta(days=days, hours=1),
                timezone='Europe/Berlin',
                status='upcoming',
                event_series=series,
                series_position=position,
            )
        Event.objects.create(
            title='Later standalone event',
            slug='later-membership-standalone',
            start_datetime=now + datetime.timedelta(days=12),
            status='upcoming',
        )

        response = self.client.get('/membership')

        self.assertEqual(len(response.context['upcoming_rows']), 1)
        row = response.context['upcoming_rows'][0]
        self.assertEqual(row['kind'], 'series')
        self.assertEqual(row['series'], series)
        self.assertEqual(row['count'], 2)
        self.assertContains(response, 'data-testid="events-timeline-day"')
        self.assertContains(response, 'data-testid="event-series-card"')
        self.assertContains(response, '2 upcoming sessions')
        self.assertContains(response, f'href="{series.get_absolute_url()}"')
        self.assertNotContains(response, 'Later standalone event')

    def test_member_timezone_registration_and_attendee_count_match_events(self):
        member = User.objects.create_user(
            email='membership-timeline@example.com',
            password='pw',
            preferred_timezone='Asia/Kolkata',
        )
        other = User.objects.create_user(
            email='membership-attendee@example.com',
            password='pw',
        )
        event = Event.objects.create(
            title='Registered Membership Event',
            slug='registered-membership-event',
            start_datetime=timezone.now() + datetime.timedelta(days=2),
            timezone='UTC',
            status='upcoming',
        )
        EventRegistration.objects.create(user=member, event=event)
        EventRegistration.objects.create(user=other, event=event)
        self.client.force_login(member)

        membership = self.client.get('/membership')
        events = self.client.get('/events')

        self.assertEqual(membership.context['events_display_timezone'], 'Asia/Kolkata')
        self.assertEqual(
            membership.context['upcoming_days'],
            events.context['upcoming_days'][:1],
        )
        self.assertIn(event.id, membership.context['registered_event_ids'])
        self.assertContains(membership, 'Registered')
        self.assertContains(membership, '2 registered')


class ActivitiesPreviewEmptyStates1243Test(TestCase):
    def test_empty_previews_render_friendly_shared_empty_states(self):
        response = self.client.get("/membership")

        self.assertContains(response, 'data-testid="membership-live-events-empty"')
        self.assertContains(response, "No live events scheduled yet")
        self.assertContains(response, 'data-testid="membership-workshops-empty"')
        self.assertContains(response, "No workshops published yet")
        self.assertContains(response, 'data-testid="member-empty-state"', count=3)
        self.assertContains(response, 'href="/events"')
        self.assertContains(response, 'href="/workshops"')
