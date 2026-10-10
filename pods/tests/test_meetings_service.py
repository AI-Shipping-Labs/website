"""Pod meeting rules (issue #1919): propose, agree, weekly series, timezones
and DST, limits, validation, can't make it, move, cancel, outcome, leave.

Dates are fixed under ``freeze_time`` (date-rot-ok: every test freezes the
clock): Tuesday 2026-10-13 is two weeks before the EU clock change
(Sunday 2026-10-25) and three before the US change (Sunday 2026-11-01).
"""

import datetime
import uuid
from datetime import UTC
from zoneinfo import ZoneInfo

from django.test import TestCase, tag
from freezegun import freeze_time

from notifications.models import Notification
from pods.models import (
    MEETING_RESPONSE_CANT,
    MEETING_RESPONSE_GOING,
    PodMeeting,
    PodMeetingResponse,
)
from pods.services import meetings as mtg
from pods.services import membership as svc
from pods.services.meeting_rules import meeting_state, used_meeting_count
from pods.tests.fixtures import enroll, make_cohort, make_course, make_meeting, make_pod, make_user, set_windows

BERLIN = ZoneInfo('Europe/Berlin')
NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday


def utc(*args):
    return datetime.datetime(*args, tzinfo=UTC)


def berlin(*args):
    return datetime.datetime(*args, tzinfo=BERLIN).astimezone(UTC)


class MeetingFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.anna = make_user('anna@example.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@example.com', first_name='Raj', last_name='Shah', timezone_name='Asia/Kolkata')
        cls.mike = make_user('mike@example.com', first_name='Mike', last_name='Kay', timezone_name='America/New_York')
        cls.lena = make_user('lena@example.com', first_name='Lena', last_name='Lu', timezone_name='Europe/Berlin')
        cls.staff = make_user('staff@example.com', first_name='Sam', last_name='Staff', staff=True)
        for user in (cls.anna, cls.raj, cls.mike, cls.lena):
            enroll(user, cls.cohort)

    def pod(self, members=None, **kwargs):
        members = [self.anna, self.raj, self.mike] if members is None else members
        return make_pod(self.cohort, 'RAG evals study group', members, owner=members[0] if members else None, **kwargs)

    def titles(self, user):
        return list(Notification.objects.filter(user=user, notification_type='pod_meeting').values_list('title', flat=True))


