"""Dev-suite sitemap.xml smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def test_sitemap_xml_is_served(django_server, page):
    """/sitemap.xml is served with at least one <url> entry."""
    response = page.goto(
        f"{django_server}/sitemap.xml", wait_until="domcontentloaded"
    )
    assert response.status == 200, f"/sitemap.xml returned {response.status}"
    body = page.content()
    # The sitemap should contain at least one <url> or <loc> entry. We
    # check the rendered text to avoid coupling to specific URLs which
    # may vary between environments.
    assert "<url>" in body or "<loc>" in body, (
        "Sitemap does not contain any <url>/<loc> entries"
    )
