"""Banner Generator adapter for Buildcamp PDF certificates (intake 32366cde)."""

from __future__ import annotations

import base64
import logging

import requests
from community_base.coursework.attempt_certificates import PrivatePreviewArtifact
from django.conf import settings
from django.utils import timezone

from integrations.config import get_config
from integrations.services.banner_generator import (
    BannerGeneratorError,
    _http_failure_message,
    _resolve_timeout_seconds,
    render_to_s3,
)
from integrations.services.banner_generator.tasks import cdn_url_for_key

logger = logging.getLogger(__name__)

CERTIFICATE_TEMPLATE = 'ai-buildcamp-certificate'
CERTIFICATE_SIZE = 'certificate'
CERTIFICATE_FORMAT = 'pdf'
CERTIFICATE_CONTENT_TYPE = 'application/pdf'


def certificate_s3_key(course_slug, certificate_id):
    """Return the S3 key under ``banners/`` where Lambda has PutObject access."""
    slug = str(course_slug or 'course').strip().strip('/')
    return f'banners/certificates/{slug}/{certificate_id}.pdf'


def _site_base_url():
    configured = getattr(settings, 'COMMUNITY_BASE', {}).get('SITE_URL', '')
    return str(configured or 'https://aishippinglabs.com').rstrip('/')


def _course_public_url(course):
    enroll_url = str(getattr(course, 'enroll_url', '') or '').strip()
    if enroll_url:
        return enroll_url
    return f'{_site_base_url()}/courses/{course.slug}'


def _format_certificate_date(when):
    dt = when
    if dt is None:
        dt = timezone.now()
    return f'{dt.day} {dt.strftime("%B %Y")}'


def build_certificate_payload(
    *,
    recipient_name,
    course,
    certificate_id,
    certificate_url,
    issued_at=None,
    qr=None,
):
    """Build the Banner Generator data payload for ``ai-buildcamp-certificate``."""
    payload = {
        'name': str(recipient_name or '').strip() or 'Graduate',
        'course_name': str(course.title or '').strip(),
        'dates': _format_certificate_date(issued_at),
        'course_url': _course_public_url(course),
        'certificate_id': str(certificate_id),
        'certificate_url': str(certificate_url),
    }
    if qr:
        payload['qr'] = str(qr)
    return payload


def _recipient_name(user):
    full_name = str(user.get_full_name() or '').strip()
    if full_name:
        return full_name
    return str(user.email or '').strip()


def _render_to_s3_with_timeout_retry(*, data, s3_key):
    def _call():
        return render_to_s3(
            template=CERTIFICATE_TEMPLATE,
            size=CERTIFICATE_SIZE,
            fmt=CERTIFICATE_FORMAT,
            data=data,
            s3_key=s3_key,
            content_type=CERTIFICATE_CONTENT_TYPE,
        )

    try:
        return _call()
    except BannerGeneratorError as exc:
        if not getattr(exc, 'is_timeout', False):
            raise
        logger.warning(
            'certificate render timed out for %s; retrying once', s3_key,
        )
        return _call()


def render_certificate_pdf_to_s3(certificate, *, course):
    """Render one certificate PDF via Banner Generator into S3 and return its CDN URL."""
    s3_key = certificate_s3_key(course.slug, certificate.id)
    cert_url = f'{_site_base_url()}{certificate.get_absolute_url()}'
    data = build_certificate_payload(
        recipient_name=_recipient_name(certificate.user),
        course=course,
        certificate_id=certificate.id,
        certificate_url=cert_url,
        issued_at=certificate.issued_at,
    )
    _render_to_s3_with_timeout_retry(data=data, s3_key=s3_key)
    cdn_url = cdn_url_for_key(s3_key)
    if not cdn_url:
        raise BannerGeneratorError(
            'rendered certificate PDF but CONTENT_CDN_BASE is unset',
        )
    return cdn_url


def _decode_direct_pdf_response(response):
    content_type = str(response.headers.get('Content-Type') or '').lower()
    if 'application/pdf' in content_type:
        if not response.content:
            raise BannerGeneratorError('banner-generator returned empty PDF')
        return response.content
    try:
        body = response.json()
    except ValueError:
        raise BannerGeneratorError(
            f'banner-generator returned unexpected preview body (HTTP {response.status_code})',
            status_code=response.status_code,
        ) from None
    if isinstance(body, dict) and body.get('isBase64Encoded') and body.get('body'):
        return base64.b64decode(body['body'])
    err = ''
    if isinstance(body, dict):
        err = str(body.get('error') or '')
    suffix = ''
    if err:
        suffix = f': {err}'
    raise BannerGeneratorError(
        f'banner-generator preview failed{suffix}',
        status_code=response.status_code,
    )


def render_direct_pdf_bytes(*, template, size, data, timeout=None):
    """Call Banner Generator WITHOUT an ``s3`` target so no object is published."""
    url = (get_config('BANNER_GENERATOR_FUNCTION_URL', '') or '').strip()
    token = (get_config('BANNER_GENERATOR_AUTH_TOKEN', '') or '').strip()
    if not url or not token:
        raise BannerGeneratorError(
            'banner-generator is not configured (URL or token missing)',
        )
    resolved_timeout = timeout
    if resolved_timeout is None:
        resolved_timeout = _resolve_timeout_seconds()
    payload = {
        'template': template,
        'format': CERTIFICATE_FORMAT,
        'size': size,
        'data': dict(data),
    }
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    try:
        response = requests.post(
            url, json=payload, headers=headers, timeout=resolved_timeout,
        )
    except requests.RequestException as exc:
        raise BannerGeneratorError(
            f'banner-generator preview request failed: {type(exc).__name__}',
            is_timeout=isinstance(exc, requests.Timeout),
        ) from None
    if response.status_code < 200 or response.status_code >= 300:
        raise BannerGeneratorError(
            _http_failure_message(response),
            status_code=response.status_code,
        )
    return _decode_direct_pdf_response(response)


def render_certificate_preview_pdf(*, recipient_name, course, issued_at=None):
    """Render a private preview PDF without issuing a credential or writing to S3."""
    preview_url = f'{_site_base_url()}/certificates/preview'
    data = build_certificate_payload(
        recipient_name=recipient_name,
        course=course,
        certificate_id='preview',
        certificate_url=preview_url,
        issued_at=issued_at,
    )
    pdf_bytes = render_direct_pdf_bytes(
        template=CERTIFICATE_TEMPLATE,
        size=CERTIFICATE_SIZE,
        data=data,
    )
    return PrivatePreviewArtifact(
        content=pdf_bytes,
        content_type=CERTIFICATE_CONTENT_TYPE,
        filename=f'{course.slug}-certificate-preview.pdf',
    )
