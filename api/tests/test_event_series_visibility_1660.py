"""``EventSeries.visibility`` on the token API (issue #1660).

Mirrors the existing ``is_active`` PATCH/GET contract in
``api/tests/test_event_series.py``, which this file reuses the base
fixture from.
"""

from api.tests.test_event_series import EventSeriesApiTestBase
from events.models.event_series import VISIBILITY_HIDDEN, VISIBILITY_PUBLIC


class EventSeriesVisibilityApiTest(EventSeriesApiTestBase):
    def test_get_includes_visibility_field(self):
        response = self._get(f'/api/event-series/{self.series.pk}')
        # No separate status-code assertion: the JSON field check below
        # only passes for the success body shape, not an error payload.
        self.assertEqual(response.json()['visibility'], VISIBILITY_PUBLIC)

    def test_list_includes_visibility_field(self):
        response = self._get('/api/event-series')
        body = response.json()['event_series']
        matching = next(
            item for item in body if item['id'] == self.series.pk
        )
        self.assertEqual(matching['visibility'], VISIBILITY_PUBLIC)

    def test_patch_sets_visibility_hidden(self):
        response = self._patch(
            f'/api/event-series/{self.series.pk}',
            {'visibility': 'hidden'},
        )
        self.assertEqual(response.json()['visibility'], VISIBILITY_HIDDEN)
        self.series.refresh_from_db()
        self.assertEqual(self.series.visibility, VISIBILITY_HIDDEN)
        self.assertTrue(self.series.is_hidden)

    def test_patch_sets_visibility_back_to_public(self):
        self.series.visibility = VISIBILITY_HIDDEN
        self.series.save()
        response = self._patch(
            f'/api/event-series/{self.series.pk}',
            {'visibility': 'public'},
        )
        self.assertEqual(response.json()['visibility'], VISIBILITY_PUBLIC)
        self.series.refresh_from_db()
        self.assertEqual(self.series.visibility, VISIBILITY_PUBLIC)

    def test_patch_invalid_visibility_returns_422(self):
        response = self._patch(
            f'/api/event-series/{self.series.pk}',
            {'visibility': 'secret'},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn('visibility', response.json()['details'])

    def test_visibility_is_independent_of_is_active(self):
        response = self._patch(
            f'/api/event-series/{self.series.pk}',
            {'visibility': 'hidden'},
        )
        self.assertEqual(response.json()['visibility'], VISIBILITY_HIDDEN)
        self.series.refresh_from_db()
        self.assertTrue(self.series.is_active)
        self.assertEqual(self.series.visibility, VISIBILITY_HIDDEN)

    def test_create_defaults_to_public_visibility(self):
        response = self._post('/api/event-series', {
            'name': 'New Series Visibility',
            'day_of_week': 1,
            'start_time': '18:00',
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['visibility'], VISIBILITY_PUBLIC)

    def test_create_accepts_hidden_visibility(self):
        response = self._post('/api/event-series', {
            'name': 'Hidden At Create',
            'day_of_week': 1,
            'start_time': '18:00',
            'visibility': 'hidden',
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['visibility'], VISIBILITY_HIDDEN)

    def test_non_staff_token_cannot_patch(self):
        response = self._patch(
            f'/api/event-series/{self.series.pk}',
            {'visibility': 'hidden'},
            token=self.non_staff_token,
        )
        self.assertEqual(response.status_code, 401)
