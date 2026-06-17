from datetime import UTC, date, datetime
from types import SimpleNamespace

from django.template import Context, Template
from django.test import SimpleTestCase

from accounts.templatetags import date_formatting


class DateFormattingHelperTest(SimpleTestCase):
    def test_member_date_helpers(self):
        value = date(2026, 3, 21)
        self.assertEqual(date_formatting.member_full_date(value), 'March 21, 2026')
        self.assertEqual(date_formatting.member_short_date(value), 'Mar 21, 2026')
        self.assertEqual(date_formatting.member_compact_date(value), 'Mar 21')

    def test_member_short_datetime_helper(self):
        value = datetime(2026, 3, 21, 16, 5, tzinfo=UTC)
        self.assertEqual(
            date_formatting.member_short_datetime(value),
            'Mar 21, 2026 16:05',
        )

    def test_operator_helpers(self):
        value = datetime(2026, 3, 21, 16, 5, 7, tzinfo=UTC)
        self.assertEqual(date_formatting.operator_date(value), '2026-03-21')
        self.assertEqual(date_formatting.operator_datetime(value), '2026-03-21 16:05')
        self.assertEqual(
            date_formatting.operator_datetime_seconds(value),
            '2026-03-21 16:05:07',
        )
        self.assertEqual(
            date_formatting.operator_datetime_tz(value),
            '2026-03-21 16:05:07 UTC',
        )

    def test_form_and_split_time_helpers(self):
        value = datetime(2026, 3, 21, 16, 5, tzinfo=UTC)
        self.assertEqual(date_formatting.form_date_value(value), '2026-03-21')
        self.assertEqual(date_formatting.operator_time(value), '16:05')

    def test_empty_values_render_empty_string(self):
        helpers = [
            date_formatting.member_full_date,
            date_formatting.member_short_date,
            date_formatting.member_compact_date,
            date_formatting.member_short_datetime,
            date_formatting.operator_date,
            date_formatting.operator_datetime,
            date_formatting.operator_datetime_seconds,
            date_formatting.operator_datetime_tz,
            date_formatting.form_date_value,
            date_formatting.operator_time,
            date_formatting.event_source_short_datetime,
            date_formatting.event_source_full_datetime,
        ]
        for helper in helpers:
            with self.subTest(helper=helper.__name__):
                self.assertEqual(helper(None), '')
                self.assertEqual(helper(''), '')

    def test_user_event_datetime_delegates_to_preferred_timezone_helper(self):
        user = SimpleNamespace(preferred_timezone='America/New_York')
        value = datetime(2026, 3, 21, 16, 0, tzinfo=UTC)

        self.assertEqual(
            date_formatting.user_event_datetime(value, user),
            'March 21, 2026, 12:00 America/New_York',
        )

    def test_event_source_helpers_use_event_timezone(self):
        event = SimpleNamespace(
            start_datetime=datetime(2026, 3, 21, 16, 0, tzinfo=UTC),
            timezone='Europe/Berlin',
        )

        self.assertEqual(
            date_formatting.event_source_short_datetime(event),
            'Sat, Mar 21, 2026 · 17:00',
        )
        self.assertEqual(
            date_formatting.event_source_full_datetime(event),
            'Saturday, Mar 21, 2026 · 17:00 Europe/Berlin',
        )

    def test_filters_are_available_as_template_builtins(self):
        rendered = Template('{{ value|member_short_date }}').render(
            Context({'value': date(2026, 3, 21)})
        )

        self.assertEqual(rendered, 'Mar 21, 2026')
