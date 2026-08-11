def get_tiers():
    """Load tier display data from the database.

    Returns the parsed tier list (one dict per tier: Basic, Main, Premium).
    Returns an empty list if no tier data has been synced.
    """
    from content.models import SiteConfig

    try:
        config = SiteConfig.objects.get(key='tiers')
        return config.data or []
    except SiteConfig.DoesNotExist:
        return []


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

        # The benefit title is the compact plan-card bullet.  Its optional
        # description is reserved for the expanded Membership explanation.
        features = []
        if prev_tier_name:
            features.append({'text': f'Everything in {prev_tier_name}', 'included': True})
        if 'benefits' in tier:
            for benefit in _tier_benefits(tier):
                title = str(benefit.get('title', '')).strip()
                if title:
                    features.append({'text': title, 'included': True})
        else:
            # Older synced revisions used activities with nested feature
            # bullets. Keep rendering them during a rolling content deploy.
            for activity in tier.get('activities', []):
                for feature in activity.get('features', []):
                    features.append({'text': feature, 'included': True})

        tier_copy['features'] = features
        result.append(tier_copy)
        prev_tier_name = tier['name']

    return result


ACTIVITY_ACTIONS = {
    'Exclusive Substack Content': {
        'label': 'Browse member articles',
        'url': '/blog',
    },
    'Behind-the-Scenes Research': {
        'label': 'Browse research notes',
        'url': '/blog',
    },
    'Curated Social Content Collection': {
        'label': 'Browse curated resources',
        'url': '/resources',
    },
    'Closed Community Access': {
        'label': 'Compare community membership',
        'url': '/membership',
    },
    'Collaborative Problem-Solving & Mentorship': {
        'label': 'See live community sessions',
        'url': '/events',
    },
    'Interactive Group Coding Sessions': {
        'label': 'See events',
        'url': '/events',
    },
    'Guided Project-Based Learning': {
        'label': 'Explore sprints',
        'url': '/sprints',
    },
    'Community Hackathons': {
        'label': 'Explore sprints',
        'url': '/sprints',
    },
    'Career Advancement Discussions': {
        'label': 'See events',
        'url': '/events',
    },
    'Personal Brand Development': {
        'label': 'Browse workshops',
        'url': '/workshops',
    },
    'Developer Productivity Tips & Workflows': {
        'label': 'Browse related resources',
        'url': '/resources',
    },
    'Propose and Vote on Topics': {
        'label': 'Open topic voting',
        'url': '/vote',
    },
    'Mini-Courses on Specialized Topics': {
        'label': 'Browse courses',
        'url': '/courses',
    },
    'Vote on Course Topics': {
        'label': 'Open course voting',
        'url': '/vote',
    },
    'Profile Teardowns': {
        'label': 'Compare Premium membership',
        'url': '/membership',
    },
}

DEFAULT_ACTIVITY_ACTION = {
    'label': 'Compare membership options',
    'url': '/membership',
}


def _activity_action(title):
    return ACTIVITY_ACTIONS.get(title, DEFAULT_ACTIVITY_ACTION)


def _tier_benefits(tier):
    """Return canonical benefits, with legacy ``activities`` compatibility."""
    if 'benefits' in tier:
        return tier.get('benefits') or []
    return tier.get('activities') or []


def get_membership_benefits():
    """Return the flat benefit list assembled from synced ``tiers.yaml``.

    The tier-level list and the expanded explanation use the same source
    record. A blank description intentionally means "show in the plan only".
    ``activities`` remains a read-only fallback while older content revisions
    are still in circulation during deployment.
    """
    tiers = get_tiers()
    tier_names = [t['stripe_key'] for t in tiers]

    benefits = []
    seen_titles = set()

    for i, tier in enumerate(tiers):
        # This tier and all higher tiers inherit this benefit.
        inheriting_tiers = tier_names[i:]
        for benefit in _tier_benefits(tier):
            title = str(benefit.get('title', '')).strip()
            if not title:
                continue
            if title in seen_titles:
                continue
            seen_titles.add(title)
            action = _activity_action(title)
            benefits.append({
                'icon': benefit.get('icon', 'circle'),
                'title': title,
                'description': str(benefit.get('description', '') or '').strip(),
                'tiers': inheriting_tiers,
                'minimum_tier': tier['stripe_key'],
                'tier_name': tier['name'],
                'required_level': tier.get('level', (i + 1) * 10),
                'action_label': benefit.get('action_label', action['label']),
                'action_url': benefit.get('action_url', action['url']),
            })

    return benefits


def get_activities():
    """Backward-compatible alias for callers using the former name."""
    return get_membership_benefits()
