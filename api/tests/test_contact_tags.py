"""Staff-token contact-tag namespace API contracts."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from accounts.models import Token
from api.openapi import build_spec
from api.urls import urlpatterns as api_urlpatterns

User = get_user_model()


class ContactTagsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="tag-api-staff@test.com",
            is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="tag api")
        cls.member = User.objects.create_user(email="tag-api-member@test.com")
        cls.member_token = Token(
            key="non-staff-contact-tag-token",
            user=cls.member,
            name="nonstaff",
        )
        Token.objects.bulk_create([cls.member_token])
        cls.alice = User.objects.create_user(
            email="tag-api-alice@test.com",
            tags=["wave-2", "paid"],
        )
        cls.bob = User.objects.create_user(
            email="tag-api-bob@test.com",
            tags=["wave-2"],
        )

    def _auth(self, key=None):
        return {
            "HTTP_AUTHORIZATION": (
                f"Token {key if key is not None else self.staff_token.key}"
            ),
        }

    def test_staff_lists_sorted_tags_with_counts(self):
        response = self.client.get(
            reverse("api_contact_tags_collection"),
            **self._auth(),
        )

        self.assertEqual(
            response.json(),
            {
                "tags": [
                    {"name": "paid", "user_count": 1},
                    {"name": "wave-2", "user_count": 2},
                ],
                "count": 2,
            },
        )

    def test_staff_renames_with_canonical_helper_semantics(self):
        response = self.client.post(
            reverse("api_contact_tag_rename", args=["wave-2"]),
            data=json.dumps({"new": "Wave 3"}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(
            response.json(),
            {"old": "wave-2", "new": "wave-3", "affected": 2},
        )
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        for user in (self.alice, self.bob):
            self.assertIn("wave-3", user.tags)
            self.assertNotIn("wave-2", user.tags)
            self.assertEqual(
                set(user.contact_tags.values_list("slug", flat=True)),
                set(user.tags),
            )

    def test_empty_new_name_returns_422_without_mutation(self):
        response = self.client.post(
            reverse("api_contact_tag_rename", args=["wave-2"]),
            data=json.dumps({"new": "!!!"}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_tag")
        self.alice.refresh_from_db()
        self.assertIn("wave-2", self.alice.tags)

    def test_both_endpoints_require_staff_token(self):
        list_url = reverse("api_contact_tags_collection")
        rename_url = reverse("api_contact_tag_rename", args=["wave-2"])
        cases = ({}, self._auth(self.member_token.key))

        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get(list_url, **headers).status_code, 401)
                response = self.client.post(
                    rename_url,
                    data=json.dumps({"new": "wave-3"}),
                    content_type="application/json",
                    **headers,
                )
                self.assertEqual(response.status_code, 401)

    def test_no_global_delete_route_exists(self):
        match = resolve("/api/contact-tags/wave-2/delete")
        self.assertNotEqual(match.func.__module__, "api.views.contact_tags")

    def test_openapi_documents_list_and_rename_only(self):
        document = build_spec(api_urlpatterns)
        collection = document["paths"]["/api/contact-tags"]
        rename = document["paths"]["/api/contact-tags/{name}/rename"]

        self.assertEqual(set(collection), {"get"})
        self.assertEqual(set(rename), {"post"})
        self.assertEqual(rename["post"]["parameters"][0]["name"], "name")
        self.assertIn("422", rename["post"]["responses"])
