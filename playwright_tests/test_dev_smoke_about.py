"""Dev-suite about-page smoke check (Issue #656).

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

from playwright_tests.conftest import base_url_is_local, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture(autouse=True)
def _ensure_tiers_seeded(django_db_blocker):
    """Re-seed Tier rows on local runs (no-op on dev/prod base URLs)."""
    if not base_url_is_local():
        return
    with django_db_blocker.unblock():
        ensure_tiers()


def test_about_page_renders_with_founder_links(django_server, page):
    """/about returns 200 and renders the founder LinkedIn links."""
    response = page.goto(
        f"{django_server}/about", wait_until="domcontentloaded"
    )
    assert response.status == 200, f"/about returned {response.status}"
    # The founder bio cards each link to LinkedIn. Lucide dropped brand
    # icons around v0.475 (#277), so this assertion also guards against
    # the LinkedIn icon disappearing silently.
    linkedin_links = page.locator('a[aria-label="LinkedIn"]')
    assert linkedin_links.count() >= 1, (
        f"Expected at least one LinkedIn link on /about, "
        f"got {linkedin_links.count()}"
    )
