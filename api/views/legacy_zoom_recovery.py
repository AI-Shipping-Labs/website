"""Fixed-key secret recovery action for the retained legacy Zoom configuration."""

from community_base.api.auth import bearer_required
from community_base.api.errors import APIError, error_response, method_not_allowed
from community_base.api.registry import json_response
from community_base.api.safety import read_json_object
from community_base.config.models import Setting
from django.core.exceptions import ImproperlyConfigured

from api.openapi import openapi_spec
from integrations.services.legacy_zoom_recovery import recover_legacy_zoom_secret


@bearer_required(scopes=("settings.write",))
@openapi_spec(
    tag="Runtime settings",
    summary="Recover the retained legacy Zoom secret",
    methods={"POST": {
        "description": (
            "Versioned settings action requiring Bearer settings.write. Accepts "
            "only a nonempty value string for the existing fixed legacy key. "
            "Returns only recovered/decryptable booleans; never enables consumers."
        ),
        "responses": {200: {"description": "Recovery and decryption verified."}},
    }},
)
def recover_legacy_zoom(request):
    if request.method != "POST":
        return error_response(request, method_not_allowed())
    try:
        payload = read_json_object(request, max_bytes=8192)
        if set(payload) != {"value"}:
            raise APIError(422, "invalid_recovery", "A secret value is required.")
        result = recover_legacy_zoom_secret(
            payload["value"], actor_ref=f"api-key:{request.api_key.pk}",
        )
    except APIError as error:
        return error_response(request, error)
    except Setting.DoesNotExist:
        return error_response(request, APIError(
            404, "setting_not_found", "The legacy setting does not exist.",
        ))
    except (ValueError, ImproperlyConfigured):
        return error_response(request, APIError(
            422, "recovery_failed", "Secret recovery could not be verified.",
        ))
    return json_response(result)
