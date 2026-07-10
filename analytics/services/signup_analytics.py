"""Shared signup analytics reporting helpers for Studio and the staff API."""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.utils import timezone

from analytics.models import SIGNUP_PATH_CHOICES, CampaignVisit, UserAttribution
from integrations.models import UtmCampaign

SIGNUP_PATH_VALUES = [value for value, _label in SIGNUP_PATH_CHOICES]
SIGNUP_PATH_LABELS = dict(SIGNUP_PATH_CHOICES)

DEFAULT_RANGE = '7d'
RANGE_CHOICES = ('24h', '7d', '30d', 'custom')
TOP_N = 10
RECENT_PAGE_SIZE = 50
API_RECENT_LIMIT_MAX = 100

DIRECT_SOURCE_LABEL = 'direct / no tracked source'
NO_TRACKED_VISITS_LABEL = 'No tracked pre-signup visits'


def resolve_window(range_key, start_str=None, end_str=None, *, now=None):
    """Resolve a ``(start, end)`` window from filter params."""
    if now is None:
        now = timezone.now()

    if range_key == 'custom' and start_str and end_str:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
        end = timezone.make_aware(datetime.combine(end_date, time.max), tz)
        return start, end

    if range_key == '24h':
        return now - timedelta(hours=24), now
    days_map = {'7d': 7, '30d': 30}
    days = days_map.get(range_key, 7)
    return now - timedelta(days=days), now


def parse_signup_analytics_filters(params, *, strict=False, now=None):
    """Parse dashboard/API query params into normalized report filters.

    Studio calls this with ``strict=False`` to preserve the existing forgiving
    fallback behavior. The API calls it with ``strict=True`` so invalid filters
    can become structured 422 responses.
    """
    range_key = (params.get('range') or DEFAULT_RANGE).strip()
    start_str = (params.get('start') or '').strip()
    end_str = (params.get('end') or '').strip()
    signup_path = (params.get('signup_path') or '').strip()
    errors = {}

    if range_key not in RANGE_CHOICES:
        errors['range'] = (
            'Use one of: 24h, 7d, 30d, custom.'
        )
        range_key = DEFAULT_RANGE

    if signup_path and signup_path not in SIGNUP_PATH_VALUES:
        errors['signup_path'] = 'Unknown signup path.'
        signup_path = ''

    if range_key == 'custom':
        if strict and not start_str:
            errors['start'] = 'Start date is required when range=custom.'
        if strict and not end_str:
            errors['end'] = 'End date is required when range=custom.'

        if start_str and end_str:
            try:
                start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
            except ValueError:
                errors['date'] = 'Use YYYY-MM-DD dates for start and end.'
            else:
                if start_date > end_date:
                    errors['date'] = 'Start date must be on or before end date.'
    elif strict:
        start_str = ''
        end_str = ''

    if strict and errors:
        return None, errors

    if errors:
        range_key = DEFAULT_RANGE
        start_str = ''
        end_str = ''

    try:
        start, end = resolve_window(
            range_key,
            start_str,
            end_str,
            now=now,
        )
    except ValueError:
        range_key = DEFAULT_RANGE
        start_str = ''
        end_str = ''
        start, end = resolve_window(range_key, now=now)

    window = end - start
    return {
        'range_key': range_key,
        'start_str': start_str,
        'end_str': end_str,
        'signup_path': signup_path,
        'start': start,
        'end': end,
        'prior_start': start - window,
        'prior_end': start,
    }, {}


def parse_recent_limit(value, *, default=RECENT_PAGE_SIZE):
    """Parse the API recent-signups limit and cap it at a safe maximum."""
    if value in (None, ''):
        return default, {}
    try:
        limit = int(str(value).strip())
    except (TypeError, ValueError):
        return default, {'limit': 'Limit must be a positive integer.'}
    if limit < 1:
        return default, {'limit': 'Limit must be a positive integer.'}
    return min(limit, API_RECENT_LIMIT_MAX), {}


def querystring(filters):
    """Encode active filters as ``?key=value&...`` (empty for defaults)."""
    params = {}
    if filters['range_key'] != DEFAULT_RANGE:
        params['range'] = filters['range_key']
    if filters['range_key'] == 'custom':
        if filters['start_str']:
            params['start'] = filters['start_str']
        if filters['end_str']:
            params['end'] = filters['end_str']
    if filters['signup_path']:
        params['signup_path'] = filters['signup_path']
    if not params:
        return ''
    return '?' + urlencode(params)


