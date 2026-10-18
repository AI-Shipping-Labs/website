"""Migration invariants for the #1984 self-service study-group transition."""

import importlib

from django.apps import apps
from django.test import TestCase, tag

from notifications.models import Notification
from pods.models import Pod, PodJoinRequest, PodMembership
from pods.tests.fixtures import make_cohort, make_course, make_user


def _group(cohort, name, *, members=(), purpose="Build useful things.", **extra):
    pod = Pod.objects.create(
        cohort=cohort,
        name=name,
        purpose=purpose,
        status="open",
        source="studio",
        **extra,
    )
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source="staff")
    return pod


def _reviewed_groups(cohort, users, migration):
    pods = []
    for number, title in enumerate(migration.REVIEWED_PURPOSES, start=1):
        pod = _group(cohort, title, purpose=f"Raw formation note {number}", owner=None)
        pods.append(pod)
    for index, user in enumerate(users):
        PodMembership.objects.create(pod=pods[index % 23], user=user, source="staff")
    return pods


def _historical_requests(pods, user):
    target = PodJoinRequest.objects.create(
        pod=pods[0],
        user=user,
        status="waitlisted",
        message="target historical",
    )
    other_course = make_course(slug="other-course", title="Other")
    other_cohort = make_cohort(other_course, key="1", name="Other cohort")
    historical_group = _group(other_cohort, "Historical requests")
    unrelated = PodJoinRequest.objects.create(
        pod=historical_group,
        user=user,
        status="pending",
        message="historical",
    )
    return target, unrelated


@tag("core")
class StudyGroupTransitionDataTest(TestCase):
    def transition_fixture(self):
        course = make_course(slug="ai-buildcamp", title="AI Buildcamp")
        cohort = make_cohort(course, key="4", name="Cohort 4")
        users = [make_user(f"user-{index}@test.com") for index in range(52)]
        migration = importlib.import_module("pods.migrations.0010_unlimited_self_service_groups")
        pods = _reviewed_groups(cohort, users, migration)
        lookalike = _group(
            cohort,
            "Group 01 — Imported",
            purpose="Unreviewed lookalike",
            owner=None,
        )
        return pods, lookalike, _historical_requests(pods, users[-1])

    def membership_state(self, pods):
        memberships = list(
            PodMembership.objects.filter(pod__in=pods)
            .order_by("pk")
            .values_list(
                "pk",
                "pod_id",
                "user_id",
                "joined_at",
                "source",
                "added_by_id",
            )
        )
        owners = list(
            Pod.objects.filter(pk__in=[pod.pk for pod in pods])
            .order_by("pk")
            .values_list(
                "pk",
                "owner_id",
            )
        )
        return memberships, owners

    def assert_reviewed_copy(self, pods, migration):
        groups = Pod.objects.filter(pk__in=[pod.pk for pod in pods]).order_by("pk")
        for number, (pod, purpose) in enumerate(zip(groups, migration.REVIEWED_PURPOSES.values()), start=1):
            self.assertEqual(pod.internal_notes, f"Raw formation note {number}")
            self.assertEqual(pod.purpose, purpose)
            self.assertEqual(pod.scheduling_note, "")

    def test_transition_preserves_23_groups_and_52_memberships_and_cancels_open_history(self):
        pods, lookalike, requests = self.transition_fixture()
        membership_before, owner_before = self.membership_state(pods)

        migration = importlib.import_module("pods.migrations.0010_unlimited_self_service_groups")
        migration.preserve_notes_and_retire_requests(apps, None)

        membership_after, owner_after = self.membership_state(pods)
        self.assertEqual(membership_after, membership_before)
        self.assertEqual(owner_after, owner_before)
        self.assertEqual(PodMembership.objects.filter(pod__in=pods).count(), 52)
        self.assert_reviewed_copy(pods, migration)
        lookalike.refresh_from_db()
        self.assertEqual((lookalike.purpose, lookalike.internal_notes), ("Unreviewed lookalike", ""))
        for request in requests:
            request.refresh_from_db()
            self.assertEqual(request.status, "cancelled")
            self.assertIsNotNone(request.decided_at)
        self.assertFalse(Notification.objects.exists())
