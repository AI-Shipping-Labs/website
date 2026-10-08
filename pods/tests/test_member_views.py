"""Member Pods pages: enablement, access, list, pod page, actions (issue #1918)."""

import datetime
import re
from html.parser import HTMLParser

from django.test import TestCase, override_settings, tag
from django.utils import timezone
from freezegun import freeze_time

from content.models import CourseAccess
from notifications.models import Notification
from payments.models import Tier
from pods.models import POD_SOURCE_STUDIO, Pod, PodJoinRequest, PodMembership
from pods.services import membership as svc
from pods.tests.fixtures import (
    COURSE_SLUG,
    enroll,
    make_cohort,
    make_course,
    make_user,
    set_windows,
)
from tests.fixtures import set_membership

ENABLED = override_settings(PODS_COURSE_SLUGS=COURSE_SLUG, SLACK_TEAM_ID='T0TEAM')
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')


class _TestIdText(HTMLParser):
    """Collect the text inside every element carrying a given data-testid."""

    def __init__(self, testid):
        super().__init__()
        self.testid = testid
        self.depth = 0
        self.chunks = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.depth += 1
        elif dict(attrs).get('data-testid') == self.testid:
            self.depth = 1
            self.chunks = []

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            if not self.depth:
                self.blocks.append(' '.join(' '.join(self.chunks).split()))

    def handle_data(self, data):
        if self.depth:
            self.chunks.append(data)


def texts(response, testid):
    parser = _TestIdText(testid)
    parser.feed(response.content.decode())
    return parser.blocks


def _pod(cohort, name, members=(), owner=None, max_members=4, status='open', created_offset=0):
    pod = Pod.objects.create(
        cohort=cohort, name=name, purpose=f'{name} purpose', max_members=max_members,
        owner=owner, source=POD_SOURCE_STUDIO, status=status,
    )
    if created_offset:
        Pod.objects.filter(pk=pod.pk).update(created_at=timezone.now() - datetime.timedelta(minutes=created_offset))
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source='staff')
    return pod


class PodViewFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.main_tier = Tier.objects.get(slug='main')
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@test.com', first_name='Raj', last_name='Shah', timezone_name='Asia/Kolkata')
        cls.mike = make_user('mike@test.com', first_name='Mike', last_name='Kay', timezone_name='America/New_York')
        cls.basic = make_user('basic@test.com', first_name='Bea', last_name='Sic')
        cls.outsider = make_user('main@test.com', first_name='Out', last_name='Sider')
        cls.staff = make_user('admin@test.com', staff=True)
        for user in (cls.anna, cls.raj, cls.mike, cls.outsider):
            set_membership(user, tier=cls.main_tier)
        set_membership(cls.basic, tier=Tier.objects.get(slug='basic'))
        for user in (cls.anna, cls.raj, cls.mike, cls.basic):
            enroll(user, cls.cohort)
        CourseAccess.objects.create(user=cls.basic, course=cls.course, access_type='granted')

    def pods_url(self, suffix=''):
        return f'/courses/{COURSE_SLUG}/home/pods{suffix}'

    def pod_url(self, pod, suffix=''):
        return f'/courses/{COURSE_SLUG}/home/pods/{pod.pk}{suffix}'


