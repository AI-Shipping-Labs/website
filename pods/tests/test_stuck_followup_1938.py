"""Stuck-proposal alert follow-ups (issue #1938).

The halfway rule, the ``N proposals waiting`` Studio list line, the
``Can't be confirmed`` label for staff (Studio, API, Slack), the new Slack
headline, and long Slack lines cut on whole names.

The clock is frozen (date-rot-ok: fixed Friday 2026-10-09 08:00 UTC).
"""

import json
import re

from django.db import connection
from django.test import tag
from django.test.utils import CaptureQueriesContext
from freezegun import freeze_time

from accounts.models import Token, User
from pods.models import (
    MEETING_RESPONSE_CANT,
    MEETING_RESPONSE_GOING,
    Pod,
    PodJoinRequest,
    PodMeeting,
    PodMeetingResponse,
)
from pods.services import meetings as mtg
from pods.services.stale_requests import (
    MAX_SECTION_CHARS,
    build_pods_alert_message,
    proposal_line,
    request_line,
    send_stale_request_alert,
)
from pods.services.stuck_proposals import ProposalWait, stuck_proposals, stuck_threshold
from pods.tests.fixtures import make_meeting
from pods.tests.test_meeting_views import texts
from pods.tests.test_stuck_proposals_1935 import (
    BASE_URL,
    DAY,
    HOUR,
    NOW,
    StuckFixture,
    _payload,
    _section_texts,
)

UNCONFIRMABLE = " - can't be confirmed, needs another time"


def _has_partial_link(text):
    """``True`` when a ``<`` opens a link that never closes."""
    return text.rfind('<') > text.rfind('>')


def _has_partial_entity(text):
    """``True`` when an ``&`` is not the start of a whole escaped entity."""
    return any(not text.startswith(('&amp;', '&lt;', '&gt;'), m.start()) for m in re.finditer('&', text))


