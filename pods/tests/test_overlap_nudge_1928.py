"""Weekly overlap view and the ``Plan your first meeting`` dashboard row
(issue #1928).

Every test freezes the clock (date-rot-ok: fixed instants). The EU clocks go
back on Sunday 2026-10-25 and forward on Sunday 2027-03-28; the US clocks go
back on Sunday 2026-11-01.
"""

import datetime
from datetime import UTC
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import SimpleTestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from freezegun import freeze_time

from bookclub.models import BOOK_STATUS_CURRENT, Book, Chapter
from pods.models import PodMembership
from pods.services import config as pods_config
from pods.services.first_meeting_nudge import dashboard_pod_nudges
from pods.services.suggestions import (
    OVERLAP_LEVEL_FULL,
    OVERLAP_LEVEL_LOW,
    OVERLAP_LEVEL_MID,
    OVERLAP_LEVEL_NONE,
    MemberAvailability,
    _overlap_level,
    weekly_overlap,
)
from pods.tests.fixtures import COURSE_SLUG, make_meeting, make_pod, set_windows
from pods.tests.test_meeting_views import texts
from pods.tests.test_member_views import ENABLED, PodViewFixture

MONDAY = datetime.datetime(2026, 10, 12, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Monday
BERLIN = ZoneInfo('Europe/Berlin')
ALL_DAYS = range(7)


def member(zone, windows):
    """A :class:`MemberAvailability` with ``(weekday, 'HH:MM', 'HH:MM')`` windows."""
    rows = []
    for weekday, start, end in windows:
        sh, sm = (int(x) for x in start.split(':'))
        eh, em = (int(x) for x in end.split(':'))
        rows.append((weekday, sh * 60 + sm, eh * 60 + em, 'preferred'))
    return MemberAvailability(user=None, timezone_name=zone, windows=rows)


def cell(overlap, header, hour):
    column = [c['header'] for c in overlap['columns']].index(header)
    row = next((r for r in overlap['rows'] if r['hour'] == hour), None)
    return row['cells'][column]['count'] if row else None


class WeeklyOverlapServiceTest(SimpleTestCase):
    def test_columns_are_seven_viewer_dates_starting_today(self):
        members = [member('Europe/Berlin', [(1, '18:00', '21:00')]), member('Europe/London', [(1, '17:00', '20:00')])]
        # 23:30 UTC Monday is already Tuesday in Kolkata.
        late = datetime.datetime(2026, 10, 12, 23, 30, tzinfo=UTC)  # date-rot-ok
        berlin = weekly_overlap(members, 'Europe/Berlin', now=late)
        kolkata = weekly_overlap(members, 'Asia/Kolkata', now=late)
        self.assertEqual([c['header'] for c in berlin['columns']], [
            'Tue Oct 13', 'Wed Oct 14', 'Thu Oct 15', 'Fri Oct 16', 'Sat Oct 17', 'Sun Oct 18', 'Mon Oct 19',
        ])
        self.assertEqual(kolkata['columns'][0]['header'], 'Tue Oct 13')
        berlin_monday = weekly_overlap(members, 'Europe/Berlin', now=MONDAY)
        self.assertEqual(berlin_monday['columns'][0]['header'], 'Mon Oct 12')
        self.assertEqual(len(berlin_monday['columns']), 7)

    def test_same_pod_shifts_to_each_viewers_local_hours(self):
        # Both members are free 16:00-19:00 UTC on Tuesday Oct 13.
        members = [member('Europe/Berlin', [(1, '18:00', '21:00')]), member('Europe/London', [(1, '17:00', '20:00')])]
        berlin = weekly_overlap(members, 'Europe/Berlin', now=MONDAY)
        self.assertEqual([r['label'] for r in berlin['rows']], ['18:00', '19:00', '20:00'])
        self.assertEqual([cell(berlin, 'Tue Oct 13', h) for h in (18, 19, 20)], [2, 2, 2])

        kolkata = weekly_overlap(members, 'Asia/Kolkata', now=MONDAY)
        # 16:00-19:00 UTC is 21:30-00:30 IST: only 22:00 and 23:00 are whole hours.
        self.assertEqual([r['label'] for r in kolkata['rows']], ['22:00', '23:00'])
        self.assertEqual([cell(kolkata, 'Tue Oct 13', h) for h in (22, 23)], [2, 2])
        self.assertEqual(cell(kolkata, 'Wed Oct 14', 22), 0)

    def test_new_york_window_moves_one_berlin_hour_across_the_us_clock_change(self):
        thursday = datetime.datetime(2026, 10, 29, 8, 0, tzinfo=UTC)  # date-rot-ok: EU already on CET
        members = [
            member('America/New_York', [(d, '12:00', '13:00') for d in ALL_DAYS]),
            member('Europe/Berlin', [(d, '00:00', '24:00') for d in ALL_DAYS]),
        ]
        overlap = weekly_overlap(members, 'Europe/Berlin', now=thursday)
        # Before Sunday Nov 1 New York is UTC-4: 12:00 there is 17:00 in Berlin.
        self.assertEqual((cell(overlap, 'Sat Oct 31', 17), cell(overlap, 'Sat Oct 31', 18)), (2, 1))
        # From Nov 1 New York is UTC-5: 12:00 there is 18:00 in Berlin.
        self.assertEqual((cell(overlap, 'Sun Nov 1', 17), cell(overlap, 'Sun Nov 1', 18)), (1, 2))
        self.assertEqual((cell(overlap, 'Mon Nov 2', 17), cell(overlap, 'Mon Nov 2', 18)), (1, 2))

    def test_viewer_fall_back_day_has_no_duplicate_row_and_counts_the_first_occurrence(self):
        friday = datetime.datetime(2026, 10, 23, 8, 0, tzinfo=UTC)  # date-rot-ok
        sunday_utc = [(6, '00:00', '24:00')]
        overlap = weekly_overlap([member('UTC', sunday_utc), member('UTC', sunday_utc)], 'Europe/Berlin', now=friday)
        hours = [r['hour'] for r in overlap['rows']]
        self.assertEqual(len(hours), len(set(hours)))
        # Berlin 01:00 on Oct 25 is still CEST (23:00 UTC Saturday): nobody.
        self.assertEqual(cell(overlap, 'Sun Oct 25', 1), 0)
        # The first 02:00 (CEST) is 00:00 UTC Sunday: both free.
        self.assertEqual(cell(overlap, 'Sun Oct 25', 2), 2)
        self.assertEqual(cell(overlap, 'Sun Oct 25', 23), 2)

    def test_repeated_0200_counts_only_members_free_in_the_first_occurrence(self):
        friday = datetime.datetime(2026, 10, 23, 8, 0, tzinfo=UTC)  # date-rot-ok
        # Berlin 02:00 on Oct 25 happens twice: 00:00-01:00 UTC (CEST, first)
        # and 01:00-02:00 UTC (CET, second). A Berlin member free all Sunday
        # keeps the row; two UTC members cover only one of the two hours.
        berlin_sunday = member('Europe/Berlin', [(6, '00:00', '24:00')])
        first_only = [member('UTC', [(6, '00:00', '01:00')]) for _ in range(2)]
        second_only = [member('UTC', [(6, '01:00', '02:00')]) for _ in range(2)]
        first = weekly_overlap([berlin_sunday, *first_only], 'Europe/Berlin', now=friday)
        second = weekly_overlap([berlin_sunday, *second_only], 'Europe/Berlin', now=friday)
        self.assertEqual(cell(first, 'Sun Oct 25', 2), 3)
        self.assertEqual(cell(second, 'Sun Oct 25', 2), 1)

    def test_viewer_spring_forward_hour_is_an_empty_cell(self):
        friday = datetime.datetime(2027, 3, 26, 8, 0, tzinfo=UTC)  # date-rot-ok
        sunday_utc = [(6, '00:00', '24:00')]
        overlap = weekly_overlap([member('UTC', sunday_utc), member('UTC', sunday_utc)], 'Europe/Berlin', now=friday)
        self.assertEqual(cell(overlap, 'Sun Mar 28', 2), 0)
        self.assertEqual(cell(overlap, 'Sun Mar 28', 3), 2)

    def test_rows_are_trimmed_to_first_and_last_busy_hour_without_gaps(self):
        windows = [(1, '08:00', '09:00'), (1, '20:00', '21:00')]
        overlap = weekly_overlap([member('Europe/Berlin', windows), member('Europe/Berlin', windows)],
                                 'Europe/Berlin', now=MONDAY)
        self.assertEqual([r['hour'] for r in overlap['rows']], list(range(8, 21)))
        self.assertEqual(cell(overlap, 'Tue Oct 13', 14), 0)

    def test_partial_hour_does_not_count_and_if_needed_does(self):
        members = [
            member('Europe/Berlin', [(1, '18:30', '20:00')]),
            MemberAvailability(user=None, timezone_name='Europe/Berlin', windows=[(1, 18 * 60, 20 * 60, 'if_needed')]),
        ]
        overlap = weekly_overlap(members, 'Europe/Berlin', now=MONDAY)
        self.assertEqual((cell(overlap, 'Tue Oct 13', 18), cell(overlap, 'Tue Oct 13', 19)), (1, 2))

    def test_denominator_is_members_with_availability(self):
        members = [
            member('Europe/Berlin', [(1, '18:00', '19:00')]),
            member('Europe/Berlin', [(1, '18:00', '19:00')]),
            MemberAvailability(user=None, timezone_name='America/New_York', windows=[]),
        ]
        overlap = weekly_overlap(members, 'Europe/Berlin', now=MONDAY)
        labels = [c['label'] for c in overlap['rows'][0]['cells']]
        self.assertEqual(labels[1], 'Tuesday Oct 13, 18:00, 2 of 2 free')
        self.assertEqual(labels[0], 'Monday Oct 12, 18:00, 0 of 2 free')

    def test_fewer_than_two_members_with_availability_gives_none(self):
        members = [member('Europe/Berlin', [(1, '18:00', '19:00')]),
                   MemberAvailability(user=None, timezone_name='UTC', windows=[])]
        self.assertIsNone(weekly_overlap(members, 'Europe/Berlin', now=MONDAY))

    def test_intensity_steps(self):
        self.assertEqual([_overlap_level(n, 4) for n in range(5)], [
            OVERLAP_LEVEL_NONE, OVERLAP_LEVEL_LOW, OVERLAP_LEVEL_MID, OVERLAP_LEVEL_MID, OVERLAP_LEVEL_FULL,
        ])
        self.assertEqual(_overlap_level(1, 2), OVERLAP_LEVEL_MID)


@tag('core')
@ENABLED
@freeze_time(MONDAY)
class WeeklyOverlapPageTest(PodViewFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        set_windows(cls.anna, 'Europe/Berlin', [(1, '18:00', '21:00'), (3, '18:00', '21:00')])
        set_windows(cls.raj, 'Asia/Kolkata', [(1, '21:30', '23:30'), (3, '21:30', '23:30')])
        set_windows(cls.mike, 'Europe/London', [(1, '17:00', '20:00')])

    def pod_of(self, members):
        return make_pod(self.cohort, 'RAG evals study group', members, owner=members[0])

    def test_member_sees_closed_accordion_with_table_inside_suggested_times(self):
        pod = self.pod_of([self.anna, self.raj, self.mike])
        self.client.force_login(self.anna)
        html = self.client.get(self.pod_url(pod)).content.decode()
        section = html.split('data-testid="pod-suggested-times"')[1].split('</section>')[0]
        self.assertIn('id="suggested-times"', html.split('data-testid="pod-suggested-times"')[0][-200:])
        # The md-and-up visibility is checked at real widths in Playwright.
        self.assertIn('data-testid="pod-weekly-overlap-wrapper"', section)
        details = section.split('data-testid="pod-weekly-overlap"')[1].split('>')[0]
        self.assertNotIn('open', details)
        self.assertIn('See full weekly overlap', section)
        self.assertIn('data-testid="pod-overlap-table"', section)
        # Before ``Propose another time`` and after the slot list.
        self.assertLess(section.index('data-testid="pod-slots"'), section.index('data-testid="pod-weekly-overlap"'))
        self.assertLess(section.index('data-testid="pod-weekly-overlap"'),
                        section.index('data-testid="pod-times-propose-custom"'))

    def test_table_counts_labels_caption_and_legend(self):
        pod = self.pod_of([self.anna, self.raj, self.mike])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-overlap-caption'),
                         ['Who is free each hour, in your timezone: Europe/Berlin'])
        self.assertEqual(texts(response, 'pod-overlap-date')[:2], ['Mon Oct 12', 'Tue Oct 13'])
        self.assertEqual(texts(response, 'pod-overlap-hour'), ['18:00', '19:00', '20:00'])
        labels = texts(response, 'pod-overlap-label')
        self.assertIn('Tuesday Oct 13, 18:00, 3 of 3 free', labels)
        self.assertIn('Thursday Oct 15, 18:00, 2 of 3 free', labels)
        self.assertIn('Monday Oct 12, 18:00, 0 of 3 free', labels)
        # Zero cells show no visible number: 21 cells, 6 of them non-zero.
        self.assertEqual(len(labels), 21)
        self.assertEqual(sorted(texts(response, 'pod-overlap-count')), ['1', '2', '2', '2', '3', '3'])
        self.assertEqual(texts(response, 'pod-overlap-legend'), [
            'Numbers show how many of you are free for that whole hour. Darker means more of you. '
            'Times include "If needed" windows.',
        ])

    def test_accordion_absent_with_fewer_than_two_members_with_availability(self):
        pod = self.pod_of([self.anna, self.basic])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(len(texts(response, 'pod-times-needs-more')), 1)
        self.assertNotContains(response, 'data-testid="pod-weekly-overlap"')

    def test_honest_denominator_when_a_member_has_no_availability(self):
        pod = self.pod_of([self.anna, self.raj, self.basic])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-overlap-based-on'),
                         ["Based on 2 of 3 members. Bea S. hasn't added availability yet."])
        labels = texts(response, 'pod-overlap-label')
        self.assertTrue(labels)
        self.assertTrue(all(label.endswith('of 2 free') for label in labels), labels)

    def test_enrolled_non_member_gets_no_overlap(self):
        pod = self.pod_of([self.anna, self.raj])
        self.client.force_login(self.mike)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-join'), ['Join'])
        self.assertNotContains(response, 'pod-weekly-overlap')
        self.assertNotContains(response, 'pod-overlap-table')

    def test_staff_sees_the_table(self):
        pod = self.pod_of([self.anna, self.raj])
        self.client.force_login(self.staff)
        response = self.client.get(self.pod_url(pod))
        self.assertContains(response, 'data-testid="pod-overlap-table"')

    def test_overlap_adds_no_queries(self):
        pod = self.pod_of([self.anna, self.raj, self.mike])
        self.client.force_login(self.anna)
        self.client.get(self.pod_url(pod))
        with CaptureQueriesContext(connection) as with_overlap:
            self.client.get(self.pod_url(pod))
        with mock.patch('pods.services.pod_suggestions.weekly_overlap', return_value=None):
            with CaptureQueriesContext(connection) as without_overlap:
                response = self.client.get(self.pod_url(pod))
        self.assertNotContains(response, 'pod-weekly-overlap')
        self.assertEqual(len(with_overlap), len(without_overlap))


