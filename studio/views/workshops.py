"""Studio views for workshop management (issue #297).

Workshops are synced from the public ``AI-Shipping-Labs/workshops-content``
repo via the standard sync pipeline. Studio is advisory: most fields are
read-only mirrors of the yaml/markdown source. Only five fields are
Studio-editable — ``status``, ``cover_image_url``, and the three-gate chain
(``landing_required_level``, ``pages_required_level``,
``recording_required_level``) which must satisfy
``landing <= pages <= recording`` per ``Workshop.clean()``.

Mirrors the shape of ``studio.views.courses`` and ``studio.views.campaigns``:
list/detail/edit views guarded by ``@staff_required``, plus a re-sync trigger
that fans out ``async_task`` calls for every ``ContentSource`` of
``content_type='workshop'`` and routes the operator back to the standard
sync dashboard.
"""

import logging
import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from content.access import get_required_tier_label
from content.models import Workshop
from integrations.models import ContentSource
from studio.decorators import staff_required
from studio.utils import get_github_edit_url
from studio.views.sync import _mark_source_queued, _worker_warning_suffix

logger = logging.getLogger(__name__)


def _build_page_github_url(workshop, page):
    """Return the GitHub URL for a ``WorkshopPage`` markdown file.

    New synced pages carry their own ``source_repo``. Older rows may still
    have only the parent workshop's repo metadata, so fall back to the
    workshop when needed.

    Returns an empty string when the workshop or page lacks the required
    source metadata so templates can use ``|default:''`` style fallbacks.
    """
    source_repo = page.source_repo or workshop.source_repo
    if not source_repo or not page.source_path:
        return ''
    return (
        f'https://github.com/{source_repo}/blob/main/'
        f'{page.source_path}'
    )


@staff_required
def workshop_list(request):
    """List all workshops with status filter and search.

    Search matches ``title`` or ``slug`` case-insensitively. The status
    filter takes ``draft`` or ``published``; missing/empty value returns
    every workshop. Result set is ordered by ``-date`` to match the model
    ``Meta.ordering`` and the public-facing list (issue #296).
    """
    status_filter = request.GET.get('status', '')
    search = request.GET.get('q', '').strip()

    workshops = (
        Workshop.objects
        .select_related('event')
        .prefetch_related('pages')
        .order_by('-date')
    )

    if status_filter:
        workshops = workshops.filter(status=status_filter)

    if search:
        # Match against title OR slug; ORM ``Q`` keeps it as a single SQL
        # query even when both fields are populated.
        workshops = workshops.filter(
            Q(title__icontains=search) | Q(slug__icontains=search),
        )

    return render(request, 'studio/workshops/list.html', {
        'workshops': workshops,
        'status_filter': status_filter,
        'search': search,
    })


@staff_required
def workshop_detail(request, workshop_id):
    """Read-only detail page for a workshop.

    Surfaces every field — including yaml-sourced ones — and the
    list of ``WorkshopPage`` rows in ``sort_order`` order. Editing is
    delegated to ``workshop_edit`` (only five fields are mutable). Page
    bodies are intentionally hidden because they're managed in markdown
    files; the template links out to the GitHub source for each page.
    """
    workshop = get_object_or_404(
        Workshop.objects.select_related('event'),
        pk=workshop_id,
    )
    pages = list(workshop.pages.order_by('sort_order'))

    # Pre-compute GitHub URLs server-side so the template stays declarative.
    # Issue #571: surface the per-page ``required_level`` override as a
    # human-readable label so staff can audit overrides without clicking
    # through to the Django admin. Empty string when the page inherits.
    pages_with_urls = [
        {
            'page': page,
            'github_url': _build_page_github_url(workshop, page),
            'required_level_label': (
                get_required_tier_label(page.required_level)
                if page.required_level is not None
                else ''
            ),
        }
        for page in pages
    ]

    return render(request, 'studio/workshops/detail.html', {
        'workshop': workshop,
        'pages_with_urls': pages_with_urls,
        'github_edit_url': get_github_edit_url(workshop),
    })


# Field options used by the edit form. Mirrors ``content.access.VISIBILITY_CHOICES``
# but pinned here so the template can iterate without importing.
TIER_LEVEL_CHOICES = [
    (0, 'Free (0)'),
    (10, 'Basic (10)'),
    (20, 'Main (20)'),
    (30, 'Premium (30)'),
]


