import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event
from integrations.services.zoom import ZoomAPIError

User = get_user_model()


class EventZoomSyncApiTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='zoom-sync-staff@example.com',
            password='pw',
            is_staff=True,
        )
        self.staff_token = Token.objects.create(user=self.staff, name='zoom sync')
        self.staff_key = self.staff_token.key
        self.member = User.objects.create_user(
            email='zoom-sync-member@example.com',
            password='pw',
        )
        self.member_token = Token(
            key='zoom-sync-nonstaff-token',
            user=self.member,
            name='nonstaff',
        )
        Token.objects.bulk_create([self.member_token])
        self.start = timezone.now() + timedelta(days=2)
        self.event = self._event(
            title='Zoom sync event',
            slug='zoom-sync-event',
            zoom_meeting_id='83220656366',
            zoom_join_url='https://zoom.us/j/private-join',
        )

    def _event(self, **overrides):
        values = {
            'title': 'Event',
            'slug': f'event-{uuid.uuid4().hex}',
            'platform': 'zoom',
            'start_datetime': self.start,
            'end_datetime': self.start + timedelta(hours=2),
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'origin': 'studio',
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def _post(self, event=None, *, key=None, client=None):
        event = event or self.event
        key = self.staff_key if key is None else key
        headers = {'HTTP_AUTHORIZATION': f'Token {key}'} if key else {}
        client = client or self.client
        return client.post(
            f'/api/events/{event.slug}/sync-zoom',
            **headers,
        )

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_staff_token_bypasses_csrf_while_unauthorized_callers_do_not(
        self,
        update_meeting,
    ):
        csrf_client = Client(enforce_csrf_checks=True)

        anonymous = self._post(key='', client=csrf_client)
        nonstaff = self._post(
            key=self.member_token.key,
            client=csrf_client,
        )
        update_meeting.assert_not_called()

        staff = self._post(client=csrf_client)

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(nonstaff.status_code, 401)
        self.assertEqual(staff.status_code, 200)
        self.assertEqual(staff.json()['zoom_sync_status'], 'synced')
        update_meeting.assert_called_once()

    @patch('api.views.events._maybe_enqueue_banner')
    @patch('api.views.events.enqueue_schedule_update')
    @patch('api.views.events.create_meeting')
    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_success_and_repeat_patch_same_meeting_without_local_side_effects(
        self,
        update_meeting,
        create_meeting,
        enqueue_schedule_update,
        enqueue_banner,
    ):
        before = {
            'title': self.event.title,
            'start_datetime': self.event.start_datetime,
            'end_datetime': self.event.end_datetime,
            'timezone': self.event.timezone,
            'zoom_meeting_id': self.event.zoom_meeting_id,
            'zoom_join_url': self.event.zoom_join_url,
            'updated_at': self.event.updated_at,
        }

        first = self._post()
        second = self._post()

        for response in (first, second):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(),
                {
                    'zoom_sync_status': 'synced',
                    'zoom_meeting_id': '83220656366',
                },
            )
            self.assertNotIn('join', response.content.decode().lower())
        self.assertEqual(update_meeting.call_count, 2)
        self.assertTrue(all(
            call.args[0].zoom_meeting_id == '83220656366'
            for call in update_meeting.call_args_list
        ))
        create_meeting.assert_not_called()
        enqueue_schedule_update.assert_not_called()
        enqueue_banner.assert_not_called()
        self.event.refresh_from_db()
        self.assertEqual(
            {
                'title': self.event.title,
                'start_datetime': self.event.start_datetime,
                'end_datetime': self.event.end_datetime,
                'timezone': self.event.timezone,
                'zoom_meeting_id': self.event.zoom_meeting_id,
                'zoom_join_url': self.event.zoom_join_url,
                'updated_at': self.event.updated_at,
            },
            before,
        )

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_provider_failure_returns_sanitized_structured_502(self, update):
        update.side_effect = ZoomAPIError(
            'Zoom PATCH failed',
            status_code=400,
            response_data={
                'code': 300,
                'message': (
                    'Unsupported auto_transcribing; '
                    'join_url=https://zoom.us/j/private '
                    'Bearer oauth-secret'
                ),
                'authorization': 'Bearer raw-secret',
                'debug_payload': {'unexpected': True},
            },
        )
        before = (
            self.event.title,
            self.event.start_datetime,
            self.event.end_datetime,
            self.event.zoom_meeting_id,
            self.event.zoom_join_url,
            self.event.updated_at,
        )

        response = self._post()

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body['code'], 'zoom_sync_failed')
        self.assertIn('not changed', body['error'])
        self.assertEqual(
            body['details'],
            {
                'operation': 'update_meeting',
                'http_status': 400,
                'provider_code': 300,
                'provider_message': (
                    'Unsupported auto_transcribing; '
                    'join_url=[redacted] Bearer [redacted]'
                ),
                'zoom_meeting_id': '83220656366',
            },
        )
        response_text = response.content.decode()
        self.assertNotIn('zoom.us', response_text)
        self.assertNotIn('oauth-secret', response_text)
        self.assertNotIn('raw-secret', response_text)
        self.assertNotIn('debug_payload', response_text)
        self.assertNotIn('private-join', response_text)
        self.event.refresh_from_db()
        self.assertEqual(
            (
                self.event.title,
                self.event.start_datetime,
                self.event.end_datetime,
                self.event.zoom_meeting_id,
                self.event.zoom_join_url,
                self.event.updated_at,
            ),
            before,
        )

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_structured_provider_message_never_reaches_response_or_log(self, update):
        update.side_effect = ZoomAPIError(
            'Zoom PATCH failed',
            status_code=400,
            response_data={
                'code': 300,
                'message': {
                    'access_token': 'nested-secret-token',
                    'authorization': {'Bearer': 'nested-auth-secret'},
                    'join_url': 'https://zoom.us/j/nested-private',
                    'debug_payload': {
                        'account': 42,
                        'items': [{'secret': 'arbitrary-debug-secret'}],
                    },
                },
            },
        )

        with self.assertLogs(
            'events.services.zoom_lifecycle',
            level='ERROR',
        ) as logs:
            response = self._post()

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body['code'], 'zoom_sync_failed')
        self.assertEqual(body['details']['http_status'], 400)
        self.assertEqual(body['details']['provider_code'], 300)
        self.assertEqual(
            body['details']['provider_message'],
            'Structured provider message omitted.',
        )
        combined = response.content.decode() + '\n' + '\n'.join(logs.output)
        for forbidden in (
            'nested-secret-token',
            'nested-auth-secret',
            'nested-private',
            'arbitrary-debug-secret',
            'access_token',
            'authorization',
            'join_url',
            'debug_payload',
            'account',
            'zoom.us',
        ):
            self.assertNotIn(forbidden, combined)

        # ZoomAPIError itself retains only the safe allowlisted replacement.
        exception_state = repr(update.side_effect.response_data)
        self.assertEqual(
            update.side_effect.response_data,
            {
                'code': 300,
                'message': 'Structured provider message omitted.',
            },
        )
        self.assertNotIn('nested-secret-token', exception_state)

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_network_failure_is_sanitized_and_returns_502(self, update):
        update.side_effect = RuntimeError(
            'Authorization=Bearer network-secret '
            'https://zoom.us/v2/meetings/83220656366'
        )

        response = self._post()

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body['code'], 'zoom_sync_failed')
        self.assertEqual(body['details']['operation'], 'update_meeting')
        serialized = response.content.decode()
        self.assertNotIn('network-secret', serialized)
        self.assertNotIn('zoom.us', serialized)

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_ineligible_events_return_stable_error_without_zoom_call(self, update):
        events = [
            (
                self._event(
                    platform='custom',
                    zoom_meeting_id='custom-id',
                    slug='custom-sync',
                ),
                'custom_platform',
            ),
            (self._event(slug='missing-id'), 'missing_meeting_id'),
            (
                self._event(
                    slug='cancelled-sync',
                    status='cancelled',
                    zoom_meeting_id='cancelled-id',
                ),
                'cancelled',
            ),
            (
                self._event(
                    slug='ended-sync',
                    status='completed',
                    zoom_meeting_id='ended-id',
                ),
                'ended',
            ),
            (
                self._event(
                    slug='stale-upcoming-sync',
                    start_datetime=timezone.now() - timedelta(hours=2),
                    end_datetime=timezone.now() - timedelta(hours=1),
                    zoom_meeting_id='stale-id',
                ),
                'ended',
            ),
        ]

        for event, reason in events:
            with self.subTest(reason=reason):
                response = self._post(event)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()['code'], 'zoom_sync_ineligible')
                self.assertEqual(response.json()['details'], {'reason': reason})
        update.assert_not_called()

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_synced_event_returns_read_only_conflict_without_zoom_call(self, update):
        event = self._event(
            slug='synced-zoom-event',
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/synced.md',
            content_id=uuid.uuid4(),
            zoom_meeting_id='synced-id',
        )

        response = self._post(event)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'synced_event_read_only')
        update.assert_not_called()

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_unauthenticated_and_nonstaff_cannot_trigger_zoom(self, update):
        anonymous = self._post(key='')
        nonstaff = self._post(key=self.member_token.key)

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(nonstaff.status_code, 401)
        update.assert_not_called()
