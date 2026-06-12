"""Dev-suite pricing-page smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.

Local-mode hardening (Issue #786): when the suite runs against the
in-process Django test DB, an earlier ``@pytest.mark.django_db(
transaction=True)`` test can truncate the ``Tier`` table between
fixtures. The ``_ensure_tiers_seeded`` fixture re-seeds the four
bootstrap tiers before this test runs so ``/pricing`` always finds
data. The fixture is a no-op when ``PLAYWRIGHT_BASE_URL`` points at a
deployed host — the dev/prod databases must not be written to from
the Playwright runner.
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


@pytest.mark.django_db
def test_pricing_page_renders_tier_grid(django_server, page):
    """/pricing renders the tier comparison grid with all four tier cards."""
    response = goto_with_retry(page, f"{django_server}/pricing")
    assert response.status == 200, f"/pricing returned {response.status}"
    # The pricing template renders one ``[data-tier-card]`` per tier.
    expected_tiers = {"free", "basic", "main", "premium"}
    found_tiers = set()
    for slug in expected_tiers:
        if page.locator(f'[data-tier-card="{slug}"]').count() >= 1:
            found_tiers.add(slug)
    assert found_tiers == expected_tiers, (
        f"Expected tier cards {sorted(expected_tiers)}, "
        f"found {sorted(found_tiers)}"
    )
