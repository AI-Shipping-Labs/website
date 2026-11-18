"""Serializers for member peer-review endpoints."""

from api.serializers.datetime import isoformat_or_none
from content.services.peer_pseudonym import peer_pseudonym
from content.services.project_criteria import criteria_summary
from content.services.project_results import review_total


def _project_criteria(submission):
    project = submission.course_project
    if project is None:
        return []
    return list(project.evaluation_criteria or [])


def serialize_course(course):
    """Serialize minimal course metadata for a peer review."""
    return {
        "id": course.pk,
        "slug": course.slug,
        "title": course.title,
    }


def serialize_project(project):
    """Serialize CourseProject metadata, or None for legacy submissions."""
    if project is None:
        return None
    return {
        "id": project.pk,
        "slug": project.slug,
        "title": project.title,
        "state": project.state,
        "state_label": project.get_state_display(),
        "review_due_at": isoformat_or_none(project.review_due_at),
        "is_self_paced": project.is_self_paced,
        "review_open": project.is_review_open(),
    }


def serialize_submission(submission):
    """Serialize the reviewed submission without exposing author PII."""
    links = submission.learning_in_public_links
    if links is None:
        links = []
    return {
        "id": submission.pk,
        "author_label": peer_pseudonym(submission.user_id, submission.course_id),
        "project_url": submission.project_url,
        "commit_id": submission.commit_id,
        "description": submission.description,
        "learning_in_public_links": list(links),
        "time_spent": submission.time_spent,
        "submitted_at": isoformat_or_none(submission.submitted_at),
        "review_deadline": isoformat_or_none(submission.review_deadline),
    }


def _serialize_criterion(criterion):
    options = []
    for index, option in enumerate(criterion.get("options", [])):
        options.append({
            "index": index,
            "points": option["points"],
            "text": option["text"],
        })
    return {
        "id": criterion["id"],
        "title": criterion["title"],
        "description": criterion.get("description", ""),
        "type": criterion["type"],
        "max_points": criterion["max_points"],
        "options": options,
    }


def serialize_peer_review_summary(review):
    """Serialize an assigned PeerReview for the list endpoint."""
    submission = review.submission
    criteria = _project_criteria(submission)
    scores = dict(review.criteria_scores or {})
    return {
        "id": review.pk,
        "submission_id": submission.pk,
        "course": serialize_course(submission.course),
        "project": serialize_project(submission.course_project),
        "submission": serialize_submission(submission),
        "is_complete": review.is_complete,
        "assigned_at": isoformat_or_none(review.assigned_at),
        "completed_at": isoformat_or_none(review.completed_at),
        "feedback": review.feedback,
        "score": review.score,
        "criteria_scores": scores,
        "total_points": review_total(criteria, scores),
        "has_criteria": bool(criteria),
    }


def serialize_peer_review_detail(review):
    """Serialize an assigned PeerReview with full evaluation criteria."""
    submission = review.submission
    raw_criteria = _project_criteria(submission)
    summary = criteria_summary(raw_criteria)
    data = serialize_peer_review_summary(review)
    data["criteria"] = [_serialize_criterion(c) for c in summary["criteria"]]
    data["criteria_max_points"] = {
        "core": summary["core_max"],
        "bonus": summary["bonus_max"],
    }
    data["passing_score"] = submission.course.project_passing_score
    return data
