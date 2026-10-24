"""Template context and legacy preview rendering for series calendar mail."""

from accounts.services.timezones import (
    CALENDAR_INVITE_DATETIME_FORMAT,
    build_timezone_account_url,
    build_timezone_email_line,
    format_user_datetime,
)
from content.access import _resolve_required_level, can_access, get_required_tier_name
from email_app.services.email_rendering import (
    render_html_email,
    render_plain_text_email,
    render_template_parts,
)
from events.services.series_registration import _eligible_occurrences
from integrations.config import site_base_url


def build_series_template_context(user, series, events, changed_event=None, old_start=None):
    """Build the native Relay context shared by all series templates."""
    site_url = site_base_url()
    ordered = sorted(events, key=lambda event: event.start_datetime)
    changed_in_list = _changed_event_is_listed(ordered, changed_event, old_start)
    occurrences = _occurrence_lines(ordered, user, changed_event, old_start)
    registered_count = len(ordered)
    return {
        "series_name": series.name,
        "series_url": f"{site_url}{series.get_absolute_url()}",
        "registered_count": registered_count,
        "registered_count_plural": "" if registered_count == 1 else "s",
        "occurrences_list": "\n".join(occurrences),
        "partial_note": _partial_access_note(user, series, ordered, site_url),
        "changed_occurrence": changed_in_list,
        "event_title": changed_event.title if changed_in_list else "",
        "timezone_help": build_timezone_email_line(
            user,
            build_timezone_account_url(site_url),
        ),
    }


def _changed_event_is_listed(events, changed_event, old_start):
    if changed_event is None or old_start is None:
        return False
    for event in events:
        if event.pk == changed_event.pk:
            return True
    return False


def _occurrence_lines(events, user, changed_event, old_start):
    changed_in_list = _changed_event_is_listed(events, changed_event, old_start)
    lines = []
    for event in events:
        new_time = format_user_datetime(
            event.start_datetime,
            user,
            fmt=CALENDAR_INVITE_DATETIME_FORMAT,
        )
        if changed_in_list and event.pk == changed_event.pk:
            old_time = format_user_datetime(
                old_start,
                user,
                fmt=CALENDAR_INVITE_DATETIME_FORMAT,
            )
            lines.append(f"- {event.title} — {new_time} (was {old_time})")
            continue
        lines.append(f"- {event.title} — {new_time}")
    return lines


def _partial_access_note(user, series, accessible_events, site_url):
    accessible_ids = {event.id for event in accessible_events}
    gated = []
    for event in _eligible_occurrences(series):
        if event.id in accessible_ids or can_access(user, event):
            continue
        gated.append(event)
    if not gated:
        return ""
    return _gated_note(gated, site_url)


def _gated_note(gated, site_url):
    count = len(gated)
    plural = ""
    is_are = "is"
    isnt_arent = "isn't"
    it_them = "it"
    if count != 1:
        plural = "s"
        is_are = "are"
        isnt_arent = "aren't"
        it_them = "them"
    highest_level = max(_resolve_required_level(event) for event in gated)
    tier_name = get_required_tier_name(highest_level)
    return (
        f"Heads up: {count} more session{plural} in this series {is_are} "
        f"available on the {tier_name} tier and {isnt_arent} included above. "
        f"Upgrade any time to add {it_them} to your calendar: "
        f"{site_url}/membership"
    )


def render_series_email_parts(
    template_name,
    user,
    series,
    events,
    email_type,
    changed_event=None,
    old_start=None,
):
    """Render the legacy local preview from the Relay-native context."""
    context = build_series_template_context(
        user,
        series,
        events,
        changed_event=changed_event,
        old_start=old_start,
    )
    subject, body_markdown, body_html, footer_note = render_template_parts(
        template_name,
        user,
        context,
    )
    full_html = render_html_email(subject, body_html, footer_note=footer_note)
    plain_text = render_plain_text_email(body_markdown, footer_note=footer_note)
    return subject, plain_text, full_html


def build_series_subject_context(
    template_name,
    user,
    series,
    events,
    changed_event=None,
    old_start=None,
):
    """Return the audit subject and native Relay context for one send."""
    context = build_series_template_context(
        user,
        series,
        events,
        changed_event=changed_event,
        old_start=old_start,
    )
    subject, _body, _html, _footer = render_template_parts(
        template_name,
        user,
        context,
    )
    return subject, context


def render_series_email(
    template_name,
    user,
    series,
    events,
    email_type,
    changed_event=None,
    old_start=None,
):
    """Compatibility helper for HTML-only previews and render tests."""
    subject, _plain_text, full_html = render_series_email_parts(
        template_name,
        user,
        series,
        events,
        email_type,
        changed_event=changed_event,
        old_start=old_start,
    )
    return subject, full_html
