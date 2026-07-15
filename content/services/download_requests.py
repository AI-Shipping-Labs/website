"""Privacy-safe email request flow for downloadable resources."""

import datetime
import hashlib
import logging
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounts.services.verification import resolve_unverified_ttl_days
from accounts.utils.tokens import generate_user_action_token
from content.services.download_delivery import (
    create_delivery_grant,
    get_delivery_token_ttl_hours,
)
from email_app.services.email_service import EmailService
from integrations.config import site_base_url
from website.request_ip import client_ip_from_request

logger = logging.getLogger(__name__)
User = get_user_model()

REQUEST_IP_LIMIT = 8
REQUEST_EMAIL_LIMIT = 4
REQUEST_DOWNLOAD_LIMIT = 30
REQUEST_WINDOW_SECONDS = 3600
GENERIC_REQUEST_MESSAGE = (
    'If this address can receive the resource, we sent a secure link. '
    'Please check your email.'
)


def normalize_download_email(value):
    email = User.objects.normalize_email(str(value or '').strip()).lower()
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValueError('Enter a valid email address.') from exc
    return email


def _counter_exceeded(key, limit):
    if cache.add(key, 1, REQUEST_WINDOW_SECONDS):
        return False
    try:
        count = cache.incr(key)
    except ValueError:
        cache.add(key, 1, REQUEST_WINDOW_SECONDS)
        count = 1
    return count > limit


def consume_download_request_rate_limit(request, email, slug):
    """Rate limit IP, hashed email, and resource without storing raw email."""
    email_digest = hashlib.sha256(email.encode()).hexdigest()
    ip_digest = hashlib.sha256(
        client_ip_from_request(request).encode(),
    ).hexdigest()
    checks = (
        (f'download-request:ip:{ip_digest}', REQUEST_IP_LIMIT),
        (f'download-request:email:{email_digest}', REQUEST_EMAIL_LIMIT),
        (f'download-request:slug:{slug}', REQUEST_DOWNLOAD_LIMIT),
    )
    return any(_counter_exceeded(key, limit) for key, limit in checks)


def _get_or_create_download_user(email):
    try:
        return User.objects.get(email__iexact=email), False
    except User.DoesNotExist:
        ttl_days = resolve_unverified_ttl_days()
        user = User.objects.create_user(
            email=email,
            signup_source='download',
            unsubscribed=True,
            email_preferences={'newsletter': False},
            verification_expires_at=(
                timezone.now() + datetime.timedelta(days=ttl_days)
            ),
        )
        return user, True


def send_download_request(
    user,
    download,
    *,
    newsletter_opt_in=False,
    surface='detail',
):
    """Create a grant and send either verification or direct delivery mail."""
    with transaction.atomic():
        expires_hours = get_delivery_token_ttl_hours()
        grant_token = create_delivery_grant(
            user,
            download,
            newsletter_opt_in=newsletter_opt_in,
            surface=surface,
        )
        internal_path = (
            f'/api/downloads/{download.slug}/file?grant={quote(grant_token)}'
        )
        if user.email_verified:
            delivery_url = f'{site_base_url()}{internal_path}'
        else:
            verify_token = generate_user_action_token(
                user.pk,
                'verify_email',
                expiry_hours=expires_hours,
                return_path=internal_path,
            )
            delivery_url = f'{site_base_url()}/api/verify-email?token={verify_token}'

        try:
            EmailService().send(
                user,
                'download_delivery',
                {
                    'resource_title': download.title,
                    'delivery_url': delivery_url,
                    'verification_required': not user.email_verified,
                    'newsletter_opt_in': bool(newsletter_opt_in),
                    'expires_hours': expires_hours,
                    'site_url': site_base_url(),
                },
            )
        except Exception:
            raise
    return grant_token


def request_download_for_email(
    email,
    download,
    *,
    newsletter_opt_in=False,
    surface='detail',
):
    # Roll a new capture back with its grant when transactional delivery
    # fails, so retries never leave an unreachable passwordless account.
    with transaction.atomic():
        user, _created = _get_or_create_download_user(email)
        send_download_request(
            user,
            download,
            newsletter_opt_in=newsletter_opt_in,
            surface=surface,
        )
    return user
