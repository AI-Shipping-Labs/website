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
from django.test import TestCase, tag

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
    'templates/content/peer_review/dashboard.html',
    'templates/includes/tag_rule_components.html',
    'templates/events/cancel_registration_confirm.html',
)

FORBIDDEN_ACTION_PALETTE_TOKENS = (
    'bg-purple-600',
    'hover:bg-purple-700',
    'bg-green-600',
    'hover:bg-green-700',
    'bg-red-500/90',
)


IGNORED_SOURCE_REGIONS = re.compile(
    r'{#.*?#}'
    r'|{%\s*comment\s*%}.*?{%\s*endcomment\s*%}'
    r'|<!--.*?-->'
    r'|<script\b.*?</script\s*>'
    r'|<style\b.*?</style\s*>',
    re.DOTALL | re.IGNORECASE,
)
MARKUP_TAG_RE = re.compile(
    r'<[a-z][a-z0-9:-]*\b(?P<attrs>[^>]*)>',
    re.DOTALL | re.IGNORECASE,
)
CLASS_ATTR_RE = re.compile(
    r'\bclass\s*=\s*(?P<quote>["\'])(?P<classes>.*?)(?P=quote)',
    re.DOTALL | re.IGNORECASE,
)
MAX_PALETTE_DIAGNOSTICS = 25

# The #1434 implementation baseline contains no forbidden action-palette
# occurrences in Studio. The explicit rule/path map is intentionally empty:
# every new occurrence therefore has a positive budget of zero. The separate
# ceiling prevents the map from ever growing beyond this baseline.
KNOWN_STUDIO_ACTION_PALETTE_EXCEPTIONS: dict[tuple[str, str], int] = {}
STUDIO_ACTION_PALETTE_EXCEPTION_CEILING: dict[tuple[str, str], int] = {}


def _mask_ignored_regions(source: str) -> str:
    return IGNORED_SOURCE_REGIONS.sub(
        lambda match: re.sub(r'[^\n]', ' ', match.group(0)),
        source,
    )


def _action_palette_occurrences(html: str) -> list[tuple[str, int]]:
    """Find exact forbidden class tokens on action-shaped markup."""

    masked = _mask_ignored_regions(html)
    occurrences = []
    for tag_match in MARKUP_TAG_RE.finditer(masked):
        class_match = CLASS_ATTR_RE.search(tag_match.group('attrs'))
        if class_match is None:
            continue
        classes = class_match.group('classes')
        class_offset = tag_match.start('attrs') + class_match.start('classes')
        for token in FORBIDDEN_ACTION_PALETTE_TOKENS:
            token_re = re.compile(rf'(?<!\S){re.escape(token)}(?!\S)')
            for token_match in token_re.finditer(classes):
                offset = class_offset + token_match.start()
                occurrences.append((token, masked.count('\n', 0, offset) + 1))
    return occurrences


def _scan_forbidden_action_palette(html: str) -> list[str]:
    """Compatibility helper returning each matched token in source order."""

    return [token for token, _line in _action_palette_occurrences(html)]


def _palette_diagnostic(
    rule: str,
    path: str,
    line: int,
    allowed: int,
    actual: int,
    remediation: str,
) -> str:
    return (
        f'rule={rule} path={path} line={line} allowed={allowed} '
        f'actual={actual} remediation={remediation}'
    )


def _scan_action_palette_sources(
    sources: dict[str, str],
    exceptions: dict[tuple[str, str], int] | None = None,
    ceiling: dict[tuple[str, str], int] | None = None,
) -> list[str]:
    """Scan all template sources with Studio-only rule/path debt budgets."""

    exceptions = (
        KNOWN_STUDIO_ACTION_PALETTE_EXCEPTIONS
        if exceptions is None
        else exceptions
    )
    ceiling = (
        STUDIO_ACTION_PALETTE_EXCEPTION_CEILING if ceiling is None else ceiling
    )
    diagnostics = []

    for (rule, path), allowed_count in sorted(exceptions.items()):
        ceiling_count = ceiling.get((rule, path))
        if ceiling_count is None:
            diagnostics.append(
                _palette_diagnostic(
                    'studio-action-palette-exception-path-ceiling',
                    path,
                    1,
                    0,
                    1,
                    'use-studio-action-owner-instead-of-adding-an-exception',
                ),
            )
        elif allowed_count > ceiling_count:
            diagnostics.append(
                _palette_diagnostic(
                    'studio-action-palette-exception-count-ceiling',
                    path,
                    1,
                    ceiling_count,
                    allowed_count,
                    f'remove-{rule}-and-use-studio-action-owner',
                ),
            )

    actual_counts = {}
    first_lines = {}
    for path, source in sorted(sources.items()):
        for rule, line in _action_palette_occurrences(source):
            key = (rule, path)
            actual_counts[key] = actual_counts.get(key, 0) + 1
            first_lines.setdefault(key, line)

    keys = set(actual_counts) | set(exceptions)
    for rule, path in sorted(keys):
        actual = actual_counts.get((rule, path), 0)
        allowed = exceptions.get((rule, path), 0) if path.startswith('studio/') else 0
        if actual == allowed:
            continue
        is_studio = path.startswith('studio/')
        owner = (
            'studio_header_actions|studio_overflow_menu|studio_list_action|'
            'studio_action_class'
            if is_studio
            else 'button_classes'
        )
        diagnostics.append(
            _palette_diagnostic(
                f'forbidden-action-palette:{rule}',
                path,
                first_lines.get((rule, path), 1),
                allowed,
                actual,
                f'use-indexed-owner-{owner}',
            ),
        )

    ordered = sorted(set(diagnostics))
    if len(ordered) <= MAX_PALETTE_DIAGNOSTICS:
        return ordered
    return ordered[:MAX_PALETTE_DIAGNOSTICS] + [
        _palette_diagnostic(
            'studio-action-palette-diagnostic-limit',
            'studio',
            1,
            MAX_PALETTE_DIAGNOSTICS,
            len(ordered),
            'fix-listed-violations-and-rerun',
        ),
    ]

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


