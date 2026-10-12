"""Follow-ups to proposals that can no longer be confirmed (issue #1940).

1. Suggested times says the open proposal can't be confirmed instead of
   asking members to confirm it.
2. A proposal replaced through ``Propose <time> instead`` links to the new
   proposal (``PodMeeting.replaced_by``) and its row says
   ``Replaced by a new proposed time.``
3. A moved proposal reads ``Proposed by``, ``Moved by``, then the waiting
   or can't-be-confirmed line.

Every test freezes the clock (date-rot-ok: fixed Friday 2026-10-09 08:00
UTC).
"""

import datetime
import re
from datetime import UTC
from types import SimpleNamespace

from django.db import connection
from django.test import tag
from django.test.utils import CaptureQueriesContext
from freezegun import freeze_time

from accounts.models import Token
from pods.models import MEETING_RESPONSE_CANT, MEETING_RESPONSE_GOING, PodMeeting
from pods.services import meetings as mtg
from pods.services.meeting_presentation import (
    MSG_PROPOSAL_OPEN,
    MSG_PROPOSAL_UNCONFIRMABLE,
    open_proposal_unconfirmable,
    propose_state,
)
from pods.services.meeting_rules import meeting_state
from pods.tests.fixtures import make_meeting, make_pod
from pods.tests.test_meeting_views import MeetingViewFixture, berlin, message_texts, texts
from pods.tests.test_member_views import ENABLED

NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday
DEAD = "The proposed time can't be confirmed. Propose another time from the meeting above."
LIVE = 'Confirm or cancel the proposed time before proposing another.'
REPLACED = 'Replaced by a new proposed time.'
UNCONFIRMABLE_LINE = "Too few of you can make this time, so it can't be confirmed. Propose another time."


def row_html(response, meeting):
    rows = response.content.decode().split('data-testid="pod-meeting"')
    return next(r for r in rows if f'data-meeting-id="{meeting.pk}"' in r)


def testid_order(html, testids):
    """The ``testids`` present in ``html``, in DOM order."""
    found = [(html.index(f'data-testid="{t}"'), t) for t in testids if f'data-testid="{t}"' in html]
    return [t for _pos, t in sorted(found)]


def assert_cancelled(test, row):
    """``row`` (from :func:`row_html`) carries the ``Cancelled`` badge."""
    test.assertRegex(row, r'data-testid="pod-meeting-badge"[^>]*>\s*Cancelled\s*<')


META = [
    'pod-meeting-badge', 'pod-meeting-replaced', 'pod-meeting-proposed-by', 'pod-meeting-moved',
    'pod-meeting-waiting', 'pod-meeting-unconfirmable', 'pod-meeting-cant',
]


# --- Item 1: the Suggested times sentence ----------------------------------------

@tag('core')
class ProposeStateMessageTest(MeetingViewFixture):
    def test_open_proposal_message_depends_on_whether_it_can_be_confirmed(self):
        pod = SimpleNamespace(status='open', meeting_count=4)
        state = SimpleNamespace(has_open_proposal=True, used=1)
        self.assertEqual(propose_state(pod, state, is_member=True, member_count=3), (False, MSG_PROPOSAL_OPEN))
        self.assertEqual(
            propose_state(pod, state, is_member=True, member_count=3, proposal_unconfirmable=True),
            (False, MSG_PROPOSAL_UNCONFIRMABLE),
        )
        self.assertEqual(MSG_PROPOSAL_UNCONFIRMABLE, DEAD)
        # The non-member and all-planned branches are unchanged.
        self.assertEqual(
            propose_state(pod, state, is_member=False, member_count=3, proposal_unconfirmable=True), (False, ''),
        )
        planned = SimpleNamespace(has_open_proposal=False, used=4)
        self.assertEqual(
            propose_state(pod, planned, is_member=True, member_count=3, proposal_unconfirmable=True),
            (False, 'All 4 meetings are planned.'),
        )


@tag('core')
@freeze_time(NOW)
class OpenProposalUnconfirmableTest(MeetingViewFixture):
    def test_weekly_proposal_is_judged_on_its_head_meeting(self):
        pod = self.make_pod(meeting_count=3)
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        members = [self.anna, self.raj, self.mike]
        self.assertEqual(len(meetings), 3)
        self.assertFalse(open_proposal_unconfirmable(meeting_state(pod, NOW), members))
        mtg.respond_to_meeting(meetings[0], self.raj, MEETING_RESPONSE_CANT)
        self.assertFalse(open_proposal_unconfirmable(meeting_state(pod, NOW), members))
        mtg.respond_to_meeting(meetings[0], self.mike, MEETING_RESPONSE_CANT)
        self.assertTrue(open_proposal_unconfirmable(meeting_state(pod, NOW), members))

    def test_no_open_proposal_needs_no_query(self):
        pod = self.make_pod()
        make_meeting(pod, NOW + datetime.timedelta(days=2))
        state = meeting_state(pod, NOW)
        with self.assertNumQueries(0):
            self.assertFalse(open_proposal_unconfirmable(state, [self.anna, self.raj]))