@tag('core')
class EnablementAndAccessTest(PodViewFixture):
    def test_disabled_course_404s_every_pod_url_and_hides_tab(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna])
        self.client.force_login(self.anna)
        for url in (self.pods_url(), self.pods_url('/new'), self.pods_url('/availability'), self.pod_url(pod)):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        home = self.client.get(f'/courses/{COURSE_SLUG}/home')
        self.assertNotContains(home, 'data-testid="course-tab-pods"')

    @ENABLED
    def test_enabled_course_shows_pods_tab_after_projects_with_cohort_query(self):
        self.client.force_login(self.anna)
        home = self.client.get(f'/courses/{COURSE_SLUG}/home')
        nav = home.content.decode().split('aria-label="Course pages"')[1].split('</nav>')[0]
        self.assertLess(nav.index('>Projects</a>'), nav.index('data-testid="course-tab-pods"'))
        self.assertIn(f'href="/courses/{COURSE_SLUG}/home/pods?cohort=4"', nav)

    @ENABLED
    def test_non_enrolled_user_gets_404_and_anonymous_is_redirected(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna])
        self.client.force_login(self.outsider)
        list_response = self.client.get(self.pods_url('?cohort=4'))
        pod_response = self.client.get(self.pod_url(pod))
        self.assertEqual((list_response.status_code, pod_response.status_code), (404, 404))
        self.assertNotContains(pod_response, 'Anna K.', status_code=404)
        self.client.logout()
        response = self.client.get(self.pod_url(pod))
        self.assertRedirects(response, f'/accounts/login/?next={self.pod_url(pod)}', fetch_redirect_response=False)

    @ENABLED
    def test_pod_under_another_course_slug_is_404(self):
        other = make_course(slug='other-course', title='Other')
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna])
        self.client.force_login(self.anna)
        with override_settings(PODS_COURSE_SLUGS=f'{COURSE_SLUG},{other.slug}'):
            response = self.client.get(f'/courses/{other.slug}/home/pods/{pod.pk}')
        self.assertEqual(response.status_code, 404)

    @ENABLED
    def test_staff_can_open_and_approve_from_member_page(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.raj], owner=self.raj)
        request = svc.request_to_join(pod, self.anna)
        self.client.force_login(self.staff)
        page = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(page, 'pod-request-name'), ['Anna K.'])
        self.client.post(self.pod_url(pod, f'/requests/{request.pk}/approve'))
        self.assertTrue(pod.memberships.filter(user=self.anna).exists())


@tag('core')
@ENABLED
class PodsListTest(PodViewFixture):
    def test_edit_availability_link_returns_to_the_editor_after_save(self):
        _pod(self.cohort, 'Evening builders', members=[self.raj])
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        self.assertContains(response, f'href="/courses/{COURSE_SLUG}/home/pods/availability?cohort=4"')

    def test_empty_state_offers_start_a_pod(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        self.assertTrue(texts(response, 'pods-empty')[0].startswith('Empty state No pods yet'))
        self.assertContains(response, f'href="/courses/{COURSE_SLUG}/home/pods/new?cohort=4"')

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday so weekly fit is deterministic
    def test_order_is_mine_then_open_by_fit_then_full_then_closed_and_no_archived(self):
        set_windows(self.anna, 'Europe/Berlin', [(d, '18:00', '21:00') for d in range(5)])
        set_windows(self.raj, 'Europe/Berlin', [(1, '18:00', '20:00')])
        set_windows(self.mike, 'Asia/Tokyo', [(1, '06:00', '07:00')])
        closed = _pod(self.cohort, 'Closed pod', members=[self.basic], status='closed')
        full = _pod(self.cohort, 'Full pod', members=[self.basic], max_members=1)
        morning = _pod(self.cohort, 'Morning crew', members=[self.mike])
        evening = _pod(self.cohort, 'Evening builders', members=[self.raj], created_offset=10)
        mine = _pod(self.cohort, 'My pod', members=[self.anna], created_offset=20)
        _pod(self.cohort, 'Archived pod', members=[self.raj], status='archived')
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        self.assertEqual(
            texts(response, 'pod-row-name'),
            [mine.name, evening.name, morning.name, full.name, closed.name],
        )
        self.assertEqual(
            texts(response, 'pod-row-badge'),
            ['Your pod', 'Open', 'Open', 'Full - waiting list', 'Closed'],
        )
        self.assertEqual(
            texts(response, 'pod-row-action'),
            ['Open pod', 'Request to join', 'Request to join', 'Join waiting list', 'Open pod'],
        )
        fits = texts(response, 'pod-row-fit')
        self.assertEqual(fits[:2], ['Overlaps with you 2 h a week', 'No overlap with you yet'])

    def test_request_states_show_badges(self):
        sent = _pod(self.cohort, 'Sent pod', members=[self.raj])
        waiting = _pod(self.cohort, 'Waiting pod', members=[self.raj], max_members=1)
        declined = _pod(self.cohort, 'Declined pod', members=[self.raj])
        svc.request_to_join(sent, self.anna)
        svc.request_to_join(waiting, self.anna)
        svc.decline_request(svc.request_to_join(declined, self.anna), self.staff)
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        badges = dict(zip(texts(response, 'pod-row-name'), texts(response, 'pod-row-badge'), strict=True))
        self.assertEqual(badges['Sent pod'], 'Request sent')
        self.assertEqual(badges['Waiting pod'], 'On waiting list - #1')
        self.assertEqual(badges['Declined pod'], 'Not accepted')

    def test_owner_sees_pending_count_on_tab(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.raj], owner=self.raj)
        svc.request_to_join(pod, self.anna)
        self.client.force_login(self.raj)
        response = self.client.get(f'/courses/{COURSE_SLUG}/home')
        self.assertEqual(texts(response, 'course-tab-pods-count'), ['1'])

    def test_list_never_shows_emails(self):
        _pod(self.cohort, 'Evening builders', members=[self.raj, self.mike], owner=self.raj)
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        rows = ' '.join(texts(response, 'pods-rows'))
        self.assertNotRegex(rows, EMAIL_RE)


