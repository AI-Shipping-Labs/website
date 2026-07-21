"""Source lint: internal IA vocabulary must not ship as public page copy.

Landing-page audits (`_docs/landing-pages/README.md`, "Internal IA notes
shipped as public copy") found taxonomy-contract language from
`_docs/product.md` pasted into visitor-facing templates as body copy.
This guard keeps that category of regression out of shipped markup.

Phrase-list policy: every entry must be genuinely internal vocabulary —
a term that describes the site's data model or content taxonomy rather
than a visitor benefit. Words that are merely weak copy do not belong
here. The taxonomy language itself stays canonical in
`_docs/product.md`; it is only banned from user-facing templates.
Modeled on ``content/tests/test_design_system_lint.py``.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, tag

# Each phrase is matched case-insensitively as a substring of template
# source, so singular forms also catch plurals.
INTERNAL_PHRASES = (
    "legacy discovery",
    "canonical learning artifact",
    "durable hands-on learning artifact",
)

# Staff-facing template trees where internal vocabulary is acceptable.
EXCLUDED_PREFIXES = (
    "templates/studio/",
    "templates/emails/",
)


def find_internal_phrases(relative_path: str, source: str) -> tuple[tuple[str, int], ...]:
    """Return ``(phrase, line)`` hits for internal phrases in one template."""
    if any(relative_path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return ()
    lowered = source.lower()
    hits: list[tuple[str, int]] = []
    for phrase in INTERNAL_PHRASES:
        search_from = 0
        while (index := lowered.find(phrase, search_from)) != -1:
            hits.append((phrase, source.count("\n", 0, index) + 1))
            search_from = index + len(phrase)
    return tuple(sorted(hits, key=lambda hit: (hit[1], hit[0])))


@tag("core")
class InternalCopyLintTest(SimpleTestCase):
    maxDiff = None

    def test_public_templates_do_not_ship_internal_ia_vocabulary(self):
        base_dir = Path(settings.BASE_DIR)
        offenders: list[str] = []
        for path in sorted((base_dir / "templates").rglob("*.html"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative_path = path.relative_to(base_dir).as_posix()
            source = path.read_text(encoding="utf-8")
            for phrase, line in find_internal_phrases(relative_path, source):
                offenders.append(f"{relative_path}:{line}: {phrase!r}")

        self.assertEqual(
            offenders,
            [],
            "Internal IA vocabulary shipped in user-facing templates:\n"
            + "\n".join(offenders)
            + "\nRemediation: rewrite the sentence as visitor-facing copy "
            "(what the visitor gets, in plain words). Keep taxonomy "
            "language in _docs/product.md, not in shipped markup.",
        )

    def test_matcher_fires_on_positive_example(self):
        source = (
            "<p>\n"
            "  Recordings stay here for legacy discovery. The workshop is\n"
            "  the canonical learning artifact.\n"
            "</p>\n"
            "<p>Workshop pages are durable hands-on learning artifacts.</p>\n"
        )
        self.assertEqual(
            find_internal_phrases("templates/events/example.html", source),
            (
                ("legacy discovery", 2),
                ("canonical learning artifact", 3),
                ("durable hands-on learning artifact", 5),
            ),
        )

    def test_matcher_is_case_insensitive(self):
        self.assertEqual(
            find_internal_phrases(
                "templates/example.html",
                "<p>Legacy Discovery</p>",
            ),
            (("legacy discovery", 1),),
        )

    def test_matcher_skips_staff_facing_trees(self):
        for relative_path in (
            "templates/studio/example.html",
            "templates/emails/example.html",
        ):
            with self.subTest(path=relative_path):
                self.assertEqual(
                    find_internal_phrases(
                        relative_path,
                        "<p>legacy discovery</p>",
                    ),
                    (),
                )
