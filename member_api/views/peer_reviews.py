"""Member peer-review list, inspection, and submission endpoints."""

import math

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import member_api_key_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import body_must_be_object_response, parse_json_body, require_methods
from content.models import PeerReview
from content.services.project_criteria import settle_completion
from member_api.scopes import require_scope
from member_api.serializers.peer_reviews import (
    serialize_peer_review_detail,
    serialize_peer_review_summary,
)
from member_api.views.peer_reviews_schemas import (
    PEER_REVIEW_DETAIL_OPENAPI,
    PEER_REVIEWS_COLLECTION_OPENAPI,
)
from member_api.views.peer_reviews_write import apply_peer_review_update

PAGE_SIZE = 20
VALID_STATUSES = {"all", "pending", "completed"}


def _parse_page(raw_page):
    if not raw_page:
        return 1, None
    try:
        page = int(raw_page)
    except (TypeError, ValueError):
        return None, error_response(
            "page must be a positive integer.",
            "invalid_query_param",
            status=422,
        )
    if page < 1:
        return None, error_response(
            "page must be a positive integer.",
            "invalid_query_param",
            status=422,
        )
    return page, None


def _parse_status(raw_status):
    status = raw_status or "all"
    if status not in VALID_STATUSES:
        return None, error_response(
            "status must be one of: all, pending, completed.",
            "invalid_query_param",
            status=422,
        )
    return status, None


def _assigned_queryset(user, course_slug, project_slug, status):
    qs = PeerReview.objects.filter(reviewer=user).select_related(
        "submission",
        "submission__course",
        "submission__course_project",
        "submission__course_project__cohort",
    )
    if course_slug:
        qs = qs.filter(submission__course__slug=course_slug)
    if project_slug:
        qs = qs.filter(submission__course_project__slug=project_slug)
    if status == "pending":
        qs = qs.filter(is_complete=False)
    elif status == "completed":
        qs = qs.filter(is_complete=True)
    return qs.order_by("is_complete", "-assigned_at", "pk")


def _settle_page_reviews(reviews):
    for review in reviews:
        project = review.submission.course_project
        if project is not None and project.evaluation_criteria:
            settle_completion([review], project.evaluation_criteria)


def _get_assigned_review(user, review_id):
    return (
        PeerReview.objects.filter(pk=review_id, reviewer=user)
        .select_related(
            "submission",
            "submission__course",
            "submission__course_project",
            "submission__course_project__cohort",
        )
        .first()
    )


@csrf_exempt
@require_methods("GET")
@member_api_key_required("peer_reviews:read")
@openapi_spec(tag="Peer Reviews", methods=PEER_REVIEWS_COLLECTION_OPENAPI)
def peer_reviews_collection(request):
    """List peer reviews assigned to the authenticated member."""
    page, page_err = _parse_page(request.GET.get("page"))
    if page_err is not None:
        return page_err
    status, status_err = _parse_status(request.GET.get("status"))
    if status_err is not None:
        return status_err

    course_slug = (request.GET.get("course") or "").strip()
    project_slug = (request.GET.get("project") or "").strip()
    qs = _assigned_queryset(request.user, course_slug, project_slug, status)
    total = qs.count()
    total_pages = 0
    if total:
        total_pages = math.ceil(total / PAGE_SIZE)
    offset = (page - 1) * PAGE_SIZE
    reviews = list(qs[offset : offset + PAGE_SIZE])
    _settle_page_reviews(reviews)
    return JsonResponse({
        "peer_reviews": [serialize_peer_review_summary(r) for r in reviews],
        "pagination": {
            "page": page,
            "page_size": PAGE_SIZE,
            "total": total,
            "total_pages": total_pages,
        },
    })


def _handle_detail_get(request, review):
    scope_err = require_scope(request, "peer_reviews:read")
    if scope_err is not None:
        return scope_err
    project = review.submission.course_project
    if project is not None and project.evaluation_criteria:
        settle_completion([review], project.evaluation_criteria)
    return JsonResponse(serialize_peer_review_detail(review))


def _handle_detail_write(request, review):
    scope_err = require_scope(request, "peer_reviews:write")
    if scope_err is not None:
        return scope_err
    body, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(body, dict):
        return body_must_be_object_response()
    err = apply_peer_review_update(request, review, body)
    if err is not None:
        return err
    return JsonResponse(serialize_peer_review_detail(review))


@csrf_exempt
@require_methods("GET", "PUT", "PATCH")
@member_api_key_required()
@openapi_spec(tag="Peer Reviews", methods=PEER_REVIEW_DETAIL_OPENAPI)
def peer_review_detail(request, review_id):
    """Inspect or submit/update one assigned peer review."""
    review = _get_assigned_review(request.user, review_id)
    if review is None:
        return error_response(
            "Peer review not found.",
            "peer_review_not_found",
            status=404,
        )
    if request.method == "GET":
        return _handle_detail_get(request, review)
    return _handle_detail_write(request, review)
