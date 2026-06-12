"""Dev-suite downloads-listing smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

from playwright_tests.conftest import goto_with_retry

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def test_downloads_listing_renders(django_server, page):
    """/downloads returns 200 and renders a main heading."""
    response = goto_with_retry(page, f"{django_server}/downloads")
    assert response.status == 200, f"/downloads returned {response.status}"
    assert page.locator("main h1").count() >= 1
