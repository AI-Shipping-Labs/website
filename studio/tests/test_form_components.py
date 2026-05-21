"""Tests for shared Studio form helpers and includes."""

import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from studio.views.form_helpers import parse_comma_separated_tags


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
        html = self._template('base.html')

        self.assertIn('select.app-select,', html)
        self.assertIn('select.studio-select', html)
        self.assertIn('appearance: none;', html)
        self.assertIn('linear-gradient(45deg', html)
        self.assertIn('hsl(var(--muted-foreground))', html)
        self.assertNotIn('data:image/svg+xml', html)

    def test_studio_base_does_not_duplicate_studio_select_rule(self):
        html = self._template('studio/base.html')

        self.assertNotIn('select.studio-select {', html)

    def test_every_select_in_templates_has_canonical_class(self):
        """Every <select> opening tag in templates/ must carry the canonical
        chrome class (`app-select` or `studio-select`).

        The scan walks every `.html` file under `templates/`, strips out
        regions where a `<select>` substring is not a real form control
        (`{% comment %}` blocks, `{# ... #}` single-line tags, and
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
        # `re.DOTALL` lets `.` match newlines so multi-line opening tags
        # (attributes wrapped across lines) are captured as one string.
        tag_re = re.compile(r'<select\b[^>]*?>', re.DOTALL)
        comment_block_re = re.compile(
            r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', re.DOTALL,
        )
        script_re = re.compile(
            r'<script\b[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE,
        )
        line_comment_re = re.compile(r'\{#.*?#\}', re.DOTALL)

        violations = []
        for path in sorted(templates_root.rglob('*.html')):
            text = path.read_text()
            # Strip non-control regions before scanning for <select> tags.
            scrubbed = comment_block_re.sub('', text)
            scrubbed = script_re.sub('', scrubbed)
            scrubbed = line_comment_re.sub('', scrubbed)
            for match in tag_re.finditer(scrubbed):
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
