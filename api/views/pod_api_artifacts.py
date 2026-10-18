"""Cohesive route-family owner extracted from the Pods staff API."""

# The shared module exports the single wire/query contract used by each route
# family, including private serializer names.
from api.views.pod_api_shared import (
    _ERR_UNKNOWN_POD,
    ARTIFACT_KIND_CHOICES,
    TAG,
    JsonResponse,
    PodArtifact,
    _get_pod,
    _parse_limit,
    _unknown_pod,
    csrf_exempt,
    display_name,
    error_response,
    isoformat_or_none,
    meeting_state,
    openapi_spec,
    require_methods,
    token_required,
    validation_response,
)

# --- Pod notes and transcripts (issue #1922) ---------------------------------------

ARTIFACT_LIST_DEFAULT_LIMIT = 50
ARTIFACT_LIST_MAX_LIMIT = 200
_ARTIFACT_KIND_VALUES = [value for value, _label in ARTIFACT_KIND_CHOICES]

_ARTIFACT_EXAMPLE = {
    "id": 5,
    "pod_id": 7,
    "meeting_id": 30,
    "meeting_number": 2,
    "kind": "transcript",
    "title": "Meeting 2 - Oct 14",
    "source": "uploaded",
    "original_filename": "meeting-2.vtt",
    "added_by_email": "raj@example.com",
    "added_by_name": "Raj P.",
    "created_at": "2026-10-14T19:05:00+00:00",
    "size_bytes": 48211,
}
_ARTIFACT_DETAIL_EXAMPLE = {
    **_ARTIFACT_EXAMPLE,
    "body": "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nAnna: Let us start with the demo.",
}
_ERR_UNKNOWN_ARTIFACT = {"error": "Artifact not found", "code": "unknown_artifact"}


def _serialize_artifact(artifact, numbers, *, with_body=False):
    meeting_number = None
    if artifact.meeting_id:
        meeting_number = numbers.get(artifact.meeting_id)
    added_by_email = None
    added_by_name = None
    if artifact.added_by_id:
        added_by_email = artifact.added_by.email
        added_by_name = display_name(artifact.added_by)
    data = {
        "id": artifact.pk,
        "pod_id": artifact.pod_id,
        "meeting_id": artifact.meeting_id,
        "meeting_number": meeting_number,
        "kind": artifact.kind,
        "title": artifact.title,
        "source": artifact.source,
        "original_filename": artifact.original_filename,
        "added_by_email": added_by_email,
        "added_by_name": added_by_name,
        "created_at": isoformat_or_none(artifact.created_at),
        "size_bytes": artifact.size_bytes,
    }
    if with_body:
        data["body"] = artifact.body
    return data


def _artifact_filters(request, artifacts):
    kind = request.GET.get("kind")
    if kind is not None:
        if kind not in _ARTIFACT_KIND_VALUES:
            error = validation_response(
                {"field": "kind", "allowed": _ARTIFACT_KIND_VALUES},
                message=f"kind must be one of: {', '.join(_ARTIFACT_KIND_VALUES)}",
            )
            return None, error
        artifacts = artifacts.filter(kind=kind)
    meeting_raw = request.GET.get("meeting_id")
    if meeting_raw is not None:
        meeting_raw = meeting_raw.strip()
        if not meeting_raw.isascii() or not meeting_raw.isdigit() or len(meeting_raw) > 18:
            error = validation_response(
                {"field": "meeting_id", "expected": "integer"},
                message="meeting_id must be an integer",
            )
            return None, error
        artifacts = artifacts.filter(meeting_id=int(meeting_raw))
    return artifacts, None


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="List pod notes and transcripts (staff-only)",
    methods={
        "GET": {
            "summary": "List pod notes and transcripts (staff-only)",
            "description": (
                "Meeting notes and transcripts pod members added, newest first, "
                "without ``body`` (fetch one artifact for the text). "
                "``meeting_number`` is the meeting's number in the pod, or "
                "``null`` when the artifact names no meeting or the meeting has "
                "no number (cancelled). ``size_bytes`` is the UTF-8 size of the "
                "body. Filter with ``kind`` and ``meeting_id``; page with "
                "``limit`` and ``offset``; ``total`` counts every match. Works "
                "whatever ``PODS_ARTIFACTS_ENABLED`` and ``PODS_COURSE_SLUGS`` "
                "say. There is no create or delete endpoint."
            ),
            "query": {
                "kind": {"type": "string", "enum": _ARTIFACT_KIND_VALUES},
                "meeting_id": {"type": "integer"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": ARTIFACT_LIST_MAX_LIMIT,
                    "default": ARTIFACT_LIST_DEFAULT_LIMIT,
                },
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "responses": {
                200: {
                    "description": "Artifact page.",
                    "example": {
                        "total": 1,
                        "limit": ARTIFACT_LIST_DEFAULT_LIMIT,
                        "offset": 0,
                        "artifacts": [_ARTIFACT_EXAMPLE],
                    },
                },
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod.", "example": _ERR_UNKNOWN_POD},
                422: {
                    "description": "Bad kind, meeting_id, limit or offset.",
                    "example": {
                        "error": "limit must be between 1 and 200; offset must be 0 or more",
                        "code": "validation_error",
                    },
                },
            },
        },
    },
)
def pod_artifacts(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    limit, offset, error = _parse_limit(request, ARTIFACT_LIST_DEFAULT_LIMIT, ARTIFACT_LIST_MAX_LIMIT)
    if error is not None:
        return error
    artifacts = (
        PodArtifact.objects.filter(pod=pod).select_related("added_by").defer("body").order_by("-created_at", "-pk")
    )
    artifacts, filter_error = _artifact_filters(request, artifacts)
    if filter_error is not None:
        return filter_error
    numbers = meeting_state(pod).numbers
    return JsonResponse(
        {
            "total": artifacts.count(),
            "limit": limit,
            "offset": offset,
            "artifacts": [_serialize_artifact(a, numbers) for a in artifacts[offset : offset + limit]],
        }
    )


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Read one pod note or transcript (staff-only)",
    methods={
        "GET": {
            "summary": "Read one pod note or transcript (staff-only)",
            "description": (
                "The same fields as the list plus ``body``: the plain text as "
                "saved (trimmed, no markdown). An uploaded ``.vtt`` transcript "
                "keeps its WebVTT text. ``404 unknown_artifact`` for an artifact "
                "of another pod."
            ),
            "responses": {
                200: {"description": "The artifact with its body.", "example": _ARTIFACT_DETAIL_EXAMPLE},
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod, or an artifact of another pod.", "example": _ERR_UNKNOWN_ARTIFACT},
            },
        },
    },
)
def pod_artifact_detail(request, pod_id, artifact_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    artifact = PodArtifact.objects.select_related("added_by").filter(pk=artifact_id, pod=pod).first()
    if artifact is None:
        return error_response("Artifact not found", "unknown_artifact", status=404)
    return JsonResponse(_serialize_artifact(artifact, meeting_state(pod).numbers, with_body=True))
