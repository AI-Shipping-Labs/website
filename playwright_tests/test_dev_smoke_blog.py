"""Dev-suite blog-listing smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

from playwright_tests.conftest import goto_with_retry

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def test_blog_listing_renders_heading(django_server, page):
    """/blog returns 200 and renders a main heading."""
    response = goto_with_retry(page, f"{django_server}/blog")
    assert response.status == 200, f"/blog returned {response.status}"
    assert page.locator("main h1").count() >= 1
