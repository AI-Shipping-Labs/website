"""Dev-suite sitemap.xml smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.

Local-mode hardening (Issue #786): defensive autouse seed for the
``Tier`` table so that this smoke test cannot fail if a prior
``transaction=True`` test in the same shard truncated the table. The
fixture is a no-op when ``PLAYWRIGHT_BASE_URL`` points at a deployed
host.
"""

import os

import pytest

from playwright_tests.conftest import (
    base_url_is_local,
    ensure_tiers,
    goto_with_retry,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture(autouse=True)
def _ensure_tiers_seeded(django_db_blocker):
    """Re-seed Tier rows on local runs (no-op on dev/prod base URLs)."""
    if not base_url_is_local():
        return
    with django_db_blocker.unblock():
        ensure_tiers()


def test_sitemap_xml_is_served(django_server, page):
    """/sitemap.xml is served with at least one <url> entry."""
    response = goto_with_retry(page, f"{django_server}/sitemap.xml")
    assert response.status == 200, f"/sitemap.xml returned {response.status}"
    body = page.content()
    # The sitemap should contain at least one <url> or <loc> entry. We
    # check the rendered text to avoid coupling to specific URLs which
    # may vary between environments.
    assert "<url>" in body or "<loc>" in body, (
        "Sitemap does not contain any <url>/<loc> entries"
    )