# --- Propose and agree ---------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class ProposeAndAgreeTest(MeetingFixture):
    def test_member_proposal_is_proposed_with_a_going_answer_for_the_proposer(self):
        pod = self.pod()
        start = berlin(2026, 10, 13, 18, 0)
        [meeting] = mtg.propose_meeting(pod, self.anna, start)
        self.assertEqual(meeting.status, 'proposed')
        self.assertEqual(meeting.created_via, 'member')
        self.assertEqual(meeting.timezone, 'Europe/Berlin')
        self.assertEqual(meeting.duration_minutes, 60)
        self.assertEqual(
            list(meeting.responses.values_list('user_id', 'response')),
            [(self.anna.pk, MEETING_RESPONSE_GOING)],
        )

    def test_proposal_notifies_other_members_in_their_own_timezone(self):
        pod = self.pod()
        mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        self.assertEqual(self.titles(self.anna), [])
        self.assertEqual(
            self.titles(self.raj),
            ['Anna K. proposed a time for RAG evals study group: Tue Oct 13, 21:30 Asia/Kolkata'],
        )
        self.assertEqual(
            self.titles(self.mike),
            ['Anna K. proposed a time for RAG evals study group: Tue Oct 13, 12:00 America/New_York'],
        )

    def test_pod_of_four_agrees_on_the_third_going_answer(self):
        pod = self.pod([self.anna, self.raj, self.mike, self.lena])
        [meeting] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        self.assertFalse(mtg.respond_to_meeting(meeting, self.raj, MEETING_RESPONSE_GOING))
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'proposed')
        self.assertTrue(mtg.respond_to_meeting(meeting, self.mike, MEETING_RESPONSE_GOING))
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'scheduled')
        self.assertEqual(meeting.status_changed_by, self.mike)
        self.assertIn('RAG evals study group meets Tue Oct 13, 18:00 Europe/Berlin', self.titles(self.lena))

    def test_pod_of_two_needs_both_members(self):
        pod = self.pod([self.anna, self.raj])
        [meeting] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'proposed')
        mtg.respond_to_meeting(meeting, self.raj, MEETING_RESPONSE_GOING)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'scheduled')

    def test_a_second_open_proposal_is_rejected(self):
        pod = self.pod()
        mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        with self.assertRaisesMessage(svc.PodError, 'There is already a proposed time. Confirm it or cancel it first.'):
            mtg.propose_meeting(pod, self.raj, berlin(2026, 10, 15, 18, 0))
        self.assertEqual(PodMeeting.objects.filter(pod=pod).count(), 1)

    def test_weekly_proposal_creates_every_remaining_meeting_and_agrees_them_together(self):
        pod = self.pod(meeting_count=4)
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        self.assertEqual(len(meetings), 4)
        self.assertEqual(len({m.series_id for m in meetings}), 1)
        self.assertIsNotNone(meetings[0].series_id)
        self.assertEqual(
            self.titles(self.raj),
            ['Anna K. proposed a time for RAG evals study group: Tuesdays at 21:30 Asia/Kolkata, 4 meetings'],
        )
        # Responses on any series meeting land on the first one.
        mtg.respond_to_meeting(meetings[2], self.raj, MEETING_RESPONSE_GOING)
        self.assertEqual(
            set(PodMeeting.objects.filter(pod=pod).values_list('status', flat=True)), {'scheduled'},
        )
        self.assertEqual(PodMeetingResponse.objects.filter(meeting=meetings[2]).count(), 0)

    def test_non_member_and_single_member_pods_cannot_propose(self):
        pod = self.pod()
        with self.assertRaisesMessage(svc.PodError, 'Only pod members can do this.'):
            mtg.propose_meeting(pod, self.lena, berlin(2026, 10, 13, 18, 0))
        solo = self.pod([self.anna])
        with self.assertRaisesMessage(svc.PodError, 'Meetings start when someone joins.'):
            mtg.propose_meeting(solo, self.anna, berlin(2026, 10, 13, 18, 0))


# --- Timezones and DST --------------------------------------------------------------

