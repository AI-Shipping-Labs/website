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
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventHost, EventRegistration, Host
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.services.zoom import ZoomAPIError

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
        cls.host_1 = Host.objects.create(
            name="Alpha Host",
            slug="alpha-host",
            bio="Alpha bio",
            email="alpha@example.com",
        )
        cls.host_2 = Host.objects.create(
            name="Beta Host",
            slug="beta-host",
            bio="Beta bio",
            photo_url="https://cdn.example.com/beta.jpg",
            email="beta@example.com",
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
                "status",
                "series_position",
                "external_host",
                "published",
                "host_email",
                "hosts",
                "banner_url",
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
    def setUp(self):
        clear_config_cache()
        super().setUp()

    def tearDown(self):
        clear_config_cache()
        super().tearDown()

    def _set_event_display_timezone(self, timezone_name):
        IntegrationSetting.objects.update_or_create(
            key="EVENT_DISPLAY_TIMEZONE",
            defaults={
                "value": timezone_name,
                "group": "site",
                "is_secret": False,
                "description": "Default public event timezone.",
            },
        )
        clear_config_cache()

    def test_create_with_minimal_fields_applies_studio_defaults(self):
        start = (self.start + timedelta(days=10)).isoformat()
        response = self._post({"title": "Minimal API Event", "start_datetime": start})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["slug"], "minimal-api-event")
        self.assertEqual(body["kind"], "standard")
        self.assertEqual(body["platform"], "zoom")
        self.assertEqual(body["timezone"], "UTC")
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["required_level"], 0)
        self.assertTrue(body["published"])
        self.assertEqual(body["tags"], [])
        self.assertEqual(body["location"], "")
        self.assertEqual(body["external_host"], "")
        self.assertEqual(body["origin"], "studio")
        self.assertEqual(body["source_repo"], "")

        event = Event.objects.get(slug="minimal-api-event")
        self.assertEqual(event.end_datetime, event.start_datetime + timedelta(hours=1))

    def test_create_without_timezone_uses_token_owner_preferred_timezone(self):
        self.staff.preferred_timezone = "Europe/Berlin"
        self.staff.save(update_fields=["preferred_timezone"])
        start = (self.start + timedelta(days=10)).isoformat()

        response = self._post({
            "title": "Preferred Timezone API Event",
            "start_datetime": start,
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["timezone"], "Europe/Berlin")
        event = Event.objects.get(slug="preferred-timezone-api-event")
        self.assertEqual(event.timezone, "Europe/Berlin")

    def test_create_with_explicit_timezone_preserves_override(self):
        self.staff.preferred_timezone = "Europe/Berlin"
        self.staff.save(update_fields=["preferred_timezone"])
        start = (self.start + timedelta(days=10)).isoformat()

        response = self._post({
            "title": "Explicit Timezone API Event",
            "start_datetime": start,
            "timezone": "America/New_York",
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["timezone"], "America/New_York")
        event = Event.objects.get(slug="explicit-timezone-api-event")
        self.assertEqual(event.timezone, "America/New_York")

    def test_create_without_timezone_uses_site_default_when_preference_missing(self):
        self.staff.preferred_timezone = ""
        self.staff.save(update_fields=["preferred_timezone"])
        self._set_event_display_timezone("Asia/Kolkata")
        start = (self.start + timedelta(days=10)).isoformat()

        response = self._post({
            "title": "Site Default Timezone API Event",
            "start_datetime": start,
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["timezone"], "Asia/Kolkata")
        event = Event.objects.get(slug="site-default-timezone-api-event")
        self.assertEqual(event.timezone, "Asia/Kolkata")

    def test_create_without_timezone_falls_back_to_utc_when_config_invalid(self):
        self.staff.preferred_timezone = "Not/AZone"
        self.staff.save(update_fields=["preferred_timezone"])
        self._set_event_display_timezone("Mars/Phobos")
        start = (self.start + timedelta(days=10)).isoformat()

        response = self._post({
            "title": "UTC Fallback API Event",
            "start_datetime": start,
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["timezone"], "UTC")
        event = Event.objects.get(slug="utc-fallback-api-event")
        self.assertEqual(event.timezone, "UTC")

    def test_create_rejects_invalid_explicit_timezone_without_creating_event(self):
        before = Event.objects.count()
        start = (self.start + timedelta(days=10)).isoformat()

        response = self._post({
            "title": "Invalid Timezone API Event",
            "start_datetime": start,
            "timezone": "Mars/Phobos",
        })

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["timezone"], "Unknown timezone.")
        self.assertEqual(Event.objects.count(), before)

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

    def test_create_coerces_optional_text_fields(self):
        response = self._post({
            "title": "  Text Event  ",
            "description": None,
            "location": "  Zoom Room  ",
            "zoom_join_url": None,
            "start_datetime": self.start.isoformat(),
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["title"], "Text Event")
        self.assertEqual(body["description"], "")
        self.assertEqual(body["location"], "Zoom Room")
        self.assertEqual(body["zoom_join_url"], "")

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
            "tags",
            "external_host",
            "published",
        ):
            self.assertIn(field, body["details"])
        self.assertEqual(Event.objects.count(), before)

    def test_create_returns_400_on_non_object_body(self):
        response = self.client.post(
            "/api/events",
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")
        self.assertEqual(
            response.json()["details"],
            {"field": "body", "expected": "object"},
        )

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

    def test_create_with_host_ids_assigns_hosts_in_order(self):
        response = self._post({
            "title": "Hosted API Event",
            "start_datetime": (self.start + timedelta(days=12)).isoformat(),
            "host_ids": [self.host_2.id, self.host_1.id],
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(
            [host["id"] for host in body["hosts"]],
            [self.host_2.id, self.host_1.id],
        )
        self.assertEqual(
            body["hosts"][0],
            {
                "id": self.host_2.id,
                "name": "Beta Host",
                "slug": "beta-host",
                "photo_url": "https://cdn.example.com/beta.jpg",
                "email": "beta@example.com",
            },
        )
        event = Event.objects.get(slug=body["slug"])
        self.assertEqual(
            list(
                EventHost.objects.filter(event=event).values_list(
                    "host_id", "position"
                )
            ),
            [(self.host_2.id, 0), (self.host_1.id, 1)],
        )

    def test_create_rejects_unknown_host_id_without_creating_event(self):
        before = Event.objects.count()
        unknown_id = Host.objects.order_by("-id").first().id + 1000
        response = self._post({
            "title": "Bad Host Event",
            "start_datetime": (self.start + timedelta(days=13)).isoformat(),
            "host_ids": [unknown_id],
        })

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("host_ids", body["details"])
        self.assertIn(str(unknown_id), body["details"]["host_ids"])
        self.assertEqual(Event.objects.count(), before)


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

    def test_patch_returns_400_on_non_object_body(self):
        response = self.client.patch(
            "/api/events/studio-event",
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")
        self.assertEqual(
            response.json()["details"],
            {"field": "body", "expected": "object"},
        )

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

    def test_patch_changes_and_clears_hosts(self):
        EventHost.objects.create(
            event=self.studio_event,
            host=self.host_1,
            position=0,
        )
        EventHost.objects.create(
            event=self.studio_event,
            host=self.host_2,
            position=1,
        )

        response = self._patch("studio-event", {"host_ids": [self.host_2.id]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [host["id"] for host in response.json()["hosts"]],
            [self.host_2.id],
        )

        clear = self._patch("studio-event", {"host_ids": []})
        self.assertEqual(clear.status_code, 200)
        self.assertEqual(clear.json()["hosts"], [])
        self.assertFalse(EventHost.objects.filter(event=self.studio_event).exists())


ZOOM_RESULT = {
    "meeting_id": "88899900011",
    "join_url": "https://zoom.us/j/88899900011",
}

# create_meeting is imported into the view module, so patch it there.
CREATE_MEETING_PATH = "api.views.events.create_meeting"


class EventsCreateZoomTest(EventsApiTestBase):
    """The write-only ``create_zoom`` action trigger (issue #986)."""

    def test_create_zoom_provisions_meeting_and_populates_join_url(self):
        with patch(CREATE_MEETING_PATH, return_value=ZOOM_RESULT) as mock_create:
            response = self._post(
                {
                    "title": "Zoom Office Hours",
                    "platform": "zoom",
                    "start_datetime": "2026-05-05T17:00:00+02:00",
                    "create_zoom": True,
                }
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(mock_create.call_count, 1)
        body = response.json()
        self.assertEqual(body["zoom_join_url"], ZOOM_RESULT["join_url"])
        # create_zoom is a write-only trigger, never echoed back.
        self.assertNotIn("create_zoom", body)
        self.assertNotIn("zoom_error", body)

        # The service was called with the freshly-created event.
        called_event = mock_create.call_args.args[0]
        self.assertEqual(called_event.slug, body["slug"])

        # Re-GET shows the persisted join URL and still no create_zoom field.
        detail = self.client.get(
            f"/api/events/{body['slug']}", **self._auth()
        ).json()
        self.assertEqual(detail["zoom_join_url"], ZOOM_RESULT["join_url"])
        self.assertNotIn("create_zoom", detail)
        event = Event.objects.get(slug=body["slug"])
        self.assertEqual(event.zoom_meeting_id, ZOOM_RESULT["meeting_id"])

    def test_create_without_create_zoom_does_not_call_service(self):
        for payload_extra in ({}, {"create_zoom": False}):
            with self.subTest(extra=payload_extra):
                with patch(CREATE_MEETING_PATH) as mock_create:
                    response = self._post(
                        {
                            "title": f"No Zoom {payload_extra}",
                            "platform": "zoom",
                            "start_datetime": "2026-05-05T17:00:00+02:00",
                            **payload_extra,
                        }
                    )
                self.assertEqual(response.status_code, 201)
                mock_create.assert_not_called()
                event = Event.objects.get(slug=response.json()["slug"])
                self.assertEqual(event.zoom_meeting_id, "")

    def test_patch_adds_zoom_meeting_to_existing_event(self):
        with patch(CREATE_MEETING_PATH, return_value=ZOOM_RESULT) as mock_create:
            response = self._patch("studio-event", {"create_zoom": True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(response.json()["zoom_join_url"], ZOOM_RESULT["join_url"])
        self.studio_event.refresh_from_db()
        self.assertEqual(
            self.studio_event.zoom_meeting_id, ZOOM_RESULT["meeting_id"]
        )

    def test_create_zoom_is_idempotent_when_meeting_exists(self):
        self.studio_event.zoom_meeting_id = "123"
        self.studio_event.zoom_join_url = "https://zoom.us/j/123"
        self.studio_event.save()

        with patch(CREATE_MEETING_PATH) as mock_create:
            response = self._patch("studio-event", {"create_zoom": True})

        self.assertEqual(response.status_code, 200)
        mock_create.assert_not_called()
        self.studio_event.refresh_from_db()
        self.assertEqual(self.studio_event.zoom_meeting_id, "123")
        self.assertEqual(self.studio_event.zoom_join_url, "https://zoom.us/j/123")

    def test_create_zoom_with_custom_platform_is_rejected_before_save(self):
        before_count = Event.objects.count()
        with patch(CREATE_MEETING_PATH) as mock_create:
            response = self._post(
                {
                    "title": "Custom No Zoom",
                    "platform": "custom",
                    "start_datetime": "2026-05-05T17:00:00+02:00",
                    "create_zoom": True,
                }
            )

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("create_zoom", body["details"])
        mock_create.assert_not_called()
        self.assertEqual(Event.objects.count(), before_count)

    def test_zoom_outage_keeps_event_and_allows_safe_retry(self):
        with patch(
            CREATE_MEETING_PATH,
            side_effect=ZoomAPIError("Zoom OAuth credentials not configured. ..."),
        ) as mock_create:
            response = self._post(
                {
                    "title": "Outage Event",
                    "platform": "zoom",
                    "start_datetime": "2026-05-05T17:00:00+02:00",
                    "create_zoom": True,
                }
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(mock_create.call_count, 1)
        body = response.json()
        slug = body["slug"]
        self.assertIn("zoom_error", body)
        self.assertIn("credentials not configured", body["zoom_error"])
        self.assertEqual(body["zoom_join_url"], "")

        # Event persisted despite the Zoom failure.
        detail = self.client.get(f"/api/events/{slug}", **self._auth())
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["zoom_join_url"], "")

        # Retry with the service now succeeding populates the join URL.
        with patch(CREATE_MEETING_PATH, return_value=ZOOM_RESULT) as mock_retry:
            retry = self._patch(slug, {"create_zoom": True})

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(mock_retry.call_count, 1)
        self.assertEqual(retry.json()["zoom_join_url"], ZOOM_RESULT["join_url"])
        self.assertNotIn("zoom_error", retry.json())

    def test_non_boolean_create_zoom_is_rejected(self):
        before_count = Event.objects.count()
        with patch(CREATE_MEETING_PATH) as mock_create:
            response = self._post(
                {
                    "title": "Bad Type",
                    "platform": "zoom",
                    "start_datetime": "2026-05-05T17:00:00+02:00",
                    "create_zoom": "yes",
                }
            )

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("create_zoom", body["details"])
        mock_create.assert_not_called()
        self.assertEqual(Event.objects.count(), before_count)


class EventsHostAutoRegistrationTest(EventsApiTestBase):
    """API create/update uses host auto-registration and attendee email."""

    def _create_payload(self, **overrides):
        payload = {
            "title": "Host Auto Registration Event",
            "platform": "zoom",
            "start_datetime": self.start.isoformat(),
            "status": "upcoming",
            "published": True,
        }
        payload.update(overrides)
        return payload

    def _registration_logs(self, user):
        from email_app.models import EmailLog

        return EmailLog.objects.filter(
            user=user,
            email_type="event_registration",
        )

    def test_create_with_resolvable_host_email_registers_and_emails_host(self):
        host = User.objects.create_user(email="host@test.com", password="pw")

        with patch(
            "events.services.registration_email._send_raw_email",
            return_value="ses-1",
        ) as mock_send:
            response = self._post(
                self._create_payload(host_email=host.email),
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["host_email"], "host@test.com")
        event = Event.objects.get(slug=body["slug"])
        self.assertTrue(
            EventRegistration.objects.filter(event=event, user=host).exists(),
        )
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to_email"], host.email)
        self.assertIn(
            f"/studio/events/{event.pk}/edit",
            mock_send.call_args.kwargs["html_body"],
        )
        self.assertEqual(self._registration_logs(host).count(), 1)

    def test_create_without_host_email_has_no_fallback_mailbox_behavior(self):
        with patch(
            "events.services.registration_email._send_raw_email",
        ) as mock_send, self.assertLogs(
            "events.services.host_registration",
            level="WARNING",
        ) as logs:
            response = self._post(self._create_payload())

        self.assertEqual(response.status_code, 201)
        mock_send.assert_not_called()
        event = Event.objects.get(slug=response.json()["slug"])
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())
        self.assertTrue(
            any("host_email is blank" in line for line in logs.output),
            logs.output,
        )

    def test_create_with_non_user_host_email_skips_registration_and_email(self):
        with patch(
            "events.services.registration_email._send_raw_email",
        ) as mock_send, self.assertLogs(
            "events.services.host_registration",
            level="WARNING",
        ) as logs:
            response = self._post(
                self._create_payload(host_email="external-host@test.com"),
            )

        self.assertEqual(response.status_code, 201)
        mock_send.assert_not_called()
        event = Event.objects.get(slug=response.json()["slug"])
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())
        self.assertTrue(
            any("did not resolve to a platform user" in line for line in logs.output),
            logs.output,
        )

    def test_create_draft_does_not_register_or_email_host(self):
        host = User.objects.create_user(email="host@test.com", password="pw")

        with patch("events.services.registration_email._send_raw_email") as mock_send:
            response = self._post(
                self._create_payload(
                    status="draft", published=False, host_email=host.email,
                )
            )

        self.assertEqual(response.status_code, 201)
        mock_send.assert_not_called()
        event = Event.objects.get(slug=response.json()["slug"])
        self.assertFalse(
            EventRegistration.objects.filter(event=event, user=host).exists(),
        )

    def test_patch_publishing_a_draft_registers_and_emails_once(self):
        host = User.objects.create_user(email="host@test.com", password="pw")
        draft = Event.objects.create(
            title="Draft To Publish",
            slug="draft-to-publish",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            status="draft",
            origin="studio",
            published=False,
            host_email=host.email,
        )

        with patch(
            "events.services.registration_email._send_raw_email",
            return_value="ses-1",
        ) as mock_send:
            first = self._patch(
                draft.slug,
                {"status": "upcoming", "published": True},
            )
        self.assertEqual(first.status_code, 200)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to_email"], host.email)

        with patch(
            "events.services.registration_email._send_raw_email",
        ) as mock_send_again:
            second = self._patch(draft.slug, {"published": True})
        self.assertEqual(second.status_code, 200)
        mock_send_again.assert_not_called()
        self.assertEqual(self._registration_logs(host).count(), 1)

    def test_patch_adding_resolvable_host_email_registers_and_emails_host(self):
        host = User.objects.create_user(email="late-host@test.com", password="pw")
        published = Event.objects.create(
            title="Published No Host",
            slug="published-no-host",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            status="upcoming",
            published=True,
            origin="studio",
            host_email="",
        )

        with patch(
            "events.services.registration_email._send_raw_email",
            return_value="ses-1",
        ) as mock_send:
            response = self._patch(
                published.slug, {"host_email": host.email}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["host_email"], "late-host@test.com")
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to_email"], host.email)
        self.assertTrue(
            EventRegistration.objects.filter(event=published, user=host).exists(),
        )

    def test_invalid_host_email_is_rejected_and_nothing_created(self):
        before_count = Event.objects.count()
        with patch("events.services.registration_email._send_raw_email") as mock_send:
            response = self._post(
                self._create_payload(host_email="not-an-email")
            )

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("host_email", body["details"])
        mock_send.assert_not_called()
        self.assertEqual(Event.objects.count(), before_count)

    def test_empty_host_email_clears_field_without_error(self):
        event = Event.objects.create(
            title="Has Host",
            slug="has-host",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            status="upcoming",
            published=True,
            origin="studio",
            host_email="old@test.com",
        )

        response = self._patch(event.slug, {"host_email": ""})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["host_email"], "")
        event.refresh_from_db()
        self.assertEqual(event.host_email, "")

    def test_host_email_is_serialized_on_list_and_detail(self):
        host = User.objects.create_user(email="host@test.com", password="pw")

        created = self._post(self._create_payload(host_email=host.email))
        slug = created.json()["slug"]

        detail = self.client.get(f"/api/events/{slug}", **self._auth()).json()
        self.assertEqual(detail["host_email"], "host@test.com")

        listing = self.client.get("/api/events", **self._auth()).json()
        match = next(e for e in listing["events"] if e["slug"] == slug)
        self.assertEqual(match["host_email"], "host@test.com")


class EventsSkillDocSyncTest(TestCase):
    """Keep the events skill doc in sync with the create_zoom feature."""

    def test_skill_documents_create_zoom_and_drops_stale_note(self):
        skill_path = (
            Path(settings.BASE_DIR)
            / ".claude"
            / "skills"
            / "ai-shipping-labs-events"
            / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        # Fields table row.
        self.assertIn("| `create_zoom` |", text)
        # Zoom-meetings section mentions the new single-event trigger.
        self.assertIn('"create_zoom": true', text)
        self.assertIn("zoom_error", text)
        # The stale "no fully-automatic per-event Zoom creation" wording is gone.
        self.assertNotIn("no fully-automatic per-event Zoom creation", text)
        # host_email field and host auto-registration are documented.
        self.assertIn("| `host_email` |", text)
        self.assertIn("Host auto-registration", text)
        self.assertIn("platform user", text)
