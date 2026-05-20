"""Dev-suite register-form smoke check (Issue #656).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.
"""

import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def test_register_page_renders_form(django_server, page):
    """/accounts/register/ renders the email + password registration form.

    The ``/accounts/signup/`` URL is the allauth default and 302-redirects
    to our canonical ``/accounts/register/`` route. We follow the redirect
    and assert on the final form rather than depending on whichever URL
    the dev environment exposes.
    """
    response = page.goto(
        f"{django_server}/accounts/register/", wait_until="domcontentloaded"
    )
    assert response.status == 200, (
        f"/accounts/register/ returned {response.status}"
    )
    # The register form has its own password fields rendered server-side.
    assert page.locator('input[type="password"]').count() >= 1