def _safe_int(raw, default):
    """Parse ``raw`` as int, falling back to ``default`` on bad input.

    POST values reach Django as strings; an empty/garbled select would
    otherwise raise ``ValueError`` and produce a 500. Defaulting to the
    current value keeps the form forgiving without silently mutating data.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@staff_required
def workshop_edit(request, workshop_id):
    """Edit the five Studio-mutable fields on a workshop.

    Whitelists exactly: ``status``, ``cover_image_url``, and the three tier
    gates. Any other POSTed field is ignored to keep yaml-sourced values
    from drifting out of band. ``Workshop.full_clean()`` re-runs the
    three-way invariant ``landing <= pages <= recording``; on
    ``ValidationError`` the form re-renders with the submitted values and
    inline errors so the operator can correct them in place.
    """
    workshop = get_object_or_404(Workshop, pk=workshop_id)

    form_errors = {}
    submitted = {
        'status': workshop.status,
        'cover_image_url': workshop.cover_image_url,
        'landing_required_level': workshop.landing_required_level,
        'pages_required_level': workshop.pages_required_level,
        'recording_required_level': workshop.recording_required_level,
    }

    if request.method == 'POST':
        # Read only the five whitelisted fields.
        submitted = {
            'status': request.POST.get('status', workshop.status),
            'cover_image_url': request.POST.get(
                'cover_image_url', workshop.cover_image_url,
            ).strip(),
            'landing_required_level': _safe_int(
                request.POST.get('landing_required_level'),
                workshop.landing_required_level,
            ),
            'pages_required_level': _safe_int(
                request.POST.get('pages_required_level'),
                workshop.pages_required_level,
            ),
            'recording_required_level': _safe_int(
                request.POST.get('recording_required_level'),
                workshop.recording_required_level,
            ),
        }

        # Coerce status to a known value so a tampered POST can't slip a
        # garbage status through model validation as a passthrough.
        if submitted['status'] not in {'draft', 'published'}:
            submitted['status'] = workshop.status

        # Apply to the in-memory instance and run model validation.
        workshop.status = submitted['status']
        workshop.cover_image_url = submitted['cover_image_url']
        workshop.landing_required_level = submitted['landing_required_level']
        workshop.pages_required_level = submitted['pages_required_level']
        workshop.recording_required_level = submitted['recording_required_level']

        try:
            workshop.full_clean()
        except ValidationError as exc:
            # ``message_dict`` produces ``{'field': ['msg', ...]}`` — flatten
            # the lists to the first message per field for the inline display.
            form_errors = {
                field: errs[0] if isinstance(errs, list) else str(errs)
                for field, errs in exc.message_dict.items()
            }
            # Discard the in-memory mutation so a subsequent re-render of
            # the detail page doesn't show the rejected values.
            workshop.refresh_from_db()
        else:
            workshop.save()
            messages.success(
                request, f'Workshop "{workshop.title}" updated.',
            )
            return redirect('studio_workshop_detail', workshop_id=workshop.pk)

    return render(request, 'studio/workshops/form.html', {
        'workshop': workshop,
        'submitted': submitted,
        'form_errors': form_errors,
        'tier_level_choices': TIER_LEVEL_CHOICES,
        'github_edit_url': get_github_edit_url(workshop),
        'notify_url': reverse(
            'studio_workshop_notify',
            kwargs={'workshop_id': workshop.pk},
        ),
        'announce_url': reverse(
            'studio_workshop_announce_slack',
            kwargs={'workshop_id': workshop.pk},
        ),
    })


@staff_required
@require_POST
def workshop_resync(request):
    """Trigger a sync for the workshops ``ContentSource``.

    Issue #310: with one ``ContentSource`` per repo, this resolves to the
    ``AI-Shipping-Labs/workshops-content`` repo. If the operator ever
    splits workshops across repos, this view can grow back.
    """
    workshops_repo = 'AI-Shipping-Labs/workshops-content'
    source = ContentSource.objects.filter(repo_name=workshops_repo).first()

    if source is None:
        messages.error(
            request,
            f'No content source for {workshops_repo}. '
            'Add one under Sync Dashboard.',
        )
        return redirect('studio_sync_dashboard')

    batch_id = uuid.uuid4()

    try:
        try:
            # Lazy import: django_q is optional in test environments.
            from django_q.tasks import async_task

            from jobs.tasks.names import build_task_name
            async_task(
                'integrations.services.github.sync_content_source',
                source,
                batch_id=batch_id,
                task_name=build_task_name(
                    'Sync content source',
                    source.repo_name,
                    'Studio workshop sync',
                ),
            )
            _mark_source_queued(source, batch_id=batch_id)
        except ImportError:
            # Fall back to a synchronous sync when django_q isn't
            # installed (test runner, dev shell).
            from integrations.services.github import sync_content_source
            sync_content_source(source, batch_id=batch_id)
    except Exception:
        logger.exception(
            'Error triggering workshop sync for %s', source.repo_name,
        )

    warning = _worker_warning_suffix()
    base_msg = format_html(
        'Workshop sync queued for {repo}. Watch progress at '
        '<a href="/studio/sync/" class="underline">/studio/sync/</a>{warning}',
        repo=source.repo_name,
        warning=warning,
    )
    if warning:
        messages.warning(request, base_msg)
    else:
        messages.success(request, base_msg)

    return redirect('studio_sync_dashboard')
