"""Regression test for issue #600.

The ASCII ` -- ` (space, two hyphens, space) sequence should never appear
in user-facing prose inside the markdown email templates. Plain-text mail
clients render markdown verbatim, so the fallback looks unpolished.

This test scans every shipped template file (the source of truth for both
plain-text and HTML email bodies) and fails if the sequence resurfaces.
"""

from django.test import SimpleTestCase

from email_app.services.email_service import TEMPLATES_DIR


class EmailTemplatesHaveNoAsciiDoubleDashTest(SimpleTestCase):
    """Every ``.md`` file in ``email_app/email_templates/`` is free of
    ` -- ` and the ``&mdash;`` HTML entity."""

    def test_no_ascii_double_dash_in_any_template_file(self):
        offenders = []
        for template_path in sorted(TEMPLATES_DIR.glob("*.md")):
            text = template_path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if " -- " in line:
                    offenders.append(f"{template_path.name}:{lineno}: {line!r}")

        self.assertEqual(
            offenders,
            [],
            "Email template files must not contain the ' -- ' ASCII "
            "fallback in user-facing prose; use a real em-dash (U+2014) "
            "or rewrite the sentence. Offending lines:\n"
            + "\n".join(offenders),
        )

    def test_no_mdash_html_entity_in_any_template_file(self):
        offenders = []
        for template_path in sorted(TEMPLATES_DIR.glob("*.md")):
            text = template_path.read_text(encoding="utf-8")
            if "&mdash;" in text:
                offenders.append(template_path.name)

        self.assertEqual(
            offenders,
            [],
            "Markdown email templates must use the literal U+2014 "
            "em-dash character, not the &mdash; HTML entity (the entity "
            "does not decode in plain-text mail). Offending files: "
            + ", ".join(offenders),
        )
