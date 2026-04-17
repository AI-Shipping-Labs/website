"""On-demand SQL helpers for the Studio UTM Analytics dashboard (#196).

All metrics on the UTM Analytics surface go through this module so the
three views (`utm_dashboard`, `utm_campaign_detail`, `utm_link_detail`)
share logic and can be unit-tested in isolation.

Performance: aggregate queries with `.values().annotate(Count(...))`
over indexed columns (`CampaignVisit.utm_campaign`, `CampaignVisit.ts`,
`UserAttribution.first_touch_utm_campaign`,
`UserAttribution.first_touch_ts`, `ConversionAttribution.created_at`,
`ConversionAttribution.first_touch_utm_campaign`) keep us well under
100ms for low-thousand-row date windows. If any single rollup grows past
~500ms in production, replace it with a precomputed daily rollup table
written by a `compute_utm_rollups` management command — TODO until that
day comes.

The helpers handle the case where ``ConversionAttribution`` is absent
(``has_conversion_data`` returns False before #195 was merged) by
returning empty querysets / 0 / Decimal('0'). The dashboard hides MRR
and Paid columns in that case.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from analytics.models import CampaignVisit, UserAttribution

# Module-top import of ConversionAttribution. The `analytics` app does not
# formally depend on `payments`, so we wrap the import in a try/except so
# this module stays importable even if `payments` is uninstalled. The
# `_conversion_model()` and `has_conversion_data()` helpers below check
# this binding so the dashboard degrades gracefully when the model is
# absent (the "ship-before-#195" stage).
try:
    from payments.models import ConversionAttribution as _ConversionAttribution
except ImportError:  # pragma: no cover — payments is always installed today
    _ConversionAttribution = None


def has_conversion_data():
    """Whether the ConversionAttribution model is importable.

    Returns False before payments issue #195 has shipped — used by the
    dashboard to hide the Paid Conversions and MRR columns + KPI cards.
    Today this returns True (the model exists), but we keep the check
    behind a function so the dashboard degrades gracefully if the model
    ever gets removed.
    """
    return _ConversionAttribution is not None


def _conversion_model():
    """Return the ``ConversionAttribution`` model class, or None if absent."""
    return _ConversionAttribution


# ---------------------------------------------------------------------------
# Date window helpers
# ---------------------------------------------------------------------------

DEFAULT_RANGE = '30d'
RANGE_CHOICES = ('7d', '30d', '90d', 'custom')


def resolve_window(range_key, start_str=None, end_str=None, *, now=None):
    """Resolve a ``(start, end)`` datetime tuple from filter params.

    ``range_key`` is one of ``7d``, ``30d``, ``90d``, ``custom``. For
    ``custom`` the ISO date strings ``start_str`` and ``end_str`` are
    parsed (YYYY-MM-DD); end is shifted to end-of-day so the day is
    inclusive.

    Falls back to the default 30-day window if any input is invalid.
    Returns timezone-aware datetimes in the current TZ (UTC by default).
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

    days_map = {'7d': 7, '30d': 30, '90d': 90}
    days = days_map.get(range_key, 30)
    return now - timedelta(days=days), now


# ---------------------------------------------------------------------------
# Visits
# ---------------------------------------------------------------------------

def visits_for(start, end, *, campaign_slug=None, utm_content=None,
               utm_source=None, utm_medium=None):
    """Return a CampaignVisit queryset filtered by the given parameters."""
    qs = CampaignVisit.objects.filter(ts__gte=start, ts__lte=end)
    if campaign_slug:
        qs = qs.filter(utm_campaign=campaign_slug)
    if utm_content:
        qs = qs.filter(utm_content=utm_content)
    if utm_source:
        qs = qs.filter(utm_source=utm_source)
    if utm_medium:
        qs = qs.filter(utm_medium=utm_medium)
    return qs


def visit_count(start, end, **filters):
    return visits_for(start, end, **filters).count()


def unique_visitor_count(start, end, **filters):
    return (
        visits_for(start, end, **filters)
        .order_by()  # clear default `-ts` ordering so distinct() is on anonymous_id only
        .values('anonymous_id')
        .distinct()
        .count()
    )


def distinct_utm_values(start, end, field, **filters):
    """Return a sorted list of distinct non-empty values for a UTM field."""
    qs = visits_for(start, end, **filters)
    values = (
        qs.exclude(**{field: ''})
        .order_by()  # clear default `-ts` ordering so distinct() is on `field` only
        .values_list(field, flat=True)
        .distinct()
    )
    return sorted(values)