@tag('core')
class WeeklySeriesTimezoneTest(MeetingFixture):
    @freeze_time('2026-10-09 08:00:00')  # date-rot-ok: frozen before the EU clock change
    def test_weekly_series_keeps_berlin_wall_clock_time_across_the_october_change(self):
        pod = self.pod(meeting_count=4)
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        local = [m.starts_at.astimezone(BERLIN) for m in meetings]
        self.assertEqual([(d.month, d.day, d.hour, d.minute) for d in local],
                         [(10, 13, 18, 0), (10, 20, 18, 0), (10, 27, 18, 0), (11, 3, 18, 0)])
        self.assertEqual([m.starts_at.astimezone(UTC).hour for m in meetings], [16, 16, 17, 17])

    def test_repeat_in_a_spring_forward_gap_moves_forward_by_the_gap(self):
        # Berlin skips 02:00-03:00 on Sunday 2026-03-29.
        anchor = berlin(2026, 3, 22, 2, 30)  # date-rot-ok: DST fixture
        repeat = mtg.weekly_start(anchor, 'Europe/Berlin', 1)
        self.assertEqual(repeat, utc(2026, 3, 29, 1, 30))  # date-rot-ok: DST fixture
        self.assertEqual(repeat.astimezone(BERLIN).strftime('%H:%M %Z'), '03:30 CEST')
        # The week after the gap is back on the anchor's wall-clock time.
        self.assertEqual(mtg.weekly_start(anchor, 'Europe/Berlin', 2).astimezone(BERLIN).strftime('%H:%M'), '02:30')

    def test_an_ambiguous_fall_back_time_uses_the_first_occurrence(self):
        # 02:30 happens twice in Berlin on Sunday 2026-10-25.
        anchor = berlin(2026, 10, 18, 2, 30)  # date-rot-ok: DST fixture
        repeat = mtg.weekly_start(anchor, 'Europe/Berlin', 1)
        self.assertEqual(repeat, utc(2026, 10, 25, 0, 30))  # date-rot-ok: DST fixture
        self.assertEqual(repeat.astimezone(BERLIN).strftime('%H:%M %Z'), '02:30 CEST')

    def test_a_custom_time_inside_the_gap_is_read_as_the_shifted_time(self):
        start = mtg.parse_local('2026-03-29', '02:30', 'Europe/Berlin')  # date-rot-ok: DST fixture
        self.assertEqual(start.astimezone(BERLIN).strftime('%Y-%m-%d %H:%M'), '2026-03-29 03:30')

    @freeze_time('2026-10-09 08:00:00')  # date-rot-ok: frozen
    def test_quarter_offset_zone_keeps_its_local_time_and_passes_the_grid(self):
        # Asia/Kathmandu is GMT+05:45: 18:45 local is 13:00 UTC.
        start = mtg.parse_local('2026-10-13', '18:45', 'Asia/Kathmandu')
        self.assertEqual(start, utc(2026, 10, 13, 13, 0))
        pod = self.pod([self.anna, self.raj], meeting_count=3)
        meetings = mtg.propose_meeting(pod, self.raj, start, repeat_weekly=True, zone_name='Asia/Kathmandu')
        self.assertEqual(
            [m.starts_at.astimezone(ZoneInfo('Asia/Kathmandu')).strftime('%a %H:%M') for m in meetings],
            ['Tue 18:45', 'Tue 18:45', 'Tue 18:45'],
        )
        with self.assertRaisesMessage(svc.PodError, 'Pick a start time on the quarter hour.'):
            mtg.parse_local('2026-10-13', '18:50', 'Asia/Kathmandu')

    @freeze_time('2026-10-09 08:00:00')  # date-rot-ok: frozen
    def test_local_midnight_series_stays_on_its_local_day(self):
        # 00:00 Tuesday in Tokyo is Monday 15:00 UTC; each repeat keeps the local Tuesday.
        start = mtg.parse_local('2026-10-13', '00:00', 'Asia/Tokyo')
        self.assertEqual(start, utc(2026, 10, 12, 15, 0))
        pod = self.pod([self.anna, self.raj], meeting_count=2)
        meetings = mtg.propose_meeting(pod, self.anna, start, repeat_weekly=True, zone_name='Asia/Tokyo')
        self.assertEqual(
            [m.starts_at.astimezone(ZoneInfo('Asia/Tokyo')).strftime('%a %d %H:%M') for m in meetings],
            ['Tue 13 00:00', 'Tue 20 00:00'],
        )
        # Raj reads the series on his own weekday (Monday 20:30 in Kolkata).
        self.assertEqual(
            self.titles(self.raj),
            ['Anna K. proposed a time for RAG evals study group: Mondays at 20:30 Asia/Kolkata, 2 meetings'],
        )

    @freeze_time('2026-10-09 08:00:00')  # date-rot-ok: frozen
    def test_repeat_that_overlaps_an_existing_meeting_skips_a_week(self):
        pod = self.pod(meeting_count=4)
        make_meeting(pod, berlin(2026, 10, 20, 18, 30))
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        self.assertEqual(
            [m.starts_at.astimezone(BERLIN).strftime('%m-%d') for m in meetings],
            ['10-13', '10-27', '11-03'],
        )


# --- Limits, expiry and validation ---------------------------------------------------

