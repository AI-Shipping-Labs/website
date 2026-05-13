"""Studio views for registering ContentSource records via the GitHub App.

Provides a single-click form (issue #310) that:
- Pulls the list of repositories the GitHub App installation can access and
  shows them in a dropdown (no free-text typos).
- Hides repos that already have a ``ContentSource`` row to avoid duplicate
  registration.
- Auto-fills ``is_private`` from the GitHub API response and auto-generates
  a ``webhook_secret`` when one is not supplied.

The Django admin form for ``ContentSource`` is intentionally left unchanged so
power users can still register sources with the manual fields.
"""

import logging
import secrets

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from integrations.models import ContentSource
from integrations.services.github import (
    GitHubSyncError,
    clear_installation_repositories_cache,
    list_installation_repositories,
    sync_content_source,
)
from studio.decorators import staff_required
from studio.views.sync import _mark_source_queued, _worker_warning_suffix

logger = logging.getLogger(__name__)


def _existing_repo_names():
    """Return the set of repo names that already have a ContentSource row."""
    return set(ContentSource.objects.values_list('repo_name', flat=True))


def _load_available_repos(force_refresh=False):
    """Return ``(available_repos, all_repos, error)``.

    ``available_repos`` excludes repos that already have a ContentSource row.
    ``all_repos`` is the unfiltered list from GitHub (used so the template can
    distinguish "no repos accessible" from "all repos already registered").
    On failure both lists are empty and ``error`` carries the message.
    """
    try:
        all_repos = list_installation_repositories(force_refresh=force_refresh)
    except GitHubSyncError as exc:
        logger.warning('Could not fetch installation repositories: %s', exc)
        return [], [], str(exc)

    existing = _existing_repo_names()
    available = [r for r in all_repos if r['full_name'] not in existing]
    return available, all_repos, None


def _render_form(request, *, selected_repo='', webhook_secret='',
                 force_refresh=False, status=200):
    available, all_repos, repo_error = _load_available_repos(
        force_refresh=force_refresh,
    )
    context = {
        'repos': available,
        'all_repos_count': len(all_repos),
        'repo_error': repo_error,
        'selected_repo': selected_repo,
        'webhook_secret': webhook_secret,
        # ``all_registered`` distinguishes "GitHub returned nothing" from
        # "every accessible repo is already registered" -- the template shows
        # different copy in each case.
        'all_registered': bool(all_repos) and not available,
    }
    return render(
        request,
        'studio/content_sources/create.html',
        context,
        status=status,
    )


def _queue_initial_sync(request, source):
    """Queue the first sync after a Studio ContentSource create.

    Mirrors the manual single-source trigger: same task target, same
    task_name, and the queued SyncLog is written only after enqueue succeeds.
    """
    try:
        try:
            from django_q.tasks import async_task

            from jobs.tasks.names import build_task_name
            async_task(
                'integrations.services.github.sync_content_source',
                source,
                task_name=build_task_name(
                    'Sync content source',
                    source.repo_name,
                    'Studio content source create',
                ),
            )
            _mark_source_queued(source)
            warning = _worker_warning_suffix()
            message = format_html(
                'Added {repo}. First sync queued. Webhook secret: {secret}. '
                'You can see the status '
                '<a href="/studio/worker/" class="underline">here</a>{warning}',
                repo=source.repo_name,
                secret=source.webhook_secret,
                warning=warning,
            )
            if warning:
                messages.warning(request, message)
            else:
                messages.success(request, message)
        except ImportError:
            sync_content_source(source)
            messages.success(
                request,
                format_html(
                    'Added {repo}. First sync completed. Webhook secret: {secret}',
                    repo=source.repo_name,
                    secret=source.webhook_secret,
                ),
            )
    except Exception as exc:
        logger.exception(
            'Could not queue initial sync for %s', source.repo_name,
        )
        messages.warning(
            request,
            format_html(
                'Added {repo}. Webhook secret: {secret}. The first sync '
                'could not be queued: {error}. Fix the queue or worker, then '
                'use Sync now from this dashboard.',
                repo=source.repo_name,
                secret=source.webhook_secret,
                error=exc,
            ),
        )


@staff_required
def content_source_create(request):
    """Show / handle the "Add content source" form.

    GET shows the repo dropdown. POST creates a single ``ContentSource``
    row for the selected repo and redirects to the sync dashboard with a
    success flash that includes the webhook secret. See issue #310.
    """
    if request.method != 'POST':
        return _render_form(request)

    repo_name = (request.POST.get('repo_name') or '').strip()
    webhook_secret = (request.POST.get('webhook_secret') or '').strip()

    if not repo_name:
        messages.error(request, 'Pick a repository from the list.')
        return _render_form(
            request,
            selected_repo=repo_name,
            webhook_secret=webhook_secret,
            status=400,
        )

    # Verify the repo really is accessible to the installation right now,
    # and grab its private flag from the API instead of trusting the form.
    available, all_repos, repo_error = _load_available_repos()
    if repo_error:
        messages.error(
            request,
            f'Could not verify the repository against GitHub: {repo_error}',
        )
        return _render_form(
            request,
            selected_repo=repo_name,
            webhook_secret=webhook_secret,
            status=400,
        )

    match = next(
        (r for r in all_repos if r['full_name'] == repo_name),
        None,
    )
    if match is None:
        messages.error(
            request,
            f"Repository '{repo_name}' is not accessible to the GitHub App "
            "installation. Click 'Refresh repo list' if you just granted "
            "access.",
        )
        return _render_form(
            request,
            selected_repo=repo_name,
            webhook_secret=webhook_secret,
            status=400,
        )

    if ContentSource.objects.filter(repo_name=repo_name).exists():
        messages.error(
            request,
            f"A content source for '{repo_name}' is already registered.",
        )
        return _render_form(
            request,
            selected_repo=repo_name,
            webhook_secret=webhook_secret,
            status=400,
        )

    if not webhook_secret:
        webhook_secret = secrets.token_urlsafe(32)

    source = ContentSource.objects.create(
        repo_name=repo_name,
        webhook_secret=webhook_secret,
        is_private=match['private'],
    )

    _queue_initial_sync(request, source)
    return redirect('studio_sync_dashboard')


@staff_required
@require_POST
def content_source_refresh(request):
    """Drop the cached repo list and reload the form with fresh data."""
    clear_installation_repositories_cache()
    messages.success(request, 'Repository list refreshed from GitHub.')
    return redirect(reverse('studio_content_source_create'))
