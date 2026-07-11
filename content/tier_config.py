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
        'url': '/pricing',
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
        'url': '/pricing',
    },
}

DEFAULT_ACTIVITY_ACTION = {
    'label': 'Compare membership options',
    'url': '/pricing',
}


def _activity_action(title):
    return ACTIVITY_ACTIONS.get(title, DEFAULT_ACTIVITY_ACTION)


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
            action = _activity_action(title)
            activities.append({
                'icon': activity['icon'],
                'title': title,
                'description': activity['description'].strip(),
                'tiers': inheriting_tiers,
                'action_label': action['label'],
                'action_url': action['url'],
            })

    return activities
