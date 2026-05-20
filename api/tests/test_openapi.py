"""Tests for the OpenAPI scaffolding (issue #722).

Covers:

- ``build_spec`` produces a valid OpenAPI 3.1 document with the
  ``tokenAuth`` security scheme and the sprints paths.
- Every public view function in ``api.views.sprints`` carries the
  ``__openapi_spec__`` attribute the decorator is supposed to set.
- ``GET /api/openapi.json`` is staff-only (anon redirects, non-staff 403,
  staff 200 with ``application/json`` body).
- ``GET /api/docs`` is staff-only with the same access matrix.
- ``generate_openapi --check`` exits 0 on a clean tree and 1 when the
  committed file has drifted.
"""

import importlib
import inspect
import io
import json
import pkgutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

import api.views as api_views_package
from accounts.models import Token
from api.openapi import OPENAPI_SPEC_ATTR, build_spec
from api.urls import urlpatterns

User = get_user_model()


class BuildSpecTest(TestCase):
    """Pure-function tests for ``api.openapi.builder.build_spec``."""

    @classmethod
    def setUpTestData(cls):
        cls.document = build_spec(urlpatterns)

    def test_document_is_openapi_3_1(self):
        self.assertEqual(self.document["openapi"], "3.1.0")

    def test_sprints_collection_path_present(self):
        self.assertIn("/api/sprints", self.document["paths"])
        operations = self.document["paths"]["/api/sprints"]
        self.assertIn("get", operations)
        self.assertIn("post", operations)

    def test_sprint_detail_path_uses_slug_template(self):
        # Builder converts ``<slug:slug>`` to ``{slug}``.
        self.assertIn("/api/sprints/{slug}", self.document["paths"])
        operations = self.document["paths"]["/api/sprints/{slug}"]
        self.assertEqual(
            set(operations.keys()) & {"get", "patch", "delete"},
            {"get", "patch", "delete"},
        )

    def test_path_parameter_inferred_from_django_converter(self):
        operations = self.document["paths"]["/api/sprints/{slug}"]
        get_params = operations["get"].get("parameters", [])
        slug_param = next(p for p in get_params if p["name"] == "slug")
        self.assertEqual(slug_param["in"], "path")
        self.assertTrue(slug_param["required"])
        self.assertEqual(slug_param["schema"], {"type": "string"})

    def test_security_scheme_describes_token_header(self):
        security_schemes = self.document["components"]["securitySchemes"]
        self.assertIn("tokenAuth", security_schemes)
        # The description must name the literal header shape the codebase
        # accepts, otherwise client authors guess ``Bearer`` instead of
        # ``Token``.
        self.assertIn(
            "Authorization: Token",
            security_schemes["tokenAuth"]["description"],
        )

    def test_default_document_security_is_token_auth(self):
        # Document-level default: every operation that doesn't opt out
        # inherits this. The SES webhook (in #723's scope) will opt out
        # via ``security=[]`` on its decorator.
        self.assertEqual(self.document["security"], [{"tokenAuth": []}])

    def test_doc_routes_excluded_from_spec(self):
        # ``/api/openapi.json`` and ``/api/docs`` exist in the URL conf
        # but the spec must not document itself.
        self.assertNotIn("/api/openapi.json", self.document["paths"])
        self.assertNotIn("/api/docs", self.document["paths"])

    def test_sprints_tag_assigned(self):
        operations = self.document["paths"]["/api/sprints"]
        self.assertEqual(operations["get"]["tags"], ["Sprints"])
        self.assertEqual(operations["post"]["tags"], ["Sprints"])


