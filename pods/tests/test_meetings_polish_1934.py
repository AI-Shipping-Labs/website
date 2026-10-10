"""Pod meetings polish (issue #1934): the visible confirmation rule, the
next best time near the meeting, the ``Waiting on`` hint, unanswered
proposals on the dashboard, the call link affordance and clock-change
wording.

Every test freezes the clock (date-rot-ok: fixed Friday 2026-10-09 08:00
UTC). The EU clocks go back on Sunday 2026-10-25, the US clocks on Sunday
2026-11-01; in spring the US changes on 2027-03-14 and the EU on
2027-03-28.
"""

import datetime
import re
import uuid
from datetime import UTC
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings, tag
from freezegun import freeze_time

from accounts.templatetags.accounts_extras import button_classes
from pods.models import MEETING_RESPONSE_CANT, MEETING_RESPONSE_GOING, PodMeeting, PodMeetingResponse
from pods.services import meetings as mtg
from pods.services.meeting_presentation import dst_lines, next_best_slot
from pods.services.meeting_rules import meeting_state, members_without_answer
from pods.services.suggestions import load_member_availability
from pods.tests.fixtures import COURSE_SLUG, make_meeting, make_pod, make_user, set_windows
from pods.tests.test_meeting_views import MeetingViewFixture, texts

NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday
BERLIN = ZoneInfo('Europe/Berlin')
KATHMANDU = ZoneInfo('Asia/Kathmandu')


def local(zone, *args):
    return datetime.datetime(*args, tzinfo=zone).astimezone(UTC)


def berlin(*args):
    return local(BERLIN, *args)


def avail(pod):
    users = [m.user for m in pod.memberships.select_related('user').order_by('joined_at', 'pk')]
    availability = load_member_availability(users)
    return [availability[u.pk] for u in users]


def row_html(response, meeting):
    rows = response.content.decode().split('data-testid="pod-meeting"')
    return next(r for r in rows if f'data-meeting-id="{meeting.pk}"' in r)


class PolishFixture(MeetingViewFixture):
    def pod_of(self, members, **kwargs):
        return make_pod(self.cohort, 'RAG evals study group', members, owner=members[0], **kwargs)

    def everyone(self, zone, windows, users=None):
        for user in users or (self.anna, self.raj, self.mike):
            set_windows(user, zone, windows)


# --- 1. The confirmation rule -------------------------------------------------------

@tag('core')
@override_settings(PODS_COURSE_SLUGS=COURSE_SLUG, SLACK_TEAM_ID='T0TEAM')
@freeze_time(NOW)
class ConfirmationRuleTest(PolishFixture):
    def test_badge_counts_against_the_needed_members(self):
        pair = self.pod_of([self.anna, self.mike])
        mtg.propose_meeting(pair, self.anna, berlin(2026, 10, 13, 18, 0))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pair))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Proposed - 1 of 2 needed'])
        four = self.pod_of([self.anna, self.raj, self.mike, self.basic])
        mtg.propose_meeting(four, self.anna, berlin(2026, 10, 14, 18, 0))
        response = self.client.get(self.pod_url(four))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Proposed - 1 of 3 needed'])

    def test_propose_page_states_the_rule_above_the_repeat_checkbox(self):
        pair = self.pod_of([self.anna, self.mike])
        four = self.pod_of([self.anna, self.raj, self.mike, self.basic])
        self.client.force_login(self.anna)
        start = berlin(2026, 10, 13, 18, 0).isoformat()
        for pod, line in ((pair, 'Confirmed once 2 of you can make it.'), (four, 'Confirmed once 3 of you can make it.')):
            for url in (self.meetings_url(pod, '/new'), self.meetings_url(pod, '/new') + f'?start={start}'):
                with self.subTest(url=url):
                    response = self.client.get(url.replace('+', '%2B'))
                    self.assertEqual(texts(response, 'pod-meeting-form-rule'), [line])
                    html = response.content.decode()
                    custom_or_slot = 'pod-meeting-form-slot' if '?start=' in url else 'pod-meeting-custom'
                    self.assertIn(f'data-testid="{custom_or_slot}"', html)
                    positions = [html.index(f'data-testid="{t}"') for t in
                                 (custom_or_slot, 'pod-meeting-form-rule', 'pod-meeting-repeat')]
                    self.assertEqual(positions, sorted(positions))

    def test_change_time_page_states_the_rule_only_for_a_proposal(self):
        pod = self.pod_of([self.anna, self.mike])
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        self.client.force_login(self.mike)
        response = self.client.get(self.meetings_url(pod, f'/{proposal.pk}/move'))
        self.assertEqual(texts(response, 'pod-meeting-form-rule'), ['Confirmed once 2 of you can make it.'])
        scheduled = make_meeting(pod, berlin(2026, 10, 20, 18, 0), zone='Europe/Berlin')
        response = self.client.get(self.meetings_url(pod, f'/{scheduled.pk}/move'))
        self.assertEqual(texts(response, 'pod-meeting-form-current'), ['Now: Tue Oct 20, 12:00 America/New_York'])
        self.assertEqual(texts(response, 'pod-meeting-form-rule'), [])


