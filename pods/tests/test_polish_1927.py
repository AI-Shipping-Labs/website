"""Pods polish (issue #1927): honest suggested-time labels, near-duplicate
slots, re-request cooldown after a decline, and Studio's pending-only stale
badge. The daily staff Slack alert lives in ``test_stale_request_alert``."""


from django.test import SimpleTestCase, tag
from freezegun import freeze_time

from pods.services.suggestions import (
    MIN_WEEKLY_GAP_MINUTES,
    member_strip,
    minute_of_week,
    suggest_slots,
    weekly_gap,
)
from pods.tests.fixtures import make_user, set_windows
from pods.tests.test_member_views import ENABLED, PodViewFixture, _pod, texts
from pods.tests.test_suggestions import TUESDAY, member, utc

THURSDAY = 3


# --- 1. Honest suggested-time labels ------------------------------------------

@tag('core')
class HonestFitLabelTest(SimpleTestCase):
    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_label_names_how_many_had_availability_when_some_have_none(self):
        a = member('A', 'UTC', [(TUESDAY, '12:00', '13:00')])
        b = member('B', 'UTC', [(TUESDAY, '12:00', '13:00')])
        mike = member('Mike', 'America/New_York', [])
        slots = suggest_slots([a, b, mike], 60, horizon_days=7, all_members=[a, b, mike])
        self.assertEqual(slots[0].fit_label, 'All 2 with availability - works well')
        self.assertEqual(slots[0].fit_tone, 'success')

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_if_needed_variant_keeps_the_info_tone(self):
        a = member('A', 'UTC', [(TUESDAY, '12:00', '13:00')])
        b = member('B', 'UTC', [(TUESDAY, '12:00', '13:00', 'if_needed')])
        mike = member('Mike', '', [])
        slots = suggest_slots([a, b, mike], 60, horizon_days=7, all_members=[a, b, mike])
        self.assertEqual(slots[0].fit_label, 'All 2 with availability - some if needed')
        self.assertEqual(slots[0].fit_tone, 'info')

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_everyone_and_missing_labels_unchanged_when_all_have_availability(self):
        a = member('A', 'UTC', [(0, '10:00', '11:00'), (TUESDAY, '12:00', '13:00'), (THURSDAY, '10:00', '11:00')])
        b = member('B', 'UTC', [(0, '10:00', '11:00'), (TUESDAY, '12:00', '13:00'), (THURSDAY, '10:00', '11:00')])
        c = member('C', 'UTC', [(0, '10:00', '11:00', 'if_needed'), (TUESDAY, '12:00', '13:00')])
        slots = suggest_slots([a, b, c], 60, horizon_days=7, all_members=[a, b, c])
        self.assertEqual(
            sorted(s.fit_label for s in slots),
            ['Everyone - some if needed', 'Everyone - works well', 'Missing 1: C'],
        )

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_strip_lists_every_member_and_marks_those_without_availability(self):
        anna = member('Anna', 'Europe/Berlin', [(TUESDAY, '14:00', '15:00')])
        raj = member('Raj', 'UTC', [(TUESDAY, '12:00', '13:00')])
        zone_only = member('Mike', 'America/New_York', [])
        no_zone = member('Lea', '', [])
        everyone = [anna, zone_only, raj, no_zone]
        slot = suggest_slots(everyone, 60, horizon_days=7, all_members=everyone)[0]
        rows = member_strip(slot, everyone)
        self.assertEqual([r['name'] for r in rows], ['Anna', 'Mike', 'Raj', 'Lea'])
        self.assertEqual(
            [(r['name'], r.get('time'), r.get('city'), r['no_availability']) for r in rows],
            [
                ('Anna', '14:00', 'Berlin', False),
                ('Mike', None, None, True),
                ('Raj', '12:00', 'UTC', False),
                ('Lea', None, None, True),
            ],
        )


