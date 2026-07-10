"""Studio views for the read-only SES events browser (issue #763).

Surfaces ``email_app.SesEvent`` rows so operators can answer
"did our webhook process this bounce / complaint, and did it unsubscribe
the user?" without dropping to the Django admin or a shell.

Two views live here:

* ``ses_event_list`` — paginated, filterable list at ``/studio/ses-events/``.
* ``ses_event_detail`` — single-row inspector at ``/studio/ses-events/<pk>/``.

Both views are read-only. The only writer for ``SesEvent`` is the
``/api/ses-events`` webhook (``api/views/ses_events.py``). The follow-up
JSON API (issue #764) will reuse the shared queryset builder below.

The empty-state for the "no rows ever received" case (``kind='fresh'``)
passes ``create_url=None`` because this surface has no create action.
The canonical helper at ``templates/studio/includes/empty_state.html``
already hides the CTA when ``create_url`` is falsy, so no helper change
was needed.
"""

import datetime
import json

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from email_app.models import EmailCampaign
from email_app.models.ses_event import SesEvent
from studio.decorators import staff_required
from studio.utils import coerce_page_number

# Meta filter values used by the type chips. ``TYPE_FILTER_ALL`` is the
# no-op filter; ``TYPE_FILTER_OTHER`` maps to the bucket of event types
# that aren't a primary bounce/complaint/delivery surface.
TYPE_FILTER_ALL = 'all'
TYPE_FILTER_OTHER = 'other'

# Raw event_type values that the ``Other`` chip rolls up. Anything not in
# the primary chip set lands here — kept explicit (vs. negation) so the
# bucket is stable across future EVENT_TYPE_CHOICES additions.
OTHER_EVENT_TYPES = [
    SesEvent.EVENT_TYPE_BOUNCE_OTHER,
    SesEvent.EVENT_TYPE_OPEN,
    SesEvent.EVENT_TYPE_CLICK,
    SesEvent.EVENT_TYPE_SUBSCRIPTION_CONFIRMATION,
    SesEvent.EVENT_TYPE_UNSUBSCRIBE_CONFIRMATION,
    SesEvent.EVENT_TYPE_OTHER,
]

# Allowed values for the ``?type=`` query parameter. Anything else falls
# through to ``TYPE_FILTER_ALL``.
VALID_TYPE_FILTERS = {
    TYPE_FILTER_ALL,
    TYPE_FILTER_OTHER,
    *(choice for choice, _label in SesEvent.EVENT_TYPE_CHOICES),
}

# Pill colour palette for the Type column. Keys are raw ``event_type``
# values; ``_default`` is the fallback. The neutral grey is the same one
# the design-system uses for archived/completed pills so the visual
# hierarchy matches.
EVENT_TYPE_PILL_CLASSES = {
    SesEvent.EVENT_TYPE_BOUNCE_PERMANENT: 'bg-red-500/20 text-red-400',
    SesEvent.EVENT_TYPE_COMPLAINT: 'bg-red-500/20 text-red-400',
    SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT: 'bg-amber-500/20 text-amber-300',
    SesEvent.EVENT_TYPE_BOUNCE_OTHER: 'bg-amber-500/20 text-amber-300',
    SesEvent.EVENT_TYPE_DELIVERY: 'bg-secondary text-muted-foreground',
}
EVENT_TYPE_PILL_DEFAULT = 'bg-secondary text-muted-foreground'

# Page size for the SES events list. PM ruling: 50 rows, same as
# ``/studio/users/`` so the canonical pager partial reads consistently.
SES_EVENT_PAGE_SIZE = 50


def _parse_iso_date(raw):
    """Parse an ISO ``YYYY-MM-DD`` date or return ``None`` on garbage.

    Invalid input is silently ignored per the spec — operators do not want
    a 422 on a typo when they're in the middle of triage. ``None`` is also
    returned for blank strings so the caller can keep the no-filter branch
    one ``if`` away.
    """
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _normalize_type_filter(raw):
    """Coerce the ``?type=`` value to a known meta or event_type."""
    if raw in VALID_TYPE_FILTERS:
        return raw
    return TYPE_FILTER_ALL