def headline_cards(now, *, signup_path=None):
    """Compute the rolling 24h / 7d / 30d headline cards."""
    base = UserAttribution.objects.all()
    if signup_path:
        base = base.filter(signup_path=signup_path)

    windows = [
        ('24h', '24h', timedelta(hours=24)),
        ('Last 7d', '7d', timedelta(days=7)),
        ('Last 30d', '30d', timedelta(days=30)),
    ]

    aggregates = {}
    for _label, key, window in windows:
        cutoff = now - window
        prior_cutoff = cutoff - window
        aggregates[f'cur_{key}'] = Count(
            'user_id',
            filter=Q(created_at__gte=cutoff, created_at__lt=now),
        )
        aggregates[f'prv_{key}'] = Count(
            'user_id',
            filter=Q(created_at__gte=prior_cutoff, created_at__lt=cutoff),
        )

    row = base.aggregate(**aggregates)
    labels = {'24h': 'Last 24h', '7d': 'Last 7d', '30d': 'Last 30d'}
    cards = []
    for _label, key, _window in windows:
        cur = row[f'cur_{key}'] or 0
        prv = row[f'prv_{key}'] or 0
        cards.append({
            'label': labels[key],
            'window_label': key,
            'count': cur,
            'delta': _delta(cur, prv),
        })
    return cards


def build_signup_analytics_report(filters, *, now=None, has_referrer_data=True):
    """Return all dashboard/API rows for the selected filter window."""
    if now is None:
        now = timezone.now()

    attributions = list(_window_attributions(filters))
    window_total = len(attributions)
    campaigns_by_slug = _campaigns_by_slug(attributions)
    visits_by_user_id = _pre_signup_visits_by_user_id(attributions)
    sources_by_user_id = {}

    for attr in attributions:
        source = actionable_source_for_attribution(
            attr,
            campaigns_by_slug,
            has_referrer_data=has_referrer_data,
        )
        sources_by_user_id[attr.user_id] = source
        _attach_journey_context(attr, source, visits_by_user_id.get(attr.user_id, []))

    return {
        'filters': filters,
        'headline_cards': headline_cards(
            now,
            signup_path=filters['signup_path'],
        ),
        'window_total': window_total,
        'signup_path_rows': _signup_path_rows(attributions, window_total),
        'utm_source_rows': _utm_source_rows(attributions, window_total),
        'referrer_rows': _referrer_rows(
            attributions,
            window_total,
            has_referrer_data=has_referrer_data,
        ),
        'campaign_rows': _campaign_rows(
            attributions,
            window_total,
            campaigns_by_slug,
        ),
        'actionable_source_rows': _actionable_source_rows(
            attributions,
            window_total,
        ),
        'pre_signup_activity_rows': _pre_signup_activity_rows(
            attributions,
            sources_by_user_id,
        ),
        'recent_signups': attributions,
    }


def actionable_source_for_attribution(
    attr,
    campaigns_by_slug,
    *,
    has_referrer_data=True,
):
    """Resolve the actionable source for one signup using the issue priority."""
    campaign_slug = (attr.first_touch_utm_campaign or '').strip()
    campaign = getattr(attr, 'first_touch_campaign', None)
    if not campaign and campaign_slug:
        campaign = campaigns_by_slug.get(campaign_slug)
    if campaign:
        return {
            'key': f'campaign:{campaign.slug}',
            'label': f'{campaign.name} ({campaign.slug})',
            'kind': 'campaign',
            'campaign_id': campaign.pk,
            'campaign_name': campaign.name,
            'campaign_slug': campaign.slug,
            'has_campaign': True,
        }

    if campaign_slug:
        return {
            'key': f'utm_campaign:{campaign_slug}',
            'label': campaign_slug,
            'kind': 'utm',
            'campaign_id': None,
            'campaign_name': '',
            'campaign_slug': '',
            'has_campaign': False,
        }

    source = (attr.first_touch_utm_source or '').strip()
    medium = (attr.first_touch_utm_medium or '').strip()
    if source or medium:
        if source and medium:
            label = f'{source} / {medium}'
        else:
            label = source or medium
        return {
            'key': f'utm:{source}|{medium}',
            'label': label,
            'kind': 'utm',
            'campaign_id': None,
            'campaign_name': '',
            'campaign_slug': '',
            'has_campaign': False,
        }

    if has_referrer_data:
        host = (getattr(attr, 'first_touch_referrer_host', '') or '').strip()
        referrer_source = (
            getattr(attr, 'first_touch_referrer_source', '') or ''
        ).strip()
        if host or (referrer_source and referrer_source != 'direct'):
            label = _referrer_label(attr, host, referrer_source)
            return {
                'key': f'referrer:{referrer_source}|{host}',
                'label': label,
                'kind': 'referrer',
                'campaign_id': None,
                'campaign_name': '',
                'campaign_slug': '',
                'has_campaign': False,
            }

    return {
        'key': 'direct:',
        'label': DIRECT_SOURCE_LABEL,
        'kind': 'direct',
        'campaign_id': None,
        'campaign_name': '',
        'campaign_slug': '',
        'has_campaign': False,
    }


