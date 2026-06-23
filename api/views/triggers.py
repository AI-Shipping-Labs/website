"""Authenticated operator API for the event-hooks subsystem (issue #1070).

Staff-token-gated (via ``token_required``) management + observability:

- ``GET/POST /api/triggers/subscriptions`` and
  ``GET/PATCH /api/triggers/subscriptions/<id>``
- ``GET/POST /api/triggers/widgets`` and ``GET/PATCH /api/triggers/widgets/<id>``
- ``GET /api/triggers/emissions`` (read-only, filterable)
- ``GET /api/triggers/deliveries`` (read-only, filterable)

DELETE is intentionally NOT exposed: deactivate via ``is_active`` instead,
so the no-deletes-via-API policy guard stays green. Subscription/widget
secrets are accepted on write but NEVER returned in responses.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import (
    body_must_be_object_response,
    coerce_optional_text,
    parse_bool_query,
    parse_json_body,
    require_methods,
    validation_response,
)
from triggers.models import (
    EVENT_TYPE_CUSTOM,
    EventEmission,
    EventWidget,
    TriggerSubscription,
    WebhookDelivery,
)

# ---------------------------------------------------------------------------
# Serialization (secrets NEVER returned)
# ---------------------------------------------------------------------------


def _iso(value):
    return value.isoformat() if value is not None else None


def _serialize_subscription(sub):
    return {
        "id": sub.pk,
        "event_type": sub.event_type,
        "property_filter": sub.property_filter or {},
        "target_url": sub.target_url,
        # ``has_secret`` lets a client confirm a secret is set without ever
        # seeing the value; the raw secret is intentionally omitted.
        "has_secret": bool(sub.secret),
        "is_active": sub.is_active,
        "description": sub.description,
        "created_at": _iso(sub.created_at),
        "updated_at": _iso(sub.updated_at),
    }


def _serialize_widget(widget):
    return {
        "id": widget.pk,
        "slug": widget.slug,
        "event_name": widget.event_name,
        "min_level": widget.min_level,
        "claim_label": widget.claim_label,
        "claim_body": widget.claim_body,
        "signin_cta": widget.signin_cta,
        "claimed_label": widget.claimed_label,
        "exhausted_label": widget.exhausted_label,
        "is_active": widget.is_active,
        "created_at": _iso(widget.created_at),
        "updated_at": _iso(widget.updated_at),
    }


def _serialize_emission(em):
    return {
        "id": em.pk,
        "user_id": em.user_id,
        "event_name": em.event_name,
        "properties": em.properties or {},
        "envelope_id": em.envelope_id,
        "created_at": _iso(em.created_at),
    }


def _serialize_delivery(d):
    return {
        "id": d.pk,
        "emission_id": d.emission_id,
        "subscription_id": d.subscription_id,
        "target_url": d.target_url,
        "response_status": d.response_status,
        "attempt": d.attempt,
        "succeeded": d.succeeded,
        "error": d.error,
        "created_at": _iso(d.created_at),
    }


# ---------------------------------------------------------------------------
# Subscription validation
# ---------------------------------------------------------------------------


def _collect_subscription_values(data, *, existing=None):
    errors = {}
    values = {}

    if "event_type" in data:
        values["event_type"] = coerce_optional_text(data["event_type"]) or (
            EVENT_TYPE_CUSTOM
        )
    elif existing is None:
        values["event_type"] = EVENT_TYPE_CUSTOM

    if "property_filter" in data:
        pf = data["property_filter"]
        if not isinstance(pf, dict):
            errors["property_filter"] = "Must be a JSON object."
        else:
            values["property_filter"] = pf

    if "target_url" in data:
        target = coerce_optional_text(data["target_url"])
        if not target:
            errors["target_url"] = "Target URL is required."
        values["target_url"] = target
    elif existing is None:
        errors["target_url"] = "Target URL is required."

    if "secret" in data:
        secret = coerce_optional_text(data["secret"])
        if not secret:
            errors["secret"] = "Secret cannot be blank."
        values["secret"] = secret
    elif existing is None:
        errors["secret"] = "Secret is required."

    if "description" in data:
        values["description"] = coerce_optional_text(data["description"])

    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            errors["is_active"] = "Must be a boolean."
        else:
            values["is_active"] = data["is_active"]

    return values, errors


_SUBSCRIPTION_EXAMPLE = {
    "id": 1,
    "event_type": "custom",
    "property_filter": {"name": "v0_workshop"},
    "target_url": "https://handler.example.com/hook",
    "has_secret": True,
    "is_active": True,
    "description": "v0 credit fulfilment",
    "created_at": "2026-06-23T12:00:00+00:00",
    "updated_at": "2026-06-23T12:00:00+00:00",
}


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Triggers",
    summary="List or create trigger subscriptions",
    methods={
        "GET": {
            "summary": "List subscriptions",
            "responses": {
                200: {
                    "description": "List of subscriptions (secrets omitted).",
                    "example": {"subscriptions": [_SUBSCRIPTION_EXAMPLE]},
                },
                401: {"description": "Missing or invalid token."},
            },
        },
        "POST": {
            "summary": "Create a subscription",
            "request_body": {
                "required": ["target_url", "secret"],
                "properties": {
                    "event_type": {"type": "string"},
                    "property_filter": {"type": "object"},
                    "target_url": {"type": "string"},
                    "secret": {"type": "string"},
                    "description": {"type": "string"},
                    "is_active": {"type": "boolean"},
                },
                "example": {
                    "property_filter": {"name": "v0_workshop"},
                    "target_url": "https://handler.example.com/hook",
                    "secret": "s3cr3t",
                },
            },
            "responses": {
                201: {
                    "description": "Created (secret not echoed).",
                    "example": _SUBSCRIPTION_EXAMPLE,
                },
                422: {"description": "Validation error."},
            },
        },
    },
)
def subscriptions_collection(request):
    """GET/POST ``/api/triggers/subscriptions``. No DELETE (deactivate instead)."""
    if request.method == "GET":
        qs = TriggerSubscription.objects.all()
        active = parse_bool_query(request.GET.get("active"))
        if active is not None:
            qs = qs.filter(is_active=active)
        return JsonResponse(
            {"subscriptions": [_serialize_subscription(s) for s in qs]},
            status=200,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    values, errors = _collect_subscription_values(data, existing=None)
    if errors:
        return validation_response(errors)

    sub = TriggerSubscription(**values)
    sub.save()
    return JsonResponse(_serialize_subscription(sub), status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH")
@openapi_spec(
    tag="Triggers",
    summary="Retrieve or update a trigger subscription",
    methods={
        "GET": {
            "summary": "Retrieve a subscription",
            "responses": {
                200: {"description": "Subscription detail.", "example": _SUBSCRIPTION_EXAMPLE},
                404: {"description": "Subscription not found."},
            },
        },
        "PATCH": {
            "summary": "Update a subscription (including is_active toggle)",
            "request_body": {
                "properties": {
                    "event_type": {"type": "string"},
                    "property_filter": {"type": "object"},
                    "target_url": {"type": "string"},
                    "secret": {"type": "string"},
                    "description": {"type": "string"},
                    "is_active": {"type": "boolean"},
                },
                "example": {"is_active": False},
            },
            "responses": {
                200: {"description": "Updated.", "example": _SUBSCRIPTION_EXAMPLE},
                404: {"description": "Subscription not found."},
                422: {"description": "Validation error."},
            },
        },
    },
)
def subscription_detail(request, subscription_id):
    """GET/PATCH ``/api/triggers/subscriptions/<id>``. No DELETE."""
    sub = TriggerSubscription.objects.filter(pk=subscription_id).first()
    if sub is None:
        return error_response(
            "Subscription not found", "unknown_subscription", status=404,
        )

    if request.method == "GET":
        return JsonResponse(_serialize_subscription(sub), status=200)

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    values, errors = _collect_subscription_values(data, existing=sub)
    if errors:
        return validation_response(errors)

    for field, value in values.items():
        setattr(sub, field, value)
    sub.save()
    return JsonResponse(_serialize_subscription(sub), status=200)


# ---------------------------------------------------------------------------
# Widget validation
# ---------------------------------------------------------------------------


def _collect_widget_values(data, *, existing=None):
    errors = {}
    values = {}

    if "slug" in data:
        slug = coerce_optional_text(data["slug"])
        if not slug:
            errors["slug"] = "Slug is required."
        values["slug"] = slug
    elif existing is None:
        errors["slug"] = "Slug is required."

    if "event_name" in data:
        event_name = coerce_optional_text(data["event_name"])
        if not event_name:
            errors["event_name"] = "Event name is required."
        values["event_name"] = event_name
    elif existing is None:
        errors["event_name"] = "Event name is required."

    if "min_level" in data:
        try:
            values["min_level"] = int(data["min_level"])
        except (TypeError, ValueError):
            errors["min_level"] = "Must be an integer."

    for field in (
        "claim_label",
        "claim_body",
        "signin_cta",
        "claimed_label",
        "exhausted_label",
    ):
        if field in data:
            values[field] = coerce_optional_text(data[field])

    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            errors["is_active"] = "Must be a boolean."
        else:
            values["is_active"] = data["is_active"]

    return values, errors


_WIDGET_EXAMPLE = {
    "id": 1,
    "slug": "v0-claim",
    "event_name": "v0_workshop",
    "min_level": 5,
    "claim_label": "Claim your credit",
    "claim_body": "Get $10 of v0 credit.",
    "signin_cta": "Sign in to claim",
    "claimed_label": "Claimed",
    "exhausted_label": "No longer available",
    "is_active": True,
    "created_at": "2026-06-23T12:00:00+00:00",
    "updated_at": "2026-06-23T12:00:00+00:00",
}


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Triggers",
    summary="List or create event widgets",
    methods={
        "GET": {
            "summary": "List widgets",
            "responses": {
                200: {"description": "List of widgets.", "example": {"widgets": [_WIDGET_EXAMPLE]}},
                401: {"description": "Missing or invalid token."},
            },
        },
        "POST": {
            "summary": "Create a widget",
            "request_body": {
                "required": ["slug", "event_name"],
                "properties": {
                    "slug": {"type": "string"},
                    "event_name": {"type": "string"},
                    "min_level": {"type": "integer"},
                    "claim_label": {"type": "string"},
                    "claim_body": {"type": "string"},
                    "signin_cta": {"type": "string"},
                    "claimed_label": {"type": "string"},
                    "exhausted_label": {"type": "string"},
                    "is_active": {"type": "boolean"},
                },
                "example": {"slug": "v0-claim", "event_name": "v0_workshop"},
            },
            "responses": {
                201: {"description": "Created.", "example": _WIDGET_EXAMPLE},
                422: {"description": "Validation error."},
            },
        },
    },
)
def widgets_collection(request):
    """GET/POST ``/api/triggers/widgets``. No DELETE (deactivate instead)."""
    if request.method == "GET":
        qs = EventWidget.objects.all()
        active = parse_bool_query(request.GET.get("active"))
        if active is not None:
            qs = qs.filter(is_active=active)
        return JsonResponse(
            {"widgets": [_serialize_widget(w) for w in qs]}, status=200,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    values, errors = _collect_widget_values(data, existing=None)
    if errors:
        return validation_response(errors)

    if EventWidget.objects.filter(slug=values["slug"]).exists():
        return validation_response({"slug": "A widget with that slug already exists."})

    widget = EventWidget(**values)
    widget.save()
    return JsonResponse(_serialize_widget(widget), status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH")
@openapi_spec(
    tag="Triggers",
    summary="Retrieve or update an event widget",
    methods={
        "GET": {
            "summary": "Retrieve a widget",
            "responses": {
                200: {"description": "Widget detail.", "example": _WIDGET_EXAMPLE},
                404: {"description": "Widget not found."},
            },
        },
        "PATCH": {
            "summary": "Update a widget (including is_active toggle)",
            "request_body": {
                "properties": {
                    "event_name": {"type": "string"},
                    "min_level": {"type": "integer"},
                    "claim_label": {"type": "string"},
                    "claim_body": {"type": "string"},
                    "signin_cta": {"type": "string"},
                    "claimed_label": {"type": "string"},
                    "exhausted_label": {"type": "string"},
                    "is_active": {"type": "boolean"},
                },
                "example": {"is_active": False},
            },
            "responses": {
                200: {"description": "Updated.", "example": _WIDGET_EXAMPLE},
                404: {"description": "Widget not found."},
                422: {"description": "Validation error."},
            },
        },
    },
)
def widget_detail(request, widget_id):
    """GET/PATCH ``/api/triggers/widgets/<id>``. No DELETE."""
    widget = EventWidget.objects.filter(pk=widget_id).first()
    if widget is None:
        return error_response("Widget not found", "unknown_widget", status=404)

    if request.method == "GET":
        return JsonResponse(_serialize_widget(widget), status=200)

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    values, errors = _collect_widget_values(data, existing=widget)
    if errors:
        return validation_response(errors)

    if "slug" in values and values["slug"] != widget.slug and (
        EventWidget.objects.filter(slug=values["slug"]).exists()
    ):
        return validation_response({"slug": "A widget with that slug already exists."})

    for field, value in values.items():
        setattr(widget, field, value)
    widget.save()
    return JsonResponse(_serialize_widget(widget), status=200)


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Triggers",
    summary="List event emissions (read-only)",
    methods={
        "GET": {
            "summary": "List emissions",
            "query": {
                "user": {"type": "integer", "required": False, "description": "Filter by user id."},
                "event_name": {"type": "string", "required": False, "description": "Filter by event name."},
            },
            "responses": {
                200: {"description": "List of emissions."},
                401: {"description": "Missing or invalid token."},
            },
        },
    },
)
def emissions_collection(request):
    """GET ``/api/triggers/emissions``. Read-only log."""
    qs = EventEmission.objects.all()
    user_id = request.GET.get("user")
    if user_id:
        qs = qs.filter(user_id=user_id)
    event_name = request.GET.get("event_name")
    if event_name:
        qs = qs.filter(event_name=event_name)
    return JsonResponse(
        {"emissions": [_serialize_emission(e) for e in qs[:500]]}, status=200,
    )


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Triggers",
    summary="List webhook deliveries (read-only)",
    methods={
        "GET": {
            "summary": "List deliveries",
            "query": {
                "subscription": {"type": "integer", "required": False, "description": "Filter by subscription id."},
                "succeeded": {"type": "string", "enum": ["true", "false"], "required": False, "description": "Filter by success."},
            },
            "responses": {
                200: {"description": "List of deliveries."},
                401: {"description": "Missing or invalid token."},
            },
        },
    },
)
def deliveries_collection(request):
    """GET ``/api/triggers/deliveries``. Read-only log."""
    qs = WebhookDelivery.objects.all()
    subscription = request.GET.get("subscription")
    if subscription:
        qs = qs.filter(subscription_id=subscription)
    succeeded = parse_bool_query(request.GET.get("succeeded"))
    if succeeded is not None:
        qs = qs.filter(succeeded=succeeded)
    return JsonResponse(
        {"deliveries": [_serialize_delivery(d) for d in qs[:500]]}, status=200,
    )
