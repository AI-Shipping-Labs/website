"""Pods polish (issue #1927): honest suggested-time labels, near-duplicate
slots, re-request cooldown after a decline, and Studio's pending-only stale
badge. The daily staff Slack alert lives in ``test_stale_request_alert``."""

import datetime
from datetime import UTC

from django.test import SimpleTestCase, override_settings, tag
from django.utils import timezone
from freezegun import freeze_time

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from notifications.models import Notification
from pods.models import PodJoinRequest, PodMembership
from pods.services import membership as svc
from pods.services.suggestions import (
    MIN_WEEKLY_GAP_MINUTES,
    member_strip,
    minute_of_week,
    suggest_slots,
    weekly_gap,
)
from pods.tests.fixtures import COURSE_SLUG, make_user, set_windows
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


# --- 2. Re-request cooldown after a decline -----------------------------------

DECIDED = datetime.datetime(2026, 10, 9, 23, 30, tzinfo=UTC)


class CooldownFixture(PodViewFixture):
    def setUp(self):
        self.addCleanup(clear_config_cache)
        self.pod_a = _pod(self.cohort, 'Pod A', members=[self.raj], owner=self.raj)
        self.pod_b = _pod(self.cohort, 'Pod B', members=[self.mike], owner=self.mike)

    def _set(self, key, value):
        IntegrationSetting.objects.update_or_create(key=key, defaults={'value': value})
        clear_config_cache()

    def decline(self, pod, user, decided_at=DECIDED):
        request = PodJoinRequest.objects.create(pod=pod, user=user, status='pending')
        svc.decline_request(request, pod.owner)
        PodJoinRequest.objects.filter(pk=request.pk).update(decided_at=decided_at, created_at=decided_at)
        return request

    def owner_request_notifications(self, pod):
        return Notification.objects.filter(user=pod.owner, title=f'New request to join {pod.name}').count()


@tag('core')
class RerequestRuleTest(CooldownFixture):
    @freeze_time('2026-10-10 12:00:00')  # date-rot-ok: one day after the frozen decline
    def test_first_decline_starts_a_cooldown_in_the_students_timezone(self):
        self.decline(self.pod_a, self.anna)
        with self.assertRaises(svc.PodError) as ctx:
            svc.request_to_join(self.pod_a, self.anna, 'please')
        self.assertEqual(ctx.exception.code, 'request_cooldown')
        # Oct 23 23:30 UTC is Oct 24 in Berlin.
        self.assertEqual(ctx.exception.message, 'You can ask to join this pod again on Oct 24.')
        self.assertEqual(PodJoinRequest.objects.filter(pod=self.pod_a, user=self.anna).count(), 1)
        self.assertEqual(self.owner_request_notifications(self.pod_a), 0)

        set_windows(self.anna, 'America/New_York', [])
        self.anna._pods_profile = None
        with self.assertRaises(svc.PodError) as ctx:
            svc.request_to_join(self.pod_a, self.anna)
        self.assertEqual(ctx.exception.message, 'You can ask to join this pod again on Oct 23.')

    def test_cooldown_passed_allows_one_more_request_that_notifies_the_owner_once(self):
        with freeze_time('2026-10-24 00:00:00'):  # date-rot-ok: 14 days after the frozen decline
            self.decline(self.pod_a, self.anna)
            request = svc.request_to_join(self.pod_a, self.anna, 'second try')
        self.assertEqual(request.status, 'pending')
        self.assertEqual(self.owner_request_notifications(self.pod_a), 1)

    @freeze_time('2026-12-01 00:00:00')  # date-rot-ok: long after both frozen declines
    def test_second_decline_is_final_and_does_not_notify(self):
        self.decline(self.pod_a, self.anna)
        self.decline(self.pod_a, self.anna, decided_at=DECIDED + datetime.timedelta(days=20))
        with self.assertRaises(svc.PodError) as ctx:
            svc.request_to_join(self.pod_a, self.anna)
        self.assertEqual(ctx.exception.code, 'request_declined_final')
        self.assertEqual(ctx.exception.message, "You can't ask to join this pod again.")
        self.assertEqual(self.owner_request_notifications(self.pod_a), 0)

    @freeze_time('2026-10-10 12:00:00')  # date-rot-ok
    def test_decline_on_one_pod_does_not_block_another_and_withdrawals_do_not_count(self):
        self.decline(self.pod_a, self.anna)
        for _ in range(3):
            request = svc.request_to_join(self.pod_b, self.anna)
            self.assertEqual(request.status, 'pending')
            svc.withdraw_request(request, self.anna)
        self.assertEqual(svc.request_to_join(self.pod_b, self.anna).status, 'pending')
        self.assertEqual(svc.rerequest_state(self.pod_b, self.anna).key, svc.REREQUEST_ALLOWED)

    @freeze_time('2026-10-10 00:00:00')  # date-rot-ok
    def test_zero_cooldown_allows_immediate_retry_but_second_decline_is_final(self):
        self._set('PODS_REREQUEST_COOLDOWN_DAYS', '0')
        self.decline(self.pod_a, self.anna)
        second = svc.request_to_join(self.pod_a, self.anna)
        svc.decline_request(second, self.raj)
        with self.assertRaises(svc.PodError) as ctx:
            svc.request_to_join(self.pod_a, self.anna)
        self.assertEqual(ctx.exception.code, 'request_declined_final')

    @freeze_time('2026-10-10 12:00:00')  # date-rot-ok
    def test_invalid_cooldown_falls_back_to_fourteen_days(self):
        self.decline(self.pod_a, self.anna)
        for value in ('-3', 'soon'):
            with self.subTest(value=value):
                self._set('PODS_REREQUEST_COOLDOWN_DAYS', value)
                state = svc.rerequest_state(self.pod_a, self.anna)
                self.assertEqual(state.available_at, DECIDED + datetime.timedelta(days=14))

    @freeze_time('2026-12-01 00:00:00')  # date-rot-ok
    def test_staff_can_still_add_a_twice_declined_student(self):
        self.decline(self.pod_a, self.anna)
        self.decline(self.pod_a, self.anna, decided_at=DECIDED + datetime.timedelta(days=20))
        staff = make_user('ops@test.com', staff=True)
        self.client.force_login(staff)
        self.client.post(f'/studio/pods/{self.pod_a.pk}/members/add', {'emails': 'anna@test.com'})
        self.assertTrue(PodMembership.objects.filter(pod=self.pod_a, user=self.anna).exists())