@tag('core')
@freeze_time(NOW)
class LimitsAndValidationTest(MeetingFixture):
    def test_creating_beyond_meeting_count_fails(self):
        pod = self.pod(meeting_count=2)
        make_meeting(pod, NOW - datetime.timedelta(days=7), status='held')
        make_meeting(pod, NOW + datetime.timedelta(days=3))
        with self.assertRaisesMessage(
            svc.PodError,
            'All 2 meetings are planned. Cancel one or raise the number of meetings to plan another.',
        ) as ctx:
            mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 20, 18, 0))
        self.assertEqual(ctx.exception.code, 'meeting_limit_reached')

    def test_lowering_meeting_count_below_used_meetings_fails(self):
        pod = self.pod(meeting_count=4)
        make_meeting(pod, NOW - datetime.timedelta(days=7), status='held')
        make_meeting(pod, NOW + datetime.timedelta(days=3))
        make_meeting(pod, NOW + datetime.timedelta(days=4), status='proposed')
        make_meeting(pod, NOW + datetime.timedelta(days=5), status='cancelled')
        with self.assertRaisesMessage(
            svc.PodError,
            'This pod already has 3 meetings planned or held. Cancel a meeting before lowering the number.',
        ):
            svc.update_pod(pod, {'meeting_count': '2'}, actor=self.anna)
        pod.refresh_from_db()
        self.assertEqual(pod.meeting_count, 4)
        svc.update_pod(pod, {'meeting_count': '3'}, actor=self.anna)
        pod.refresh_from_db()
        self.assertEqual(pod.meeting_count, 3)

    def test_expired_proposal_does_not_count_or_block_and_has_no_number(self):
        pod = self.pod(meeting_count=1)
        expired = make_meeting(pod, NOW - datetime.timedelta(hours=1), status='proposed')
        state = meeting_state(pod)
        self.assertEqual(state.used, 0)
        self.assertNotIn(expired.pk, state.numbers)
        [new] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        self.assertEqual(meeting_state(pod).numbers[new.pk], 1)

    def test_a_proposed_series_expires_with_its_first_meeting(self):
        pod = self.pod(meeting_count=3)
        series = uuid.uuid4()
        make_meeting(pod, NOW - datetime.timedelta(hours=2), status='proposed', series_id=series)
        make_meeting(pod, NOW + datetime.timedelta(days=7), status='proposed', series_id=series)
        self.assertEqual(used_meeting_count(pod), 0)
        self.assertFalse(meeting_state(pod).has_open_proposal)

    def test_start_time_rules(self):
        pod = self.pod()
        cases = [
            (NOW + datetime.timedelta(minutes=45), 'Pick a time at least 1 hour from now and within the next 6 months.'),
            (NOW + datetime.timedelta(days=181), 'Pick a time at least 1 hour from now and within the next 6 months.'),
            (utc(2026, 10, 13, 16, 10), 'Pick a start time on the quarter hour.'),
        ]
        for start, message in cases:
            with self.subTest(start=start), self.assertRaisesMessage(svc.PodError, message):
                mtg.propose_meeting(pod, self.anna, start)

    def test_overlap_names_the_meeting_in_the_actors_zone(self):
        pod = self.pod()
        make_meeting(pod, berlin(2026, 10, 14, 18, 0))
        with self.assertRaisesMessage(
            svc.PodError, 'This overlaps meeting 1 (Wed Oct 14, 18:00 Europe/Berlin). Pick another time.',
        ):
            mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 14, 18, 30))
        # Back-to-back is fine: intervals are half-open.
        mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 14, 19, 0))


# --- Can't make it, move, cancel, outcome ---------------------------------------------

