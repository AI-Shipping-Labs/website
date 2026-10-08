"""Availability validation and saving (issue #1918)."""

from django.http import QueryDict
from django.test import SimpleTestCase, TestCase, tag

from pods.models import AvailabilityWindow
from pods.services.availability import (
    AvailabilityError,
    WindowSpec,
    parse_window_rows,
    save_availability,
    validate_windows,
)
from pods.tests.fixtures import make_user


def spec(weekday, start, end, pref='preferred'):
    return WindowSpec(weekday, start, end, pref)


@tag('core')
class ValidateWindowsTest(SimpleTestCase):
    def assert_rejected(self, specs, message):
        with self.assertRaisesMessage(AvailabilityError, message):
            validate_windows(specs)

    def test_rejects_times_off_the_half_hour(self):
        self.assert_rejected([spec(0, 18 * 60 + 15, 20 * 60)], 'Use times on the hour or half hour')

    def test_rejects_crossing_midnight_and_empty_windows(self):
        self.assert_rejected(
            [spec(0, 22 * 60, 60)],
            'To cover time after midnight, add a second window on the next day.',
        )
        self.assert_rejected([spec(0, 600, 600)], 'End time must be after the start time.')

    def test_rejects_overlapping_windows_on_one_day(self):
        self.assert_rejected(
            [spec(1, 18 * 60, 21 * 60), spec(1, 20 * 60, 22 * 60)],
            'Windows on Tuesday overlap. Merge them into one window.',
        )

    def test_rejects_more_than_21_windows(self):
        specs = [spec(day, start * 60, start * 60 + 30) for day in range(7) for start in (8, 10, 12, 14)]
        self.assert_rejected(specs[:22], 'You can add up to 21 windows a week.')
        self.assertEqual(len(validate_windows(specs[:21])), 21)

    def test_allows_midnight_end_and_adjacent_windows(self):
        ordered = validate_windows([spec(2, 22 * 60, 1440), spec(2, 20 * 60, 22 * 60, 'if_needed')])
        self.assertEqual([(s.start_minute, s.end_minute) for s in ordered], [(1200, 1320), (1320, 1440)])

    def test_parse_rows_reads_midnight_end_and_skips_blank_rows(self):
        post = QueryDict(mutable=True)
        post.update({
            'window-1-weekday': '4', 'window-1-start': '22:00', 'window-1-end': '00:00',
            'window-1-preference': 'if_needed',
            'window-2-weekday': '5', 'window-2-start': '', 'window-2-end': '',
        })
        self.assertEqual(parse_window_rows(post), [spec(4, 1320, 1440, 'if_needed')])


@tag('core')
class SaveAvailabilityTest(TestCase):
    def test_saves_local_times_with_zone_and_fills_empty_preferred_timezone(self):
        user = make_user('anna@test.com')
        profile = save_availability(user, 'Europe/Berlin', [spec(1, 18 * 60, 21 * 60)])
        user.refresh_from_db()
        self.assertEqual(profile.timezone, 'Europe/Berlin')
        self.assertEqual(user.preferred_timezone, 'Europe/Berlin')
        self.assertEqual(
            list(AvailabilityWindow.objects.filter(profile=profile).values_list('weekday', 'start_minute', 'end_minute')),
            [(1, 1080, 1260)],
        )

    def test_keeps_existing_preferred_timezone_and_allows_clearing(self):
        user = make_user('raj@test.com', timezone_name='Asia/Kolkata')
        save_availability(user, 'Europe/Berlin', [spec(1, 600, 660)])
        profile = save_availability(user, 'Europe/Berlin', [])
        user.refresh_from_db()
        self.assertEqual(user.preferred_timezone, 'Asia/Kolkata')
        self.assertFalse(profile.windows.exists())

    def test_invalid_window_keeps_saved_windows(self):
        user = make_user('mike@test.com')
        profile = save_availability(user, 'America/New_York', [spec(1, 600, 660)])
        with self.assertRaises(AvailabilityError):
            save_availability(user, 'America/New_York', [spec(1, 600, 720), spec(1, 660, 750)])
        self.assertEqual(list(profile.windows.values_list('start_minute', flat=True)), [600])
        with self.assertRaisesMessage(AvailabilityError, 'Choose a valid timezone.'):
            save_availability(user, 'Mars/Olympus', [])
