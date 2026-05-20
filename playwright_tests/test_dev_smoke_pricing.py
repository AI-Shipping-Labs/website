"""Dev-suite pricing-page smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def test_pricing_page_renders_tier_grid(django_server, page):
    """/pricing renders the tier comparison grid with all four tier cards."""
    response = page.goto(
        f"{django_server}/pricing", wait_until="domcontentloaded"
    )
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
