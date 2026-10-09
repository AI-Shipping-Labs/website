"""Member pod meeting pages (issue #1919): access, the Meetings section,
propose/confirm, can't make it with next best time, move, cancel, held,
call link, Pods tab line and the dashboard row.

Every test freezes the clock (date-rot-ok: fixed Friday 2026-10-09 08:00
UTC). Availability windows cover 16:00-18:30 UTC on weekdays for every
member, written in each member's own zone.
"""

import datetime
import re
import uuid
from datetime import UTC
from zoneinfo import ZoneInfo

from django.test import override_settings, tag
from freezegun import freeze_time

from notifications.models import Notification
from pods.models import MEETING_RESPONSE_CANT, PodMeeting, PodMeetingResponse
from pods.services import meetings as mtg
from pods.tests.fixtures import COURSE_SLUG, make_meeting, make_pod, set_windows
from pods.tests.test_member_views import EMAIL_RE, ENABLED, PodViewFixture, _TestIdText

VOID_TAGS = {'input', 'br', 'img', 'hr', 'meta', 'link', 'source', 'wbr'}


class _TestIdTextWithVoids(_TestIdText):
    """``texts`` for blocks that contain void elements such as ``<input>``."""

    def handle_starttag(self, tag, attrs):
        if self.depth and tag in VOID_TAGS:
            return
        super().handle_starttag(tag, attrs)


def texts(response, testid):
    parser = _TestIdTextWithVoids(testid)
    parser.feed(response.content.decode())
    return parser.blocks


def message_texts(response):
    return ' '.join(texts(response, 'messages-region'))

NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday
BERLIN = ZoneInfo('Europe/Berlin')
WEEKDAYS = range(5)


def berlin(*args):
    return datetime.datetime(*args, tzinfo=BERLIN).astimezone(UTC)