class FollowupFixture(StuckFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.token = Token.objects.create(user=self.staff, name='pods-1938')

    def surfaces(self, pod, head):
        """``(studio stuck badge, api stuck, in Slack proposals section)``."""
        detail = self.client.get(f'/studio/pods/{pod.pk}/')
        api = self.client.get(f'/api/pods/{pod.pk}/meetings', HTTP_AUTHORIZATION=f'Token {self.token.key}')
        payload = {m['id']: m for m in json.loads(api.content)['meetings']}[head.pk]
        self.post.reset_mock()
        PodMeeting.objects.filter(pk=head.pk).update(stuck_alerted_at=None)
        send_stale_request_alert(now=NOW)
        in_slack = self.post.called and any(
            f'/studio/pods/{pod.pk}/|' in text for text in _section_texts(_payload(self.post))
        )
        return texts(detail, 'studio-pod-meeting-stuck') == ['Stuck'], payload['stuck'], in_slack

    def api_meetings(self, pod):
        response = self.client.get(f'/api/pods/{pod.pk}/meetings', HTTP_AUTHORIZATION=f'Token {self.token.key}')
        return {m['id']: m for m in json.loads(response.content)['meetings']}


# --- Halfway rule ------------------------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class HalfwayRuleTest(FollowupFixture):
    def test_threshold_is_the_smaller_of_the_setting_and_half_the_notice(self):
        for notice, expected in ((72 * HOUR, 36 * HOUR), (10 * DAY, 48 * HOUR), (20 * HOUR, 10 * HOUR)):
            with self.subTest(notice=notice):
                self.assertEqual(stuck_threshold(NOW, NOW + notice, 48), expected)

    def test_72_hour_notice_is_stuck_after_36_hours_on_every_surface(self):
        young = self.pod('Young')
        young_head = self.proposal(young, age=35 * HOUR, starts_in=37 * HOUR)
        old = self.pod('Old')
        old_head = self.proposal(old, age=37 * HOUR, starts_in=35 * HOUR)
        self.assertEqual(self.surfaces(young, young_head), (False, False, False))
        self.assertEqual(self.surfaces(old, old_head), (True, True, True))

    def test_10_day_notice_keeps_the_48_hour_threshold(self):
        young = self.pod('Young')
        young_head = self.proposal(young, age=47 * HOUR, starts_in=10 * DAY - 47 * HOUR)
        old = self.pod('Old')
        old_head = self.proposal(old, age=49 * HOUR, starts_in=10 * DAY - 49 * HOUR)
        self.assertEqual(self.surfaces(young, young_head), (False, False, False))
        self.assertEqual(self.surfaces(old, old_head), (True, True, True))

    def test_20_hour_notice_is_stuck_after_10_hours_and_in_the_next_alert(self):
        pod = self.pod('Short notice')
        head = self.proposal(pod, age=11 * HOUR, starts_in=9 * HOUR)
        self.assertEqual(send_stale_request_alert(now=NOW), 1)
        headline, line = _section_texts(_payload(self.post))
        self.assertEqual(headline, '*1 pod proposal has waited more than 48 hours or half the time to the meeting*')
        self.assertIn('|Short notice>', line)
        head.refresh_from_db()
        self.assertEqual(head.stuck_alerted_at, NOW)

    def test_a_move_restarts_the_clock_and_recomputes_the_halfway_threshold(self):
        stuck_pod = self.pod('Moved 16h ago')
        stuck_head = self.proposal(stuck_pod, age=5 * DAY, starts_in=6 * DAY)
        fresh_pod = self.pod('Moved 14h ago')
        fresh_head = self.proposal(fresh_pod, age=5 * DAY, starts_in=6 * DAY)
        # Each moved to a time 30 hours after the move: stuck after 15 hours.
        with freeze_time(NOW - 16 * HOUR):
            mtg.move_meeting(stuck_head, self.anna, NOW + 14 * HOUR, now=NOW - 16 * HOUR)
        with freeze_time(NOW - 14 * HOUR):
            mtg.move_meeting(fresh_head, self.anna, NOW + 16 * HOUR, now=NOW - 14 * HOUR)

        self.assertEqual([w.head.pk for w in stuck_proposals(now=NOW)], [stuck_head.pk])

    def test_expired_archived_and_series_tail_stay_never_stuck_under_the_halfway_rule(self):
        self.proposal(self.pod('Expired'), age=2 * HOUR, starts_in=-HOUR)
        self.proposal(self.pod('Archived', status='archived'), age=11 * HOUR, starts_in=9 * HOUR)
        head, *tail = self.proposal(self.pod('Series'), age=11 * HOUR, starts_in=9 * HOUR, series=3)
        stuck = [w.head.pk for w in stuck_proposals(now=NOW)]
        self.assertEqual(stuck, [head.pk])
        self.assertTrue(all(m.pk not in stuck for m in tail))


# --- Studio list proposal count -----------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class StudioListProposalCountTest(FollowupFixture):
    def row_line(self, response, pod):
        html = response.content.decode()
        rows = {r.split('"', 1)[0]: r for r in html.split('data-pod-id="')[1:]}
        row = rows[str(pod.pk)]
        return re.search(r'data-testid="studio-pod-next-meeting">([^<]*)<', row).group(1)

    def test_proposal_count_replaces_no_meeting_planned(self):
        one = self.pod('One proposal')
        self.proposal(one, age=HOUR)
        two = self.pod('Two proposals')
        self.proposal(two, age=HOUR)
        self.proposal(two, age=HOUR, starts_in=5 * DAY, series=3)
        scheduled = self.pod('Scheduled')
        make_meeting(scheduled, NOW + DAY)
        self.proposal(scheduled, age=HOUR, starts_in=7 * DAY)
        expired = self.pod('Only expired')
        self.proposal(expired, age=3 * DAY, starts_in=-HOUR)
        idle = self.pod('Idle')

        response = self.client.get('/studio/pods/')

        self.assertEqual(self.row_line(response, one), '1 proposal waiting')
        self.assertEqual(self.row_line(response, two), '2 proposals waiting')
        self.assertEqual(self.row_line(response, scheduled), 'Next 2026-10-10 08:00')
        self.assertEqual(self.row_line(response, expired), 'No meeting planned')
        self.assertEqual(self.row_line(response, idle), 'No meeting planned')

    def test_query_count_does_not_grow_with_rows(self):
        def queries(expected_rows):
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get('/studio/pods/')
            self.assertEqual(len(texts(response, 'studio-pod-next-meeting')), expected_rows)
            return len(captured.captured_queries)

        first = self.pod('Pod 0')
        self.proposal(first, age=3 * DAY)
        make_meeting(first, NOW + DAY)
        queries(1)  # The first request also warms per-process caches.
        single = queries(1)
        for i in range(1, 5):
            pod = self.pod(f'Pod {i}')
            self.proposal(pod, age=3 * DAY)
            self.proposal(pod, age=HOUR, starts_in=6 * DAY, series=2)
        self.assertEqual(queries(5), single)


# --- Can't be confirmed ---------------------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class UnconfirmableForStaffTest(FollowupFixture):
    def setUp(self):
        super().setUp()
        self.pod3 = self.pod('Trio', members=[self.anna, self.mike, self.raj])

    def cant(self, head, *users):
        for user in users:
            PodMeetingResponse.objects.update_or_create(
                meeting=head, user=user, defaults={'response': MEETING_RESPONSE_CANT},
            )

    def test_slack_line_ends_with_the_label_only_when_unconfirmable(self):
        unconfirmable = self.proposal(self.pod3, age=3 * DAY)
        self.cant(unconfirmable, self.mike, self.raj)
        self.proposal(self.pod('Duo ok', members=[self.anna, self.mike]), age=3 * DAY)

        send_stale_request_alert(now=NOW)

        lines = _section_texts(_payload(self.post))[1].split('\n')
        by_pod = {line.split('|')[1].split('>')[0]: line for line in lines}
        self.assertTrue(by_pod['Trio'].endswith(" - can't make it: Mike K., Raj P." + UNCONFIRMABLE))
        self.assertNotIn("can't be confirmed", by_pod['Duo ok'])

    def test_studio_detail_shows_the_label_before_stuck_whether_or_not_stuck(self):
        fresh = self.proposal(self.pod3, age=2 * HOUR)
        self.cant(fresh, self.mike, self.raj)
        response = self.client.get(f'/studio/pods/{self.pod3.pk}/')
        self.assertEqual(texts(response, 'studio-pod-meeting-unconfirmable'), ["Can't be confirmed"])
        self.assertEqual(texts(response, 'studio-pod-meeting-stuck'), [])

        PodMeeting.objects.filter(pk=fresh.pk).update(created_at=NOW - 3 * DAY)
        response = self.client.get(f'/studio/pods/{self.pod3.pk}/')
        [cell] = texts(response, 'studio-pod-meeting-status')
        self.assertLess(cell.index("Can't be confirmed"), cell.index('Stuck'))

    def test_label_clears_when_a_member_switches_back_or_the_proposal_moves(self):
        head = self.proposal(self.pod3, age=2 * HOUR)
        self.cant(head, self.mike, self.raj)
        PodMeetingResponse.objects.filter(meeting=head, user=self.raj).update(response=MEETING_RESPONSE_GOING)
        response = self.client.get(f'/studio/pods/{self.pod3.pk}/')
        self.assertEqual(texts(response, 'studio-pod-meeting-unconfirmable'), [])

        self.cant(head, self.raj)
        mtg.move_meeting(head, self.anna, NOW + 5 * DAY, now=NOW)
        response = self.client.get(f'/studio/pods/{self.pod3.pk}/')
        self.assertEqual(texts(response, 'studio-pod-meeting-unconfirmable'), [])

    def test_label_never_on_scheduled_or_series_tail_meetings(self):
        head, *tail = self.proposal(self.pod3, age=2 * HOUR, series=2)
        self.cant(head, self.mike, self.raj)
        scheduled = make_meeting(self.pod3, NOW + 30 * DAY)
        self.cant(scheduled, self.mike, self.raj)
        response = self.client.get(f'/studio/pods/{self.pod3.pk}/')
        self.assertEqual(texts(response, 'studio-pod-meeting-unconfirmable'), ["Can't be confirmed"])

    def test_api_confirmable_true_false_and_null(self):
        unconfirmable, tail = self.proposal(self.pod3, age=2 * HOUR, series=2)
        self.cant(unconfirmable, self.mike, self.raj)
        confirmable = self.proposal(self.pod3, age=2 * HOUR, starts_in=2 * DAY)
        self.cant(confirmable, self.raj)
        scheduled = make_meeting(self.pod3, NOW + 30 * DAY)
        held = make_meeting(self.pod3, NOW - 2 * DAY, status='held')
        cancelled = make_meeting(self.pod3, NOW + 31 * DAY, status='cancelled')
        expired = make_meeting(self.pod3, NOW - DAY, status='proposed', created_via='member', proposed_by=self.anna)
        self.cant(expired, self.mike, self.raj)

        meetings = self.api_meetings(self.pod3)

        self.assertIs(meetings[unconfirmable.pk]['confirmable'], False)
        self.assertIs(meetings[confirmable.pk]['confirmable'], True)
        for other in (tail, scheduled, held, cancelled, expired):
            self.assertIsNone(meetings[other.pk]['confirmable'], other.status)


# --- Long Slack lines ---------------------------------------------------------------------

def _user(pk, first, last='', email=None):
    return User(pk=pk, email=email or f'user{pk}@example.com', first_name=first, last_name=last)


@tag('core')
@freeze_time(NOW)
class LongLineTest(StuckFixture):
    def wait(self, pod, *, waiting=(), cant=(), unconfirmable=False):
        head = PodMeeting(
            pk=9000, pod=pod, starts_at=NOW + 2 * DAY, status='proposed',
            proposed_by=self.anna, created_at=NOW - 3 * DAY,
        )
        return ProposalWait(
            head=head, waiting=list(waiting), cant=list(cant),
            waiting_since=head.created_at, stuck=True, unconfirmable=unconfirmable,
        )

    def unsaved_pod(self, name):
        return Pod(pk=8000, name=name, cohort=self.cohort)

    def test_200_waiting_members_drop_whole_entries_and_end_with_and_n_more(self):
        waiting = [
            _user(1000 + i, f'Member & <{i:03d}> with a fairly long first name', 'Lastname',
                  email=f'a-very-long-address-number-{i:03d}@example-university.edu')
            for i in range(200)
        ]
        cant = [_user(2000 + i, f'Cant {i}', 'Z') for i in range(3)]
        line = proposal_line(self.wait(self.unsaved_pod('Pod A'), waiting=waiting, cant=cant), BASE_URL, NOW)

        self.assertLess(len(line), MAX_SECTION_CHARS)
        waiting_part = line.split(' - waiting on ', 1)[1].split(" - can't make it: ", 1)[0]
        # Every entry ends ``)``; escaped names contain ``; `` themselves.
        shown = waiting_part.split('); ')
        self.assertRegex(shown[-1], r'^and \d+ more$')
        self.assertEqual(len(shown) - 1 + int(shown[-1].split()[1]), 200)
        self.assertTrue(line.endswith(" - can't make it: Cant 0 Z., Cant 1 Z., Cant 2 Z."))
        self.assertFalse(_has_partial_entity(line))
        self.assertFalse(_has_partial_link(line))

    def test_5000_character_pod_name_is_cut_with_an_ellipsis_in_both_sections(self):
        hostile = ('Tom & Jerry <crew> ' * 300)[:5000]
        pod = self.unsaved_pod(hostile)
        owner = _user(3000, 'Owner', 'O')
        pod.owner = owner
        join_request = PodJoinRequest(pk=7000, pod=pod, user=_user(3001, 'Req', 'R'), created_at=NOW - 8 * DAY)
        unconfirmable_wait = self.wait(pod, cant=[_user(3002, 'Mike', 'K')], unconfirmable=True)

        message = build_pods_alert_message(
            [join_request], 0, [unconfirmable_wait], 0, stale_days=5, stuck_hours=48, now=NOW,
        )

        sections = [b['text']['text'] for b in message['blocks'] if b['type'] == 'section']
        request_text, proposal_text = sections[1], sections[3]
        for text in (request_text, proposal_text):
            with self.subTest(text=text[:40]):
                self.assertLessEqual(len(text), MAX_SECTION_CHARS)
                self.assertTrue(text.endswith('...'))
                self.assertFalse(_has_partial_entity(text))
                self.assertFalse(_has_partial_link(text))
                self.assertTrue(text.startswith(f'<{BASE_URL}/studio/pods/8000/|Tom &amp; Jerry &lt;crew&gt;'))
        self.assertTrue(all(len(text) <= MAX_SECTION_CHARS for text in sections))

    def proposal_text(self, pod):
        wait = self.wait(pod, waiting=[_user(4000, 'Mike', 'K')])
        message = build_pods_alert_message([], 0, [wait], 0, stale_days=5, stuck_hours=48, now=NOW)
        return [b['text']['text'] for b in message['blocks'] if b['type'] == 'section'][1]

    def assert_whole_link(self, text, escaped_name):
        """``<url|label>...``: one complete link whose label starts ``escaped_name``."""
        prefix = f'<{BASE_URL}/studio/pods/8000/|'
        self.assertTrue(text.startswith(prefix))
        self.assertTrue(text.endswith('>...'))
        label = text[len(prefix):-len('>...')]
        self.assertTrue(label)
        self.assertTrue(escaped_name.startswith(label))
        self.assertEqual((text.count('<'), text.count('>')), (1, 1))

    def test_pod_name_without_spaces_is_never_cut_inside_an_entity(self):
        # Shifting the name by 0-4 characters lands the cut on every
        # position inside ``&amp;`` (five characters).
        for shift in range(5):
            with self.subTest(shift=shift):
                name = 'x' * shift + '&' * 2000
                text = self.proposal_text(self.unsaved_pod(name))
                self.assertLessEqual(len(text), MAX_SECTION_CHARS)
                self.assertFalse(_has_partial_entity(text))
                self.assert_whole_link(text, 'x' * shift + '&amp;' * 2000)

    def test_cut_inside_the_pod_link_keeps_the_link_whole_and_ends_on_a_word(self):
        for word in ('crew', 'builders', 'ab'):
            with self.subTest(word=word):
                name = f'{word} & ' * 1500
                escaped = name.replace('&', '&amp;')
                text = self.proposal_text(self.unsaved_pod(name))
                self.assertLessEqual(len(text), MAX_SECTION_CHARS)
                self.assert_whole_link(text, escaped)
                label = text[len(f'<{BASE_URL}/studio/pods/8000/|'):-len('>...')]
                # Ends on a whole word: the escaped name continues with a space.
                self.assertEqual(escaped[len(label)], ' ')
                self.assertFalse(_has_partial_entity(text))

    def test_cut_outside_the_link_ends_on_a_word_boundary(self):
        pod = self.unsaved_pod('Pod A')
        pod.owner = _user(5000, 'Owner', 'O')
        for word in ('alpha', 'bravo1', 'charlie12'):
            with self.subTest(word=word):
                requester = _user(5001, f'{word} ' * 800, 'R')
                join_request = PodJoinRequest(pk=7001, pod=pod, user=requester, created_at=NOW - 8 * DAY)
                original = request_line(join_request, BASE_URL, NOW)
                message = build_pods_alert_message(
                    [join_request], 0, [], 0, stale_days=5, stuck_hours=48, now=NOW,
                )
                text = [b['text']['text'] for b in message['blocks'] if b['type'] == 'section'][1]
                self.assertLessEqual(len(text), MAX_SECTION_CHARS)
                self.assertTrue(text.endswith('...'))
                kept = text[:-len('...')]
                self.assertTrue(original.startswith(kept))
                self.assertEqual(original[len(kept)], ' ')
                self.assertFalse(_has_partial_link(text))