@tag('core')
@ENABLED
@freeze_time(NOW)
class SuggestedTimesSentenceTest(MeetingViewFixture):
    def proposal(self, *cant, members=None):
        pod = self.make_pod(members)
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        for user in cant:
            mtg.respond_to_meeting(proposal, user, MEETING_RESPONSE_CANT)
        return pod, proposal

    def test_dead_proposal_points_back_to_the_meeting_and_still_blocks_proposing(self):
        pod, _proposal = self.proposal(self.raj, self.mike)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ["Can't be confirmed"])
        self.assertEqual(texts(response, 'pod-times-propose-blocked'), [DEAD])
        self.assertNotContains(response, LIVE)
        self.assertGreater(len(texts(response, 'pod-slot-time')), 0)
        self.assertEqual(texts(response, 'pod-slot-propose'), [])
        self.assertNotContains(response, 'data-testid="pod-times-propose-custom"')

    def test_live_proposal_keeps_the_confirm_or_cancel_sentence(self):
        for cant in ((), (self.raj,)):
            with self.subTest(cant=len(cant)):
                pod, _proposal = self.proposal(*cant)
                self.client.force_login(self.mike)
                response = self.client.get(self.pod_url(pod))
                self.assertEqual(texts(response, 'pod-times-propose-blocked'), [LIVE])
                self.assertNotContains(response, DEAD)

    def test_sentence_follows_the_answers_both_ways(self):
        # A pod of 4 needs 3, so switching back to going makes the proposal
        # confirmable again without agreeing it.
        pod, proposal = self.proposal(self.raj, members=[self.anna, self.raj, self.mike, self.basic])
        self.client.force_login(self.mike)
        response = self.client.post(
            self.meetings_url(pod, f'/{proposal.pk}/respond'), {'response': MEETING_RESPONSE_CANT}, follow=True,
        )
        self.assertIn("This time can't be confirmed", message_texts(response))
        self.assertEqual(texts(response, 'pod-times-propose-blocked'), [DEAD])
        response = self.client.post(
            self.meetings_url(pod, f'/{proposal.pk}/respond'), {'response': MEETING_RESPONSE_GOING}, follow=True,
        )
        self.assertEqual(texts(response, 'pod-times-propose-blocked'), [LIVE])

    def test_opening_the_confirm_page_directly_flashes_the_dead_sentence(self):
        pod, _proposal = self.proposal(self.raj, self.mike)
        self.client.force_login(self.anna)
        response = self.client.get(self.meetings_url(pod, '/new'), follow=True)
        self.assertRedirects(response, self.pod_url(pod))
        self.assertEqual(message_texts(response), DEAD)

    def test_pod_page_query_count_does_not_grow_with_meetings(self):
        pod, _proposal = self.proposal(self.raj, self.mike)
        self.client.force_login(self.anna)
        self.client.get(self.pod_url(pod))  # warm per-session caches

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(self.pod_url(pod))
            self.assertEqual(texts(response, 'pod-times-propose-blocked'), [DEAD])
            return len(ctx.captured_queries)

        with_one = count_queries()
        # A held meeting and a cancelled, replaced proposal add rows, not queries.
        make_meeting(pod, NOW - datetime.timedelta(days=3), status='held')
        old = make_meeting(pod, NOW + datetime.timedelta(days=6), status='cancelled', created_via='member')
        PodMeeting.objects.filter(pk=old.pk).update(replaced_by=_proposal)
        with_three = count_queries()
        self.assertEqual(with_one, with_three)


# --- Item 2: Replaced by a new proposed time ------------------------------------------

