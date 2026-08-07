"""Repository-wide lint: Django ``{# #}`` comments must be single-line.

Django recognizes a ``{# ... #}`` comment token only when the closing ``#}``
sits on the same line as the opening ``{#``.  A ``{# #}`` block that spans more
than one line is never lexed as a comment, so its raw text renders as visible
page content.  This has leaked to real pages repeatedly (book-club issues #1363
and #1366), caught only at design review.  The previous mitigation was a
per-page ``assertNotContains(response, '{#')`` guard, which is reactive and
misses untested branches.

This lint scans every ``templates/**/*.html`` file as RAW TEXT and fails on any
multi-line or unclosed ``{# #}``.  Unlike ``content/tests/test_design_system_lint.py``
(an HTML-layer check that masks ``<script>``/``<style>``/HTML-comment bodies),
this is a Django-syntax check: Django processes ``{# #}`` everywhere in a
template regardless of surrounding HTML, so a multi-line ``{#`` inside a
``<script>`` string, a ``<style>`` block, or an HTML comment is still a real,
broken Django comment.  Masking any context here would hide real bugs.

Exception policy: the current tree is clean (0 multi-line / unclosed across
~330 single-line comments), so this lands as a hard gate with no baseline.  Do
not add a baseline, wildcard/directory ignore, skip, or auto-update mode to hide
an offender — fix the template by switching to ``{% comment %} ... {% endcomment %}``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, tag


@dataclass(frozen=True)
class Offender:
    line: int
    kind: str  # "multi-line" or "unclosed"
    excerpt: str


def _one_line_excerpt(text: str, limit: int = 140) -> str:
    excerpt = " ".join(text.split())
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 1].rstrip() + "…"
    return excerpt


def find_comment_offenders(source: str) -> tuple[Offender, ...]:
    """Return every multi-line or unclosed Django ``{# #}`` comment in ``source``.

    Detection rule (exact, no rendering, no HTML parsing, no masking):

    * For every literal ``{#``, find the FIRST following ``#}`` (Django closes a
      comment at the first ``#}``).
    * No following ``#}`` -> unclosed offender.
    * The span from ``{#`` up to and including that ``#}`` contains a newline ->
      multi-line offender.
    * Otherwise the comment is single-line and passes.

    The reported line is the 1-based line of the opening ``{#``.
    """
    offenders: list[Offender] = []
    index = 0
    while True:
        open_at = source.find("{#", index)
        if open_at == -1:
            break
        line = source.count("\n", 0, open_at) + 1
        close_at = source.find("#}", open_at + 2)
        if close_at == -1:
            offenders.append(
                Offender(line=line, kind="unclosed", excerpt=_one_line_excerpt(source[open_at:]))
            )
            break
        span = source[open_at : close_at + 2]
        if "\n" in span:
            offenders.append(
                Offender(line=line, kind="multi-line", excerpt=_one_line_excerpt(span))
            )
        index = close_at + 2
    return tuple(offenders)


def discover_templates(template_root: Path) -> tuple[Path, ...]:
    """Return every regular HTML file below ``template_root`` in stable order."""
    return tuple(
        path
        for path in sorted(template_root.rglob("*.html"), key=lambda item: item.as_posix())
        if path.is_file()
    )


def _relative_posix(path: Path, base_dir: Path) -> str:
    return path.relative_to(base_dir).as_posix()


def scan_templates(template_root: Path, base_dir: Path) -> dict[str, tuple[Offender, ...]]:
    """Map each offending template's repo-relative POSIX path to its offenders."""
    results: dict[str, tuple[Offender, ...]] = {}
    for path in discover_templates(template_root):
        offenders = find_comment_offenders(path.read_text(encoding="utf-8"))
        if offenders:
            results[_relative_posix(path, base_dir)] = offenders
    return results


@tag("core")
class TemplateCommentLintTest(SimpleTestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.template_root = cls.base_dir / "templates"
        cls.templates = discover_templates(cls.template_root)
        cls.results = scan_templates(cls.template_root, cls.base_dir)

    def test_no_multiline_or_unclosed_comments_in_templates(self):
        """The whole live template tree must be free of multi-line/unclosed comments."""
        offenders = [
            f"{path}:{offender.line} [{offender.kind}] {offender.excerpt!r}"
            for path in sorted(self.results)
            for offender in self.results[path]
        ]
        self.assertEqual(
            offenders,
            [],
            "Multi-line or unclosed Django `{# #}` comments render as visible page "
            "text (Django comments are single-line only). Use "
            "`{% comment %} ... {% endcomment %}` instead. Offenders:\n"
            + "\n".join(offenders),
        )

    def test_discovery_finds_the_known_page_templates(self):
        """Fail loudly if discovery stops matching real templates."""
        self.assertGreater(len(self.templates), 50)
        discovered = {_relative_posix(path, self.base_dir) for path in self.templates}
        self.assertIn("templates/base.html", discovered)

    def test_multiline_comment_is_detected(self):
        source = "<p>x</p>\n{# line one\n line two #}\n<p>y</p>"
        offenders = find_comment_offenders(source)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].kind, "multi-line")
        self.assertEqual(offenders[0].line, 2)

    def test_single_line_comment_passes(self):
        self.assertEqual(find_comment_offenders("{# a normal single-line comment #}"), ())

    def test_two_closers_on_one_line_stay_single_line(self):
        # Django closes at the first `#}`, so both of these are single-line.
        self.assertEqual(
            find_comment_offenders("{# comment #} <p>x</p> {# another #}"),
            (),
        )
        self.assertEqual(find_comment_offenders("{# a #} b #}"), ())

    def test_unclosed_comment_is_detected(self):
        source = "<p>fine</p>\n{# never closed\nmore text\n"
        offenders = find_comment_offenders(source)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].kind, "unclosed")
        self.assertEqual(offenders[0].line, 2)

    def test_multiline_comment_inside_script_is_flagged(self):
        # Script/style bodies are NOT masked: this is a Django-syntax check.
        source = "<script>\n{# multi\n line #}\n</script>"
        offenders = find_comment_offenders(source)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].kind, "multi-line")
        self.assertEqual(offenders[0].line, 2)

    def test_multiple_offenders_reported_with_their_lines(self):
        source = "{# ok #}\n{# bad\nspan #}\n{# also\nbad #}\n"
        offenders = find_comment_offenders(source)
        self.assertEqual([o.line for o in offenders], [2, 4])
        self.assertTrue(all(o.kind == "multi-line" for o in offenders))

    def test_discovery_includes_untracked_templates(self):
        """A nested untracked template with an offender is discovered and reported."""
        local_tmp = self.base_dir / ".tmp"
        local_tmp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="template-comment-lint-",
            dir=local_tmp,
        ) as temporary_directory:
            temporary_base = Path(temporary_directory)
            temporary_templates = temporary_base / "templates"
            nested = temporary_templates / "new" / "untracked.html"
            nested.parent.mkdir(parents=True)
            nested.write_text("<p>x</p>\n{# multi\n line #}", encoding="utf-8")

            discovered = discover_templates(temporary_templates)
            results = scan_templates(temporary_templates, temporary_base)

            self.assertEqual(discovered, (nested,))
            offenders = results["templates/new/untracked.html"]
            self.assertEqual(len(offenders), 1)
            self.assertEqual(offenders[0].kind, "multi-line")
            self.assertEqual(offenders[0].line, 2)
