"""Tests for the booking Zoom meeting service payload (issue #1951).

AC10: ``create_booking_zoom_meeting`` must send ``auto_recording``
from the ``ZOOM_AUTO_RECORDING`` config (default ``cloud``) so booked
1:1s cloud-record and the capture pipeline can pull the transcript.
"""

import datetime
import os
from unittest import mock

from django.test import TestCase

from booking.services.zoom import create_booking_zoom_meeting


def _ok_response():
    response = mock.Mock()
    response.status_code = 201
    response.json.return_value = {
        'id': 12345,
        'join_url': 'https://zoom.test/j/12345',
    }
    return response


class CreateBookingZoomMeetingAutoRecordingTest(TestCase):
    def _create(self, env_overrides):
        env = {k: v for k, v in os.environ.items()
               if k != 'ZOOM_AUTO_RECORDING'}
        env.update(env_overrides)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            'booking.services.zoom.get_access_token', return_value='tok',
        ), mock.patch(
            'booking.services.zoom.requests.post',
            return_value=_ok_response(),
        ) as post:
            result = create_booking_zoom_meeting(
                topic='30-min meeting: Jane and Alexey',
                start_datetime=datetime.datetime(
                    2026, 10, 20, 15, 0,
                    tzinfo=datetime.timezone.utc,
                ),
                duration_minutes=30,
            )
        return result, post

    def test_cloud_recording_by_default(self):
        result, post = self._create({})
        self.assertEqual(result['meeting_id'], '12345')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['settings']['auto_recording'], 'cloud')

    def test_config_override_is_honoured(self):
        _result, post = self._create({'ZOOM_AUTO_RECORDING': 'local'})
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['settings']['auto_recording'], 'local')

    def test_scheduling_payload_is_still_sent(self):
        _result, post = self._create({})
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['type'], 2)
        self.assertEqual(payload['duration'], 30)
        self.assertTrue(payload['settings']['join_before_host'])
        self.assertFalse(payload['settings']['waiting_room'])
