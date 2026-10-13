"""Loader for the skill-gap background data (issue #1716).

``skill_gap/data/backgrounds.yaml`` is the single source of truth for the
``/skill-gap`` page: five field-guide backgrounds, each with strengths,
ordered gaps, a suggested path, a timeline, and exactly three curated next
actions that link to platform assets.

The file is static repo content, so it is parsed and validated once per
process; a malformed file fails loudly on first access instead of
rendering a half-empty page.
"""

from functools import lru_cache
from pathlib import Path

import yaml

DATA_FILE = Path(__file__).resolve().parent / 'data' / 'backgrounds.yaml'

LIST_FIELDS = ('already_have', 'need_to_learn', 'suggested_order')
TEXT_FIELDS = ('label', 'hook', 'timeline')
ACTION_FIELDS = ('title', 'type', 'url', 'why')
# type -> (badge label, Lucide icon) for the next-step cards.
ACTION_TYPES = {
    'course': ('Course', 'book-open'),
    'workshop': ('Workshop', 'graduation-cap'),
    'sprint': ('Sprint', 'timer'),
    'project': ('Project', 'folder-git-2'),
}
NEXT_ACTION_COUNT = 3


class BackgroundDataError(Exception):
    """Raised when ``backgrounds.yaml`` is missing or malformed."""


def _validate_background(key, profile):
    if not isinstance(profile, dict):
        raise BackgroundDataError(f'Background {key!r} is not a mapping')
    for field in TEXT_FIELDS:
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise BackgroundDataError(f'Background {key!r} needs a non-empty {field!r}')
    for field in LIST_FIELDS:
        items = profile.get(field)
        if not isinstance(items, list) or not items:
            raise BackgroundDataError(f'Background {key!r} needs a non-empty {field!r} list')
    actions = profile.get('next_actions')
    if not isinstance(actions, list) or len(actions) != NEXT_ACTION_COUNT:
        raise BackgroundDataError(
            f'Background {key!r} needs exactly {NEXT_ACTION_COUNT} next_actions',
        )
    for action in actions:
        if not isinstance(action, dict):
            raise BackgroundDataError(f'Background {key!r} has a malformed next action')
        for field in ACTION_FIELDS:
            if not isinstance(action.get(field), str) or not action[field].strip():
                raise BackgroundDataError(
                    f'Background {key!r} next action is missing {field!r}: {action!r}',
                )
        if action['type'] not in ACTION_TYPES:
            raise BackgroundDataError(
                f'Background {key!r} next action has unknown type {action["type"]!r}',
            )
        if not action['url'].startswith('/') or action['url'].startswith('//'):
            raise BackgroundDataError(
                f'Background {key!r} next action url must be platform-relative: '
                f'{action["url"]!r}',
            )


@lru_cache(maxsize=None)
def _load():
    try:
        data = yaml.safe_load(DATA_FILE.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise BackgroundDataError(f'Cannot load {DATA_FILE}: {exc}') from exc
    backgrounds = (data or {}).get('backgrounds')
    if not isinstance(backgrounds, dict) or not backgrounds:
        raise BackgroundDataError('backgrounds.yaml has no backgrounds mapping')
    for key, profile in backgrounds.items():
        _validate_background(key, profile)
    return backgrounds


def all_backgrounds():
    """Return the background profiles keyed by slug, in file order."""
    return _load()


def get_background(key):
    """Return one background profile, or ``None`` for an unknown slug."""
    return _load().get(key)


def action_type_badge(action_type):
    """Badge ``(label, icon)`` for a next-action type (``workshop`` -> ``Workshop``)."""
    return ACTION_TYPES[action_type]
