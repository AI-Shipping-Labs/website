"""Studio ``EventSeries.visibility`` control (issue #1660).

A NEW, separate flag from the pre-existing ``is_active`` "Visible to the
public" checkbox covered in ``StudioEventSeriesVisibilityToggleTest``
(``studio/tests/test_event_series.py``).
"""

from datetime import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from events.models import EventSeries
from events.models.event_series import VISIBILITY_HIDDEN

User = get_user_model()


class StaffMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff-1660@test.com', password='pass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff-1660@test.com', password='pass')


class StudioEventSeriesVisibilityControlTest(StaffMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Discovery Series', slug='discovery-series-1660',
            start_time=time(17, 0),
        )

    def test_detail_page_renders_visibility_select(self):
        response = self.client.get(f'/studio/event-series/{self.series.pk}/')
        self.assertContains(response, 'data-testid="event-series-visibility"')
        self.assertContains(response, 'Public listing')
        self.assertContains(response, 'Hidden series')

    def test_default_series_is_public(self):
        response = self.client.get(f'/studio/event-series/{self.series.pk}/')
        self.assertContains(
            response, '<option value="public" selected>',
        )

    def test_posting_hidden_persists(self):
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': self.series.name, 'slug': self.series.slug,
                'description': '', 'is_active': 'on',
                'visibility': 'hidden',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.series.refresh_from_db()
        self.assertEqual(self.series.visibility, VISIBILITY_HIDDEN)
        self.assertTrue(self.series.is_hidden)

    def test_visibility_is_independent_of_is_active(self):
        # Hiding via visibility does not touch is_active, and vice versa.
        self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': self.series.name, 'slug': self.series.slug,
                'description': '', 'is_active': 'on',
                'visibility': 'hidden',
            },
        )
        self.series.refresh_from_db()
        self.assertTrue(self.series.is_active)
        self.assertTrue(self.series.is_hidden)

    def test_posting_public_restores_discovery(self):
        self.series.visibility = 'hidden'
        self.series.save()
        self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': self.series.name, 'slug': self.series.slug,
                'description': '', 'is_active': 'on',
                'visibility': 'public',
            },
        )
        self.series.refresh_from_db()
        self.assertEqual(self.series.visibility, 'public')


class StudioEventSeriesListHiddenPillTest(StaffMixin, TestCase):
    def test_hidden_series_shows_pill(self):
        EventSeries.objects.create(
            name='Hidden Pill Series', slug='hidden-pill-series-1660',
            start_time=time(17, 0), visibility='hidden',
        )
        response = self.client.get('/studio/event-series/')
        self.assertContains(response, 'Hidden Pill Series')
        self.assertContains(response, 'Hidden')

    def test_public_series_shows_no_hidden_pill(self):
        EventSeries.objects.create(
            name='Public Pill Series', slug='public-pill-series-1660',
            start_time=time(17, 0),
        )
        response = self.client.get('/studio/event-series/')
        self.assertContains(response, 'Public Pill Series')
        self.assertNotContains(response, 'data-component="studio-status-badge"')
