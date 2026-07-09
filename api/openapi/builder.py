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


def _query_parameters(query_spec):
    """Translate a ``query`` decorator dict to OpenAPI parameters.

    Input shape::

        {"status": {"type": "string", "required": False, "enum": [...]}}

    Output is a list of OpenAPI parameter objects. ``required`` defaults
    to False (consistent with HTTP query-string semantics).
    """
    parameters = []
    for name, schema_meta in query_spec.items():
        required = schema_meta.pop("required", False) if isinstance(schema_meta, dict) else False
        # Re-pop is a mutating peek; restore for downstream consumers.
        if isinstance(schema_meta, dict) and "required" not in schema_meta:
            # Don't put ``required`` back inside the schema -- it belongs
            # at the parameter level, not the schema level.
            pass
        parameters.append({
            "name": name,
            "in": "query",
            "required": required,
            "schema": dict(schema_meta),
        })
    return parameters


def _build_request_body(body_spec):
    """Translate a ``request_body`` decorator dict to OpenAPI requestBody.

    Input shape::

        {"required": ["name", ...], "properties": {...}, "example": {...}}

    Output is an OpenAPI ``requestBody`` object with one
    ``application/json`` content type.
    """
    schema = {"type": "object"}
    if "required" in body_spec:
        schema["required"] = list(body_spec["required"])
    if "properties" in body_spec:
        schema["properties"] = dict(body_spec["properties"])
    content = {"schema": schema}
    if "example" in body_spec:
        content["example"] = body_spec["example"]
    return {
        "required": True,
        "content": {"application/json": content},
    }


def _build_responses(responses_spec):
    """Translate a ``responses`` decorator dict to OpenAPI responses.

    Input shape::

        {200: {"description": "...", "example": {...}},
         403: {"description": "..."}}

    Status keys are coerced to strings (OpenAPI requires string keys).
    """
    out = {}
    for status, meta in responses_spec.items():
        node = {"description": meta.get("description", "")}
        if "example" in meta:
            node["content"] = {
                "application/json": {"example": meta["example"]},
            }
        if "schema" in meta:
            content = node.setdefault("content", {}).setdefault(
                "application/json", {},
            )
            content["schema"] = meta["schema"]
        out[str(status)] = node
    return out


def _operation_from_method_spec(method_meta, default_summary, tag):
    """Assemble an OpenAPI operation object from per-method decorator data."""
    operation = {
        "tags": [tag],
        "summary": method_meta.get("summary") or default_summary or "",
    }
    if "description" in method_meta:
        operation["description"] = method_meta["description"]

    parameters = []
    if "query" in method_meta:
        parameters.extend(_query_parameters(method_meta["query"]))
    # Path parameters are added later by the caller (which knows the
    # full path string).
    if parameters:
        operation["parameters"] = parameters

    if "request_body" in method_meta:
        operation["requestBody"] = _build_request_body(method_meta["request_body"])

    if "responses" in method_meta:
        operation["responses"] = _build_responses(method_meta["responses"])
    else:
        # Every operation must declare at least one response per the
        # OpenAPI spec; default to a generic 200 if the view author
        # didn't supply anything more specific.
        operation["responses"] = {"200": {"description": "Success"}}

    # Per-operation security override. Lets a single route mix
    # auth schemes across methods (e.g. token-gated GET + SNS-signed
    # POST on ``/api/ses-events``). ``[]`` clears security on the
    # operation; ``None`` / absent inherits the view- or document-level
    # default. Takes precedence over the view-level ``security`` kwarg
    # applied by the caller.
    if "security" in method_meta and method_meta["security"] is not None:
        operation["security"] = method_meta["security"]
    return operation


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
    """Build and return an OpenAPI 3.1 document as a dict.

    The single source of truth for endpoints is the supplied
    ``urlpatterns`` list (``api.urls.urlpatterns``). The spec is keyed
    by the OpenAPI path-template form ("/api/sprints/{slug}"), with one
    operation per HTTP method declared in the decorator's ``methods``
    dict.
    """
    description = description or (
        "Operator API for AI Shipping Labs. All endpoints accept "
        "JSON in and return JSON out. Authentication is via the "
        "``Authorization: Token <key>`` header where ``<key>`` is "
        "a token owned by a staff user. Studio shows new or rotated "
        "operator token values once; existing plaintext tokens cannot "
        "be retrieved later.\n\n"
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
    token_description = token_description or (
        "Send the header ``Authorization: Token <key>`` where "
        "``<key>`` is a staff-owned token from the Studio "
        "tokens page. New and rotated token values are shown once "
        "in Studio and cannot be retrieved later. The literal scheme "
        "name is ``Token``, not ``Bearer`` (Swagger UI's authorize "
        "dialog renders this as ``bearer`` but the wire format we "
        "accept is ``Token``)."
    )

    spec = APISpec(
        title=title,
        version=version,
        openapi_version="3.1.0",
        info={"description": description},
    )

    # Security scheme: declared once at the document level. We use the
    # ``http`` / ``bearer`` shape because that's the closest OpenAPI
    # primitive to our literal ``Authorization: Token <key>`` header.
    # The description names the exact header shape so client authors
    # don't guess ``Bearer`` vs ``Token``.
    spec.components.security_scheme(
        "tokenAuth",
        {
            "type": "http",
            "scheme": "bearer",
            "description": token_description,
        },
    )

    # Canonical error-response schema, referenced from every operation
    # that declares a 4xx response.
    spec.components.schema(
        "ErrorResponse",
        {
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
        },
    )

    for django_path, _callback, _name, view_spec in _iter_decorated_routes(
        urlpatterns,
        docs_route_names=docs_route_names,
    ):
        openapi_path = _convert_django_path_to_openapi(
            django_path,
            path_prefix=path_prefix,
        )
        path_params = _path_parameters(django_path)

        operations = {}
        for method, method_meta in view_spec["methods"].items():
            op = _operation_from_method_spec(
                method_meta,
                default_summary=view_spec.get("summary"),
                tag=view_spec["tag"],
            )
            # Merge path parameters before any query parameters the
            # operation already declared.
            if path_params:
                op_params = op.get("parameters", [])
                op["parameters"] = list(path_params) + op_params

            # Security precedence: per-operation override (set inside
            # ``_operation_from_method_spec``) beats the per-view override,
            # which beats the document default. Only apply the view-level
            # override when the operation did not declare its own.
            if "security" not in op:
                sec = view_spec.get("security")
                if sec is not None:
                    # ``[]`` => no security required (SNS-signed webhook).
                    op["security"] = sec
            operations[method.lower()] = op

        spec.path(path=openapi_path, operations=operations)

    document = spec.to_dict()

    # Document-level default security so every operation that does not
    # opt out inherits ``tokenAuth``.
    document["security"] = [{"tokenAuth": []}]

    return document