@tag('core')
@ENABLED
class StartPodTest(PodViewFixture):
    def test_start_pod_creates_owned_pod_and_asks_for_availability(self):
        self.client.force_login(self.anna)
        response = self.client.post(self.pods_url('/new?cohort=4'), {
            'name': 'RAG evals study group', 'purpose': 'Weekly review of our eval notebooks',
            'max_members': '4', 'meeting_count': '1', 'meeting_minutes': '60',
        }, follow=True)
        pod = Pod.objects.get(name='RAG evals study group')
        self.assertEqual((pod.owner, pod.status, pod.source), (self.anna, 'open', 'member'))
        self.assertEqual(response.redirect_chain[-1][0], self.pod_url(pod))
        self.assertIn('Pod started. Add your availability so others can see if they fit.', texts(response, 'messages-region')[0])
        self.assertEqual(texts(response, 'pod-status-badge'), ['Your pod'])
        self.assertIn('1 of 4 members', texts(response, 'pod-meta')[0])
        self.assertEqual(
            texts(response, 'pod-times-needs-more'),
            ['Suggested times appear when at least two members have added availability.'],
        )

    def test_invalid_size_is_rejected_with_message(self):
        self.client.force_login(self.anna)
        response = self.client.post(self.pods_url('/new'), {'name': 'X', 'purpose': 'Y', 'max_members': '20'})
        self.assertEqual(texts(response, 'pod-form-error'), ['Size limit must be between 1 and 12.'])
        self.assertFalse(Pod.objects.exists())


