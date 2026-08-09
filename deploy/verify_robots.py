"""Fail-closed validation for the deployed production robots contract."""

import argparse
import sys
import urllib.error
import urllib.request
from collections import Counter


class RobotsVerificationError(ValueError):
    """The robots response was unavailable or violated its contract."""


def validate_robots(body, content_type, expected_sitemap):
    """Validate and return directive counts for the production response."""
    media_type = str(content_type or '').split(';', 1)[0].strip().lower()
    if media_type != 'text/plain':
        raise RobotsVerificationError('response is not plain text')

    try:
        text = body.decode('utf-8')
    except (AttributeError, UnicodeDecodeError) as exc:
        raise RobotsVerificationError('response is not valid UTF-8') from exc

    parsed = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ':' not in line:
            raise RobotsVerificationError('malformed robots directive')
        name, value = line.split(':', 1)
        parsed.append((name.strip().lower(), value.strip()))

    names = Counter(name for name, _value in parsed)
    if names['disallow']:
        raise RobotsVerificationError('Disallow directive present')
    if names['user-agent'] != 1 or [
        value for name, value in parsed if name == 'user-agent'
    ] != ['*']:
        raise RobotsVerificationError('expected exactly one User-agent: *')
    if names['allow'] != 1 or [
        value for name, value in parsed if name == 'allow'
    ] != ['/']:
        raise RobotsVerificationError('expected exactly one Allow: /')
    if names['sitemap'] != 1:
        raise RobotsVerificationError('expected exactly one Sitemap directive')
    if [value for name, value in parsed if name == 'sitemap'] != [
        expected_sitemap
    ]:
        raise RobotsVerificationError('unexpected Sitemap target')

    expected_names = {'user-agent', 'allow', 'sitemap'}
    if set(names) != expected_names:
        raise RobotsVerificationError('unexpected robots directive')
    return names


def verify_robots_url(url, expected_sitemap, timeout=20):
    """Fetch and validate robots without exposing its response body."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            status = getattr(response, 'status', None)
            if status != 200:
                raise RobotsVerificationError('robots response was not 200')
            content_type = response.headers.get('Content-Type', '')
            body = response.read()
    except RobotsVerificationError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RobotsVerificationError('robots response unavailable') from exc
    return validate_robots(body, content_type, expected_sitemap)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('expected_sitemap')
    args = parser.parse_args(argv)

    try:
        validate_result = verify_robots_url(args.url, args.expected_sitemap)
    except RobotsVerificationError as exc:
        print(f'::error ::Robots verification failed: {exc}', file=sys.stderr)
        return 1

    print(
        'Robots verified: '
        f'allow={validate_result["allow"]} '
        f'sitemap={validate_result["sitemap"]}',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