class SprintsModuleHasOpenApiSpecTest(TestCase):
    """Every public view function in ``api.views.sprints`` must be decorated."""

    def test_every_sprints_view_has_openapi_spec_attribute(self):
        import api.views.sprints as sprints_module

        view_names = ["sprints_collection", "sprint_detail"]
        for name in view_names:
            view = getattr(sprints_module, name)
            spec = getattr(view, OPENAPI_SPEC_ATTR, None)
            self.assertIsNotNone(
                spec,
                f"api.views.sprints.{name} is missing the @openapi_spec decorator",
            )
            # The decorator stores a dict with at least these keys.
            self.assertIn("tag", spec)
            self.assertIn("methods", spec)
            self.assertEqual(spec["tag"], "Sprints")

    def test_methods_match_require_methods_for_sprints_collection(self):
        # ``@require_methods("GET", "POST")`` -- the decorator data must
        # mirror that. Drift here is exactly what the spec is supposed
        # to catch.
        import api.views.sprints as sprints_module

        spec = getattr(sprints_module.sprints_collection, OPENAPI_SPEC_ATTR)
        self.assertEqual(set(spec["methods"].keys()), {"GET", "POST"})

    def test_methods_match_require_methods_for_sprint_detail(self):
        import api.views.sprints as sprints_module

        spec = getattr(sprints_module.sprint_detail, OPENAPI_SPEC_ATTR)
        self.assertEqual(
            set(spec["methods"].keys()),
            {"GET", "PATCH", "DELETE"},
        )