# --- 2. Next best time near the meeting ----------------------------------------------

@tag('core')
@freeze_time(NOW)
class NextBestNearTheMeetingTest(PolishFixture):
    def offer(self, pod, meeting):
        state = meeting_state(pod, NOW)
        group = [meeting]
        slot = next_best_slot(meeting, group, state, avail(pod), now=NOW)
        return slot.start if slot else None

    def test_worked_example_offers_the_same_week_not_the_week_before(self):
        pod = self.pod_of([self.anna, self.mike])
        # Tue and Wed 18:00-19:00 Berlin for both members.
        self.everyone('Europe/Berlin', [(1, '18:00', '19:00'), (2, '18:00', '19:00')], [self.anna, self.mike])
        series = uuid.uuid4()
        make_meeting(pod, berlin(2026, 10, 13, 18, 0), zone='Europe/Berlin', series_id=series)
        second = make_meeting(pod, berlin(2026, 10, 20, 18, 0), zone='Europe/Berlin', series_id=series)
        # Wed Oct 14 is free too, but Wed Oct 21 is in meeting 2's own week.
        self.assertEqual(self.offer(pod, second), berlin(2026, 10, 21, 18, 0))

    def test_same_week_wins_over_a_closer_day_in_the_neighbouring_week(self):
        pod = self.pod_of([self.anna, self.mike])
        # Sundays and Thursdays; the meeting is Monday Oct 19.
        self.everyone('Europe/Berlin', [(6, '18:00', '19:00'), (3, '18:00', '19:00'), (0, '18:00', '19:00')],
                      [self.anna, self.mike])
        meeting = make_meeting(pod, berlin(2026, 10, 19, 18, 0), zone='Europe/Berlin')
        # Sun Oct 18 is one day away but in the week before; Thu Oct 22 is in the same week.
        self.assertEqual(self.offer(pod, meeting), berlin(2026, 10, 22, 18, 0))

    def test_week_is_counted_in_the_meetings_own_zone_at_plus_05_45(self):
        pod = self.pod_of([self.anna, self.mike])
        self.everyone('Asia/Kathmandu', [(6, '20:00', '21:00'), (5, '20:00', '21:00'), (0, '01:00', '02:00')],
                      [self.anna, self.mike])
        # Monday 01:00 in Kathmandu is still Sunday 19:15 in UTC.
        meeting = make_meeting(pod, local(KATHMANDU, 2026, 10, 19, 1, 0), zone='Asia/Kathmandu')
        self.assertEqual(meeting.starts_at, datetime.datetime(2026, 10, 18, 19, 15, tzinfo=UTC))
        # Sun Oct 18 20:00 Kathmandu is the same UTC day but the week before in
        # Kathmandu; Sat Oct 24 is the meeting's own Kathmandu week.
        self.assertEqual(self.offer(pod, meeting), local(KATHMANDU, 2026, 10, 24, 20, 0))

    def test_clock_change_week_keeps_the_local_time_and_the_meetings_week(self):
        pod = self.pod_of([self.anna, self.mike])
        self.everyone('Europe/Berlin', [(5, '18:00', '19:00'), (6, '18:00', '19:00'), (1, '18:00', '19:00')],
                      [self.anna, self.mike])
        # Tue Oct 27 is the first week after the EU clocks go back.
        meeting = make_meeting(pod, berlin(2026, 10, 27, 18, 0), zone='Europe/Berlin')
        offer = self.offer(pod, meeting)
        # Sat Oct 24 (CEST) is 3 days away in the week before; Sat Oct 31 is
        # in the same week, still at 18:00 Berlin after the change.
        self.assertEqual(offer, berlin(2026, 10, 31, 18, 0))
        self.assertEqual(offer, datetime.datetime(2026, 10, 31, 17, 0, tzinfo=UTC))

    def test_spring_gap_window_is_offered_at_its_real_local_time(self):
        pod = self.pod_of([self.anna, self.mike])
        # Sundays 02:00-04:00 Berlin; on 2027-03-28 02:00-03:00 does not exist,
        # so only 03:00-04:00 CEST is left and the meeting fits exactly.
        self.everyone('Europe/Berlin', [(6, '02:00', '04:00'), (5, '10:00', '11:00')], [self.anna, self.mike])
        meeting = make_meeting(pod, berlin(2027, 3, 27, 10, 0), zone='Europe/Berlin')
        offer = self.offer(pod, meeting)
        self.assertEqual(offer, datetime.datetime(2027, 3, 28, 1, 0, tzinfo=UTC))
        self.assertEqual(mtg.format_in_zone(offer, 'Europe/Berlin'), 'Sun Mar 28, 03:00 Europe/Berlin')

    def test_one_off_neighbours_and_a_live_proposal_bound_the_offer(self):
        pod = self.pod_of([self.anna, self.mike])
        self.everyone('UTC', [(d, '16:00', '19:00') for d in range(7)], [self.anna, self.mike])
        make_meeting(pod, datetime.datetime(2026, 10, 19, 16, 0, tzinfo=UTC))
        meeting = make_meeting(pod, datetime.datetime(2026, 10, 20, 16, 0, tzinfo=UTC))
        # A one-off live proposal at 17:30 the same day is the next meeting.
        make_meeting(pod, datetime.datetime(2026, 10, 20, 17, 30, tzinfo=UTC), status='proposed')
        # Oct 20: 16:00-16:45 are within 60 minutes, 17:00 and 17:15 overlap
        # the proposal, later starts pass it. Oct 19 16:00 is the previous
        # meeting and 16:15-16:45 overlap it, so the closest start left in the
        # same week is Mon Oct 19 18:00.
        self.assertEqual(self.offer(pod, meeting), datetime.datetime(2026, 10, 19, 18, 0, tzinfo=UTC))

    def test_a_one_off_next_meeting_is_never_jumped(self):
        pod = self.pod_of([self.anna, self.mike])
        self.everyone('UTC', [(d, '16:00', '17:30') for d in range(7)], [self.anna, self.mike])
        meeting = make_meeting(pod, datetime.datetime(2026, 10, 19, 16, 0, tzinfo=UTC))
        make_meeting(pod, datetime.datetime(2026, 10, 20, 16, 0, tzinfo=UTC))
        # Wed Oct 21 is in the same week but past the one-off next meeting, so
        # the offer falls back to the week before: Sun Oct 18, closest start.
        self.assertEqual(self.offer(pod, meeting), datetime.datetime(2026, 10, 18, 16, 30, tzinfo=UTC))

    def test_a_one_off_previous_meeting_is_never_jumped(self):
        pod = self.pod_of([self.anna, self.mike])
        self.everyone('UTC', [(d, '16:00', '17:30') for d in range(7)], [self.anna, self.mike])
        make_meeting(pod, datetime.datetime(2026, 10, 19, 17, 0, tzinfo=UTC))
        meeting = make_meeting(pod, datetime.datetime(2026, 10, 20, 16, 0, tzinfo=UTC))
        # Mon Oct 19 16:00 fits without overlapping the 17:00 one-off meeting,
        # is one day away and the earlier of two ties, but lies before it.
        self.assertEqual(self.offer(pod, meeting), datetime.datetime(2026, 10, 21, 16, 0, tzinfo=UTC))

    def test_nothing_further_than_a_week_from_the_meeting_is_offered(self):
        pod = self.pod_of([self.anna, self.mike])
        # Monday windows that only line up once the EU clocks go back
        # (Oct 25): Mon Oct 12 and Oct 19 overlap for 30 minutes, Mon Oct 26
        # fits a whole meeting at 00:00 UTC, 12 days after the meeting.
        set_windows(self.anna, 'Europe/Berlin', [(0, '01:00', '02:30')])
        set_windows(self.mike, 'UTC', [(0, '00:00', '01:30')])
        meeting = make_meeting(pod, datetime.datetime(2026, 10, 14, 16, 0, tzinfo=UTC))
        self.assertIsNone(self.offer(pod, meeting))

    def test_fewer_days_away_beats_a_better_fit(self):
        pod = self.pod_of([self.anna, self.raj, self.mike])
        self.everyone('UTC', [(2, '16:00', '17:00'), (4, '16:00', '17:00')], [self.anna, self.raj])
        set_windows(self.mike, 'UTC', [(4, '16:00', '17:00')])
        meeting = make_meeting(pod, datetime.datetime(2026, 10, 20, 16, 0, tzinfo=UTC))
        # Wed Oct 21 (1 day, 2 of 3) beats Fri Oct 23 (3 days, all 3).
        self.assertEqual(self.offer(pod, meeting), datetime.datetime(2026, 10, 21, 16, 0, tzinfo=UTC))

    def test_meeting_three_weeks_out_gets_an_offer_inside_its_own_window(self):
        pod = self.pod_of([self.anna, self.mike])
        self.everyone('Europe/Berlin', [(1, '18:00', '19:00'), (3, '18:00', '19:00')], [self.anna, self.mike])
        meeting = make_meeting(pod, berlin(2026, 11, 3, 18, 0), zone='Europe/Berlin')
        offer = self.offer(pod, meeting)
        self.assertEqual(offer, berlin(2026, 11, 5, 18, 0))
        self.assertLessEqual(abs(offer - meeting.starts_at), datetime.timedelta(days=7))

    def test_a_proposals_own_series_does_not_block_its_offer(self):
        pod = self.pod_of([self.anna, self.mike])
        self.everyone('Europe/Berlin', [(1, '18:00', '19:00')], [self.anna, self.mike])
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        state = meeting_state(pod, NOW)
        group = [m for m in state.meetings if m.status == 'proposed']
        slot = next_best_slot(group[0], group, state, avail(pod), now=NOW)
        # Tue Oct 20 is the proposal's own second meeting, not a neighbour
        # (Tue Oct 6 is already past).
        self.assertEqual(slot.start, meetings[1].starts_at)

    def test_no_candidate_names_the_week_window(self):
        pod = self.pod_of([self.anna, self.mike])
        self.everyone('Europe/Berlin', [(1, '18:00', '19:00')], [self.anna, self.mike])
        series = uuid.uuid4()
        make_meeting(pod, berlin(2026, 10, 13, 18, 0), zone='Europe/Berlin', series_id=series)
        second = make_meeting(pod, berlin(2026, 10, 20, 18, 0), zone='Europe/Berlin', series_id=series)
        make_meeting(pod, berlin(2026, 10, 27, 18, 0), zone='Europe/Berlin', series_id=series)
        PodMeetingResponse.objects.create(meeting=second, user=self.mike, response=MEETING_RESPONSE_CANT)
        self.assertIsNone(self.offer(pod, second))
        with override_settings(PODS_COURSE_SLUGS=COURSE_SLUG, SLACK_TEAM_ID='T0TEAM'):
            self.client.force_login(self.mike)
            response = self.client.get(self.pod_url(pod))
        self.assertEqual(
            texts(response, 'pod-meeting-no-offer'),
            ['No other time within a week of this meeting fits at least 2 of you.'],
        )
        self.assertIn('Change time', row_html(response, second))


