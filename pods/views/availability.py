"""Member availability editor for study groups."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.services.timezones import build_timezone_options, get_timezone_label, is_valid_timezone
from pods.services.availability import (
    AvailabilityError,
    editor_days,
    freshness_line,
    get_profile,
    parse_window_rows,
    save_availability,
    summary_rows,
)
from pods.services.tab import pods_frame_context
from pods.views.member import LOGIN_URL, _safe_next, _scope


def _timezone_value(request, profile):
    if profile is not None:
        return profile.timezone
    return request.user.preferred_timezone or ""


def _save(request, activity, timezone_value):
    if request.method != "POST":
        return timezone_value, None, "", None
    timezone_value = (request.POST.get("timezone") or "").strip()
    try:
        specs = parse_window_rows(request.POST)
        save_availability(request.user, timezone_value, specs)
    except AvailabilityError as exc:
        return timezone_value, None, str(exc), None
    messages.success(request, "Availability saved.")
    response = redirect(_safe_next(request, activity.availability_url()))
    return timezone_value, specs, "", response


def _timezone_labels(value):
    if not is_valid_timezone(value):
        return "", ""
    return value, get_timezone_label(value)


def _context(request, activity, profile, specs, error, timezone_value):
    clean_timezone, timezone_label = _timezone_labels(timezone_value)
    context = pods_frame_context(activity, request.user)
    context.update(
        {
            "profile": profile,
            "days": editor_days(profile, specs),
            "error": error,
            "timezone_value": clean_timezone,
            "has_saved_timezone": bool(profile is not None or request.user.preferred_timezone),
            "timezone_label": timezone_label,
            "timezone_options": build_timezone_options(),
            "freshness_line": freshness_line(profile),
            "summary": summary_rows(profile),
            "back_url": _safe_next(request, activity.list_url()),
            "next_value": request.POST.get("next") or request.GET.get("next") or "",
        }
    )
    return context


@login_required(login_url=LOGIN_URL)
def availability_edit(request, slug=None, sprint_slug=None):
    """Edit the profile shared by every study-group activity."""
    activity = _scope(request, slug, sprint_slug)
    profile = get_profile(request.user)
    timezone_value = _timezone_value(request, profile)
    timezone_value, specs, error, response = _save(request, activity, timezone_value)
    if response is not None:
        return response
    context = _context(request, activity, profile, specs, error, timezone_value)
    status = 200
    if error:
        status = 400
    return render(request, "pods/availability.html", context, status=status)
