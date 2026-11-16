"""Structured project evaluation criteria authored as YAML in course.yaml."""

import re

CRITERION_TYPES = ('scale', 'checklist')
_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


class CriteriaError(ValueError):
    """Invalid authored criteria; the sync layer converts it to GitHubSyncError."""


def parse_criteria(raw, where):
    """Validate and normalize a criteria list; raise CriteriaError on bad input."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CriteriaError(f'{where}: criteria must be a list')
    seen = set()
    return [_parse_criterion(item, where, seen) for item in raw]


def _parse_criterion(item, where, seen):
    if not isinstance(item, dict):
        raise CriteriaError(f'{where}: each criterion must be a mapping')
    cid = item.get('id')
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        raise CriteriaError(f'{where}: criterion id {cid!r} must be a lowercase slug')
    if cid in seen:
        raise CriteriaError(f'{where}: duplicate criterion id {cid!r}')
    seen.add(cid)
    title = item.get('title')
    if not isinstance(title, str) or not title.strip():
        raise CriteriaError(f'{where}: criterion {cid!r} requires a title')
    kind = item.get('type', 'scale')
    if kind not in CRITERION_TYPES:
        raise CriteriaError(
            f'{where}: criterion {cid!r} type must be one of {", ".join(CRITERION_TYPES)}'
        )
    options = item.get('options')
    if not isinstance(options, list) or not options:
        raise CriteriaError(f'{where}: criterion {cid!r} requires options')
    return {
        'id': cid,
        'title': title.strip(),
        'description': str(item.get('description') or '').strip(),
        'type': kind,
        'options': [_parse_option(opt, cid, where) for opt in options],
    }


def _parse_option(opt, cid, where):
    if not isinstance(opt, dict):
        raise CriteriaError(f'{where}: criterion {cid!r} options must be mappings')
    points = opt.get('points')
    text = opt.get('text')
    if not isinstance(points, int) or isinstance(points, bool) or points < 0:
        raise CriteriaError(f'{where}: criterion {cid!r} option points must be a non-negative integer')
    if not isinstance(text, str) or not text.strip():
        raise CriteriaError(f'{where}: criterion {cid!r} option requires text')
    return {'points': points, 'text': text.strip()}


def max_points(criterion):
    points = [opt['points'] for opt in criterion['options']]
    if criterion['type'] == 'checklist':
        return sum(points)
    return max(points)


def split_criteria(criteria):
    """(core scale criteria, bonus checklists) for display."""
    core = [c for c in criteria if c['type'] == 'scale']
    bonus = [c for c in criteria if c['type'] == 'checklist']
    return core, bonus


LIST_INTROS = {'scale': 'Scoring:', 'checklist': 'Bonus points (checklist):'}


def points_label(points):
    """'0 points', '1 point', '2 points'."""
    if points == 1:
        return '1 point'
    return f'{points} points'


def _summary_option(option):
    return {**option, 'label': f'{option["text"]}: {points_label(option["points"])}'}


def _summary_row(criterion):
    """One criterion in authored order with its list intro and option lines."""
    options = [_summary_option(opt) for opt in criterion['options']]
    return {
        **criterion,
        'max_points': max_points(criterion),
        'list_intro': LIST_INTROS[criterion['type']],
        'options': options,
    }


def criteria_summary(criteria):
    """Template-ready rows (authored order) plus core and bonus totals."""
    rows = [_summary_row(c) for c in criteria]
    core, bonus = split_criteria(rows)
    core_max = sum(r['max_points'] for r in core)
    bonus_max = sum(r['max_points'] for r in bonus)
    return {
        'criteria': rows,
        'core_max': core_max,
        'bonus_max': bonus_max,
        'totals_label': totals_label(core_max, bonus_max),
    }


def totals_label(core_max, bonus_max):
    """'up to 18 points, plus up to 17 bonus points' (no bonus part when 0)."""
    label = f'up to {core_max} points'
    if bonus_max:
        label += f', plus up to {bonus_max} bonus points'
    return label


def score_review(criteria, data):
    """Parse posted per-criterion choices."""
    scores = {}
    total = 0
    missing = []
    for criterion in criteria:
        key = f'criterion-{criterion["id"]}'
        options = criterion['options']
        if criterion['type'] == 'checklist':
            picked = sorted({
                int(v) for v in data.getlist(key)
                if v.isdigit() and int(v) < len(options)
            })
            scores[criterion['id']] = picked
            total += sum(options[i]['points'] for i in picked)
            continue
        raw = data.get(key, '')
        if not raw.isdigit() or int(raw) >= len(options):
            missing.append(criterion['title'])
            continue
        scores[criterion['id']] = int(raw)
        total += options[int(raw)]['points']
    return scores, total, missing


def scores_complete(criteria, scores):
    """True when every scale criterion has a valid stored answer."""
    scores = scores if isinstance(scores, dict) else {}
    for criterion in criteria:
        if criterion['type'] == 'checklist':
            continue
        value = scores.get(criterion['id'])
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        if not 0 <= value < len(criterion['options']):
            return False
    return True


def settle_completion(reviews, criteria):
    """Defensive: un-complete, in memory only, reviews missing a required answer."""
    reviews = list(reviews)
    if criteria:
        for review in reviews:
            if review.is_complete and not scores_complete(criteria, review.criteria_scores):
                review.is_complete = False
    return reviews