class NudgeDaysConfigTest(SimpleTestCase):
    def test_default_and_fallbacks(self):
        self.assertEqual(pods_config.first_meeting_nudge_days(), 3)
        for raw, expected in (('0', 0), ('30', 30), ('7', 7), ('31', 3), ('-1', 3), ('soon', 3)):
            with override_settings(PODS_FIRST_MEETING_NUDGE_DAYS=raw):
                self.assertEqual(pods_config.first_meeting_nudge_days(), expected, raw)


@tag('core')
@ENABLED
@freeze_time(MONDAY)
class FirstMeetingNudgeTest(PodViewFixture):
    def pod_of(self, members, name='Agents study group', joined_days_ago=4, **kwargs):
        pod = make_pod(self.cohort, name, members, owner=members[0], **kwargs)
        PodMembership.objects.filter(pod=pod).update(joined_at=MONDAY - datetime.timedelta(days=joined_days_ago))
        return pod

    def dashboard(self, user):
        self.client.force_login(user)
        return self.client.get('/')

    def titles(self, user):
        return texts(self.dashboard(user), 'dashboard-pod-nudge-title')

    def test_eligible_pod_shows_the_row_with_eyebrow_body_and_anchor_link(self):
        pod = self.pod_of([self.anna, self.mike])
        response = self.dashboard(self.anna)
        self.assertEqual(texts(response, 'dashboard-pod-nudge-title'), ['Plan your first meeting with Agents study group'])
        self.assertEqual(texts(response, 'dashboard-pod-nudge-body'),
                         ["No meeting planned yet. Pick a time from your pod's suggested times."])
        row = texts(response, 'dashboard-pod-nudge-row')[0]
        self.assertTrue(row.startswith('Pod'), row)
        self.assertContains(response, f'href="/courses/{COURSE_SLUG}/home/pods/{pod.pk}#suggested-times"')

    def test_second_member_joined_two_days_ago_is_too_soon(self):
        self.pod_of([self.anna, self.mike], joined_days_ago=2)
        self.assertEqual(self.titles(self.anna), [])

    def test_threshold_uses_the_second_earliest_join(self):
        pod = self.pod_of([self.anna, self.mike])
        PodMembership.objects.filter(pod=pod, user=self.mike).update(joined_at=MONDAY - datetime.timedelta(days=1))
        self.assertEqual(self.titles(self.anna), [])

    def test_pod_of_three_uses_the_second_join_not_the_first_or_last(self):
        pod = self.pod_of([self.anna, self.mike, self.raj])
        joins = {self.anna: 10, self.mike: 5, self.raj: 1}
        for user, days in joins.items():
            PodMembership.objects.filter(pod=pod, user=user).update(joined_at=MONDAY - datetime.timedelta(days=days))
        # Second join 5 days ago, last join yesterday: past the 3-day threshold.
        self.assertEqual(self.titles(self.anna), ['Plan your first meeting with Agents study group'])
        PodMembership.objects.filter(pod=pod, user=self.mike).update(joined_at=MONDAY - datetime.timedelta(days=2))
        # First join 10 days ago, second join 2 days ago: still too soon.
        self.assertEqual(self.titles(self.anna), [])

    def test_zero_days_shows_the_row_right_away(self):
        self.pod_of([self.anna, self.mike], joined_days_ago=0)
        self.assertEqual(self.titles(self.anna), [])
        with override_settings(PODS_FIRST_MEETING_NUDGE_DAYS='0'):
            self.assertEqual(self.titles(self.anna), ['Plan your first meeting with Agents study group'])

    def test_pod_of_one_gets_no_row(self):
        self.pod_of([self.anna])
        self.assertEqual(self.titles(self.anna), [])

    def test_live_meetings_suppress_the_row(self):
        for status, start in (
            ('proposed', MONDAY + datetime.timedelta(days=2)),
            ('scheduled', MONDAY + datetime.timedelta(days=2)),
            ('held', MONDAY - datetime.timedelta(days=1)),
        ):
            with self.subTest(status=status):
                pod = self.pod_of([self.anna, self.mike], name=f'Pod {status}')
                make_meeting(pod, start, status=status)
                self.assertEqual(self.titles(self.anna), [])
                pod.delete()

    def test_cancelled_or_expired_meetings_do_not_suppress_the_row(self):
        pod = self.pod_of([self.anna, self.mike])
        make_meeting(pod, MONDAY + datetime.timedelta(days=2), status='cancelled')
        make_meeting(pod, MONDAY - datetime.timedelta(days=1), status='proposed')
        self.assertEqual(self.titles(self.anna), ['Plan your first meeting with Agents study group'])

    def test_slack_link_archived_pod_and_disabled_course_hide_the_row(self):
        pod = self.pod_of([self.anna, self.mike], slack_channel_url='https://example.slack.com/archives/C1')
        self.assertEqual(self.titles(self.anna), [])
        pod.slack_channel_url = ''
        pod.status = 'archived'
        pod.save(update_fields=['slack_channel_url', 'status'])
        self.assertEqual(self.titles(self.anna), [])
        pod.status = 'open'
        pod.save(update_fields=['status'])
        self.assertEqual(self.titles(self.anna), ['Plan your first meeting with Agents study group'])
        with override_settings(PODS_COURSE_SLUGS='other-course'):
            self.assertEqual(self.titles(self.anna), [])

    def test_non_members_including_pending_and_waitlisted_get_no_row(self):
        from pods.models import REQUEST_STATUS_PENDING, REQUEST_STATUS_WAITLISTED, PodJoinRequest

        pod = self.pod_of([self.anna, self.mike])
        PodJoinRequest.objects.create(pod=pod, user=self.raj, status=REQUEST_STATUS_PENDING)
        PodJoinRequest.objects.create(pod=pod, user=self.basic, status=REQUEST_STATUS_WAITLISTED)
        for user in (self.raj, self.basic, self.outsider):
            self.assertEqual(self.titles(user), [], user.email)
        self.assertEqual(self.titles(self.mike), ['Plan your first meeting with Agents study group'])

    def test_your_week_renders_with_only_the_nudge_row(self):
        self.pod_of([self.anna, self.mike])
        response = self.dashboard(self.anna)
        week = texts(response, 'dashboard-this-week')
        self.assertEqual(len(week), 1)
        self.assertEqual(len(texts(response, 'dashboard-pod-nudge-row')), 1)
        self.assertEqual(texts(response, 'dashboard-upcoming-event-row'), [])
        self.assertEqual(texts(response, 'dashboard-pod-meeting-row'), [])

    def test_nudges_follow_dated_rows_and_precede_the_book_club_deadline(self):
        scheduled = self.pod_of([self.anna, self.raj], name='Busy pod')
        make_meeting(scheduled, MONDAY + datetime.timedelta(days=2))
        self.pod_of([self.anna, self.mike], name='Newer pod', joined_days_ago=4)
        self.pod_of([self.anna, self.basic], name='Older pod', joined_days_ago=6)
        book = Book.objects.create(
            title='Designing ML Systems', slug='dmls', author='Chip Huyen',
            status=BOOK_STATUS_CURRENT, required_level=0, start_date=MONDAY.date(),
        )
        Chapter.objects.create(book=book, number=1, title='Overview', deadline=MONDAY.date() + datetime.timedelta(days=2))
        response = self.dashboard(self.anna)
        self.assertEqual(texts(response, 'dashboard-pod-nudge-title'), [
            'Plan your first meeting with Older pod',
            'Plan your first meeting with Newer pod',
        ])
        html = response.content.decode()
        meeting = html.index('data-testid="dashboard-pod-meeting-row"')
        nudge = html.index('data-testid="dashboard-pod-nudge-row"')
        deadline = html.index('data-testid="dashboard-book-club-deadline"')
        self.assertLess(meeting, nudge)
        self.assertLess(nudge, deadline)

    def test_service_query_count_is_constant(self):
        pods = [self.pod_of([self.anna, self.mike], name=f'Pod {k}') for k in range(3)]
        make_meeting(pods[0], MONDAY - datetime.timedelta(days=2), status='cancelled')
        with self.assertNumQueries(3):
            rows = dashboard_pod_nudges(self.anna, MONDAY)
        self.assertEqual(len(rows), 3)

    def test_dashboard_query_count_does_not_grow_with_pods(self):
        self.pod_of([self.anna, self.mike], name='Pod 1')
        self.dashboard(self.anna)
        with CaptureQueriesContext(connection) as one_pod:
            response = self.client.get('/')
        self.assertEqual(len(texts(response, 'dashboard-pod-nudge-row')), 1)
        self.pod_of([self.anna, self.raj], name='Pod 2')
        self.pod_of([self.anna, self.basic], name='Pod 3')
        with CaptureQueriesContext(connection) as three_pods:
            response = self.client.get('/')
        self.assertEqual(len(texts(response, 'dashboard-pod-nudge-row')), 3)
        self.assertEqual(len(one_pod), len(three_pods))
