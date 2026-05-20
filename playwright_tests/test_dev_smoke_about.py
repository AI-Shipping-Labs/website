"""Dev-suite about-page smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


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
