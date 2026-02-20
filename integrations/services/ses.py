"""Amazon SES / SNS notification validation service.

Validates incoming SNS notifications by checking the message
signature against the SNS signing certificate.

For SES bounce/complaint notifications delivered via SNS.
"""

import base64
import logging
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

# Valid SNS certificate URL hostnames
VALID_SNS_HOSTS = {
    'sns.us-east-1.amazonaws.com',
    'sns.us-west-2.amazonaws.com',
    'sns.eu-west-1.amazonaws.com',
    'sns.eu-central-1.amazonaws.com',
    'sns.ap-southeast-1.amazonaws.com',
    'sns.ap-northeast-1.amazonaws.com',
}


def validate_sns_notification(payload):
    """Validate an incoming SNS notification.

    Performs the following checks:
    1. Verifies the SigningCertURL is from a valid AWS domain
    2. Downloads the certificate and verifies the message signature

    In development/testing mode (settings.DEBUG=True or when
    SES_WEBHOOK_VALIDATION_ENABLED is False), validation is skipped.

    Args:
        payload: Parsed JSON dict of the SNS notification.

    Returns:
        bool: True if the notification is valid, False otherwise.
    """
    # Skip validation in development/testing when disabled
    validation_enabled = getattr(
        settings, 'SES_WEBHOOK_VALIDATION_ENABLED', not settings.DEBUG,
    )
    if not validation_enabled:
        return True

    # Check that the SigningCertURL is from an AWS domain
    cert_url = payload.get('SigningCertURL', '')
    if not _is_valid_cert_url(cert_url):
        logger.warning(
            'Invalid SNS SigningCertURL: %s', cert_url,
        )
        return False

    # Verify the message signature
    return _verify_signature(payload, cert_url)


def _is_valid_cert_url(cert_url):
    """Check that a certificate URL is from a valid AWS SNS domain.

    Args:
        cert_url: URL string to validate.

    Returns:
        bool: True if the URL is from a valid AWS domain.
    """
    if not cert_url:
        return False

    parsed = urlparse(cert_url)

    # Must be HTTPS
    if parsed.scheme != 'https':
        return False

    # Must be from an AWS SNS domain
    if parsed.hostname not in VALID_SNS_HOSTS:
        return False

    return True


def _verify_signature(payload, cert_url):
    """Verify the SNS message signature using the signing certificate.

    Downloads the X.509 certificate from the SigningCertURL and
    verifies the signature against the constructed message string.

    Args:
        payload: Parsed JSON dict of the SNS notification.
        cert_url: URL of the signing certificate.

    Returns:
        bool: True if the signature is valid.
    """
    try:
        import requests
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.x509 import load_pem_x509_certificate

        # Download the certificate
        response = requests.get(cert_url, timeout=10)
        if response.status_code != 200:
            logger.warning(
                'Failed to download SNS certificate from %s: %s',
                cert_url, response.status_code,
            )
            return False

        # Load the certificate
        cert = load_pem_x509_certificate(response.content)
        public_key = cert.public_key()

        # Build the message string to verify
        message_string = _build_signing_string(payload)
        if message_string is None:
            return False

        # Decode the signature
        signature = base64.b64decode(payload.get('Signature', ''))

        # Verify
        public_key.verify(
            signature,
            message_string.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
        return True

    except Exception:
        logger.exception('SNS signature verification failed')
        return False


def _build_signing_string(payload):
    """Build the canonical string used for SNS signature verification.

    The signing string depends on the message type:
    - Notification: Message, MessageId, Subject (if present),
      Timestamp, TopicArn, Type
    - SubscriptionConfirmation / UnsubscribeConfirmation:
      Message, MessageId, SubscribeURL, Timestamp, Token, TopicArn, Type

    Args:
        payload: Parsed JSON dict of the SNS notification.

    Returns:
        str: The canonical signing string, or None if type unknown.
    """
    message_type = payload.get('Type', '')

    if message_type == 'Notification':
        fields = ['Message', 'MessageId']
        if 'Subject' in payload:
            fields.append('Subject')
        fields.extend(['Timestamp', 'TopicArn', 'Type'])
    elif message_type in ('SubscriptionConfirmation', 'UnsubscribeConfirmation'):
        fields = [
            'Message', 'MessageId', 'SubscribeURL',
            'Timestamp', 'Token', 'TopicArn', 'Type',
        ]
    else:
        logger.warning('Unknown SNS message type: %s', message_type)
        return None

    parts = []
    for field in fields:
        parts.append(field)
        parts.append(payload.get(field, ''))

    return '\n'.join(parts) + '\n'
