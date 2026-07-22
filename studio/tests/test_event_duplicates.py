"""View + flow tests for the Studio duplicate-event merge tool (issue #881).

Covers the confirm-token guard (a tampered/stale token is refused and no merge
happens), the preview-is-a-no-op guarantee, the staff gate, and the
end-to-end-on-the-server preview->confirm path including the duplicate
disappearing from the public events surfaces.
"""

import datetime as dt

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import Client, TestCase

from content.models import Article
from events.models import Event, EventRegistration

User = get_user_model()

MAY19 = dt.datetime(2026, 5, 19, 0, 0, tzinfo=dt.timezone.utc)
MAY19_STUDIO = dt.datetime(2026, 5, 19, 15, 0, tzinfo=dt.timezone.utc)


def _studio_event():
    return Event.objects.create(
        slug="may19-studio", title="May 19 Workshop",
        start_datetime=MAY19_STUDIO, origin="studio", source_repo="",
        status="upcoming", published=True)


def _github_event():
    return Event.objects.create(
        slug="may19-github", title="May 19 Workshop",
        start_datetime=MAY19, origin="github", source_repo="workshops-content",
        kind="workshop", status="completed", published=True)


class DuplicatesListTest(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(
            email="staff@test.com", password="x", is_staff=True)
        self.client.login(email="staff@test.com", password="x")

    def test_list_returns_200_and_template(self):
        response = self.client.get("/studio/events/duplicates/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "studio/events/duplicates.html")

    def test_candidate_pair_listed(self):
        _studio_event()
        _github_event()
        response = self.client.get("/studio/events/duplicates/")
        self.assertContains(response, 'data-testid="event-duplicate-row"')

    def test_empty_state_when_no_pairs(self):
        response = self.client.get("/studio/events/duplicates/")
        self.assertContains(response, 'data-testid="event-duplicates-empty"')


class StaffGateTest(TestCase):
    def test_non_staff_blocked(self):
        client = Client()
        User.objects.create_user(email="member@test.com", password="x")
        client.login(email="member@test.com", password="x")
        response = client.get("/studio/events/duplicates/")
        self.assertEqual(response.status_code, 403)


class PreviewIsNoOpTest(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(
            email="staff@test.com", password="x", is_staff=True)
        self.client.login(email="staff@test.com", password="x")
        self.member = User.objects.create_user(email="m@test.com", password="x")

    def test_preview_writes_nothing(self):
        canonical = _studio_event()
        duplicate = _github_event()
        EventRegistration.objects.create(event=duplicate, user=self.member)

        response = self.client.post(
            "/studio/events/duplicates/preview",
            {"canonical_id": canonical.pk, "duplicate_id": duplicate.pk})
        self.assertContains(response, 'data-testid="event-merge-preview"')

        # Nothing persisted.
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "completed")
        self.assertTrue(duplicate.published)
        self.assertTrue(
            EventRegistration.objects.filter(event=duplicate).exists())
        self.assertFalse(
            EventRegistration.objects.filter(event=canonical).exists())

    def test_preview_reports_source_articles_to_relink(self):
        canonical = _studio_event()
        duplicate = _github_event()
        Article.objects.create(
            slug='preview-source-article',
            title='Preview Source Article',
            date=dt.date(2026, 5, 20),
            source_event=duplicate,
        )

        response = self.client.post(
            "/studio/events/duplicates/preview",
            {"canonical_id": canonical.pk, "duplicate_id": duplicate.pk},
        )

        self.assertContains(response, 'Source articles to relink: 1')


class ConfirmTokenGuardTest(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(
            email="staff@test.com", password="x", is_staff=True)
        self.client.login(email="staff@test.com", password="x")

    def test_tampered_token_refuses_merge(self):
        canonical = _studio_event()
        duplicate = _github_event()
        # A token signed for a DIFFERENT pair (swapped ids) must be rejected.
        bad_token = signing.dumps(
            {"canonical_pk": duplicate.pk, "duplicate_pk": canonical.pk},
            salt="studio.event_merge.confirm")

        response = self.client.post(
            "/studio/events/duplicates/confirm",
            {"canonical_id": canonical.pk, "duplicate_id": duplicate.pk,
             "confirm_token": bad_token})

        self.assertContains(response, 'data-testid="event-merge-error-confirm"')
        # No merge happened.
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "completed")

    def test_garbage_token_refuses_merge(self):
        canonical = _studio_event()
        duplicate = _github_event()
        response = self.client.post(
            "/studio/events/duplicates/confirm",
            {"canonical_id": canonical.pk, "duplicate_id": duplicate.pk,
             "confirm_token": "not-a-real-token"})
        self.assertContains(response, 'data-testid="event-merge-error-confirm"')
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "completed")


class PreviewConfirmFlowTest(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(
            email="staff@test.com", password="x", is_staff=True)
        self.client.login(email="staff@test.com", password="x")
        self.member = User.objects.create_user(email="m@test.com", password="x")

    def test_preview_then_confirm_merges_and_hides_duplicate(self):
        canonical = _studio_event()
        duplicate = _github_event()
        EventRegistration.objects.create(event=duplicate, user=self.member)

        # Preview: pull the signed token out of the rendered form.
        preview = self.client.post(
            "/studio/events/duplicates/preview",
            {"canonical_id": canonical.pk, "duplicate_id": duplicate.pk})
        token = preview.context["confirm_token"]
        self.assertIsNotNone(token)

        # Confirm with the real token.
        confirm = self.client.post(
            "/studio/events/duplicates/confirm",
            {"canonical_id": canonical.pk, "duplicate_id": duplicate.pk,
             "confirm_token": token})
        self.assertContains(
            confirm, 'data-testid="event-merge-result-headline"')

        # Registration moved, duplicate retired (not deleted).
        self.assertTrue(
            EventRegistration.objects.filter(
                event=canonical, user=self.member).exists())
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "cancelled")
        self.assertFalse(duplicate.published)
        self.assertTrue(Event.objects.filter(pk=duplicate.pk).exists())

        # Duplicate gone from the public listing; canonical present.
        listing = self.client.get("/events", follow=True)
        self.assertNotContains(listing, "may19-github")

        # Duplicate detail 404s for the public (cancelled + unpublished).
        detail = Client().get(
            f"/events/{duplicate.pk}/{duplicate.slug}", follow=True)
        self.assertEqual(detail.status_code, 404)