# ---------------------------------------------------------------------------
# Signups (UserAttribution)
# ---------------------------------------------------------------------------

def _attribution_field(attribution, suffix):
    """Return either ``first_touch_<suffix>`` or ``last_touch_<suffix>``."""
    prefix = 'last_touch_' if attribution == 'last_touch' else 'first_touch_'
    return prefix + suffix


def signups_for(start, end, *, campaign_slug=None, utm_content=None,
                utm_source=None, utm_medium=None, attribution='first_touch'):
    """Return a UserAttribution queryset filtered by the given parameters.

    For ``attribution='last_touch'`` the queries swap to the
    ``last_touch_*`` fields (and ``last_touch_ts`` for the date window).
    """
    ts_field = _attribution_field(attribution, 'ts')
    campaign_field = _attribution_field(attribution, 'utm_campaign')
    content_field = _attribution_field(attribution, 'utm_content')
    source_field = _attribution_field(attribution, 'utm_source')
    medium_field = _attribution_field(attribution, 'utm_medium')

    qs = UserAttribution.objects.filter(
        **{f'{ts_field}__gte': start, f'{ts_field}__lte': end}
    )
    if campaign_slug:
        qs = qs.filter(**{campaign_field: campaign_slug})
    if utm_content:
        qs = qs.filter(**{content_field: utm_content})
    if utm_source:
        qs = qs.filter(**{source_field: utm_source})
    if utm_medium:
        qs = qs.filter(**{medium_field: utm_medium})
    return qs


def signup_count(start, end, **filters):
    return signups_for(start, end, **filters).count()


# ---------------------------------------------------------------------------
# Conversions + MRR (ConversionAttribution)
# ---------------------------------------------------------------------------

def conversions_for(start, end, *, campaign_slug=None, utm_content=None,
                    utm_source=None, utm_medium=None,
                    attribution='first_touch'):
    """Return a ConversionAttribution queryset, or .none() if model absent."""
    model = _conversion_model()
    if model is None:
        return CampaignVisit.objects.none()  # consistent .count() / iteration

    campaign_field = _attribution_field(attribution, 'utm_campaign')
    content_field = _attribution_field(attribution, 'utm_content')
    source_field = _attribution_field(attribution, 'utm_source')
    medium_field = _attribution_field(attribution, 'utm_medium')

    qs = model.objects.filter(created_at__gte=start, created_at__lte=end)
    if campaign_slug:
        qs = qs.filter(**{campaign_field: campaign_slug})
    if utm_content:
        qs = qs.filter(**{content_field: utm_content})
    if utm_source:
        qs = qs.filter(**{source_field: utm_source})
    if utm_medium:
        qs = qs.filter(**{medium_field: utm_medium})
    return qs


def conversion_count(start, end, **filters):
    qs = conversions_for(start, end, **filters)
    if not hasattr(qs, 'count'):
        return 0
    return qs.count()


def mrr_for(start, end, **filters):
    """Sum of monthly-equivalent EUR for matching conversions.

    Uses the per-row ``mrr_eur`` value computed at conversion time
    (``payments.services._record_conversion_attribution`` already
    normalises yearly to monthly by dividing by 12). One-off course
    purchases have ``mrr_eur=NULL`` and are excluded.

    Returns ``Decimal('0')`` when there are no rows so the dashboard
    never has to handle ``None``.
    """
    qs = conversions_for(start, end, **filters)
    if _conversion_model() is None:
        return Decimal('0')
    total = 0
    for amount in qs.values_list('mrr_eur', flat=True):
        if amount is not None:
            total += int(amount)
    return Decimal(total)


# ---------------------------------------------------------------------------
# Conversion-rate formatter
# ---------------------------------------------------------------------------

def conversion_rate(numerator, denominator):
    """Format a percentage as ``"12.3%"`` or ``"n/a"`` when denom is zero.

    Per acceptance criterion: never show ``"0.0%"`` when there is no
    data — show ``"n/a"`` so the empty-data state is unambiguous.
    """
    if not denominator:
        return 'n/a'
    pct = (numerator / denominator) * 100
    return f'{pct:.1f}%'


# ---------------------------------------------------------------------------
# Daily buckets (sparkline data)
# ---------------------------------------------------------------------------

