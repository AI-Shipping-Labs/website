"""Proposals that can no longer be confirmed, and small wording fixes (issue #1937).

A live proposal can no longer be confirmed once the pod's members minus
those who answered ``Can't make it`` are fewer than the required
attendance. The pod page, the bell, the dashboard and the flash after
answering all say so. Plus: ``Cancel meeting`` on proposal
rows, no row ``Call link:`` line (covered in ``test_meetings_polish_1934``)
and ``since today`` / ``since yesterday`` dates.

Every test freezes the clock (date-rot-ok: fixed Friday 2026-10-09 08:00
UTC, which is 04:00 on Oct 9 in New York and 10:00 in Berlin).
"""

import datetime
import re
from datetime import UTC
from types import SimpleNamespace

from django.test import SimpleTestCase, tag
from freezegun import freeze_time

from accounts.templatetags.accounts_extras import button_classes
from notifications.models import Notification
from pods.models import MEETING_RESPONSE_CANT, MEETING_RESPONSE_GOING, PodMeeting
from pods.services import meetings as mtg
from pods.services.meeting_rules import meeting_state, proposal_can_be_confirmed, proposal_unconfirmable
from pods.tests.fixtures import COURSE_SLUG, make_meeting, make_pod, set_windows
from pods.tests.test_meeting_views import MeetingViewFixture, berlin, message_texts, texts
from pods.tests.test_member_views import ENABLED

NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday
POD = 'RAG evals study group'
LINE = "Too few of you can make this time, so it can't be confirmed. Propose another time."
PRIMARY = button_classes('primary', size='sm')
SECONDARY = button_classes('secondary', size='sm')


def answers(*pairs):
    return [SimpleNamespace(user_id=user_id, response=response) for user_id, response in pairs]


def row_html(response, meeting):
    rows = response.content.decode().split('data-testid="pod-meeting"')
    return next(r for r in rows if f'data-meeting-id="{meeting.pk}"' in r)


def button_class(html, testid):
    """The class attribute of the element carrying ``testid``."""
    match = re.search(rf'class="([^"]+)" data-testid="{testid}"', html)
    return match.group(1) if match else None


# --- The rule -----------------------------------------------------------------------

@tag('core')
class ProposalCanBeConfirmedTest(SimpleTestCase):
    def test_pod_of_three_needs_two(self):
        members = [1, 2, 3]
        self.assertTrue(proposal_can_be_confirmed(members, answers((2, MEETING_RESPONSE_CANT))))
        self.assertFalse(proposal_can_be_confirmed(
            members, answers((2, MEETING_RESPONSE_CANT), (3, MEETING_RESPONSE_CANT)),
        ))

    def test_pod_of_four_needs_three(self):
        members = [1, 2, 3, 4]
        self.assertTrue(proposal_can_be_confirmed(members, answers((2, MEETING_RESPONSE_CANT))))
        self.assertFalse(proposal_can_be_confirmed(
            members, answers((2, MEETING_RESPONSE_CANT), (3, MEETING_RESPONSE_CANT)),
        ))

    def test_only_current_members_cant_answers_count(self):
        # A member who left (user 9) and going answers do not count.
        self.assertTrue(proposal_can_be_confirmed(
            [1, 2, 3],
            answers((9, MEETING_RESPONSE_CANT), (2, MEETING_RESPONSE_CANT), (1, MEETING_RESPONSE_GOING)),
        ))

    def test_pod_of_two_needs_both(self):
        members = [1, 2]
        self.assertTrue(proposal_can_be_confirmed(members, answers((1, MEETING_RESPONSE_GOING))))
        self.assertTrue(proposal_can_be_confirmed(members, []))
        self.assertFalse(proposal_can_be_confirmed(members, answers((2, MEETING_RESPONSE_CANT))))

    def test_pod_of_one_is_never_unconfirmable(self):
        # Too small to need anyone: no false alarm even after Can't make it.
        self.assertTrue(proposal_can_be_confirmed([1], answers((1, MEETING_RESPONSE_CANT))))

    def test_explicit_needed_and_users_as_members(self):
        users = [SimpleNamespace(pk=pk) for pk in (1, 2, 3, 4)]
        cant = answers((2, MEETING_RESPONSE_CANT))
        self.assertTrue(proposal_can_be_confirmed(users, cant, needed=3))
        self.assertFalse(proposal_can_be_confirmed(users, cant, needed=4))


