from functools import lru_cache
from pathlib import Path

import yaml
from django.conf import settings


@lru_cache(maxsize=1)
def get_tiers():
    """Load tier display data from tiers.yaml in the content repo.

    Returns the parsed YAML list (one dict per tier: Basic, Main, Premium).
    Returns an empty list if CONTENT_REPO_DIR is not configured or tiers.yaml is missing.
    """
    repo_dir = getattr(settings, 'CONTENT_REPO_DIR', None)
    if not repo_dir or not Path(repo_dir).is_dir():
        return []
    path = Path(repo_dir) / 'tiers.yaml'
    if not path.exists():
        return []
    with open(path) as f:
        return yaml.safe_load(f) or []


def get_tiers_with_features():
    """Return tier dicts with assembled feature lists for the homepage.

    Each tier dict gets a 'features' key containing a list of
    {'text': '...', 'included': True} dicts. Higher tiers get an
    "Everything in {previous tier}" line prepended.
    """
    tiers = get_tiers()
    result = []
    prev_tier_name = None

    for tier in tiers:
        tier_copy = dict(tier)

        # Collect feature bullets from this tier's activities
        features = []
        if prev_tier_name:
            features.append({'text': f'Everything in {prev_tier_name}', 'included': True})
        for activity in tier.get('activities', []):
            for feat in activity.get('features', []):
                features.append({'text': feat, 'included': True})

        tier_copy['features'] = features
        result.append(tier_copy)
        prev_tier_name = tier['name']

    return result


def get_activities():
    """Return the flat activities list for the activities page.

    Each activity dict has 'icon', 'title', 'description', and 'tiers'
    (list of tier slug strings). Activities owned by a lower tier are
    inherited by all higher tiers.
    """
    tiers = get_tiers()
    tier_names = [t['stripe_key'] for t in tiers]

    activities = []
    seen_titles = set()

    for i, tier in enumerate(tiers):
        # This tier and all higher tiers inherit this activity
        inheriting_tiers = tier_names[i:]
        for activity in tier.get('activities', []):
            title = activity['title']
            if title in seen_titles:
                continue
            seen_titles.add(title)
            activities.append({
                'icon': activity['icon'],
                'title': title,
                'description': activity['description'].strip(),
                'tiers': inheriting_tiers,
            })

    return activities