def daily_buckets(start, end, *, metric='visits', campaign_slug=None,
                  utm_content=None, utm_source=None, utm_medium=None,
                  attribution='first_touch'):
    """Return a list of ``(date, count)`` for the given metric.

    Always returns one entry per day in the [start, end] window so the
    sparkline polyline has consistent x-axis spacing even on zero-count
    days. ``metric`` is one of ``visits``, ``signups``.
    """
    if metric == 'signups':
        qs = signups_for(
            start, end,
            campaign_slug=campaign_slug,
            utm_content=utm_content,
            utm_source=utm_source,
            utm_medium=utm_medium,
            attribution=attribution,
        )
        ts_field = _attribution_field(attribution, 'ts')
    else:
        qs = visits_for(
            start, end,
            campaign_slug=campaign_slug,
            utm_content=utm_content,
            utm_source=utm_source,
            utm_medium=utm_medium,
        )
        ts_field = 'ts'

    raw = qs.extra(select={'day': f'DATE({ts_field})'}).values('day').annotate(c=Count('*'))
    # Some backends return strings, some date objects.
    by_day = {}
    for row in raw:
        day = row['day']
        if isinstance(day, str):
            try:
                day = datetime.strptime(day, '%Y-%m-%d').date()
            except ValueError:
                continue
        by_day[day] = row['c']

    out = []
    cursor = start.date()
    end_date = end.date()
    while cursor <= end_date:
        out.append((cursor, by_day.get(cursor, 0)))
        cursor += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Per-campaign rollup (one row per Campaign with at least one visit)
# ---------------------------------------------------------------------------

def campaign_rollup(start, end, *, utm_source=None, utm_medium=None,
                    attribution='first_touch'):
    """Return a list of dicts, one per campaign with visits in window.

    Each dict has the keys used by the dashboard table: ``slug``,
    ``visits``, ``unique_visitors``, ``signups``, ``conversions``,
    ``mrr``, ``visit_to_signup_pct``, ``signup_to_paid_pct``,
    ``utm_source``, ``utm_medium``. Rows are sorted by ``visits`` desc.
    """
    visit_qs = visits_for(start, end, utm_source=utm_source, utm_medium=utm_medium)
    rows = (
        visit_qs.exclude(utm_campaign='')
        .values('utm_campaign')
        .annotate(
            visits=Count('id'),
            unique_visitors=Count('anonymous_id', distinct=True),
        )
        .order_by('-visits')
    )

    out = []
    for row in rows:
        slug = row['utm_campaign']
        signups = signup_count(
            start, end,
            campaign_slug=slug,
            utm_source=utm_source,
            utm_medium=utm_medium,
            attribution=attribution,
        )
        conversions = conversion_count(
            start, end,
            campaign_slug=slug,
            utm_source=utm_source,
            utm_medium=utm_medium,
            attribution=attribution,
        )
        mrr = mrr_for(
            start, end,
            campaign_slug=slug,
            utm_source=utm_source,
            utm_medium=utm_medium,
            attribution=attribution,
        )

        # Distinct source / medium values for visits to this campaign
        sources = sorted(
            visit_qs.filter(utm_campaign=slug)
            .exclude(utm_source='')
            .order_by()
            .values_list('utm_source', flat=True)
            .distinct()
        )
        mediums = sorted(
            visit_qs.filter(utm_campaign=slug)
            .exclude(utm_medium='')
            .order_by()
            .values_list('utm_medium', flat=True)
            .distinct()
        )

        out.append({
            'slug': slug,
            'visits': row['visits'],
            'unique_visitors': row['unique_visitors'],
            'signups': signups,
            'conversions': conversions,
            'mrr': mrr,
            'visit_to_signup_pct': conversion_rate(signups, row['unique_visitors']),
            'signup_to_paid_pct': conversion_rate(conversions, signups),
            'utm_sources': sources,
            'utm_mediums': mediums,
        })
    return out


