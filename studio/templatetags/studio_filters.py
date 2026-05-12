from django import template
from django_q.models import OrmQ

from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
    LEVEL_REGISTERED,
)
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
ACTION_GROUP_CLASS = 'studio-action-group inline-flex flex-wrap items-center justify-end gap-2'
ACTION_FORM_CLASS = 'inline-flex'
ACTION_BASE_CLASS = (
    'studio-action inline-flex items-center justify-center whitespace-nowrap rounded-md '
    'border px-2.5 py-1 text-xs font-medium transition-colors '
    'focus:outline-none focus:ring-2 focus:ring-accent/50'
)
ACTION_KIND_CLASSES = {
    'primary': 'border-accent bg-accent text-accent-foreground hover:opacity-90',
    'secondary': 'border-border bg-secondary text-foreground hover:bg-muted',
    'destructive': 'border-red-500/40 bg-transparent text-red-400 hover:bg-red-500/10 hover:text-red-300',
    'async': 'border-blue-500/40 bg-blue-500/10 text-blue-200 hover:bg-blue-500/20',
}

STATUS_BADGE_CLASSES = {
    'published': 'bg-green-500/20 text-green-400',
    'draft': 'bg-yellow-500/20 text-yellow-400',
    'upcoming': 'bg-blue-500/20 text-blue-400',
    'completed': 'bg-secondary text-muted-foreground',
    'cancelled': 'bg-red-500/20 text-red-400',
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
}


@register.filter
def dict_get(dictionary, key):
    """Look up a key in a dictionary. Returns None if key is missing."""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


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


@register.inclusion_tag('studio/includes/list_filter_form.html')
def studio_list_filter(
    search='',
    status_filter='',
    placeholder='Search...',
    status_kind='publication',
    auto_submit=True,
):
    """Render the shared Studio list search/status filter form."""
    return {
        'search': search,
        'status_filter': status_filter,
        'placeholder': placeholder,
        'status_options': STATUS_OPTIONS.get(status_kind, STATUS_OPTIONS['publication']),
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

    Issue #570 reorganised the Studio sidebar into five collapsible
    sections (Content, People, Events, Marketing, Operations) plus a
    nested Users sub-group inside People. To avoid a flash of
    collapsed-then-expanded on first paint, the section containing the
    active page must render expanded server-side — that means the
    template needs to know which section is active before any JS runs.

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
        or 'courses' in p
        or 'projects' in p
        or '/workshops' in p
        or 'recordings' in p
        or 'downloads' in p
    )
    # Users sub-group children: Imports, Tier overrides, New user.
    users_children_active = (
        '/studio/imports/' in p
        or 'tier-override' in p
        or '/users/new' in p
        or '/users/created' in p
    )
    # Users row itself is the leaf for /studio/users/ and /studio/users/export.
    users_row_active = (
        p == '/studio/users/' or p == '/studio/users/export'
    )
    people_active = (
        users_row_active
        or users_children_active
        or '/crm' in p
        or '/sprints' in p
        or '/plans' in p
    )
    events_active = (
        '/events/' in p
        or p == '/studio/events'
        or 'event-groups' in p
        or 'notifications' in p
    )
    marketing_active = (
        ('/campaigns' in p and 'utm-campaigns' not in p)
        or '/email-templates' in p
        or '/announcement' in p
        or 'utm-campaigns' in p
        or 'utm-analytics' in p
    )
    operations_active = (
        '/sync' in p
        or '/worker' in p
        or 'redirects' in p
        or '/settings' in p
        or '/api-tokens' in p
    )

    # Events is the dashboard default (#576) — when no other section is
    # active, Events renders expanded so the admin lands on its primary
    # surface. Once any other section is active, Events collapses back.
    any_other_section_active = (
        content_active or people_active or marketing_active or operations_active
    )
    events_expanded = events_active or not any_other_section_active

    return {
        'content_active': content_active,
        'people_active': people_active,
        'events_active': events_active,
        'events_expanded': events_expanded,
        'marketing_active': marketing_active,
        'operations_active': operations_active,
        'users_row_active': users_row_active,
        'users_children_active': users_children_active,
        # The Users sub-group also auto-expands when Users itself is active
        # — the spec calls for the friendlier expanded default.
        'users_expanded': users_row_active or users_children_active,
    }


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
