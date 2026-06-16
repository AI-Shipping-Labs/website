"""Studio Signup Analytics dashboard (issue #770).

Single read-only view that aggregates ``analytics.UserAttribution`` rows to
answer "where are our signups coming from this week?". Sibling of
``utm_analytics`` — same Tracking section in the sidebar — but campaign-
centric (UTM Analytics) vs. signup-centric (this one).

Performance: every section is one annotated ``.values()`` query so the
total page-load query budget stays under 10. No precomputed rollup table.

Soft-dependency on issue #772 (referrer fields on ``UserAttribution``):
section 5 is only populated when ``first_touch_referrer_source`` is
present on the model. When it's missing, the section renders a single
placeholder paragraph and we skip the query entirely. The dashboard does
not fail if #772 hasn't shipped.
"""

from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import render
from django.utils import timezone

from analytics.models import SIGNUP_PATH_CHOICES, UserAttribution
from integrations.models import UtmCampaign
from studio.decorators import staff_required
from studio.utils import coerce_page_number

SIGNUP_PATH_VALUES = [value for value, _label in SIGNUP_PATH_CHOICES]
SIGNUP_PATH_LABELS = dict(SIGNUP_PATH_CHOICES)

DEFAULT_RANGE = '7d'
RANGE_CHOICES = ('24h', '7d', '30d', 'custom')
TOP_N = 10
# Page size for the Recent signups list (Section 7). Matches
# ``/studio/users/`` and ``/studio/ses-events/`` so the canonical pager
# partial reads consistently across Studio.
RECENT_PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Filter parsing
# ---------------------------------------------------------------------------

def _resolve_window(range_key, start_str=None, end_str=None, *, now=None):
    """Resolve a ``(start, end)`` window from filter params.

    Mirrors ``analytics.aggregations.resolve_window`` but adds the ``24h``
    range key. Invalid input falls back to ``7d``.
    """
    if now is None:
        now = timezone.now()

    if range_key == 'custom' and start_str and end_str:
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        except ValueError:
            range_key = DEFAULT_RANGE
        else:
            tz = timezone.get_current_timezone()
            start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
            end = timezone.make_aware(datetime.combine(end_date, time.max), tz)
            return start, end

    if range_key == '24h':
        return now - timedelta(hours=24), now
    days_map = {'7d': 7, '30d': 30}
    days = days_map.get(range_key, 7)
    return now - timedelta(days=days), now


def _parse_filters(request):
    """Pull filter params off the request and resolve the active date window."""
    range_key = request.GET.get('range', DEFAULT_RANGE)
    if range_key not in RANGE_CHOICES:
        range_key = DEFAULT_RANGE
    start_str = request.GET.get('start', '')
    end_str = request.GET.get('end', '')

    signup_path = request.GET.get('signup_path', '').strip()
    if signup_path and signup_path not in SIGNUP_PATH_VALUES:
        signup_path = ''

    start, end = _resolve_window(range_key, start_str, end_str)
    window = end - start
    prior_start = start - window
    prior_end = start

    return {
        'range_key': range_key,
        'start_str': start_str,
        'end_str': end_str,
        'signup_path': signup_path,
        'start': start,
        'end': end,
        'prior_start': prior_start,
        'prior_end': prior_end,
    }


def _querystring(filters):
    """Encode the active filters as ``?key=value&...`` (empty for defaults)."""
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


def _pager_querystring(request, page_number):
    """Build ``?...&page=N`` preserving every other request param.

    Mirrors ``studio.views.ses_events._pager_querystring`` so the canonical
    pager partial keeps the ``range`` / ``start`` / ``end`` / ``signup_path``
    filters alive across page navigation. The leading ``?`` is included so the
    template can drop the value straight into ``href``.
    """
    params = request.GET.copy()
    params['page'] = str(page_number)
    return '?' + params.urlencode()


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def _has_referrer_field():
    """Return True when ``UserAttribution`` has the #772 referrer fields."""
    try:
        UserAttribution._meta.get_field('first_touch_referrer_source')
    except Exception:
        return False
    return True


def _delta(current, prior):
    """Return a sign + diff dict for the headline-card delta chip.

    ``sign`` is one of ``'+'``, ``'-'``, or ``'='``. ``diff`` is the
    absolute difference so the template can render ``+3`` / ``-2`` / ``=``.
    """
    if current > prior:
        return {'sign': '+', 'diff': current - prior}
    if current < prior:
        return {'sign': '-', 'diff': prior - current}
    return {'sign': '=', 'diff': 0}


def _headline_cards(now, *, signup_path=None):
    """Compute all three rolling headline cards in a single SQL query.

    Conditional aggregation over six date ranges (3 current + 3 prior)
    keeps the budget to one query for the whole headline strip rather
    than six. Filter by ``signup_path`` when active.
    """
    base = UserAttribution.objects.all()
    if signup_path:
        base = base.filter(signup_path=signup_path)

    windows = [
        ('24h', '24h', timedelta(hours=24)),
        ('Last 7d', '7d', timedelta(days=7)),
        ('Last 30d', '30d', timedelta(days=30)),
    ]

    aggregates = {}
    for label, key, window in windows:
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

    cards = []
    labels = {'24h': 'Last 24h', '7d': 'Last 7d', '30d': 'Last 30d'}
    for _full_label, key, _window in windows:
        cur = row[f'cur_{key}'] or 0
        prv = row[f'prv_{key}'] or 0
        cards.append({
            'label': labels[key],
            'window_label': key,
            'count': cur,
            'delta': _delta(cur, prv),
        })
    return cards