@tag('core')
@freeze_time(NOW)
class ProposalUnconfirmableTest(MeetingViewFixture):
    def test_only_a_live_proposal_head_can_be_unconfirmable(self):
        pod = self.make_pod()
        members = [self.anna.pk, self.raj.pk, self.mike.pk]
        cant = answers((self.raj.pk, MEETING_RESPONSE_CANT), (self.mike.pk, MEETING_RESPONSE_CANT))
        live = make_meeting(pod, NOW + datetime.timedelta(days=3), status='proposed')
        expired = make_meeting(pod, NOW - datetime.timedelta(hours=1), status='proposed')
        scheduled = make_meeting(pod, NOW + datetime.timedelta(days=5))
        state = meeting_state(pod, NOW)
        self.assertTrue(proposal_unconfirmable(live, state, members, cant))
        self.assertFalse(proposal_unconfirmable(expired, state, members, cant))
        self.assertFalse(proposal_unconfirmable(scheduled, state, members, cant))


# --- The pod page row -----------------------------------------------------------------

@tag('core')
@ENABLED
@freeze_time(NOW)
class UnconfirmableRowTest(MeetingViewFixture):
    def dead_proposal(self, members=None):
        pod = self.make_pod(members)
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        mtg.respond_to_meeting(proposal, self.raj, MEETING_RESPONSE_CANT)
        mtg.respond_to_meeting(proposal, self.mike, MEETING_RESPONSE_CANT)
        return pod, proposal

    def test_row_reads_cant_be_confirmed_with_the_next_best_time_as_the_one_primary(self):
        pod, proposal = self.dead_proposal()
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ["Can't be confirmed"])
        [row] = [r for r in response.context['meetings']['rows'] if r['meeting'].pk == proposal.pk]
        self.assertEqual(row['tone'], 'warning')
        self.assertEqual(texts(response, 'pod-meeting-unconfirmable'), [LINE])
        self.assertEqual(texts(response, 'pod-meeting-waiting'), [])
        self.assertEqual(texts(response, 'pod-meeting-cant'), ["Can't make it: Raj S., Mike K."])
        [button] = texts(response, 'pod-meeting-offer-propose')
        self.assertRegex(button, r'^Propose .+ instead$')
        self.assertEqual(texts(response, 'pod-meeting-change'), ['Propose another time'])
        html = response.content.decode()
        self.assertEqual(button_class(html, 'pod-meeting-offer-propose'), PRIMARY)
        self.assertEqual(button_class(html, 'pod-meeting-change'), SECONDARY)
        self.assertEqual(html.count(PRIMARY), 1)

    def test_i_can_make_it_steps_back_to_secondary(self):
        # Pod of 4 needs 3: two Can't make it answers leave only 2.
        pod, _proposal = self.dead_proposal([self.anna, self.raj, self.mike, self.basic])
        self.client.force_login(self.basic)
        response = self.client.get(self.pod_url(pod))
        # Bea has not answered, yet a dead proposal waits on nobody.
        self.assertEqual(texts(response, 'pod-meeting-badge'), ["Can't be confirmed"])
        self.assertEqual(texts(response, 'pod-meeting-waiting'), [])
        html = response.content.decode()
        self.assertEqual(button_class(html, 'pod-meeting-going'), SECONDARY)
        self.assertEqual(button_class(html, 'pod-meeting-offer-propose'), PRIMARY)
        self.assertEqual(html.count(PRIMARY), 1)

    def test_without_a_next_best_time_propose_another_time_is_primary(self):
        # Nobody shares a weekday, so no time fits 2 of the 3.
        set_windows(self.anna, 'Europe/Berlin', [(0, '18:00', '19:00')])
        set_windows(self.raj, 'Europe/Berlin', [(1, '18:00', '19:00')])
        set_windows(self.mike, 'Europe/Berlin', [(2, '18:00', '19:00')])
        pod, _proposal = self.dead_proposal()
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(
            texts(response, 'pod-meeting-no-offer'),
            ['No other time within a week of this meeting fits at least 2 of you.'],
        )
        self.assertEqual(texts(response, 'pod-meeting-offer-propose'), [])
        html = response.content.decode()
        self.assertEqual(button_class(html, 'pod-meeting-change'), PRIMARY)
        self.assertEqual(texts(response, 'pod-meeting-change'), ['Propose another time'])

    def test_one_absence_in_a_pod_of_four_keeps_the_normal_proposal(self):
        pod = self.make_pod([self.anna, self.raj, self.mike, self.basic])
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        mtg.respond_to_meeting(proposal, self.raj, MEETING_RESPONSE_CANT)
        self.client.force_login(self.mike)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Proposed - 1 of 3 needed'])
        self.assertEqual(texts(response, 'pod-meeting-waiting'), ['Waiting on you and Bea S. since today.'])
        self.assertEqual(texts(response, 'pod-meeting-unconfirmable'), [])
        self.assertEqual(texts(response, 'pod-meeting-change'), ['Change time'])
        html = response.content.decode()
        self.assertEqual(button_class(html, 'pod-meeting-going'), PRIMARY)

    def test_propose_another_time_moves_the_proposal_and_resets_it(self):
        pod, proposal = self.dead_proposal()
        self.client.force_login(self.raj)
        page = self.client.get(self.pod_url(pod))
        self.assertIn(f'action="{self.meetings_url(pod, f"/{proposal.pk}/move")}"', row_html(page, proposal))
        form = self.client.get(self.meetings_url(pod, f'/{proposal.pk}/move'))
        self.assertEqual(texts(form, 'pod-meeting-form-current'), ['Now: Tue Oct 13, 21:30 Asia/Kolkata'])
        self.assertEqual(texts(form, 'pod-meeting-form-rule'), ['Confirmed once 2 of you can make it.'])
        response = self.client.post(self.meetings_url(pod, f'/{proposal.pk}/move'), {
            'choice': 'custom', 'date': '2026-10-15', 'time': '21:30',
        }, follow=True)
        self.assertIn('Meeting moved.', message_texts(response))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Proposed - 1 of 2 needed'])
        self.assertEqual(texts(response, 'pod-meeting-cant'), [])
        self.assertEqual(texts(response, 'pod-meeting-unconfirmable'), [])

    def test_changing_back_to_going_returns_the_normal_state_or_confirms(self):
        pod, proposal = self.dead_proposal([self.anna, self.raj, self.mike, self.basic])
        self.client.force_login(self.mike)
        response = self.client.post(
            self.meetings_url(pod, f'/{proposal.pk}/respond'), {'response': 'going'}, follow=True,
        )
        self.assertIn('Marked that you can make it.', message_texts(response))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Proposed - 2 of 3 needed'])
        self.assertEqual(texts(response, 'pod-meeting-unconfirmable'), [])
        self.assertEqual(texts(response, 'pod-meeting-waiting'), ['Waiting on Bea S. since today.'])
        # In a pod of 3 the switch reaches 2 going and confirms it.
        trio, dead = self.dead_proposal()
        response = self.client.post(self.meetings_url(trio, f'/{dead.pk}/respond'), {'response': 'going'}, follow=True)
        self.assertIn('Meeting confirmed.', message_texts(response))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Confirmed'])


