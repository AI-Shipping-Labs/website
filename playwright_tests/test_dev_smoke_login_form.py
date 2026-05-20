"""Dev-suite login-form smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def test_login_page_renders_form(django_server, page):
    """/accounts/login/ renders the email + password login form."""
    response = page.goto(
        f"{django_server}/accounts/login/", wait_until="domcontentloaded"
    )
    assert response.status == 200, (
        f"/accounts/login/ returned {response.status}"
    )
    # The login form posts to allauth and always renders a password field.
    assert page.locator('input[type="password"]').count() >= 1
    # A submit control (button or input) must be present.
    submit_count = (
        page.locator('button[type="submit"], input[type="submit"]').count()
    )
    assert submit_count >= 1, "Login form has no submit control"
