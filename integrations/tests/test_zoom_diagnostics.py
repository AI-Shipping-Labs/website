from django.test import SimpleTestCase

from integrations.services.zoom import (
    ZOOM_PROVIDER_MESSAGE_MAX_LENGTH,
    ZoomAPIError,
    sanitize_provider_message,
)


class ZoomProviderDiagnosticsTest(SimpleTestCase):
    def test_error_retains_only_sanitized_known_provider_fields(self):
        error = ZoomAPIError(
            'Zoom PATCH failed',
            status_code=400,
            response_data={
                'code': 300,
                'message': (
                    'Invalid join_url=https://zoom.us/j/secret '
                    'Authorization: Bearer oauth-secret'
                ),
                'access_token': 'must-never-survive',
                'debug': {'raw': 'payload'},
            },
        )

        self.assertEqual(
            error.diagnostics('update_meeting'),
            {
                'operation': 'update_meeting',
                'http_status': 400,
                'provider_code': 300,
                'provider_message': (
                    'Invalid join_url=[redacted] '
                    'Authorization=[redacted] [redacted]'
                ),
            },
        )
        serialized = repr(error.response_data)
        self.assertNotIn('must-never-survive', serialized)
        self.assertNotIn('debug', serialized)
        self.assertNotIn('zoom.us', serialized)
        self.assertNotIn('oauth-secret', serialized)

    def test_plain_message_conversion_is_bounded_and_actionable(self):
        message = sanitize_provider_message('Invalid topic: ' + 'x' * 500)

        self.assertEqual(len(message), ZOOM_PROVIDER_MESSAGE_MAX_LENGTH)
        self.assertTrue(message.endswith('…'))
        self.assertTrue(message.startswith('Invalid topic:'))

    def test_structured_messages_are_replaced_without_serializing_contents(self):
        structured_values = (
            {
                'access_token': 'nested-secret-token',
                'authorization': {'Bearer': 'nested-auth-secret'},
                'join_url': 'https://zoom.us/j/nested-private',
                'debug_payload': {'account': 42},
            },
            [
                {'access_token': 'list-secret-token'},
                'https://zoom.us/j/list-private',
            ],
            (
                {'authorization': 'Bearer tuple-secret-token'},
                {'debug': 'tuple-debug'},
            ),
        )

        for value in structured_values:
            with self.subTest(value_type=type(value).__name__):
                message = sanitize_provider_message(value)
                self.assertEqual(message, 'Structured provider message omitted.')
                serialized = repr(message)
                for forbidden in (
                    'secret-token', 'zoom.us', 'debug', 'account', 'authorization',
                ):
                    self.assertNotIn(forbidden, serialized)

    def test_json_encoded_structured_message_is_also_omitted(self):
        message = sanitize_provider_message(
            '{"access_token":"quoted-secret","debug":{"join_url":'
            '"https://zoom.us/j/quoted-private"}}'
        )

        self.assertEqual(message, 'Structured provider message omitted.')

    def test_authorization_and_url_are_redacted_from_plain_text(self):
        message = sanitize_provider_message(
            'Bearer super-secret at https://zoom.us/j/123?pwd=hidden'
        )

        self.assertEqual(message, 'Bearer [redacted] at [redacted-url]')

    def test_quoted_sensitive_assignment_is_redacted_from_plain_text(self):
        message = sanitize_provider_message(
            'Invalid field "access_token": "quoted-secret"'
        )

        self.assertEqual(message, 'Invalid field access_token=[redacted]')
