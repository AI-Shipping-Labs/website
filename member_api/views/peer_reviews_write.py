"""Validation and persistence for member peer-review writes."""

from django.db import transaction
from django.utils import timezone

from api.safety import error_response
from content.services.peer_review_service import PeerReviewService


def _validation_error(message, details=None):
    return error_response(message, "validation_error", status=422, details=details)


def _check_window(review):
    project = review.submission.course_project
    if project is not None:
        if not project.is_review_open(timezone.now()):
            return error_response(
                "Peer review is not open for this project.",
                "review_window_closed",
                status=409,
            )
        return None
    if review.is_complete:
        return error_response(
            "Review already submitted.",
            "review_already_submitted",
            status=409,
        )
    return None


def _parse_feedback(body, review, is_patch):
    if "feedback" not in body:
        if is_patch:
            return review.feedback, None
        return "", None
    raw = body["feedback"]
    if not isinstance(raw, str):
        return None, _validation_error("feedback must be a string.")
    return raw.strip(), None


def _parse_checklist_value(cid, val, option_count):
    if not isinstance(val, list):
        return None, _validation_error(
            f"criteria_scores.{cid} must be a list of option indexes.",
        )
    cleaned = []
    for item in val:
        if isinstance(item, bool) or not isinstance(item, int):
            return None, _validation_error(
                f"criteria_scores.{cid} items must be integers.",
            )
        if not 0 <= item < option_count:
            return None, _validation_error(
                f"criteria_scores.{cid} index {item} is out of range.",
            )
        cleaned.append(item)
    return sorted(set(cleaned)), None


def _parse_scale_value(cid, val, option_count):
    if isinstance(val, bool) or not isinstance(val, int):
        return None, _validation_error(
            f"criteria_scores.{cid} must be an integer option index.",
        )
    if not 0 <= val < option_count:
        return None, _validation_error(
            f"criteria_scores.{cid} index {val} is out of range.",
        )
    return val, None


def _validate_criteria_scores(criteria, raw_scores, draft):
    by_id = {c["id"]: c for c in criteria}
    unknown = sorted(set(raw_scores) - set(by_id))
    if unknown:
        return None, _validation_error(
            f"Unknown criteria IDs: {', '.join(unknown)}.",
            details={"unknown_criteria": unknown},
        )
    normalized = {}
    missing = []
    for criterion in criteria:
        cid = criterion["id"]
        option_count = len(criterion["options"])
        if cid not in raw_scores:
            if criterion["type"] == "checklist":
                normalized[cid] = []
            elif not draft:
                missing.append(cid)
            continue
        if criterion["type"] == "checklist":
            val, err = _parse_checklist_value(cid, raw_scores[cid], option_count)
        else:
            val, err = _parse_scale_value(cid, raw_scores[cid], option_count)
        if err is not None:
            return None, err
        normalized[cid] = val
    if missing:
        return None, _validation_error(
            f"Missing required criteria scores: {', '.join(missing)}.",
            details={"missing_criteria": missing},
        )
    return normalized, None


def _parse_criteria_scores(body, review, criteria, is_patch, draft):
    if "criteria_scores" not in body:
        if is_patch:
            raw = dict(review.criteria_scores or {})
        else:
            raw = {}
    else:
        raw = body["criteria_scores"]
        if not isinstance(raw, dict):
            return None, _validation_error("criteria_scores must be an object.")
    return _validate_criteria_scores(criteria, raw, draft)


def _parse_legacy_score(body, review, is_patch):
    if "score" not in body:
        if is_patch:
            return review.score, None
        return None, None
    raw = body["score"]
    if raw is None:
        return None, None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 5:
        return None, _validation_error("score must be an integer between 1 and 5.")
    return raw, None


def _resolve_update_fields(request, review, body, draft):
    is_patch = request.method == "PATCH"
    feedback, err = _parse_feedback(body, review, is_patch)
    if err is not None:
        return None, err
    project = review.submission.course_project
    criteria = []
    if project is not None:
        criteria = list(project.evaluation_criteria or [])
    scores = dict(review.criteria_scores or {})
    legacy_score = review.score
    if criteria:
        scores, err = _parse_criteria_scores(body, review, criteria, is_patch, draft)
        if err is not None:
            return None, err
    else:
        legacy_score, err = _parse_legacy_score(body, review, is_patch)
        if err is not None:
            return None, err
        if not draft and not feedback:
            return None, _validation_error("feedback is required.")
    return (feedback, scores, legacy_score), None


def apply_peer_review_update(request, review, body):
    """Validate and persist a draft or completed peer review update."""
    window_err = _check_window(review)
    if window_err is not None:
        return window_err
    draft = body.get("draft", False)
    if not isinstance(draft, bool):
        return _validation_error("draft must be a boolean.")
    if draft and review.is_complete:
        return error_response(
            "Cannot save a draft on an already-submitted review.",
            "review_already_submitted",
            status=409,
        )
    resolved, err = _resolve_update_fields(request, review, body, draft)
    if err is not None:
        return err
    feedback, scores, legacy_score = resolved
    _save_review_update(request.user, review, feedback, scores, legacy_score, draft)
    return None


def _save_review_update(user, review, feedback, scores, legacy_score, draft):
    with transaction.atomic():
        review.feedback = feedback
        review.criteria_scores = scores
        review.score = legacy_score
        fields = ["criteria_scores", "score", "feedback"]
        if not draft:
            review.is_complete = True
            if review.completed_at is None:
                review.completed_at = timezone.now()
            fields.extend(["is_complete", "completed_at"])
        review.save(update_fields=fields)
        if not draft:
            submission = review.submission
            PeerReviewService.check_and_update_submission_status(submission)
            project = submission.course_project
            if project is not None and project.is_self_paced:
                PeerReviewService.check_certificate_eligibility(
                    user, project.course, course_project=project,
                )
