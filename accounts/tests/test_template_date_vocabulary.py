import re
from pathlib import Path

from django.test import SimpleTestCase

from accounts.templatetags import date_formatting

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = PROJECT_ROOT / 'templates'
DATE_FILTER_RE = re.compile(r"""\|date:(?P<quote>['"])(?P<format>.*?)(?P=quote)""")


class TemplateDateVocabularyGuardTest(SimpleTestCase):
    def test_templates_do_not_use_raw_date_display_filters(self):
        offenders = []
        for path in sorted(TEMPLATE_ROOT.rglob('*.html')):
            text = path.read_text(encoding='utf-8')
            for match in DATE_FILTER_RE.finditer(text):
                offenders.append(
                    f'{path.relative_to(PROJECT_ROOT)} uses raw '
                    f'date:"{match.group("format")}"'
                )

        self.assertEqual(
            offenders,
            [],
            msg=(
                'Use semantic date_formatting helpers from '
                '_docs/design-system.md instead of raw template date filters:\n'
                + '\n'.join(offenders)
            ),
        )

    def test_guard_regex_catches_unapproved_raw_date_filter(self):
        text = '{{ value|date:"D, M d, Y" }}'

        self.assertIsNotNone(DATE_FILTER_RE.search(text))

    def test_documentation_and_helper_names_stay_in_sync(self):
        docs = (PROJECT_ROOT / '_docs' / 'design-system.md').read_text(
            encoding='utf-8',
        )
        helper_names = [
            'member_full_date',
            'member_short_date',
            'member_compact_date',
            'member_short_datetime',
            'operator_date',
            'operator_datetime',
            'operator_datetime_seconds',
            'operator_datetime_tz',
            'form_date_value',
            'operator_time',
            'user_event_datetime',
            'event_source_short_datetime',
            'event_source_full_datetime',
        ]
        for helper_name in helper_names:
            with self.subTest(helper_name=helper_name):
                self.assertTrue(hasattr(date_formatting, helper_name))
                self.assertIn(f'`{helper_name}`', docs)
