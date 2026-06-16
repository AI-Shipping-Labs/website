"""Focused tests for shared JSON API utility helpers."""

from django.test import SimpleTestCase

from api.utils import (
    body_must_be_object_response,
    coerce_optional_text,
    delete_not_available_response,
    parse_bool_query,
    validation_response,
)


class ApiUtilsTest(SimpleTestCase):
    def test_body_must_be_object_response_shape_and_status(self):
        response = body_must_be_object_response()
        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(
            response.content,
            {
                "error": "Body must be a JSON object",
                "code": "invalid_type",
                "details": {"field": "body", "expected": "object"},
            },
        )

        self.assertEqual(body_must_be_object_response(status=422).status_code, 422)

    def test_validation_response_shape_and_status(self):
        response = validation_response({"name": "Name is required."})
        self.assertEqual(response.status_code, 422)
        self.assertJSONEqual(
            response.content,
            {
                "error": "Validation error",
                "code": "validation_error",
                "details": {"name": "Name is required."},
            },
        )

    def test_coerce_optional_text(self):
        self.assertEqual(coerce_optional_text(None), "")
        self.assertEqual(coerce_optional_text("  hello  "), "hello")
        self.assertEqual(coerce_optional_text(123), "123")

    def test_parse_bool_query_accepts_canonical_values(self):
        for value in ("true", " TRUE ", "1", "yes", "on"):
            with self.subTest(value=value):
                self.assertIs(parse_bool_query(value), True)

        for value in ("false", " FALSE ", "0", "no", "off"):
            with self.subTest(value=value):
                self.assertIs(parse_bool_query(value), False)

        for value in (None, "", "maybe"):
            with self.subTest(value=value):
                self.assertIsNone(parse_bool_query(value))

    def test_delete_not_available_response_shape_and_status(self):
        response = delete_not_available_response(
            "Delete through Studio.",
            "thing_delete_not_available",
        )
        self.assertEqual(response.status_code, 405)
        self.assertJSONEqual(
            response.content,
            {
                "error": "Delete through Studio.",
                "code": "thing_delete_not_available",
            },
        )