# --- 3. Waiting on -----------------------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class MembersWithoutAnswerTest(TestCase):
    def test_any_answer_counts_and_member_order_is_kept(self):
        from pods.tests.fixtures import enroll, make_cohort, make_course

        cohort = make_cohort(make_course())
        users = [make_user(f'u{i}@example.com') for i in range(4)]
        for user in users:
            enroll(user, cohort)
        pod = make_pod(cohort, 'Pod', users, owner=users[0])
        head = make_meeting(pod, NOW + datetime.timedelta(days=2), status='proposed')
        PodMeetingResponse.objects.create(meeting=head, user=users[0], response=MEETING_RESPONSE_GOING)
        PodMeetingResponse.objects.create(meeting=head, user=users[2], response=MEETING_RESPONSE_CANT)
        self.assertEqual(members_without_answer(head, users), [users[1], users[3]])
        self.assertEqual(members_without_answer(head, users, responses=list(head.responses.all())), [users[1], users[3]])


@tag('core')
@override_settings(PODS_COURSE_SLUGS=COURSE_SLUG, SLACK_TEAM_ID='T0TEAM')
@freeze_time(NOW)
class WaitingOnTest(PolishFixture):
    def test_proposer_and_quiet_member_read_the_line_in_their_own_words(self):
        pod = self.pod_of([self.anna, self.mike])
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-waiting'), ['Waiting on Mike K. since Oct 9.'])
        html = response.content.decode()
        self.assertLess(html.index('data-testid="pod-meeting-proposed-by"'), html.index('data-testid="pod-meeting-waiting"'))
        self.client.force_login(self.mike)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-waiting'), ['Waiting on you since Oct 9.'])
        response = self.client.post(
            self.meetings_url(pod, f'/{proposal.pk}/respond'), {'response': 'cant_make_it'}, follow=True,
        )
        self.assertEqual(texts(response, 'pod-meeting-waiting'), [])

    def test_viewer_is_listed_first_and_the_date_follows_the_last_move(self):
        pod = self.pod_of([self.anna, self.raj, self.mike, self.basic])
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        mtg.respond_to_meeting(proposal, self.basic, MEETING_RESPONSE_GOING)
        self.client.force_login(self.mike)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-waiting'), ['Waiting on you and Raj S. since Oct 9.'])
        with freeze_time(NOW + datetime.timedelta(days=2)):
            mtg.move_meeting(proposal, self.anna, berlin(2026, 10, 14, 18, 0))
            response = self.client.get(self.pod_url(pod))
        # A move resets the answers to the mover alone.
        self.assertEqual(texts(response, 'pod-meeting-waiting'), ['Waiting on you, Raj S. and Bea S. since Oct 11.'])

    def test_staff_viewer_sees_names_and_expired_or_confirmed_proposals_have_no_line(self):
        pod = self.pod_of([self.anna, self.mike])
        make_meeting(pod, NOW - datetime.timedelta(hours=1), status='proposed', proposed_by=self.anna)
        self.client.force_login(self.staff)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Not confirmed'])
        self.assertEqual(texts(response, 'pod-meeting-waiting'), [])
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-waiting'), ['Waiting on Mike K. since Oct 9.'])
        mtg.respond_to_meeting(proposal, self.mike, MEETING_RESPONSE_GOING)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-waiting'), [])