@tag('core')
@freeze_time(NOW)
class ChangeMeetingTest(MeetingFixture):
    def series(self, pod, count=4):
        series_id = uuid.uuid4()
        return [
            make_meeting(pod, berlin(2026, 10, 13 + 7 * k, 18, 0) if k < 3 else berlin(2026, 11, 3, 18, 0),
                         zone='Europe/Berlin', series_id=series_id)
            for k in range(count)
        ]

    def test_cant_make_it_stores_the_answer_and_notifies_the_others(self):
        pod = self.pod()
        meetings = self.series(pod)
        mtg.respond_to_meeting(meetings[1], self.mike, MEETING_RESPONSE_CANT)
        self.assertEqual(meetings[1].responses.get(user=self.mike).response, MEETING_RESPONSE_CANT)
        self.assertEqual(self.titles(self.anna), ["Mike K. can't make RAG evals study group meeting 2"])
        self.assertEqual(self.titles(self.mike), [])
        # A repeated click does not notify twice.
        mtg.respond_to_meeting(meetings[1], self.mike, MEETING_RESPONSE_CANT)
        self.assertEqual(len(self.titles(self.anna)), 1)

    def test_one_tap_move_moves_only_this_meeting_and_resets_its_state(self):
        pod = self.pod()
        meetings = self.series(pod)
        PodMeeting.objects.filter(pk=meetings[1].pk).update(reminder_sent_at=NOW)
        mtg.respond_to_meeting(meetings[1], self.mike, MEETING_RESPONSE_CANT)
        new_start = berlin(2026, 10, 22, 18, 0)
        mtg.move_meeting(meetings[1], self.anna, new_start)
        moved = PodMeeting.objects.get(pk=meetings[1].pk)
        self.assertEqual(moved.starts_at, new_start)
        self.assertEqual(moved.previous_starts_at, berlin(2026, 10, 20, 18, 0))
        self.assertEqual((moved.moved_by, moved.moved_at, moved.status), (self.anna, NOW, 'scheduled'))
        self.assertIsNone(moved.reminder_sent_at)
        self.assertFalse(moved.responses.filter(response=MEETING_RESPONSE_CANT).exists())
        for later in meetings[2:]:
            self.assertEqual(PodMeeting.objects.get(pk=later.pk).starts_at, later.starts_at)
        self.assertIn(
            'Anna K. moved RAG evals study group meeting 2 to Thu Oct 22, 12:00 America/New_York',
            self.titles(self.mike),
        )

    def test_move_later_moves_the_rest_of_the_series_in_the_movers_zone_with_one_notification(self):
        pod = self.pod()
        meetings = self.series(pod)
        PodMeeting.objects.filter(pk=meetings[0].pk).update(starts_at=NOW - datetime.timedelta(days=2), status='held')
        # Raj picks Thursday 17:00 in Kolkata for meeting 2 and the rest.
        new_start = mtg.parse_local('2026-10-22', '17:00', 'Asia/Kolkata')
        mtg.move_meeting(meetings[1], self.raj, new_start, move_later=True)
        kolkata = ZoneInfo('Asia/Kolkata')
        starts = [PodMeeting.objects.get(pk=m.pk).starts_at.astimezone(kolkata) for m in meetings[1:]]
        self.assertEqual([s.strftime('%a %m-%d %H:%M') for s in starts],
                         ['Thu 10-22 17:00', 'Thu 10-29 17:00', 'Thu 11-05 17:00'])
        self.assertEqual(
            {PodMeeting.objects.get(pk=m.pk).timezone for m in meetings[1:]}, {'Asia/Kolkata'},
        )
        self.assertEqual(PodMeeting.objects.get(pk=meetings[0].pk).status, 'held')
        anna_titles = self.titles(self.anna)
        self.assertEqual(len(anna_titles), 1)
        self.assertEqual(
            anna_titles[0],
            'Raj S. moved RAG evals study group meeting 2 to Thu Oct 22, 13:30 Europe/Berlin and the later meetings',
        )

    def test_move_later_is_all_or_nothing_on_overlap(self):
        pod = self.pod(meeting_count=5)
        meetings = self.series(pod)
        blocker = make_meeting(pod, berlin(2026, 10, 29, 18, 0), zone='Europe/Berlin')
        with self.assertRaisesMessage(svc.PodError, 'This overlaps meeting'):
            mtg.move_meeting(meetings[1], self.anna, berlin(2026, 10, 22, 18, 0), move_later=True)
        for meeting in meetings:
            self.assertEqual(PodMeeting.objects.get(pk=meeting.pk).starts_at, meeting.starts_at)
        self.assertTrue(PodMeeting.objects.filter(pk=blocker.pk).exists())

    def test_moving_a_proposal_resets_answers_to_the_mover(self):
        pod = self.pod([self.anna, self.raj, self.mike, self.lena])
        [meeting] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        mtg.respond_to_meeting(meeting, self.raj, MEETING_RESPONSE_GOING)
        mtg.move_meeting(meeting, self.mike, berlin(2026, 10, 15, 18, 0))
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'proposed')
        self.assertEqual(
            list(meeting.responses.values_list('user_id', 'response')), [(self.mike.pk, MEETING_RESPONSE_GOING)],
        )

    def test_past_held_cancelled_and_expired_meetings_cannot_change(self):
        pod = self.pod()
        past = make_meeting(pod, NOW - datetime.timedelta(hours=3))
        held = make_meeting(pod, NOW - datetime.timedelta(days=7), status='held')
        cancelled = make_meeting(pod, NOW + datetime.timedelta(days=2), status='cancelled')
        expired = make_meeting(pod, NOW - datetime.timedelta(hours=1), status='proposed')
        target = berlin(2026, 10, 16, 18, 0)
        with self.assertRaisesMessage(svc.PodError, 'This meeting has already happened.') as ctx:
            mtg.move_meeting(past, self.anna, target)
        self.assertEqual(ctx.exception.code, 'meeting_not_movable')
        for meeting in (held, cancelled, expired):
            with self.subTest(status=meeting.status):
                with self.assertRaisesMessage(svc.PodError, 'This meeting can no longer be changed.'):
                    mtg.move_meeting(meeting, self.anna, target)
                with self.assertRaisesMessage(svc.PodError, 'This meeting can no longer be changed.'):
                    mtg.respond_to_meeting(meeting, self.anna, MEETING_RESPONSE_GOING)
        self.assertFalse(PodMeetingResponse.objects.filter(meeting__pod=pod).exists())

    def test_cancelling_a_proposed_series_cancels_the_whole_proposal(self):
        pod = self.pod()
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        mtg.cancel_meeting(meetings[0], self.raj)
        self.assertEqual(set(PodMeeting.objects.filter(pod=pod).values_list('status', flat=True)), {'cancelled'})
        self.assertIn('Raj S. cancelled RAG evals study group meeting 1', self.titles(self.anna))

    def test_outcome_only_after_the_start(self):
        pod = self.pod()
        future = make_meeting(pod, NOW + datetime.timedelta(hours=2))
        with self.assertRaisesMessage(svc.PodError, 'This meeting has not started yet.'):
            mtg.record_outcome(future, self.anna, mtg.OUTCOME_HELD)
        started = make_meeting(pod, NOW - datetime.timedelta(hours=2))
        mtg.record_outcome(started, self.mike, mtg.OUTCOME_HELD)
        started.refresh_from_db()
        self.assertEqual((started.status, started.status_changed_by), ('held', self.mike))
        missed = make_meeting(pod, NOW - datetime.timedelta(days=1))
        mtg.record_outcome(missed, self.mike, mtg.OUTCOME_NOT_HELD)
        missed.refresh_from_db()
        self.assertEqual(missed.status, 'cancelled')
        self.assertEqual(meeting_state(pod).held, 1)
        with self.assertRaisesMessage(svc.PodError, 'This meeting can no longer be changed.'):
            mtg.record_outcome(started, self.anna, mtg.OUTCOME_NOT_HELD)


