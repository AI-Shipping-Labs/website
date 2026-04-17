"""Studio views for the UTM Analytics dashboard (#196).

Three server-rendered views:

- ``utm_dashboard`` — top-level rollup, one row per campaign with visits
- ``utm_campaign_detail`` — drill-down per ``utm_content`` for a campaign
- ``utm_link_detail`` — visits + conversions audit trail for one link

All three accept the same query params: ``range`` (7d/30d/90d/custom),
``start`` + ``end`` (when range=custom), ``attribution`` (first_touch /
last_touch), ``utm_source``, ``utm_medium``. Filters are preserved
across drill-downs.

No HTMX, no JS chart lib — matches the existing Studio pattern in
``subscribers/list.html`` (GET form, ``onchange="this.form.submit()"``).
Sparklines are inline SVG polylines.
"""

from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from analytics import aggregations
from analytics.models import CampaignVisit
from integrations.models import UtmCampaign, UtmCampaignLink
from studio.decorators import staff_required

# ---------------------------------------------------------------------------
# Filter parsing
# ---------------------------------------------------------------------------

def _parse_filters(request):
    """Pull filter params off the request and resolve the date window."""
    range_key = request.GET.get('range', aggregations.DEFAULT_RANGE)
    if range_key not in aggregations.RANGE_CHOICES:
        range_key = aggregations.DEFAULT_RANGE
    start_str = request.GET.get('start', '')
    end_str = request.GET.get('end', '')
    attribution = request.GET.get('attribution', 'first_touch')
    if attribution not in ('first_touch', 'last_touch'):
        attribution = 'first_touch'
    utm_source = request.GET.get('utm_source', '').strip()
    utm_medium = request.GET.get('utm_medium', '').strip()

    start, end = aggregations.resolve_window(range_key, start_str, end_str)

    return {
        'range_key': range_key,
        'start_str': start_str,
        'end_str': end_str,
        'attribution': attribution,
        'utm_source': utm_source,
        'utm_medium': utm_medium,
        'start': start,
        'end': end,
    }


def _filters_querystring(filters):
    """Encode the active filters as a ``?key=value&...`` string.

    Used to preserve filters when navigating between dashboard, campaign
    drill-down, and link drill-down.
    """
    params = {}
    if filters['range_key'] != aggregations.DEFAULT_RANGE:
        params['range'] = filters['range_key']
    if filters['range_key'] == 'custom':
        if filters['start_str']:
            params['start'] = filters['start_str']
        if filters['end_str']:
            params['end'] = filters['end_str']
    if filters['attribution'] != 'first_touch':
        params['attribution'] = filters['attribution']
    if filters['utm_source']:
        params['utm_source'] = filters['utm_source']
    if filters['utm_medium']:
        params['utm_medium'] = filters['utm_medium']
    if not params:
        return ''
    return '?' + urlencode(params)


def _sparkline_for(start, end, *, campaign_slug=None, utm_content=None,
                   utm_source=None, utm_medium=None, attribution='first_touch'):
    """Return an inline-SVG-ready dict with visit + signup polyline points."""
    visits = aggregations.daily_buckets(
        start, end, metric='visits',
        campaign_slug=campaign_slug, utm_content=utm_content,
        utm_source=utm_source, utm_medium=utm_medium,
    )
    signups = aggregations.daily_buckets(
        start, end, metric='signups',
        campaign_slug=campaign_slug, utm_content=utm_content,
        utm_source=utm_source, utm_medium=utm_medium,
        attribution=attribution,
    )
    visit_counts = [c for _, c in visits]
    signup_counts = [c for _, c in signups]
    max_v = max(visit_counts + signup_counts) if (visit_counts or signup_counts) else 0
    return {
        'visits_points': aggregations.sparkline_polyline(visits),
        'signups_points': aggregations.sparkline_polyline(signups),
        'has_data': max_v > 0,
    }


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------

@staff_required
def utm_dashboard(request):
    """Top-level rollup: one row per campaign with visits in the window."""
    filters = _parse_filters(request)
    common = dict(
        utm_source=filters['utm_source'] or None,
        utm_medium=filters['utm_medium'] or None,
        attribution=filters['attribution'],
    )

    kpis = aggregations.kpi_strip(filters['start'], filters['end'], **common)
    rows = aggregations.campaign_rollup(filters['start'], filters['end'], **common)

    # Resolve UtmCampaign records for slugs that exist in the table so we
    # can show the human-readable name and link to the builder for "Edit
    # campaign". Slugs without a matching UtmCampaign are still shown
    # (visits with stale / unknown utm_campaign values).
    campaign_map = {
        c.slug: c for c in UtmCampaign.objects.filter(slug__in=[r['slug'] for r in rows])
    }
    for r in rows:
        camp = campaign_map.get(r['slug'])
        r['campaign'] = camp
        r['name'] = camp.name if camp else r['slug']
        r['sparkline'] = _sparkline_for(
            filters['start'], filters['end'],
            campaign_slug=r['slug'],
            **common,
        )

    options = aggregations.filter_options(filters['start'], filters['end'])

    context = {
        'filters': filters,
        'querystring': _filters_querystring(filters),
        'kpis': kpis,
        'rows': rows,
        'has_conversion_data': aggregations.has_conversion_data(),
        'source_options': options['sources'],
        'medium_options': options['mediums'],
        'is_dashboard': True,
    }
    return render(request, 'studio/utm_analytics/dashboard.html', context)


# ---------------------------------------------------------------------------
# Campaign drill-down view
# ---------------------------------------------------------------------------