def _pct_share(count, total):
    """Return a ``'12.5%'`` string for a count out of a total (0%/100% safe)."""
    if not total:
        return '0.0%'
    return f'{(count / total) * 100:.1f}%'


def _annotate_share(rows, total, *, key='n'):
    """Mutate each row dict with a ``pct`` string field for % share rendering."""
    for r in rows:
        r['pct'] = _pct_share(r[key], total)
    return rows


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

@staff_required
def signup_analytics_dashboard(request):
    """Read-only aggregation dashboard over ``UserAttribution``."""
    filters = _parse_filters(request)
    now = timezone.now()

    base_window = UserAttribution.objects.filter(
        created_at__gte=filters['start'], created_at__lt=filters['end'],
    )
    if filters['signup_path']:
        base_window = base_window.filter(signup_path=filters['signup_path'])

    # ---- Section 2: rolling headline cards (always 24h / 7d / 30d) --------
    headline_cards = _headline_cards(now, signup_path=filters['signup_path'])

    # ---- Section 3: breakdown by signup_path -----------------------------
    signup_path_rows = list(
        base_window.values('signup_path')
        .annotate(n=Count('user_id'))
        .order_by('-n')
    )
    # The sum across this breakdown is the total signup count in the
    # window. Reuse it as the % share denominator for sections 3-6 so we
    # don't fire an extra COUNT(*) query.
    window_total = sum(r['n'] for r in signup_path_rows)
    for row in signup_path_rows:
        row['label'] = SIGNUP_PATH_LABELS.get(row['signup_path'], row['signup_path'])
    _annotate_share(signup_path_rows, window_total)

    # ---- Section 4: top first-touch UTM sources --------------------------
    utm_source_rows = list(
        base_window.values('first_touch_utm_source')
        .annotate(n=Count('user_id'))
        .order_by('-n')[:TOP_N]
    )
    for row in utm_source_rows:
        row['label'] = row['first_touch_utm_source'] or '(no UTM)'
    _annotate_share(utm_source_rows, window_total)

    # ---- Section 5: top first-touch referrer sources (gated on #772) -----
    has_referrer_data = _has_referrer_field()
    referrer_rows = []
    if has_referrer_data:
        referrer_rows = list(
            base_window.values('first_touch_referrer_source')
            .annotate(n=Count('user_id'))
            .order_by('-n')[:TOP_N]
        )
        for row in referrer_rows:
            row['label'] = row['first_touch_referrer_source'] or '(direct / no referrer)'
        _annotate_share(referrer_rows, window_total)

    # ---- Section 6: top first-touch campaigns ----------------------------
    # Use Exists() so the "does a UtmCampaign with this slug exist?" check
    # rides on the same query as the breakdown — one SQL round trip
    # instead of two (the breakdown + a separate UtmCampaign lookup).
    campaign_exists = Exists(
        UtmCampaign.objects.filter(slug=OuterRef('first_touch_utm_campaign'))
    )
    campaign_rows = list(
        base_window.values('first_touch_utm_campaign')
        .annotate(n=Count('user_id'), has_campaign=campaign_exists)
        .order_by('-n')[:TOP_N]
    )
    for row in campaign_rows:
        slug = row['first_touch_utm_campaign']
        row['slug'] = slug
        row['label'] = slug or '(no campaign)'
        # ``has_campaign`` is True when a UtmCampaign with this slug exists
        # AND the slug is non-empty (Exists matches blank rows too because
        # both sides are empty strings, so we gate on slug being truthy).
        row['campaign'] = bool(slug) and bool(row['has_campaign'])
    _annotate_share(campaign_rows, window_total)

    # ---- Section 7: recent signups (paginated) ---------------------------
    # Paginate over the filtered, ordered queryset rather than slicing —
    # ``Paginator`` adds exactly one COUNT(*) plus one page slice, keeping
    # the page under the <10 query budget. The queryset is not materialized
    # into a list before paging so the COUNT stays in SQL.
    recent_qs = base_window.select_related('user').order_by('-created_at')
    paginator = Paginator(recent_qs, RECENT_PAGE_SIZE)
    page_number = coerce_page_number(
        request.GET.get('page'), paginator.num_pages or 1,
    )
    page = paginator.page(page_number)
    recent_has_referrer = has_referrer_data

    if page.has_previous():
        first_url = _pager_querystring(request, 1)
        prev_url = _pager_querystring(request, page.previous_page_number())
    else:
        first_url = None
        prev_url = None
    if page.has_next():
        next_url = _pager_querystring(request, page.next_page_number())
        last_url = _pager_querystring(request, paginator.num_pages)
    else:
        next_url = None
        last_url = None
    show_pager = paginator.num_pages > 1

    context = {
        'filters': filters,
        'querystring': _querystring(filters),
        'signup_path_choices': SIGNUP_PATH_CHOICES,
        'range_choices': RANGE_CHOICES,
        'headline_cards': headline_cards,
        'window_total': window_total,
        'signup_path_rows': signup_path_rows,
        'utm_source_rows': utm_source_rows,
        'has_referrer_data': has_referrer_data,
        'referrer_rows': referrer_rows,
        'campaign_rows': campaign_rows,
        'recent_signups': page.object_list,
        'recent_has_referrer': recent_has_referrer,
        'page': page,
        'paginator': paginator,
        'show_pager': show_pager,
        'pager_first_url': first_url,
        'pager_prev_url': prev_url,
        'pager_next_url': next_url,
        'pager_last_url': last_url,
        'page_start_index': page.start_index(),
        'page_end_index': page.end_index(),
        'filtered_total': paginator.count,
    }
    return render(request, 'studio/signup_analytics/dashboard.html', context)


__all__ = ['signup_analytics_dashboard']
