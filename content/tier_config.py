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


CURATED_ACTIVITIES = (
    {
        'slug': 'community-sprints',
        'icon': 'timer',
        'title': 'Community sprints',
        'description': (
            'Time-boxed cohorts with check-ins, deadlines, and accountability '
            'for shipping one project per sprint.'
        ),
        'tiers': ('main', 'premium'),
        'action_label': 'Explore community sprints',
        'action_url': '/sprints',
    },
    {
        'slug': 'live-events',
        'icon': 'calendar-days',
        'title': 'Live events',
        'description': (
            'Regular live building sessions, office hours, mock interviews, '
            'and career sessions.'
        ),
        'tiers': ('main', 'premium'),
        'action_label': 'View live events',
        'action_url': '/events',
    },
    {
        'slug': 'workshops',
        'icon': 'presentation',
        'title': 'Hands-on workshops',
        'description': (
            'Hands-on workshops with recordings, step-by-step tutorials, and '
            'materials for putting ideas into practice.'
        ),
        'tiers': ('main', 'premium'),
        'action_label': 'Browse workshops',
        'action_url': '/workshops',
    },
    {
        'slug': 'slack-community',
        'icon': 'messages-square',
        'title': 'Private Slack community',
        'description': (
            'A private Slack space for questions, feedback, group learning, '
            'and trend breakdowns.'
        ),
        'tiers': ('main', 'premium'),
        'action_label': 'Compare community membership',
        'action_url': '/pricing',
    },
    {
        'slug': 'personal-plans',
        'icon': 'list-checks',
        'title': 'Personalized plans and accountability',
        'description': (
            'A personalized plan tailored to your goals and used inside '
            'sprints and accountability check-ins.'
        ),
        'tiers': ('main', 'premium'),
        'action_label': 'See how sprints work',
        'action_url': '/sprints',
    },
    {
        'slug': 'exclusive-content',
        'icon': 'file-text',
        'title': 'Exclusive written content',
        'description': (
            'Exclusive articles, tutorials with code examples, and practical '
            'AI tool breakdowns.'
        ),
        'tiers': ('basic', 'main', 'premium'),
        'action_label': 'Browse member articles',
        'action_url': '/blog',
    },
    {
        'slug': 'courses',
        'icon': 'book-open',
        'title': 'Mini-courses',
        'description': 'Structured mini-courses on specialized topics.',
        'tiers': ('premium',),
        'action_label': 'Browse courses',
        'action_url': '/courses',
    },
)


def get_curated_activities():
    """Return the stable, code-owned activities shown on ``/activities``.

    Copies keep callers from mutating the shared marketing contract while the
    tuple-valued tier mapping remains deterministic and easy to compare.
    ``get_activities()`` intentionally remains the synced-data path consumed
    by the separate ``/community`` surface.
    """
    return [dict(activity) for activity in CURATED_ACTIVITIES]


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