# --- Bell and flash ---------------------------------------------------------------------

@tag('core')
@ENABLED
@freeze_time(NOW)
class UnconfirmableNotificationTest(MeetingViewFixture):
    def titles(self, user):
        return list(Notification.objects.filter(user=user).order_by('pk').values_list('title', 'url'))

    def test_the_answer_that_kills_the_proposal_tells_everyone_else(self):
        pod = self.make_pod()
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        mtg.respond_to_meeting(proposal, self.mike, MEETING_RESPONSE_CANT)
        Notification.objects.all().delete()
        self.client.force_login(self.raj)
        response = self.client.post(
            self.meetings_url(pod, f'/{proposal.pk}/respond'), {'response': 'cant_make_it'}, follow=True,
        )
        self.assertIn(
            "Marked that you can't make it. This time can't be confirmed, so propose another time.",
            message_texts(response),
        )
        expected = [(
            f"Raj S. can't make {POD} meeting 1, so this time can't be confirmed. Propose another time.",
            f'/courses/{COURSE_SLUG}/home/pods/{pod.pk}#meetings',
        )]
        self.assertEqual(self.titles(self.anna), expected)
        self.assertEqual(self.titles(self.mike), expected)
        self.assertEqual(self.titles(self.raj), [])

    def test_a_cant_that_leaves_it_confirmable_keeps_the_plain_title_and_message(self):
        pod = self.make_pod()
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        Notification.objects.all().delete()
        self.client.force_login(self.mike)
        response = self.client.post(
            self.meetings_url(pod, f'/{proposal.pk}/respond'), {'response': 'cant_make_it'}, follow=True,
        )
        self.assertIn("Marked that you can't make it. The pod sees the next best time.", message_texts(response))
        plain = [(f"Mike K. can't make {POD} meeting 1", f'/courses/{COURSE_SLUG}/home/pods/{pod.pk}')]
        self.assertEqual(self.titles(self.anna), plain)
        self.assertEqual(self.titles(self.raj), plain)

    def test_a_cant_on_an_already_dead_proposal_keeps_the_plain_title(self):
        pod = self.make_pod([self.anna, self.raj, self.mike, self.basic])
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        mtg.respond_to_meeting(proposal, self.raj, MEETING_RESPONSE_CANT)
        mtg.respond_to_meeting(proposal, self.mike, MEETING_RESPONSE_CANT)
        Notification.objects.all().delete()
        answer = mtg.answer_meeting(proposal, self.basic, MEETING_RESPONSE_CANT)
        self.assertFalse(answer.made_unconfirmable)
        plain = [(f"Bea S. can't make {POD} meeting 1", f'/courses/{COURSE_SLUG}/home/pods/{pod.pk}')]
        for user in (self.anna, self.raj, self.mike):
            self.assertEqual(self.titles(user), plain)

    def test_weekly_proposal_uses_the_meeting_label_and_scheduled_meetings_never_flag(self):
        pod = self.make_pod()
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        mtg.respond_to_meeting(meetings[0], self.mike, MEETING_RESPONSE_CANT)
        Notification.objects.all().delete()
        # Answering a later series meeting answers the head.
        self.assertTrue(mtg.answer_meeting(meetings[2], self.raj, MEETING_RESPONSE_CANT).made_unconfirmable)
        self.assertEqual(
            [t for t, _url in self.titles(self.anna)],
            [f"Raj S. can't make {POD} meeting 1, so this time can't be confirmed. Propose another time."],
        )
        scheduled = make_meeting(self.make_pod(), berlin(2026, 10, 14, 18, 0))
        mtg.respond_to_meeting(scheduled, self.mike, MEETING_RESPONSE_CANT)
        self.assertFalse(mtg.answer_meeting(scheduled, self.raj, MEETING_RESPONSE_CANT).made_unconfirmable)