# --- Leaving, archiving, call link ------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class LeaveAndCallLinkTest(MeetingFixture):
    def test_leaving_deletes_only_future_answers(self):
        pod = self.pod()
        past = make_meeting(pod, NOW - datetime.timedelta(days=3), status='held')
        future = make_meeting(pod, NOW + datetime.timedelta(days=3))
        PodMeetingResponse.objects.create(meeting=past, user=self.mike, response=MEETING_RESPONSE_GOING)
        PodMeetingResponse.objects.create(meeting=future, user=self.mike, response=MEETING_RESPONSE_CANT)
        svc.remove_member(pod, self.mike, actor=self.mike)
        self.assertEqual(
            list(PodMeetingResponse.objects.filter(user=self.mike).values_list('meeting_id', flat=True)), [past.pk],
        )

    def test_emptied_member_pod_cancels_its_future_meetings(self):
        pod = self.pod([self.anna], source='member')
        past = make_meeting(pod, NOW - datetime.timedelta(days=3), status='held')
        future = make_meeting(pod, NOW + datetime.timedelta(days=3))
        proposal = make_meeting(pod, NOW + datetime.timedelta(days=5), status='proposed')
        svc.remove_member(pod, self.anna, actor=self.anna)
        statuses = dict(PodMeeting.objects.filter(pod=pod).values_list('pk', 'status'))
        self.assertEqual(statuses, {past.pk: 'held', future.pk: 'cancelled', proposal.pk: 'cancelled'})

    def test_call_link_must_be_https_and_at_most_500_characters(self):
        pod = self.pod()
        message = 'Paste an https link to your call, for example https://meet.google.com/abc-defg-hij.'
        for raw in ('http://meet.google.com/abc', 'javascript:alert(1)', 'meet.google.com/abc',
                    'https://' + 'a' * 490 + '.com'):
            with self.subTest(raw=raw[:30]), self.assertRaisesMessage(svc.PodError, message):
                mtg.set_call_link(pod, self.anna, raw)
        self.assertEqual(mtg.set_call_link(pod, self.anna, ' https://meet.google.com/abc-defg-hij '),
                         'https://meet.google.com/abc-defg-hij')
        pod.refresh_from_db()
        self.assertEqual(mtg.call_link_label(pod.meeting_url), 'meet.google.com/abc-defg-hij')
        mtg.set_call_link(pod, self.anna, '')
        pod.refresh_from_db()
        self.assertEqual(pod.meeting_url, '')
        with self.assertRaisesMessage(svc.PodError, 'Only pod members can do this.'):
            mtg.set_call_link(pod, self.lena, 'https://meet.google.com/x')


