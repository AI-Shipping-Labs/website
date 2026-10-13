"""Member-facing skill-gap page (issue #1716).

``GET /skill-gap`` is stateless and deterministic:

- Anonymous visitors get the canonical gated card (sign in or create a
  free account). No background content is rendered for them.
- Any signed-in member gets the five-background picker. The picker is a
  plain GET form, so ``?background=<key>`` is a shareable URL that renders
  that background's strengths, gaps, suggested path, timeline, and three
  next steps from ``skill_gap/data/backgrounds.yaml``.
- An unknown ``background`` value re-renders the picker with a friendly
  error.

Nothing is stored, metered, or sent to an LLM. The only database work
is a read-only lookup of each linked asset's access level, so every
next-step card carries the canonical access badge.
"""

from urllib.parse import urlencode

from django.shortcuts import render

from accounts.oauth_context import get_oauth_provider_context
from content.access import LEVEL_REGISTERED, build_gated_access_copy
from content.models import Course, Project, Workshop

from ..backgrounds import action_type_badge, all_backgrounds, get_background

RESOURCE_URL = '/skill-gap'
TEMPLATE = 'skill_gap/skill_gap.html'
INVALID_BACKGROUND_MESSAGE = (
    "We don't have a learning path for that background. "
    'Pick one of the five below.'
)


# URL prefix -> (model, access-level field) for the next-step badges. The
# workshop field matches ``content/_workshop_card_badges.html``. The sprint
# program index (``/sprints``) has no single level - each sprint sets its
# own tier - so sprint steps keep only their type label.
ACCESS_LEVEL_SOURCES = {
    'courses': (Course, 'required_level'),
    'workshops': (Workshop, 'pages_required_level'),
    'projects': (Project, 'required_level'),
}


def _return_url(requested):
    """Where sign-in sends the visitor back: keep a valid shared background."""
    if requested and get_background(requested) is not None:
        return f'{RESOURCE_URL}?{urlencode({"background": requested})}'
    return RESOURCE_URL


def _gated_context(requested):
    """Copy for the anonymous sign-in card, from the shared builder (#1335)."""
    return_url = _return_url(requested)
    copy = build_gated_access_copy(
        gated_reason='authentication_required',
        verb='see your skill-gap analysis',
        noun='skill-gap analysis',
        required_level=LEVEL_REGISTERED,
        resource_url=return_url,
    )
    return {
        **copy,
        'gated_card_testid': 'skill-gap-gated-card',
        'gated_icon': 'compass',
        'signup_cta_testid': 'skill-gap-signup-cta',
        'signin_cta_testid': 'skill-gap-signin-cta',
        **get_oauth_provider_context(),
        'next_url': return_url,
    }


def _picker_options(selected_key):
    return [
        {
            'key': key,
            'label': profile['label'],
            'hook': profile['hook'],
            'checked': key == selected_key,
        }
        for key, profile in all_backgrounds().items()
    ]


def _detail_target(url):
    """``('workshops', 'slug')`` for a detail URL, else ``None``."""
    parts = [segment for segment in url.split('/') if segment]
    if len(parts) == 2 and parts[0] in ACCESS_LEVEL_SOURCES:
        return parts[0], parts[1]
    return None


def _access_levels(actions):
    """Read-only lookup of each linked asset's level, keyed by URL.

    One query per content type. An asset missing from the database gets no
    entry, so its card renders no access badge rather than a wrong one.
    """
    slugs_by_kind = {}
    for action in actions:
        target = _detail_target(action['url'])
        if target:
            slugs_by_kind.setdefault(target[0], set()).add(target[1])
    levels = {}
    for kind, slugs in slugs_by_kind.items():
        model, field = ACCESS_LEVEL_SOURCES[kind]
        for slug, level in model.objects.filter(slug__in=slugs).values_list('slug', field):
            levels[f'/{kind}/{slug}'] = level
    return levels


def _next_action(action, levels):
    label, icon = action_type_badge(action['type'])
    return {
        **action,
        'type_label': label,
        'type_icon': icon,
        'access_level': levels.get(action['url']),
    }


def _analysis(key, profile):
    levels = _access_levels(profile['next_actions'])
    return {
        'key': key,
        'label': profile['label'],
        'strengths': profile['already_have'],
        'gaps': profile['need_to_learn'],
        'path': profile['suggested_order'],
        'timeline': profile['timeline'],
        'next_actions': [_next_action(action, levels) for action in profile['next_actions']],
    }


def skill_gap(request):
    """Render the gated teaser, the picker, or one background's analysis."""
    if not request.user.is_authenticated:
        requested = request.GET.get('background', '').strip()
        return render(request, TEMPLATE, {'is_gated': True, **_gated_context(requested)})

    requested = request.GET.get('background', '').strip()
    profile = get_background(requested) if requested else None
    if profile is not None:
        return render(request, TEMPLATE, {
            'is_gated': False,
            'analysis': _analysis(requested, profile),
        })

    return render(request, TEMPLATE, {
        'is_gated': False,
        'analysis': None,
        'options': _picker_options(requested),
        'error': INVALID_BACKGROUND_MESSAGE if requested else '',
    })
