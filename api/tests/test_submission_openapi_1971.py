"""OpenAPI contract for homework submission reads (#1971)."""

from django.test import SimpleTestCase

from api.openapi import build_spec
from api.urls import urlpatterns


class SubmissionReadOpenApiTest(SimpleTestCase):
    def test_documents_required_target_query(self):
        document = build_spec(urlpatterns)
        operation = document["paths"]["/api/submissions/on-behalf"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}

        self.assertEqual(
            set(parameters), {"user", "homework_id", "course", "homework", "cohort"},
        )
        self.assertTrue(parameters["user"]["required"])
        self.assertFalse(parameters["homework_id"]["required"])
