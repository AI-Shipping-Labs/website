"""Read-only incoming review breakdowns shared by operator and member surfaces."""

from statistics import median

from api.serializers.datetime import isoformat_or_none
from content.services.attempt_reviews import reviewer_label
from content.services.project_criteria import max_points, settle_completion
from content.services.project_results import chosen_indexes, criterion_points


def submission_criteria(submission):
    project = submission.course_project
    if project is None:
        return []
    return project.evaluation_criteria or []


def incoming_reviews(submission):
    return settle_completion(
        submission.reviews.select_related('reviewer').order_by('pk'),
        submission_criteria(submission),
    )


def completed_reviews(reviews):
    completed = []
    for review in reviews:
        if review.is_complete:
            completed.append(review)
    return completed


def _criterion_review(criterion, scores):
    selected = chosen_indexes(criterion, scores)
    points = None
    if selected or criterion['type'] == 'checklist':
        points = criterion_points(criterion, scores)
    return {
        'id': criterion['id'], 'title': criterion['title'],
        'type': criterion['type'], 'points': points,
        'max_points': max_points(criterion),
        'selected_text': [criterion['options'][i]['text'] for i in selected],
        # PeerReview stores overall feedback only, not criterion comments.
        'feedback': None,
    }


def _review_row(review, criteria, label, staff):
    row = {
        'reviewer_label': label, 'is_complete': review.is_complete,
        'status': 'pending', 'feedback': review.feedback,
        'assigned_at': isoformat_or_none(review.assigned_at),
        'completed_at': isoformat_or_none(review.completed_at),
        'criteria': [_criterion_review(c, review.criteria_scores or {}) for c in criteria],
    }
    if review.is_complete:
        row['status'] = 'completed'
    if staff:
        row['id'] = review.pk
        row['reviewer'] = {
            'id': review.reviewer_id, 'email': review.reviewer.email,
            'name': review.reviewer.get_full_name(),
        }
    return row


def _criterion_median(criterion, reviews):
    points = []
    for review in reviews:
        points.append(criterion_points(criterion, review.criteria_scores or {}))
    score = None
    if points:
        score = median(points)
    return {
        'id': criterion['id'], 'title': criterion['title'],
        'points': score, 'max_points': max_points(criterion),
    }


def preliminary_score(criteria, reviews, passing_score):
    rows = [_criterion_median(c, reviews) for c in criteria]
    total = None
    meets_threshold = None
    if criteria and reviews:
        total = sum(row['points'] for row in rows)
        if passing_score > 0:
            meets_threshold = total >= passing_score
    return {
        'is_preliminary': True, 'method': 'median_per_criterion_then_sum',
        'review_count': len(reviews), 'criteria': rows, 'total': total,
        'passing_score': passing_score, 'meets_pass_threshold': meets_threshold,
    }


def incoming_breakdown(submission, *, staff=False, reviews=None):
    """Prepare data without saving or settling any records in the database."""
    if reviews is None:
        reviews = incoming_reviews(submission)
    complete = completed_reviews(reviews)
    visible = complete
    if staff:
        visible = reviews
    criteria = submission_criteria(submission)
    rows = []
    for number, review in enumerate(visible, start=1):
        rows.append(_review_row(review, criteria, reviewer_label(number), staff))
    return {
        'submission_id': submission.pk,
        'assigned_reviews_count': len(reviews), 'completed_reviews_count': len(complete),
        'reviews': rows,
        'preliminary_score': preliminary_score(
            criteria, complete, submission.course.project_passing_score,
        ),
    }
