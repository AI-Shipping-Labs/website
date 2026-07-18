import json
import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from api.openapi import build_spec
from api.urls import urlpatterns
from content.models import Course, CourseInstructor, Instructor, Workshop

User = get_user_model()


class ContentPolishApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="api-1292@example.com", password="pw", is_staff=True
        )
        cls.token = Token.objects.create(user=cls.staff, name="issue-1292")
        cls.member = User.objects.create_user(email="member-api-1292@example.com", password="pw")
        cls.member_token = Token(key="member-token-1292", user=cls.member, name="member")
        Token.objects.bulk_create([cls.member_token])
        cls.course = Course.objects.create(title="Database course", slug="database-course")
        cls.source_course = Course.objects.create(
            title="Source course", slug="source-course", source_repo="owner/repo"
        )
        cls.ada = Instructor.objects.create(instructor_id="ada", name="Ada")
        cls.grace = Instructor.objects.create(instructor_id="grace", name="Grace")
        cls.workshop = Workshop.objects.create(
            content_id=uuid.uuid4(),
            slug="private-preview",
            title="Private preview",
            date=date(2026, 7, 18),
            status="draft",
        )

    @property
    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_course_instructors_get_put_and_atomic_validation(self):
        url = f"/api/courses/{self.course.slug}/instructors"
        self.assertEqual(self.client.get(url).status_code, 401)

        response = self.client.put(
            url,
            data=json.dumps({"instructor_ids": ["grace", "ada"]}),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"instructors": [
                {"instructor_id": "grace", "name": "Grace", "position": 0},
                {"instructor_id": "ada", "name": "Ada", "position": 1},
            ]},
        )
        response = self.client.put(
            url,
            data=json.dumps({"instructor_ids": ["ada", "unknown"]}),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            list(CourseInstructor.objects.filter(course=self.course).values_list(
                "instructor__instructor_id", flat=True
            )),
            ["grace", "ada"],
        )
        duplicate = self.client.put(
            url,
            data=json.dumps({"instructor_ids": ["ada", "ada"]}),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(duplicate.status_code, 422)
        self.assertEqual(
            list(CourseInstructor.objects.filter(course=self.course).values_list(
                "instructor__instructor_id", flat=True
            )),
            ["grace", "ada"],
        )
        emptied = self.client.put(
            url,
            data=json.dumps({"instructor_ids": []}),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(emptied.status_code, 200)
        self.assertEqual(emptied.json(), {"instructors": []})

    def test_course_instructor_api_auth_404_and_strict_shape(self):
        url = f"/api/courses/{self.course.slug}/instructors"
        self.assertEqual(
            self.client.get(
                url, HTTP_AUTHORIZATION=f"Token {self.member_token.key}"
            ).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/courses/missing/instructors", **self.auth).status_code, 404)
        response = self.client.put(
            url,
            data=json.dumps({"instructor_ids": ["ada"], "extra": True}),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 422)

    def test_source_course_rejects_api_replacement(self):
        response = self.client.put(
            f"/api/courses/{self.source_course.slug}/instructors",
            data=json.dumps({"instructor_ids": ["ada"]}),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "source_owned")

    def test_workshop_preview_api_is_private_and_rotation_invalidates_old_url(self):
        link_url = f"/api/workshops/{self.workshop.slug}/preview-link"
        rotate_url = f"/api/workshops/{self.workshop.slug}/preview-token/regenerate"
        self.assertEqual(self.client.get(link_url).status_code, 401)
        first = self.client.get(link_url, **self.auth).json()
        self.assertEqual(set(first), {"slug", "preview_url"})
        self.assertEqual(self.client.get(first["preview_url"]).status_code, 200)
        second_response = self.client.post(rotate_url, **self.auth)
        self.assertEqual(second_response.status_code, 200)
        second = second_response.json()
        self.assertNotEqual(first["preview_url"], second["preview_url"])
        self.assertEqual(self.client.get(first["preview_url"]).status_code, 404)
        self.assertEqual(self.client.get(second["preview_url"]).status_code, 200)
        self.assertEqual(self.client.get("/api/workshops/missing/preview-link", **self.auth).status_code, 404)
        self.assertEqual(
            self.client.get(
                link_url, HTTP_AUTHORIZATION=f"Token {self.member_token.key}"
            ).status_code,
            401,
        )

    def test_campaign_recipient_count_is_aggregate_only_and_strict(self):
        User.objects.create_user(
            email="eligible-1292@example.com", password="pw", email_verified=True
        )
        url = "/api/campaigns/recipient-count"
        self.assertEqual(self.client.post(url, data="{}", content_type="application/json").status_code, 401)
        response = self.client.post(
            url, data=json.dumps({}), content_type="application/json", **self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"recipient_count"})
        response = self.client.post(
            url,
            data=json.dumps({"unknown": "field"}),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        invalid_token = self.client.post(
            url,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Token invalid",
        )
        self.assertEqual(invalid_token.status_code, 401)
        nonstaff = self.client.post(
            url,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.member_token.key}",
        )
        self.assertEqual(nonstaff.status_code, 401)
        for payload in (
            {"target_min_level": 999},
            {"slack_filter": "sometimes"},
            {"audience_verification": "maybe"},
            {"target_tags_any": "not-a-list"},
            {"target_event": 999999},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    url, data=json.dumps(payload), content_type="application/json", **self.auth
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "validation_error")


class ContentPolishOpenApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.paths = build_spec(urlpatterns)["paths"]

    def test_campaign_recount_contract_is_documented(self):
        operation = self.paths["/api/campaigns/recipient-count"]["post"]
        self.assertEqual(set(operation["responses"]), {"200", "401", "422"})
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(
            set(schema["properties"]),
            {
                "target_min_level", "target_tags_any", "target_tags_none",
                "slack_filter", "audience_verification", "target_event",
            },
        )
        self.assertEqual(
            set(operation["responses"]["200"]["content"]["application/json"]["example"]),
            {"recipient_count"},
        )

    def test_workshop_preview_contracts_are_documented(self):
        for path, method in (
            ("/api/workshops/{slug}/preview-link", "get"),
            ("/api/workshops/{slug}/preview-token/regenerate", "post"),
        ):
            with self.subTest(path=path):
                operation = self.paths[path][method]
                self.assertEqual(set(operation["responses"]), {"200", "401", "404"})
                self.assertEqual(operation["parameters"][0]["name"], "slug")
                example = operation["responses"]["200"]["content"]["application/json"]["example"]
                self.assertEqual(set(example), {"slug", "preview_url"})

    def test_course_instructor_contract_is_documented(self):
        operations = self.paths["/api/courses/{slug}/instructors"]
        self.assertEqual(set(operations), {"get", "put"})
        self.assertEqual(set(operations["get"]["responses"]), {"200", "401", "404"})
        self.assertEqual(
            set(operations["put"]["responses"]),
            {"200", "401", "404", "409", "422"},
        )
        schema = operations["put"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(schema["required"], ["instructor_ids"])
        self.assertEqual(schema["properties"]["instructor_ids"]["type"], "array")