@tag('core')
@ENABLED
class PodPageTest(PodViewFixture):
    def test_non_member_sees_names_timezones_and_fit_but_no_private_sections(self):
        set_windows(self.anna, 'Europe/Berlin', [(1, '18:00', '21:00')])
        set_windows(self.raj, 'Europe/Berlin', [(1, '18:00', '21:00')])
        set_windows(self.mike, 'Europe/Berlin', [(1, '18:00', '21:00')])
        pod = _pod(self.cohort, 'Evening builders', members=[self.raj, self.mike], owner=self.raj)
        Pod.objects.filter(pk=pod.pk).update(slack_channel_url='https://x.slack.com/archives/C1')
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-member-name'), ['Raj S.', 'Mike K.'])
        self.assertIn('Europe/Berlin', texts(response, 'pod-member-meta')[0])
        self.assertEqual(texts(response, 'pod-fit-line'), ['Overlaps with you 3 h a week'])
        for hidden in ('pod-suggested-times', 'pod-slack', 'pod-my-availability', 'pod-requests'):
            self.assertNotContains(response, f'data-testid="{hidden}"')
        self.assertNotContains(response, 'x.slack.com')
        members_block = ' '.join(texts(response, 'pod-members'))
        self.assertNotRegex(members_block, EMAIL_RE)

    def test_request_withdraw_and_leave(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.raj, self.mike], owner=self.raj)
        self.client.force_login(self.anna)
        page = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(page, 'pod-request-submit'), ['Request to join'])
        response = self.client.post(self.pod_url(pod, '/request'), {'message': 'I am building a RAG eval harness'}, follow=True)
        self.assertEqual(texts(response, 'pod-status-badge'), ['Request sent'])
        self.assertEqual(texts(response, 'pod-withdraw'), ['Withdraw request'])
        self.client.post(self.pod_url(pod, '/withdraw'))
        self.assertEqual(PodJoinRequest.objects.get(user=self.anna).status, 'withdrawn')
        self.client.force_login(self.mike)
        page = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(page, 'pod-leave-confirm'), ['Yes, leave pod'])
        self.client.post(self.pod_url(pod, '/leave'))
        self.assertFalse(pod.memberships.filter(user=self.mike).exists())

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday for request fit
    def test_owner_sees_request_with_fit_and_full_pod_disables_approve(self):
        set_windows(self.raj, 'Europe/Berlin', [(1, '18:00', '21:00'), (3, '18:00', '21:00')])
        set_windows(self.mike, 'Europe/Berlin', [(1, '18:00', '21:00'), (3, '18:00', '21:00')])
        set_windows(self.anna, 'Europe/Berlin', [(1, '18:00', '21:00')])
        pod = _pod(self.cohort, 'Evening builders', members=[self.raj, self.mike], owner=self.raj, max_members=3)
        svc.request_to_join(pod, self.anna, 'I am building a RAG eval harness')
        self.client.force_login(self.raj)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-request-name'), ['Anna K.'])
        self.assertEqual(texts(response, 'pod-request-message-text'), ['I am building a RAG eval harness'])
        self.assertEqual(texts(response, 'pod-request-fit'), ['Fits 2 of 4 suggested times'])
        self.assertIn('Requested today', texts(response, 'pod-request-meta')[0])
        PodMembership.objects.create(pod=pod, user=self.basic, source='staff')
        PodJoinRequest.objects.filter(pod=pod).update(status='waitlisted')
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-full-line'), ['Pod is full. Raise the size limit to approve.'])
        self.assertContains(response, 'disabled aria-describedby="pod-full-line"')
        self.assertEqual(texts(response, 'pod-request-status'), ['On waiting list - #1'])

    def test_non_owner_member_gets_403_on_edit_and_owner_can_close(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.raj, self.mike], owner=self.raj)
        self.client.force_login(self.mike)
        self.assertEqual(self.client.get(self.pod_url(pod, '/edit')).status_code, 403)
        self.client.force_login(self.raj)
        self.client.post(self.pod_url(pod, '/edit'), {
            'name': 'Evening builders', 'purpose': 'New purpose', 'max_members': '5',
            'meeting_count': '3', 'meeting_minutes': '45', 'status': 'closed',
        })
        pod.refresh_from_db()
        self.assertEqual((pod.status, pod.max_members, pod.meeting_minutes, pod.purpose), ('closed', 5, 45, 'New purpose'))

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday so suggestions are deterministic
    def test_suggested_times_render_in_viewer_zone_with_strip_and_based_on_line(self):
        set_windows(self.anna, 'Europe/Berlin', [(1, '18:00', '21:00'), (3, '18:00', '21:00')])
        set_windows(self.raj, 'Asia/Kolkata', [(1, '21:00', '23:30'), (3, '21:00', '23:30')])
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, self.raj, self.mike], owner=self.raj)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        times = texts(response, 'pod-slot-time')
        self.assertTrue(times)
        self.assertTrue(all(t.endswith('Europe/Berlin') for t in times), times)
        self.assertEqual(times[0], 'Tue, Jul 07, 18:00 Europe/Berlin')
        strip = texts(response, 'pod-slot-strip')[0]
        self.assertIn('Anna K. 18:00 Berlin', strip)
        self.assertIn('Raj S. 21:30 Kolkata', strip)
        self.assertEqual(
            texts(response, 'pod-times-based-on'),
            ["Based on 2 of 3 members. Mike K. hasn't added availability yet."],
        )
        self.assertEqual(texts(response, 'pod-slot-fit')[0], 'Everyone - works well')
        self.assertEqual(texts(response, 'availability-summary-day'), ['Tuesday 18:00-21:00', 'Thursday 18:00-21:00'])
        self.client.force_login(self.raj)
        raj_times = texts(self.client.get(self.pod_url(pod)), 'pod-slot-time')
        self.assertEqual(raj_times[0], 'Tue, Jul 07, 21:30 Asia/Kolkata')

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_no_fit_shows_message_with_edit_availability(self):
        set_windows(self.anna, 'Europe/Berlin', [(1, '08:00', '09:00')])
        set_windows(self.raj, 'Europe/Berlin', [(2, '08:00', '09:00')])
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, self.raj])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(
            texts(response, 'pod-times-no-fit'),
            ['No time fits at least 2 of you in the next 2 weeks. Ask members to add more windows, or add some yourself.'],
        )
        self.assertEqual(texts(response, 'pod-times-edit-availability'), ['Edit my availability'])


