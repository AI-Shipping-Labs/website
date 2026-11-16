"""The CMP-style evaluation form for one assigned peer review."""

from django.utils import timezone

from content.services.project_criteria import points_label, score_review
from content.services.project_results import chosen_indexes, review_total

MISSING_CRITERIA_MESSAGE = 'Choose an option for every criterion.'


def missing_criteria_message(titles):
    return f'{MISSING_CRITERIA_MESSAGE} Missing: {", ".join(titles)}.'


def _option_rows(criterion, selected, readonly):
    picked = chosen_indexes(criterion, selected)
    rows = []
    for index, option in enumerate(criterion['options']):
        rows.append({
            'index': index,
            'label': f'{option["text"]} ({points_label(option["points"])})',
            'text': option['text'],
            'points_label': points_label(option['points']),
            'checked': index in picked,
            'readonly': readonly,
        })
    return rows


def _criterion_row(number, criterion, selected, missing, readonly):
    input_type = 'radio'
    if criterion['type'] == 'checklist':
        input_type = 'checkbox'
    return {
        'id': criterion['id'],
        'number': number,
        'title': criterion['title'],
        'description': criterion['description'],
        'input_name': f'criterion-{criterion["id"]}',
        'input_type': input_type,
        'is_bonus': criterion['type'] == 'checklist',
        'missing': criterion['title'] in missing,
        'options': _option_rows(criterion, selected, readonly),
    }


def evaluation_form(criteria, selected=None, missing=(), readonly=False):
    """Rows numbered in authored order, plus the total."""
    selected = selected or {}
    rows = [
        _criterion_row(number, criterion, selected, missing, readonly)
        for number, criterion in enumerate(criteria, start=1)
    ]
    return {
        'rows': rows,
        'total': review_total(criteria, selected),
        'missing_titles': list(missing),
    }


def save_draft(review, criteria, data):
    """Autosave the posted answers as a draft; True when they were stored."""
    if review.is_complete:
        return False
    scores, _total, _missing = score_review(criteria, data)
    review.criteria_scores = scores
    review.feedback = data.get('feedback', '').strip()
    review.save(update_fields=['criteria_scores', 'feedback'])
    return True


def save_review(review, criteria, data):
    """Submit a posted evaluation; returns ``(form, complete)``."""
    scores, _total, missing = score_review(criteria, data)
    feedback = data.get('feedback', '').strip()
    if missing:
        save_draft(review, criteria, data)
        form = evaluation_form(criteria, scores, missing)
        form['feedback'] = feedback
        return form, False
    review.criteria_scores = scores
    review.feedback = feedback
    review.is_complete = True
    if review.completed_at is None:
        review.completed_at = timezone.now()
    review.save(update_fields=['criteria_scores', 'feedback', 'is_complete', 'completed_at'])
    return evaluation_form(criteria, scores), True
