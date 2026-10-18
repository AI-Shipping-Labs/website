"""Staff user autocomplete search for Studio."""

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import Http404, JsonResponse

from accounts.utils.display import display_name
from content.access import (
    LEVEL_MAIN,
    get_active_overrides_by_user,
    get_user_level,
)
from plans.models import Plan, Sprint, SprintEnrollment
from studio.decorators import staff_required

User = get_user_model()


def _display_name_for(user):
    """Return the picker display name for a user."""
    return display_name(user)


def _relevance_rank(user, query_lower):
    """Sort key: lower is better."""
    email_l = user.email.lower()
    first_l = (user.first_name or '').lower()
    last_l = (user.last_name or '').lower()
    full_l = f'{first_l} {last_l}'.strip()
    if email_l == query_lower or full_l == query_lower:
        return 0
    if (
        email_l.startswith(query_lower)
        or first_l.startswith(query_lower)
        or last_l.startswith(query_lower)
        or full_l.startswith(query_lower)
    ):
        return 1
    return 2


def _build_name_or_email_query(query):
    """Build Q filter matching query across email, first_name, and last_name."""
    tokens = [t for t in query.split() if t]
    name_or_email = (
        Q(email__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
    )
    for token in tokens:
        if token == query:
            continue
        name_or_email = name_or_email | (
            Q(email__icontains=token)
            | Q(first_name__icontains=token)
            | Q(last_name__icontains=token)
        )
    if query.isdigit():
        name_or_email = name_or_email | Q(pk=int(query))
    return name_or_email


def _fetch_candidates(query):
    """Fetch top 10 candidate users matching query ordered by relevance."""
    name_or_email = _build_name_or_email_query(query)
    candidates = list(
        User.objects
        .filter(name_or_email)
        .select_related('membership__tier')
        [:50]
    )
    query_lower = query.lower()
    candidates.sort(
        key=lambda u: (
            _relevance_rank(u, query_lower),
            _display_name_for(u).lower(),
        )
    )
    return candidates[:10]


def _get_sprint_context(sprint, candidate_ids):
    """Return enrolled and plan user id sets for the candidate users in sprint."""
    if sprint is None or not candidate_ids:
        return set(), set()
    sprint_user_ids = set(
        SprintEnrollment.objects
        .filter(sprint=sprint, user_id__in=candidate_ids)
        .values_list('user_id', flat=True)
    )
    plan_user_ids = set(
        Plan.objects
        .filter(sprint=sprint, member_id__in=candidate_ids)
        .values_list('member_id', flat=True)
    )
    return sprint_user_ids, plan_user_ids


def _build_result_rows(candidates, sprint, sprint_user_ids, plan_user_ids):
    """Build result dict rows with batched override resolution."""
    overrides_by_user = get_active_overrides_by_user(candidates)
    results = []
    for user in candidates:
        tier_level = get_user_level(
            user,
            active_override=overrides_by_user.get(user.pk),
        )
        row = {
            'id': user.pk,
            'email': user.email,
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'display_name': _display_name_for(user),
            'tier_level': tier_level,
            'has_community_access': tier_level >= LEVEL_MAIN,
        }
        if sprint is not None:
            row['in_sprint'] = user.pk in sprint_user_ids
            row['has_plan_in_sprint'] = user.pk in plan_user_ids
        results.append(row)
    return results


@staff_required
def studio_user_search(request):
    """Staff-only JSON user search for Studio autocomplete fields."""
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})

    sprint_slug = request.GET.get('sprint', '').strip()
    sprint = None
    if sprint_slug:
        try:
            sprint = Sprint.objects.get(slug=sprint_slug)
        except Sprint.DoesNotExist as exc:
            raise Http404(f'Sprint {sprint_slug!r} not found') from exc

    candidates = _fetch_candidates(query)
    candidate_ids = [u.pk for u in candidates]
    sprint_user_ids, plan_user_ids = _get_sprint_context(sprint, candidate_ids)
    results = _build_result_rows(candidates, sprint, sprint_user_ids, plan_user_ids)
    return JsonResponse({'results': results})