@tag('core')
@ENABLED
class HonestSuggestedTimesPageTest(PodViewFixture):
    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday so suggestions are deterministic
    def test_based_on_line_renders_above_the_slot_list(self):
        set_windows(self.anna, 'Europe/Berlin', [(TUESDAY, '18:00', '21:00')])
        set_windows(self.raj, 'Asia/Kolkata', [(TUESDAY, '21:00', '23:30')])
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, self.raj, self.mike])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        html = response.content.decode()
        self.assertLess(html.index('data-testid="pod-times-based-on"'), html.index('data-testid="pod-slots"'))
        self.assertEqual(texts(response, 'pod-slot-fit')[0], 'All 2 with availability - works well')

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_based_on_line_renders_above_the_no_fit_message(self):
        set_windows(self.anna, 'Europe/Berlin', [(1, '08:00', '09:00')])
        set_windows(self.raj, 'Europe/Berlin', [(2, '08:00', '09:00')])
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, self.raj, self.mike])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        html = response.content.decode()
        self.assertEqual(
            texts(response, 'pod-times-based-on'),
            ["Based on 2 of 3 members. Mike K. hasn't added availability yet."],
        )
        self.assertLess(html.index('data-testid="pod-times-based-on"'), html.index('data-testid="pod-times-no-fit"'))

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_member_without_timezone_is_listed_as_no_availability(self):
        no_zone = make_user('lea@test.com', first_name='Lea', last_name='Nord')
        set_windows(self.anna, 'Europe/Berlin', [(TUESDAY, '18:00', '21:00')])
        set_windows(self.raj, 'Asia/Kolkata', [(TUESDAY, '21:00', '23:30')])
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, no_zone, self.raj])
        self.client.force_login(self.anna)
        items = texts(self.client.get(self.pod_url(pod)), 'pod-slot-strip-item')
        self.assertEqual(items[:3], ['Anna K. 18:00 Berlin', 'Lea N. - no availability', 'Raj S. 21:30 Kolkata'])


# --- 4. No near-duplicate suggested times -------------------------------------

@tag('core')
class NearDuplicateSlotTest(SimpleTestCase):
    def test_weekly_gap_is_cyclic(self):
        sunday_late = minute_of_week(utc(2026, 7, 12, 23, 30))
        monday_early = minute_of_week(utc(2026, 7, 13, 0, 0))
        self.assertEqual(weekly_gap(sunday_late, monday_early), 30)
        self.assertEqual(weekly_gap(monday_early, sunday_late), 30)

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday, two week horizon
    def test_shared_tuesday_window_and_thursday_give_different_times(self):
        a = member('A', 'UTC', [(TUESDAY, '18:00', '20:00'), (THURSDAY, '10:00', '11:00')])
        b = member('B', 'UTC', [(TUESDAY, '18:00', '20:00'), (THURSDAY, '10:00', '11:00')])
        slots = suggest_slots([a, b], 60, horizon_days=14, count=5)
        starts = [s.start for s in slots]
        self.assertEqual(
            starts,
            [utc(2026, 7, 7, 18), utc(2026, 7, 9, 10), utc(2026, 7, 14, 19)],
        )

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_wrap_around_the_week_counts_as_near_duplicate(self):
        # Sunday 23:30 and Monday 00:00 the following week are 30 minutes apart
        # on the weekly cycle even though they fall on different dates.
        a = member('A', 'UTC', [(6, '23:00', '24:00'), (0, '00:00', '01:00')])
        b = member('B', 'UTC', [(6, '23:00', '24:00'), (0, '00:00', '01:00')])
        slots = suggest_slots([a, b], 30, horizon_days=14, count=10)
        weekly = [minute_of_week(s.start) for s in slots]
        for i, first in enumerate(weekly):
            for second in weekly[i + 1:]:
                self.assertGreaterEqual(weekly_gap(first, second), MIN_WEEKLY_GAP_MINUTES, slots)
        self.assertEqual(len({s.start.date() for s in slots}), len(slots))
