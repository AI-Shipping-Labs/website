"""Read-only verification for the deployed canonical-host redirect contract."""

import argparse
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT_PATH = '/'
QUERY_PATH = '/blog?utm_source=canonical-redirect-verifier'


class CanonicalRedirectVerificationError(ValueError):
    """A redirect response was unavailable or violated the contract."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        return None


def _canonical_origin(value):
    try:
        parsed = urlsplit(str(value).strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError('canonical_origin must be a clean HTTPS origin') from exc

    if (
        parsed.scheme.lower() != 'https'
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in ('', '/')
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError('canonical_origin must be a clean HTTPS origin')

    hostname = parsed.hostname.lower().rstrip('.')
    return f'https://{hostname}', hostname


def _request_once(url, timeout):
    """Return status and Location without following an HTTP redirect."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={'User-Agent': 'ai-shipping-labs-redirect-verifier/1'},
    )
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.headers.get('Location')
    except urllib.error.HTTPError as exc:
        status = exc.code
        location = exc.headers.get('Location')
        exc.close()
        return status, location
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CanonicalRedirectVerificationError(
            'redirect endpoint unavailable',
        ) from exc


def verify_canonical_redirects(canonical_origin, timeout=20, request_once=None):
    """Verify apex HTTP and ``www`` HTTP/HTTPS redirects in one hop."""
    origin, hostname = _canonical_origin(canonical_origin)
    request_once = request_once or _request_once
    observations = 0

    for path_and_query in (ROOT_PATH, QUERY_PATH):
        expected_location = f'{origin}{path_and_query}'
        source_urls = (
            f'http://{hostname}{path_and_query}',
            f'http://www.{hostname}{path_and_query}',
            f'https://www.{hostname}{path_and_query}',
        )
        for source_url in source_urls:
            status, location = request_once(source_url, timeout)
            observations += 1
            if status != 301:
                raise CanonicalRedirectVerificationError(
                    'non-canonical origin did not return HTTP 301',
                )
            if location != expected_location:
                raise CanonicalRedirectVerificationError(
                    'redirect target was not the clean canonical URL',
                )

        status, location = request_once(expected_location, timeout)
        observations += 1
        if 300 <= status < 400 or location is not None:
            raise CanonicalRedirectVerificationError(
                'canonical target requires more than one redirect hop',
            )
        if status != 200:
            raise CanonicalRedirectVerificationError(
                'canonical target did not return HTTP 200',
            )

    return observations


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('canonical_origin')
    args = parser.parse_args(argv)

    try:
        observations = verify_canonical_redirects(args.canonical_origin)
    except (CanonicalRedirectVerificationError, ValueError) as exc:
        print(
            f'::error ::Canonical redirect verification failed: {exc}',
            file=sys.stderr,
        )
        return 1

    origin, _hostname = _canonical_origin(args.canonical_origin)
    print(
        'Canonical redirects verified: '
        f'observations={observations} origin={origin}',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