# --- Staff scheduling and status ---------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class StaffScheduleTest(MeetingFixture):
    def test_staff_schedule_creates_scheduled_weekly_meetings_and_notifies_everyone(self):
        pod = self.pod([self.anna, self.raj])
        meetings = mtg.schedule_meetings(
            pod, self.staff, berlin(2026, 10, 15, 17, 0), zone_name='Europe/Berlin',
            repeat_weekly=True, created_via='studio',
        )
        self.assertEqual(len(meetings), 4)
        self.assertEqual({(m.status, m.created_via) for m in meetings}, {('scheduled', 'studio')})
        self.assertEqual(self.titles(self.anna), ['RAG evals study group meets Thu Oct 15, 17:00 Europe/Berlin'])
        self.assertEqual(Notification.objects.get(user=self.raj).body, 'Weekly, 4 meetings.')

    def test_staff_schedule_respects_the_limit_and_count_rules(self):
        pod = self.pod(meeting_count=2)
        with self.assertRaisesMessage(svc.PodError, 'All 2 meetings are planned.') as ctx:
            mtg.schedule_meetings(pod, self.staff, berlin(2026, 10, 15, 17, 0), zone_name='UTC',
                                  repeat_weekly=True, count=3, created_via='api')
        self.assertEqual(ctx.exception.code, 'meeting_limit_reached')
        with self.assertRaisesMessage(svc.PodError, 'Use repeat_weekly to create more than one meeting.'):
            mtg.schedule_meetings(pod, self.staff, berlin(2026, 10, 15, 17, 0), zone_name='UTC',
                                  count=2, created_via='api')
        with self.assertRaisesMessage(svc.PodError, 'Choose a valid timezone.'):
            mtg.schedule_meetings(pod, self.staff, berlin(2026, 10, 15, 17, 0), zone_name='Mars/Base',
                                  created_via='api')

    def test_staff_can_restore_a_cancelled_meeting_only_within_the_limit(self):
        pod = self.pod(meeting_count=1)
        cancelled = make_meeting(pod, NOW + datetime.timedelta(days=2), status='cancelled')
        make_meeting(pod, NOW + datetime.timedelta(days=4))
        with self.assertRaisesMessage(svc.PodError, 'All 1 meetings are planned.'):
            mtg.set_meeting_status(cancelled, self.staff, 'scheduled')
        # Issue #1934: a future meeting is never marked held, even by staff.
        with self.assertRaisesMessage(svc.PodError, 'This meeting has not started yet.'):
            mtg.set_meeting_status(cancelled, self.staff, 'held')
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, 'cancelled')
        started = make_meeting(pod, NOW - datetime.timedelta(hours=2), status='cancelled')
        mtg.set_meeting_status(started, self.staff, 'held')
        started.refresh_from_db()
        self.assertEqual(started.status, 'held')


