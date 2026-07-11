"""Studio Signup Analytics dashboard."""

from django.core.paginator import Paginator
from django.shortcuts import render

from accounts.lifecycle import ACCOUNT_LIFECYCLE_CHOICES
from analytics.models import SIGNUP_PATH_CHOICES, UserAttribution
from analytics.services.signup_analytics import (
    RANGE_CHOICES,
    RECENT_PAGE_SIZE,
    build_signup_analytics_report,
    parse_signup_analytics_filters,
    querystring,
)
from analytics.services.signup_analytics import (
    TOP_N as SIGNUP_ANALYTICS_TOP_N,
)
from studio.decorators import staff_required
from studio.utils import coerce_page_number

TOP_N = SIGNUP_ANALYTICS_TOP_N


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


def _has_referrer_field():
    """Return True when ``UserAttribution`` has the #772 referrer fields."""
    try:
        UserAttribution._meta.get_field('first_touch_referrer_source')
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

@staff_required
def signup_analytics_dashboard(request):
    """Read-only aggregation dashboard over ``UserAttribution``."""
    filters, _errors = parse_signup_analytics_filters(
        request.GET,
        strict=False,
    )
    has_referrer_data = _has_referrer_field()
    report = build_signup_analytics_report(
        filters,
        has_referrer_data=has_referrer_data,
    )

    paginator = Paginator(report['recent_signups'], RECENT_PAGE_SIZE)
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
        'querystring': querystring(filters),
        'signup_path_choices': SIGNUP_PATH_CHOICES,
        'account_lifecycle_choices': ACCOUNT_LIFECYCLE_CHOICES,
        'range_choices': RANGE_CHOICES,
        'headline_cards': report['headline_cards'],
        'window_total': report['window_total'],
        'account_lifecycle_rows': report['account_lifecycle_rows'],
        'signup_path_rows': report['signup_path_rows'],
        'utm_source_rows': report['utm_source_rows'],
        'has_referrer_data': has_referrer_data,
        'referrer_rows': report['referrer_rows'],
        'campaign_rows': report['campaign_rows'],
        'actionable_source_rows': report['actionable_source_rows'],
        'pre_signup_activity_rows': report['pre_signup_activity_rows'],
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
