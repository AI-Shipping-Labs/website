"""Tests for the Sprint endpoints (issue #433)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from api.serializers.plans import serialize_sprint
from events.models.event_series import EventSeries
from plans.models import (
    ACCOUNTABILITY_SOURCE_MANUAL,
    ACCOUNTABILITY_SOURCE_RANDOM,
    Plan,
    Sprint,
    SprintAccountabilityPartner,
    SprintEnrollment,
)
from plans.services.accountability import assign_accountability_partners

User = get_user_model()


class SprintApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="staff")

        cls.sprint_active = Sprint.objects.create(
            name="May 2026", slug="may-2026",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6, status="active",
        )
        cls.sprint_draft = Sprint.objects.create(
            name="Jul 2026", slug="jul-2026",
            start_date=datetime.date(2026, 7, 1),
            duration_weeks=4, status="draft",
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}


class SerializeSprintEndDateTest(TestCase):
    """``serialize_sprint`` exposes the derived ``end_date`` (issue #978)."""

    def test_includes_end_date_iso_and_retains_duration_weeks(self):
        sprint = Sprint.objects.create(
            name="June 2026", slug="june-2026",
            start_date=datetime.date(2026, 6, 17),
            duration_weeks=6, status="active",
        )
        data = serialize_sprint(sprint)
        self.assertEqual(data["start_date"], "2026-06-17")
        self.assertEqual(data["end_date"], "2026-07-29")
        self.assertEqual(data["duration_weeks"], 6)
        self.assertEqual(
            set(data["lifecycle_badge"]),
            {"state", "label"},
        )


class SprintsListTest(SprintApiTestBase):
    def test_list_returns_canonical_shape(self):
        response = self.client.get("/api/sprints", **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("sprints", body)
        self.assertEqual(len(body["sprints"]), 2)
        first = body["sprints"][0]
        # Exactly the documented keys.
        self.assertEqual(
            set(first.keys()),
            {
                "slug", "name", "start_date", "end_date",
                "description", "outcomes", "audience",
                "duration_weeks", "status", "lifecycle_badge", "event_series",
                "created_at", "updated_at",
            },
        )

    def test_list_filters_by_status(self):
        response = self.client.get(
            "/api/sprints?status=active", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        slugs = {s["slug"] for s in response.json()["sprints"]}
        self.assertIn("may-2026", slugs)
        self.assertNotIn("jul-2026", slugs)


class SprintsCreateTest(SprintApiTestBase):
    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/sprints",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def test_create_returns_201_and_persists(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "description": "Build and ship.",
            "outcomes": "Prototype\nLaunch",
            "audience": "AI builders",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Sprint.objects.count(), before + 1)
        body = response.json()
        self.assertEqual(body["slug"], "sep-2026")
        self.assertEqual(body["duration_weeks"], 8)
        self.assertEqual(body["status"], "draft")  # default
        self.assertEqual(body["description"], "Build and ship.")
        self.assertEqual(body["outcomes"], "Prototype\nLaunch")
        self.assertEqual(body["audience"], "AI builders")

    def test_create_rejects_missing_required_field(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "x", "slug": "x", "duration_weeks": 6,
        })
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "missing_field")
        self.assertEqual(body["details"]["field"], "start_date")
        self.assertEqual(Sprint.objects.count(), before)

    def test_create_rejects_non_string_landing_field(self):
        response = self._post({
            "name": "Bad", "slug": "bad-landing-field",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "outcomes": ["not", "a", "string"],
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")
        self.assertFalse(Sprint.objects.filter(slug="bad-landing-field").exists())


class SprintDetailTest(SprintApiTestBase):
    def test_detail_for_unknown_slug_returns_404(self):
        response = self.client.get("/api/sprints/nope", **self._auth())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_sprint")

    def test_patch_updates_only_supplied_fields(self):
        response = self.client.patch(
            "/api/sprints/may-2026",
            data=json.dumps({"status": "completed"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(
            set(body["lifecycle_badge"]),
            {"state", "label"},
        )
        # Untouched fields keep their values.
        self.assertEqual(body["name"], "May 2026")
        self.assertEqual(body["start_date"], "2026-05-01")

    def test_patch_updates_landing_fields_independently(self):
        self.sprint_active.description = "Original"
        self.sprint_active.outcomes = "Keep this"
        self.sprint_active.audience = "Keep this too"
        self.sprint_active.save(update_fields=[
            "description", "outcomes", "audience",
        ])

        response = self.client.patch(
            "/api/sprints/may-2026",
            data=json.dumps({"description": "Updated"}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["description"], "Updated")
        self.assertEqual(body["outcomes"], "Keep this")
        self.assertEqual(body["audience"], "Keep this too")

    def test_patch_rejects_non_string_landing_field_without_writing(self):
        response = self.client.patch(
            "/api/sprints/may-2026",
            data=json.dumps({"description": {"html": "not accepted"}}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.sprint_active.refresh_from_db()
        self.assertEqual(self.sprint_active.description, "")

    def test_detail_includes_stored_status_and_date_lifecycle_badge(self):
        response = self.client.get("/api/sprints/may-2026", **self._auth())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["lifecycle_badge"]["state"], "ended")
        self.assertEqual(body["lifecycle_badge"]["label"], "Ended")

    def test_patch_completed_is_staff_only_and_preserves_lifecycle_badge(self):
        response = self.client.patch(
            "/api/sprints/may-2026",
            data=json.dumps({"status": "completed"}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["lifecycle_badge"]["state"], "ended")
        self.assertEqual(body["lifecycle_badge"]["label"], "Ended")

    def test_patch_completed_rejects_missing_invalid_and_non_staff_tokens(self):
        non_staff_token = Token(
            key="non-staff-sprint-patch-token",
            user=self.member,
            name="legacy-non-staff-sprint-patch",
        )
        Token.objects.bulk_create([non_staff_token])

        requests = [
            ({}, 401),
            ({"HTTP_AUTHORIZATION": "Token does-not-exist"}, 401),
            (self._auth(non_staff_token), 401),
        ]
        for headers, expected_status in requests:
            with self.subTest(headers=headers):
                self.sprint_active.status = "active"
                self.sprint_active.save(update_fields=["status"])
                response = self.client.patch(
                    "/api/sprints/may-2026",
                    data=json.dumps({"status": "completed"}),
                    content_type="application/json",
                    **headers,
                )

                self.assertEqual(response.status_code, expected_status)
                self.sprint_active.refresh_from_db()
                self.assertEqual(self.sprint_active.status, "active")

    def test_delete_returns_405_pointing_to_studio(self):
        # Issue #864 (2026-06-13): sprint DELETE is blocked via the API. The
        # 405 fires before any lookup and the sprint row is never touched.
        Plan.objects.create(member=self.member, sprint=self.sprint_active)
        response = self.client.delete(
            "/api/sprints/may-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        body = response.json()
        self.assertEqual(body["code"], "sprint_delete_not_available")
        self.assertIn("Studio", body["error"])
        self.assertTrue(Sprint.objects.filter(slug="may-2026").exists())

    def test_delete_empty_sprint_also_returns_405(self):
        # Even an empty sprint (no attached plans) cannot be deleted via the
        # API; the row survives.
        response = self.client.delete(
            "/api/sprints/jul-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.json()["code"], "sprint_delete_not_available",
        )
        self.assertTrue(Sprint.objects.filter(slug="jul-2026").exists())


class SprintsAuthTest(SprintApiTestBase):
    def test_no_header_returns_401_no_side_effects(self):
        before = Sprint.objects.count()
        response = self.client.get("/api/sprints")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Sprint.objects.count(), before)

    def test_invalid_token_returns_401(self):
        response = self.client.get(
            "/api/sprints",
            HTTP_AUTHORIZATION="Token does-not-exist",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_wrong_method_returns_405(self):
        response = self.client.put(
            "/api/sprints", **self._auth(),
        )
        self.assertEqual(response.status_code, 405)


class SprintAccountabilityPartnersApiTest(SprintApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.alice = User.objects.create_user(
            email="alice@example.com",
            password="pw",
            first_name="Alice",
            last_name="Example",
        )
        cls.bob = User.objects.create_user(
            email="bob@example.com",
            password="pw",
            first_name="Bob",
            last_name="Example",
        )
        cls.cara = User.objects.create_user(
            email="cara@example.com",
            password="pw",
            first_name="Cara",
            last_name="Example",
        )
        cls.dana = User.objects.create_user(
            email="dana@example.com",
            password="pw",
            first_name="Dana",
            last_name="Example",
        )
        cls.outsider = User.objects.create_user(
            email="outsider@example.com",
            password="pw",
        )
        for user in (cls.alice, cls.bob, cls.cara, cls.dana):
            SprintEnrollment.objects.create(
                sprint=cls.sprint_active,
                user=user,
                enrolled_by=cls.staff,
            )
        cls.non_staff_token = Token(
            key="non-staff-accountability-token",
            user=cls.member,
            name="legacy-non-staff",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def _accountability_url(self):
        return "/api/sprints/may-2026/accountability-partners"

    def _randomize_url(self):
        return "/api/sprints/may-2026/accountability-partners/randomize"

    def _post_pair(self, member_email, partner_email, *, token=None):
        return self.client.post(
            self._accountability_url(),
            data=json.dumps({
                "member_email": member_email,
                "partner_email": partner_email,
            }),
            content_type="application/json",
            **self._auth(token),
        )

    def _delete_pair(self, member_email, partner_email, *, token=None):
        return self.client.delete(
            self._accountability_url(),
            data=json.dumps({
                "member_email": member_email,
                "partner_email": partner_email,
            }),
            content_type="application/json",
            **self._auth(token),
        )

    def _partner_emails_for(self, body, user_email):
        row = next(
            member
            for member in body["members"]
            if member["user_email"] == user_email
        )
        return [partner["user_email"] for partner in row["partners"]]

    def test_list_returns_enrolled_members_and_partner_metadata(self):
        assign_accountability_partners(
            sprint=self.sprint_active,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )

        response = self.client.get(self._accountability_url(), **self._auth())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["sprint"],
            {"slug": "may-2026", "name": "May 2026"},
        )
        member_emails = [member["user_email"] for member in body["members"]]
        self.assertEqual(
            member_emails,
            [
                "alice@example.com",
                "bob@example.com",
                "cara@example.com",
                "dana@example.com",
            ],
        )
        self.assertNotIn("outsider@example.com", member_emails)
        alice_partner = body["members"][0]["partners"][0]
        self.assertEqual(alice_partner["user_email"], "bob@example.com")
        self.assertEqual(alice_partner["display_name"], "Bob Example")
        self.assertEqual(alice_partner["source"], ACCOUNTABILITY_SOURCE_MANUAL)
        self.assertEqual(alice_partner["assigned_by"], "staff@test.com")
        self.assertIsNotNone(alice_partner["created_at"])
        self.assertIsNotNone(alice_partner["updated_at"])

    def test_list_omits_stale_partner_assignments_to_unenrolled_users(self):
        assign_accountability_partners(
            sprint=self.sprint_active,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )
        SprintEnrollment.objects.filter(
            sprint=self.sprint_active,
            user=self.bob,
        ).delete()

        response = self.client.get(self._accountability_url(), **self._auth())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        member_emails = [member["user_email"] for member in body["members"]]
        self.assertNotIn("bob@example.com", member_emails)
        self.assertEqual(
            self._partner_emails_for(body, "alice@example.com"),
            [],
        )

    def test_manual_assign_creates_reciprocal_edges_and_records_bearer(self):
        response = self._post_pair(
            " Alice@Example.com ",
            "BOB@example.com",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["created"], True)
        self.assertEqual(response.json()["created_edges"], 2)
        self.assertEqual(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                source=ACCOUNTABILITY_SOURCE_MANUAL,
                assigned_by=self.staff,
            ).count(),
            2,
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                member=self.alice,
                partner=self.bob,
            ).exists()
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                member=self.bob,
                partner=self.alice,
            ).exists()
        )

    def test_manual_assign_is_idempotent(self):
        self._post_pair("alice@example.com", "bob@example.com")

        response = self._post_pair("alice@example.com", "bob@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], False)
        self.assertEqual(response.json()["created_edges"], 0)
        self.assertEqual(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                member__in=[self.alice, self.bob],
                partner__in=[self.alice, self.bob],
            ).count(),
            2,
        )

    def test_manual_assign_promotes_existing_random_pair_to_manual(self):
        assign_accountability_partners(
            sprint=self.sprint_active,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
            source=ACCOUNTABILITY_SOURCE_RANDOM,
        )

        response = self._post_pair("alice@example.com", "bob@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])
        self.assertEqual(
            set(
                SprintAccountabilityPartner.objects.filter(
                    sprint=self.sprint_active,
                    member__in=[self.alice, self.bob],
                ).values_list("source", flat=True)
            ),
            {ACCOUNTABILITY_SOURCE_MANUAL},
        )
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                source=ACCOUNTABILITY_SOURCE_RANDOM,
            ).exists()
        )

    def test_delete_removes_pair_without_affecting_other_partners(self):
        assign_accountability_partners(
            sprint=self.sprint_active,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )
        assign_accountability_partners(
            sprint=self.sprint_active,
            member=self.alice,
            partner=self.cara,
            assigned_by=self.staff,
        )

        response = self._delete_pair("alice@example.com", "bob@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"removed": True, "deleted_edges": 2})
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                member=self.alice,
                partner=self.bob,
            ).exists()
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                member=self.alice,
                partner=self.cara,
            ).exists()
        )
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                member=self.cara,
                partner=self.alice,
            ).exists()
        )

    def test_delete_is_idempotent_when_pair_absent(self):
        response = self._delete_pair("alice@example.com", "bob@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"removed": False, "deleted_edges": 0},
        )
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
            ).exists()
        )

    def test_randomize_preserves_manual_pairs_and_returns_counts(self):
        assign_accountability_partners(
            sprint=self.sprint_active,
            member=self.alice,
            partner=self.bob,
            assigned_by=self.staff,
        )

        response = self.client.post(
            self._randomize_url(),
            data=json.dumps({}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["assigned_pair_count"], 1)
        self.assertEqual(body["unassigned_count"], 0)
        self.assertEqual(body["preserved_manual_count"], 2)
        self.assertEqual(body["random_edges_count"], 2)
        self.assertEqual(
            self._partner_emails_for(body, "alice@example.com"),
            ["bob@example.com"],
        )
        self.assertEqual(
            set(self._partner_emails_for(body, "cara@example.com")),
            {"dana@example.com"},
        )
        self.assertEqual(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
                source=ACCOUNTABILITY_SOURCE_MANUAL,
                member__in=[self.alice, self.bob],
            ).count(),
            2,
        )

    def test_randomize_assigns_odd_pool_as_three_person_pod(self):
        SprintEnrollment.objects.filter(
            sprint=self.sprint_active,
            user=self.dana,
        ).delete()

        response = self.client.post(
            self._randomize_url(),
            data=json.dumps({}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["assigned_pair_count"], 3)
        self.assertEqual(body["unassigned_count"], 0)
        self.assertEqual(body["random_edges_count"], 6)
        for email in (
            "alice@example.com",
            "bob@example.com",
            "cara@example.com",
        ):
            self.assertEqual(len(self._partner_emails_for(body, email)), 2)

    def test_unknown_sprint_returns_404(self):
        response = self.client.get(
            "/api/sprints/nope/accountability-partners",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_sprint")

    def test_unknown_user_returns_422_without_writes(self):
        response = self._post_pair(
            "alice@example.com",
            "missing@example.com",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_user")
        self.assertEqual(
            response.json()["details"],
            {"partner_email": "Unknown user"},
        )
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
            ).exists()
        )

    def test_self_partnering_returns_422_without_writes(self):
        response = self._post_pair("alice@example.com", "ALICE@example.com")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
            ).exists()
        )

    def test_non_enrolled_participant_returns_422_without_writes(self):
        response = self._post_pair(
            "alice@example.com",
            "outsider@example.com",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
            ).exists()
        )

    def test_delete_rejects_non_enrolled_participant(self):
        response = self._delete_pair(
            "alice@example.com",
            "outsider@example.com",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_missing_field_returns_422(self):
        response = self.client.post(
            self._accountability_url(),
            data=json.dumps({"member_email": "alice@example.com"}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "missing_field")
        self.assertEqual(response.json()["details"]["field"], "partner_email")

    def test_blank_email_returns_missing_field(self):
        response = self._post_pair("alice@example.com", "   ")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "missing_field")
        self.assertEqual(response.json()["details"]["field"], "partner_email")

    def test_invalid_json_returns_existing_parse_error(self):
        response = self.client.post(
            self._accountability_url(),
            data="{",
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid JSON"})

    def test_non_object_body_returns_structured_error(self):
        response = self.client.post(
            self._randomize_url(),
            data=json.dumps([]),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")

    def test_auth_matrix_rejects_missing_bogus_and_non_staff_tokens(self):
        requests = [
            lambda headers: self.client.get(self._accountability_url(), **headers),
            lambda headers: self.client.post(
                self._accountability_url(),
                data=json.dumps({
                    "member_email": "alice@example.com",
                    "partner_email": "bob@example.com",
                }),
                content_type="application/json",
                **headers,
            ),
            lambda headers: self.client.delete(
                self._accountability_url(),
                data=json.dumps({
                    "member_email": "alice@example.com",
                    "partner_email": "bob@example.com",
                }),
                content_type="application/json",
                **headers,
            ),
            lambda headers: self.client.post(
                self._randomize_url(),
                data=json.dumps({}),
                content_type="application/json",
                **headers,
            ),
        ]

        for make_request in requests:
            for headers in (
                {},
                {"HTTP_AUTHORIZATION": "Token does-not-exist"},
                {"HTTP_AUTHORIZATION": "Bearer nope"},
                self._auth(self.non_staff_token),
            ):
                with self.subTest(headers=headers):
                    response = make_request(headers)
                    self.assertEqual(response.status_code, 401)

        self.assertFalse(
            SprintAccountabilityPartner.objects.filter(
                sprint=self.sprint_active,
            ).exists()
        )

    def test_wrong_methods_return_405_for_staff_token(self):
        response = self.client.put(self._accountability_url(), **self._auth())
        self.assertEqual(response.status_code, 405)

        response = self.client.get(self._randomize_url(), **self._auth())
        self.assertEqual(response.status_code, 405)


class SprintEventSeriesBase(SprintApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name="June 2026 Community Sprint",
            slug="june-2026-community-sprint",
            start_time=datetime.time(18, 0),
        )
        cls.other_series = EventSeries.objects.create(
            name="Other Series",
            slug="other-series",
            start_time=datetime.time(17, 0),
        )

    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/sprints",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _patch(self, slug, payload, *, token=None):
        return self.client.patch(
            f"/api/sprints/{slug}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )


class SprintSerializerEventSeriesTest(SprintEventSeriesBase):
    def test_linked_sprint_serializes_id_and_slug(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        data = serialize_sprint(self.sprint_active)
        self.assertEqual(
            data["event_series"],
            {"id": self.series.id, "slug": "june-2026-community-sprint"},
        )

    def test_unlinked_sprint_serializes_null(self):
        data = serialize_sprint(self.sprint_draft)
        self.assertIsNone(data["event_series"])

    def test_detail_endpoint_shows_linked_series(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self.client.get(
            "/api/sprints/may-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["event_series"],
            {"id": self.series.id, "slug": "june-2026-community-sprint"},
        )

    def test_detail_endpoint_shows_null_when_unlinked(self):
        response = self.client.get(
            "/api/sprints/jul-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["event_series"])


class SprintCreateEventSeriesTest(SprintEventSeriesBase):
    def test_create_with_series_id_links(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": self.series.id,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()["event_series"],
            {"id": self.series.id, "slug": "june-2026-community-sprint"},
        )
        sprint = Sprint.objects.get(slug="sep-2026")
        self.assertEqual(sprint.event_series_id, self.series.id)

    def test_create_with_series_slug_links(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": "june-2026-community-sprint",
        })
        self.assertEqual(response.status_code, 201)
        sprint = Sprint.objects.get(slug="sep-2026")
        self.assertEqual(sprint.event_series_id, self.series.id)

    def test_create_with_numeric_string_id_links(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": str(self.series.id),
        })
        self.assertEqual(response.status_code, 201)
        sprint = Sprint.objects.get(slug="sep-2026")
        self.assertEqual(sprint.event_series_id, self.series.id)

    def test_create_omitting_series_is_unlinked(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
        })
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["event_series"])
        self.assertIsNone(
            Sprint.objects.get(slug="sep-2026").event_series_id,
        )

    def test_create_with_null_series_is_unlinked(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": None,
        })
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(
            Sprint.objects.get(slug="sep-2026").event_series_id,
        )

    def test_create_with_unknown_id_returns_422_no_write(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": 999999,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_series")
        self.assertEqual(Sprint.objects.count(), before)

    def test_create_with_unknown_slug_returns_422_no_write(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": "no-such-series",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_series")
        self.assertFalse(Sprint.objects.filter(slug="sep-2026").exists())
        self.assertEqual(Sprint.objects.count(), before)

    def test_create_with_wrong_type_returns_422_validation_error(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": [],
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(Sprint.objects.count(), before)


class SprintPatchEventSeriesTest(SprintEventSeriesBase):
    def test_patch_sets_link_with_id_and_bumps_updated_at(self):
        before = Sprint.objects.get(slug="may-2026").updated_at
        response = self._patch("may-2026", {"event_series": self.series.id})
        self.assertEqual(response.status_code, 200)
        sprint = Sprint.objects.get(slug="may-2026")
        self.assertEqual(sprint.event_series_id, self.series.id)
        self.assertGreater(sprint.updated_at, before)

    def test_patch_retargets_with_slug(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch(
            "may-2026", {"event_series": "other-series"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Sprint.objects.get(slug="may-2026").event_series_id,
            self.other_series.id,
        )

    def test_patch_null_clears_link(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch("may-2026", {"event_series": None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(
            Sprint.objects.get(slug="may-2026").event_series_id,
        )

    def test_patch_empty_string_clears_link(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch("may-2026", {"event_series": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(
            Sprint.objects.get(slug="may-2026").event_series_id,
        )

    def test_patch_omitting_series_leaves_link_untouched(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch("may-2026", {"status": "completed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Sprint.objects.get(slug="may-2026").event_series_id,
            self.series.id,
        )

    def test_patch_unknown_id_returns_422_unchanged(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch("may-2026", {"event_series": 999999})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_series")
        self.assertEqual(
            Sprint.objects.get(slug="may-2026").event_series_id,
            self.series.id,
        )

    def test_patch_wrong_type_returns_422_validation_error(self):
        response = self._patch("may-2026", {"event_series": {"id": 1}})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertIsNone(
            Sprint.objects.get(slug="may-2026").event_series_id,
        )


class SprintEventSeriesAuthTest(SprintEventSeriesBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Mint a token while the user is staff, then demote: the token still
        # authenticates the user, but the staff gate must reject the write
        # before any event_series resolution runs. ``token_required`` returns
        # 401 for a now-non-staff token; the view's own 403 branch is
        # defence in depth. Either rejection is acceptable -- what matters is
        # that the request never resolves the series and never writes.
        cls.demoted = User.objects.create_user(
            email="demoted@test.com", password="pw", is_staff=True,
        )
        cls.demoted_token = Token.objects.create(
            user=cls.demoted, name="demoted",
        )
        cls.demoted.is_staff = False
        cls.demoted.save(update_fields=["is_staff"])

    def test_non_staff_post_rejected_before_resolution(self):
        before = Sprint.objects.count()
        response = self._post(
            {
                "name": "Sep 2026", "slug": "sep-2026",
                "start_date": "2026-09-01", "duration_weeks": 8,
                "event_series": self.series.id,
            },
            token=self.demoted_token,
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(Sprint.objects.count(), before)
        self.assertFalse(Sprint.objects.filter(slug="sep-2026").exists())

    def test_non_staff_patch_rejected_before_resolution(self):
        response = self._patch(
            "may-2026",
            {"event_series": self.series.id},
            token=self.demoted_token,
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertIsNone(
            Sprint.objects.get(slug="may-2026").event_series_id,
        )