def _apply_filters(queryset, *, search, type_filter, bounce_type,
                   bounce_subtype, since, until, campaign_id=None):
    """Apply every query-string filter to the base SES-event queryset.

    Extracted so the upcoming JSON API (issue #764) can lift this helper
    into a shared module without re-deriving the chip / search semantics.
    """
    if search:
        queryset = queryset.filter(recipient_email__icontains=search)

    if type_filter == TYPE_FILTER_OTHER:
        queryset = queryset.filter(event_type__in=OTHER_EVENT_TYPES)
    elif type_filter != TYPE_FILTER_ALL:
        queryset = queryset.filter(event_type=type_filter)

    if bounce_type and bounce_type != TYPE_FILTER_ALL:
        queryset = queryset.filter(bounce_type=bounce_type)

    if bounce_subtype and bounce_subtype != TYPE_FILTER_ALL:
        queryset = queryset.filter(bounce_subtype=bounce_subtype)

    if since is not None:
        queryset = queryset.filter(received_at__date__gte=since)

    if until is not None:
        queryset = queryset.filter(received_at__date__lte=until)

    if campaign_id is not None:
        queryset = queryset.filter(email_log__campaign_id=campaign_id)

    return queryset


def _pager_querystring(request, page_number):
    """Build ``?...&page=N`` preserving every other request param.

    Mirrors ``studio.views.users._pager_querystring`` so the canonical
    pager partial keeps every chip / search / date filter alive across
    navigation. The leading ``?`` is included so the template can drop
    the value straight into ``href``.
    """
    params = request.GET.copy()
    params['page'] = str(page_number)
    return '?' + params.urlencode()


def _last_30_day_counts():
    """Compute the three small headline counters shown above the chips.

    These counters intentionally ignore the active filter — they exist to
    give operators a baseline ("are we getting bounces at all?") rather
    than a filtered view (which the page total already shows).
    """
    cutoff = timezone.now() - datetime.timedelta(days=30)
    counts = SesEvent.objects.filter(received_at__gte=cutoff).aggregate(
        bounce_permanent=Count(
            'pk',
            filter=Q(event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT),
        ),
        complaint=Count(
            'pk',
            filter=Q(event_type=SesEvent.EVENT_TYPE_COMPLAINT),
        ),
        total=Count('pk'),
    )
    return {
        'last_30d_bounce_permanent': counts['bounce_permanent'] or 0,
        'last_30d_complaint': counts['complaint'] or 0,
        'last_30d_total': counts['total'] or 0,
    }


