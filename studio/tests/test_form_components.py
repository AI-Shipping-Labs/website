"""Tests for shared Studio form helpers and includes."""

import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from studio.views.form_helpers import parse_comma_separated_tags

# `re.DOTALL` lets `.` match newlines so multi-line `<select>` opening tags
# (attributes wrapped across lines) are captured as one string.
SELECT_TAG_RE = re.compile(r'<select\b[^>]*?>', re.DOTALL)
COMMENT_BLOCK_RE = re.compile(
    r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', re.DOTALL,
)
SCRIPT_RE = re.compile(r'<script\b[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE)
# No `re.DOTALL` on this one, and none may be re-added: it mirrors Django's
# `tag_re`, which lexes `{# #}` only when the closer sits on the opening line.
# A `{#` that closes on a later line is NOT a comment — Django renders it, so a
# `<select>` inside it is a real control on a real page and must stay visible
# to this lint instead of being scrubbed away.
# `content/tests/test_template_comment_lint.py` keeps the tree free of such
# regions in the first place.
LINE_COMMENT_RE = re.compile(r'\{#[^\n]*?#\}')


def _strip_non_control_regions(text):
    """Blank regions where a `<select>` substring is not a real form control."""
    scrubbed = COMMENT_BLOCK_RE.sub('', text)
    scrubbed = SCRIPT_RE.sub('', scrubbed)
    return LINE_COMMENT_RE.sub('', scrubbed)


class StudioFormHelperTest(SimpleTestCase):
    """Test shared helpers used by hand-rendered Studio forms."""

    def test_parse_comma_separated_tags_trims_and_drops_empty_values(self):
        tags = parse_comma_separated_tags(' ai, , django ,, shipping ')

        self.assertEqual(tags, ['ai', 'django', 'shipping'])

    def test_parse_comma_separated_tags_handles_empty_values(self):
        self.assertEqual(parse_comma_separated_tags(''), [])
        self.assertEqual(parse_comma_separated_tags(None), [])


class StudioFormIncludeTest(SimpleTestCase):
    """Test shared form include rendering."""

    def test_required_level_renders_exact_options_and_preserves_selection(self):
        html = render_to_string(
            'studio/includes/forms/required_level.html',
            {'selected': 20, 'disabled': False},
        )

        options = re.findall(r'<option value="([^"]+)"([^>]*)>([^<]+)</option>', html)
        self.assertEqual(
            [(value, label.strip()) for value, _attrs, label in options],
            [
                ('0', 'Free (0)'),
                ('10', 'Basic (10)'),
                ('20', 'Main (20)'),
                ('30', 'Premium (30)'),
            ],
        )
        selected_attrs = {value: attrs for value, attrs, _label in options}
        self.assertNotIn('selected', selected_attrs['0'])
        self.assertIn('selected', selected_attrs['20'])

    def test_required_level_disabled_state_is_preserved(self):
        html = render_to_string(
            'studio/includes/forms/required_level.html',
            {'selected': 10, 'disabled': True},
        )

        select = re.search(r'<select[^>]*name="required_level"[^>]*>', html)
        self.assertIsNotNone(select)
        self.assertIn('disabled', select.group(0))

    def test_common_fields_render_disabled_state(self):
        for template_name, context, field_name in [
            (
                'studio/includes/forms/title.html',
                {'value': 'Title', 'disabled': True},
                'title',
            ),
            (
                'studio/includes/forms/slug.html',
                {'value': 'slug', 'disabled': True},
                'slug',
            ),
            (
                'studio/includes/forms/tags.html',
                {'tags': ['ai', 'shipping'], 'disabled': True},
                'tags',
            ),
        ]:
            with self.subTest(template_name=template_name):
                html = render_to_string(template_name, context)
                field = re.search(
                    rf'<input[^>]*name="{field_name}"[^>]*>',
                    html,
                )
                self.assertIsNotNone(field)
                self.assertIn('disabled', field.group(0))

    def test_tags_include_joins_values_for_editing(self):
        html = render_to_string(
            'studio/includes/forms/tags.html',
            {'tags': ['ai', 'shipping'], 'disabled': False},
        )

        self.assertIn('value="ai, shipping"', html)

    def test_action_row_suppresses_save_for_synced_content(self):
        html = render_to_string(
            'studio/includes/forms/action_row.html',
            {
                'is_synced': True,
                'submit_label': 'Save Changes',
                'cancel_url': '/studio/articles/',
            },
        )

        self.assertNotIn('Save Changes', html)
        self.assertNotIn('type="submit"', html)

    def test_action_row_renders_save_and_cancel_for_manual_content(self):
        html = render_to_string(
            'studio/includes/forms/action_row.html',
            {
                'is_synced': False,
                'submit_label': 'Save Changes',
                'cancel_url': '/studio/articles/',
            },
        )

        self.assertIn('Save Changes', html)
        self.assertIn('href="/studio/articles/"', html)


class GlobalSelectStyleTest(SimpleTestCase):
    """Regression coverage for the shared select chrome from issue #596."""

    def _template(self, relative_path):
        return Path(settings.BASE_DIR, 'templates', relative_path).read_text()

    def test_global_base_defines_app_select_and_studio_select_alias(self):
        css = Path(settings.BASE_DIR, 'assets/css/tailwind.css').read_text()

        self.assertIn('select.app-select,', css)
        self.assertIn('select.studio-select', css)
        self.assertIn('appearance: none;', css)
        self.assertIn('linear-gradient(45deg', css)
        self.assertIn('hsl(var(--muted-foreground))', css)
        self.assertNotIn('data:image/svg+xml', css)

    def test_studio_base_does_not_duplicate_studio_select_rule(self):
        html = self._template('studio/base.html')

        self.assertNotIn('select.studio-select {', html)

    def test_every_select_in_templates_has_canonical_class(self):
        """Every <select> opening tag in templates/ must carry the canonical
        chrome class (`app-select` or `studio-select`).

        The scan walks every `.html` file under `templates/`, strips out
        regions where a `<select>` substring is not a real form control
        (`{% comment %}` blocks, genuinely single-line `{# ... #}` tags, and
        `<script>...</script>` bodies), then locates each `<select` opening
        tag with a multi-line aware regex so tags whose attributes wrap
        across lines (see `templates/studio/_partials/datetime_picker.html`)
        are matched as a single string. Each matched tag must contain
        `app-select` or `studio-select` in its class attribute.

        On failure the test fails ONCE with the full `(file, line, snippet)`
        list so a future contributor can fix every violation in one pass
        instead of running the suite repeatedly. See `_docs/design-system.md`
        Form Controls section for the canonical class string.
        """
        templates_root = Path(settings.BASE_DIR, 'templates')

        violations = []
        for path in sorted(templates_root.rglob('*.html')):
            text = path.read_text()
            scrubbed = _strip_non_control_regions(text)
            for match in SELECT_TAG_RE.finditer(scrubbed):
                tag = match.group(0)
                if 'app-select' in tag or 'studio-select' in tag:
                    continue
                # Look up the line number against the ORIGINAL text so the
                # snippet points the contributor at the real source line.
                # The 40-char prefix is enough to disambiguate against any
                # other `<select` opening in the file.
                offset = text.find(tag[:40])
                line_no = (
                    text.count('\n', 0, offset) + 1 if offset >= 0 else None
                )
                rel = path.relative_to(settings.BASE_DIR)
                snippet = ' '.join(tag.split())[:160]
                violations.append((str(rel), line_no, snippet))

        if violations:
            lines = [
                'Found <select> elements missing app-select / studio-select:',
            ]
            for rel, line_no, snippet in violations:
                lines.append(f'  {rel}:{line_no}  {snippet!r}')
            lines.append(
                'Add "app-select" (public templates) or "studio-select" '
                '(under /studio/) to the class attribute. See '
                '_docs/design-system.md Form Controls.'
            )
            self.fail('\n'.join(lines))


class NonControlRegionStripTest(SimpleTestCase):
    """The scrub must mirror Django's tokenizer, not a permissive superset."""

    def test_select_inside_a_multiline_comment_survives_stripping(self):
        source = (
            '{# note about the control\n'
            '<select name="reminder"></select>\n'
            'end of note #}\n'
        )

        # Django never lexes that comment, so the control really renders and
        # the canonical-class lint must still see it.
        self.assertIn('<select name="reminder">', _strip_non_control_regions(source))

    def test_select_inside_a_single_line_comment_is_stripped(self):
        source = '{# <select name="reminder"></select> #}'

        self.assertNotIn('<select', _strip_non_control_regions(source))

    def test_block_regions_are_still_stripped(self):
        sources = (
            '{% comment %}\n<select name="reminder"></select>\n{% endcomment %}',
            '<script>\nvar x = "<select name=\'reminder\'>";\n</script>',
        )

        for source in sources:
            with self.subTest(source=source.splitlines()[0]):
                self.assertNotIn('<select', _strip_non_control_regions(source))


class GlobalIframeTitleTest(SimpleTestCase):
    """Every first-party template iframe has a non-empty accessible name."""

    def test_every_iframe_in_templates_has_non_empty_title(self):
        templates_root = Path(settings.BASE_DIR, 'templates')
        iframe_re = re.compile(r'<iframe\b[^>]*?>', re.DOTALL | re.IGNORECASE)
        title_re = re.compile(
            r'\btitle\s*=\s*(["\'])(.*?)\1',
            re.DOTALL | re.IGNORECASE,
        )
        violations = []

        for path in sorted(templates_root.rglob('*.html')):
            source = path.read_text(encoding='utf-8')
            for iframe_match in iframe_re.finditer(source):
                iframe = iframe_match.group(0)
                title_match = title_re.search(iframe)
                if title_match and title_match.group(2).strip():
                    continue
                line_no = source.count('\n', 0, iframe_match.start()) + 1
                relative_path = path.relative_to(settings.BASE_DIR)
                violations.append(f'{relative_path}:{line_no}')

        self.assertEqual(
            violations,
            [],
            'Found first-party template iframes without a non-empty title: '
            + ', '.join(violations),
        )
