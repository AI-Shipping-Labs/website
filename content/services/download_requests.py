"""Privacy-safe email request flow for downloadable resources."""

import datetime
import hashlib
import logging

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounts.services.verification import resolve_unverified_ttl_days
from email_app.package_mail import send_package_mail
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
    """Queue the durable download-delivery email for one request.

    A1.2 slice 3: the send goes through the package, so no grant is minted
    here any more — the worker resolver creates a fresh one-time grant and
    the delivery or verification link at delivery time, keeping every
    bearer token out of the durable row (#1613) and starting its TTL clock
    when the mail is produced rather than when the request arrived. The
    stored context carries scalars only; the ``Download`` rides along as
    the delivery's ``related`` relation. Returns the durable
    ``EmailDelivery`` — a transport failure never raises here, the worker
    retries it.
    """
    with transaction.atomic():
        return send_package_mail(
            user,
            'download_delivery',
            {
                'resource_title': download.title,
                'newsletter_opt_in': bool(newsletter_opt_in),
                'surface': surface,
            },
            related=download,
        )


def request_download_for_email(
    email,
    download,
    *,
    newsletter_opt_in=False,
    surface='detail',
):
    # Roll a new capture back when the durable send is refused (a guard or
    # configuration failure would never deliver), so retries never leave an
    # unreachable passwordless account. Transport trouble no longer raises:
    # the delivery is durable and the worker retries it, so the account
    # stays reachable.
    with transaction.atomic():
        user, _created = _get_or_create_download_user(email)
        send_download_request(
            user,
            download,
            newsletter_opt_in=newsletter_opt_in,
            surface=surface,
        )
    return user