@tag('core')
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

    def test_all_templates_have_no_unbudgeted_forbidden_action_palette(self):
        templates = Path(settings.BASE_DIR) / 'templates'
        sources = {}
        for path in templates.rglob('*.html'):
            relative = path.relative_to(templates).as_posix()
            sources[relative] = path.read_text(encoding='utf-8')
        self.assertEqual(_scan_action_palette_sources(sources), [])

    def test_action_palette_lint_rejects_synthetic_violation(self):
        bad = '<a class="rounded bg-green-600 hover:bg-green-700">Go</a>'
        self.assertEqual(
            _scan_forbidden_action_palette(bad),
            ['bg-green-600', 'hover:bg-green-700'],
        )

    def test_new_studio_palette_violation_defaults_to_zero(self):
        diagnostics = _scan_action_palette_sources({
            'studio/synthetic.html': (
                '<button\n class="rounded bg-green-600">Go</button>'
            ),
        })
        self.assertEqual(
            diagnostics,
            [
                'rule=forbidden-action-palette:bg-green-600 '
                'path=studio/synthetic.html line=2 allowed=0 actual=1 '
                'remediation=use-indexed-owner-studio_header_actions|'
                'studio_overflow_menu|studio_list_action|studio_action_class',
            ],
        )

    def test_studio_palette_exception_cannot_raise_empty_baseline(self):
        diagnostics = _scan_action_palette_sources(
            {'studio/synthetic.html': '<button class="bg-green-600">Go</button>'},
            exceptions={('bg-green-600', 'studio/synthetic.html'): 1},
            ceiling={},
        )
        self.assertTrue(
            any(
                'rule=studio-action-palette-exception-path-ceiling' in row
                for row in diagnostics
            ),
        )
        for row in diagnostics:
            self.assertRegex(
                row,
                r'rule=\S+ path=\S+ line=\d+ allowed=\d+ actual=\d+ remediation=',
            )

    def test_action_matcher_boundaries_and_studio_owners(self):
        owner_source = """
            {% studio_header_actions title='Owned' %}
              {% studio_overflow_menu %}{% endstudio_overflow_menu %}
              {% studio_list_action detail_url 'View' 'secondary' %}
              <button class="{%
                  studio_action_class
                  'primary'
              %}">Save</button>
            {% endstudio_header_actions %}
        """
        boundary_sources = {
            'studio/owners.html': owner_source,
            'studio/lookalikes.html': """
                {# <button class="bg-green-600">Comment</button> #}
                {% comment %}<a class="bg-green-600">Comment</a>{% endcomment %}
                <!-- <a class="bg-green-600">Comment</a> -->
                <script>const klass = 'bg-green-600';</script>
                <style>.bg-green-600ish { color: green; }</style>
                <a class="bg-green-600ish">Substring</a>
            """,
            # The palette guard is Studio-wide, so genuine partial violations
            # are deliberately scanned even though header discovery ignores them.
            'studio/_partial.html': '<a class="bg-green-600">Partial action</a>',
            'studio/non_page.html': '<div class="text-green-600">Status</div>',
        }
        diagnostics = _scan_action_palette_sources(boundary_sources)
        self.assertEqual(len(diagnostics), 1)
        self.assertIn('path=studio/_partial.html', diagnostics[0])
        self.assertNotIn('button_classes', owner_source)

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
