"""Staff token API for UTM campaigns and tracked links (issue #875).

Source-of-truth contract:
- Campaigns can be listed, created, fetched, and patched. The campaign
  detail also returns its ``links`` array so a caller sees the whole
  campaign in one request.
- Links live under a campaign: list, create, fetch, patch.
- Every serialized link includes the ready-to-share ``url`` from
  ``UtmCampaignLink.build_url()`` plus ``effective_source`` /
  ``effective_medium``, so callers never rebuild the tracked URL.
- No hard delete (policy #864): DELETE is NOT registered in any
  ``require_methods`` chain, so a DELETE request falls through to the
  ``require_methods`` 405 ("Method not allowed"). Archive via
  ``PATCH {"is_archived": true}``; unarchive via ``{"is_archived": false}``.
- The campaign ``slug`` and a link's ``utm_content`` are validated against
  ``UTM_SLUG_VALIDATOR`` (lowercase letters/digits/underscore).
- ``utm_source`` / ``utm_medium`` accept any custom value. The preset lists
  (``UTM_SOURCE_PRESETS`` / ``UTM_MEDIUM_PRESETS``) are guidance only,
  surfaced as OpenAPI examples — they are NOT enforced.

Because no DELETE handler is defined here, this module needs no entry in
``api/delete_policy.py``; the delete-policy guard stays green.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
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
from integrations.models import UtmCampaign, UtmCampaignLink
from integrations.models.utm_campaign import (
    UTM_MEDIUM_PRESETS,
    UTM_SLUG_VALIDATOR,
    UTM_SOURCE_PRESETS,
)

# Preset guidance for utm_source / utm_medium. These are NOT enforced — any
# custom string is accepted on create/update — but they are surfaced in the
# OpenAPI examples so callers know the house-standard values. The lists live in
# integrations/models/utm_campaign.py so the token API and the Studio forms
# (studio/views/utm_campaigns.py) share one source of truth (issue #874).

CAMPAIGN_WRITABLE_FIELDS = {
    "name",
    "slug",
    "default_utm_source",
    "default_utm_medium",
    "notes",
    "is_archived",
}

LINK_WRITABLE_FIELDS = {
    "utm_content",
    "destination",
    "label",
    "utm_term",
    "utm_source",
    "utm_medium",
    "is_archived",
}

_CAMPAIGN_EXAMPLE = {
    "id": 1,
    "name": "May 2026 newsletter",
    "slug": "may_2026_newsletter",
    "default_utm_source": "newsletter",
    "default_utm_medium": "email",
    "notes": "Weekly sprint roundup.",
    "is_archived": False,
    "created_at": "2026-05-01T12:00:00+00:00",
    "updated_at": "2026-05-01T12:00:00+00:00",
}

_LINK_EXAMPLE = {
    "id": 1,
    "campaign": 1,
    "utm_content": "hero_cta",
    "utm_term": "",
    "utm_source": "",
    "utm_medium": "",
    "destination": "/events/demo-day",
    "label": "Hero call-to-action",
    "is_archived": False,
    "effective_source": "newsletter",
    "effective_medium": "email",
    "url": (
        "https://aishippinglabs.com/events/demo-day"
        "?utm_source=newsletter&utm_medium=email"
        "&utm_campaign=may_2026_newsletter&utm_content=hero_cta"
    ),
    "created_at": "2026-05-01T12:05:00+00:00",
    "updated_at": "2026-05-01T12:05:00+00:00",
}

_CAMPAIGN_DETAIL_EXAMPLE = dict(_CAMPAIGN_EXAMPLE, links=[_LINK_EXAMPLE])


def _iso(value):
    return value.isoformat() if value is not None else None


def serialize_campaign(campaign, *, links=None):
    """Return the canonical campaign object.

    When ``links`` is provided (an iterable of ``UtmCampaignLink``) the
    serialized ``links`` array is included (campaign detail). For list rows
    we omit it to keep the payload tight.
    """
    data = {
        "id": campaign.pk,
        "name": campaign.name,
        "slug": campaign.slug,
        "default_utm_source": campaign.default_utm_source,
        "default_utm_medium": campaign.default_utm_medium,
        "notes": campaign.notes,
        "is_archived": campaign.is_archived,
        "created_at": _iso(campaign.created_at),
        "updated_at": _iso(campaign.updated_at),
    }
    if links is not None:
        data["links"] = [serialize_link(link) for link in links]
    return data


def serialize_link(link):
    """Return the canonical link object, always including the tracked ``url``."""
    return {
        "id": link.pk,
        "campaign": link.campaign_id,
        "utm_content": link.utm_content,
        "utm_term": link.utm_term,
        "utm_source": link.utm_source,
        "utm_medium": link.utm_medium,
        "destination": link.destination,
        "label": link.label,
        "is_archived": link.is_archived,
        "effective_source": link.effective_source(),
        "effective_medium": link.effective_medium(),
        "url": link.build_url(),
        "created_at": _iso(link.created_at),
        "updated_at": _iso(link.updated_at),
    }


def _unknown_campaign_response():
    return error_response(
        "Campaign not found",
        "unknown_campaign",
        status=404,
    )


def _unknown_link_response():
    return error_response(
        "Link not found",
        "unknown_link",
        status=404,
    )


def _is_valid_slug(value):
    try:
        UTM_SLUG_VALIDATOR(value)
    except ValidationError:
        return False
    return True


def _collect_campaign_values(data, *, existing=None):
    """Validate a campaign payload and return ``(values, errors)``.

    ``existing`` is ``None`` for POST (creation) so required fields are
    checked even when absent. On PATCH it's the row being updated; only keys
    present in ``data`` are validated (partial update). Slug uniqueness and
    the slug-lock rule are enforced here.
    """
    errors = {}
    values = {}

    if "name" in data:
        name = coerce_optional_text(data["name"])
        if not name:
            errors["name"] = "Name is required."
        elif len(name) > 200:
            errors["name"] = "Name must be at most 200 characters."
        values["name"] = name
    elif existing is None:
        errors["name"] = "Name is required."

    if "slug" in data:
        slug = coerce_optional_text(data["slug"])
        if not slug:
            errors["slug"] = "Slug is required."
        elif not _is_valid_slug(slug):
            errors["slug"] = (
                "Use lowercase letters, digits, and underscores only."
            )
        elif len(slug) > 100:
            errors["slug"] = "Slug must be at most 100 characters."
        else:
            if existing is not None:
                # Slug is locked once the campaign has links (mirror Studio).
                if slug != existing.slug and existing.has_links():
                    errors["slug"] = (
                        "Slug is locked because the campaign has links."
                    )
                elif (
                    slug != existing.slug
                    and UtmCampaign.objects.filter(slug=slug)
                    .exclude(pk=existing.pk)
                    .exists()
                ):
                    errors["slug"] = (
                        f'A campaign with slug "{slug}" already exists.'
                    )
                else:
                    values["slug"] = slug
            else:
                if UtmCampaign.objects.filter(slug=slug).exists():
                    errors["slug"] = (
                        f'A campaign with slug "{slug}" already exists.'
                    )
                else:
                    values["slug"] = slug
    elif existing is None:
        errors["slug"] = "Slug is required."

    for field in ("default_utm_source", "default_utm_medium"):
        if field in data:
            value = coerce_optional_text(data[field])
            if not value:
                errors[field] = "This field is required."
            elif len(value) > 100:
                errors[field] = "Must be at most 100 characters."
            else:
                values[field] = value
        elif existing is None:
            errors[field] = "This field is required."

    if "notes" in data:
        notes = data["notes"]
        if notes is None:
            notes = ""
        if not isinstance(notes, str):
            errors["notes"] = "Must be a string."
        else:
            values["notes"] = notes

    if "is_archived" in data:
        if not isinstance(data["is_archived"], bool):
            errors["is_archived"] = "Must be a boolean."
        else:
            values["is_archived"] = data["is_archived"]

    return values, errors


def _collect_link_values(data, campaign, *, existing=None):
    """Validate a link payload and return ``(values, errors)``.

    Enforces ``utm_content`` slug format and uniqueness within the campaign
    (mirroring the model's ``unique_together``).
    """
    errors = {}
    values = {}

    if "utm_content" in data:
        utm_content = coerce_optional_text(data["utm_content"])
        if not utm_content:
            errors["utm_content"] = "utm_content is required."
        elif not _is_valid_slug(utm_content):
            errors["utm_content"] = (
                "Use lowercase letters, digits, and underscores only."
            )
        elif len(utm_content) > 100:
            errors["utm_content"] = "Must be at most 100 characters."
        else:
            duplicate = (
                campaign.links.filter(utm_content=utm_content)
                .exclude(pk=existing.pk if existing is not None else None)
                .exists()
            )
            if duplicate:
                errors["utm_content"] = (
                    f'A link with utm_content "{utm_content}" already '
                    "exists for this campaign."
                )
            else:
                values["utm_content"] = utm_content
    elif existing is None:
        errors["utm_content"] = "utm_content is required."

    if "destination" in data:
        destination = coerce_optional_text(data["destination"])
        if not destination:
            errors["destination"] = "destination is required."
        elif len(destination) > 1000:
            errors["destination"] = "Must be at most 1000 characters."
        else:
            values["destination"] = destination
    elif existing is None:
        errors["destination"] = "destination is required."

    for field in ("label", "utm_term", "utm_source", "utm_medium"):
        if field in data:
            value = coerce_optional_text(data[field])
            max_len = 200 if field == "label" else 100
            if len(value) > max_len:
                errors[field] = f"Must be at most {max_len} characters."
            else:
                values[field] = value

    if "is_archived" in data:
        if not isinstance(data["is_archived"], bool):
            errors["is_archived"] = "Must be a boolean."
        else:
            values["is_archived"] = data["is_archived"]

    return values, errors


def _apply_values(instance, values, writable):
    for field, value in values.items():
        if field in writable:
            setattr(instance, field, value)


def _get_campaign(campaign_id):
    return UtmCampaign.objects.filter(pk=campaign_id).first()


# ---------------------------------------------------------------------------
# Campaign collection: GET (list) / POST (create)
# ---------------------------------------------------------------------------
@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="UTM",
    summary="List or create UTM campaigns",
    methods={
        "GET": {
            "summary": "List UTM campaigns",
            "description": (
                "Returns ``UtmCampaign`` rows ordered by ``-created_at``. "
                "Optional filters narrow the result."
            ),
            "query": {
                "is_archived": {
                    "type": "string",
                    "enum": ["true", "false"],
                    "required": False,
                    "description": "Filter on is_archived (boolean string).",
                },
                "q": {
                    "type": "string",
                    "required": False,
                    "description": "Case-insensitive substring match on name.",
                },
            },
            "responses": {
                200: {
                    "description": "List of campaigns.",
                    "example": {"campaigns": [_CAMPAIGN_EXAMPLE]},
                },
                401: {"description": "Missing or invalid token."},
                422: {"description": "Invalid filter value."},
            },
        },
        "POST": {
            "summary": "Create a UTM campaign",
            "description": (
                "Creates a campaign. ``slug`` must match "
                "``^[a-z0-9_]+$`` and be unique. ``default_utm_source`` / "
                "``default_utm_medium`` accept any value; presets "
                f"(source: {', '.join(UTM_SOURCE_PRESETS)}; medium: "
                f"{', '.join(UTM_MEDIUM_PRESETS)}) are guidance only."
            ),
            "request_body": {
                "required": [
                    "name",
                    "slug",
                    "default_utm_source",
                    "default_utm_medium",
                ],
                "properties": {
                    "name": {"type": "string", "maxLength": 200},
                    "slug": {
                        "type": "string",
                        "maxLength": 100,
                        "pattern": "^[a-z0-9_]+$",
                    },
                    "default_utm_source": {
                        "type": "string",
                        "maxLength": 100,
                        "example": "newsletter",
                    },
                    "default_utm_medium": {
                        "type": "string",
                        "maxLength": 100,
                        "example": "email",
                    },
                    "notes": {"type": "string"},
                },
                "example": {
                    "name": "May 2026 newsletter",
                    "slug": "may_2026_newsletter",
                    "default_utm_source": "newsletter",
                    "default_utm_medium": "email",
                },
            },
            "responses": {
                201: {
                    "description": "Campaign created.",
                    "example": _CAMPAIGN_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                422: {
                    "description": (
                        "Validation error (missing field, invalid or "
                        "duplicate slug)."
                    ),
                },
            },
        },
    },
)
def utm_campaigns_collection(request):
    """GET/POST ``/api/utm-campaigns``.

    DELETE is intentionally NOT in the allowed-methods list: archive via
    ``PATCH {"is_archived": true}`` on the detail route. DELETE falls
    through to the ``require_methods`` 405.
    """
    if request.method == "GET":
        qs = UtmCampaign.objects.all().order_by("-created_at")

        archived_raw = request.GET.get("is_archived")
        if archived_raw is not None:
            archived = parse_bool_query(archived_raw)
            if archived is None:
                return validation_response(
                    {"is_archived": "Must be a boolean."}
                )
            qs = qs.filter(is_archived=archived)

        q = request.GET.get("q")
        if q:
            qs = qs.filter(name__icontains=q)

        return JsonResponse(
            {"campaigns": [serialize_campaign(c) for c in qs]},
            status=200,
        )

    # POST: create.
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response(status=422)

    values, errors = _collect_campaign_values(data, existing=None)
    if errors:
        return validation_response(errors)

    campaign = UtmCampaign(
        created_by=request.user if request.user.is_authenticated else None,
    )
    _apply_values(campaign, values, CAMPAIGN_WRITABLE_FIELDS)
    try:
        campaign.save()
    except IntegrityError:
        # Race on slug uniqueness; surface as a validation error.
        return validation_response(
            {"slug": "A campaign with this slug already exists."}
        )
    return JsonResponse(serialize_campaign(campaign), status=201)


# ---------------------------------------------------------------------------
# Campaign detail: GET (with links) / PATCH
# ---------------------------------------------------------------------------
@token_required
@csrf_exempt
@require_methods("GET", "PATCH")
@openapi_spec(
    tag="UTM",
    summary="Retrieve or update a UTM campaign",
    methods={
        "GET": {
            "summary": "Retrieve a campaign with its links",
            "description": (
                "Returns the campaign plus a ``links`` array. By default "
                "only active links are included; pass "
                "``?include_archived=true`` to also include archived links."
            ),
            "query": {
                "include_archived": {
                    "type": "string",
                    "enum": ["true", "false"],
                    "required": False,
                    "description": "Include archived links in the response.",
                },
            },
            "responses": {
                200: {
                    "description": "Campaign detail with links.",
                    "example": _CAMPAIGN_DETAIL_EXAMPLE,
                },
                404: {
                    "description": "Campaign not found.",
                    "example": {
                        "error": "Campaign not found",
                        "code": "unknown_campaign",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update a campaign",
            "description": (
                "Partial update of ``name``, ``default_utm_source``, "
                "``default_utm_medium``, ``notes``, and ``is_archived``. "
                "``slug`` is locked once the campaign has links — attempting "
                "to change it on a campaign with links returns 422. Use "
                "``is_archived=true`` to archive (no DELETE route)."
            ),
            "request_body": {
                "properties": {
                    "name": {"type": "string", "maxLength": 200},
                    "slug": {
                        "type": "string",
                        "maxLength": 100,
                        "pattern": "^[a-z0-9_]+$",
                    },
                    "default_utm_source": {"type": "string", "maxLength": 100},
                    "default_utm_medium": {"type": "string", "maxLength": 100},
                    "notes": {"type": "string"},
                    "is_archived": {"type": "boolean"},
                },
                "example": {"is_archived": True},
            },
            "responses": {
                200: {
                    "description": "Campaign updated.",
                    "example": _CAMPAIGN_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                404: {"description": "Campaign not found."},
                422: {
                    "description": (
                        "Validation error (bad slug, duplicate slug, or "
                        "slug change on a campaign with links)."
                    ),
                },
            },
        },
    },
)
def utm_campaign_detail(request, campaign_id):
    """GET/PATCH ``/api/utm-campaigns/<id>``.

    DELETE is not in the allowed-methods list; ``require_methods`` 405s it.
    """
    campaign = _get_campaign(campaign_id)
    if campaign is None:
        return _unknown_campaign_response()

    if request.method == "GET":
        include_archived = parse_bool_query(
            request.GET.get("include_archived")
        )
        links_qs = campaign.links.all()
        if not include_archived:
            links_qs = links_qs.filter(is_archived=False)
        return JsonResponse(
            serialize_campaign(campaign, links=links_qs),
            status=200,
        )

    # PATCH
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response(status=422)

    values, errors = _collect_campaign_values(data, existing=campaign)
    if errors:
        return validation_response(errors)

    _apply_values(campaign, values, CAMPAIGN_WRITABLE_FIELDS)
    try:
        campaign.save()
    except IntegrityError:
        return validation_response(
            {"slug": "A campaign with this slug already exists."}
        )
    return JsonResponse(serialize_campaign(campaign), status=200)


# ---------------------------------------------------------------------------
# Link collection: GET (list) / POST (create)
# ---------------------------------------------------------------------------
@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="UTM",
    summary="List or create tracked links for a campaign",
    methods={
        "GET": {
            "summary": "List links for a campaign",
            "query": {
                "is_archived": {
                    "type": "string",
                    "enum": ["true", "false"],
                    "required": False,
                    "description": "Filter on is_archived (boolean string).",
                },
            },
            "responses": {
                200: {
                    "description": "List of links.",
                    "example": {"links": [_LINK_EXAMPLE]},
                },
                404: {"description": "Campaign not found."},
            },
        },
        "POST": {
            "summary": "Create a tracked link",
            "description": (
                "Creates a link under the campaign. ``utm_content`` must "
                "match ``^[a-z0-9_]+$`` and be unique within the campaign. "
                "``utm_source`` / ``utm_medium`` override the campaign "
                "defaults when non-empty and accept any value."
            ),
            "request_body": {
                "required": ["utm_content", "destination"],
                "properties": {
                    "utm_content": {
                        "type": "string",
                        "maxLength": 100,
                        "pattern": "^[a-z0-9_]+$",
                    },
                    "destination": {"type": "string", "maxLength": 1000},
                    "label": {"type": "string", "maxLength": 200},
                    "utm_term": {"type": "string", "maxLength": 100},
                    "utm_source": {"type": "string", "maxLength": 100},
                    "utm_medium": {"type": "string", "maxLength": 100},
                },
                "example": {
                    "utm_content": "hero_cta",
                    "destination": "/events/demo-day",
                    "label": "Hero call-to-action",
                },
            },
            "responses": {
                201: {
                    "description": "Link created.",
                    "example": _LINK_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                404: {"description": "Campaign not found."},
                422: {
                    "description": (
                        "Validation error (missing field, invalid or "
                        "duplicate utm_content)."
                    ),
                },
            },
        },
    },
)
def utm_campaign_links_collection(request, campaign_id):
    """GET/POST ``/api/utm-campaigns/<id>/links``."""
    campaign = _get_campaign(campaign_id)
    if campaign is None:
        return _unknown_campaign_response()

    if request.method == "GET":
        qs = campaign.links.all()
        archived_raw = request.GET.get("is_archived")
        if archived_raw is not None:
            archived = parse_bool_query(archived_raw)
            if archived is None:
                return validation_response(
                    {"is_archived": "Must be a boolean."}
                )
            qs = qs.filter(is_archived=archived)
        return JsonResponse(
            {"links": [serialize_link(link) for link in qs]},
            status=200,
        )

    # POST: create.
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response(status=422)

    values, errors = _collect_link_values(data, campaign, existing=None)
    if errors:
        return validation_response(errors)

    link = UtmCampaignLink(
        campaign=campaign,
        created_by=request.user if request.user.is_authenticated else None,
    )
    _apply_values(link, values, LINK_WRITABLE_FIELDS)
    try:
        link.save()
    except IntegrityError:
        return validation_response(
            {
                "utm_content": (
                    "A link with this utm_content already exists for this "
                    "campaign."
                )
            }
        )
    return JsonResponse(serialize_link(link), status=201)


# ---------------------------------------------------------------------------
# Link detail: GET / PATCH
# ---------------------------------------------------------------------------
@token_required
@csrf_exempt
@require_methods("GET", "PATCH")
@openapi_spec(
    tag="UTM",
    summary="Retrieve or update a tracked link",
    methods={
        "GET": {
            "summary": "Retrieve a link",
            "description": (
                "Returns the link including the generated tracked ``url`` "
                "from ``build_url()`` plus ``effective_source`` / "
                "``effective_medium``."
            ),
            "responses": {
                200: {
                    "description": "Link detail.",
                    "example": _LINK_EXAMPLE,
                },
                404: {
                    "description": "Campaign or link not found.",
                    "example": {
                        "error": "Link not found",
                        "code": "unknown_link",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update a link",
            "description": (
                "Partial update of ``utm_content``, ``destination``, "
                "``label``, ``utm_term``, ``utm_source``, ``utm_medium``, "
                "and ``is_archived``. Archive a link with "
                "``is_archived=true`` (no DELETE route)."
            ),
            "request_body": {
                "properties": {
                    "utm_content": {
                        "type": "string",
                        "maxLength": 100,
                        "pattern": "^[a-z0-9_]+$",
                    },
                    "destination": {"type": "string", "maxLength": 1000},
                    "label": {"type": "string", "maxLength": 200},
                    "utm_term": {"type": "string", "maxLength": 100},
                    "utm_source": {"type": "string", "maxLength": 100},
                    "utm_medium": {"type": "string", "maxLength": 100},
                    "is_archived": {"type": "boolean"},
                },
                "example": {"is_archived": True},
            },
            "responses": {
                200: {
                    "description": "Link updated.",
                    "example": _LINK_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                404: {"description": "Campaign or link not found."},
                422: {
                    "description": (
                        "Validation error (invalid or duplicate "
                        "utm_content)."
                    ),
                },
            },
        },
    },
)
def utm_campaign_link_detail(request, campaign_id, link_id):
    """GET/PATCH ``/api/utm-campaigns/<id>/links/<link_id>``."""
    campaign = _get_campaign(campaign_id)
    if campaign is None:
        return _unknown_campaign_response()
    link = campaign.links.filter(pk=link_id).first()
    if link is None:
        return _unknown_link_response()

    if request.method == "GET":
        return JsonResponse(serialize_link(link), status=200)

    # PATCH
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response(status=422)

    values, errors = _collect_link_values(data, campaign, existing=link)
    if errors:
        return validation_response(errors)

    _apply_values(link, values, LINK_WRITABLE_FIELDS)
    try:
        link.save()
    except IntegrityError:
        return validation_response(
            {
                "utm_content": (
                    "A link with this utm_content already exists for this "
                    "campaign."
                )
            }
        )
    return JsonResponse(serialize_link(link), status=200)
