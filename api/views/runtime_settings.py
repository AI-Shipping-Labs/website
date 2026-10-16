"""Site-safe read adapters for the package runtime settings API (#1969)."""

from functools import wraps

from community_base.api.auth import bearer_required
from community_base.api.errors import APIError, error_response
from community_base.api.registry import json_response
from community_base.api.safety import parse_pagination
from community_base.config.crypto import decrypt
from community_base.config.models import Setting
from community_base.config.registry import definition, definitions
from community_base.config.service import _fallback, resolved_source
from community_base.kernel.redaction import REDACTED
from django.core.exceptions import ImproperlyConfigured, ValidationError

INVALID_STORED_VALUE = {
    "code": "invalid_stored_value",
    "message": "Stored value is invalid.",
}
_INVALID_VALUE_EXCEPTIONS = (ImproperlyConfigured, ValidationError, ValueError)


def _base_description(item, value, source):
    return {
        "key": item.key,
        "group": item.group,
        "label": item.label,
        "description": item.description,
        "value_type": item.value_type,
        "value": value,
        "configured": source != "default",
        "source": source,
        "secret": item.secret,
        "multiline": item.multiline,
        "optional": item.optional,
        "is_email": item.is_email,
        "docs_url": item.docs_url,
        "requires_restart": item.requires_restart,
    }


def _stored_value(item, row):
    value = row.value
    if item.secret:
        value = decrypt(value)
    return item.coerce(value)


def _safe_description(item, row=None):
    if row is None:
        value = _fallback(item)
        source = resolved_source(item.key)
        if item.secret:
            value = REDACTED
        return _base_description(item, value, source)
    try:
        value = _stored_value(item, row)
    except _INVALID_VALUE_EXCEPTIONS:
        payload = _base_description(item, None, "db")
        payload["error"] = INVALID_STORED_VALUE
        return payload
    if item.secret:
        value = REDACTED
    return _base_description(item, value, "db")


def _setting_rows(items):
    keys = [item.key for item in items]
    return Setting.objects.filter(key__in=keys).in_bulk(field_name="key")


@bearer_required(scopes=("settings.read",))
def _list_settings(request):
    try:
        page = parse_pagination(request)
    except APIError as error:
        return error_response(request, error)
    declared = definitions()
    selected = declared[page.offset : page.offset + page.limit]
    rows = _setting_rows(selected)
    payload = [_safe_description(item, rows.get(item.key)) for item in selected]
    return json_response({
        "settings": payload,
        "pagination": {
            "limit": page.limit,
            "offset": page.offset,
            "total": len(declared),
        },
    })


@bearer_required(scopes=("settings.read",))
def _get_setting(request, key):
    try:
        item = definition(key)
    except ImproperlyConfigured:
        error = APIError(
            404,
            "setting_not_found",
            "Configuration key is not declared.",
            {"key": key},
        )
        return error_response(request, error)
    row = Setting.objects.filter(key=key).first()
    return json_response(_safe_description(item, row))


def settings_list_adapter(package_dispatch):
    """Use the safe list reader for GET and preserve the package dispatcher otherwise."""

    @wraps(package_dispatch)
    def adapter(request, *args, **kwargs):
        if request.method == "GET":
            return _list_settings(request)
        return package_dispatch(request, *args, **kwargs)

    return adapter


def setting_detail_adapter(package_dispatch):
    """Use the safe detail reader for GET and preserve package PUT ownership."""

    @wraps(package_dispatch)
    def adapter(request, *args, **kwargs):
        if request.method == "GET":
            return _get_setting(request, *args, **kwargs)
        return package_dispatch(request, *args, **kwargs)

    return adapter
