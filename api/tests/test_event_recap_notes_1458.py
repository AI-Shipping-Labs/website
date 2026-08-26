"""Staff API surface for event recap notes (issue #1458).

The automation case: a book club organiser posts weekly notes from a script.
``recap_notes`` is writable; ``has_recap`` / ``recap_published`` / ``recap_url``
are derived read-only state. GitHub-origin events keep the blanket
409 ``synced_event_read_only`` contract on PATCH — Studio stays the escape
hatch for the rare synced-event recap.
"""

import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event

User = get_user_model()


class EventRecapNotesApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-recap-api@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="recap")
        now = timezone.now()
        cls.past_event = Event.objects.create(
            title="Book Club Kickoff",
            slug="book-club-kickoff",
            description="Weekly book club meeting.",
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status="completed",
            origin="studio",
        )
        cls.synced_event = Event.objects.create(
            title="Synced Launch",
            slug="synced-launch",
            description="Synced from content",
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status="completed",
            origin="github",
            source_repo="AI-Shipping-Labs/content",
            source_path="events/synced-launch.yaml",
            content_id=uuid.uuid4(),
            recap_file="launch/recap.md",
            recap_html="<h2>Synced recap</h2>",
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _patch(self, slug, payload):
        return self.client.patch(
            f"/api/events/{slug}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def test_detail_exposes_recap_fields(self):
        response = self.client.get(
            f"/api/events/{self.past_event.slug}", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["recap_notes"], "")
        self.assertFalse(body["has_recap"])
        self.assertFalse(body["recap_published"])
        self.assertEqual(body["recap_url"], "")

    def test_detail_reports_synced_recap_state_for_github_events(self):
        response = self.client.get(
            f"/api/events/{self.synced_event.slug}", **self._auth(),
        )
        body = response.json()
        # A synced recap counts as a recap even though recap_notes is empty.
        self.assertEqual(body["recap_notes"], "")
        self.assertTrue(body["has_recap"])
        self.assertTrue(body["recap_published"])
        self.assertEqual(body["recap_url"], self.synced_event.get_recap_url())

    def test_patch_writes_notes_and_publishes_the_recap_page(self):
        response = self._patch(
            self.past_event.slug,
            {"recap_notes": "## Week 1\n\nBatching and KV cache."},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["has_recap"])
        self.assertTrue(body["recap_published"])
        self.assertTrue(body["recap_url"].endswith("/recap"))

        page = self.client.get(body["recap_url"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "<h2>Week 1</h2>")
        self.assertContains(page, "Batching and KV cache.")

    def test_patch_with_empty_string_clears_notes_and_recap_404s_again(self):
        self._patch(self.past_event.slug, {"recap_notes": "## Week 1\n\nNotes."})
        self.past_event.refresh_from_db()
        recap_url = self.past_event.get_recap_url()

        response = self._patch(self.past_event.slug, {"recap_notes": ""})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["has_recap"])
        self.assertFalse(body["recap_published"])
        self.assertEqual(body["recap_url"], "")

        page = self.client.get(recap_url)
        self.assertEqual(page.status_code, 404)

    def test_patch_on_github_origin_event_still_returns_409(self):
        response = self._patch(
            self.synced_event.slug, {"recap_notes": "Should not land."},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "synced_event_read_only")
        self.synced_event.refresh_from_db()
        self.assertEqual(self.synced_event.recap_notes, "")