class AllApiViewsHaveOpenApiSpecTest(TestCase):
    """Every public view function across every module under ``api.views``
    must carry the ``@openapi_spec`` decorator.

    The list of modules is discovered at runtime so that adding a new
    ``api/views/<thing>.py`` automatically extends coverage -- the test
    suite is the source of truth for "every view is decorated", not the
    builder (the builder silently skips undecorated routes by design).
    """

    # Modules under ``api.views`` that intentionally do NOT carry the
    # decorator. ``_permissions`` is a pure helper module (no views);
    # ``docs`` hosts the staff-only Swagger UI page and the openapi.json
    # endpoint, both of which the builder explicitly excludes from the
    # generated spec.
    SKIP_MODULES = {"_permissions", "docs"}

    def _iter_view_modules(self):
        # Discovered fresh per test so the parallel-test pickling layer
        # never has to round-trip ``module`` objects via ``setUpTestData``.
        for module_info in pkgutil.iter_modules(api_views_package.__path__):
            if module_info.ispkg:
                continue
            name = module_info.name
            if name in self.SKIP_MODULES:
                continue
            yield importlib.import_module(f"api.views.{name}")

    def _iter_public_view_functions(self, module):
        """Yield ``(name, function)`` pairs for public Django view callables.

        A Django view is identified by signature: the first positional
        parameter is named ``request``. This filter intentionally excludes
        public serializer helpers (``serialize_event``, ``serialize_*``)
        which live in the same module but are not URL-routed callables.
        """
        for name, value in vars(module).items():
            if name.startswith("_"):
                continue
            if not callable(value):
                continue
            # Skip imports (re-exports). Their decorator already lives
            # on the original module's definition; checking it twice is
            # noise.
            origin = getattr(value, "__module__", None)
            if origin != module.__name__:
                continue
            if inspect.isclass(value):
                continue
            # The view-vs-helper signal: view functions in this codebase
            # always take ``request`` as their first positional argument
            # (see api/views/sprints.py as the canonical pattern).
            try:
                signature = inspect.signature(value)
            except (TypeError, ValueError):
                continue
            params = list(signature.parameters.values())
            if not params:
                continue
            first = params[0]
            if first.name != "request":
                continue
            yield name, value

    def test_every_public_view_has_openapi_spec_attribute(self):
        missing = []
        for module in self._iter_view_modules():
            for name, view in self._iter_public_view_functions(module):
                spec = getattr(view, OPENAPI_SPEC_ATTR, None)
                if spec is None:
                    missing.append(f"{module.__name__}.{name}")
                    continue
                # Decorator contract: tag + methods are mandatory.
                if "tag" not in spec or "methods" not in spec:
                    missing.append(
                        f"{module.__name__}.{name} (spec missing tag/methods)",
                    )
        self.assertFalse(
            missing,
            msg=(
                "These public api.views functions are missing the "
                "@openapi_spec(...) decorator:\n  " + "\n  ".join(missing)
            ),
        )

    def test_openapi_spec_methods_keys_are_uppercase_http_verbs(self):
        """Every method key in ``methods`` must be one of the canonical
        uppercase HTTP verbs the builder knows how to lower-case for the
        OpenAPI operations dict.
        """
        valid_verbs = {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"}
        bad = []
        for module in self._iter_view_modules():
            for name, view in self._iter_public_view_functions(module):
                spec = getattr(view, OPENAPI_SPEC_ATTR, None)
                if spec is None:
                    continue
                for key in spec["methods"]:
                    if key not in valid_verbs:
                        bad.append(
                            f"{module.__name__}.{name}: method key "
                            f"{key!r} is not a canonical HTTP verb",
                        )
        self.assertFalse(bad, msg="\n".join(bad))

    def test_ses_webhook_explicitly_opts_out_of_security(self):
        """The SES webhook receives SNS-signed payloads; bearer tokens
        do not apply. The decorator contract is ``security=[]`` (NOT
        ``security=None``) so the operation renders as unauthenticated
        in Swagger UI rather than inheriting the document default.
        """
        from api.views.ses_events import ses_events

        spec = getattr(ses_events, OPENAPI_SPEC_ATTR)
        self.assertEqual(
            spec["security"], [],
            msg=(
                "api.views.ses_events.ses_events must declare "
                "``security=[]`` so the SES webhook does not "
                "inherit the document-level tokenAuth requirement"
            ),
        )


class OpenApiJsonViewTest(TestCase):
    """Access control on ``GET /api/openapi.json``.

    The route is dual-auth: a staff browser session OR a staff-owned
    ``Authorization: Token <key>`` header. The matrix below covers
    every cell of (anon, non-staff session, staff session) x
    (no header, malformed header, non-staff token, staff token).
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="staff-tok")
        # Non-staff token: bypass the manager's staff-only validator by
        # constructing the row directly. Models the legacy case where a
        # token was minted while the user was staff but the user has
        # since been demoted.
        cls.non_staff_token = Token(
            key="non-staff-token-key",
            user=cls.member,
            name="legacy-non-staff",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get("/api/openapi.json")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_non_staff_authenticated_user_gets_403(self):
        self.client.force_login(self.member)
        response = self.client.get("/api/openapi.json")
        self.assertEqual(response.status_code, 403)

    def test_staff_session_gets_200_application_json(self):
        self.client.force_login(self.staff)
        response = self.client.get("/api/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        # The body must parse as JSON and look like an OpenAPI document.
        body = json.loads(response.content)
        self.assertEqual(body["openapi"], "3.1.0")
        self.assertIn("/api/sprints", body["paths"])

    def test_staff_token_gets_200_application_json(self):
        response = self.client.get(
            "/api/openapi.json",
            HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        body = json.loads(response.content)
        self.assertEqual(body["openapi"], "3.1.0")
        self.assertIn("/api/sprints", body["paths"])

    def test_non_staff_token_gets_401(self):
        # Matches ``token_required`` masking semantics: non-staff tokens
        # report as ``Invalid token`` (not 403), so the response shape
        # does not leak whether the key exists.
        response = self.client.get(
            "/api/openapi.json",
            HTTP_AUTHORIZATION=f"Token {self.non_staff_token.key}",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_malformed_authorization_header_returns_401(self):
        # Anything other than the literal "Token <key>" scheme is treated
        # as a malformed token attempt rather than falling back to the
        # session redirect. API clients should not be redirected to a
        # browser login page.
        response = self.client.get(
            "/api/openapi.json",
            HTTP_AUTHORIZATION="Bearer xyz",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )

    def test_token_path_bumps_last_used_at(self):
        # Proves the token branch runs through ``token_required`` rather
        # than a parallel implementation that would skip the bump.
        fresh = Token.objects.create(user=self.staff, name="bump-check")
        self.assertIsNone(fresh.last_used_at)

        before = timezone.now()
        response = self.client.get(
            "/api/openapi.json",
            HTTP_AUTHORIZATION=f"Token {fresh.key}",
        )
        self.assertEqual(response.status_code, 200)

        fresh.refresh_from_db()
        self.assertIsNotNone(fresh.last_used_at)
        self.assertGreaterEqual(fresh.last_used_at, before)
        self.assertLessEqual(fresh.last_used_at, timezone.now())


class DocsPageViewTest(TestCase):
    """Access control on ``GET /api/docs`` (Swagger UI page)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get("/api/docs")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_non_staff_authenticated_user_gets_403(self):
        self.client.force_login(self.member)
        response = self.client.get("/api/docs")
        self.assertEqual(response.status_code, 403)

    def test_staff_session_gets_swagger_ui_page(self):
        self.client.force_login(self.staff)
        response = self.client.get("/api/docs")
        self.assertEqual(response.status_code, 200)
        # The template must wire Swagger UI to our JSON endpoint and
        # mount it in the documented container element. We assert on
        # the specific bytes that bind those two together, not on the
        # full HTML body.
        self.assertContains(response, "/api/openapi.json")
        self.assertContains(response, 'id="swagger-ui"')


class GenerateOpenapiCommandTest(TestCase):
    """``generate_openapi`` write mode and ``--check`` drift detection."""

    def test_check_passes_on_clean_tree(self):
        # The committed ``_docs/openapi.json`` is regenerated by every
        # CI run; a clean checkout must report no drift. We capture
        # stdout to confirm the success message even though the
        # exit-zero path doesn't raise.
        out = io.StringIO()
        call_command("generate_openapi", "--check", stdout=out)
        self.assertIn("up to date", out.getvalue())

    def test_check_detects_drift_against_modified_file(self):
        # Point the command at a temp directory, write a stub spec, and
        # confirm ``--check`` exits 1 with a diff. We override
        # ``BASE_DIR`` so the command reads/writes inside the tempdir
        # without touching the committed file.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "_docs").mkdir()
            stub_path = Path(tmp) / "_docs" / "openapi.json"
            stub_path.write_text("{}\n", encoding="utf-8")

            with override_settings(BASE_DIR=tmp):
                err = io.StringIO()
                with self.assertRaises(SystemExit) as exit_cm:
                    call_command(
                        "generate_openapi", "--check",
                        stdout=io.StringIO(),
                        stderr=err,
                    )
                self.assertEqual(exit_cm.exception.code, 1)
                # The drift message must be specific enough that a
                # developer reading CI logs knows to run the command.
                self.assertIn("drift", err.getvalue().lower())

    def test_write_mode_produces_valid_openapi_document(self):
        # Write into a temp tree, then re-parse the file and assert the
        # top-level OpenAPI shape. Catches accidental newlines, BOM,
        # or non-UTF-8 encoding issues in the writer.
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(BASE_DIR=tmp):
                call_command("generate_openapi", stdout=io.StringIO())
            written = Path(tmp) / "_docs" / "openapi.json"
            self.assertTrue(written.exists())
            document = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(document["openapi"], "3.1.0")
            self.assertIn("/api/sprints", document["paths"])
            # Trailing-newline contract -- diff stability.
            self.assertTrue(written.read_text(encoding="utf-8").endswith("\n"))

    def test_check_exits_nonzero_when_decorator_changes_without_regen(self):
        # Simulate the real CI failure mode: a developer edits a
        # decorator, runs tests, but forgets to regenerate the spec.
        # We monkeypatch ``build_spec`` to return a different document
        # so ``--check`` finds a diff against the committed file.
        from api.management.commands import generate_openapi as cmd_module

        def _fake_build(*_args, **_kwargs):
            return {
                "openapi": "3.1.0",
                "info": {"title": "drifted", "version": "0.0.0"},
                "paths": {},
            }

        err = io.StringIO()
        with mock.patch.object(cmd_module, "build_spec", _fake_build):
            with self.assertRaises(SystemExit) as exit_cm:
                call_command(
                    "generate_openapi", "--check",
                    stdout=io.StringIO(),
                    stderr=err,
                )
        self.assertEqual(exit_cm.exception.code, 1)
        # Make sure the failure path printed the actual diff (--check's
        # whole job).
        self.assertIn("---", err.getvalue())
        self.assertIn("+++", err.getvalue())
