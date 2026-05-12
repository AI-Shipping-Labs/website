"""Issue #277: LinkedIn icons missing on /about.

Lucide removed brand icons (linkedin, github, etc.) around v0.475. Templates
still using `<i data-lucide="linkedin">` rendered an empty `<i>` tag with no
visible glyph. We replaced those with inline SVGs so they always render.

This regression test loads /about and asserts the LinkedIn link's first
child element is an <svg>, not an <i> placeholder. If the icon ever
silently disappears again (e.g. someone reverts the inline SVG), this
test will fail.
"""

import os

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAboutPageBrandIcons:
    """The /about page must render LinkedIn icons for both founder cards."""

    def test_linkedin_icons_render_as_inline_svg(
        self, django_server, page
    ):
        page.goto(f"{django_server}/about", wait_until="domcontentloaded")

        # Both LinkedIn anchors must be present.
        linkedin_links = page.locator('a[aria-label="LinkedIn"]')
        assert linkedin_links.count() == 2, (
            f"Expected 2 LinkedIn links on /about, "
            f"got {linkedin_links.count()}"
        )

        # Each LinkedIn link's first child must be an <svg> (not <i>).
        # An empty <i data-lucide="linkedin"> would mean lucide failed to
        # swap it out — which is exactly the bug we are guarding against.
        for i in range(linkedin_links.count()):
            link = linkedin_links.nth(i)
            # Wait for icons (lucide JS replaces non-brand <i> tags after
            # DOMContentLoaded — give it a beat to settle).
            page.wait_for_function(
                "el => el.querySelector('svg') !== null",
                arg=link.element_handle(),
                timeout=3000,
            )
            svg = link.locator("svg").first
            assert svg.count() == 1, (
                f"LinkedIn link {i} has no <svg> child — "
                "icon failed to render"
            )
            # The SVG must use currentColor so it inherits theme color.
            fill = svg.get_attribute("fill")
            assert fill == "currentColor", (
                f"LinkedIn SVG fill should be currentColor, got {fill!r}"
            )

            # aria-label preserved on the wrapping anchor.
            assert link.get_attribute("aria-label") == "LinkedIn"
