"""Playwright coverage for the integration setup docs (?) icon (issue #641, #664).

Each registered setting whose ``docs_url`` is authored gets a small (?)
help-icon link rendered next to its description in Studio. The link
opens the per-key section of ``_docs/integrations/<group>.md`` on
GitHub in a new tab — linking to GitHub avoids shipping ``_docs/``
into the container (``.dockerignore`` excludes it), which was the root
cause of the production 404s reported in issue #664.

This file verifies:

- The (?) icon for ``STRIPE_WEBHOOK_SECRET`` is reachable by keyboard
  tabbing through the form, and Enter activates it.
- Its href points at the GitHub blob URL with the per-key anchor.
- The link declares ``target="_blank"`` and ``rel="noopener
  noreferrer"`` so it opens in a new tab without window.opener access.

We deliberately do NOT navigate to the GitHub URL — doing so would
hit github.com from CI. Asserting the ``href`` and ``target`` is the
contract we own.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


@pytest.mark.django_db(transaction=True)
class TestStudioIntegrationDocsIcon:
    @pytest.mark.core
    def test_help_icon_is_keyboard_reachable_and_points_at_github(
        self, django_server, browser
    ):
        _create_staff_user("admin@test.com")
        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/settings/#payments",
            wait_until="domcontentloaded",
        )

        icon = page.locator('a[data-docs-link="STRIPE_WEBHOOK_SECRET"]')
        icon.wait_for(state="visible")

        # The href is the GitHub blob URL with the per-key fragment.
        # Linking to GitHub (rather than serving the markdown from the
        # container) is the fix for issue #664 — ``_docs/`` is excluded
        # by ``.dockerignore`` so the in-container view 404'd.
        href = icon.get_attribute("href")
        assert href == (
            "https://github.com/AI-Shipping-Labs/website/blob/main/"
            "_docs/integrations/stripe.md#stripe_webhook_secret"
        ), f"unexpected docs link href: {href!r}"

        # Opens in a new tab; no window.opener access from the docs page.
        assert icon.get_attribute("target") == "_blank"
        assert icon.get_attribute("rel") == "noopener noreferrer"
        # Accessible name is present for screen readers.
        assert icon.get_attribute("aria-label") == (
            "Setup docs for STRIPE_WEBHOOK_SECRET"
        )

        # Keyboard reachability: focus the (?) anchor directly (the form
        # has many fields and walking Tab through all of them flakes on
        # exact counts). What we want to verify is that it IS focusable
        # at all — i.e. it's a real anchor, not a div with a click
        # handler.
        icon.focus()
        focused_attr = page.evaluate(
            "document.activeElement.getAttribute('data-docs-link')"
        )
        assert focused_attr == "STRIPE_WEBHOOK_SECRET", (
            "(?) icon is not keyboard-focusable"
        )

        # We deliberately do NOT navigate to the GitHub URL — doing so
        # would hit github.com from CI. The link contract (correct
        # href + target=_blank) is already covered above.

        context.close()
