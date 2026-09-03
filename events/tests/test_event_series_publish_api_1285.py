"""Relocated staff-token series publish-drafts API owner (#1482)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventSeries

User = get_user_model()


def _event(slug, **overrides):
    values = {
        'title': 'Operator source event',
        'slug': slug,
        'description': 'Reusable operator description',
        'start_datetime': timezone.now() + timedelta(days=7),
        'end_datetime': timezone.now() + timedelta(days=7, hours=2),
        'timezone': 'Europe/Berlin',
        'status': 'draft',
        'origin': 'studio',
    }
    values.update(overrides)
    return Event.objects.create(**values)


@tag('core')
class TokenApiPublishScopedIdempotentTest(TestCase):
    """Owns token-auth series publish-drafts scoping and replay.

    Relocated from Playwright ``test_token_api_publish_is_scoped_and_idempotent``.
    """

    def test_token_api_publish_is_scoped_and_idempotent(self):
        staff = User.objects.create_user(
            email='api-browser-operator-1285@test.com',
            password='pw',
            is_staff=True,
        )
        token = Token.objects.create(user=staff, name='browser-publish-1285')
        series = EventSeries.objects.create(
            name='Browser API series',
            slug='browser-api-series-1285',
            start_time=timezone.localtime().time().replace(
                second=0, microsecond=0,
            ),
        )
        drafts = [
            _event(
                f'browser-api-draft-{index}-1285',
                event_series=series,
                series_position=index,
            )
            for index in (1, 2)
        ]
        unrelated = _event('browser-api-unrelated-1285')
        series_id = series.pk
        draft_ids = [event.pk for event in drafts]
        headers = {'HTTP_AUTHORIZATION': f'Token {token.key}'}
        url = f'/api/event-series/{series_id}/publish-drafts'

        first = self.client.post(url, **headers)
        self.assertEqual(first.json(), {
            'series_id': series_id,
            'published_count': 2,
            'occurrence_ids': draft_ids,
        })
        replay = self.client.post(url, **headers)
        self.assertEqual(replay.json(), {
            'series_id': series_id,
            'published_count': 0,
            'occurrence_ids': [],
        })
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.status, 'draft')
        self.assertEqual(
            list(
                Event.objects.filter(pk__in=draft_ids)
                .order_by('pk')
                .values_list('status', flat=True)
            ),
            ['upcoming', 'upcoming'],
        )
