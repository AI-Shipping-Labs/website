"""Member event discovery, detail, and self-registration API tests."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, tag
from freezegun import freeze_time

from accounts.models import MemberAPIKey, Token, User
from analytics.models import UserActivity
from content.access import LEVEL_MAIN, LEVEL_PREMIUM
from content.models import Instructor
from events.models import (
    Event,
    EventHost,
    EventRegistration,
    EventSeries,
    Host,
    SeriesOccurrenceOptOut,
    SeriesRegistration,
)
from tests.fixtures import TierSetupMixin

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


@freeze_time(NOW)
@tag("core")
class MemberEventsApiTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email="event-member@test.com",
            tier=cls.main_tier,
            account_activated=False,
        )
        cls.other = User.objects.create_user(
            email="event-other@test.com",
            tier=cls.main_tier,
        )
        cls.free_member = User.objects.create_user(
            email="event-free@test.com",
            tier=cls.free_tier,
        )
        cls.key, cls.plaintext = MemberAPIKey.create_for_user(
            user=cls.member,
            name="events",
        )
        cls.free_key, cls.free_plaintext = MemberAPIKey.create_for_user(
            user=cls.free_member,
            name="free events",
        )
        cls.staff = User.objects.create_user(
            email="event-staff@test.com",
            is_staff=True,
        )
        cls.operator_token = Token.objects.create(user=cls.staff, name="operator")

    def _auth(self, key=None):
        return {"HTTP_AUTHORIZATION": f"Token {key or self.plaintext}"}

    def _event(self, slug, **overrides):
        defaults = {
            "slug": slug,
            "title": slug.replace("-", " ").title(),
            "description": "## Build together\n\nBring **questions**.",
            "start_datetime": NOW + timedelta(days=2),
            "end_datetime": NOW + timedelta(days=2, hours=2),
            "status": "upcoming",
            "published": True,
            "required_level": LEVEL_MAIN,
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    def test_existing_keys_with_old_metadata_get_every_member_capability(self):
        historical_keys = [
            MemberAPIKey.create_for_user(
                user=self.member,
                name="plans era",
                scopes=["plans:read"],
            )[1],
            MemberAPIKey.create_for_user(
                user=self.member,
                name="books era",
                scopes=["books:read"],
            )[1],
            self.plaintext,
        ]
        for plaintext in historical_keys:
            with self.subTest(prefix=plaintext[:24]):
                plans = self.client.get(
                    "/member-api/v1/plans",
                    **self._auth(plaintext),
                )
                books = self.client.get(
                    "/member-api/v1/books/reader-profile",
                    **self._auth(plaintext),
                )
                events = self.client.get(
                    "/member-api/v1/events",
                    **self._auth(plaintext),
                )
                self.assertEqual(plans.json(), {"plans": []})
                self.assertIn("visibility", books.json())
                self.assertEqual(events.json()["events"], [])

    def test_invalid_credentials_keep_member_401_contract(self):
        event = self._event("auth-event")
        revoked_key, revoked = MemberAPIKey.create_for_user(
            user=self.member,
            name="revoked",
        )
        revoked_key.revoke()
        credentials = [
            {},
            {"HTTP_AUTHORIZATION": "Bearer malformed"},
            self._auth(revoked),
            self._auth(self.operator_token.key),
        ]
        self.client.force_login(self.member)
        for headers in credentials:
            with self.subTest(headers=headers):
                response = self.client.post(
                    f"/member-api/v1/events/{event.id}/register",
                    data="{}",
                    content_type="application/json",
                    **headers,
                )
                self.assertEqual(response.status_code, 401)
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())

    def test_list_applies_time_visibility_external_and_pagination_contract(self):
        accessible = self._event("accessible", start_datetime=NOW + timedelta(days=1))
        self._event(
            "premium",
            start_datetime=NOW + timedelta(days=2),
            required_level=LEVEL_PREMIUM,
        )
        external = self._event(
            "external",
            start_datetime=NOW + timedelta(days=3),
            required_level=LEVEL_PREMIUM,
            external_host="Maven",
            zoom_join_url="https://maven.com/event",
        )
        self._event("draft", status="draft")
        self._event("cancelled", status="cancelled")
        past = self._event(
            "past",
            status="completed",
            start_datetime=NOW - timedelta(days=2),
            end_datetime=NOW - timedelta(days=2) + timedelta(hours=1),
        )

        upcoming = self.client.get("/member-api/v1/events", **self._auth()).json()
        past_data = self.client.get(
            "/member-api/v1/events?filter=past",
            **self._auth(),
        ).json()

        self.assertEqual([item["id"] for item in upcoming["events"]], [accessible.id, external.id])
        self.assertEqual(upcoming["events"][0]["registration_source"], "none")
        self.assertFalse(upcoming["events"][0]["is_registered"])
        self.assertTrue(upcoming["events"][0]["registration_available"])
        self.assertEqual(upcoming["events"][0]["registration_targets"], ["event"])
        self.assertEqual(upcoming["events"][1]["external_registration_url"], "https://maven.com/event")
        self.assertFalse(upcoming["events"][1]["registration_available"])
        self.assertEqual(upcoming["events"][1]["registration_targets"], [])
        self.assertEqual(upcoming["pagination"], {
            "page": 1,
            "page_size": 20,
            "total": 2,
            "total_pages": 1,
        })
        self.assertEqual([item["id"] for item in past_data["events"]], [past.id])
        self.assertFalse(past_data["events"][0]["registration_available"])
        self.assertEqual(past_data["events"][0]["registration_targets"], [])

        invalid = self.client.get(
            "/member-api/v1/events?filter=tomorrow",
            **self._auth(),
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["code"], "validation_error")

    def test_list_paginates_tied_occurrences_and_rejects_invalid_pages(self):
        tied_start = NOW + timedelta(days=1)
        events = [
            self._event(
                f"page-{index:02d}",
                start_datetime=tied_start,
                end_datetime=tied_start + timedelta(hours=1),
            )
            for index in range(22)
        ]

        first_page = self.client.get(
            "/member-api/v1/events?page=1",
            **self._auth(),
        ).json()
        second_page = self.client.get(
            "/member-api/v1/events?page=2",
            **self._auth(),
        ).json()

        self.assertEqual(
            [item["id"] for item in first_page["events"]],
            [event.id for event in events[:20]],
        )
        self.assertEqual(
            [item["id"] for item in second_page["events"]],
            [event.id for event in events[20:]],
        )
        self.assertEqual(first_page["pagination"], {
            "page": 1,
            "page_size": 20,
            "total": 22,
            "total_pages": 2,
        })
        self.assertEqual(second_page["pagination"]["page"], 2)

        for invalid_page in ("0", "-1", "abc", "1.5"):
            with self.subTest(page=invalid_page):
                response = self.client.get(
                    f"/member-api/v1/events?page={invalid_page}",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "validation_error")

    def test_list_uses_effective_end_boundaries_for_upcoming_and_past(self):
        active = self._event(
            "active-at-boundary",
            start_datetime=NOW - timedelta(minutes=30),
            end_datetime=NOW + timedelta(seconds=1),
        )
        ended = self._event(
            "ended-at-boundary",
            start_datetime=NOW - timedelta(hours=1),
            end_datetime=NOW,
        )
        active_without_end = self._event(
            "active-without-end",
            start_datetime=NOW - timedelta(minutes=59, seconds=59),
            end_datetime=None,
        )
        ended_without_end = self._event(
            "ended-without-end",
            start_datetime=NOW - timedelta(hours=1),
            end_datetime=None,
        )

        upcoming = self.client.get(
            "/member-api/v1/events",
            **self._auth(),
        ).json()
        past = self.client.get(
            "/member-api/v1/events?filter=past",
            **self._auth(),
        ).json()

        self.assertEqual(
            {item["id"] for item in upcoming["events"]},
            {active.id, active_without_end.id},
        )
        self.assertEqual(
            {item["id"] for item in past["events"]},
            {ended.id, ended_without_end.id},
        )

    def test_detail_returns_public_fields_and_no_private_fields(self):
        series = EventSeries.objects.create(name="Agent workloads", slug="agents")
        event = self._event(
            "detail",
            event_series=series,
            location="Zoom",
            zoom_meeting_id="secret-id",
            zoom_join_url="https://zoom.us/j/raw-secret",
            host_email="private-host@test.com",
            source_repo="",
            cover_image_url="https://cdn.example.com/banner.png",
        )
        instructor = Instructor.objects.create(
            instructor_id="teacher",
            name="Teacher",
            email="teacher-private@test.com",
            bio="Public bio",
            photo_url="https://cdn.example.com/teacher.png",
            links=[{"label": "Site", "url": "https://example.com"}],
            status="published",
        )
        event.instructors.add(instructor, through_defaults={"position": 0})
        host = Host.objects.create(
            name="Host",
            slug="host",
            email="host-private@test.com",
            bio="Host bio",
        )
        EventHost.objects.create(event=event, host=host, position=0)
        EventRegistration.objects.create(event=event, user=self.other)

        data = self.client.get(
            f"/member-api/v1/events/{event.id}",
            **self._auth(),
        ).json()

        self.assertEqual(data["id"], event.id)
        self.assertEqual(data["description"], event.description)
        self.assertIn("<strong>questions</strong>", data["description_html"])
        self.assertEqual(data["series"]["id"], series.id)
        self.assertEqual(data["attendee_count"], 1)
        self.assertEqual(data["banner_url"], "https://cdn.example.com/banner.png")
        self.assertEqual(data["instructors"][0]["instructor_id"], "teacher")
        self.assertEqual(data["hosts"][0]["slug"], "host")
        serialized = str(data)
        for private in (
            "private-host@test.com",
            "teacher-private@test.com",
            "host-private@test.com",
            "secret-id",
            "raw-secret",
            "source_repo",
            "registrations",
        ):
            with self.subTest(private=private):
                self.assertNotIn(private, serialized)

    def test_detail_visibility_errors_do_not_leak_content(self):
        draft = self._event("draft-detail", status="draft", description="draft secret")
        retired = self._event(
            "retired",
            status="cancelled",
            published=False,
            description="retired secret",
        )
        premium = self._event(
            "premium-detail",
            required_level=LEVEL_PREMIUM,
            description="premium secret",
        )
        for event, expected in ((draft, 404), (retired, 404), (premium, 403)):
            with self.subTest(event=event.slug):
                response = self.client.get(
                    f"/member-api/v1/events/{event.id}",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("secret", str(response.json()))

    @patch("member_api.views.events._send_registration_emails")
    def test_standalone_registration_is_owner_only_and_runs_side_effects(self, send):
        event = self._event("standalone")
        forbidden = self.client.post(
            f"/member-api/v1/events/{event.id}/register",
            data={"email": self.other.email},
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(forbidden.status_code, 422)
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())

        response = self.client.post(
            f"/member-api/v1/events/{event.id}/register",
            data={},
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["registration_source"], "event")
        self.assertTrue(payload["is_registered"])
        self.assertFalse(payload["registration_available"])
        self.assertEqual(payload["registration_targets"], [])
        self.assertEqual(payload["registration_status"], "registered")
        registration = EventRegistration.objects.get(event=event)
        self.assertEqual(registration.user, self.member)
        self.member.refresh_from_db()
        self.assertTrue(self.member.account_activated)
        self.assertTrue(UserActivity.objects.filter(
            user=self.member,
            event_type=UserActivity.EVENT_EVENT_REGISTER,
            object_id=event.slug,
        ).exists())
        send.assert_called_once_with(self.member, event, registration, None)

    @patch("member_api.views.events._send_registration_emails")
    def test_series_default_fans_out_and_event_target_stays_single(self, send):
        series = EventSeries.objects.create(name="Series", slug="series")
        first = self._event("series-1", event_series=series)
        second = self._event(
            "series-2",
            event_series=series,
            start_datetime=NOW + timedelta(days=3),
            end_datetime=NOW + timedelta(days=3, hours=1),
        )
        self._event(
            "series-locked",
            event_series=series,
            start_datetime=NOW + timedelta(days=4),
            end_datetime=NOW + timedelta(days=4, hours=1),
            required_level=LEVEL_PREMIUM,
        )
        opted_out = self._event(
            "series-opted",
            event_series=series,
            start_datetime=NOW + timedelta(days=5),
            end_datetime=NOW + timedelta(days=5, hours=1),
        )
        SeriesRegistration.objects.create(series=series, user=self.member)
        SeriesOccurrenceOptOut.objects.create(
            series=series,
            event=opted_out,
            user=self.member,
        )

        response = self.client.post(
            f"/member-api/v1/events/{first.id}/register",
            data={},
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            set(EventRegistration.objects.filter(user=self.member).values_list("event_id", flat=True)),
            {first.id, second.id},
        )
        payload = response.json()
        self.assertEqual(payload["registration_source"], "event")
        self.assertTrue(payload["is_registered"])
        summary = payload["summary"]
        self.assertEqual(summary["registered"], 2)
        self.assertEqual(summary["skipped_no_access"], 1)
        self.assertEqual(summary["skipped_opted_out"], 1)

        another = User.objects.create_user(email="single@test.com", tier=self.main_tier)
        _, another_key = MemberAPIKey.create_for_user(user=another, name="single")
        single = self.client.post(
            f"/member-api/v1/events/{second.id}/register",
            data={"scope": "event"},
            content_type="application/json",
            **self._auth(another_key),
        )
        self.assertEqual(single.status_code, 201)
        self.assertEqual(EventRegistration.objects.filter(user=another).count(), 1)
        self.assertFalse(SeriesRegistration.objects.filter(user=another).exists())

    @patch("member_api.views.events._send_registration_emails")
    def test_rejected_registration_matrix_has_no_side_effects(self, send):
        draft = self._event("draft-register", status="draft")
        cancelled = self._event("cancelled-register", status="cancelled")
        past = self._event(
            "closed",
            status="completed",
            start_datetime=NOW - timedelta(days=1),
            end_datetime=NOW - timedelta(days=1) + timedelta(hours=1),
        )
        inaccessible = self._event(
            "inaccessible-register",
            required_level=LEVEL_PREMIUM,
        )
        upcoming = self._event("duplicate")
        EventRegistration.objects.create(event=upcoming, user=self.member)
        external = self._event(
            "external-register",
            required_level=LEVEL_PREMIUM,
            external_host="Luma",
            zoom_join_url="https://lu.ma/public",
        )
        cases = (
            (draft, 404, "event_not_found"),
            (cancelled, 409, "event_registration_closed"),
            (past, 409, "event_registration_closed"),
            (inaccessible, 403, "event_access_denied"),
            (upcoming, 409, "already_registered"),
            (external, 409, "external_registration_required"),
        )
        initial_registration_count = EventRegistration.objects.count()
        initial_activity_count = UserActivity.objects.count()

        for event, expected_status, expected_code in cases:
            with self.subTest(event=event.slug):
                response = self.client.post(
                    f"/member-api/v1/events/{event.id}/register",
                    data={},
                    content_type="application/json",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["code"], expected_code)
                self.assertEqual(
                    EventRegistration.objects.count(),
                    initial_registration_count,
                )
                self.assertEqual(UserActivity.objects.count(), initial_activity_count)
                self.member.refresh_from_db()
                self.assertFalse(self.member.account_activated)
                send.assert_not_called()

        external_response = self.client.post(
            f"/member-api/v1/events/{external.id}/register",
            data={},
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(
            external_response.json()["details"]["registration_url"],
            "https://lu.ma/public",
        )
        send.assert_not_called()
        self.assertFalse(EventRegistration.objects.filter(event=external).exists())

    def test_join_url_timing_matrix_is_registered_member_only_and_platform_safe(self):
        event = self._event(
            "live",
            required_level=0,
            start_datetime=NOW + timedelta(minutes=10),
            end_datetime=NOW + timedelta(minutes=70),
            zoom_join_url="https://zoom.us/j/private-live",
        )
        EventRegistration.objects.create(event=event, user=self.member)
        timing_cases = (
            (NOW, None),
            (NOW + timedelta(minutes=6), event.get_join_url()),
            (NOW + timedelta(minutes=71), None),
        )

        for at_time, expected_registered_url in timing_cases:
            with self.subTest(at_time=at_time), freeze_time(at_time):
                registered = self.client.get(
                    f"/member-api/v1/events/{event.id}",
                    **self._auth(),
                ).json()
                unregistered = self.client.get(
                    f"/member-api/v1/events/{event.id}",
                    **self._auth(self.free_plaintext),
                ).json()

                self.assertEqual(registered["join_url"], expected_registered_url)
                self.assertIsNone(unregistered["join_url"])
                self.assertNotIn("private-live", str(registered))
                self.assertNotIn("private-live", str(unregistered))