# --- 4. Dashboard proposals ------------------------------------------------------------------

@tag('core')
@override_settings(PODS_COURSE_SLUGS=COURSE_SLUG, SLACK_TEAM_ID='T0TEAM')
@freeze_time(NOW)
class DashboardProposalTest(PolishFixture):
    def dashboard(self, user):
        self.client.force_login(user)
        return self.client.get('/')

    def test_unanswered_proposal_is_listed_with_needs_your_answer(self):
        pod = self.pod_of([self.anna, self.mike])
        mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 12, 18, 0))
        response = self.dashboard(self.mike)
        self.assertEqual(texts(response, 'dashboard-pod-meeting-title'), ['RAG evals study group - Proposed time'])
        self.assertEqual(texts(response, 'dashboard-pod-meeting-time'), ['Mon Oct 12, 12:00 America/New_York'])
        self.assertEqual(texts(response, 'dashboard-pod-meeting-needs-answer'), ['Needs your answer'])
        self.assertContains(response, f'href="/courses/{COURSE_SLUG}/home/pods/{pod.pk}#meetings"')
        # The proposer is already going, so nothing to answer.
        response = self.dashboard(self.anna)
        self.assertEqual(texts(response, 'dashboard-pod-meeting-title'), [])

    def test_answering_removes_the_row_and_confirmation_brings_the_meeting_row(self):
        pod = self.pod_of([self.anna, self.raj, self.mike, self.basic])
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 12, 18, 0), repeat_weekly=True)
        mtg.respond_to_meeting(meetings[0], self.mike, MEETING_RESPONSE_GOING)
        # Pod of 4 needs 3: Mike answered and is not nagged, Raj still is.
        self.assertEqual(texts(self.dashboard(self.mike), 'dashboard-pod-meeting-title'), [])
        response = self.dashboard(self.raj)
        self.assertEqual(texts(response, 'dashboard-pod-meeting-title'), ['RAG evals study group - Proposed time'])
        self.assertEqual(texts(response, 'dashboard-pod-meeting-needs-answer'), ['Needs your answer'])
        self.client.post(self.meetings_url(pod, f'/{meetings[0].pk}/respond'), {'response': 'going'})
        self.assertEqual(PodMeeting.objects.get(pk=meetings[0].pk).status, 'scheduled')
        response = self.dashboard(self.raj)
        self.assertEqual(texts(response, 'dashboard-pod-meeting-title'), ['RAG evals study group - Meeting 1 of 4'])
        self.assertEqual(texts(response, 'dashboard-pod-meeting-needs-answer'), [])
        self.assertContains(response, f'href="/courses/{COURSE_SLUG}/home/pods/{pod.pk}"')

    def test_cant_make_it_answer_far_expired_archived_and_disabled_are_left_out(self):
        answered = self.pod_of([self.anna, self.mike])
        [proposal] = mtg.propose_meeting(answered, self.anna, berlin(2026, 10, 12, 18, 0))
        mtg.respond_to_meeting(proposal, self.mike, MEETING_RESPONSE_CANT)
        far = make_pod(self.cohort, 'Far pod', [self.anna, self.mike], owner=self.anna)
        mtg.propose_meeting(far, self.anna, berlin(2026, 10, 17, 18, 0))
        expired = make_pod(self.cohort, 'Expired pod', [self.anna, self.mike], owner=self.anna)
        make_meeting(expired, NOW - datetime.timedelta(hours=1), status='proposed')
        archived = make_pod(self.cohort, 'Archived pod', [self.anna, self.mike], owner=self.anna)
        mtg.propose_meeting(archived, self.anna, berlin(2026, 10, 13, 18, 0))
        archived.status = 'archived'
        archived.save()
        self.assertEqual(texts(self.dashboard(self.mike), 'dashboard-pod-meeting-title'), [])
        open_pod = make_pod(self.cohort, 'Open pod', [self.anna, self.mike], owner=self.anna)
        mtg.propose_meeting(open_pod, self.anna, berlin(2026, 10, 14, 18, 0))
        self.assertEqual(texts(self.dashboard(self.mike), 'dashboard-pod-meeting-title'), ['Open pod - Proposed time'])
        with override_settings(PODS_COURSE_SLUGS=''):
            self.assertEqual(texts(self.dashboard(self.mike), 'dashboard-pod-meeting-title'), [])

    def test_proposals_sort_with_scheduled_meetings_by_start(self):
        scheduled = self.pod_of([self.anna, self.mike])
        make_meeting(scheduled, berlin(2026, 10, 14, 18, 0), zone='Europe/Berlin')
        proposing = make_pod(self.cohort, 'Proposal pod', [self.anna, self.mike], owner=self.anna)
        mtg.propose_meeting(proposing, self.anna, berlin(2026, 10, 12, 18, 0))
        self.assertEqual(
            texts(self.dashboard(self.mike), 'dashboard-pod-meeting-title'),
            ['Proposal pod - Proposed time', 'RAG evals study group - Meeting 1 of 4'],
        )


