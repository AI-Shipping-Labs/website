"""Staff API over the Relay template catalog (intake 1f596393).

The same operations as the Studio template editor
(``studio.views.email_templates``), for operators and agents without a
browser session:

- ``GET  /api/email-templates/<key>``          -- current draft + versions.
- ``PUT  /api/email-templates/<key>``          -- save a draft (nothing goes live).
- ``POST /api/email-templates/<key>/publish``  -- publish the saved draft.
- ``POST /api/email-templates/<key>/preview``  -- render draft or a version.

Relay campaigns without an explicit version render the latest published
version, so publishing changes what native campaigns (for example the host
``event_recording_ready`` notice) send next. Website transactional sends
stay pinned by ``email_app.relay_versions``. Errors carry only the safe
Relay error code, never template or recipient content.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from api.views.relay_template_helpers import (
    _TEMPLATE_EXAMPLE,
    _forbidden_key,
    _json_object,
    _relay_error,
    _template_payload,
    _validated_updates,
)
from api.views.relay_template_helpers import (
    RelayTemplateKeyConverter as RelayTemplateKeyConverter,
)
from email_app.relay_versions import template_version_for
from email_app.services.preview_contexts import RECIPIENT_NAMED, get_preview_context
from email_app.services.relay_templates import (
    RelayTemplateError,
    configured_template_client,
)


@token_required
@csrf_exempt
@require_methods("GET", "PUT")
@openapi_spec(
    tag="Email templates",
    summary="Read or save a Relay template draft",
    methods={
        "GET": {
            "summary": "Read a Relay template draft and its published versions",
            "description": (
                "Returns the Relay draft, its immutable published versions, "
                "and the version website transactional sends are pinned to."
            ),
            "responses": {
                200: {"description": "Template detail.", "example": _TEMPLATE_EXAMPLE},
                404: {
                    "description": "Unknown or unmanageable template.",
                    "example": {"error": "Relay template operation failed (relay_http_404)", "code": "relay_error"},
                },
                502: {
                    "description": "Relay unavailable or misconfigured.",
                    "example": {"error": "Relay template operation failed (relay_unavailable)", "code": "relay_error"},
                },
            },
        },
        "PUT": {
            "summary": "Save a Relay template draft (nothing goes live)",
            "description": (
                "Merges the given fields into the current draft and saves it. "
                "Publishing is a separate call. Unknown fields return 422."
            ),
            "request_body": {
                "properties": {
                    "subject": {"type": "string"},
                    "markdown_body": {"type": "string"},
                    "html_body": {"type": "string"},
                    "text_body": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "category": {"type": "string"},
                    "default_sender_id": {"type": "string"},
                    "is_active": {"type": "boolean"},
                    "markdown_layout_context": {"type": "boolean"},
                },
                "example": {"markdown_body": "Hi {{ user_name }}, ..."},
            },
            "responses": {
                200: {"description": "Saved draft and versions.", "example": _TEMPLATE_EXAMPLE},
                422: {
                    "description": "Unknown or mistyped field.",
                    "example": {"error": "Unknown field: key", "code": "unknown_field"},
                },
                502: {
                    "description": "Relay rejected the draft.",
                    "example": {"error": "Relay template operation failed (relay_http_400)", "code": "relay_error"},
                },
            },
        },
    },
)
def email_template_detail(request, template_key):
    """``GET``/``PUT /api/email-templates/<key>``."""
    forbidden = _forbidden_key(template_key, allow_read_only=request.method == "GET")
    if forbidden is not None:
        return forbidden

    updates = None
    if request.method == "PUT":
        updates, error = _validated_updates(request)
        if error is not None:
            return error
    try:
        client = configured_template_client()
        template = client.template(template_key)
        if updates is not None:
            draft = dict(template)
            draft.update(updates)
            template = client.save_draft(template_key, draft)
        versions = client.versions(template_key)
    except RelayTemplateError as error:
        return _relay_error(error)
    return JsonResponse(_template_payload(template_key, template, versions))


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Email templates",
    summary="Publish a Relay template draft",
    methods={
        "POST": {
            "summary": "Publish the saved draft as a new immutable version",
            "description": (
                "Relay campaigns without an explicit version (native event "
                "notices) render the newest published version from now on. "
                "Website transactional sends stay pinned to "
                "``active_send_version`` until a release changes the pin."
            ),
            "responses": {
                200: {
                    "description": "Published version number.",
                    "example": {"template_key": "event_recording_ready", "version": 3, "active_send_version": 2},
                },
                502: {
                    "description": "Relay rejected the publish.",
                    "example": {"error": "Relay template operation failed (relay_http_409)", "code": "relay_error"},
                },
            },
        },
    },
)
def email_template_publish(request, template_key):
    """``POST /api/email-templates/<key>/publish``."""
    forbidden = _forbidden_key(template_key)
    if forbidden is not None:
        return forbidden
    try:
        version = configured_template_client().publish(template_key)
    except RelayTemplateError as error:
        return _relay_error(error)
    return JsonResponse({
        "template_key": template_key,
        "version": version,
        "active_send_version": template_version_for(template_key),
    })


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Email templates",
    summary="Preview a Relay template",
    methods={
        "POST": {
            "summary": "Render the draft or a published version",
            "description": (
                "Renders through Relay with the Studio sample context for the "
                "template, overlaid with ``context``. ``version`` renders that "
                "published version; without it the saved draft is rendered. "
                "Nothing is sent."
            ),
            "request_body": {
                "properties": {
                    "version": {"type": "integer"},
                    "context": {"type": "object"},
                },
                "example": {"version": 3, "context": {"publish_state": "Uploaded and currently published"}},
            },
            "responses": {
                200: {
                    "description": "Rendered subject and bodies.",
                    "example": {"subject": "Recording ready for review: Sample", "text_body": "...", "html_body": "..."},
                },
                422: {
                    "description": "Bad version or context.",
                    "example": {"error": "version must be a positive integer", "code": "invalid_type"},
                },
            },
        },
    },
)
def email_template_preview(request, template_key):
    """``POST /api/email-templates/<key>/preview``."""
    forbidden = _forbidden_key(template_key, allow_read_only=True)
    if forbidden is not None:
        return forbidden
    context, version, error = _preview_input(request, template_key)
    if error is not None:
        return error
    try:
        rendered = configured_template_client().preview(
            template_key, context, version=version, draft=version is None,
        )
    except RelayTemplateError as error:
        return _relay_error(error)
    return JsonResponse({
        "subject": rendered["subject"],
        "text_body": rendered["text_body"],
        "html_body": rendered["html_body"],
    })


def _preview_input(request, template_key):
    data, error = _json_object(request)
    if error is not None:
        return None, None, error
    version = data.get("version")
    if version is not None and (
        not isinstance(version, int) or isinstance(version, bool) or version < 1
    ):
        return None, None, error_response(
            "version must be a positive integer", "invalid_type", status=422,
            details={"field": "version"},
        )
    overrides = data.get("context") or {}
    if not isinstance(overrides, dict):
        return None, None, error_response(
            "context must be an object", "invalid_type", status=422,
            details={"field": "context"},
        )
    context = get_preview_context(template_key, recipient=RECIPIENT_NAMED)
    context.setdefault("user_name", "Ada")
    context.setdefault("user_email", "ada@example.com")
    context.update(overrides)
    return context, version, None
