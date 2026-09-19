"""Repository-wide lint: Django template constructs must be single-line.

Django recognizes ``{% ... %}``, ``{{ ... }}`` and ``{# ... #}`` only when the
closing marker sits on the same line as the opening one -- ``tag_re`` is one
non-DOTALL alternation over all three.  A construct that spans more than one
line is never lexed, so its raw text renders as visible content.  This has
leaked to real pages repeatedly (book-club issues), caught only at design
review.  The previous mitigation was a per-page ``assertNotContains`` guard on
the opening marker, which is reactive and misses untested branches.

This lint scans, as RAW TEXT, every file Django compiles as a template:

* ``templates/**/*.html`` -- the only HTML template root (``website/settings.py``
  sets ``DIRS: [BASE_DIR / 'templates']``, and no app-level template directory
  is tracked despite ``APP_DIRS``).
* ``email_app/email_templates/*.md`` -- the shipped transactional email bodies,
  compiled by ``django.template.Template`` in
  ``email_app/services/email_rendering.py``.

The detection rule itself lives in ``content/utils/template_comments.py`` so the
Studio email-template editor can reject the same defect in operator-authored
``EmailTemplateOverride`` copy, which no file lint can see.  There is exactly
one implementation of the rule.

Nothing else is scanned.  ``.py``, ``_docs/``, ``specs/``, ``.claude/`` and
``skills/`` carry roughly 38 legitimate lone opening markers -- assertion
literals across six apps and a deliberately-bad fenced example in the testing
guidelines -- which are prose and string literals, not template source.

Unlike ``content/tests/test_design_system_lint.py`` (an HTML-layer check that
masks ``<script>``/``<style>``/HTML-comment bodies), this is a Django-syntax
check: Django processes these constructs everywhere in a template regardless of
surrounding HTML, so a multi-line comment inside a ``<script>`` string, a
``<style>`` block, or an HTML comment is still a real, broken Django comment.
Masking any context here would hide real bugs.

Exception policy: the current tree is clean (0 multi-line / unclosed across both
trees and all three constructs, alongside ~300 correct single-line ``{# #}``
comments), so this is a hard gate with no baseline.  Do not add a baseline,
wildcard/directory ignore, skip, or auto-update mode to hide an offender -- fix
the template.  A single-line ``{# ... #}`` remains fully acceptable; this is a
rule about span, not a deprecation.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, tag

from content.utils.template_comments import (
    COMMENT_TOKEN,
    KIND_MULTILINE,
    KIND_UNCLOSED,
    TOKEN_FORMS,
    Offender,
    describe_offender,
    find_token_offenders,
)


def _sorted_files(matches: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(matches, key=lambda item: item.as_posix())
        if path.is_file()
    )


def discover_templates(template_root: Path) -> tuple[Path, ...]:
    """Return every regular HTML file below ``template_root`` in stable order."""
    return _sorted_files(template_root.rglob("*.html"))


def discover_email_templates(email_template_root: Path) -> tuple[Path, ...]:
    """Return every transactional email body in ``email_template_root``.

    The directory is flat and non-recursive on purpose: it is the exact set
    ``email_app/services/email_rendering.py`` compiles.
    """
    return _sorted_files(email_template_root.glob("*.md"))


#: ``(repo-relative root, discovery function)`` for every tree Django compiles.
#: Two named functions with LITERAL glob patterns, rather than one table-driven
#: ``root.rglob(pattern)`` call: the rule-14 discovery census in
#: ``tests/test_affected_tests.py`` is a static AST resolver that only folds
#: constant patterns, so a table-driven scan would be invisible to the guard
#: that keeps ``REPO_WIDE_GUARDS`` honest (#1755).
SCAN_ROOTS: tuple[tuple[str, Callable[[Path], tuple[Path, ...]]], ...] = (
    ("templates", discover_templates),
    ("email_app/email_templates", discover_email_templates),
)


def _relative_posix(path: Path, base_dir: Path) -> str:
    return path.relative_to(base_dir).as_posix()


def scan_root(
    root: Path,
    base_dir: Path,
    discover: Callable[[Path], tuple[Path, ...]],
) -> dict[str, tuple[Offender, ...]]:
    """Map each offending file's repo-relative POSIX path to its offenders."""
    results: dict[str, tuple[Offender, ...]] = {}
    for path in discover(root):
        offenders = find_token_offenders(path.read_text(encoding="utf-8"))
        if offenders:
            results[_relative_posix(path, base_dir)] = offenders
    return results


def discover_all_sources(base_dir: Path) -> tuple[Path, ...]:
    """Return every template Django compiles, across all scanned roots.

    ``tests/test_affected_tests.py`` imports this as the enumerator evidence
    binding this lint's ``REPO_WIDE_GUARDS`` row to what it actually reads.
    """
    return tuple(
        path for root, discover in SCAN_ROOTS for path in discover(base_dir / root)
    )