@tag('core')
@ENABLED
@freeze_time(NOW)
class ReplacedProposalTest(MeetingViewFixture):
    NEW_START = berlin(2026, 10, 15, 18, 0)

    def dead_proposal(self, pod=None, *, weekly=False):
        pod = pod or self.make_pod(meeting_count=3)
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=weekly)
        mtg.respond_to_meeting(meetings[0], self.raj, MEETING_RESPONSE_CANT)
        mtg.respond_to_meeting(meetings[0], self.mike, MEETING_RESPONSE_CANT)
        return pod, meetings

    def propose_instead(self, pod, head, start=None):
        """POST ``Propose <time> instead``; returns the confirm page response."""
        start = (start or self.NEW_START).isoformat()
        response = self.client.post(self.meetings_url(pod, f'/{head.pk}/cancel'), {'then_propose': start})
        self.assertEqual(response.status_code, 302)
        return self.client.get(response['Location'])

    def hidden(self, response, name):
        match = re.search(rf'name="{name}" value="([^"]*)"', response.content.decode())
        return match.group(1) if match else None

    def test_propose_instead_links_every_meeting_of_a_weekly_proposal(self):
        pod, old = self.dead_proposal(weekly=True)
        self.assertEqual(len(old), 3)
        self.client.force_login(self.anna)
        confirm = self.propose_instead(pod, old[0])
        self.assertEqual(self.hidden(confirm, 'replaces'), str(old[0].pk))
        response = self.client.post(self.meetings_url(pod, '/new'), {
            'start': self.hidden(confirm, 'start'), 'replaces': self.hidden(confirm, 'replaces'),
        }, follow=True)
        self.assertIn('Time proposed. It is confirmed once 2 of you can make it.', message_texts(response))
        new_head = PodMeeting.objects.get(pod=pod, status='proposed')
        self.assertEqual(
            list(PodMeeting.objects.filter(pk__in=[m.pk for m in old]).values_list('replaced_by', flat=True)),
            [new_head.pk] * 3,
        )
        self.assertEqual(new_head.replaced_by_id, None)

    def test_replaced_row_shows_the_hint_under_the_cancelled_badge(self):
        pod, [old] = self.dead_proposal()
        self.client.force_login(self.anna)
        confirm = self.propose_instead(pod, old)
        response = self.client.post(self.meetings_url(pod, '/new'), {
            'start': self.hidden(confirm, 'start'), 'replaces': self.hidden(confirm, 'replaces'),
        }, follow=True)
        self.assertIn('Proposed - 1 of 2 needed', texts(response, 'pod-meeting-badge'))
        self.assertEqual(texts(response, 'pod-meeting-replaced'), [REPLACED])
        old_row = row_html(response, old)
        self.assertEqual(testid_order(old_row, META), ['pod-meeting-badge', 'pod-meeting-replaced'])
        assert_cancelled(self, old_row)

    def test_leaving_the_confirm_page_links_nothing(self):
        pod, [old] = self.dead_proposal()
        self.client.force_login(self.anna)
        self.propose_instead(pod, old)
        response = self.client.get(self.pod_url(pod))
        assert_cancelled(self, row_html(response, old))
        self.assertEqual(texts(response, 'pod-meeting-replaced'), [])

    def test_manual_cancel_then_a_suggested_time_stays_a_plain_cancelled_row(self):
        pod, [old] = self.dead_proposal()
        self.client.force_login(self.anna)
        self.client.post(self.meetings_url(pod, f'/{old.pk}/cancel'))
        page = self.client.get(self.pod_url(pod))
        link = re.findall(r'href="([^"]*meetings/new\?start=[^"]*)"', page.content.decode())[0].replace('&amp;', '&')
        self.assertNotIn('replaces', link)
        confirm = self.client.get(link)
        self.assertIsNone(self.hidden(confirm, 'replaces'))
        response = self.client.post(self.meetings_url(pod, '/new'), {'start': self.hidden(confirm, 'start')}, follow=True)
        self.assertIn('Time proposed.', message_texts(response))
        assert_cancelled(self, row_html(response, old))
        self.assertEqual(texts(response, 'pod-meeting-replaced'), [])
        old.refresh_from_db()
        self.assertIsNone(old.replaced_by_id)

    def test_cancelled_confirmed_meeting_has_no_hint(self):
        pod = self.make_pod()
        meeting = make_meeting(pod, NOW + datetime.timedelta(days=3))
        self.client.force_login(self.anna)
        response = self.client.post(self.meetings_url(pod, f'/{meeting.pk}/cancel'), follow=True)
        assert_cancelled(self, row_html(response, meeting))
        self.assertEqual(texts(response, 'pod-meeting-replaced'), [])

    def test_bad_replaces_values_still_propose_and_link_nothing(self):
        other_pod = make_pod(self.cohort, 'Other pod', [self.basic], owner=self.basic)
        foreign = make_meeting(other_pod, NOW + datetime.timedelta(days=4), status='cancelled')
        self.client.force_login(self.anna)
        cases = {
            'abc': lambda pod: 'abc',
            'other pod': lambda pod: str(foreign.pk),
            'not cancelled': lambda pod: str(make_meeting(pod, NOW + datetime.timedelta(days=20)).pk),
            'already replaced': lambda pod: str(make_meeting(
                pod, NOW + datetime.timedelta(days=21), status='cancelled',
                replaced_by=make_meeting(pod, NOW + datetime.timedelta(days=22), status='cancelled'),
            ).pk),
            'huge': lambda pod: '9' * 40,
        }
        for label, value in cases.items():
            with self.subTest(label):
                pod = self.make_pod(meeting_count=6)
                replaces = value(pod)
                linked_before = {
                    pk: rid for pk, rid in PodMeeting.objects.values_list('pk', 'replaced_by')
                }
                response = self.client.post(self.meetings_url(pod, '/new'), {
                    'start': self.NEW_START.isoformat(), 'replaces': replaces,
                })
                self.assertRedirects(response, self.pod_url(pod), fetch_redirect_response=False)
                new = PodMeeting.objects.get(pod=pod, status='proposed')
                self.assertFalse(PodMeeting.objects.filter(replaced_by=new).exists())
                self.assertEqual(
                    {pk: rid for pk, rid in PodMeeting.objects.exclude(pk=new.pk).values_list('pk', 'replaced_by')},
                    linked_before,
                )
                follow = self.client.get(self.pod_url(pod))
                self.assertIn('Time proposed.', message_texts(follow))

    def test_staff_api_payload_has_no_replaced_by(self):
        pod, [old] = self.dead_proposal()
        self.client.force_login(self.anna)
        confirm = self.propose_instead(pod, old)
        self.client.post(self.meetings_url(pod, '/new'), {
            'start': self.hidden(confirm, 'start'), 'replaces': self.hidden(confirm, 'replaces'),
        })
        old.refresh_from_db()
        self.assertIsNotNone(old.replaced_by_id)
        token = Token.objects.create(user=self.staff, name='pods')
        self.client.logout()
        response = self.client.get(f'/api/pods/{pod.pk}/meetings', HTTP_AUTHORIZATION=f'Token {token.key}')
        payload = response.json()['meetings']
        self.assertIn(old.pk, [m['id'] for m in payload])
        for meeting in payload:
            self.assertNotIn('replaced_by', meeting)
            self.assertNotIn('replaces', meeting)


