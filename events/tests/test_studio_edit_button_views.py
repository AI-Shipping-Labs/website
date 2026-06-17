"""View tests for the "Edit in Studio" button on event surfaces (issue #667).

Covers the event detail and event-series public page. The button is
rendered server-side and gated on ``request.user.is_staff``: staff get
the button, anonymous and non-staff visitors get nothing.
"""

from datetime import time

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from events.models import Event, EventSeries

User = get_user_model()

STUDIO_BUTTON_TESTID = 'data-testid="studio-edit-button"'


def _staff_user():
    return User.objects.create_user(
        email='staff@test.com', password='pw', is_staff=True,
    )


def _free_user():
    return User.objects.create_user(
        email='free@test.com', password='pw',
    )


@tag('core')
class StudioEditButtonEventDetailTest(TestCase):
    """Event detail page renders the button for staff only."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Sample Event',
            slug='sample-event',
            start_datetime=timezone.now(),
            status='upcoming',
        )

    def test_staff_sees_button(self):
        _staff_user()
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(self.event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.event.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')

    def test_free_user_does_not_see_button(self):
        _free_user()
        self.client.login(email='free@test.com', password='pw')
        response = self.client.get(self.event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')


@tag('core')
class StudioEditButtonEventSeriesTest(TestCase):
    """Public event-series page renders the button for staff only."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Sample Series',
            slug='sample-series',
            start_time=time(18, 0),
        )
        # Issue #858: a series needs a published occurrence to be publicly
        # reachable; without one the public page 404s for non-staff.
        Event.objects.create(
            title='Sample Session',
            slug='sample-series-session-1',
            start_datetime=timezone.now() + timezone.timedelta(days=7),
            status='upcoming',
            event_series=cls.series, series_position=1, origin='studio',
        )

    def test_staff_sees_button(self):
        _staff_user()
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(self.series.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.series.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(self.series.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')
