"""Dev-suite unknown-route 404 smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

from playwright_tests.conftest import goto_with_retry

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def test_unknown_route_returns_404(django_server, page):
    """An unknown URL returns 404 (confirms the error handler is wired)."""
    response = goto_with_retry(
        page,
        f"{django_server}/this-path-does-not-exist-issue-656",
        expected_status=404,
    )
    assert response.status == 404, (
        f"Expected 404 for unknown route, got {response.status}"
    )