@tag('core')
@ENABLED
class RerequestPodPageTest(CooldownFixture):
    @freeze_time('2026-10-10 12:00:00')  # date-rot-ok
    def test_within_cooldown_form_is_hidden_and_date_is_shown(self):
        self.decline(self.pod_a, self.anna)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(self.pod_a))
        self.assertEqual(texts(response, 'pod-status-badge'), ['Not accepted'])
        self.assertEqual(
            texts(response, 'pod-request-blocked'),
            ['Your request was not accepted. You can ask again on Oct 24.'],
        )
        self.assertEqual(texts(response, 'pod-request-form'), [])

    @freeze_time('2026-10-10 12:00:00')  # date-rot-ok
    def test_stale_form_post_shows_flash_error_and_creates_nothing(self):
        self.decline(self.pod_a, self.anna)
        self.client.force_login(self.anna)
        response = self.client.post(self.pod_url(self.pod_a, '/request'), {'message': 'again'}, follow=True)
        self.assertIn('You can ask to join this pod again on Oct 24.', [str(m) for m in response.context['messages']])
        self.assertEqual(PodJoinRequest.objects.filter(pod=self.pod_a, user=self.anna).count(), 1)
        self.assertEqual(self.owner_request_notifications(self.pod_a), 0)

    @freeze_time('2026-10-25 00:00:00')  # date-rot-ok: after the 14-day cooldown
    def test_after_cooldown_form_is_shown_with_retry_line(self):
        self.decline(self.pod_a, self.anna)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(self.pod_a))
        self.assertEqual(texts(response, 'pod-status-badge'), ['Not accepted'])
        self.assertEqual(
            texts(response, 'pod-request-retry'),
            ['Your last request was not accepted. You can ask once more.'],
        )
        self.assertEqual(len(texts(response, 'pod-request-form')), 1)
        self.assertEqual(texts(response, 'pod-request-blocked'), [])

    @freeze_time('2026-12-01 00:00:00')  # date-rot-ok
    def test_twice_declined_sees_final_line_with_browse_link(self):
        self.decline(self.pod_a, self.anna)
        self.decline(self.pod_a, self.anna, decided_at=DECIDED + datetime.timedelta(days=20))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(self.pod_a))
        self.assertEqual(
            texts(response, 'pod-request-blocked'),
            ["This pod declined your request twice, so you can't ask to join it again. "
             'Browse other pods or start your own.'],
        )
        self.assertContains(
            response, f'href="/courses/{COURSE_SLUG}/home/pods?cohort=4" ',
        )
        self.assertEqual(texts(response, 'pod-request-form'), [])

    @freeze_time('2026-10-10 12:00:00')  # date-rot-ok
    def test_pods_tab_row_for_declined_pod_is_unchanged(self):
        self.decline(self.pod_a, self.anna)
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        rows = dict(zip(texts(response, 'pod-row-name'), texts(response, 'pod-row-badge'), strict=False))
        self.assertEqual(rows.get('Pod A'), 'Not accepted')


# --- 3. Studio stale badge: pending only --------------------------------------

@tag('core')
@override_settings(PODS_STALE_REQUEST_DAYS=5)
class StudioPendingOnlyStaleBadgeTest(PodViewFixture):
    def test_only_an_old_pending_request_makes_a_pod_stale(self):
        now = timezone.now()
        pod_a = _pod(self.cohort, 'Pod A', members=[self.raj], owner=self.raj)
        pod_b = _pod(self.cohort, 'Pod B', members=[self.mike], owner=self.mike, max_members=1)
        pending = PodJoinRequest.objects.create(pod=pod_a, user=self.anna, status='pending')
        waitlisted = PodJoinRequest.objects.create(pod=pod_b, user=self.anna, status='waitlisted')
        PodJoinRequest.objects.filter(pk=pending.pk).update(created_at=now - datetime.timedelta(days=6))
        PodJoinRequest.objects.filter(pk=waitlisted.pk).update(created_at=now - datetime.timedelta(days=10))
        self.client.force_login(self.staff)

        response = self.client.get('/studio/pods/')
        stale = {row['pod'].name: row['stale'] for row in response.context['rows']}
        self.assertEqual(stale, {'Pod A': True, 'Pod B': False})

        svc.decline_request(pending, self.staff)
        response = self.client.get('/studio/pods/')
        self.assertFalse(any(row['stale'] for row in response.context['rows']))