def content_category_for_path(path):
    """Return the operator-facing content category for a tracked path."""
    normalized = _display_path(path).lower()
    if normalized == '/':
        return 'Home'
    if normalized.startswith('/pricing'):
        return 'Pricing'
    if normalized.startswith('/blog'):
        return 'Blog'
    if normalized.startswith('/workshops'):
        return 'Workshops'
    if normalized.startswith('/courses'):
        return 'Courses'
    if normalized.startswith('/events'):
        return 'Events'
    if normalized.startswith('/resources') or normalized.startswith('/tutorials'):
        return 'Resources'
    if normalized.startswith('/projects'):
        return 'Projects'
    if normalized.startswith('/downloads'):
        return 'Downloads'
    if (
        normalized.startswith('/account')
        or normalized.startswith('/accounts')
        or normalized.startswith('/login')
        or normalized.startswith('/register')
        or normalized.startswith('/subscribe')
        or normalized.startswith('/api/subscribe')
        or normalized.startswith('/api/verify-email')
    ):
        return 'Account/Auth'
    return 'Other'


def serialize_report(report, *, recent_limit=RECENT_PAGE_SIZE):
    """Serialize a report for the staff-token JSON API."""
    filters = report['filters']
    recent = report['recent_signups'][:recent_limit]
    return {
        'filters': {
            'range': filters['range_key'],
            'start': filters['start'].date().isoformat(),
            'end': filters['end'].date().isoformat(),
            'start_at': filters['start'].isoformat(),
            'end_at': filters['end'].isoformat(),
            'signup_path': filters['signup_path'],
            'signup_path_label': (
                SIGNUP_PATH_LABELS.get(filters['signup_path'])
                if filters['signup_path']
                else 'All paths'
            ),
            'limit': recent_limit,
        },
        'headline_cards': report['headline_cards'],
        'window_total': report['window_total'],
        'actionable_source_rows': [
            _serialize_actionable_source_row(row)
            for row in report['actionable_source_rows']
        ],
        'pre_signup_activity_rows': [
            _serialize_activity_row(row)
            for row in report['pre_signup_activity_rows']
        ],
        'recent_signups': [
            _serialize_recent_signup(row)
            for row in recent
        ],
    }


def _window_attributions(filters):
    qs = UserAttribution.objects.filter(
        created_at__gte=filters['start'],
        created_at__lt=filters['end'],
    )
    if filters['signup_path']:
        qs = qs.filter(signup_path=filters['signup_path'])
    return (
        qs.select_related('user', 'first_touch_campaign')
        .order_by('-created_at', '-user_id')
    )


def _campaigns_by_slug(attributions):
    slugs = {
        (attr.first_touch_utm_campaign or '').strip()
        for attr in attributions
        if (attr.first_touch_utm_campaign or '').strip()
    }
    if not slugs:
        return {}
    return {
        campaign.slug: campaign
        for campaign in UtmCampaign.objects.filter(slug__in=slugs)
    }


def _pre_signup_visits_by_user_id(attributions):
    anon_ids = {
        attr.anonymous_id
        for attr in attributions
        if attr.anonymous_id
    }
    if not anon_ids:
        return {}

    latest_signup = max(attr.created_at for attr in attributions)
    visits_by_anon = defaultdict(list)
    visits = (
        CampaignVisit.objects
        .filter(anonymous_id__in=anon_ids, ts__lte=latest_signup)
        .only('anonymous_id', 'path', 'ts')
        .order_by('anonymous_id', 'ts', 'pk')
    )
    for visit in visits:
        visits_by_anon[visit.anonymous_id].append(visit)

    visits_by_user_id = {}
    for attr in attributions:
        if not attr.anonymous_id:
            visits_by_user_id[attr.user_id] = []
            continue
        matched = [
            visit for visit in visits_by_anon.get(attr.anonymous_id, [])
            if visit.ts <= attr.created_at
        ]
        visits_by_user_id[attr.user_id] = matched
    return visits_by_user_id


