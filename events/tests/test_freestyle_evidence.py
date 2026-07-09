from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from content.models import Workshop, WorkshopPage
from events.models import Event


class FreestyleEvidenceTest(TestCase):
    def _make_past_freestyle_event(
        self, slug, title, *, days_ago, with_workshop=False,
    ):
        start = timezone.now() - timedelta(days=days_ago)
        event = Event.objects.create(
            title=title,
            slug=slug,
            start_datetime=start,
            status='completed',
            recording_url=f'https://example.com/{slug}.mp4',
        )
        if with_workshop:
            Workshop.objects.create(
                slug=f'{slug}-writeup',
                title=f'{title} Writeup',
                date=start.date(),
                status='published',
                landing_required_level=0,
                pages_required_level=5,
                recording_required_level=20,
                event=event,
            )
        return event

    def test_paid_freestyle_event_shows_three_relevant_past_links(self):
        event = Event.objects.create(
            title='Premium Freestyle Build',
            slug='premium-freestyle-build',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=20,
        )
        self._make_past_freestyle_event(
            'fresh-freestyle',
            'Fresh Freestyle Session',
            days_ago=3,
            with_workshop=True,
        )
        self._make_past_freestyle_event(
            'mid-freestyle',
            'Mid Freestyle Session',
            days_ago=7,
        )
        self._make_past_freestyle_event(
            'older-freestyle',
            'Older Freestyle Session',
            days_ago=14,
        )
        self._make_past_freestyle_event(
            'oldest-freestyle',
            'Oldest Freestyle Session',
            days_ago=21,
        )
        self._make_past_freestyle_event(
            'plain-session',
            'Regular Community Session',
            days_ago=1,
        )

        response = self.client.get(event.get_absolute_url())
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="freestyle-evidence-block"')
        self.assertEqual(body.count('data-testid="freestyle-evidence-link"'), 3)
        self.assertContains(response, 'Fresh Freestyle Session Writeup')
        self.assertContains(response, 'Mid Freestyle Session')
        self.assertContains(response, 'Older Freestyle Session')
        self.assertNotContains(response, 'Oldest Freestyle Session')
        self.assertNotContains(response, 'Regular Community Session')

    def test_non_freestyle_event_does_not_show_evidence(self):
        event = Event.objects.create(
            title='Premium Q and A',
            slug='premium-q-and-a',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=20,
        )
        self._make_past_freestyle_event(
            'freestyle-reference',
            'Freestyle Reference Session',
            days_ago=4,
        )

        response = self.client.get(event.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="freestyle-evidence-block"')

    def test_paid_freestyle_workshop_page_shows_relevant_past_links(self):
        event = Event.objects.create(
            title='Freestyle Workshop Event',
            slug='freestyle-workshop-event',
            start_datetime=timezone.now() + timedelta(days=10),
            status='upcoming',
            required_level=20,
        )
        workshop = Workshop.objects.create(
            slug='freestyle-workshop',
            title='Freestyle Workshop',
            date=timezone.localdate() + timedelta(days=10),
            status='published',
            landing_required_level=0,
            pages_required_level=20,
            recording_required_level=20,
            event=event,
        )
        page = WorkshopPage.objects.create(
            workshop=workshop,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='This is the freestyle tutorial body.',
        )
        self._make_past_freestyle_event(
            'recent-freestyle',
            'Recent Freestyle Run',
            days_ago=2,
        )
        self._make_past_freestyle_event(
            'earlier-freestyle',
            'Earlier Freestyle Run',
            days_ago=6,
            with_workshop=True,
        )

        response = self.client.get(page.get_absolute_url())
        body = response.content.decode()

        self.assertEqual(response.status_code, 403)
        self.assertIn('data-testid="page-paywall"', body)
        self.assertIn('data-testid="freestyle-evidence-block"', body)
        self.assertEqual(body.count('data-testid="freestyle-evidence-link"'), 2)
        self.assertIn('Recent Freestyle Run', body)
        self.assertIn('Earlier Freestyle Run Writeup', body)
