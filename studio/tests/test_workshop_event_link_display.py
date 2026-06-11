"""Studio read-only display of the workshop<->event link (issue #879).

The explicit-link model (workshop.yaml ``event_id`` / ``event_slug``) binds a
GitHub workshop to a Studio event. These tests verify the verification surface:

- The Studio event edit page shows the linked workshop with working links.
- The Studio workshop edit page shows the linked event with working links.
- Neither row renders when there is no link.

Link RESOLUTION is covered by the dispatcher tests in
``integrations/tests/test_workshop_sync.py``; here we only assert the read-only
display contract on the two Studio forms.
"""

import datetime
from datetime import timezone as dt_timezone

from django.test import TestCase

from content.models import Workshop
from events.models import Event
from tests.fixtures import StaffUserMixin


def _make_studio_event(slug='take-home-live', title='Take-Home Assignment Live'):
    return Event.objects.create(
        slug=slug,
        title=title,
        start_datetime=datetime.datetime(2026, 4, 21, 15, 0, tzinfo=dt_timezone.utc),
        status='upcoming',
        timezone='UTC',
        published=True,
    )


def _make_workshop(slug='demo', title='Demo Workshop', event=None):
    return Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        description='Hands-on intro.',
        status='published',
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
        source_repo='AI-Shipping-Labs/workshops-content',
        source_path=f'2026/{slug}/workshop.yaml',
        source_commit='abc1234def5678901234567890123456789abcde',
        event=event,
    )


class StudioEventLinkedWorkshopDisplayTest(StaffUserMixin, TestCase):
    """The event edit page surfaces its linked workshop, read-only."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_event_edit_shows_linked_workshop_with_links(self):
        event = _make_studio_event()
        workshop = _make_workshop(event=event)

        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="event-linked-workshop"')
        self.assertContains(response, workshop.title)
        # Both the public and the Studio edit URL are present and working.
        self.assertContains(response, f'href="{workshop.get_absolute_url()}"')
        self.assertContains(
            response, f'href="{workshop.get_studio_edit_url()}"',
        )

    def test_event_edit_without_linked_workshop_hides_row(self):
        event = _make_studio_event()
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="event-linked-workshop"')


class StudioWorkshopLinkedEventDisplayTest(StaffUserMixin, TestCase):
    """The workshop edit page surfaces its linked event, read-only."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_workshop_edit_shows_linked_event_with_links(self):
        event = _make_studio_event()
        workshop = _make_workshop(event=event)

        response = self.client.get(f'/studio/workshops/{workshop.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="workshop-linked-event"')
        self.assertContains(response, event.title)
        self.assertContains(response, f'href="{event.get_absolute_url()}"')
        self.assertContains(
            response, f'href="{event.get_studio_edit_url()}"',
        )

    def test_workshop_edit_without_linked_event_hides_row(self):
        workshop = _make_workshop()
        response = self.client.get(f'/studio/workshops/{workshop.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="workshop-linked-event"')