# --- Pods tab and dashboard -------------------------------------------------------------

@tag('core')
@ENABLED
@freeze_time(NOW)
class UnconfirmableTabAndDashboardTest(MeetingViewFixture):
    def test_browse_hides_meeting_state_and_dashboard_skips_a_dead_proposal(self):
        dead_pod = self.make_pod([self.anna, self.raj, self.basic])
        [dead] = mtg.propose_meeting(dead_pod, self.anna, berlin(2026, 10, 12, 18, 0))
        # Bea has not answered; Raj and then the proposer herself can't make it.
        mtg.respond_to_meeting(dead, self.raj, MEETING_RESPONSE_CANT)
        mtg.respond_to_meeting(dead, self.anna, MEETING_RESPONSE_CANT)
        live_pod = make_pod(self.cohort, 'Live pod', [self.anna, self.basic], owner=self.anna)
        mtg.propose_meeting(live_pod, self.anna, berlin(2026, 10, 13, 18, 0))
        self.client.force_login(self.basic)
        response = self.client.get(self.pods_url())
        self.assertEqual(texts(response, 'pod-row-meeting'), [])
        response = self.client.get('/')
        self.assertEqual(texts(response, 'dashboard-pod-meeting-title'), ['Live pod - Proposed time'])
        self.assertEqual(texts(response, 'dashboard-pod-meeting-needs-answer'), ['Needs your answer'])

