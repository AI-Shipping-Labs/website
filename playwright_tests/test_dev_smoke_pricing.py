"""Dev-suite pricing-page smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.

Local-mode hardening (Issue #786): when the suite runs against the
in-process Django test DB, an earlier ``@pytest.mark.django_db(
transaction=True)`` test can leave the ``Tier`` table empty between
fixtures. The ``_ensure_tiers_seeded`` fixture re-seeds the four
bootstrap tiers before this test runs so ``/membership`` always finds
data. The fixture is a no-op when ``PLAYWRIGHT_BASE_URL`` points at a
deployed host — the dev/prod databases must not be written to from
the Playwright runner.

Issue #1470: that re-seed only works if it COMMITS. This test therefore
runs under ``django_db(transaction=True)``, not the plain marker: the
in-process ``runserver`` thread reads through its own database
connection, so rows written inside pytest-django's rolled-back atomic
block are invisible to it and the page renders zero tier cards. The
symptom only surfaced once ``--dist loadfile`` started running this
module late in a worker's queue instead of third in alphabetical order.
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
def _ensure_tiers_seeded(request, django_db_blocker):
    """Re-seed Tier rows on local runs (no-op on dev/prod base URLs).

    The seed has to COMMIT (issue #1470). Under the plain ``django_db``
    marker pytest-django wraps the test in an atomic block that is rolled
    back, so these rows would live in an uncommitted transaction that the
    in-process ``runserver`` thread — a different database connection —
    cannot see, and ``/membership`` would render zero tier cards. Requesting
    ``transactional_db`` here (matching the test's
    ``django_db(transaction=True)`` marker) both orders this fixture after
    pytest-django's database setup and puts the write in autocommit.
    """
    if not base_url_is_local():
        return
    request.getfixturevalue("transactional_db")
    with django_db_blocker.unblock():
        ensure_tiers()


@pytest.mark.django_db(transaction=True)
def test_pricing_page_renders_tier_grid(django_server, page):
    """/membership renders the tier comparison grid with all four tier cards."""
    response = goto_with_retry(page, f"{django_server}/membership")
    assert response.status == 200, f"/membership returned {response.status}"
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