# --- Item 3: meta line order -------------------------------------------------------------

@tag('core')
@ENABLED
@freeze_time(NOW)
class MovedProposalOrderTest(MeetingViewFixture):
    def moved_proposal(self):
        pod = self.make_pod()
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        mtg.move_meeting(proposal, self.mike, berlin(2026, 10, 14, 18, 0))
        return pod, proposal

    def test_moved_dead_proposal_reads_proposer_mover_then_why(self):
        pod, proposal = self.moved_proposal()
        mtg.respond_to_meeting(proposal, self.raj, MEETING_RESPONSE_CANT)
        mtg.respond_to_meeting(proposal, self.mike, MEETING_RESPONSE_CANT)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-proposed-by'), ['Proposed by Anna K.'])
        self.assertEqual(texts(response, 'pod-meeting-moved'), ['Moved by Mike K. today'])
        self.assertEqual(texts(response, 'pod-meeting-unconfirmable'), [UNCONFIRMABLE_LINE])
        # Mike moved it, so his answer row is the older one.
        self.assertEqual(texts(response, 'pod-meeting-cant'), ["Can't make it: Mike K., Raj S."])
        self.assertEqual(testid_order(row_html(response, proposal), META), [
            'pod-meeting-badge', 'pod-meeting-proposed-by', 'pod-meeting-moved',
            'pod-meeting-unconfirmable', 'pod-meeting-cant',
        ])

    def test_moved_waiting_proposal_reads_proposer_mover_then_waiting(self):
        pod, proposal = self.moved_proposal()
        self.client.force_login(self.raj)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-moved'), ['Moved by Mike K. today'])
        [waiting] = texts(response, 'pod-meeting-waiting')
        self.assertTrue(waiting.startswith('Waiting on you'), waiting)
        self.assertEqual(testid_order(row_html(response, proposal), META), [
            'pod-meeting-badge', 'pod-meeting-proposed-by', 'pod-meeting-moved', 'pod-meeting-waiting',
        ])