# --- 5. Call link ----------------------------------------------------------------------------

@tag('core')
@override_settings(PODS_COURSE_SLUGS=COURSE_SLUG, SLACK_TEAM_ID='T0TEAM')
@freeze_time(NOW)
class CallLinkAffordanceTest(PolishFixture):
    def test_toggle_is_a_small_secondary_button_with_the_right_label(self):
        pod = self.pod_of([self.anna, self.mike])
        self.client.force_login(self.anna)
        html = self.client.get(self.pod_url(pod)).content.decode()
        toggle = re.search(r'<summary class="([^"]+)" data-testid="pod-call-link-toggle">([^<]+)</summary>', html)
        self.assertEqual(toggle.group(2), 'Add a call link')
        # Rendered by the shared tag, not hand-rolled muted text.
        self.assertTrue(toggle.group(1).startswith(button_classes('secondary', size='sm')), toggle.group(1))
        pod.meeting_url = 'https://meet.google.com/abc-defg-hij'
        pod.save()
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-call-link-toggle'), ['Change link'])

    def test_only_the_soonest_upcoming_row_shows_the_call_link(self):
        pod = self.pod_of([self.anna, self.mike], meeting_url='https://meet.google.com/abc-defg-hij')
        make_meeting(pod, NOW - datetime.timedelta(days=2), status='held')
        meetings = self.series(pod, count=3)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-call-link-line'), ['Call link: meet.google.com/abc-defg-hij'])
        self.assertEqual(texts(response, 'pod-meeting-call-link'), ['Call link: meet.google.com/abc-defg-hij'])
        self.assertIn('data-testid="pod-meeting-call-link"', row_html(response, meetings[0]))

    def test_a_proposal_can_be_the_soonest_row(self):
        pod = self.pod_of([self.anna, self.mike], meeting_url='https://zoom.us/j/123')
        make_meeting(pod, berlin(2026, 10, 20, 18, 0), zone='Europe/Berlin')
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-call-link'), ['Call link: zoom.us/j/123'])
        self.assertIn('data-testid="pod-meeting-call-link"', row_html(response, proposal))


