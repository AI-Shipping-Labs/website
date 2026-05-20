"""Dev-suite homepage smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def test_homepage_renders_title_and_nav(django_server, page):
    """Homepage 200s, renders the canonical title and a navigable header."""
    response = page.goto(f"{django_server}/", wait_until="domcontentloaded")
    assert response.status == 200, f"Homepage returned {response.status}"
    assert "AI Shipping Labs" in page.title()
    assert page.locator("header nav a").count() >= 1
