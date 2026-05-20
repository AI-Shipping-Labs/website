"""The ``@openapi_spec(...)`` decorator (issue #722).

Stores OpenAPI metadata on the view function so the build-time spec
generator can read it back. The decorator does NOT wrap the view in a
new function -- it just sets an attribute and returns the view -- so
there is zero runtime cost on every request and no risk of breaking
the existing ``token_required`` / ``csrf_exempt`` / ``require_methods``
decorator chain that sits above us.

Shape -- the decorator takes only keyword arguments:

- ``tag`` (required): grouping label for Swagger UI ("Sprints",
  "Plans", "Events", ...).
- ``summary`` (optional): default summary used by the builder when a
  per-method override is not supplied.
- ``description`` (optional): longer description used at the
  path-item level.
- ``security``: ``None`` means use the document's default (tokenAuth).
  Pass an empty list ``[]`` to explicitly clear security on this
  endpoint (used by the SES webhook, where SNS signature is the auth
  layer and there is no bearer token).
- ``methods`` (required): dict mapping HTTP-method strings (``"GET"``,
  ``"POST"``, ...) to per-operation metadata. Each per-method dict can
  carry ``summary``, ``description``, ``query``, ``path_params``,
  ``request_body``, and ``responses``. See ``api/views/sprints.py``
  for the canonical example.

The shapes inside ``methods`` are intentionally minimal -- they map
1:1 to OpenAPI operation objects but use plain dicts instead of
``apispec``-specific helpers, so a view author can write::

    @openapi_spec(
        tag="Sprints",
        methods={
            "GET": {
                "summary": "List sprints",
                "query": {"status": {"type": "string", "required": False}},
                "responses": {
                    200: {"description": "List of sprints"},
                },
            },
        },
    )

...without learning a new authoring API. The builder translates this
into the corresponding ``parameters`` / ``requestBody`` / ``responses``
nodes when it assembles the document.
"""


OPENAPI_SPEC_ATTR = "__openapi_spec__"


def openapi_spec(
    *,
    tag,
    methods,
    summary=None,
    description=None,
    security=None,
):
    """Attach OpenAPI metadata to a view function.

    See module docstring for the field-by-field shape. The decorator is
    write-only at decoration time and read-only at build time -- it does
    not mutate the view at request time.
    """
    spec = {
        "tag": tag,
        "summary": summary,
        "description": description,
        "methods": methods,
        # ``security`` defaults to ``None`` (use document default).
        # An explicit empty list ``[]`` means "no security required",
        # which the builder serializes as ``security: []`` on the
        # operation -- this is how the SES webhook opts out.
        "security": security,
    }

    def decorator(view_func):
        # Reach through ``functools.wraps`` chains so the attribute
        # lands on the innermost function definition. This makes the
        # metadata visible regardless of which wrapper (``token_required``,
        # ``csrf_exempt``, ``require_methods``) ends up on top.
        target = view_func
        while hasattr(target, "__wrapped__"):
            target = target.__wrapped__
        setattr(target, OPENAPI_SPEC_ATTR, spec)
        # Also set the attribute on the outer wrapper so callers that
        # only see the final decorated callable (e.g. the URL resolver)
        # can read it without unwrapping.
        setattr(view_func, OPENAPI_SPEC_ATTR, spec)
        return view_func

    return decorator