# --- Cancel wording ---------------------------------------------------------------------

@tag('core')
@ENABLED
@freeze_time(NOW)
class CancelWordingTest(MeetingViewFixture):
    def test_both_rows_say_cancel_meeting_and_only_the_confirmed_one_asks_first(self):
        pod = self.make_pod()
        confirmed = make_meeting(pod, berlin(2026, 10, 12, 18, 0), zone='Europe/Berlin')
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 14, 18, 0))
        self.client.force_login(self.raj)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-cancel'), ['Cancel meeting'])
        self.assertEqual(texts(response, 'pod-meeting-cancel-toggle'), ['Cancel meeting'])
        self.assertIn('data-testid="pod-meeting-cancel"', row_html(response, proposal))
        self.assertNotIn('pod-meeting-cancel-disclosure', row_html(response, proposal))
        self.assertIn('pod-meeting-cancel-disclosure', row_html(response, confirmed))
        self.assertNotRegex(response.content.decode(), r'>\s*Cancel\s*</(button|summary)>')
        response = self.client.post(self.meetings_url(pod, f'/{proposal.pk}/cancel'), follow=True)
        self.assertIn('Meeting cancelled.', message_texts(response))
        self.assertEqual(PodMeeting.objects.get(pk=proposal.pk).status, 'cancelled')


# --- Relative dates -----------------------------------------------------------------------

@tag('core')
@ENABLED
@freeze_time(NOW)
class RelativeDatesTest(MeetingViewFixture):
    """Oct 9 02:00 UTC is Oct 9 in Berlin but still Oct 8 in New York."""

    def proposal_at(self, created_at, moved_at=None):
        pod = self.make_pod()
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 14, 18, 0))
        PodMeeting.objects.filter(pk=proposal.pk).update(
            created_at=created_at, moved_at=moved_at, moved_by=self.anna if moved_at else None,
        )
        return pod

    def lines(self, user, pod, testid):
        self.client.force_login(user)
        return texts(self.client.get(self.pod_url(pod)), testid)

    def test_waiting_line_reads_today_yesterday_or_the_date_in_the_viewers_zone(self):
        pod = self.proposal_at(datetime.datetime(2026, 10, 9, 2, 0, tzinfo=UTC))
        self.assertEqual(self.lines(self.mike, pod, 'pod-meeting-waiting'), ['Waiting on you and Raj S. since yesterday.'])
        self.assertEqual(self.lines(self.anna, pod, 'pod-meeting-waiting'), ['Waiting on Raj S. and Mike K. since today.'])
        older = self.proposal_at(datetime.datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
        self.assertEqual(self.lines(self.mike, older, 'pod-meeting-waiting'), ['Waiting on you and Raj S. since Oct 7.'])

    def test_moved_line_uses_the_same_rule(self):
        pod = self.proposal_at(NOW - datetime.timedelta(days=3), moved_at=datetime.datetime(2026, 10, 9, 2, 0, tzinfo=UTC))
        self.assertEqual(self.lines(self.mike, pod, 'pod-meeting-moved'), ['Moved by Anna K. yesterday'])
        self.assertEqual(self.lines(self.mike, pod, 'pod-meeting-waiting'), ['Waiting on you and Raj S. since yesterday.'])
        self.assertEqual(self.lines(self.anna, pod, 'pod-meeting-moved'), ['Moved by Anna K. today'])
        older = self.proposal_at(NOW - datetime.timedelta(days=3), moved_at=datetime.datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
        self.assertEqual(self.lines(self.mike, older, 'pod-meeting-moved'), ['Moved by Anna K. on Oct 7'])