def _attach_journey_context(attr, source, visits):
    attr.actionable_source = source
    attr.actionable_source_label = source['label']
    attr.actionable_source_kind = source['kind']
    attr.tracked_visit_count = len(visits)
    if not visits:
        attr.first_tracked_landing_path = NO_TRACKED_VISITS_LABEL
        attr.last_tracked_pre_signup_path = NO_TRACKED_VISITS_LABEL
        attr.top_categories = []
        attr.top_categories_label = NO_TRACKED_VISITS_LABEL
        attr.pre_signup_visits = []
        return

    attr.pre_signup_visits = visits
    attr.first_tracked_landing_path = _display_path(visits[0].path)
    attr.last_tracked_pre_signup_path = _display_path(visits[-1].path)
    category_counts = Counter(
        content_category_for_path(visit.path)
        for visit in visits
    )
    top_count = max(category_counts.values())
    top_categories = sorted(
        category for category, count in category_counts.items()
        if count == top_count
    )
    attr.top_categories = top_categories
    attr.top_categories_label = ', '.join(top_categories)


def _signup_path_rows(attributions, total):
    counter = Counter(attr.signup_path for attr in attributions)
    rows = []
    for signup_path, count in _sorted_counts(counter):
        rows.append({
            'signup_path': signup_path,
            'label': SIGNUP_PATH_LABELS.get(signup_path, signup_path),
            'n': count,
            'pct': _pct_share(count, total),
        })
    return rows


def _utm_source_rows(attributions, total):
    counter = Counter(attr.first_touch_utm_source or '' for attr in attributions)
    rows = []
    for source, count in _sorted_counts(counter)[:TOP_N]:
        rows.append({
            'first_touch_utm_source': source,
            'label': source or '(no UTM)',
            'n': count,
            'pct': _pct_share(count, total),
        })
    return rows


def _referrer_rows(attributions, total, *, has_referrer_data=True):
    if not has_referrer_data:
        return []
    counter = Counter(
        getattr(attr, 'first_touch_referrer_source', '') or ''
        for attr in attributions
    )
    rows = []
    for source, count in _sorted_counts(counter)[:TOP_N]:
        rows.append({
            'first_touch_referrer_source': source,
            'label': source or '(direct / no referrer)',
            'n': count,
            'pct': _pct_share(count, total),
        })
    return rows


def _campaign_rows(attributions, total, campaigns_by_slug):
    counter = Counter(attr.first_touch_utm_campaign or '' for attr in attributions)
    rows = []
    for slug, count in _sorted_counts(counter)[:TOP_N]:
        rows.append({
            'first_touch_utm_campaign': slug,
            'slug': slug,
            'label': slug or '(no campaign)',
            'n': count,
            'pct': _pct_share(count, total),
            'campaign': bool(slug and campaigns_by_slug.get(slug)),
        })
    return rows


def _actionable_source_rows(attributions, total):
    grouped = {}
    for attr in attributions:
        source = attr.actionable_source
        row = grouped.setdefault(
            source['key'],
            {
                'key': source['key'],
                'label': source['label'],
                'kind': source['kind'],
                'n': 0,
                'campaign_id': source['campaign_id'],
                'campaign_name': source['campaign_name'],
                'campaign_slug': source['campaign_slug'],
                'has_campaign': source['has_campaign'],
                'signup_paths': Counter(),
                'landing_paths': Counter(),
            },
        )
        row['n'] += 1
        row['signup_paths'][attr.signup_path] += 1
        row['landing_paths'][attr.first_tracked_landing_path] += 1

    rows = []
    for row in grouped.values():
        top_signup_path, _count = _top_counter_item(row['signup_paths'])
        top_landing_path, _landing_count = _top_counter_item(row['landing_paths'])
        row['pct'] = _pct_share(row['n'], total)
        row['top_signup_path'] = top_signup_path
        row['top_signup_path_label'] = SIGNUP_PATH_LABELS.get(
            top_signup_path,
            top_signup_path,
        )
        row['top_landing_path'] = top_landing_path or NO_TRACKED_VISITS_LABEL
        rows.append(row)

    return sorted(rows, key=lambda row: (-row['n'], row['label']))