class MeetingMergeTest(MeetingFixture):
    """Account merge keeps the canonical member's answer (unique per meeting)."""

    @freeze_time(NOW)
    def test_merge_keeps_canonical_answer_and_repoints_the_rest(self):
        from accounts.services.account_merge import merge_accounts

        pod = self.pod()
        meeting = make_meeting(pod, NOW + datetime.timedelta(days=3), proposed_by=self.lena)
        other = make_meeting(pod, NOW + datetime.timedelta(days=10))
        PodMeetingResponse.objects.create(meeting=meeting, user=self.anna, response=MEETING_RESPONSE_GOING)
        PodMeetingResponse.objects.create(meeting=meeting, user=self.lena, response=MEETING_RESPONSE_CANT)
        PodMeetingResponse.objects.create(meeting=other, user=self.lena, response=MEETING_RESPONSE_CANT)
        merge_accounts(self.anna, self.lena, actor_label='test', actor=self.staff)
        self.assertEqual(
            sorted(PodMeetingResponse.objects.filter(user=self.anna).values_list('meeting_id', 'response')),
            sorted([(meeting.pk, MEETING_RESPONSE_GOING), (other.pk, MEETING_RESPONSE_CANT)]),
        )
        meeting.refresh_from_db()
        self.assertEqual(meeting.proposed_by, self.anna)


@tag('core')
@freeze_time(NOW)
class NextBestSlotTest(MeetingFixture):
    """The offer for a meeting stays between its live neighbours (issue #1934:
    any live pod meeting, not only series siblings)."""

    def members(self, pod, weekdays):
        from pods.services.suggestions import load_member_availability

        users = [m.user for m in pod.memberships.select_related('user').order_by('joined_at', 'pk')]
        for user in users:
            set_windows(user, 'UTC', [(d, '16:00', '17:30') for d in weekdays])
        availability = load_member_availability(users)
        return [availability[u.pk] for u in users]

    def test_candidates_skip_near_overlapping_and_out_of_order_slots(self):
        from pods.services.meeting_presentation import next_best_slot

        pod = self.pod()
        series = uuid.uuid4()
        for day in (12, 19, 26):
            make_meeting(pod, utc(2026, 10, day, 16, 0), series_id=series)
        second = PodMeeting.objects.get(pod=pod, starts_at=utc(2026, 10, 19, 16, 0))
        # A one-off meeting is a neighbour too: nothing at or after Oct 21 16:00.
        make_meeting(pod, utc(2026, 10, 21, 16, 0))
        state = meeting_state(pod)
        members = self.members(pod, range(7))
        # Oct 19 starts are within 60 minutes of the meeting, Oct 21 and later
        # pass the next meeting, so Tue Oct 20 16:00 (same week, 1 day) wins.
        self.assertEqual(next_best_slot(second, [second], state, members, now=NOW).start, utc(2026, 10, 20, 16, 0))
        # Mondays only: Oct 12 overlaps the previous meeting, Oct 19 is too
        # close, Oct 26 is after the next one.
        members = self.members(pod, [0])
        self.assertIsNone(next_best_slot(second, [second], state, members, now=NOW))


@tag('core')
@freeze_time(NOW)
class StaffStatusOnProposalTest(MeetingFixture):
    """Explicit staff-override rules for proposals."""

    def test_held_is_refused_for_a_proposal_and_cancel_takes_the_whole_proposal(self):
        pod = self.pod()
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        with self.assertRaisesMessage(svc.PodError, 'Confirm the proposed time before marking it as held.'):
            mtg.set_meeting_status(meetings[0], self.staff, 'held')
        expired = make_meeting(pod, NOW - datetime.timedelta(hours=3), status='proposed')
        with self.assertRaisesMessage(svc.PodError, 'Confirm the proposed time before marking it as held.'):
            mtg.set_meeting_status(expired, self.staff, 'held')
        self.assertEqual(set(PodMeeting.objects.filter(pod=pod).values_list('status', flat=True)), {'proposed'})
        mtg.set_meeting_status(meetings[2], self.staff, 'cancelled')
        self.assertEqual(
            list(PodMeeting.objects.filter(series_id=meetings[0].series_id).values_list('status', flat=True)),
            ['cancelled'] * 4,
        )
        mtg.set_meeting_status(expired, self.staff, 'scheduled')
        mtg.set_meeting_status(expired, self.staff, 'held')
        expired.refresh_from_db()
        self.assertEqual(expired.status, 'held')
