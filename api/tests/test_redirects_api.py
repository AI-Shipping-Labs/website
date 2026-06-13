"""Tests for the URL redirects JSON API (issue #674)."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from integrations.models import Redirect

User = get_user_model()


class RedirectsApiBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-redirects-api@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-redirects-api@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="r")
        cls.non_staff_token = Token(
            key="non-staff-redirect-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post(self, payload, *, url="/api/redirects", token=None, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload)
        return self.client.post(
            url,
            data=body,
            content_type="application/json",
            **self._auth(token),
        )

    def _patch(self, redirect_id, payload, *, token=None):
        return self.client.patch(
            f"/api/redirects/{redirect_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _delete(self, redirect_id, *, token=None):
        return self.client.delete(
            f"/api/redirects/{redirect_id}",
            **self._auth(token),
        )


class RedirectsCollectionListTest(RedirectsApiBase):
    def test_list_requires_valid_staff_token(self):
        cases = [
            ({}, 401, {"error": "Authentication token required"}),
            (
                {"HTTP_AUTHORIZATION": self.staff_token.key},
                401,
                {"error": "Authentication token required"},
            ),
            (
                {"HTTP_AUTHORIZATION": "Token does-not-exist"},
                401,
                {"error": "Invalid token"},
            ),
            (
                {"HTTP_AUTHORIZATION": f"Token {self.non_staff_token.key}"},
                401,
                {"error": "Invalid token"},
            ),
        ]
        for headers, status, expected in cases:
            with self.subTest(headers=headers):
                response = self.client.get("/api/redirects", **headers)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json(), expected)

    def test_list_returns_envelope_with_all_redirects(self):
        baseline_total = Redirect.objects.count()
        Redirect.objects.create(
            source_path="/test-aaa", target_path="/bbb",
            redirect_type=301, is_active=True,
        )
        Redirect.objects.create(
            source_path="/test-ccc", target_path="/ddd",
            redirect_type=302, is_active=False,
        )
        response = self.client.get("/api/redirects", **self._auth())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["redirects"]), baseline_total + 2)
        sources = {r["source_path"] for r in data["redirects"]}
        self.assertIn("/test-aaa", sources)
        self.assertIn("/test-ccc", sources)

    def test_list_filters_by_is_active_true(self):
        Redirect.objects.create(source_path="/test-on", target_path="/x", is_active=True)
        Redirect.objects.create(source_path="/test-off", target_path="/y", is_active=False)
        response = self.client.get(
            "/api/redirects?is_active=true",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        sources = [r["source_path"] for r in response.json()["redirects"]]
        self.assertIn("/test-on", sources)
        self.assertNotIn("/test-off", sources)

    def test_list_filters_by_is_active_false(self):
        Redirect.objects.create(source_path="/test-on", target_path="/x", is_active=True)
        Redirect.objects.create(source_path="/test-off", target_path="/y", is_active=False)
        response = self.client.get(
            "/api/redirects?is_active=false",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        sources = [r["source_path"] for r in response.json()["redirects"]]
        self.assertEqual(sources, ["/test-off"])

    def test_list_ignores_unknown_query_params(self):
        baseline_total = Redirect.objects.count()
        Redirect.objects.create(source_path="/test-foo", target_path="/bar", is_active=True)
        response = self.client.get(
            "/api/redirects?unknown=value&another=thing",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["redirects"]), baseline_total + 1)


class RedirectsCreateTest(RedirectsApiBase):
    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_post_creates_redirect_and_clears_cache(self, mock_clear):
        response = self._post({
            "source_path": "/old-page",
            "target_path": "/new-page",
            "redirect_type": 301,
            "is_active": True,
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["source_path"], "/old-page")
        self.assertEqual(body["target_path"], "/new-page")
        self.assertEqual(body["redirect_type"], 301)
        self.assertTrue(body["is_active"])
        self.assertIn("id", body)
        self.assertIsNotNone(body["created_at"])
        self.assertIsNotNone(body["updated_at"])
        self.assertTrue(Redirect.objects.filter(source_path="/old-page").exists())
        mock_clear.assert_called_once()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_post_auto_prepends_leading_slash(self, mock_clear):
        response = self._post({
            "source_path": "missing-slash",
            "target_path": "also-missing",
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["source_path"], "/missing-slash")
        self.assertEqual(body["target_path"], "/also-missing")
        mock_clear.assert_called_once()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_post_uses_defaults_for_redirect_type_and_is_active(self, mock_clear):
        response = self._post({
            "source_path": "/a",
            "target_path": "/b",
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["redirect_type"], 301)
        self.assertTrue(body["is_active"])
        mock_clear.assert_called_once()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_post_conflict_on_duplicate_source(self, mock_clear):
        Redirect.objects.create(source_path="/dup", target_path="/x")
        response = self._post({
            "source_path": "/dup",
            "target_path": "/y",
        })
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "source_path_conflict")
        mock_clear.assert_not_called()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_post_validation_errors(self, mock_clear):
        cases = [
            ({"target_path": "/x"}, "missing_field"),  # missing source_path
            ({"source_path": "/x"}, "missing_field"),  # missing target_path
            (
                {"source_path": 5, "target_path": "/x"},
                "validation_error",
            ),  # non-string
            (
                {"source_path": "/a", "target_path": "/b", "redirect_type": 404},
                "validation_error",
            ),
            (
                {"source_path": "/same", "target_path": "/same"},
                "validation_error",
            ),
            (
                {"source_path": "/a", "target_path": "/b", "is_active": "yes"},
                "validation_error",
            ),
            (
                {"source_path": "", "target_path": "/x"},
                "validation_error",
            ),
        ]
        for payload, expected_code in cases:
            with self.subTest(payload=payload):
                response = self._post(payload)
                self.assertEqual(response.status_code, 422, payload)
                self.assertEqual(response.json()["code"], expected_code)
        mock_clear.assert_not_called()

    def test_post_requires_auth(self):
        response = self.client.post(
            "/api/redirects",
            data=json.dumps({"source_path": "/a", "target_path": "/b"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_post_rejects_invalid_json(self):
        response = self._post({}, raw_body="not json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid JSON"})


class RedirectDetailTest(RedirectsApiBase):
    def test_get_returns_full_serialized_object(self):
        obj = Redirect.objects.create(
            source_path="/x", target_path="/y",
            redirect_type=302, is_active=False,
        )
        response = self.client.get(
            f"/api/redirects/{obj.pk}", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], obj.pk)
        self.assertEqual(body["source_path"], "/x")
        self.assertEqual(body["target_path"], "/y")
        self.assertEqual(body["redirect_type"], 302)
        self.assertFalse(body["is_active"])

    def test_get_unknown_id_returns_404(self):
        response = self.client.get("/api/redirects/9999", **self._auth())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "redirect_not_found")

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_patch_updates_subset_of_fields_and_clears_cache(self, mock_clear):
        obj = Redirect.objects.create(
            source_path="/x", target_path="/y",
            redirect_type=301, is_active=True,
        )
        response = self._patch(obj.pk, {"is_active": False})
        self.assertEqual(response.status_code, 200)
        obj.refresh_from_db()
        self.assertFalse(obj.is_active)
        self.assertEqual(obj.target_path, "/y")  # unchanged
        body = response.json()
        self.assertFalse(body["is_active"])
        mock_clear.assert_called_once()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_patch_can_change_source_path(self, mock_clear):
        obj = Redirect.objects.create(source_path="/old", target_path="/y")
        response = self._patch(obj.pk, {"source_path": "/new"})
        self.assertEqual(response.status_code, 200)
        obj.refresh_from_db()
        self.assertEqual(obj.source_path, "/new")
        mock_clear.assert_called_once()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_patch_rejects_source_path_collision(self, mock_clear):
        Redirect.objects.create(source_path="/taken", target_path="/x")
        obj = Redirect.objects.create(source_path="/free", target_path="/y")
        response = self._patch(obj.pk, {"source_path": "/taken"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "source_path_conflict")
        obj.refresh_from_db()
        self.assertEqual(obj.source_path, "/free")
        mock_clear.assert_not_called()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_patch_rejects_loop(self, mock_clear):
        obj = Redirect.objects.create(source_path="/a", target_path="/b")
        response = self._patch(obj.pk, {"target_path": "/a"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        mock_clear.assert_not_called()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_patch_auto_prepends_slash(self, mock_clear):
        obj = Redirect.objects.create(source_path="/a", target_path="/b")
        response = self._patch(obj.pk, {"target_path": "new-target"})
        self.assertEqual(response.status_code, 200)
        obj.refresh_from_db()
        self.assertEqual(obj.target_path, "/new-target")
        mock_clear.assert_called_once()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_patch_rejects_invalid_redirect_type(self, mock_clear):
        obj = Redirect.objects.create(source_path="/a", target_path="/b")
        response = self._patch(obj.pk, {"redirect_type": 200})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        mock_clear.assert_not_called()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_delete_returns_405_and_keeps_row(self, mock_clear):
        # Issue #864 (2026-06-13): redirect DELETE is blocked via the API.
        # The row survives and the redirect cache is never cleared (no write
        # happened). Use Studio to delete, or PATCH is_active=false.
        obj = Redirect.objects.create(source_path="/a", target_path="/b")
        response = self._delete(obj.pk)
        self.assertEqual(response.status_code, 405)
        body = response.json()
        self.assertEqual(body["code"], "redirect_delete_not_available")
        self.assertIn("Studio", body["error"])
        self.assertTrue(Redirect.objects.filter(pk=obj.pk).exists())
        mock_clear.assert_not_called()

    def test_detail_requires_auth(self):
        obj = Redirect.objects.create(source_path="/a", target_path="/b")
        response = self.client.get(f"/api/redirects/{obj.pk}")
        self.assertEqual(response.status_code, 401)

    def test_unsupported_method_returns_405(self):
        obj = Redirect.objects.create(source_path="/a", target_path="/b")
        response = self.client.post(
            f"/api/redirects/{obj.pk}",
            data="{}",
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 405)


class RedirectsBulkUpsertTest(RedirectsApiBase):
    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_bulk_creates_updates_and_skips(self, mock_clear):
        # Pre-existing rows for update/skip cases.
        Redirect.objects.create(
            source_path="/to-update",
            target_path="/old-target",
            redirect_type=301,
            is_active=True,
        )
        Redirect.objects.create(
            source_path="/to-skip",
            target_path="/skip-target",
            redirect_type=302,
            is_active=False,
        )
        response = self._post(
            {
                "redirects": [
                    {"source_path": "/brand-new", "target_path": "/new"},
                    {"source_path": "/to-update", "target_path": "/new-target"},
                    {
                        "source_path": "/to-skip",
                        "target_path": "/skip-target",
                        "redirect_type": 302,
                        "is_active": False,
                    },
                ],
            },
            url="/api/redirects/bulk",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["updated"], 1)
        self.assertEqual(body["skipped"], 1)
        self.assertEqual(body["warnings"], [])
        actions = {r["source_path"]: r["action"] for r in body["results"]}
        self.assertEqual(actions["/brand-new"], "created")
        self.assertEqual(actions["/to-update"], "updated")
        self.assertEqual(actions["/to-skip"], "skipped")
        for entry in body["results"]:
            self.assertIn("index", entry)
            self.assertIn("id", entry)
        mock_clear.assert_called_once()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_bulk_is_idempotent(self, mock_clear):
        payload = {
            "redirects": [
                {"source_path": "/x", "target_path": "/y"},
                {"source_path": "/p", "target_path": "/q"},
            ],
        }
        first = self._post(payload, url="/api/redirects/bulk").json()
        self.assertEqual(first["created"], 2)

        second = self._post(payload, url="/api/redirects/bulk").json()
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["skipped"], 2)
        # Cache cleared on both calls.
        self.assertEqual(mock_clear.call_count, 2)

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_bulk_is_atomic_on_validation_failure(self, mock_clear):
        response = self._post(
            {
                "redirects": [
                    {"source_path": "/ok1", "target_path": "/x"},
                    {"source_path": "/loop", "target_path": "/loop"},
                    {"source_path": "/ok2", "target_path": "/y"},
                ],
            },
            url="/api/redirects/bulk",
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["index"], 1)
        # Rolled back; none of the rows were saved.
        self.assertFalse(Redirect.objects.filter(source_path="/ok1").exists())
        self.assertFalse(Redirect.objects.filter(source_path="/ok2").exists())
        self.assertFalse(Redirect.objects.filter(source_path="/loop").exists())
        mock_clear.assert_not_called()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_bulk_invalid_redirects_field_returns_error(self, mock_clear):
        response = self._post(
            {"redirects": "not a list"},
            url="/api/redirects/bulk",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")
        mock_clear.assert_not_called()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_bulk_missing_redirects_field_returns_error(self, mock_clear):
        response = self._post({}, url="/api/redirects/bulk")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "missing_field")
        mock_clear.assert_not_called()

    def test_bulk_requires_auth(self):
        response = self.client.post(
            "/api/redirects/bulk",
            data=json.dumps({"redirects": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_bulk_get_returns_405(self, mock_clear):
        response = self.client.get("/api/redirects/bulk", **self._auth())
        self.assertEqual(response.status_code, 405)
        mock_clear.assert_not_called()

    @mock.patch("api.views.redirects.clear_redirect_cache")
    def test_bulk_with_empty_list_returns_zero_counts(self, mock_clear):
        response = self._post({"redirects": []}, url="/api/redirects/bulk")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["created"], 0)
        self.assertEqual(body["updated"], 0)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(body["results"], [])
        self.assertEqual(body["warnings"], [])
        # Even with zero rows, cache is cleared after the commit (cheap; the
        # implementation does not branch on counts).
        mock_clear.assert_called_once()
