import json
import re
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from django import template
from django.conf import settings
from django.template.defaultfilters import stringfilter
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.text import Truncator
from django_q.models import OrmQ

from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
    LEVEL_REGISTERED,
)
from email_app import ses_explain
from integrations.config import site_base_url
from integrations.models.utm_campaign import UTM_MEDIUM_PRESETS, UTM_SOURCE_PRESETS
from studio.utils import get_github_edit_url, is_synced
from studio.worker_health import get_worker_status

register = template.Library()


LIST_TABLE_WRAPPER_CLASS = 'studio-responsive-table bg-card border border-border rounded-lg overflow-x-auto'
LIST_TABLE_CLASS = 'w-full'
LIST_TABLE_HEAD_CLASS = 'bg-secondary'
LIST_TABLE_HEAD_CELL_CLASS = (
    'text-left px-6 py-3 text-xs font-medium text-muted-foreground '
    'uppercase tracking-wider'
)
LIST_TABLE_HEAD_CELL_RIGHT_CLASS = (
    'text-right px-6 py-3 text-xs font-medium text-muted-foreground '
    'uppercase tracking-wider'
)
LIST_TABLE_BODY_CLASS = 'divide-y divide-border'
LIST_TABLE_ROW_CLASS = 'hover:bg-secondary/50 transition-colors'
ACTION_CELL_CLASS = 'studio-actions-cell text-right'
ACTION_GROUP_CLASS = 'studio-action-group inline-flex flex-nowrap items-center justify-end gap-2'
ACTION_FORM_CLASS = 'inline-flex'
ACTION_BASE_CLASS = (
    'studio-action inline-flex items-center justify-center whitespace-nowrap rounded-md '
    'border px-2.5 py-1 text-xs font-medium transition-colors '
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent '
    'focus-visible:ring-offset-2 focus-visible:ring-offset-background'
)
ACTION_KIND_CLASSES = {
    'primary': 'border-accent bg-accent text-accent-foreground hover:opacity-90',
    'secondary': 'border-border bg-secondary text-foreground hover:bg-muted',
    'destructive': 'border-red-500/40 bg-transparent text-red-400 hover:bg-red-500/10 hover:text-red-300',
    'async': 'border-blue-500/40 bg-blue-500/10 text-blue-200 hover:bg-blue-500/20',
}

STATUS_BADGE_CLASSES = {
    'published': 'bg-green-500/20 text-green-700 dark:text-green-300',
    'draft': 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-300',
    'upcoming': 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
    'sending': 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
    'sent': 'bg-green-500/20 text-green-700 dark:text-green-300',
    'delivered': 'bg-green-500/20 text-green-700 dark:text-green-300',
    'opened': 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
    'clicked': 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
    'bounced': 'bg-red-500/20 text-red-700 dark:text-red-300',
    'complained': 'bg-red-500/20 text-red-700 dark:text-red-300',
    # ``active`` reads as the "currently happening" / "in-progress" state
    # across surfaces (content publishing, sprints, etc.) — same green as
    # ``published`` so the two read consistently. ``completed`` is the
    # archived/done state and stays grey-muted; ``archived`` aliases it
    # for the sprint vocabulary.
    'active': 'bg-green-500/20 text-green-700 dark:text-green-300',
    'completed': 'bg-secondary text-muted-foreground',
    'archived': 'bg-secondary text-muted-foreground',
    # Time-derived label for the Studio events list (#820): a finished
    # (or past-grouped) event reads grey-muted, same as ``completed``.
    'past': 'bg-secondary text-muted-foreground',
    'cancelled': 'bg-red-500/20 text-red-700 dark:text-red-300',
    # User-import batch statuses (#753): align the imports list pill colors
    # with the canonical palette instead of inline ``{% if %}`` blocks.
    'failed': 'bg-red-500/20 text-red-700 dark:text-red-300',
    'running': 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
}

TIER_PILL_CLASSES = {
    'free': 'bg-muted text-muted-foreground',
    'basic': 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
    'main': 'bg-accent/20 text-accent',
    'premium': 'bg-amber-500/20 text-amber-700 dark:text-amber-300',
}