class MeetingViewFixture(PodViewFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        set_windows(cls.anna, 'Europe/Berlin', [(d, '18:00', '21:00') for d in WEEKDAYS])
        set_windows(cls.raj, 'Asia/Kolkata', [(d, '21:30', '24:00') for d in WEEKDAYS])
        set_windows(cls.mike, 'America/New_York', [(d, '12:00', '15:00') for d in WEEKDAYS])

    def make_pod(self, members=None, **kwargs):
        members = [self.anna, self.raj, self.mike] if members is None else members
        return make_pod(self.cohort, 'RAG evals study group', members, owner=members[0], **kwargs)

    def meetings_url(self, pod, suffix=''):
        return self.pod_url(pod, f'/meetings{suffix}')

    def series(self, pod, count=4, start=None):
        start = start or berlin(2026, 10, 13, 18, 0)
        series_id = uuid.uuid4()
        return [
            make_meeting(pod, mtg.weekly_start(start, 'Europe/Berlin', k), zone='Europe/Berlin', series_id=series_id)
            for k in range(count)
        ]

    def order_of(self, html, testids):
        return [html.index(f'data-testid="{t}"') for t in testids]


@tag('core')
@ENABLED
@freeze_time(NOW)
class MeetingAccessTest(MeetingViewFixture):
    def test_every_meeting_url_is_404_for_outsiders_and_other_pods(self):
        pod = self.make_pod()
        other = make_pod(self.cohort, 'Other pod', [self.basic], owner=self.basic)
        meeting = make_meeting(pod, NOW + datetime.timedelta(days=3))
        foreign = make_meeting(other, NOW + datetime.timedelta(days=3))
        urls = [
            ('get', self.meetings_url(pod, '/new')),
            ('post', self.meetings_url(pod, f'/{meeting.pk}/respond')),
            ('get', self.meetings_url(pod, f'/{meeting.pk}/move')),
            ('post', self.meetings_url(pod, f'/{meeting.pk}/cancel')),
            ('post', self.meetings_url(pod, f'/{meeting.pk}/held')),
            ('post', self.pod_url(pod, '/call-link')),
        ]
        # Enrolled non-member and a non-enrolled user.
        for user in (self.basic, self.outsider):
            self.client.force_login(user)
            for method, url in urls:
                with self.subTest(user=user.email, url=url):
                    response = getattr(self.client, method)(url, {'response': 'going', 'meeting_url': 'https://x.io'})
                    self.assertEqual(response.status_code, 404)
        # A meeting id from another pod.
        self.client.force_login(self.anna)
        response = self.client.post(self.meetings_url(pod, f'/{foreign.pk}/respond'), {'response': 'going'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PodMeetingResponse.objects.exists())
        pod.refresh_from_db()
        self.assertEqual(pod.meeting_url, '')

    def test_disabled_course_is_404_and_anonymous_goes_to_login(self):
        pod = self.make_pod()
        meeting = make_meeting(pod, NOW + datetime.timedelta(days=3))
        self.client.force_login(self.anna)
        with override_settings(PODS_COURSE_SLUGS=''):
            self.assertEqual(self.client.get(self.meetings_url(pod, '/new')).status_code, 404)
        self.client.logout()
        for url in (self.meetings_url(pod, '/new'), self.meetings_url(pod, f'/{meeting.pk}/move')):
            response = self.client.get(url)
            self.assertRedirects(response, f'/accounts/login/?next={url}', fetch_redirect_response=False)
        url = self.meetings_url(pod, f'/{meeting.pk}/respond')
        self.assertRedirects(self.client.post(url), f'/accounts/login/?next={url}', fetch_redirect_response=False)

    def test_outsider_sees_no_meetings_or_call_link(self):
        pod = self.make_pod(meeting_url='https://meet.google.com/abc-defg-hij')
        self.series(pod)
        self.client.force_login(self.basic)
        response = self.client.get(self.pod_url(pod))
        self.assertContains(response, 'data-testid="pod-detail"')
        self.assertNotContains(response, 'data-testid="pod-meetings"')
        self.assertNotContains(response, 'meet.google.com')
        self.assertNotContains(response, 'data-testid="pod-meeting"')


@tag('core')
@ENABLED
@freeze_time(NOW)
class MeetingsSectionTest(MeetingViewFixture):
    def test_page_order_is_header_meetings_times_slack_members_requests_availability(self):
        pod = self.make_pod()
        self.client.force_login(self.anna)
        html = self.client.get(self.pod_url(pod)).content.decode()
        positions = self.order_of(html, [
            'pod-header', 'pod-meetings', 'pod-suggested-times', 'pod-slack', 'pod-members',
            'pod-requests', 'pod-my-availability',
        ])
        self.assertEqual(positions, sorted(positions))

    def test_empty_states(self):
        solo = self.make_pod([self.anna])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(solo))
        self.assertIn('Meetings start when someone joins', texts(response, 'pod-meetings-empty')[0])
        self.assertIn('Once a second member joins, pick a suggested time together.', texts(response, 'pod-meetings-empty')[0])
        pod = self.make_pod()
        response = self.client.get(self.pod_url(pod))
        self.assertIn('No meetings yet', texts(response, 'pod-meetings-empty')[0])
        self.assertEqual(texts(response, 'pod-meetings-progress-summary'), ['0 of 4 held'])

    def test_rows_badges_and_actions_per_state(self):
        pod = self.make_pod(meeting_count=6)
        held = make_meeting(pod, NOW - datetime.timedelta(days=7), status='held')
        past = make_meeting(pod, NOW - datetime.timedelta(hours=3))
        future = make_meeting(pod, berlin(2026, 10, 14, 18, 0), zone='Europe/Berlin')
        expired = make_meeting(pod, NOW - datetime.timedelta(hours=1), status='proposed')
        cancelled = make_meeting(pod, berlin(2026, 10, 16, 18, 0), status='cancelled')
        proposal = make_meeting(pod, berlin(2026, 10, 20, 18, 0), status='proposed', proposed_by=self.raj,
                                created_via='member')
        PodMeetingResponse.objects.create(meeting=proposal, user=self.raj, response='going')
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        ids = [int(i) for i in re.findall(r'data-meeting-id="(\d+)"', response.content.decode())]
        # Upcoming soonest first, then past newest first.
        self.assertEqual(ids, [future.pk, cancelled.pk, proposal.pk, expired.pk, past.pk, held.pk])
        self.assertEqual(
            texts(response, 'pod-meeting-badge'),
            ['Confirmed', 'Cancelled', 'Proposed - 1 of 2 can make it', 'Not confirmed', 'Confirmed', 'Held'],
        )
        self.assertEqual(
            texts(response, 'pod-meeting-title'),
            ['Meeting 3 of 6', 'Meeting', 'Meeting 4 of 6', 'Meeting', 'Meeting 2 of 6', 'Meeting 1 of 6'],
        )
        self.assertEqual(
            texts(response, 'pod-meeting-actions'),
            [
                "Can't make it Change time Cancel meeting Cancel this meeting? Everyone in the pod gets a "
                'notification. Yes, cancel meeting',
                "I can make it Can't make it Change time Cancel",
                "Mark as held Didn't happen",
            ],
        )
        self.assertEqual(texts(response, 'pod-meeting-proposed-by'), ['Proposed by Raj S.'])
        self.assertEqual(texts(response, 'pod-meetings-progress-summary'), ['1 of 6 held'])

    def test_viewer_going_on_a_proposal_sees_no_i_can_make_it(self):
        pod = self.make_pod()
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-actions'), ["Can't make it Change time Cancel"])
        self.assertEqual(proposal.status, 'proposed')

    def test_times_render_in_the_viewers_zone_with_the_member_strip(self):
        pod = self.make_pod()
        make_meeting(pod, berlin(2026, 10, 14, 18, 0), zone='Europe/Berlin')
        self.client.force_login(self.raj)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-time'), ['Wed Oct 14, 21:30 Asia/Kolkata'])
        self.assertEqual(
            texts(response, 'pod-meeting-strip-item'),
            ['Anna K. 18:00 Berlin', 'Raj S. 21:30 Kolkata', 'Mike K. 12:00 New York'],
        )

    def test_clock_change_line_names_the_member_whose_time_shifts(self):
        pod = self.make_pod([self.anna, self.mike])
        meetings = self.series(pod)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-dst'), ['Clock change: 13:00 for Mike K. this week (usually 12:00).'])
        rows = response.content.decode().split('data-testid="pod-meeting"')
        third = next(r for r in rows if f'data-meeting-id="{meetings[2].pk}"' in r)
        self.assertIn('Clock change: 13:00 for Mike K.', third)
        self.assertEqual(texts(response, 'pod-meeting-time')[2], 'Tue Oct 27, 18:00 Europe/Berlin')

    def test_join_call_only_in_the_join_window_and_with_a_link(self):
        pod = self.make_pod(meeting_url='https://meet.google.com/abc-defg-hij')
        make_meeting(pod, NOW + datetime.timedelta(minutes=5))
        make_meeting(pod, NOW + datetime.timedelta(days=1))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Starting now', 'Confirmed'])
        self.assertContains(
            response,
            '<a href="https://meet.google.com/abc-defg-hij" target="_blank" rel="noopener noreferrer"',
            count=3,
        )
        self.assertEqual(texts(response, 'pod-meeting-join'), ['Join call'])
        self.assertEqual(texts(response, 'pod-meeting-call-link'), ['Call link: meet.google.com/abc-defg-hij'])
        pod.meeting_url = ''
        pod.save()
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-join'), [])

    def test_no_email_appears_on_member_meeting_surfaces(self):
        pod = self.make_pod(meeting_count=6)
        meetings = self.series(pod)
        PodMeetingResponse.objects.create(meeting=meetings[1], user=self.mike, response=MEETING_RESPONSE_CANT)
        PodMeeting.objects.filter(pk=meetings[0].pk).update(moved_by=self.raj, moved_at=NOW)
        self.client.force_login(self.anna)
        for url in (self.pod_url(pod), self.meetings_url(pod, f'/{meetings[1].pk}/move'),
                    self.meetings_url(pod, '/new'), self.pods_url()):
            response = self.client.get(url)
            self.assertTemplateUsed(response, 'pods/_layout.html')
            section = response.content.decode().split('id="main-content"')[1]
            self.assertEqual(EMAIL_RE.findall(section), [], url)


@tag('core')
@ENABLED
@freeze_time(NOW)
class ProposeFlowTest(MeetingViewFixture):
    def test_suggested_slot_proposes_a_weekly_series(self):
        pod = self.make_pod()
        self.client.force_login(self.anna)
        page = self.client.get(self.pod_url(pod))
        propose_links = re.findall(r'href="([^"]*meetings/new\?start=[^"]*)"', page.content.decode())
        self.assertGreater(len(propose_links), 1)
        confirm = self.client.get(propose_links[0].replace('&amp;', '&'))
        self.assertEqual(texts(confirm, 'pod-meeting-repeat-label'), ['Repeat weekly for the remaining 4 meetings'])
        self.assertRegex(confirm.content.decode(), r'name="repeat_weekly" value="1" checked')
        self.assertEqual(texts(confirm, 'pod-meeting-form-fit'), ['Everyone - works well'])
        start = re.search(r'name="start" value="([^"]+)"', confirm.content.decode()).group(1)
        response = self.client.post(self.meetings_url(pod, '/new'), {'start': start, 'repeat_weekly': '1'}, follow=True)
        self.assertIn('Time proposed. It is confirmed once 2 of you can make it.', message_texts(response))
        title = texts(response, 'pod-meeting-title')[0]
        self.assertRegex(title, r'^Proposed: \w+days at \d\d:\d\d Europe/Berlin, 4 meetings from \w{3} \d+$')
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Proposed - 1 of 2 can make it'])
        self.assertEqual(texts(response, 'pod-slot-propose'), [])
        self.assertEqual(
            texts(response, 'pod-times-propose-blocked'),
            ['Confirm or cancel the proposed time before proposing another.'],
        )

    def test_custom_time_page_names_the_zone_and_validates(self):
        pod = self.make_pod()
        self.client.force_login(self.raj)
        page = self.client.get(self.meetings_url(pod, '/new'))
        self.assertEqual(texts(page, 'pod-meeting-zone-line'), ['Times are in GMT+05:30 Asia/Kolkata'])
        response = self.client.post(self.meetings_url(pod, '/new'), {'date': '2026-10-15', 'time': '21:40'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(texts(response, 'pod-meeting-form-error'), ['Pick a start time on the quarter hour.'])
        response = self.client.post(self.meetings_url(pod, '/new'), {'date': '2026-10-15', 'time': '21:30'})
        self.assertRedirects(response, self.pod_url(pod), fetch_redirect_response=False)
        meeting = PodMeeting.objects.get(pod=pod)
        self.assertEqual(meeting.starts_at, datetime.datetime(2026, 10, 15, 16, 0, tzinfo=UTC))
        self.assertEqual(meeting.timezone, 'Asia/Kolkata')

    def test_confirm_from_second_member_schedules_all_meetings(self):
        pod = self.make_pod()
        meetings = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0), repeat_weekly=True)
        self.client.force_login(self.raj)
        response = self.client.post(
            self.meetings_url(pod, f'/{meetings[0].pk}/respond'), {'response': 'going'}, follow=True,
        )
        self.assertIn('Meeting confirmed.', message_texts(response))
        self.assertEqual(texts(response, 'pod-meeting-title'),
                         ['Meeting 1 of 4', 'Meeting 2 of 4', 'Meeting 3 of 4', 'Meeting 4 of 4'])
        self.assertEqual(set(texts(response, 'pod-meeting-badge')), {'Confirmed'})
        self.assertEqual(texts(response, 'pod-times-propose-blocked'), ['All 4 meetings are planned.'])

    def test_same_weekly_time_leads_the_suggestions_after_a_one_off_meeting(self):
        pod = self.make_pod()
        first = make_meeting(pod, berlin(2026, 10, 12, 18, 0), zone='Europe/Berlin')
        self.client.force_login(self.raj)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-slot-same-week'), ['Same weekly time'])
        html = response.content.decode()
        self.assertTrue(html.split('data-testid="pod-slot"')[1].startswith(' data-same-week="true"'))
        expected = mtg.weekly_start(first.starts_at, 'Europe/Berlin', 1)
        starts = [datetime.datetime.fromisoformat(v)
                  for v in re.findall(r'<time datetime="([^"]+)" data-testid="pod-slot-time"', html)]
        self.assertEqual(starts[0], expected)
        # No ranked slot repeats the same weekly time.
        for start in starts[1:]:
            self.assertNotEqual((start.weekday(), start.hour), (expected.weekday(), expected.hour))

    def test_propose_actions_hide_when_all_meetings_are_planned_and_return_after_a_cancel(self):
        pod = self.make_pod(meeting_count=2)
        make_meeting(pod, NOW - datetime.timedelta(days=7), status='held')
        confirmed = make_meeting(pod, NOW + datetime.timedelta(days=3))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-slot-propose'), [])
        self.assertEqual(texts(response, 'pod-times-propose-blocked'), ['All 2 meetings are planned.'])
        response = self.client.post(self.meetings_url(pod, f'/{confirmed.pk}/cancel'), follow=True)
        self.assertIn('Meeting cancelled.', message_texts(response))
        self.assertGreater(len(texts(response, 'pod-slot-propose')), 0)

    def test_lowering_meeting_count_in_the_edit_form_is_rejected(self):
        pod = self.make_pod(meeting_count=2)
        make_meeting(pod, NOW - datetime.timedelta(days=7), status='held')
        make_meeting(pod, NOW + datetime.timedelta(days=3))
        self.client.force_login(self.anna)
        response = self.client.post(self.pod_url(pod, '/edit'), {
            'name': pod.name, 'purpose': pod.purpose, 'max_members': '4', 'meeting_count': '1',
            'meeting_minutes': '60', 'status': 'open',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            texts(response, 'pod-form-error'),
            ['This pod already has 2 meetings planned or held. Cancel a meeting before lowering the number.'],
        )


@tag('core')
@ENABLED
@freeze_time(NOW)
class ChangeFlowTest(MeetingViewFixture):
    def test_cant_make_it_then_one_tap_move_to_the_next_best_time(self):
        pod = self.make_pod()
        meetings = self.series(pod)
        self.client.force_login(self.mike)
        response = self.client.post(
            self.meetings_url(pod, f'/{meetings[1].pk}/respond'), {'response': 'cant_make_it'}, follow=True,
        )
        self.assertIn("Marked that you can't make it. The pod sees the next best time.", message_texts(response))
        self.assertEqual(texts(response, 'pod-meeting-cant'), ["Can't make it: Mike K."])
        self.assertIn('I can make it after all', texts(response, 'pod-meeting-actions')[1])

        self.client.force_login(self.anna)
        page = self.client.get(self.pod_url(pod))
        [offer_time] = texts(page, 'pod-meeting-offer-time')
        [button] = texts(page, 'pod-meeting-offer-move')
        self.assertEqual(button, f'Move to {offer_time.rsplit(" ", 1)[0]}')
        start = re.search(r'name="start" value="([^"]+)"', page.content.decode()).group(1)
        offer_start = datetime.datetime.fromisoformat(start)
        self.assertGreaterEqual(abs(offer_start - meetings[1].starts_at), datetime.timedelta(minutes=60))
        self.assertLess(offer_start, meetings[2].starts_at)
        response = self.client.post(self.meetings_url(pod, f'/{meetings[1].pk}/move'), {'start': start}, follow=True)
        self.assertIn('Meeting moved.', message_texts(response))
        self.assertEqual(texts(response, 'pod-meeting-moved'), ['Moved by Anna K. on Oct 9'])
        self.assertEqual(texts(response, 'pod-meeting-cant'), [])
        for later in meetings[2:]:
            self.assertEqual(PodMeeting.objects.get(pk=later.pk).starts_at, later.starts_at)
        self.assertTrue(Notification.objects.filter(
            user=self.mike, title__startswith='Anna K. moved RAG evals study group meeting 2 to ',
            title__endswith='America/New_York',
        ).exists())

    def test_no_candidate_line_when_no_other_time_fits(self):
        pod = self.make_pod()
        meeting = make_meeting(pod, berlin(2026, 10, 14, 18, 0), zone='Europe/Berlin')
        PodMeetingResponse.objects.create(meeting=meeting, user=self.mike, response=MEETING_RESPONSE_CANT)
        for user in (self.anna, self.raj, self.mike):
            set_windows(user, 'Europe/Berlin', [(2, '18:00', '19:00')])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(
            texts(response, 'pod-meeting-no-offer'),
            ['No other time fits at least 2 of you in the next 2 weeks.'],
        )
        self.assertIn('Change time', texts(response, 'pod-meeting-actions')[0])

    def test_change_time_form_moves_the_later_meetings_too(self):
        pod = self.make_pod()
        meetings = self.series(pod)
        self.client.force_login(self.raj)
        form = self.client.get(self.meetings_url(pod, f'/{meetings[1].pk}/move'))
        self.assertContains(form, 'Also move the later meetings to this weekly time')
        self.assertEqual(texts(form, 'pod-meeting-zone-line'), ['Times are in GMT+05:30 Asia/Kolkata'])
        response = self.client.post(self.meetings_url(pod, f'/{meetings[1].pk}/move'), {
            'choice': 'custom', 'date': '2026-10-22', 'time': '17:00', 'move_later': '1',
        }, follow=True)
        self.assertIn('Meeting moved.', message_texts(response))
        kolkata = ZoneInfo('Asia/Kolkata')
        self.assertEqual(
            [PodMeeting.objects.get(pk=m.pk).starts_at.astimezone(kolkata).strftime('%a %d %H:%M') for m in meetings[1:]],
            ['Thu 22 17:00', 'Thu 29 17:00', 'Thu 05 17:00'],
        )
        self.assertEqual(PodMeeting.objects.get(pk=meetings[0].pk).starts_at, meetings[0].starts_at)

    def test_move_form_redirects_for_a_past_meeting(self):
        pod = self.make_pod()
        past = make_meeting(pod, NOW - datetime.timedelta(hours=2))
        self.client.force_login(self.anna)
        response = self.client.get(self.meetings_url(pod, f'/{past.pk}/move'), follow=True)
        self.assertIn('This meeting has already happened.', message_texts(response))

    def test_proposal_offer_cancels_and_opens_the_confirm_page(self):
        pod = self.make_pod()
        [proposal] = mtg.propose_meeting(pod, self.anna, berlin(2026, 10, 13, 18, 0))
        mtg.respond_to_meeting(proposal, self.mike, MEETING_RESPONSE_CANT)
        self.client.force_login(self.raj)
        page = self.client.get(self.pod_url(pod))
        [button] = texts(page, 'pod-meeting-offer-propose')
        self.assertTrue(button.startswith('Propose ') and button.endswith(' instead'))
        start = re.search(r'name="then_propose" value="([^"]+)"', page.content.decode()).group(1)
        response = self.client.post(self.meetings_url(pod, f'/{proposal.pk}/cancel'), {'then_propose': start})
        self.assertRedirects(
            response, f'{self.meetings_url(pod, "/new")}?start={start.replace("+", "%2B").replace(":", "%3A")}',
            fetch_redirect_response=False,
        )
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'cancelled')

    def test_mark_as_held_counts_in_the_progress_block(self):
        pod = self.make_pod()
        started = make_meeting(pod, NOW - datetime.timedelta(hours=2))
        self.client.force_login(self.mike)
        response = self.client.post(self.meetings_url(pod, f'/{started.pk}/held'), {'outcome': 'held'}, follow=True)
        self.assertIn('Marked as held.', message_texts(response))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Held'])
        self.assertEqual(texts(response, 'pod-meeting-actions'), [])
        self.assertEqual(texts(response, 'pod-meetings-progress-summary'), ['1 of 4 held'])

    def test_call_link_rejects_http_then_saves_and_clears(self):
        pod = self.make_pod()
        self.client.force_login(self.anna)
        response = self.client.post(self.pod_url(pod, '/call-link'), {'meeting_url': 'http://meet.google.com/abc'}, follow=True)
        self.assertEqual(
            texts(response, 'pod-call-link-error'),
            ['Paste an https link to your call, for example https://meet.google.com/abc-defg-hij.'],
        )
        self.assertContains(response, 'value="http://meet.google.com/abc"')
        response = self.client.post(
            self.pod_url(pod, '/call-link'), {'meeting_url': 'https://meet.google.com/abc-defg-hij'}, follow=True,
        )
        self.assertIn('Call link saved.', message_texts(response))
        self.assertEqual(texts(response, 'pod-call-link-line'), ['Call link: meet.google.com/abc-defg-hij'])
        response = self.client.post(self.pod_url(pod, '/call-link'), {'meeting_url': ''}, follow=True)
        self.assertIn('Call link removed.', message_texts(response))
        self.assertEqual(texts(response, 'pod-call-link-toggle'), ['Add a call link'])


@tag('core')
@ENABLED
@freeze_time(NOW)
class PodsTabAndDashboardTest(MeetingViewFixture):
    def test_pods_tab_shows_next_meeting_or_waiting_line_on_own_pods_only(self):
        mine = self.make_pod()
        make_meeting(mine, berlin(2026, 10, 14, 18, 0))
        proposing = make_pod(self.cohort, 'Proposal pod', [self.anna, self.raj], owner=self.anna)
        mtg.propose_meeting(proposing, self.anna, berlin(2026, 10, 15, 18, 0))
        other = make_pod(self.cohort, 'Other pod', [self.basic, self.raj], owner=self.basic)
        make_meeting(other, berlin(2026, 10, 16, 18, 0))
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        self.assertEqual(
            sorted(texts(response, 'pod-row-meeting')),
            ['Next meeting: Wed Oct 14, 18:00 Europe/Berlin', 'Time proposed - waiting for confirmation'],
        )

    def test_dashboard_your_week_lists_scheduled_pod_meetings_the_viewer_can_make(self):
        pod = self.make_pod()
        meetings = self.series(pod)
        PodMeetingResponse.objects.create(meeting=meetings[0], user=self.raj, response=MEETING_RESPONSE_CANT)
        self.client.force_login(self.anna)
        response = self.client.get('/')
        self.assertEqual(texts(response, 'dashboard-pod-meeting-title'), ['RAG evals study group - Meeting 1 of 4'])
        self.assertEqual(texts(response, 'dashboard-pod-meeting-time'), ['Tue Oct 13, 18:00 Europe/Berlin'])
        self.assertContains(response, f'href="/courses/{COURSE_SLUG}/home/pods/{pod.pk}"')
        self.client.force_login(self.raj)
        response = self.client.get('/')
        self.assertEqual(texts(response, 'dashboard-pod-meeting-title'), [])


@tag('core')
@ENABLED
@freeze_time(NOW)
class ProposeBlockedTest(MeetingViewFixture):
    def test_confirm_page_sends_members_back_when_proposing_is_blocked(self):
        cases = [
            (self.make_pod([self.anna]), 'Meetings start when someone joins.'),
            (self.make_pod(meeting_count=1), 'All 1 meetings are planned.'),
        ]
        make_meeting(cases[1][0], NOW + datetime.timedelta(days=2))
        proposal_pod = self.make_pod()
        mtg.propose_meeting(proposal_pod, self.raj, berlin(2026, 10, 13, 18, 0))
        cases.append((proposal_pod, 'Confirm or cancel the proposed time before proposing another.'))
        self.client.force_login(self.anna)
        for pod, message in cases:
            with self.subTest(message=message):
                response = self.client.get(self.meetings_url(pod, '/new'), follow=True)
                self.assertEqual(message_texts(response), message)
                self.assertRedirects(response, self.pod_url(pod))


@tag('core')
@ENABLED
@freeze_time(NOW)
class ProposalJoinWindowTest(MeetingViewFixture):
    def test_a_proposal_in_the_join_window_shows_the_call_link_not_join_call(self):
        pod = self.make_pod(meeting_url='https://meet.google.com/abc-defg-hij')
        make_meeting(pod, NOW + datetime.timedelta(minutes=5), status='proposed', created_via='member')
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-meeting-badge'), ['Proposed - 0 of 2 can make it'])
        self.assertEqual(texts(response, 'pod-meeting-join'), [])
        self.assertEqual(texts(response, 'pod-meeting-call-link'), ['Call link: meet.google.com/abc-defg-hij'])
