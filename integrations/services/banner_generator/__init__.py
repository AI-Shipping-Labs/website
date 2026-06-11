"""Banner-generator Lambda client (issue #788).

Thin HTTP wrapper around the deployed ``banner-generator`` Lambda's
S3-output mode. Used by the content-sync auto-banner pipeline and the
Studio "Regenerate banner" buttons to render OG-card JPEGs for synced
content into the existing content CDN bucket.

This module exposes the synchronous building blocks (``is_enabled``,
``render_to_s3``, ``BannerGeneratorError``); the async enqueue +
post-render persistence live in :mod:`integrations.services.banner_generator.dispatch`
and :mod:`integrations.services.banner_generator.tasks`.

The bearer token is read via :func:`integrations.config.get_config` on
every call (no in-process cache here) so worker processes pick up
Studio-saved values without a restart. The token is never returned to
callers and never included in raised exception messages — see
``_BannerGeneratorError_safe_str``.
"""

import logging

import requests

from integrations.config import get_config

logger = logging.getLogger(__name__)

# Default HTTP timeout (seconds) for the render call. Resolved at call
# time via ``get_config('BANNER_GENERATOR_TIMEOUT_SECONDS', ...)`` so an
# operator can raise it from Studio without a redeploy. The default is
# deliberately high (90s) to comfortably cover a container-Lambda cold
# start — a warm render returns in ~1.4s, so the higher ceiling costs
# nothing on the happy path (issue #900).
DEFAULT_REQUEST_TIMEOUT_SECONDS = 90
DEFAULT_TEMPLATE = 'asl-content-card'
DEFAULT_SIZE = 'og'
DEFAULT_FORMAT = 'jpeg'
DEFAULT_CONTENT_TYPE = 'image/jpeg'


class BannerGeneratorError(Exception):
    """Raised when the banner-generator Lambda call fails.

    Failure modes covered: HTTP non-2xx, network/timeout exceptions,
    missing configuration, and 2xx responses whose JSON body reports
    ``ok=False``. The exception string never includes the bearer token
    or the request payload — see the constructor.
    """

    def __init__(self, message, *, status_code=None, is_timeout=False):
        # Coerce to str up front so callers passing tuples / dicts can't
        # smuggle a token into ``args``. The status code is helpful for
        # callers that want to branch on 4xx vs 5xx but is otherwise an
        # opaque integer. ``is_timeout`` is set only when the underlying
        # failure was a ``requests.Timeout`` (cold-start symptom) so the
        # render task can retry exactly that class and nothing else
        # (issue #900).
        self.status_code = status_code
        self.is_timeout = is_timeout
        super().__init__(str(message))


def _resolve_timeout_seconds():
    """Return the configured render HTTP timeout (seconds) as a positive int.

    Reads ``BANNER_GENERATOR_TIMEOUT_SECONDS`` via :func:`get_config` so a
    DB override (set in Studio) wins over env / default. The value may
    arrive as a string (env / DB overrides are stored as text), so we
    coerce defensively and fall back to ``DEFAULT_REQUEST_TIMEOUT_SECONDS``
    when it is unparseable or non-positive.
    """
    raw = get_config(
        'BANNER_GENERATOR_TIMEOUT_SECONDS', DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    return value


def is_enabled():
    """Return True iff both the URL and the bearer token are configured.

    Reads through :func:`get_config` so the lookup respects DB overrides
    first, then env, then Django settings. Returns False on either
    missing value — callers (the dispatcher hot path) treat that as
    "auto-banner generation is silently disabled".
    """
    url = (get_config('BANNER_GENERATOR_FUNCTION_URL', '') or '').strip()
    token = (get_config('BANNER_GENERATOR_AUTH_TOKEN', '') or '').strip()
    return bool(url) and bool(token)


def render_to_s3(
    *,
    template,
    size,
    fmt,
    data,
    s3_key,
    content_type=DEFAULT_CONTENT_TYPE,
    timeout=None,
):
    """POST a render request to the Lambda and return the parsed JSON response.

    Args:
        template: Lambda template name (e.g. ``asl-content-card``).
        size: Preset render size (e.g. ``og`` for 1200x630 OG cards).
        fmt: Output image format (``jpeg`` for generated auto-banners).
        data: Dict of template field values (kind, title, kicker, etc.).
        s3_key: Object key under ``AWS_S3_CONTENT_BUCKET`` to upload to.
        content_type: Content-Type to set on the S3 PUT (default JPEG).
        timeout: HTTP timeout in seconds. When ``None`` (the default), it
            resolves from ``get_config('BANNER_GENERATOR_TIMEOUT_SECONDS',
            90)`` at call time so a Studio override applies without a
            restart. Callers may still pass an explicit value to override.

    Returns:
        dict: Parsed JSON body of the Lambda response on success.

    Raises:
        BannerGeneratorError: On missing configuration, non-2xx HTTP
            response, network/timeout error, or a 2xx response whose JSON
            body reports ``ok=False``. The bearer token never appears in
            the exception message.
    """
    url = (get_config('BANNER_GENERATOR_FUNCTION_URL', '') or '').strip()
    token = (get_config('BANNER_GENERATOR_AUTH_TOKEN', '') or '').strip()
    bucket = (get_config('AWS_S3_CONTENT_BUCKET', '') or '').strip()

    if not url or not token:
        raise BannerGeneratorError(
            'banner-generator is not configured (URL or token missing)',
        )
    if not bucket:
        raise BannerGeneratorError(
            'banner-generator: AWS_S3_CONTENT_BUCKET is not configured',
        )

    if timeout is None:
        timeout = _resolve_timeout_seconds()

    payload = {
        'template': template,
        'format': fmt,
        'size': size,
        'data': dict(data),
        's3': {
            'bucket': bucket,
            'key': s3_key,
            'content_type': content_type,
        },
    }

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    try:
        response = requests.post(
            url, json=payload, headers=headers, timeout=timeout,
        )
    except requests.RequestException as exc:
        # ``str(exc)`` from requests typically contains the URL but not
        # any request body or headers, so the token cannot leak through
        # the chained __cause__. Wrap it as a flat string so callers
        # logging ``str(err)`` see a stable message. Flag timeouts
        # (``requests.Timeout`` covers ConnectTimeout/ReadTimeout) so the
        # render task can retry exactly the cold-start class and nothing
        # else (issue #900).
        raise BannerGeneratorError(
            f'banner-generator request failed: {type(exc).__name__}',
            is_timeout=isinstance(exc, requests.Timeout),
        ) from None

    if response.status_code < 200 or response.status_code >= 300:
        raise BannerGeneratorError(
            f'banner-generator returned HTTP {response.status_code}',
            status_code=response.status_code,
        )

    try:
        body = response.json()
    except ValueError:
        raise BannerGeneratorError(
            f'banner-generator returned non-JSON body '
            f'(HTTP {response.status_code})',
            status_code=response.status_code,
        ) from None

    if not isinstance(body, dict) or body.get('ok') is not True:
        # Surface the Lambda's own ``error`` string when present, but
        # never echo back the request payload (which contains our data
        # but not the token — token is header-only). The token-safety
        # invariant is asserted in tests.
        err_msg = ''
        if isinstance(body, dict):
            err_msg = str(body.get('error') or '')
        raise BannerGeneratorError(
            'banner-generator reported failure'
            + (f': {err_msg}' if err_msg else ''),
            status_code=response.status_code,
        )

    return body
