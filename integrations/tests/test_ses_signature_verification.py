"""Real cryptographic coverage for the SES/SNS signature trust boundary."""

import base64
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from django.test import TestCase

from email_app.models import SesEvent
from integrations.services import ses

VALID_CERT_URL = (
    'https://sns.us-east-1.amazonaws.com/'
    'SimpleNotificationService-0123456789abcdef.pem'
)

UNSAFE_CERT_URLS = {
    'empty': '',
    'non-string': None,
    'http': VALID_CERT_URL.replace('https:', 'http:'),
    'lookalike': VALID_CERT_URL.replace('amazonaws.com', 'amazonaws.com.evil.test'),
    'subdomain': VALID_CERT_URL.replace('sns.us-east-1', 'extra.sns.us-east-1'),
    'userinfo': VALID_CERT_URL.replace('https://', 'https://attacker@'),
    'password': VALID_CERT_URL.replace('https://', 'https://attacker:secret@'),
    'custom-port': VALID_CERT_URL.replace('amazonaws.com/', 'amazonaws.com:443/'),
    'empty-port': VALID_CERT_URL.replace('amazonaws.com/', 'amazonaws.com:/'),
    'unsupported-region': VALID_CERT_URL.replace('us-east-1', 'us-east-2'),
    'trailing-dot-host': VALID_CERT_URL.replace('amazonaws.com/', 'amazonaws.com./'),
    'malformed-host': 'https://[sns.us-east-1.amazonaws.com/cert.pem',
    'wrong-path': VALID_CERT_URL.replace('SimpleNotificationService-', 'cert-'),
    'short-cert-id': VALID_CERT_URL.replace('0123456789abcdef', 'short'),
    'path-traversal': VALID_CERT_URL.replace('/Simple', '/../Simple'),
    'percent-encoded-traversal': VALID_CERT_URL.replace('/Simple', '/%2e%2e/Simple'),
    'percent-encoded-slash': VALID_CERT_URL.replace('/Simple', '/%2fSimple'),
    'percent-encoded-host-dot': VALID_CERT_URL.replace('.amazonaws', '%2eamazonaws'),
    'query': f'{VALID_CERT_URL}?redirect=https://evil.test/cert.pem',
    'empty-query-delimiter': f'{VALID_CERT_URL}?',
    'duplicate-query-delimiter': f'{VALID_CERT_URL}??redirect=evil',
    'fragment': f'{VALID_CERT_URL}#ignored',
    'empty-fragment-delimiter': f'{VALID_CERT_URL}#',
    'duplicate-fragment-delimiter': f'{VALID_CERT_URL}##ignored',
    'path-params': VALID_CERT_URL.replace('.pem', '.pem;other'),
    'empty-path-params-delimiter': f'{VALID_CERT_URL};',
    'duplicate-path-params-delimiter': f'{VALID_CERT_URL};;other',
    'leading-space': f' {VALID_CERT_URL}',
    'trailing-space': f'{VALID_CERT_URL} ',
    'leading-tab': f'\t{VALID_CERT_URL}',
    'host-tab-before-path': VALID_CERT_URL.replace('amazonaws.com/', 'amazonaws.com\t/'),
    'host-newline-before-path': VALID_CERT_URL.replace(
        'amazonaws.com/',
        'amazonaws.com\n/',
    ),
    'host-carriage-return-before-path': VALID_CERT_URL.replace(
        'amazonaws.com/',
        'amazonaws.com\r/',
    ),
    'trailing-newline': f'{VALID_CERT_URL}\n',
    'embedded-path-space': VALID_CERT_URL.replace('/Simple', '/ Simple'),
    'backslash-authority': VALID_CERT_URL.replace('https://', 'https://evil.test\\@'),
    'backslash-path': VALID_CERT_URL.replace('/Simple', '\\Simple'),
    'unicode-fullwidth-dot': VALID_CERT_URL.replace(
        '.amazonaws.com',
        '\uff0eamazonaws.com',
    ),
    'unicode-division-slash': VALID_CERT_URL.replace('/Simple', '\u2215Simple'),
    'duplicate-authority-slash': VALID_CERT_URL.replace('https://', 'https:///'),
    'double-path-slash': VALID_CERT_URL.replace('/Simple', '//Simple'),
}


