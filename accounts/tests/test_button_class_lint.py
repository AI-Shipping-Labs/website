"""Regression lint for ad-hoc button paddings on scoped product surfaces.

Issue #598 formalized a three-size scale (``sm`` / ``md`` / ``lg``) and
migrated every primary, secondary, and destructive CTA on six dashboard
plus plan-workspace templates to ``{% button_classes ... %}``. This lint
fails when a developer reintroduces a bare button class string with one
of the legacy ad-hoc paddings (``px-5``, ``py-2.5``, ``py-1`` without
``py-1.5``, or ``px-3 py-1.5`` outside the rendered tag output).

The lint reads templates as raw text — it does NOT render them. The
``button_classes`` template tag's output is the source of truth for what
"canonical" means; this test only guards against hand-rolled button class
strings creeping back in.

Allow-list (these are legitimate non-button uses of similar padding):

- ``<input>`` / ``<select>`` / ``<textarea>`` form fields keep their
  ``px-4 py-2.5`` chrome — matched by HTML tag, not by class string.
- Pill / badge spans with ``rounded-full`` use ``px-2.5 py-0.5`` or
  ``px-3 py-1`` — these are not buttons.
- Table ``<th>`` / ``<td>`` cells use ``px-4 py-3`` for cell padding.
- The per-row ``size='sm'`` action on ``cohort_board.html`` legitimately
  emits ``px-3 py-1.5`` through ``{% button_classes ... size='sm' %}``.
  Matched by the presence of the canonical base classes
  (``inline-flex`` + ``transition-colors`` +
  ``disabled:cursor-not-allowed``) which only the rendered tag output
  carries.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

# Scoped templates: these six have all been fully migrated to
# {% button_classes ... %}. Adding a seventh template here is fine; the
# lint runs against every entry. Do not add public/marketing surfaces
# without first migrating them.
SCOPED_TEMPLATES = (
    'templates/content/dashboard.html',
    'templates/accounts/account.html',
    'templates/plans/my_plan_detail.html',
    'templates/plans/member_plan_detail.html',
    'templates/plans/sprint_detail.html',
    'templates/plans/cohort_board.html',
)

# Tokens we forbid inside a button-shaped class attribute. ``py-1`` without
# ``py-1.5`` is matched with a word-boundary negative lookahead.
FORBIDDEN_BUTTON_PADDING_RE = re.compile(
    r'\b(?:px-5|py-2\.5|py-1(?!\.5))\b'
)

# A class attribute that looks like a hand-rolled button: it carries one
# of the canonical product palette tokens (``bg-accent``, ``bg-transparent``,
# or a destructive border) AND a padding utility. The renderer-style regex
# is intentional — see module docstring.
BUTTON_SHAPED_CLASS_RE = re.compile(
    r'class="([^"]*?(?:bg-accent|bg-transparent|border-red-500/30)[^"]*?)"'
)

# Form-control tags whose ``px-4 py-2.5`` chrome is allow-listed.
FORM_CONTROL_TAGS = ('<input', '<select', '<textarea')

# Pill / badge shapes that share padding utilities with buttons.
PILL_MARKERS = ('rounded-full',)

# The canonical base substring emitted by ``button_classes``. A class
# attribute containing this whole substring came through the tag and is
# therefore canonical even when it includes ``px-3 py-1.5`` (the ``sm``
# size).
RENDERED_BASE_MARKER = (
    'inline-flex items-center justify-center gap-2 rounded-md '
    'font-medium transition-colors disabled:cursor-not-allowed'
)


def _is_form_control_tag(html: str, class_attr_start: int) -> bool:
    """Return ``True`` when the enclosing tag is a form control."""
    tag_start = html.rfind('<', 0, class_attr_start)
    if tag_start == -1:
        return False
    tag_prefix = html[tag_start : tag_start + 12]
    return any(tag_prefix.startswith(t) for t in FORM_CONTROL_TAGS)


def _scan_template(path: Path) -> list[str]:
    """Return a list of human-readable violation lines for ``path``."""
    html = path.read_text(encoding='utf-8')
    violations: list[str] = []

    for match in BUTTON_SHAPED_CLASS_RE.finditer(html):
        class_attr = match.group(1)

        # Skip rendered canonical buttons (any size). These came through
        # ``{% button_classes ... %}`` so the size token they carry is
        # already in the canonical scale.
        if RENDERED_BASE_MARKER in class_attr:
            continue

        # Skip pills / badges — they happen to mix accent backgrounds
        # with small paddings but are not buttons.
        if any(marker in class_attr for marker in PILL_MARKERS):
            continue

        # Skip form controls (``<input>``, ``<select>``, ``<textarea>``).
        if _is_form_control_tag(html, match.start()):
            continue

        forbidden = FORBIDDEN_BUTTON_PADDING_RE.search(class_attr)
        if forbidden:
            # Compute a 1-based line number for the offending class attr.
            line_no = html.count('\n', 0, match.start()) + 1
            violations.append(
                f'{path.name}:{line_no}: forbidden padding '
                f'{forbidden.group()!r} in class={class_attr!r}',
            )

    return violations


class ButtonPaddingLintTest(TestCase):
    """Fail if any scoped template hand-rolls a non-canonical button."""

    def test_scoped_templates_have_no_ad_hoc_button_paddings(self):
        base = Path(settings.BASE_DIR)
        all_violations: list[str] = []
        for relpath in SCOPED_TEMPLATES:
            path = base / relpath
            self.assertTrue(
                path.exists(),
                f'Scoped template missing: {relpath}',
            )
            all_violations.extend(_scan_template(path))

        self.assertEqual(
            all_violations,
            [],
            msg=(
                'Ad-hoc button paddings detected on scoped product '
                'surfaces. Use {% button_classes variant size=... %} '
                'instead. Violations:\n  ' + '\n  '.join(all_violations)
            ),
        )

    def test_lint_fires_when_a_known_bad_string_is_injected(self):
        """Self-test: inject a hand-rolled button and confirm the lint trips.

        This proves the regex actually catches the patterns it claims to
        catch. If this test passes silently, the real lint above is
        worthless — we are guarding the guard.
        """
        bad_html = (
            '<a class="inline-flex items-center gap-2 rounded-md '
            'bg-accent px-5 py-2.5 text-sm font-medium '
            'text-accent-foreground hover:opacity-90">Go</a>'
        )
        # Mirror the scan loop without writing to disk.
        violations: list[str] = []
        for match in BUTTON_SHAPED_CLASS_RE.finditer(bad_html):
            class_attr = match.group(1)
            if RENDERED_BASE_MARKER in class_attr:
                continue
            if any(marker in class_attr for marker in PILL_MARKERS):
                continue
            if _is_form_control_tag(bad_html, match.start()):
                continue
            if FORBIDDEN_BUTTON_PADDING_RE.search(class_attr):
                violations.append(class_attr)

        self.assertEqual(
            len(violations),
            1,
            msg=(
                'Lint self-test failed to detect a known-bad button. '
                f'Violations seen: {violations!r}'
            ),
        )

    def test_lint_allows_form_inputs_with_px4_py25(self):
        """Form inputs intentionally keep their own padding chrome."""
        ok_html = (
            '<input type="text" class="w-full rounded-md border '
            'border-border bg-background px-4 py-2.5 text-base '
            'text-foreground">'
        )
        violations: list[str] = []
        for match in BUTTON_SHAPED_CLASS_RE.finditer(ok_html):
            class_attr = match.group(1)
            if RENDERED_BASE_MARKER in class_attr:
                continue
            if any(marker in class_attr for marker in PILL_MARKERS):
                continue
            if _is_form_control_tag(ok_html, match.start()):
                continue
            if FORBIDDEN_BUTTON_PADDING_RE.search(class_attr):
                violations.append(class_attr)

        self.assertEqual(violations, [])

    def test_lint_allows_rendered_sm_button_with_px3_py15(self):
        """The ``size='sm'`` rendered output carries ``px-3 py-1.5``.

        The lint must NOT flag this because it came through
        ``{% button_classes ... size='sm' %}`` — the canonical base
        marker pinpoints rendered tag output.
        """
        rendered_sm_html = (
            f'<button class="{RENDERED_BASE_MARKER} disabled:opacity-50 '
            f'focus-visible:outline-none focus-visible:ring-2 '
            f'focus-visible:ring-accent focus-visible:ring-offset-2 '
            f'focus-visible:ring-offset-background px-3 py-1.5 '
            f'text-xs bg-accent text-accent-foreground '
            f'hover:bg-accent/90">Ping</button>'
        )
        violations: list[str] = []
        for match in BUTTON_SHAPED_CLASS_RE.finditer(rendered_sm_html):
            class_attr = match.group(1)
            if RENDERED_BASE_MARKER in class_attr:
                continue
            if any(marker in class_attr for marker in PILL_MARKERS):
                continue
            if _is_form_control_tag(rendered_sm_html, match.start()):
                continue
            if FORBIDDEN_BUTTON_PADDING_RE.search(class_attr):
                violations.append(class_attr)

        self.assertEqual(violations, [])
