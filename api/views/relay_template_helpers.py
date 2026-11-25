"""Shared safe Relay template API validation and response shaping."""

from api.safety import error_response
from api.utils import parse_json_body
from email_app.relay_versions import template_version_for
from email_app.services.relay_templates import INTERNAL_TEMPLATE_KEYS, READ_ONLY_TEMPLATE_KEYS

EDITABLE_FIELDS = (
    "name", "description", "subject", "html_body", "text_body",
    "markdown_body", "category", "default_sender_id", "is_active",
    "markdown_layout_context",
)
BOOLEAN_FIELDS = frozenset({"is_active", "markdown_layout_context"})

_TEMPLATE_EXAMPLE = {
    "template": {
        "key": "event_recording_ready",
        "subject": "Recording ready for review: {{ event_title }}",
        "markdown_body": "Hi {{ user_name }}, ...",
    },
    "versions": [{"version": 3}],
    "active_send_version": 2,
}


class RelayTemplateKeyConverter:
    """Relay template keys: slugs plus dots (``coursework.pool_ready``)."""

    regex = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


def _relay_error(error):
    status = 502
    if error.code == "relay_http_404":
        status = 404
    return error_response(
        f"Relay template operation failed ({error.code})",
        "relay_error",
        status=status,
        details={"relay_code": error.code},
    )


def _forbidden_key(template_key, *, allow_read_only=False):
    forbidden = set(INTERNAL_TEMPLATE_KEYS)
    if not allow_read_only:
        forbidden |= READ_ONLY_TEMPLATE_KEYS
    if template_key in forbidden:
        return error_response(
            "This Relay template cannot be managed through the API",
            "template_not_manageable",
            status=404,
        )
    return None


def _template_payload(template_key, template, versions):
    return {
        "template": template,
        "versions": list(versions),
        "active_send_version": template_version_for(template_key),
    }


def _json_object(request):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return None, parse_error
    if not isinstance(data, dict):
        return None, error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )
    return data, None



def _validated_updates(request):
    updates, error = _json_object(request)
    if error is not None:
        return None, error
    for field, value in updates.items():
        if field not in EDITABLE_FIELDS:
            return None, error_response(f"Unknown field: {field}", "unknown_field", status=422, details={"field": field})
        expected = str
        label = "string"
        if field in BOOLEAN_FIELDS:
            expected = bool
            label = "boolean"
        if not isinstance(value, expected):
            return None, error_response(f"{field} must be a {label}", "invalid_type", status=422, details={"field": field})
    return updates, None