def _pre_signup_activity_rows(attributions, sources_by_user_id):
    grouped = {}
    seen_source_signup = set()
    for attr in attributions:
        source = sources_by_user_id[attr.user_id]
        for visit in attr.pre_signup_visits:
            path = _display_path(visit.path)
            category = content_category_for_path(path)
            key = (category, path)
            row = grouped.setdefault(
                key,
                {
                    'category': category,
                    'path': path,
                    'signup_ids': set(),
                    'total_tracked_visits': 0,
                    'source_counts': Counter(),
                },
            )
            row['signup_ids'].add(attr.user_id)
            row['total_tracked_visits'] += 1
            source_signup_key = (key, attr.user_id)
            if source_signup_key not in seen_source_signup:
                row['source_counts'][source['label']] += 1
                seen_source_signup.add(source_signup_key)

    rows = []
    for row in grouped.values():
        top_source, _count = _top_counter_item(row['source_counts'])
        rows.append({
            'category': row['category'],
            'path': row['path'],
            'distinct_signup_count': len(row['signup_ids']),
            'total_tracked_visits': row['total_tracked_visits'],
            'top_actionable_source': top_source or DIRECT_SOURCE_LABEL,
        })
    return sorted(
        rows,
        key=lambda row: (
            -row['distinct_signup_count'],
            -row['total_tracked_visits'],
            row['category'],
            row['path'],
        ),
    )


def _serialize_actionable_source_row(row):
    return {
        'label': row['label'],
        'kind': row['kind'],
        'signup_count': row['n'],
        'percent_share': row['pct'],
        'top_signup_path': row['top_signup_path'],
        'top_signup_path_label': row['top_signup_path_label'],
        'top_pre_signup_landing_path': row['top_landing_path'],
        'campaign': (
            {
                'id': row['campaign_id'],
                'name': row['campaign_name'],
                'slug': row['campaign_slug'],
                'studio_url': (
                    f'/studio/utm-analytics/campaign/{row["campaign_slug"]}/'
                ),
            }
            if row['has_campaign']
            else None
        ),
    }


def _serialize_activity_row(row):
    return {
        'category': row['category'],
        'path': row['path'],
        'distinct_signup_count': row['distinct_signup_count'],
        'total_tracked_visits': row['total_tracked_visits'],
        'top_actionable_source': row['top_actionable_source'],
    }


def _serialize_recent_signup(attr):
    return {
        'user_id': attr.user_id,
        'email': attr.user.email if attr.user_id and attr.user else '',
        'user_studio_url': f'/studio/users/{attr.user_id}/' if attr.user_id else '',
        'actionable_source': attr.actionable_source,
        'first_tracked_landing_path': attr.first_tracked_landing_path,
        'last_tracked_pre_signup_path': attr.last_tracked_pre_signup_path,
        'tracked_visit_count': attr.tracked_visit_count,
        'top_categories': attr.top_categories,
        'top_categories_label': attr.top_categories_label,
        'signup_path': attr.signup_path,
        'signup_path_label': attr.get_signup_path_display(),
        'signed_up_at': attr.created_at.isoformat(),
    }


def _referrer_label(attr, host, referrer_source):
    if referrer_source:
        display = attr.get_first_touch_referrer_source_display()
        if display == referrer_source:
            display = referrer_source
    else:
        display = ''

    if display and host and host.lower() not in display.lower():
        return f'{display} ({host})'
    return display or host or DIRECT_SOURCE_LABEL


def _display_path(path):
    value = (path or '').split('?', 1)[0].strip()
    return value or '/'


def _pct_share(count, total):
    if not total:
        return '0.0%'
    return f'{(count / total) * 100:.1f}%'


def _delta(current, prior):
    if current > prior:
        return {'sign': '+', 'diff': current - prior}
    if current < prior:
        return {'sign': '-', 'diff': prior - current}
    return {'sign': '=', 'diff': 0}


def _sorted_counts(counter):
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def _top_counter_item(counter):
    if not counter:
        return '', 0
    return _sorted_counts(counter)[0]


__all__ = [
    'API_RECENT_LIMIT_MAX',
    'DEFAULT_RANGE',
    'DIRECT_SOURCE_LABEL',
    'NO_TRACKED_VISITS_LABEL',
    'RANGE_CHOICES',
    'RECENT_PAGE_SIZE',
    'SIGNUP_PATH_LABELS',
    'SIGNUP_PATH_VALUES',
    'TOP_N',
    'actionable_source_for_attribution',
    'build_signup_analytics_report',
    'content_category_for_path',
    'headline_cards',
    'parse_recent_limit',
    'parse_signup_analytics_filters',
    'querystring',
    'resolve_window',
    'serialize_report',
]
