"""Tests for ``InterviewNoteQuerySet`` -- the security-sensitive piece.

These tests verify that visibility filtering happens at the queryset
layer, not just in templates. A future API view that calls
``InterviewNote.objects.filter(member=request.user)`` and forgets the
``visibility='external'`` clause must NOT be able to leak internal
notes; ``visible_to`` is the single chokepoint.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()


class InterviewNoteQuerySetTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member_a = User.objects.create_user(
            email='a@test.com', password='pw',
        )
        cls.member_b = User.objects.create_user(
            email='b@test.com', password='pw',
        )

        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )
        cls.plan_a = Plan.objects.create(member=cls.member_a, sprint=cls.sprint)
        cls.plan_b = Plan.objects.create(member=cls.member_b, sprint=cls.sprint)

        cls.a_internal = InterviewNote.objects.create(
            plan=cls.plan_a, member=cls.member_a,
            visibility='internal', body='A_INTERNAL',
        )
        cls.a_external = InterviewNote.objects.create(
            plan=cls.plan_a, member=cls.member_a,
            visibility='external', body='A_EXTERNAL',
        )
        cls.b_internal = InterviewNote.objects.create(
            plan=cls.plan_b, member=cls.member_b,
            visibility='internal', body='B_INTERNAL',
        )
        cls.b_external = InterviewNote.objects.create(
            plan=cls.plan_b, member=cls.member_b,
            visibility='external', body='B_EXTERNAL',
        )

    def test_external_returns_only_external_notes(self):
        ids = set(InterviewNote.objects.external().values_list('pk', flat=True))
        self.assertEqual(ids, {self.a_external.pk, self.b_external.pk})

    def test_internal_returns_only_internal_notes(self):
        ids = set(InterviewNote.objects.internal().values_list('pk', flat=True))
        self.assertEqual(ids, {self.a_internal.pk, self.b_internal.pk})

    def test_visible_to_staff_returns_all_notes(self):
        ids = set(
            InterviewNote.objects.visible_to(self.staff)
            .values_list('pk', flat=True)
        )
        self.assertEqual(
            ids,
            {
                self.a_internal.pk,
                self.a_external.pk,
                self.b_internal.pk,
                self.b_external.pk,
            },
        )

    def test_visible_to_member_returns_only_own_external_notes(self):
        ids = set(
            InterviewNote.objects.visible_to(self.member_a)
            .values_list('pk', flat=True)
        )
        # Member A sees only their OWN external note. Critically NOT:
        # - their own internal note (privacy)
        # - the other member's external note (other-user data)
        # - the other member's internal note (privacy + other-user)
        self.assertEqual(ids, {self.a_external.pk})
        self.assertEqual(
            InterviewNote.objects.visible_to(self.member_a).count(), 1,
        )

    def test_visible_to_anonymous_returns_empty_queryset(self):
        anon = AnonymousUser()
        self.assertEqual(InterviewNote.objects.visible_to(anon).count(), 0)

    def test_visible_to_unauthenticated_returns_empty_queryset(self):
        self.assertEqual(InterviewNote.objects.visible_to(None).count(), 0)
