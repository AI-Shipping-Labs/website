"""Build the OpenAPI 3.1 document from the ``api/urls.py`` resolver.

Pure function -- input is the URL patterns list, output is a dict
ready to be serialized to JSON. The function is called by the
``generate_openapi`` management command and by the API tests.

How it works:

1. Walk ``api.urls.urlpatterns``. Each ``URLPattern`` carries a
   compiled regex and a callback (the view).
2. Skip the two doc routes (``openapi.json``, ``docs``) -- the spec
   does not document itself.
3. Read the OpenAPI metadata off the callback via the
   ``__openapi_spec__`` attribute that ``@openapi_spec`` left there.
   Routes without metadata are silently skipped (the test suite
   enforces full coverage separately, with a more useful error
   message than this builder could produce).
4. Convert the Django path pattern (``"sprints/<slug:slug>"``) to the
   OpenAPI path-template form (``"/api/sprints/{slug}"``) and infer
   path-parameter schemas from the Django converters
   (``int`` -> ``integer``, ``slug``/``path``/default -> ``string``,
   ``uuid`` -> ``string`` with ``format: uuid``).
5. Build an ``apispec.APISpec`` instance with the ``tokenAuth``
   security scheme declared once at the document level, plus a
   canonical error response component so per-operation error
   declarations can ``$ref`` it.
6. For each route, register the operations via ``spec.path(...)``.
"""

import re

from apispec import APISpec

from api.openapi.decorator import OPENAPI_SPEC_ATTR
from api.openapi.operations import operation_from_method_spec

# Routes that are part of the documentation surface itself. We
# explicitly exclude them so the generated spec describes the API,
# not the docs server in front of it.
_DOCS_ROUTE_NAMES = {"api_openapi_json", "api_docs"}

# Django path-converter name -> OpenAPI schema fragment.
_CONVERTER_TO_SCHEMA = {
    "int": {"type": "integer"},
    "str": {"type": "string"},
    "slug": {"type": "string"},
    "uuid": {"type": "string", "format": "uuid"},
    "path": {"type": "string"},
}

# Matches Django path-converter captures: ``<int:plan_id>`` or
# ``<slug>`` (no converter -> defaults to ``str``).
_PATH_CONVERTER_RE = re.compile(r"<(?:(?P<converter>[^:>]+):)?(?P<name>[^>]+)>")

_DEFAULT_DESCRIPTION = (
    "Operator API for AI Shipping Labs. All endpoints accept "
    "JSON in and return JSON out. Authentication is via the "
    "``Authorization: Token <key>`` header where ``<key>`` is "
    "a token owned by a staff user. Studio shows new or rotated "
    "operator token values once; existing plaintext tokens cannot "
    "be retrieved later.\n\n"
    "Token calls are throttled per token: a rate limit and a cap "
    "on concurrent requests. A throttled call returns ``429`` with "
    "a ``Retry-After`` header (seconds) and ``code`` "
    "``rate_limited`` or ``too_many_concurrent_requests`` before "
    "the endpoint runs, so it is safe to retry after waiting. Send "
    "calls sequentially; do not fan out in parallel.\n\n"
    "The spec endpoint ``/api/openapi.json`` itself accepts "
    "the same ``Authorization: Token <key>`` header, so "
    "OpenAPI tooling (Postman ``Import -> Link``, "
    "``openapi-generator``, Swagger UI) can pull the spec "
    "from the same base URL it then calls. Example:\n\n"
    "```\n"
    "curl -H \"Authorization: Token $API_TOKEN\" "
    "https://aishippinglabs.com/api/openapi.json > spec.json\n"
    "```"
)
_DEFAULT_TOKEN_DESCRIPTION = (
    "Send the header ``Authorization: Token <key>`` where "
    "``<key>`` is a staff-owned token from the Studio "
    "tokens page. New and rotated token values are shown once "
    "in Studio and cannot be retrieved later. The literal scheme "
    "name is ``Token``, not ``Bearer`` (Swagger UI's authorize "
    "dialog renders this as ``bearer`` but the wire format we "
    "accept is ``Token``)."
)
_ERROR_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["error", "code"],
    "properties": {
        "error": {"type": "string", "description": "Human-readable message"},
        "code": {"type": "string", "description": "Machine-readable error code"},
        "details": {
            "type": "object",
            "description": "Optional per-field error details",
            "additionalProperties": True,
        },
    },
}