def scan_all_sources(base_dir: Path) -> dict[str, tuple[Offender, ...]]:
    """Scan every configured root so one failure lists offenders from all of them."""
    results: dict[str, tuple[Offender, ...]] = {}
    for root, discover in SCAN_ROOTS:
        results.update(scan_root(base_dir / root, base_dir, discover))
    return results


@tag("core")
class TemplateCommentLintTest(SimpleTestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.template_root = cls.base_dir / "templates"
        cls.email_template_root = cls.base_dir / "email_app/email_templates"
        cls.sources = discover_all_sources(cls.base_dir)
        cls.results = scan_all_sources(cls.base_dir)

    def test_no_multiline_or_unclosed_constructs_in_compiled_templates(self):
        """Both compiled trees must be free of multi-line/unclosed constructs."""
        offenders = [
            f"{path}:{offender.line} [{offender.kind} {offender.token}] {offender.excerpt!r}"
            for path in sorted(self.results)
            for offender in self.results[path]
        ]
        self.assertEqual(
            offenders,
            [],
            "Multi-line or unclosed Django constructs render as visible text "
            "(Django lexes them single-line only). Use "
            "`{% comment %} ... {% endcomment %}` for long notes, and keep tag "
            "and variable markers on one line. Offenders:\n" + "\n".join(offenders),
        )

    def test_discovery_finds_the_known_page_templates(self):
        """Fail loudly if HTML discovery stops matching real templates."""
        templates = discover_templates(self.template_root)
        discovered = {_relative_posix(path, self.base_dir) for path in templates}
        self.assertGreater(len(templates), 50)
        self.assertIn("templates/base.html", discovered)

    def test_discovery_finds_the_email_template_tree(self):
        """A path change must fail loudly, not degrade into a scan of nothing."""
        email_templates = discover_email_templates(self.email_template_root)
        discovered = {_relative_posix(path, self.base_dir) for path in email_templates}
        self.assertGreater(len(email_templates), 40)
        self.assertIn("email_app/email_templates/welcome.md", discovered)

    def test_only_compiled_template_trees_are_scanned(self):
        """Prose and Python string literals must stay out of the scan.

        `.py`, `_docs/`, `specs/`, `.claude/` and `skills/` carry legitimate
        lone opening markers -- per-page assertion literals across six apps and
        a deliberately-bad fenced example in the testing guidelines. Scanning
        them would be all false positives.
        """
        relative = {_relative_posix(path, self.base_dir) for path in self.sources}

        self.assertEqual(
            {root for root, _discover in SCAN_ROOTS},
            {"templates", "email_app/email_templates"},
        )
        # The table and the two named discovery functions cannot drift apart:
        # what the class scanned is exactly their union.
        self.assertEqual(
            self.sources,
            discover_templates(self.template_root)
            + discover_email_templates(self.email_template_root),
        )
        for path in relative:
            self.assertTrue(
                path.startswith("templates/")
                or path.startswith("email_app/email_templates/"),
                path,
            )
            self.assertTrue(path.endswith((".html", ".md")), path)

    def test_multiline_comment_is_detected(self):
        source = "<p>x</p>\n{# line one\n line two #}\n<p>y</p>"
        offenders = find_token_offenders(source)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].kind, KIND_MULTILINE)
        self.assertEqual(offenders[0].token, COMMENT_TOKEN)
        self.assertEqual(offenders[0].line, 2)

    def test_single_line_comment_passes(self):
        self.assertEqual(find_token_offenders("{# a normal single-line comment #}"), ())

    def test_two_closers_on_one_line_stay_single_line(self):
        # Django closes at the first closing marker, so both of these are single-line.
        self.assertEqual(
            find_token_offenders("{# comment #} <p>x</p> {# another #}"),
            (),
        )
        self.assertEqual(find_token_offenders("{# a #} b #}"), ())

    def test_unclosed_comment_is_detected(self):
        source = "<p>fine</p>\n{# never closed\nmore source\n"
        offenders = find_token_offenders(source)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].kind, KIND_UNCLOSED)
        self.assertEqual(offenders[0].token, COMMENT_TOKEN)
        self.assertEqual(offenders[0].line, 2)

    def test_multiline_tag_is_detected_and_named(self):
        source = "<p>x</p>\n{% if user.is_staff\n   and 1 %}\nyes\n{% endif %}"
        offenders = find_token_offenders(source)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].kind, KIND_MULTILINE)
        self.assertEqual(offenders[0].token, "{% %}")
        self.assertEqual(offenders[0].line, 2)

    def test_multiline_variable_is_detected_and_named(self):
        source = "<p>{{ user.email\n   }}</p>"
        offenders = find_token_offenders(source)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].kind, KIND_MULTILINE)
        self.assertEqual(offenders[0].token, "{{ }}")
        self.assertEqual(offenders[0].line, 1)

    def test_operator_copy_names_each_construct_for_what_it_is(self):
        """A ``{{ }}`` is a variable, not a tag.

        The Studio editor shows this copy next to a Subject field whose own
        help text says "Supports Django variables, e.g. {{ tier_name }}", so
        the two would contradict each other in one screen.
        """
        messages = {
            token: describe_offender(
                Offender(line=4, kind=KIND_MULTILINE, token=token, excerpt="x")
            )
            for _opening, _closing, token in TOKEN_FORMS
        }
        self.assertIn('a "{{ }}" variable must open and close', messages["{{ }}"])
        self.assertIn("renders an unclosed variable", messages["{{ }}"])
        self.assertNotIn("tag", messages["{{ }}"])
        self.assertIn('a "{% %}" tag must open and close', messages["{% %}"])
        self.assertIn('a "{# #}" comment must open and close', messages[COMMENT_TOKEN])
        # Only the comment form has a block equivalent to point at.
        self.assertIn("{% comment %}", messages[COMMENT_TOKEN])
        for token in ("{% %}", "{{ }}"):
            self.assertNotIn("{% comment %}", messages[token])
            self.assertIn("Line 4:", messages[token])

    def test_markers_inside_a_valid_comment_are_not_re_examined(self):
        """A single-line comment that mentions a tag is not a false positive."""
        self.assertEqual(find_token_offenders("{# example: {% if x %} here #}"), ())

    def test_multiline_comment_inside_script_is_flagged(self):
        # Script/style bodies are NOT masked: this is a Django-syntax check.
        source = "<script>\n{# multi\n line #}\n</script>"
        offenders = find_token_offenders(source)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].kind, KIND_MULTILINE)
        self.assertEqual(offenders[0].line, 2)

    def test_multiple_offenders_reported_with_their_lines(self):
        source = "{# ok #}\n{# bad\nspan #}\n{# also\nbad #}\n"
        offenders = find_token_offenders(source)
        self.assertEqual([o.line for o in offenders], [2, 4])
        self.assertTrue(all(o.kind == KIND_MULTILINE for o in offenders))

    def test_discovery_includes_untracked_templates(self):
        """A nested untracked template with an offender is discovered and reported."""
        with self._temporary_tree() as temporary_base:
            temporary_templates = temporary_base / "templates"
            nested = temporary_templates / "new" / "untracked.html"
            nested.parent.mkdir(parents=True)
            nested.write_text("<p>x</p>\n{# multi\n line #}", encoding="utf-8")

            discovered = discover_templates(temporary_templates)
            results = scan_root(temporary_templates, temporary_base, discover_templates)

            self.assertEqual(discovered, (nested,))
            offenders = results["templates/new/untracked.html"]
            self.assertEqual(len(offenders), 1)
            self.assertEqual(offenders[0].kind, KIND_MULTILINE)
            self.assertEqual(offenders[0].line, 2)

    def test_malformed_email_template_markdown_is_reported(self):
        """The guard fails on a bad email template, not only passes on good ones."""
        with self._temporary_tree() as temporary_base:
            email_templates = temporary_base / "email_app" / "email_templates"
            email_templates.mkdir(parents=True)
            (email_templates / "broken.md").write_text(
                "Hi {{ user_name }}\n{# note about the CTA\n   continued here #}\nThanks",
                encoding="utf-8",
            )
            (email_templates / "good.md").write_text(
                "Hi {{ user_name }}\n"
                "{% comment %}\nnote about the CTA\ncontinued here\n{% endcomment %}\n"
                "{# short note #}\nThanks",
                encoding="utf-8",
            )

            results = scan_root(
                email_templates, temporary_base, discover_email_templates
            )

            self.assertEqual(sorted(results), ["email_app/email_templates/broken.md"])
            offenders = results["email_app/email_templates/broken.md"]
            self.assertEqual(len(offenders), 1)
            self.assertEqual(offenders[0].kind, KIND_MULTILINE)
            self.assertEqual(offenders[0].token, COMMENT_TOKEN)
            self.assertEqual(offenders[0].line, 2)

    def _temporary_tree(self):
        """A scratch tree under the repo's own ``.tmp``, never the system temp dir."""
        local_tmp = Path(settings.BASE_DIR) / ".tmp"
        local_tmp.mkdir(exist_ok=True)
        return _scratch_tree(local_tmp)


@contextmanager
def _scratch_tree(parent: Path):
    with tempfile.TemporaryDirectory(
        prefix="template-comment-lint-", dir=parent
    ) as temporary_directory:
        yield Path(temporary_directory)