USER_STATUS_PILL_CLASSES = {
    'active': 'bg-green-500/15 text-green-700 dark:text-green-300',
    'staff': 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
    'inactive': 'bg-red-500/15 text-red-700 dark:text-red-300',
}

STATUS_OPTIONS = {
    'publication': [
        ('draft', 'Draft'),
        ('published', 'Published'),
    ],
    'event': [
        ('draft', 'Draft'),
        ('upcoming', 'Upcoming'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ],
    'campaign': [
        ('draft', 'Draft'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
    ],
    'project': [
        ('pending_review', 'Pending Review'),
        ('published', 'Published'),
    ],
}

_DJANGO_TAG_RE = re.compile(r'{%.*?%}', re.DOTALL)
_DJANGO_VARIABLE_RE = re.compile(r'{{\s*(.*?)\s*}}', re.DOTALL)
_MARKDOWN_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\([^)]+\)')
_MARKDOWN_LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_MARKDOWN_MARKER_RE = re.compile(r'(?m)^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)')
_WHITESPACE_RE = re.compile(r'\s+')
_LOCAL_SITE_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0', '::1'}


@register.filter
def dict_get(dictionary, key):
    """Look up a key in a dictionary. Returns None if key is missing."""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


def _seconds_from_duration(value):
    if value in (None, ''):
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@register.filter
def compact_duration(value):
    """Render seconds/timedeltas as compact operator units: 45s, 3m, 2h, 15d."""
    seconds = _seconds_from_duration(value)
    if seconds is None:
        return ''
    seconds = abs(seconds)
    if seconds < 60:
        return f'{max(0, int(round(seconds)))}s'
    if seconds < 3600:
        return f'{max(1, int(round(seconds / 60)))}m'
    if seconds < 86400:
        return f'{max(1, int(round(seconds / 3600)))}h'
    return f'{max(1, int(round(seconds / 86400)))}d'


def _plain_text_preview(value, *, limit=96, strip_template_tags=False):
    text = '' if value is None else str(value)
    if strip_template_tags:
        text = _DJANGO_TAG_RE.sub(' ', text)
        text = _DJANGO_VARIABLE_RE.sub(r'\1', text)
    text = strip_tags(text)
    text = _MARKDOWN_IMAGE_RE.sub(r'\1', text)
    text = _MARKDOWN_LINK_RE.sub(r'\1', text)
    text = text.replace('```', ' ').replace('`', '')
    text = re.sub(r'(\*\*|__)(.*?)\1', r'\2', text)
    text = re.sub(r'(?<!\w)([*_])([^*_]+)\1(?!\w)', r'\2', text)
    text = _MARKDOWN_MARKER_RE.sub('', text)
    text = text.replace('[', '').replace(']', '')
    text = _WHITESPACE_RE.sub(' ', text).strip()
    return Truncator(text).chars(limit)


@register.filter
def plain_text_preview(value, limit=96):
    """Strip markdown/control markup for dense Studio table previews."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 96
    return _plain_text_preview(value, limit=limit)


@register.filter
def subject_preview(value, limit=96):
    """Render a compact email-subject preview without Django control-flow tags."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 96
    return _plain_text_preview(value, limit=limit, strip_template_tags=True)


@register.filter
def property_filter_preview(value):
    """Render trigger filters without leaking Python dict reprs."""
    if not value:
        return 'All events'
    if isinstance(value, dict):
        if all(not isinstance(v, (dict, list, tuple)) for v in value.values()):
            return ' · '.join(
                f'{key} = {value[key]}' for key in sorted(value)
            )
        return json.dumps(value, sort_keys=True)
    return str(value)


@register.filter
@stringfilter
def cron_gloss(value):
    """Human gloss for simple daily cron expressions."""
    fields = value.split()
    if len(fields) != 5:
        return value
    minute, hour, day_of_month, month, day_of_week = fields
    if day_of_month == month == day_of_week == '*':
        try:
            minute_i = int(minute)
            hour_i = int(hour)
        except ValueError:
            return value
        if 0 <= minute_i <= 59 and 0 <= hour_i <= 23:
            return f'daily {hour_i:02d}:{minute_i:02d} UTC'
    return value


def _site_hosts():
    hosts = set(_LOCAL_SITE_HOSTS)
    for raw in (
        site_base_url(),
        getattr(settings, 'SITE_BASE_URL', ''),
        'https://aishippinglabs.com',
    ):
        try:
            host = (urlsplit(raw).hostname or '').lower()
        except (TypeError, ValueError):
            host = ''
        if host:
            hosts.add(host)
    return hosts


@register.filter
@stringfilter
def normalize_site_url(value):
    """Collapse site-local absolute URLs to relative paths for Studio display."""
    if value.startswith('/'):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return value
    host = (parsed.hostname or '').lower()
    if host not in _site_hosts():
        return value
    path = parsed.path or '/'
    return urlunsplit(('', '', path, parsed.query, parsed.fragment))


@register.filter
def studio_tier_pill_classes(slug):
    """Return canonical Studio tier pill colours."""
    return TIER_PILL_CLASSES.get(str(slug or 'free').lower(), TIER_PILL_CLASSES['free'])


@register.filter
def studio_user_status_pill_classes(status):
    """Return canonical Studio user-status pill colours."""
    return USER_STATUS_PILL_CLASSES.get(
        str(status or 'inactive').lower(),
        USER_STATUS_PILL_CLASSES['inactive'],
    )


@register.simple_tag
def studio_list_class(part='wrapper', align='left'):
    """Return shared class names for Studio content list tables."""
    if part == 'wrapper':
        return LIST_TABLE_WRAPPER_CLASS
    if part == 'table':
        return LIST_TABLE_CLASS
    if part == 'thead':
        return LIST_TABLE_HEAD_CLASS
    if part == 'th':
        if align == 'right':
            return LIST_TABLE_HEAD_CELL_RIGHT_CLASS
        return LIST_TABLE_HEAD_CELL_CLASS
    if part == 'tbody':
        return LIST_TABLE_BODY_CLASS
    if part == 'row':
        return LIST_TABLE_ROW_CLASS
    if part == 'action_cell':
        return ACTION_CELL_CLASS
    if part == 'action_group':
        return ACTION_GROUP_CLASS
    if part == 'action_form':
        return ACTION_FORM_CLASS
    return ''


@register.simple_tag
def studio_action_class(kind='secondary'):
    """Return shared class names for Studio row actions."""
    return f"{ACTION_BASE_CLASS} {ACTION_KIND_CLASSES.get(kind, ACTION_KIND_CLASSES['secondary'])}"


@register.simple_block_tag(takes_context=True)
def studio_header_actions(
    context,
    content,
    title,
    eyebrow=None,
    subtitle=None,
    back_url=None,
    back_label=None,
    testid='studio-header',
    actions_testid='studio-header-actions',
    title_meta=None,
):
    """Render the shared stacked Studio header around local action markup."""
    actions = mark_safe(content.strip())
    return render_to_string(
        'studio/_partials/header_actions.html',
        {
            **context.flatten(),
            'eyebrow': eyebrow,
            'title': title,
            'subtitle': subtitle,
            'back_url': back_url,
            'back_label': back_label,
            'testid': testid,
            'actions_testid': actions_testid,
            'title_meta': title_meta,
            'actions': actions,
        },
    )


@register.simple_block_tag
def studio_header_title_meta(content):
    """Capture trusted, template-authored Studio header metadata."""
    return mark_safe(content.strip())


@register.simple_block_tag(takes_context=True)
def studio_overflow_menu(context, content):
    """Render the shared Studio overflow shell around local menu items."""
    return render_to_string(
        'studio/_partials/overflow_menu.html',
        {**context.flatten(), 'items': mark_safe(content.strip())},
    )


@register.simple_tag
def utm_source_presets():
    """House-standard utm_source presets (issue #874). Single source of truth
    in integrations/models/utm_campaign.py; suggestions only, not enforced."""
    return UTM_SOURCE_PRESETS


@register.simple_tag
def utm_medium_presets():
    """House-standard utm_medium presets (issue #874). Single source of truth
    in integrations/models/utm_campaign.py; suggestions only, not enforced."""
    return UTM_MEDIUM_PRESETS


@register.inclusion_tag('studio/includes/empty_state.html')
def studio_empty_state(
    kind,
    entity_label='',
    entity_label_plural='',
    create_url=None,
    clear_url=None,
    colspan=8,
    cta_label=None,
    testid_suffix='',
    empty_message='',
):
    """Render the canonical Studio list-page empty state.

    ``kind`` is ``"filter"`` for an inline ``<tr>`` empty cell that keeps
    the table chrome visible (a search/status filter is active and
    produced zero rows), or ``"fresh"`` for a separate ``bg-card`` empty
    card with a ``New <entity>`` CTA (no rows exist at all).

    Sub-issue C of #747 codifies the two-flavour convention. See
    ``_docs/design-system.md`` "Studio list pages".
    """
    return {
        'kind': kind,
        'entity_label': entity_label,
        'entity_label_plural': entity_label_plural or (
            f'{entity_label}s' if entity_label else ''
        ),
        'create_url': create_url,
        'clear_url': clear_url,
        'colspan': colspan,
        'cta_label': cta_label,
        'testid_suffix': testid_suffix,
        'empty_message': empty_message,
    }


@register.inclusion_tag('studio/includes/list_filter_form.html')
def studio_list_filter(
    search='',
    status_filter='',
    placeholder='Search...',
    status_kind='publication',
    auto_submit=True,
):
    """Render the shared Studio list search/status filter form.

    Pass ``status_kind=None`` (or an empty string) to render in search-only
    mode — the status dropdown is omitted entirely. This lets list pages
    that don't have a status concept (recordings, downloads) reuse the
    canonical search shell without rolling their own ``<form>``.
    """
    if status_kind:
        status_options = STATUS_OPTIONS.get(status_kind)
    else:
        status_options = None
    return {
        'search': search,
        'status_filter': status_filter,
        'placeholder': placeholder,
        'status_options': status_options,
        'auto_submit': auto_submit,
    }


@register.inclusion_tag('studio/includes/status_badge.html')
def studio_status_badge(status, label=''):
    """Render a centralized Studio list status badge."""
    return {
        'label': label or str(status).title(),
        'classes': STATUS_BADGE_CLASSES.get(status, STATUS_BADGE_CLASSES['draft']),
    }


@register.inclusion_tag('studio/includes/origin_badge.html')
def studio_origin_badge(obj, show_path=True, show_repo=False):
    """Render compact source provenance for Studio table/nested rows."""
    return {
        'obj': obj,
        'is_synced': is_synced(obj),
        'show_path': show_path,
        'show_repo': show_repo,
        'github_url': get_github_edit_url(obj),
    }


@register.inclusion_tag('studio/includes/origin_panel.html')
def studio_origin_panel(obj, action_obj=None, show_actions=True):
    """Render a dense provenance panel for source-aware Studio objects.

    ``obj`` is the row whose metadata should be displayed. ``action_obj`` can
    differ when the page is read-only because of a parent source-managed row,
    such as unit edit pages where re-sync should still target the parent
    course while the panel displays the unit's own markdown source.
    """
    action_obj = action_obj or obj
    return {
        'obj': obj,
        'action_obj': action_obj,
        'show_actions': show_actions,
        'is_synced': is_synced(obj),
        'github_url': get_github_edit_url(obj),
        'action_is_synced': is_synced(action_obj),
    }


@register.inclusion_tag('studio/includes/list_action.html')
def studio_list_action(href, label, kind='secondary', new_tab=False, rel=''):
    """Render shared Studio list action links."""
    return {
        'href': href,
        'label': label,
        'kind': kind,
        'class_name': studio_action_class(kind),
        'new_tab': new_tab,
        'rel': rel,
        'testid': 'view-on-site' if label == 'View on site' else '',
    }


@register.filter
def studio_access_label(required_level):
    """Return operator-facing access copy for Studio list rows."""
    try:
        level = int(required_level)
    except (TypeError, ValueError):
        return 'Custom access'

    labels = {
        LEVEL_OPEN: 'Free',
        LEVEL_REGISTERED: 'Registered users',
        LEVEL_BASIC: 'Basic (Level 10)',
        LEVEL_MAIN: 'Main (Level 20)',
        LEVEL_PREMIUM: 'Premium (Level 30)',
    }
    return labels.get(level, f'Custom (Level {level})')


@register.filter
def model_name(obj):
    """Return the lowercase Django model name for ``obj``.

    Templates can't read ``obj._meta.model_name`` directly (any attribute
    starting with an underscore is blocked by the template engine), so
    expose the value via a filter. Used by the origin panel's Re-sync
    source button (issue #281) to build the ``/studio/sync/object/<model>/``
    URL without each origin component call site having to hand-pass the model
    name.
    Returns an empty string for ``None`` or anything without an ``_meta``.
    """
    if obj is None:
        return ''
    meta = getattr(obj, '_meta', None)
    if meta is None:
        return ''
    return getattr(meta, 'model_name', '') or ''


@register.filter
def email_kind_label(value):
    """Humanize an open stored EmailLog.email_type value for operators."""
    return str(value or '').replace('_', ' ').strip().capitalize()


def _sync_status_label(status, error_count=0):
    """Return the human-readable label for a sync status.

    The DB enum keeps ``partial`` (avoid migrations + stable test contracts)
    but operators see ``Completed with N error(s)`` instead — the word
    "partial" reads as "in progress", which it isn't (it means
    "success with N errors"). See issue #245.
    """
    if status == 'success':
        return 'success'
    if status == 'failed':
        return 'failed'
    if status == 'running':
        return 'running'
    if status == 'queued':
        return 'queued'
    if status == 'skipped':
        return 'skipped'
    if status == 'partial':
        try:
            n = int(error_count or 0)
        except (TypeError, ValueError):
            n = 0
        if n == 1:
            return 'Completed with 1 error'
        if n > 1:
            return f'Completed with {n} errors'
        # Defensive: status said partial but no error count surfaced.
        return 'Completed with errors'
    return status or ''


@register.filter
def sync_status_label(status, error_count=0):
    """Filter form of the sync-status human label.

    Usage in a template:
        {{ status|sync_status_label:error_count }}
    """
    return _sync_status_label(status, error_count)


@register.inclusion_tag('studio/includes/sync_status_pill.html')
def sync_status_pill(status, error_count=0, size='sm'):
    """Render the standard sync-status pill.

    Used by the sync dashboard, sync history, and the legacy admin sync
    pages so every surface renders ``partial`` the same way (amber pill,
    "Completed with N errors" label). Centralising the render keeps the
    label and color in lock-step across templates — see issue #245.
    """
    return {
        'status': status,
        'error_count': error_count or 0,
        'label': _sync_status_label(status, error_count),
        'size': size,
    }


@register.simple_tag
def studio_sidebar_state(path):
    """Compute which collapsible sidebar section contains the active page.

    Issue #570 reorganised the Studio sidebar into collapsible sections.
    To avoid a flash of collapsed-then-expanded on first paint, the section
    containing the active page must render expanded server-side — that means
    the template needs to know which section is active before any JS runs.

    Django's ``{% with %}`` tag does not accept boolean expressions with
    mixed precedence (``a or b in c`` errors at parse time), so we
    compute the booleans here and return them as a dict the template can
    look up with ``{{ state.people_active }}`` etc. Keep these rules in
    lock-step with the per-link ``{% if ... in request.path %}`` checks
    inside ``templates/studio/base.html``.
    """
    p = path or ''

    content_active = (
        'articles' in p
        or 'marketing-pages' in p
        or 'courses' in p
        or 'projects' in p
        or '/workshops' in p
        or 'recordings' in p
        or 'downloads' in p
    )
    people_active = (
        p == '/studio/users/'
        or p == '/studio/users/export'
        or '/studio/users/payment-mismatches' in p
        or p == '/studio/tags/'
        or '/studio/imports/' in p
        or 'tier-override' in p
        or 'tier_override' in p
        or 'tier_overrides' in p
        or '/users/new' in p
        or '/users/created' in p
        or '/crm' in p
        or '/studio/call-hosts' in p
    )
    planning_active = (
        '/sprints' in p or '/plans' in p
    )
    onboarding_active = (
        '/questionnaires' in p or '/personas' in p
    )
    events_active = (
        '/events/' in p
        or p == '/studio/events'
        or 'event-series' in p
        or '/studio/hosts' in p
    )
    communication_active = (
        'notifications' in p
        or ('/campaigns' in p and 'utm-campaigns' not in p)
        or '/email-templates' in p
        or '/announcement' in p
    )
    tracking_active = (
        'utm-campaigns' in p
        or 'utm-analytics' in p
        or 'signup-analytics' in p
    )
    operations_active = (
        '/sync' in p
        or '/worker' in p
        or '/ses-events' in p
        or '/email-log' in p
        or 'redirects' in p
        or '/settings' in p
        or '/api-tokens' in p
        or '/triggers/' in p
    )
    triggers_active = '/triggers/' in p

    # Events is the dashboard default (#576) — when no other section is
    # active, Events renders expanded so the admin lands on its primary
    # surface. Once any other section is active, Events collapses back.
    any_other_section_active = (
        content_active
        or people_active
        or planning_active
        or onboarding_active
        or communication_active
        or tracking_active
        or operations_active
    )
    events_expanded = events_active or not any_other_section_active

    return {
        'content_active': content_active,
        'people_active': people_active,
        'planning_active': planning_active,
        'onboarding_active': onboarding_active,
        'events_active': events_active,
        'events_expanded': events_expanded,
        'communication_active': communication_active,
        'tracking_active': tracking_active,
        'operations_active': operations_active,
        'triggers_active': triggers_active,
    }


# --- SES event explanation tags (issue #849) --------------------------------
# Thin wrappers over ``email_app.ses_explain``. All copy lives in that module;
# these tags only forward the relevant SesEvent field to it so the list/detail
# templates can reach the explanations without the view passing extra context.


@register.simple_tag
def ses_severity(event):
    """Return the severity tier (``high`` / ``medium`` / ``info``) for an event."""
    return ses_explain.severity_for_event_type(getattr(event, 'event_type', ''))


@register.simple_tag
def ses_severity_label(event):
    """Return the severity label (``Serious`` / ``Temporary`` / ``Informational``)."""
    return ses_explain.severity_label(getattr(event, 'event_type', ''))


@register.simple_tag
def ses_severity_classes(event):
    """Return the reused pill class string for the event's severity tier."""
    return ses_explain.severity_classes(getattr(event, 'event_type', ''))


@register.simple_tag
def ses_severity_consequence(event):
    """Return the one-line consequence sentence for the event's severity tier."""
    return ses_explain.severity_consequence(getattr(event, 'event_type', ''))


@register.simple_tag
def ses_consequence_note(event):
    """Return the longer detail-page consequence note (incl. the 3-strike rule)."""
    return ses_explain.consequence_note(getattr(event, 'event_type', ''))


@register.simple_tag
def ses_term_explain(value):
    """Return plain-English text for a bounce_type/subtype value (empty if unknown)."""
    return ses_explain.explain_term(value)


@register.simple_tag
def ses_diagnostic_explain(diagnostic_code):
    """Return the decoded ``[(code, explanation), ...]`` for a diagnostic string."""
    return ses_explain.decode_diagnostic(diagnostic_code)


@register.inclusion_tag('studio/includes/worker_status_inline.html')
def worker_status_inline():
    """Render the subtle inline worker-status indicator.

    Suitable for studio pages that submit jobs to the queue (sync, campaigns,
    notifications). Calls ``get_worker_status()`` and counts queue depth via
    ``OrmQ`` so the template stays declarative.
    """
    info = get_worker_status()
    queue_depth = 0
    if info['alive']:
        try:
            queue_depth = OrmQ.objects.count()
        except Exception:
            queue_depth = 0
    return {
        'worker_status': {
            'alive': info['alive'],
            'expect_worker': info['expect_worker'],
            'queue_depth': queue_depth,
        },
    }
