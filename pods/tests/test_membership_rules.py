"""Unlimited self-service study-group membership rules (#1984)."""

import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase, tag
from django.utils import timezone

from content.models import CohortEnrollment
from notifications.models import Notification
from plans.models import Sprint
from pods.models import POD_SOURCE_MEMBER, POD_SOURCE_STUDIO, Pod, PodJoinRequest, PodMembership
from pods.services import membership as svc
from pods.tests.fixtures import enroll, make_cohort, make_course, make_meeting, make_user


def _group(cohort, *, owner=None, members=(), name='Evening builders', status='open', source=POD_SOURCE_STUDIO):
    pod = Pod.objects.create(
        cohort=cohort, name=name, purpose='Ship things together.', owner=owner,
        source=source, status=status,
    )
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source='staff')
    return pod


@tag('core')
class PodRulesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.other_cohort = make_cohort(cls.course, key='5', name='Cohort 5')
        cls.staff = make_user('admin@test.com', staff=True)
        cls.users = []
        for name in ('anna', 'raj', 'mike', 'lea', 'tom', 'zoe'):
            user = make_user(f'{name}@test.com', first_name=name.title(), last_name='K')
            enroll(user, cls.cohort)
            cls.users.append(user)
        cls.anna, cls.raj, cls.mike, cls.lea, cls.tom, cls.zoe = cls.users

    def test_pod_without_or_with_both_activities_is_rejected_by_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pod.objects.create(name='None', purpose='x')
        sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 1, 5),  # date-rot-ok
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pod.objects.create(name='Both', purpose='x', cohort=self.cohort, sprint=sprint)

    def test_self_paced_cohort_cannot_have_groups(self):
        self_paced = make_cohort(self.course, key='sp', name='Self-paced', mode='self_paced')
        enroll(self.anna, self_paced)
        with self.assertRaisesMessage(svc.PodError, svc.MSG_SELF_PACED):
            svc.start_member_pod(self.anna, self_paced, {'name': 'X', 'purpose': 'Y'})
        with self.assertRaisesMessage(svc.PodError, svc.MSG_SELF_PACED):
            svc.create_staff_pod(cohort=self_paced, data={'name': 'X', 'purpose': 'Y'}, source='studio')
        self.assertFalse(Pod.objects.filter(cohort=self_paced).exists())

    def test_member_started_group_has_creator_and_validated_copy(self):
        pod = svc.start_member_pod(self.anna, self.cohort, {
            'name': 'RAG evals', 'purpose': 'Review eval evidence.',
            'scheduling_note': 'Choose a weekly time together.',
        })
        self.assertEqual((pod.owner, pod.status, pod.scheduling_note), (
            self.anna, 'open', 'Choose a weekly time together.',
        ))
        self.assertEqual(
            list(pod.memberships.values_list('user__email', 'source')),
            [('anna@test.com', 'creator')],
        )

    def test_join_is_immediate_unlimited_multigroup_and_idempotent(self):
        first = _group(self.cohort, members=[self.raj], name='First')
        second = _group(self.cohort, members=[self.mike], name='Second')
        for _attempt in range(2):
            membership, created = svc.join_pod(first, self.anna)
        self.assertFalse(created)
        self.assertEqual((membership.source, first.memberships.filter(user=self.anna).count()), ('member', 1))
        svc.join_pod(second, self.anna)
        self.assertEqual(
            set(PodMembership.objects.filter(user=self.anna).values_list('pod__name', flat=True)),
            {'First', 'Second'},
        )
        self.assertFalse(Notification.objects.exists())

    def test_join_ignores_historical_capacity_and_preserves_ownerless_state(self):
        pod = _group(self.cohort, members=[self.raj, self.mike], name='Imported')
        Pod.objects.filter(pk=pod.pk).update(max_members=1)
        pod.refresh_from_db()
        svc.join_pod(pod, self.anna)
        pod.refresh_from_db()
        self.assertEqual((pod.memberships.count(), pod.owner_id), (3, None))

    def test_join_requires_activity_eligibility_and_open_status(self):
        outsider = make_user('outsider@test.com')
        open_group = _group(self.cohort, name='Open')
        closed_group = _group(self.cohort, name='Closed', status='closed')
        with self.assertRaisesMessage(svc.PodError, svc.MSG_NOT_ELIGIBLE):
            svc.join_pod(open_group, outsider)
        with self.assertRaisesMessage(svc.PodError, 'This study group is closed.'):
            svc.join_pod(closed_group, self.anna)
        self.assertFalse(PodMembership.objects.filter(user=outsider).exists())
        self.assertFalse(closed_group.memberships.filter(user=self.anna).exists())

    def test_existing_member_can_repeat_join_after_group_closes(self):
        pod = _group(self.cohort, members=[self.anna], status='closed')
        membership, created = svc.join_pod(pod, self.anna)
        self.assertFalse(created)
        self.assertEqual(membership.user, self.anna)

    def test_leave_is_group_local_transfers_owner_and_cleans_future_response(self):
        first = _group(self.cohort, owner=self.anna, members=[self.anna, self.raj], name='First')
        second = _group(self.cohort, members=[self.anna], name='Second')
        future = make_meeting(first, starts_at=timezone.now() + datetime.timedelta(days=1))
        past = make_meeting(first, starts_at=timezone.now() - datetime.timedelta(days=1))
        future.responses.create(user=self.anna, response='going')
        past.responses.create(user=self.anna, response='going')
        svc.remove_member(first, self.anna, actor=self.anna)
        first.refresh_from_db()
        self.assertEqual(first.owner, self.raj)
        self.assertTrue(second.memberships.filter(user=self.anna).exists())
        self.assertFalse(future.responses.filter(user=self.anna).exists())
        self.assertTrue(past.responses.filter(user=self.anna).exists())

    def test_last_member_leaving_member_group_archives_and_staff_group_stays_open(self):
        member_group = _group(
            self.cohort, owner=self.anna, members=[self.anna], name='Member', source=POD_SOURCE_MEMBER,
        )
        staff_group = _group(self.cohort, members=[self.anna], name='Staff')
        svc.remove_member(member_group, self.anna)
        svc.remove_member(staff_group, self.anna)
        member_group.refresh_from_db()
        staff_group.refresh_from_db()
        self.assertEqual((member_group.status, member_group.owner_id), ('archived', None))
        self.assertEqual((staff_group.status, staff_group.memberships.count()), ('open', 0))

    def test_staff_add_is_unlimited_idempotent_and_cancels_old_open_request(self):
        pod = _group(self.cohort, members=[self.raj])
        request = PodJoinRequest.objects.create(pod=pod, user=self.anna, status='waitlisted')
        results = svc.add_members_by_email(
            pod, ['anna@test.com', 'mike@test.com', 'anna@test.com'], actor=self.staff, source='staff',
        )
        again = svc.add_members_by_email(pod, ['anna@test.com'], actor=self.staff, source='staff')
        request.refresh_from_db()
        self.assertEqual(results.as_dict(), {
            'added': ['anna@test.com', 'mike@test.com'], 'not_enrolled': [],
            'unknown_user': [], 'already_member': [],
        })
        self.assertEqual(again.already_member, ['anna@test.com'])
        self.assertEqual((pod.memberships.count(), request.status), (3, 'cancelled'))
        self.assertIsNotNone(request.decided_at)

    def test_request_mutations_are_retired_without_changing_history(self):
        pod = _group(self.cohort)
        request = PodJoinRequest.objects.create(pod=pod, user=self.anna, status='pending', message='hello')
        calls = (
            lambda: svc.request_to_join(pod, self.raj),
            lambda: svc.approve_request(request, self.staff),
            lambda: svc.decline_request(request, self.staff),
            lambda: svc.withdraw_request(request, self.anna),
        )
        for call in calls:
            with self.assertRaisesMessage(svc.PodError, svc.MSG_REQUESTS_RETIRED):
                call()
        request.refresh_from_db()
        self.assertEqual((request.status, request.message), ('pending', 'hello'))
        self.assertFalse(pod.memberships.exists())
        self.assertFalse(Notification.objects.exists())

    def test_copy_limits_internal_notes_and_retired_capacity(self):
        cleaned, errors = svc.clean_pod_fields({
            'name': 'Group', 'purpose': 'p' * 161, 'scheduling_note': 's' * 501,
            'internal_notes': 'secret', 'max_members': 99,
        })
        self.assertNotIn('internal_notes', cleaned)
        self.assertEqual(errors, {
            'purpose': 'Purpose must be 160 characters or fewer.',
            'max_members': svc.MSG_MAX_MEMBERS_RETIRED,
            'scheduling_note': 'Scheduling guidance must be 500 characters or fewer.',
            'internal_notes': 'Only staff can edit internal formation notes.',
        })
        _, staff_errors = svc.clean_pod_fields({
            'name': 'Group', 'purpose': 'Public.', 'internal_notes': 'x' * 2001,
        }, staff=True)
        self.assertEqual(staff_errors['internal_notes'], 'Internal formation notes must be 2,000 characters or fewer.')

    def test_unenrollment_only_removes_memberships_in_that_activity(self):
        enroll(self.anna, self.other_cohort)
        here = _group(self.cohort, members=[self.anna], name='Here')
        there = _group(self.other_cohort, members=[self.anna], name='There')
        CohortEnrollment.objects.filter(cohort=self.cohort, user=self.anna).delete()
        svc.remove_user_from_cohort(self.anna, self.cohort)
        self.assertFalse(here.memberships.filter(user=self.anna).exists())
        self.assertTrue(there.memberships.filter(user=self.anna).exists())
