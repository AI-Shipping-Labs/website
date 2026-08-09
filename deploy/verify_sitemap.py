"""Fail-closed validation for a deployed canonical sitemap."""

import argparse
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urlsplit


class SitemapVerificationError(ValueError):
    """The sitemap could not be fetched or violated its origin contract."""


def _origin_parts(url):
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return parsed.scheme.lower(), parsed.netloc.lower()


def _safe_origin_label(url):
    """Describe an origin without leaking URL credentials or paths."""
    parsed = urlsplit(url)
    if not parsed.scheme or parsed.hostname is None:
        return '<relative-or-malformed>'
    try:
        port = f':{parsed.port}' if parsed.port is not None else ''
    except ValueError:
        return '<malformed-origin>'
    return f'{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}'


def validate_sitemap(xml_body, expected_origin):
    """Return the entry count when every ``loc`` has the expected origin."""
    expected = _origin_parts(expected_origin)
    if expected is None:
        raise ValueError('expected_origin must contain a scheme and host')

    try:
        root = ET.fromstring(xml_body)
    except (ET.ParseError, TypeError) as exc:
        raise SitemapVerificationError('malformed XML; entries=0') from exc

    locations = [
        (node.text or '').strip()
        for node in root.findall('.//{*}loc')
    ]
    if not locations or any(not location for location in locations):
        raise SitemapVerificationError(
            f'empty or blank <loc> set; entries={len(locations)}',
        )

    unexpected = Counter(
        _safe_origin_label(location)
        for location in locations
        if _origin_parts(location) != expected
    )
    if unexpected:
        summary = ', '.join(
            f'{origin} ({count})'
            for origin, count in sorted(unexpected.items())
        )
        raise SitemapVerificationError(
            f'unexpected origins: {summary}; entries={len(locations)}',
        )
    return len(locations)


def verify_sitemap_url(url, expected_origin, timeout=20):
    """Fetch and validate a sitemap without exposing its response body."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SitemapVerificationError('sitemap unavailable; entries=0') from exc
    return validate_sitemap(body, expected_origin)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('expected_origin')
    args = parser.parse_args(argv)

    try:
        count = verify_sitemap_url(args.url, args.expected_origin)
    except SitemapVerificationError as exc:
        print(f'::error ::Sitemap verification failed: {exc}', file=sys.stderr)
        return 1

    expected_scheme, expected_netloc = _origin_parts(args.expected_origin)
    print(
        'Sitemap verified: '
        f'entries={count} origin={expected_scheme}://{expected_netloc}',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