@staff_required
def utm_campaign_detail(request, campaign_slug):
    """Drill-down per ``utm_content`` for a single campaign.

    Uses the slug rather than the PK so the URL stays meaningful when a
    campaign exists in visits but not in ``UtmCampaign`` (e.g. a UTM
    parameter shipped with a typo). When the slug doesn't match any
    ``UtmCampaign`` row, ``campaign`` is None and we render with the
    slug as the breadcrumb label.
    """
    filters = _parse_filters(request)
    common = dict(
        utm_source=filters['utm_source'] or None,
        utm_medium=filters['utm_medium'] or None,
        attribution=filters['attribution'],
    )

    campaign = UtmCampaign.objects.filter(slug=campaign_slug).first()

    kpis = aggregations.kpi_strip(
        filters['start'], filters['end'],
        campaign_slug=campaign_slug, **common,
    )
    rows = aggregations.utm_content_rollup(
        filters['start'], filters['end'],
        campaign_slug=campaign_slug, **common,
    )

    # Map utm_content -> UtmCampaignLink (so we can show label + Actions)
    link_map = {}
    if campaign:
        for link in campaign.links.all():
            link_map[link.utm_content] = link
    for r in rows:
        link = link_map.get(r['utm_content'])
        r['link'] = link
        r['label'] = link.label if link else ''
        r['destination'] = link.destination if link else ''
        r['sparkline'] = _sparkline_for(
            filters['start'], filters['end'],
            campaign_slug=campaign_slug,
            utm_content=r['utm_content'],
            **common,
        )

    options = aggregations.filter_options(filters['start'], filters['end'])

    context = {
        'filters': filters,
        'querystring': _filters_querystring(filters),
        'campaign': campaign,
        'campaign_slug': campaign_slug,
        'campaign_name': campaign.name if campaign else campaign_slug,
        'kpis': kpis,
        'rows': rows,
        'has_conversion_data': aggregations.has_conversion_data(),
        'source_options': options['sources'],
        'medium_options': options['mediums'],
    }
    return render(request, 'studio/utm_analytics/campaign_detail.html', context)


# ---------------------------------------------------------------------------
# Link drill-down view
# ---------------------------------------------------------------------------

@staff_required
def utm_link_detail(request, campaign_slug, link_id):
    """Visits + conversions audit trail for a single ``UtmCampaignLink``."""
    filters = _parse_filters(request)
    common = dict(
        utm_source=filters['utm_source'] or None,
        utm_medium=filters['utm_medium'] or None,
        attribution=filters['attribution'],
    )

    campaign = get_object_or_404(UtmCampaign, slug=campaign_slug)
    link = get_object_or_404(UtmCampaignLink, pk=link_id, campaign=campaign)

    kpis = aggregations.kpi_strip(
        filters['start'], filters['end'],
        campaign_slug=campaign_slug, utm_content=link.utm_content,
        **common,
    )

    visit_qs = (
        CampaignVisit.objects
        .filter(
            ts__gte=filters['start'],
            ts__lte=filters['end'],
            utm_campaign=campaign_slug,
            utm_content=link.utm_content,
        )
        .select_related('user')
        .order_by('-ts')
    )
    if filters['utm_source']:
        visit_qs = visit_qs.filter(utm_source=filters['utm_source'])
    if filters['utm_medium']:
        visit_qs = visit_qs.filter(utm_medium=filters['utm_medium'])

    paginator = Paginator(visit_qs, 20)
    page_number = request.GET.get('page', 1)
    try:
        visits_page = paginator.page(page_number)
    except Exception:
        visits_page = paginator.page(1)

    # Build URL prefixes for prev/next pagination links that preserve filters.
    base_qs = _filters_querystring(filters)
    if base_qs:
        page_link_prefix = f'{base_qs}&page='
    else:
        page_link_prefix = '?page='

    # Conversions table — one row per signed-up user attributed to this link
    signups = aggregations.signups_for(
        filters['start'], filters['end'],
        campaign_slug=campaign_slug,
        utm_content=link.utm_content,
        utm_source=filters['utm_source'] or None,
        utm_medium=filters['utm_medium'] or None,
        attribution=filters['attribution'],
    ).select_related('user', 'user__tier')

    conversion_rows = []
    for sa in signups:
        user = sa.user
        # Did this user pay? Look up the most recent ConversionAttribution
        # if the model exists — graceful no-op when payments hasn't shipped.
        paid = False
        mrr = None
        model = aggregations._conversion_model()
        if model is not None and user is not None:
            ca = model.objects.filter(user=user).order_by('-created_at').first()
            if ca is not None:
                paid = bool(ca.tier_id)
                mrr = ca.mrr_eur
        conversion_rows.append({
            'user': user,
            'email': user.email if user else '',
            'signup_ts': getattr(sa, aggregations._attribution_field(filters['attribution'], 'ts')),
            'tier': user.tier if user and user.tier_id else None,
            'paid': paid,
            'mrr': mrr or 0,
        })

    context = {
        'filters': filters,
        'querystring': _filters_querystring(filters),
        'campaign': campaign,
        'link': link,
        'kpis': kpis,
        'visits_page': visits_page,
        'paginator': paginator,
        'page_link_prefix': page_link_prefix,
        'conversion_rows': conversion_rows,
        'has_conversion_data': aggregations.has_conversion_data(),
        'assembled_url': link.build_url(),
    }
    return render(request, 'studio/utm_analytics/link_detail.html', context)


__all__ = ['utm_dashboard', 'utm_campaign_detail', 'utm_link_detail']