@staff_required
def ses_event_list(request):
    """Render the filterable SES events list at ``/studio/ses-events/``."""
    search = request.GET.get('q', '').strip()
    type_filter = _normalize_type_filter(request.GET.get('type', ''))
    bounce_type = request.GET.get('bounce_type', '').strip()
    bounce_subtype = request.GET.get('bounce_subtype', '').strip()
    raw_campaign_id = request.GET.get('campaign', '').strip()
    since = _parse_iso_date(request.GET.get('since', ''))
    until = _parse_iso_date(request.GET.get('until', ''))
    campaign = None
    campaign_id = None
    if raw_campaign_id:
        try:
            campaign_id = int(raw_campaign_id)
        except (TypeError, ValueError):
            campaign_id = None
        if campaign_id is not None:
            campaign = EmailCampaign.objects.filter(pk=campaign_id).first()

    base_queryset = (
        SesEvent.objects
        .select_related('user', 'email_log', 'email_log__campaign')
        .order_by('-received_at')
    )
    filtered = _apply_filters(
        base_queryset,
        search=search,
        type_filter=type_filter,
        bounce_type=bounce_type,
        bounce_subtype=bounce_subtype,
        since=since,
        until=until,
        campaign_id=campaign.pk if campaign is not None else campaign_id,
    )

    paginator = Paginator(filtered, SES_EVENT_PAGE_SIZE)
    page_number = coerce_page_number(
        request.GET.get('page'), paginator.num_pages or 1,
    )
    page = paginator.page(page_number)

    # Attach the pill class to each row in the page so the template stays
    # declarative — keeps the colour-palette decision in Python where it
    # can be unit-tested.
    rows = []
    for event in page.object_list:
        rows.append({
            'event': event,
            'pill_class': EVENT_TYPE_PILL_CLASSES.get(
                event.event_type, EVENT_TYPE_PILL_DEFAULT,
            ),
        })

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

    # ``has_any_event`` distinguishes the "fresh install, no rows ever"
    # empty state from the "filter produced zero rows" empty state. We
    # need a separate existence check because the filtered queryset is
    # the one driving the empty path.
    has_any_event = SesEvent.objects.exists()

    # Counters strip — independent of the active filter.
    headline_counts = _last_30_day_counts()

    # Chip set in render order; the template iterates this so the chip
    # row is data-driven and the active state lights up on equality.
    type_chips = [
        (TYPE_FILTER_ALL, 'All'),
        (SesEvent.EVENT_TYPE_BOUNCE_PERMANENT, 'Bounce (permanent)'),
        (SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT, 'Bounce (transient)'),
        (SesEvent.EVENT_TYPE_COMPLAINT, 'Complaint'),
        (SesEvent.EVENT_TYPE_DELIVERY, 'Delivery'),
        (TYPE_FILTER_OTHER, 'Other'),
    ]

    # Bounce-type secondary filter; ``All`` matches every bounce_type.
    # Hidden in the UI unless the operator opens the "More filters"
    # expander or already has a value set on the URL.
    bounce_type_choices = [
        ('', 'Any'),
        ('Permanent', 'Permanent'),
        ('Transient', 'Transient'),
        ('Undetermined', 'Undetermined'),
    ]

    return render(request, 'studio/ses_events/list.html', {
        'rows': rows,
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
        'search': search,
        'type_filter': type_filter,
        'bounce_type': bounce_type,
        'bounce_subtype': bounce_subtype,
        'since': request.GET.get('since', ''),
        'until': request.GET.get('until', ''),
        'type_chips': type_chips,
        'bounce_type_choices': bounce_type_choices,
        'has_any_event': has_any_event,
        'more_filters_open': bool(
            bounce_type or bounce_subtype or since or until or raw_campaign_id,
        ),
        'campaign': campaign,
        'campaign_id': raw_campaign_id,
        **headline_counts,
    })


@staff_required
def ses_event_detail(request, pk):
    """Render the SES event detail view at ``/studio/ses-events/<pk>/``."""
    event = get_object_or_404(
        SesEvent.objects.select_related(
            'user', 'email_log', 'email_log__campaign',
        ),
        pk=pk,
    )
    # Pretty-print the raw payload so the operator gets a legible block
    # without horizontal overflow on a 1280-wide viewport. ``ensure_ascii``
    # stays default — production payloads occasionally carry non-ASCII
    # subjects and the ``\u`` escapes are easier to grep than UTF-8 bytes.
    try:
        payload_json = json.dumps(
            event.raw_payload, indent=2, sort_keys=False,
        )
    except (TypeError, ValueError):
        # Defensive: ``raw_payload`` is a JSONField so non-JSON-able
        # values shouldn't be possible, but we'd rather render an empty
        # detail page than 500 on a corrupt row.
        payload_json = ''

    return render(request, 'studio/ses_events/detail.html', {
        'event': event,
        'pill_class': EVENT_TYPE_PILL_CLASSES.get(
            event.event_type, EVENT_TYPE_PILL_DEFAULT,
        ),
        'payload_json': payload_json,
    })
