"""Project scores computed from peer reviews, following CMP's rules."""

import math
import statistics
from dataclasses import dataclass

from content.services.project_criteria import max_points, points_label


@dataclass(frozen=True)
class CriterionResult:
    id: str
    title: str
    type: str
    score: int
    options: tuple
    max_points: int = 0
    chosen_text: str = ''

    @property
    def is_bonus(self):
        return self.type == 'checklist'

    @property
    def bonus_marker(self):
        return self.is_bonus and 'bonus' not in self.title.lower()


@dataclass(frozen=True)
class ProjectResult:
    total: int
    criteria: tuple
    review_count: int
    passing_score: int
    passed: object


def _scale_index(criterion, value):
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value < len(criterion['options']):
            return value
    return None


def _checked_indexes(criterion, value):
    if not isinstance(value, list):
        return []
    picked = []
    for index in value:
        if _scale_index(criterion, index) is not None:
            picked.append(index)
    return picked


def chosen_indexes(criterion, criteria_scores):
    """Option indexes a review picked for one criterion (blank -> [])."""
    value = criteria_scores.get(criterion['id'])
    if criterion['type'] == 'checklist':
        return _checked_indexes(criterion, value)
    index = _scale_index(criterion, value)
    if index is None:
        return []
    return [index]


def criterion_points(criterion, criteria_scores):
    """Points one review gives one criterion; an unanswered one scores 0 (CMP)."""
    options = criterion['options']
    return sum(options[i]['points'] for i in chosen_indexes(criterion, criteria_scores))


def review_total(criteria, criteria_scores):
    return sum(criterion_points(c, criteria_scores) for c in criteria)


def review_points(review):
    """Total points of ONE review for its project's criteria, else None."""
    project = review.submission.course_project
    if project is None or not project.evaluation_criteria:
        return None
    if not review.is_complete:
        return None
    return review_total(project.evaluation_criteria, review.criteria_scores or {})


def default_criterion_score(criterion):
    """CMP's no-review fallback for one criterion."""
    points = [opt['points'] for opt in criterion['options']]
    if criterion['type'] == 'checklist':
        return math.ceil(sum(points) / 2)
    return math.ceil(statistics.median(points))


def _votes(criterion, score_dicts):
    counts = [0] * len(criterion['options'])
    for scores in score_dicts:
        for index in chosen_indexes(criterion, scores):
            counts[index] += 1
    options = []
    for index, option in enumerate(criterion['options']):
        label = f'{option["text"]} ({points_label(option["points"])})'
        options.append({**option, 'label': label, 'votes': counts[index]})
    return tuple(options)


def _chosen(criterion, options, score, reviewers):
    if not reviewers:
        return ()
    if criterion['type'] == 'checklist':
        picked = tuple(i for i, opt in enumerate(options) if opt['votes'] * 2 > reviewers)
        if sum(options[i]['points'] for i in picked) == score:
            return picked
        return ()
    matches = [i for i, opt in enumerate(options) if opt['points'] == score]
    if not matches:
        return ()
    return (max(matches, key=lambda i: options[i]['votes']),)


def _criterion_result(criterion, score_dicts):
    if score_dicts:
        points = [criterion_points(criterion, s) for s in score_dicts]
        score = math.ceil(statistics.median(points))
    else:
        score = default_criterion_score(criterion)
    options = _votes(criterion, score_dicts)
    chosen = _chosen(criterion, options, score, len(score_dicts))
    options = tuple({**opt, 'chosen': i in chosen} for i, opt in enumerate(options))
    return CriterionResult(
        id=criterion['id'], title=criterion['title'], type=criterion['type'],
        score=score, options=options, max_points=max_points(criterion),
        chosen_text='; '.join(options[i]['text'] for i in chosen),
    )


def project_result(criteria, reviews, passing_score=0, reviewed_enough=True):
    """Score one submission from its complete reviews; None without criteria."""
    if not criteria:
        return None
    score_dicts = [review.criteria_scores or {} for review in reviews]
    rows = tuple(_criterion_result(c, score_dicts) for c in criteria)
    total = sum(row.score for row in rows)
    passed = None
    if passing_score > 0:
        passed = total >= passing_score and reviewed_enough
    return ProjectResult(
        total=total, criteria=rows, review_count=len(score_dicts),
        passing_score=passing_score, passed=passed,
    )
