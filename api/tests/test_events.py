"""Tests and contract notes for the staff events API (issue #627).

The API is for operator integrations, not browser sessions. It uses staff
``Authorization: Token <key>`` auth, exposes GitHub-origin events for
inventory/detail reads, but only lets API clients create or edit Studio-origin
rows. Deletion is deliberately unavailable through this API; clients get a
stable guidance error and must use Studio for manual deletion.
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

DELETE_MESSAGE = (
    "Event deletion is not available through the API. "
    "Go to Studio to delete this event manually."
)


class EventsApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-events@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-events@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="events")
        cls.non_staff_token = Token(
            key="non-staff-events-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

        cls.start = timezone.now() + timedelta(days=7)
        cls.github_event = Event.objects.create(
            title="GitHub Synced Event",
            slug="github-synced-event",
            description="Synced from content",
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=1),
            status="upcoming",
            origin="github",
            source_repo="AI-Shipping-Labs/content",
            source_path="events/github.md",
            content_id=uuid.uuid4(),
        )
        cls.studio_event = Event.objects.create(
            title="Studio Event",
            slug="studio-event",
            description="Studio owned",
            start_datetime=cls.start + timedelta(days=1),
            end_datetime=cls.start + timedelta(days=1, hours=1),
            status="draft",
            origin="studio",
            tags=["studio"],
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/events",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _patch(self, slug, payload, *, token=None):
        return self.client.patch(
            f"/api/events/{slug}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )


class EventsAuthAndMethodTest(EventsApiTestBase):
    def test_missing_malformed_invalid_and_non_staff_tokens_return_json_401(self):
        cases = [
            ({}, {"error": "Authentication token required"}),
            (
                {"HTTP_AUTHORIZATION": self.staff_token.key},
                {"error": "Authentication token required"},
            ),
            (
                {"HTTP_AUTHORIZATION": "Token does-not-exist"},
                {"error": "Invalid token"},
            ),
            (
                self._auth(self.non_staff_token),
                {"error": "Invalid token"},
            ),
        ]

        for headers, expected_body in cases:
            with self.subTest(headers=headers):
                before = Event.objects.count()
                response = self.client.get("/api/events", **headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), expected_body)
                self.assertEqual(Event.objects.count(), before)

    def test_delete_collection_and_detail_returns_guidance_without_mutating(self):
        before = {
            event.slug: (event.title, event.status)
            for event in Event.objects.order_by("slug")
        }

        for path in (
            "/api/events",
            "/api/events/studio-event",
            "/api/events/github-synced-event",
            "/api/events/unknown-slug",
        ):
            with self.subTest(path=path):
                response = self.client.delete(path, **self._auth())
                self.assertEqual(response.status_code, 405)
                body = response.json()
                self.assertEqual(body["code"], "event_delete_not_available")
                self.assertEqual(body["error"], DELETE_MESSAGE)

        after = {
            event.slug: (event.title, event.status)
            for event in Event.objects.order_by("slug")
        }
        self.assertEqual(after, before)
        self.assertEqual(Event.objects.count(), 2)


class EventsListAndDetailTest(EventsApiTestBase):
    def test_list_returns_canonical_shape_and_editable_flags(self):
        response = self.client.get("/api/events", **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"events"})

        by_slug = {event["slug"]: event for event in body["events"]}
        self.assertFalse(by_slug["github-synced-event"]["editable"])
        self.assertTrue(by_slug["studio-event"]["editable"])
        self.assertEqual(
            set(by_slug["studio-event"].keys()),
            {
                "id",
                "slug",
                "title",
                "description",
                "kind",
                "platform",
                "start_datetime",
                "end_datetime",
                "timezone",
                "zoom_join_url",
                "location",
                "tags",
                "required_level",
                "max_participants",
                "status",
                "external_host",
                "published",
                "origin",
                "source_repo",
                "source_path",
                "editable",
                "created_at",
                "updated_at",
            },
        )

    def test_list_filters_by_status_origin_and_title_search(self):
        Event.objects.create(
            title="Studio Upcoming Python",
            slug="studio-upcoming-python",
            start_datetime=self.start + timedelta(days=2),
            end_datetime=self.start + timedelta(days=2, hours=1),
            status="upcoming",
            origin="studio",
        )

        response = self.client.get(
            "/api/events?status=upcoming&origin=studio&q=Python",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [event["slug"] for event in response.json()["events"]],
            ["studio-upcoming-python"],
        )

    def test_invalid_list_filters_return_structured_422(self):
        for query, field in (
            ("status=not-a-status", "status"),
            ("origin=api", "origin"),
        ):
            with self.subTest(query=query):
                response = self.client.get(f"/api/events?{query}", **self._auth())
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "validation_error")
                self.assertIn(field, response.json()["details"])

    def test_detail_returns_event_or_unknown_event_404(self):
        response = self.client.get("/api/events/github-synced-event", **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source_repo"], "AI-Shipping-Labs/content")
        self.assertEqual(body["source_path"], "events/github.md")
        self.assertFalse(body["editable"])

        missing = self.client.get("/api/events/nope", **self._auth())
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "unknown_event")


class EventsCreateTest(EventsApiTestBase):
    def test_create_with_minimal_fields_applies_studio_defaults(self):
        start = (self.start + timedelta(days=10)).isoformat()
        response = self._post({"title": "Minimal API Event", "start_datetime": start})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["slug"], "minimal-api-event")
        self.assertEqual(body["kind"], "standard")
        self.assertEqual(body["platform"], "zoom")
        self.assertEqual(body["timezone"], "Europe/Berlin")
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["required_level"], 0)
        self.assertTrue(body["published"])
        self.assertEqual(body["tags"], [])
        self.assertEqual(body["location"], "")
        self.assertEqual(body["external_host"], "")
        self.assertIsNone(body["max_participants"])
        self.assertEqual(body["origin"], "studio")
        self.assertEqual(body["source_repo"], "")

        event = Event.objects.get(slug="minimal-api-event")
        self.assertEqual(event.end_datetime, event.start_datetime + timedelta(hours=1))

    def test_create_custom_event_persists_writable_fields(self):
        start = self.start + timedelta(days=11)
        response = self._post({
            "title": "Custom API Event",
            "slug": "custom-api-event",
            "description": "Details",
            "kind": "meetup",
            "platform": "custom",
            "start_datetime": start.isoformat(),
            "end_datetime": (start + timedelta(hours=2)).isoformat(),
            "timezone": "UTC",
            "zoom_join_url": "https://example.com/join",
            "location": "External",
            "tags": ["api", "custom"],
            "required_level": 20,
            "max_participants": 25,
            "status": "upcoming",
            "external_host": "Luma",
            "published": False,
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["slug"], "custom-api-event")
        self.assertEqual(body["zoom_join_url"], "https://example.com/join")
        self.assertEqual(body["tags"], ["api", "custom"])
        self.assertEqual(body["required_level"], 20)
        self.assertFalse(body["published"])

    def test_create_rejects_read_only_source_fields(self):
        before = Event.objects.count()
        for field in ("origin", "source_repo", "source_path", "source_commit", "content_id"):
            with self.subTest(field=field):
                payload = {
                    "title": f"Read Only {field}",
                    "start_datetime": self.start.isoformat(),
                    field: "x",
                }
                response = self._post(payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "read_only_field")
                self.assertEqual(response.json()["details"]["field"], field)
        self.assertEqual(Event.objects.count(), before)

    def test_create_validation_errors_do_not_create_event(self):
        before = Event.objects.count()
        response = self._post({
            "title": "",
            "slug": "studio-event",
            "start_datetime": "bad",
            "end_datetime": self.start.isoformat(),
            "kind": "bad-kind",
            "platform": "bad-platform",
            "status": "bad-status",
            "required_level": 5,
            "max_participants": 0,
            "tags": ["ok", 3],
            "external_host": "UnknownHost",
            "published": "yes",
        })

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        for field in (
            "title",
            "slug",
            "start_datetime",
            "kind",
            "platform",
            "status",
            "required_level",
            "max_participants",
            "tags",
            "external_host",
            "published",
        ):
            self.assertIn(field, body["details"])
        self.assertEqual(Event.objects.count(), before)

    def test_create_rejects_end_before_start(self):
        response = self._post({
            "title": "Bad Ordering",
            "start_datetime": self.start.isoformat(),
            "end_datetime": (self.start - timedelta(hours=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["details"]["end_datetime"],
            "Must be after start_datetime.",
        )


class EventsUpdateTest(EventsApiTestBase):
    def test_patch_updates_studio_origin_event_and_preserves_omitted_fields(self):
        new_end = self.studio_event.end_datetime + timedelta(hours=2)
        response = self._patch(
            "studio-event",
            {
                "title": "Updated Studio Event",
                "status": "upcoming",
                "required_level": 10,
                "end_datetime": new_end.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["title"], "Updated Studio Event")
        self.assertEqual(body["status"], "upcoming")
        self.assertEqual(body["required_level"], 10)
        self.assertEqual(body["description"], "Studio owned")
        self.assertEqual(body["tags"], ["studio"])

    def test_patch_slug_change_moves_detail_url(self):
        response = self._patch("studio-event", {"slug": "renamed-studio-event"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["slug"], "renamed-studio-event")

        old_response = self.client.get("/api/events/studio-event", **self._auth())
        self.assertEqual(old_response.status_code, 404)
        new_response = self.client.get(
            "/api/events/renamed-studio-event",
            **self._auth(),
        )
        self.assertEqual(new_response.status_code, 200)

    def test_patch_duplicate_slug_and_read_only_fields_return_422(self):
        duplicate = self._patch("studio-event", {"slug": "github-synced-event"})
        self.assertEqual(duplicate.status_code, 422)
        self.assertEqual(duplicate.json()["code"], "validation_error")
        self.assertIn("slug", duplicate.json()["details"])

        read_only = self._patch("studio-event", {"source_repo": "repo"})
        self.assertEqual(read_only.status_code, 422)
        self.assertEqual(read_only.json()["code"], "read_only_field")

    def test_patch_github_origin_event_is_read_only_and_not_mutated(self):
        before = (self.github_event.title, self.github_event.status)
        response = self._patch(
            "github-synced-event",
            {"title": "Changed", "status": "cancelled"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "synced_event_read_only")
        self.github_event.refresh_from_db()
        self.assertEqual((self.github_event.title, self.github_event.status), before)

    def test_patch_custom_platform_clears_zoom_meeting_id(self):
        self.studio_event.zoom_meeting_id = "123456"
        self.studio_event.save()

        response = self._patch(
            "studio-event",
            {
                "platform": "custom",
                "zoom_join_url": "https://example.com/custom",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.studio_event.refresh_from_db()
        self.assertEqual(self.studio_event.zoom_join_url, "https://example.com/custom")
        self.assertEqual(self.studio_event.zoom_meeting_id, "")