def _notification_payload(*, subject=None, signature_version='2'):
    payload = {
        'Type': 'Notification',
        'MessageId': '11111111-2222-3333-4444-555555555555',
        'TopicArn': 'arn:aws:sns:us-east-1:123456789012:ses-events',
        'Message': json.dumps(
            {
                'notificationType': 'Delivery',
                'mail': {
                    'destination': ['member@example.com'],
                    'messageId': 'ses-message-id',
                },
                'delivery': {'recipients': ['member@example.com']},
            },
            sort_keys=True,
        ),
        'Timestamp': '2026-08-14T08:30:00.000Z',
        'SignatureVersion': signature_version,
        'SigningCertURL': VALID_CERT_URL,
    }
    if subject is not None:
        payload['Subject'] = subject
    return payload


def _confirmation_payload(message_type='SubscriptionConfirmation', *, signature_version='1'):
    return {
        'Type': message_type,
        'MessageId': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        'Token': 'confirmation-token',
        'TopicArn': 'arn:aws:sns:us-east-1:123456789012:ses-events',
        'Message': 'Please confirm this SNS subscription.',
        'SubscribeURL': (
            'https://sns.us-east-1.amazonaws.com/'
            '?Action=ConfirmSubscription&Token=confirmation-token'
        ),
        'Timestamp': '2026-08-14T08:31:00.000Z',
        'SignatureVersion': signature_version,
        'SigningCertURL': VALID_CERT_URL,
    }


def _certificate_pem(private_key, common_name):
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def _sign_payload(payload, private_key):
    signing_string = ses._build_signing_string(payload)
    if signing_string is None:
        raise AssertionError('test payload is not signable')
    signature_hash = hashes.SHA1() if payload['SignatureVersion'] == '1' else hashes.SHA256()
    signature = private_key.sign(
        signing_string.encode('utf-8'),
        padding.PKCS1v15(),
        signature_hash,
    )
    payload['Signature'] = base64.b64encode(signature).decode('ascii')
    return payload


class SnsSignatureVerificationTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        cls.certificate_pem = _certificate_pem(cls.private_key, 'sns.amazonaws.com')
        cls.other_private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        cls.other_certificate_pem = _certificate_pem(
            cls.other_private_key,
            'sns.amazonaws.com',
        )

    def _signed(self, payload):
        return _sign_payload(payload, self.private_key)

    def _certificate_response(self, *, status_code=200, content=None):
        return SimpleNamespace(
            status_code=status_code,
            content=self.certificate_pem if content is None else content,
        )

    def test_notification_signing_string_without_subject_is_canonical(self):
        payload = _notification_payload()

        self.assertEqual(
            ses._build_signing_string(payload),
            (
                f'Message\n{payload["Message"]}\n'
                f'MessageId\n{payload["MessageId"]}\n'
                f'Timestamp\n{payload["Timestamp"]}\n'
                f'TopicArn\n{payload["TopicArn"]}\n'
                'Type\nNotification\n'
            ),
        )

    def test_notification_signing_string_places_optional_subject_after_message_id(self):
        payload = _notification_payload(subject='Amazon SES event')

        self.assertEqual(
            ses._build_signing_string(payload),
            (
                f'Message\n{payload["Message"]}\n'
                f'MessageId\n{payload["MessageId"]}\n'
                'Subject\nAmazon SES event\n'
                f'Timestamp\n{payload["Timestamp"]}\n'
                f'TopicArn\n{payload["TopicArn"]}\n'
                'Type\nNotification\n'
            ),
        )

    def test_subscription_confirmation_signing_string_is_canonical(self):
        payload = _confirmation_payload()

        self.assertEqual(
            ses._build_signing_string(payload),
            (
                f'Message\n{payload["Message"]}\n'
                f'MessageId\n{payload["MessageId"]}\n'
                f'SubscribeURL\n{payload["SubscribeURL"]}\n'
                f'Timestamp\n{payload["Timestamp"]}\n'
                f'Token\n{payload["Token"]}\n'
                f'TopicArn\n{payload["TopicArn"]}\n'
                'Type\nSubscriptionConfirmation\n'
            ),
        )

    def test_unknown_type_and_missing_or_non_string_signing_fields_are_rejected(self):
        unknown = _notification_payload()
        unknown['Type'] = 'Unknown'
        self.assertIsNone(ses._build_signing_string(unknown))

        for field in ('Message', 'MessageId', 'Timestamp', 'TopicArn'):
            with self.subTest(field=field, mutation='missing'):
                payload = _notification_payload()
                del payload[field]
                self.assertIsNone(ses._build_signing_string(payload))
            with self.subTest(field=field, mutation='non-string'):
                payload = _notification_payload()
                payload[field] = None
                self.assertIsNone(ses._build_signing_string(payload))

        payload = _confirmation_payload()
        del payload['Token']
        self.assertIsNone(ses._build_signing_string(payload))

    def test_only_explicit_https_sns_certificate_urls_are_accepted(self):
        for host in sorted(ses.VALID_SNS_HOSTS):
            with self.subTest(host=host):
                self.assertTrue(
                    ses._is_valid_cert_url(
                        f'https://{host}/SimpleNotificationService-abcdefghij.pem',
                    ),
                )

    def test_unsafe_certificate_urls_are_rejected(self):
        for name, cert_url in UNSAFE_CERT_URLS.items():
            with self.subTest(name=name):
                self.assertTrue(ses._is_valid_cert_url(VALID_CERT_URL))
                self.assertFalse(ses._is_valid_cert_url(cert_url))

    def test_all_ascii_controls_and_whitespace_fail_before_request(self):
        signed_payload = self._signed(_notification_payload())
        ascii_controls = tuple(chr(codepoint) for codepoint in range(32)) + ('\x7f',)
        whitespace = (
            ' ',
            '\x85',
            '\xa0',
            '\u1680',
            '\u2000',
            '\u2007',
            '\u2028',
            '\u2029',
            '\u202f',
            '\u205f',
            '\u3000',
        )

        with patch.object(
            ses,
            '_ses_validation_enabled',
            return_value=True,
        ), patch.object(ses.logger, 'warning'), patch('requests.get') as get_mock:
            for character in ascii_controls + whitespace:
                variants = (
                    character + VALID_CERT_URL,
                    VALID_CERT_URL.replace(
                        'amazonaws.com/',
                        f'amazonaws.com{character}/',
                    ),
                    VALID_CERT_URL + character,
                )
                for position, cert_url in zip(
                    ('leading', 'embedded', 'trailing'),
                    variants,
                    strict=True,
                ):
                    with self.subTest(codepoint=ord(character), position=position):
                        payload = copy.deepcopy(signed_payload)
                        payload['SigningCertURL'] = cert_url
                        self.assertFalse(ses.validate_sns_notification(payload))

            get_mock.assert_not_called()

    def test_supported_types_and_signature_versions_verify_with_generated_certificate(self):
        cases = (
            _notification_payload(signature_version='1'),
            _notification_payload(subject='Amazon SES event', signature_version='2'),
            _confirmation_payload('SubscriptionConfirmation', signature_version='1'),
            _confirmation_payload('UnsubscribeConfirmation', signature_version='2'),
        )

        for payload in cases:
            with self.subTest(message_type=payload['Type'], version=payload['SignatureVersion']):
                signed_payload = self._signed(payload)
                with patch(
                    'requests.get',
                    return_value=self._certificate_response(),
                ) as get_mock:
                    self.assertTrue(
                        ses._verify_signature(signed_payload, VALID_CERT_URL),
                    )
                get_mock.assert_called_once_with(
                    VALID_CERT_URL,
                    timeout=10,
                    allow_redirects=False,
                )

    def test_enabled_validator_reaches_real_verifier(self):
        payload = self._signed(_notification_payload())

        with patch.object(ses, '_ses_validation_enabled', return_value=True), patch(
            'requests.get',
            return_value=self._certificate_response(),
        ) as get_mock:
            self.assertTrue(ses.validate_sns_notification(payload))

        get_mock.assert_called_once_with(
            VALID_CERT_URL,
            timeout=10,
            allow_redirects=False,
        )

    def test_disabled_validator_preserves_explicit_bypass_without_network(self):
        with patch.object(ses, '_ses_validation_enabled', return_value=False), patch(
            'requests.get',
        ) as get_mock:
            self.assertTrue(ses.validate_sns_notification({'not': 'an SNS payload'}))

        get_mock.assert_not_called()

    def test_invalid_urls_fail_before_certificate_request(self):
        signed_payload = self._signed(_notification_payload())

        for name, cert_url in UNSAFE_CERT_URLS.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(signed_payload)
                payload['SigningCertURL'] = cert_url
                with patch.object(
                    ses,
                    '_ses_validation_enabled',
                    return_value=True,
                ), patch('requests.get') as get_mock:
                    self.assertFalse(ses.validate_sns_notification(payload))

                get_mock.assert_not_called()

    def test_mutating_any_signed_notification_field_or_signature_fails(self):
        signed_payload = self._signed(
            _notification_payload(subject='Amazon SES event'),
        )
        mutations = {
            'Message': 'forged message',
            'MessageId': 'forged-message-id',
            'Subject': 'forged subject',
            'Timestamp': '2026-08-14T09:00:00.000Z',
            'TopicArn': 'arn:aws:sns:us-east-1:123456789012:forged-topic',
            'Type': 'Unknown',
            'Signature': base64.b64encode(b'forged signature').decode('ascii'),
        }

        for field, value in mutations.items():
            with self.subTest(field=field):
                payload = copy.deepcopy(signed_payload)
                payload[field] = value
                with patch(
                    'requests.get',
                    return_value=self._certificate_response(),
                ):
                    self.assertFalse(
                        ses._verify_signature(payload, VALID_CERT_URL),
                    )

    def test_missing_or_invalid_signature_material_fails_before_download(self):
        cases = []
        for field in ('Signature', 'SignatureVersion'):
            payload = self._signed(_notification_payload())
            del payload[field]
            cases.append((f'missing-{field}', payload))

        unsupported_version = self._signed(_notification_payload())
        unsupported_version['SignatureVersion'] = '3'
        cases.append(('unsupported-version', unsupported_version))

        invalid_base64 = self._signed(_notification_payload())
        invalid_base64['Signature'] = 'not valid base64!'
        cases.append(('invalid-base64', invalid_base64))

        non_string_signature = self._signed(_notification_payload())
        non_string_signature['Signature'] = b'bytes-are-not-json-signature-material'
        cases.append(('non-string-signature', non_string_signature))

        for name, payload in cases:
            with self.subTest(name=name), patch('requests.get') as get_mock:
                self.assertFalse(
                    ses._verify_signature(payload, VALID_CERT_URL),
                )
                get_mock.assert_not_called()

    def test_certificate_download_non_200_and_redirect_fail_closed(self):
        payload = self._signed(_notification_payload())

        for status_code in (302, 404, 503):
            with self.subTest(status_code=status_code), patch(
                'requests.get',
                return_value=self._certificate_response(status_code=status_code),
            ) as get_mock:
                self.assertFalse(
                    ses._verify_signature(payload, VALID_CERT_URL),
                )
                get_mock.assert_called_once_with(
                    VALID_CERT_URL,
                    timeout=10,
                    allow_redirects=False,
                )

    def test_certificate_download_timeout_and_network_error_fail_closed(self):
        payload = self._signed(_notification_payload())

        for error in (requests.Timeout('timed out'), requests.ConnectionError('offline')):
            with self.subTest(error=type(error).__name__), patch(
                'requests.get',
                side_effect=error,
            ):
                self.assertFalse(
                    ses._verify_signature(payload, VALID_CERT_URL),
                )

    def test_invalid_pem_and_wrong_public_key_fail_closed(self):
        payload = self._signed(_notification_payload())

        for name, certificate_content in (
            ('invalid-pem', b'not a certificate'),
            ('wrong-key', self.other_certificate_pem),
        ):
            with self.subTest(name=name), patch(
                'requests.get',
                return_value=self._certificate_response(content=certificate_content),
            ):
                self.assertFalse(
                    ses._verify_signature(payload, VALID_CERT_URL),
                )

    def test_forged_webhook_payload_has_no_ses_event_side_effect(self):
        payload = self._signed(_notification_payload())
        payload['Message'] = json.dumps(
            {
                'notificationType': 'Bounce',
                'mail': {'destination': ['victim@example.com']},
                'bounce': {
                    'bounceType': 'Permanent',
                    'bounceSubType': 'General',
                    'bouncedRecipients': [{'emailAddress': 'victim@example.com'}],
                },
            },
        )
        events_before = SesEvent.objects.count()

        with patch.object(ses, '_ses_validation_enabled', return_value=True), patch(
            'requests.get',
            return_value=self._certificate_response(),
        ):
            response = self.client.post(
                '/api/ses-events',
                data=json.dumps(payload),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(SesEvent.objects.count(), events_before)