@tag('core')
@ENABLED
class SlackHandOffTest(PodViewFixture):
    def setUp(self):
        for user, slack_id in ((self.anna, 'UANNA'), (self.raj, 'URAJ')):
            user.slack_member = True
            user.slack_user_id = slack_id
            user.save(update_fields=['slack_member', 'slack_user_id'])

    def test_main_members_get_callout_and_message_links(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, self.raj])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-slack-suggested-name'), [f'#pod-{pod.pk}-evening-builders'])
        self.assertContains(response, 'href="https://app.slack.com/client/T0TEAM/URAJ"')
        self.assertNotContains(response, 'https://app.slack.com/client/T0TEAM/UANNA')
        self.assertNotContains(response, 'data-testid="pod-slack-tier-warning"')

    def test_basic_member_sees_gate_and_main_member_sees_warning(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, self.basic])
        self.basic.slack_member = True
        self.basic.slack_user_id = 'UBASIC'
        self.basic.save(update_fields=['slack_member', 'slack_user_id'])
        self.client.force_login(self.basic)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-slack-gate'), ['Slack is available to Main and Premium members.'])
        self.assertContains(response, 'href="/membership"')
        self.assertNotContains(response, 'data-testid="pod-slack-input"')
        self.assertNotContains(response, 'data-testid="pod-member-slack"')
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(len(texts(response, 'pod-slack-tier-warning')), 1)
        self.assertNotContains(response, 'UBASIC')

    def test_link_validation_and_clearing(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, self.raj])
        self.client.force_login(self.anna)
        for bad in ('http://aishippinglabs.slack.com/archives/C1', 'https://example.com/chan', 'https://slack.com.evil.io/x'):
            response = self.client.post(self.pod_url(pod, '/slack'), {'slack_channel_url': bad}, follow=True)
            self.assertEqual(
                texts(response, 'pod-slack-error'),
                ['Paste a Slack channel link, for example https://yourworkspace.slack.com/archives/C0123456789.'],
            )
        pod.refresh_from_db()
        self.assertEqual(pod.slack_channel_url, '')
        for good in ('https://aishippinglabs.slack.com/archives/C0123456789', 'https://app.slack.com/client/T1/C2'):
            self.client.post(self.pod_url(pod, '/slack'), {'slack_channel_url': good})
            pod.refresh_from_db()
            self.assertEqual(pod.slack_channel_url, good)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, 'pod-slack-channel'), ['Slack channel - Open in Slack'])
        self.client.post(self.pod_url(pod, '/slack'), {'slack_channel_url': ''})
        pod.refresh_from_db()
        self.assertEqual(pod.slack_channel_url, '')

    def test_basic_member_cannot_set_link(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.anna, self.basic])
        self.client.force_login(self.basic)
        response = self.client.post(self.pod_url(pod, '/slack'), {'slack_channel_url': 'https://a.slack.com/archives/C1'})
        self.assertEqual(response.status_code, 403)
        pod.refresh_from_db()
        self.assertEqual(pod.slack_channel_url, '')


@tag('core')
@ENABLED
class AvailabilityEditorTest(PodViewFixture):
    def test_save_and_overlap_error(self):
        self.client.force_login(self.mike)
        data = {'timezone': 'America/New_York', 'cohort': '4'}
        for day in range(5):
            data.update({
                f'window-{day}-weekday': str(day), f'window-{day}-start': '18:00',
                f'window-{day}-end': '21:00', f'window-{day}-preference': 'if_needed' if day == 4 else 'preferred',
            })
        response = self.client.post(self.pods_url('/availability'), data, follow=True)
        days = texts(response, 'availability-summary-day')
        self.assertEqual(days[0], 'Monday 18:00-21:00')
        self.assertEqual(days[4], 'Friday 18:00-21:00 (If needed)')
        data.update({'window-9-weekday': '1', 'window-9-start': '20:00', 'window-9-end': '22:00', 'window-9-preference': 'preferred'})
        response = self.client.post(self.pods_url('/availability'), data)
        self.assertEqual(texts(response, 'availability-error'), ['Windows on Tuesday overlap. Merge them into one window.'])
        self.assertEqual(self.mike.availability_profile.windows.count(), 5)

    def test_notifications_link_to_pod(self):
        pod = _pod(self.cohort, 'Evening builders', members=[self.raj], owner=self.raj)
        self.client.force_login(self.anna)
        self.client.post(self.pod_url(pod, '/request'))
        self.client.force_login(self.raj)
        request = PodJoinRequest.objects.get(user=self.anna)
        self.client.post(self.pod_url(pod, f'/requests/{request.pk}/approve'))
        note = Notification.objects.get(user=self.anna)
        self.assertEqual((note.title, note.url), ('You joined Evening builders', self.pod_url(pod)))
