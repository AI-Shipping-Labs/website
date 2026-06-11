"""Integration tests for the duplicate-event merge engine (issue #881).

Covers the carry-over rules (registration move + de-dup + earliest timestamp,
content-into-empty-only, zoom-into-empty-only), the workshop relink, the
no-hard-delete retire, the idempotent already-merged no-op, the dry-run no-op,
candidate detection, and the audit-log write.
"""

import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from community.models import CommunityAuditLog
from content.models import Workshop
from events.models import Event, EventRegistration
from events.services.event_merge import (
    find_duplicate_event_pairs,
    merge_duplicate_events,
)

User = get_user_model()

MAY19 = dt.datetime(2026, 5, 19, 0, 0, tzinfo=dt.timezone.utc)
MAY19_STUDIO = dt.datetime(2026, 5, 19, 15, 0, tzinfo=dt.timezone.utc)


def _make_studio_event(**kwargs):
    defaults = {
        "slug": "may19-studio",
        "title": "May 19 Workshop",
        "start_datetime": MAY19_STUDIO,
        "origin": "studio",
        "source_repo": "",
        "status": "upcoming",
        "published": True,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def _make_github_event(**kwargs):
    defaults = {
        "slug": "may19-github",
        "title": "May 19 Workshop",
        "start_datetime": MAY19,
        "origin": "github",
        "source_repo": "workshops-content",
        "kind": "workshop",
        "status": "completed",
        "published": True,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


class FindDuplicatePairsTest(TestCase):
    def test_detects_studio_github_pair_same_title_day(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event()
        pairs = find_duplicate_event_pairs()
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0], (canonical, duplicate))

    def test_normalizes_title_for_match(self):
        canonical = _make_studio_event(title="  May 19   Workshop  ")
        duplicate = _make_github_event(title="may 19 workshop")
        pairs = find_duplicate_event_pairs()
        self.assertEqual([(c.pk, d.pk) for c, d in pairs],
                         [(canonical.pk, duplicate.pk)])

    def test_different_day_is_not_a_pair(self):
        _make_studio_event()
        _make_github_event(start_datetime=dt.datetime(2026, 5, 20, 0, 0,
                                                       tzinfo=dt.timezone.utc))
        self.assertEqual(find_duplicate_event_pairs(), [])

    def test_already_retired_duplicate_excluded(self):
        _make_studio_event()
        _make_github_event(status="cancelled", published=False)
        self.assertEqual(find_duplicate_event_pairs(), [])


class RegistrationCarryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            email="a@test.com", password="x")
        cls.user_b = User.objects.create_user(
            email="b@test.com", password="x")

    def test_unique_registration_moves_to_canonical(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event()
        EventRegistration.objects.create(event=duplicate, user=self.user_b)

        merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        self.assertTrue(
            EventRegistration.objects.filter(
                event=canonical, user=self.user_b).exists())
        self.assertFalse(
            EventRegistration.objects.filter(event=duplicate).exists())

    def test_dedup_keeps_one_row_with_earliest_timestamp(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event()

        # Canonical registration is LATER; duplicate is EARLIER.
        late = timezone.now()
        early = late - dt.timedelta(days=3)
        canon_reg = EventRegistration.objects.create(
            event=canonical, user=self.user_a)
        EventRegistration.objects.filter(pk=canon_reg.pk).update(
            registered_at=late)
        dup_reg = EventRegistration.objects.create(
            event=duplicate, user=self.user_a)
        EventRegistration.objects.filter(pk=dup_reg.pk).update(
            registered_at=early)
        # User B only on the duplicate.
        EventRegistration.objects.create(event=duplicate, user=self.user_b)

        merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        # Exactly one row for A on canonical, back-dated to the earliest.
        a_rows = EventRegistration.objects.filter(
            event=canonical, user=self.user_a)
        self.assertEqual(a_rows.count(), 1)
        self.assertEqual(
            a_rows.first().registered_at.replace(microsecond=0),
            early.replace(microsecond=0))
        # B moved over.
        self.assertTrue(
            EventRegistration.objects.filter(
                event=canonical, user=self.user_b).exists())
        # Nothing left on the duplicate.
        self.assertFalse(
            EventRegistration.objects.filter(event=duplicate).exists())


class ContentCarryTest(TestCase):
    def test_recording_fills_empty_canonical_field(self):
        canonical = _make_studio_event(recording_url="")
        duplicate = _make_github_event(
            recording_url="https://youtu.be/dup")

        plan = merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        canonical.refresh_from_db()
        self.assertEqual(canonical.recording_url, "https://youtu.be/dup")
        self.assertIn("recording_url", plan.fields_filled)

    def test_populated_canonical_field_not_clobbered(self):
        canonical = _make_studio_event(
            recording_url="https://youtu.be/canon",
            zoom_join_url="https://zoom.us/canon")
        duplicate = _make_github_event(
            recording_url="https://youtu.be/dup")

        plan = merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        canonical.refresh_from_db()
        self.assertEqual(canonical.recording_url, "https://youtu.be/canon")
        self.assertEqual(canonical.zoom_join_url, "https://zoom.us/canon")
        self.assertNotIn("recording_url", plan.fields_filled)

    def test_zoom_fills_only_when_canonical_empty(self):
        canonical = _make_studio_event(zoom_join_url="", zoom_meeting_id="")
        duplicate = _make_github_event(
            zoom_join_url="https://zoom.us/dup", zoom_meeting_id="123")

        merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        canonical.refresh_from_db()
        self.assertEqual(canonical.zoom_join_url, "https://zoom.us/dup")
        self.assertEqual(canonical.zoom_meeting_id, "123")


class WorkshopRelinkTest(TestCase):
    def test_workshop_relinks_to_canonical(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event()
        workshop = Workshop.objects.create(
            slug="may19", title="May 19 Workshop",
            date=dt.date(2026, 5, 19),
            content_id="11111111-1111-1111-1111-111111111111",
            source_repo="workshops-content",
            event=duplicate,
        )

        merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        workshop.refresh_from_db()
        self.assertEqual(workshop.event_id, canonical.pk)


class RetireDuplicateTest(TestCase):
    def test_duplicate_cancelled_unpublished_not_deleted(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event(status="completed", published=True)

        merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        # Row survives — no hard delete.
        self.assertTrue(Event.objects.filter(pk=duplicate.pk).exists())
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "cancelled")
        self.assertFalse(duplicate.published)
        self.assertIsNone(duplicate.published_at)

    def test_origin_invariant_preserved_on_both(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event()

        merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        canonical.refresh_from_db()
        duplicate.refresh_from_db()
        self.assertEqual(canonical.origin, "studio")
        self.assertEqual(canonical.source_repo, "")
        self.assertEqual(duplicate.origin, "github")
        self.assertEqual(duplicate.source_repo, "workshops-content")


class IdempotencyTest(TestCase):
    def test_already_merged_is_noop(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event(status="cancelled", published=False)

        plan = merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)

        self.assertTrue(plan.already_merged)
        self.assertEqual(plan.registrations_moved, 0)
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_events").exists())

    def test_second_merge_is_noop(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event()
        merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)
        canonical.refresh_from_db()
        duplicate.refresh_from_db()

        plan = merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=False)
        self.assertTrue(plan.already_merged)


class DryRunTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="d@test.com", password="x")

    def test_dry_run_writes_nothing(self):
        canonical = _make_studio_event(recording_url="")
        duplicate = _make_github_event(recording_url="https://youtu.be/dup")
        EventRegistration.objects.create(event=duplicate, user=self.user)

        plan = merge_duplicate_events(
            canonical, duplicate, actor_label="t", dry_run=True)

        # Plan reports the move, but nothing persisted.
        self.assertEqual(plan.registrations_moved, 1)
        canonical.refresh_from_db()
        duplicate.refresh_from_db()
        self.assertEqual(canonical.recording_url, "")
        self.assertEqual(duplicate.status, "completed")
        self.assertTrue(
            EventRegistration.objects.filter(event=duplicate).exists())
        self.assertFalse(
            EventRegistration.objects.filter(event=canonical).exists())
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_events").exists())


class AuditTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(
            email="staff@test.com", password="x", is_staff=True)

    def test_audit_row_written_with_actor_and_summary(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event()

        merge_duplicate_events(
            canonical, duplicate,
            actor_label="studio:staff@test.com",
            actor=self.actor, dry_run=False)

        log = CommunityAuditLog.objects.get(action="merge_events")
        self.assertEqual(log.user, self.actor)
        self.assertIn(str(canonical.pk), log.details)
        self.assertIn(str(duplicate.pk), log.details)
        self.assertIn("studio:staff@test.com", log.details)

    def test_no_actor_writes_no_row(self):
        canonical = _make_studio_event()
        duplicate = _make_github_event()

        merge_duplicate_events(
            canonical, duplicate, actor_label="cli", actor=None,
            dry_run=False)

        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_events").exists())
