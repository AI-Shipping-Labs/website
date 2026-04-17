"""Studio views for registering ContentSource records via the GitHub App.

Provides a guided form that:
- Pulls the list of repositories the GitHub App installation can access and
  shows them in a dropdown (no free-text typos).
- Lets the admin pick the content type and an optional subdirectory.
- Auto-fills ``is_private`` from the GitHub API response and auto-generates a
  ``webhook_secret`` when one is not supplied.

The Django admin form for ``ContentSource`` is intentionally left unchanged so
power users can still register sources without GitHub App credentials.
"""

import logging
import secrets

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from integrations.models import ContentSource
from integrations.models.content_source import CONTENT_TYPE_CHOICES
from integrations.services.github import (
    GitHubSyncError,
    clear_installation_repositories_cache,
    list_installation_repositories,
)
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


def _load_repos(force_refresh=False):
    """Return ``(repos, error)``: repos may be empty when the API fails."""
    try:
        repos = list_installation_repositories(force_refresh=force_refresh)
        return repos, None
    except GitHubSyncError as exc:
        logger.warning('Could not fetch installation repositories: %s', exc)
        return [], str(exc)


def _render_form(request, *, selected_repo='', content_type='', content_path='',
                 webhook_secret='', force_refresh=False, status=200):
    repos, repo_error = _load_repos(force_refresh=force_refresh)
    context = {
        'repos': repos,
        'repo_error': repo_error,
        'content_type_choices': CONTENT_TYPE_CHOICES,
        'selected_repo': selected_repo,
        'selected_content_type': content_type,
        'content_path': content_path,
        'webhook_secret': webhook_secret,
    }
    return render(
        request,
        'studio/content_sources/create.html',
        context,
        status=status,
    )


@staff_required
def content_source_create(request):
    """Show / handle the "Add content source" form."""
    if request.method == 'POST':
        repo_name = (request.POST.get('repo_name') or '').strip()
        content_type = (request.POST.get('content_type') or '').strip()
        content_path = (request.POST.get('content_path') or '').strip()
        webhook_secret = (request.POST.get('webhook_secret') or '').strip()

        valid_content_types = {choice[0] for choice in CONTENT_TYPE_CHOICES}

        if not repo_name:
            messages.error(request, 'Pick a repository from the list.')
            return _render_form(
                request,
                selected_repo=repo_name,
                content_type=content_type,
                content_path=content_path,
                webhook_secret=webhook_secret,
                status=400,
            )

        if content_type not in valid_content_types:
            messages.error(request, 'Pick a valid content type.')
            return _render_form(
                request,
                selected_repo=repo_name,
                content_type=content_type,
                content_path=content_path,
                webhook_secret=webhook_secret,
                status=400,
            )

        # Verify the repo really is accessible to the installation right now,
        # and grab its private flag from the API instead of trusting the form.
        repos, repo_error = _load_repos()
        if repo_error:
            messages.error(
                request,
                'Could not verify the repository against GitHub: '
                f'{repo_error}',
            )
            return _render_form(
                request,
                selected_repo=repo_name,
                content_type=content_type,
                content_path=content_path,
                webhook_secret=webhook_secret,
                status=400,
            )

        match = next(
            (r for r in repos if r['full_name'] == repo_name),
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
                content_type=content_type,
                content_path=content_path,
                webhook_secret=webhook_secret,
                status=400,
            )

        is_private = match['private']

        if ContentSource.objects.filter(
            repo_name=repo_name, content_type=content_type,
        ).exists():
            messages.error(
                request,
                f"A content source for '{repo_name}' with content type "
                f"'{content_type}' already exists.",
            )
            return _render_form(
                request,
                selected_repo=repo_name,
                content_type=content_type,
                content_path=content_path,
                webhook_secret=webhook_secret,
                status=400,
            )

        if not webhook_secret:
            webhook_secret = secrets.token_urlsafe(32)

        source = ContentSource.objects.create(
            repo_name=repo_name,
            content_type=content_type,
            content_path=content_path,
            webhook_secret=webhook_secret,
            is_private=is_private,
        )
        messages.success(
            request,
            f"Content source registered: {source.repo_name} "
            f"({source.content_type}).",
        )
        return redirect('studio_sync_dashboard')

    return _render_form(request)


@staff_required
@require_POST
def content_source_refresh(request):
    """Drop the cached repo list and reload the form with fresh data."""
    clear_installation_repositories_cache()
    messages.success(request, 'Repository list refreshed from GitHub.')
    return redirect(reverse('studio_content_source_create'))