# --- 6. Clock change wording -----------------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class ClockChangeWordingTest(PolishFixture):
    def lines(self, pod, meetings):
        state = meeting_state(pod, NOW)
        members = avail(pod)
        by_pk = {m.pk: m for m in state.meetings}
        return [dst_lines(by_pk[m.pk], state, members) for m in meetings]

    def test_lasting_shift_is_announced_once_and_a_reverting_one_each_week(self):
        pod = self.pod_of([self.anna, self.raj, self.mike])
        meetings = self.series(pod, start=berlin(2026, 10, 20, 18, 0))
        self.assertEqual(self.lines(pod, meetings), [
            [],
            [
                'Clock change: 22:30 for Raj S. from this week (was 21:30).',
                'Clock change: 13:00 for Mike K. this week (usually 12:00).',
            ],
            [],
            [],
        ])

    def test_shift_on_the_final_meeting_only_is_temporary(self):
        pod = self.pod_of([self.anna, self.raj])
        # Spring: Tue Mar 9 - Mar 30 2027; the EU change (Mar 28) only hits the last one.
        meetings = self.series(pod, start=berlin(2027, 3, 9, 18, 0))
        self.assertEqual(self.lines(pod, meetings), [
            [], [], [], ['Clock change: 21:30 for Raj S. this week (usually 22:30).'],
        ])

    def test_a_multi_week_temporary_shift_reads_this_week_on_each_meeting(self):
        pod = self.pod_of([self.anna, self.mike])
        # US clocks change on Mar 14, the EU ones on Mar 28: two shifted weeks.
        meetings = self.series(pod, start=berlin(2027, 3, 9, 18, 0))
        self.assertEqual(self.lines(pod, meetings), [
            [],
            ['Clock change: 13:00 for Mike K. this week (usually 12:00).'],
            ['Clock change: 13:00 for Mike K. this week (usually 12:00).'],
            [],
        ])

    def test_lasting_shift_continued_to_the_last_meeting_stays_quiet(self):
        pod = self.pod_of([self.anna, self.raj])
        meetings = self.series(pod, count=3, start=berlin(2026, 10, 20, 18, 0))
        self.assertEqual(self.lines(pod, meetings), [
            [], ['Clock change: 22:30 for Raj S. from this week (was 21:30).'], [],
        ])

    @override_settings(PODS_COURSE_SLUGS=COURSE_SLUG, SLACK_TEAM_ID='T0TEAM')
    def test_pod_page_renders_the_lines_on_their_rows(self):
        pod = self.pod_of([self.anna, self.raj, self.mike])
        meetings = self.series(pod, start=berlin(2026, 10, 20, 18, 0))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-dst'), [
            'Clock change: 22:30 for Raj S. from this week (was 21:30).',
            'Clock change: 13:00 for Mike K. this week (usually 12:00).',
        ])
        self.assertIn('data-testid="pod-meeting-dst"', row_html(response, meetings[1]))
