"""Read-only Studio browser for SES-accepted outbound sends."""

import datetime
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import reverse

from accounts.services.email_resolution import normalize_email
from email_app.models import EmailLog
from email_app.services.email_log_history import (
    DISPOSITIONS,
    apply_email_log_filters,
    displayed_recipient,
    email_log_queryset,
    recipient_user_map,
)
from studio.decorators import staff_required
from studio.utils import coerce_page_number

EMAIL_LOG_PAGE_SIZE = 50


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _pager_url(request, page_number):
    params = request.GET.copy()
    params["page"] = str(page_number)
    return "?" + params.urlencode()


@staff_required
def email_log_list(request):
    search = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "").strip()
    status = request.GET.get("status", "").strip()
    raw_since = request.GET.get("since", "")
    raw_until = request.GET.get("until", "")
    since = _parse_date(raw_since)
    until = _parse_date(raw_until)
    filters_active = bool(search or kind or status or raw_since or raw_until)

    queryset = apply_email_log_filters(
        email_log_queryset(),
        search=search,
        kind=kind,
        status=status if status in DISPOSITIONS else "",
        since=since,
        until=until,
    ).order_by("-sent_at", "-pk")

    paginator = Paginator(queryset, EMAIL_LOG_PAGE_SIZE)
    page_number = coerce_page_number(
        request.GET.get("page"), paginator.num_pages or 1,
    )
    page = paginator.page(page_number)
    logs = list(page.object_list)
    address_users = recipient_user_map(logs)
    rows = []
    for log in logs:
        recipient = displayed_recipient(log)
        recipient_user = log.user if log.user_id else address_users.get(
            normalize_email(recipient)
        )
        rows.append({
            "log": log,
            "recipient": recipient,
            "recipient_user": recipient_user,
            "type_label": log.email_type.replace("_", " ").capitalize(),
            "ses_events_url": (
                f"{reverse('studio_ses_event_list')}?"
                f"{urlencode({'q': recipient})}"
            ),
        })

    kinds = list(
        EmailLog.objects.order_by("email_type")
        .values_list("email_type", flat=True)
        .distinct()
    )
    kind_choices = [
        (value, value.replace("_", " ").capitalize()) for value in kinds
    ]

    return render(request, "studio/email_log/list.html", {
        "rows": rows,
        "page": page,
        "paginator": paginator,
        "show_pager": paginator.num_pages > 1,
        "pager_first_url": _pager_url(request, 1) if page.has_previous() else None,
        "pager_prev_url": (
            _pager_url(request, page.previous_page_number())
            if page.has_previous() else None
        ),
        "pager_next_url": (
            _pager_url(request, page.next_page_number())
            if page.has_next() else None
        ),
        "pager_last_url": (
            _pager_url(request, paginator.num_pages) if page.has_next() else None
        ),
        "page_start_index": page.start_index(),
        "page_end_index": page.end_index(),
        "filtered_total": paginator.count,
        "has_any_log": EmailLog.objects.exists(),
        "filters_active": filters_active,
        "search": search,
        "kind": kind,
        "status": status,
        "since": raw_since,
        "until": raw_until,
        "kind_choices": kind_choices,
        "dispositions": DISPOSITIONS,
    })