def utm_content_rollup(start, end, *, campaign_slug, utm_source=None,
                       utm_medium=None, attribution='first_touch'):
    """One row per ``utm_content`` value within a single campaign."""
    visit_qs = visits_for(
        start, end,
        campaign_slug=campaign_slug,
        utm_source=utm_source,
        utm_medium=utm_medium,
    )
    rows = (
        visit_qs.values('utm_content')
        .annotate(
            visits=Count('id'),
            unique_visitors=Count('anonymous_id', distinct=True),
        )
        .order_by('-visits')
    )

    out = []
    for row in rows:
        content = row['utm_content']
        # Pass blank utm_content through as exact match to count "no-content" visits.
        signups = signup_count(
            start, end,
            campaign_slug=campaign_slug,
            utm_content=content,
            utm_source=utm_source,
            utm_medium=utm_medium,
            attribution=attribution,
        )
        conversions = conversion_count(
            start, end,
            campaign_slug=campaign_slug,
            utm_content=content,
            utm_source=utm_source,
            utm_medium=utm_medium,
            attribution=attribution,
        )
        mrr = mrr_for(
            start, end,
            campaign_slug=campaign_slug,
            utm_content=content,
            utm_source=utm_source,
            utm_medium=utm_medium,
            attribution=attribution,
        )
        out.append({
            'utm_content': content,
            'visits': row['visits'],
            'unique_visitors': row['unique_visitors'],
            'signups': signups,
            'conversions': conversions,
            'mrr': mrr,
            'visit_to_signup_pct': conversion_rate(signups, row['unique_visitors']),
            'signup_to_paid_pct': conversion_rate(conversions, signups),
        })
    return out


# ---------------------------------------------------------------------------
# KPI strip helper
# ---------------------------------------------------------------------------

def kpi_strip(start, end, *, campaign_slug=None, utm_content=None,
              utm_source=None, utm_medium=None, attribution='first_touch'):
    """Return the KPI-card numbers used by all three views."""
    common = dict(
        campaign_slug=campaign_slug,
        utm_content=utm_content,
        utm_source=utm_source,
        utm_medium=utm_medium,
    )
    visits = visit_count(start, end, **common)
    uniques = unique_visitor_count(start, end, **common)
    signups = signup_count(start, end, attribution=attribution, **common)
    conversions = conversion_count(start, end, attribution=attribution, **common)
    mrr = mrr_for(start, end, attribution=attribution, **common)
    return {
        'visits': visits,
        'unique_visitors': uniques,
        'signups': signups,
        'conversions': conversions,
        'mrr': mrr,
    }


# ---------------------------------------------------------------------------
# Filter dropdown helpers (distinct source / medium from window)
# ---------------------------------------------------------------------------

def filter_options(start, end):
    """Distinct source / medium values seen in the date window."""
    base_qs = CampaignVisit.objects.filter(ts__gte=start, ts__lte=end)
    # `.order_by()` clears the model's default `ordering = ['-ts']`,
    # otherwise `.distinct()` would include `ts` in the SELECT and we'd
    # get one row per (source, ts) pair instead of distinct sources.
    sources = sorted(
        base_qs.exclude(utm_source='')
        .order_by()
        .values_list('utm_source', flat=True)
        .distinct()
    )
    mediums = sorted(
        base_qs.exclude(utm_medium='')
        .order_by()
        .values_list('utm_medium', flat=True)
        .distinct()
    )
    return {'sources': sources, 'mediums': mediums}


# ---------------------------------------------------------------------------
# Sparkline path generator
# ---------------------------------------------------------------------------

def sparkline_polyline(buckets, *, width=120, height=24):
    """Return an SVG `points` string for the given (date, count) buckets.

    Empty / single-bucket data returns ''. The caller is responsible for
    wrapping the polyline in ``<svg>...</svg>``.
    """
    if not buckets or len(buckets) < 2:
        return ''
    counts = [c for _, c in buckets]
    max_v = max(counts)
    if max_v == 0:
        max_v = 1
    n = len(buckets) - 1
    points = []
    for i, (_, c) in enumerate(buckets):
        x = (i / n) * width
        y = height - (c / max_v) * height
        points.append(f'{x:.1f},{y:.1f}')
    return ' '.join(points)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    'DEFAULT_RANGE',
    'RANGE_CHOICES',
    'campaign_rollup',
    'conversion_count',
    'conversion_rate',
    'conversions_for',
    'daily_buckets',
    'distinct_utm_values',
    'filter_options',
    'has_conversion_data',
    'kpi_strip',
    'mrr_for',
    'resolve_window',
    'signup_count',
    'signups_for',
    'sparkline_polyline',
    'unique_visitor_count',
    'utm_content_rollup',
    'visit_count',
    'visits_for',
]