def _convert_django_path_to_openapi(django_path, *, path_prefix="/api"):
    """Convert a Django path pattern to OpenAPI path-template form.

    Examples::

        sprints/<slug:slug>        -> /api/sprints/{slug}
        plans/<int:plan_id>/weeks  -> /api/plans/{plan_id}/weeks
        ses-events                 -> /api/ses-events
    """
    converted = _PATH_CONVERTER_RE.sub(
        lambda m: "{" + m.group("name") + "}", django_path,
    )
    prefix = path_prefix.rstrip("/")
    return f"{prefix}/" + converted


def _path_parameters(django_path):
    """Extract OpenAPI ``parameters`` entries for path captures.

    Returns a list of OpenAPI parameter objects keyed by capture name.
    The schema is inferred from the Django converter name.
    """
    parameters = []
    for match in _PATH_CONVERTER_RE.finditer(django_path):
        converter = match.group("converter") or "str"
        name = match.group("name")
        schema = _CONVERTER_TO_SCHEMA.get(converter, {"type": "string"})
        parameters.append({
            "name": name,
            "in": "path",
            "required": True,
            "schema": schema,
        })
    return parameters


def _iter_decorated_routes(urlpatterns, *, docs_route_names=None):
    """Yield ``(django_path, callback, name, spec)`` for documented routes.

    Skips:
    - The two doc routes (``api_openapi_json``, ``api_docs``).
    - Routes whose callback has no ``__openapi_spec__`` (the test suite
      enforces full coverage; the builder just renders what it sees).
    - Routes with no ``name`` (defensive -- shouldn't happen for our API).
    """
    docs_route_names = set(docs_route_names or _DOCS_ROUTE_NAMES)
    for pattern in urlpatterns:
        name = getattr(pattern, "name", None)
        if name in docs_route_names:
            continue
        callback = getattr(pattern, "callback", None)
        if callback is None:
            continue
        spec = getattr(callback, OPENAPI_SPEC_ATTR, None)
        if spec is None:
            continue
        # ``pattern.pattern`` is a ``RoutePattern`` whose ``str()`` gives
        # back the original ``"sprints/<slug:slug>"`` template.
        django_path = str(pattern.pattern)
        yield django_path, callback, name, spec


def _new_spec(title, version, description, token_description):
    """Create the document shell and shared components."""
    spec = APISpec(
        title=title,
        version=version,
        openapi_version="3.1.0",
        info={"description": description},
    )
    spec.components.security_scheme(
        "tokenAuth",
        {
            "type": "http",
            "scheme": "bearer",
            "description": token_description,
        },
    )
    spec.components.schema("ErrorResponse", _ERROR_RESPONSE_SCHEMA)
    return spec


def _route_operation(view_spec, method_meta, path_parameters):
    """Build an operation and apply path and view-level metadata."""
    operation = operation_from_method_spec(
        method_meta,
        default_summary=view_spec.get("summary"),
        tag=view_spec["tag"],
    )
    if path_parameters:
        existing_parameters = operation.get("parameters", [])
        operation["parameters"] = list(path_parameters) + existing_parameters
    if "security" not in operation:
        security = view_spec.get("security")
        if security is not None:
            operation["security"] = security
    return operation


def _register_routes(spec, urlpatterns, path_prefix, docs_route_names):
    """Register every decorated route on the APISpec instance."""
    routes = _iter_decorated_routes(
        urlpatterns,
        docs_route_names=docs_route_names,
    )
    for django_path, _callback, _name, view_spec in routes:
        openapi_path = _convert_django_path_to_openapi(
            django_path,
            path_prefix=path_prefix,
        )
        path_parameters = _path_parameters(django_path)
        operations = {}
        for method, method_meta in view_spec["methods"].items():
            operations[method.lower()] = _route_operation(
                view_spec,
                method_meta,
                path_parameters,
            )
        spec.path(path=openapi_path, operations=operations)


def build_spec(
    urlpatterns,
    *,
    title="AI Shipping Labs Operator API",
    version="1.0.0",
    path_prefix="/api",
    docs_route_names=None,
    description=None,
    token_description=None,
):
    """Build and return an OpenAPI 3.1 document as a dict."""
    spec = _new_spec(
        title=title,
        version=version,
        description=description or _DEFAULT_DESCRIPTION,
        token_description=token_description or _DEFAULT_TOKEN_DESCRIPTION,
    )
    _register_routes(
        spec,
        urlpatterns,
        path_prefix,
        docs_route_names,
    )
    document = spec.to_dict()
    document["security"] = [{"tokenAuth": []}]
    return document
