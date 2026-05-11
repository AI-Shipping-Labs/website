"""Playwright E2E test for Studio Users tag-pill dark-mode contrast (issue #563).

The previous bug used ``bg-accent/20 ... text-accent-foreground`` on the
Studio user-row tag pills, the active-tag filter chip, and the user-detail
tag chips. In dark mode ``--accent-foreground`` resolves to near-black
(``0 0% 4%``) and the translucent accent fill is also near-black with a
lime tint, so the tag label was effectively invisible.

The canonical bordered-accent pill per ``_docs/design-system.md`` is
``border border-accent/30 bg-accent/10 text-accent``; the FILLED accent
pill keeps ``bg-accent text-accent-foreground`` because the foreground is
intentionally paired with the full-opacity accent surface.

This module mirrors the channel-delta approach used by
``playwright_tests/test_dark_mode_contrast.py`` (issue #362):

- ``CHANNEL_DELTA_THRESHOLD = 80`` is the same value known to catch
  near-black-on-near-black regressions without being fragile.
- We assert a max per-channel RGB delta between the chip background and
  its text color, in BOTH dark and light modes (no light-mode regression).
- One guard scenario asserts the unrelated full-opacity filter chips
  (``All``, ``Paid``, ...) keep their existing ``bg-accent text-accent-foreground``
  pairing -- changing those would be a regression.

Usage:
    uv run pytest playwright_tests/test_studio_users_tag_contrast.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


CHANNEL_DELTA_THRESHOLD = 80


def _force_dark_mode(context):
    """Set localStorage['theme']='dark' before any document loads.

    The blocking script in templates/base.html reads localStorage on
    first paint and adds the 'dark' class to <html>, so this guarantees
    the page renders in dark mode from the very first frame.
    """
    context.add_init_script(
        "window.localStorage.setItem('theme', 'dark');"
    )


def _force_light_mode(context):
    """Force the light theme.

    The base template defaults to dark when no preference is set, so we
    explicitly write 'light' rather than removing the key.
    """
    context.add_init_script(
        "window.localStorage.setItem('theme', 'light');"
    )


def _parse_rgba(css_color):
    """Parse a 'rgb(r, g, b)' or 'rgba(r, g, b, a)' string to (r, g, b, a).

    Alpha defaults to 1.0 for ``rgb(...)``. Returns ints for r/g/b and a
    float for alpha.
    """
    inner = css_color.strip()
    inner = inner[inner.index("(") + 1 : inner.rindex(")")]
    parts = [p.strip() for p in inner.split(",")]
    r = int(float(parts[0]))
    g = int(float(parts[1]))
    b = int(float(parts[2]))
    a = float(parts[3]) if len(parts) >= 4 else 1.0
    return (r, g, b, a)


def _parse_rgb(css_color):
    """Parse to (r, g, b) for legacy callers. Drops alpha."""
    r, g, b, _a = _parse_rgba(css_color)
    return (r, g, b)


def _max_channel_delta(rgb_a, rgb_b):
    """Return the maximum per-channel absolute difference between two RGB tuples."""
    return max(abs(a - b) for a, b in zip(rgb_a, rgb_b, strict=True))


def _computed_bg_and_fg(page, locator):
    """Read the EFFECTIVE background and text color on the first match.

    Translucent backgrounds (``bg-accent/10`` etc.) report their raw
    ``rgba(...)`` from ``getComputedStyle`` -- a delta against the text
    color computed off that raw value is meaningless because the user
    actually sees the translucent surface composited onto its parent
    chain. We walk up the DOM and alpha-composite the chip's background
    against the first opaque ancestor background-color, returning the
    fully-resolved, visible RGB.
    """
    handle = locator.first
    handle.wait_for(state="attached", timeout=5000)
    bg = handle.evaluate(
        "el => {\n"
        "  function parse(c) {\n"
        "    const m = /rgba?\\(([^)]+)\\)/.exec(c);\n"
        "    if (!m) return null;\n"
        "    const p = m[1].split(',').map(s => parseFloat(s.trim()));\n"
        "    return { r: p[0]|0, g: p[1]|0, b: p[2]|0, a: p.length >= 4 ? p[3] : 1 };\n"
        "  }\n"
        "  let stack = [];\n"
        "  let node = el;\n"
        "  while (node) {\n"
        "    const c = parse(getComputedStyle(node).backgroundColor);\n"
        "    if (c && c.a > 0) {\n"
        "      stack.push(c);\n"
        "      if (c.a >= 0.999) break;\n"
        "    }\n"
        "    node = node.parentElement;\n"
        "  }\n"
        "  // Fall back to canvas/body default white if everything is transparent.\n"
        "  let out = { r: 255, g: 255, b: 255 };\n"
        "  // The bottom of the stack (last pushed) is the deepest opaque\n"
        "  // ancestor; composite from there UP to the chip itself.\n"
        "  for (let i = stack.length - 1; i >= 0; i--) {\n"
        "    const top = stack[i];\n"
        "    const a = top.a;\n"
        "    out = {\n"
        "      r: Math.round(top.r * a + out.r * (1 - a)),\n"
        "      g: Math.round(top.g * a + out.g * (1 - a)),\n"
        "      b: Math.round(top.b * a + out.b * (1 - a)),\n"
        "    };\n"
        "  }\n"
        "  return `rgb(${out.r}, ${out.g}, ${out.b})`;\n"
        "}"
    )
    fg = handle.evaluate(
        "el => getComputedStyle(el).color"
    )
    return bg, fg


def _set_tags(email, tags):
    from django.db import connection

    from accounts.models import User

    user = User.objects.get(email=email)
    user.tags = tags
    user.save(update_fields=["tags"])
    connection.close()


def _user_pk(email):
    from django.db import connection

    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


@pytest.mark.django_db(transaction=True)
class TestStudioUserTagContrast:
    """Tag pills in Studio Users must be legible in BOTH dark and light mode.

    Covers the three sites of the bug:
    - user row tag pill on /studio/users/
    - active-tag filter chip on /studio/users/?tag=<tag>
    - per-tag chip on /studio/users/<id>/
    """

    def test_tag_pills_legible_in_dark_and_light_modes(
        self, django_server, browser
    ):
        _ensure_tiers()
        staff_email = "tag-contrast-admin@test.com"
        _create_staff_user(staff_email)

        tagged_email = "tagged@test.com"
        _create_user(tagged_email, tier_slug="free")
        _set_tags(tagged_email, ["early-bird"])
        tagged_pk = _user_pk(tagged_email)

        # ---- Dark mode pass ----
        dark_context = _auth_context(browser, staff_email)
        _force_dark_mode(dark_context)
        page = dark_context.new_page()

        # /studio/users/ -- the user-row tag pill
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )
        assert page.evaluate(
            "() => document.documentElement.classList.contains('dark')"
        ) is True, "expected /studio/users/ to render in dark mode"

        row_tag_pill = page.locator(
            '[data-testid="user-tags-cell"] a[href*="tag="]'
        )
        assert row_tag_pill.count() >= 1, (
            "expected at least one tag pill in the Studio users row"
        )
        bg, fg = _computed_bg_and_fg(page, row_tag_pill)
        delta = _max_channel_delta(_parse_rgb(bg), _parse_rgb(fg))
        assert delta >= CHANNEL_DELTA_THRESHOLD, (
            f"row tag pill on /studio/users/ has illegible label in dark "
            f"mode: bg={bg}, color={fg}, max channel delta={delta} "
            f"< {CHANNEL_DELTA_THRESHOLD}. Use "
            f"border-accent/30 bg-accent/10 text-accent."
        )
        # The chip text must read as lime/green-family (the accent),
        # not near-black. Green channel must dominate the text color.
        r, g, b = _parse_rgb(fg)
        assert g > r and g > b, (
            f"row tag pill text color is not the lime accent: "
            f"rgb={r},{g},{b}. Use text-accent, not text-accent-foreground."
        )

        # /studio/users/?tag=early-bird -- the active-tag filter chip
        page.goto(
            f"{django_server}/studio/users/?tag=early-bird",
            wait_until="domcontentloaded",
        )
        active_tag_chip = page.locator(
            '[data-testid="active-tag-chip"] span'
        )
        assert active_tag_chip.count() >= 1, (
            "expected the active-tag chip on /studio/users/?tag=early-bird"
        )
        assert "Tag: early-bird" in active_tag_chip.first.inner_text()
        bg, fg = _computed_bg_and_fg(page, active_tag_chip)
        delta = _max_channel_delta(_parse_rgb(bg), _parse_rgb(fg))
        assert delta >= CHANNEL_DELTA_THRESHOLD, (
            f"active-tag chip on /studio/users/?tag=... has illegible label "
            f"in dark mode: bg={bg}, color={fg}, max channel delta={delta} "
            f"< {CHANNEL_DELTA_THRESHOLD}. Use "
            f"bg-accent/10 border-accent/30 text-accent."
        )

        # /studio/users/<id>/ -- per-tag chip in the Tags section
        page.goto(
            f"{django_server}/studio/users/{tagged_pk}/",
            wait_until="domcontentloaded",
        )
        detail_tag_chip = page.locator(
            '[data-testid="user-tag-chip"]'
        )
        assert detail_tag_chip.count() >= 1, (
            "expected at least one tag chip on the user detail page"
        )
        bg, fg = _computed_bg_and_fg(page, detail_tag_chip)
        delta = _max_channel_delta(_parse_rgb(bg), _parse_rgb(fg))
        assert delta >= CHANNEL_DELTA_THRESHOLD, (
            f"user-detail tag chip has illegible label in dark mode: "
            f"bg={bg}, color={fg}, max channel delta={delta} "
            f"< {CHANNEL_DELTA_THRESHOLD}. Use "
            f"bg-accent/10 text-accent border-accent/30."
        )

        dark_context.close()

        # ---- Light mode pass (no regression in the other theme) ----
        light_context = _auth_context(browser, staff_email)
        _force_light_mode(light_context)
        page = light_context.new_page()

        page.goto(
            f"{django_server}/studio/users/{tagged_pk}/",
            wait_until="domcontentloaded",
        )
        assert page.evaluate(
            "() => document.documentElement.classList.contains('dark')"
        ) is False, "expected user detail page to render in light mode"

        light_detail_chip = page.locator(
            '[data-testid="user-tag-chip"]'
        )
        bg, fg = _computed_bg_and_fg(page, light_detail_chip)
        delta = _max_channel_delta(_parse_rgb(bg), _parse_rgb(fg))
        assert delta >= CHANNEL_DELTA_THRESHOLD, (
            f"user-detail tag chip has illegible label in LIGHT mode: "
            f"bg={bg}, color={fg}, max channel delta={delta} "
            f"< {CHANNEL_DELTA_THRESHOLD}. The same bordered-accent class "
            f"must work in both themes."
        )

        # Also re-verify the row tag pill in light mode.
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )
        light_row_pill = page.locator(
            '[data-testid="user-tags-cell"] a[href*="tag="]'
        )
        bg, fg = _computed_bg_and_fg(page, light_row_pill)
        delta = _max_channel_delta(_parse_rgb(bg), _parse_rgb(fg))
        assert delta >= CHANNEL_DELTA_THRESHOLD, (
            f"row tag pill on /studio/users/ has illegible label in LIGHT "
            f"mode: bg={bg}, color={fg}, max channel delta={delta} "
            f"< {CHANNEL_DELTA_THRESHOLD}."
        )

        light_context.close()

    def test_full_opacity_filter_chips_keep_accent_foreground(
        self, django_server, browser
    ):
        """Regression guard: the active filter chip (e.g. ``Paid``) uses
        ``bg-accent text-accent-foreground`` -- a FILLED accent surface with
        the matching foreground token. That pairing is correct and must
        NOT be changed by this fix.
        """
        _ensure_tiers()
        staff_email = "filter-chip-admin@test.com"
        _create_staff_user(staff_email)

        context = _auth_context(browser, staff_email)
        _force_dark_mode(context)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/?filter=paid",
            wait_until="domcontentloaded",
        )
        assert page.evaluate(
            "() => document.documentElement.classList.contains('dark')"
        ) is True, "expected /studio/users/?filter=paid to render in dark mode"

        # The active Paid chip is inside the filter chip group and has
        # bg-accent on the active state.
        active_paid_chip = page.locator(
            '[data-testid="user-filter-chips"] a[data-filter="paid"]'
        )
        assert active_paid_chip.count() == 1, (
            "expected one Paid filter chip"
        )

        chip = active_paid_chip.first
        bg = chip.evaluate("el => getComputedStyle(el).backgroundColor")
        fg = chip.evaluate("el => getComputedStyle(el).color")

        # The accent in dark mode is lime (~ rgb(191, 255, 0)) -- the green
        # channel dominates. The accent-foreground in dark mode is
        # near-black (~ rgb(10, 10, 10)). We assert (a) the active chip
        # has a strong lime/green background (green channel dominates),
        # and (b) the foreground is near-black (all three channels below
        # 60). Together that confirms the bg-accent + text-accent-foreground
        # pairing was not regressed by the tag-pill fix.
        r, g, b = _parse_rgb(bg)
        assert g > r and g > b and g > 150, (
            f"active Paid chip background is not the lime accent: "
            f"rgb={r},{g},{b}. The filled chip must keep bg-accent."
        )
        fr, fg_, fb = _parse_rgb(fg)
        assert max(fr, fg_, fb) < 60, (
            f"active Paid chip text color is not near-black: "
            f"rgb={fr},{fg_},{fb}. The filled chip must keep "
            f"text-accent-foreground; do NOT switch it to text-accent."
        )

        # Sanity: the delta between bg and text on the FILLED chip is huge
        # (lime vs near-black), so it's also legible. We assert this too
        # so the guard scenario shares the legibility floor used elsewhere.
        delta = _max_channel_delta((r, g, b), (fr, fg_, fb))
        assert delta >= CHANNEL_DELTA_THRESHOLD, (
            f"active Paid chip somehow lost its bg/fg contrast: "
            f"bg={bg}, fg={fg}, delta={delta}."
        )

        context.close()
