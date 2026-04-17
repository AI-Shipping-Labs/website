"""Studio views for registering ContentSource records via the GitHub App.

Provides a guided form that:
- Pulls the list of repositories the GitHub App installation can access and
  shows them in a dropdown (no free-text typos).
- Hides repos that already have at least one ``ContentSource`` row to avoid
  accidental duplicate registration (issue #213).
- Auto-detects the content type(s) and path(s) by walking the repo via the
  GitHub Contents API on submit (issue #213). The user no longer picks a
  content type or path manually -- the structure is the source of truth.
- Auto-fills ``is_private`` from the GitHub API response and auto-generates a
  ``webhook_secret`` when one is not supplied.

The Django admin form for ``ContentSource`` is intentionally left unchanged so
power users can still register sources with the manual fields.
"""

import json
import logging
import secrets

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from integrations.models import ContentSource
from integrations.services.github import (
    GitHubSyncError,
    clear_installation_repositories_cache,
    detect_content_sources,
    list_installation_repositories,
)
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


def _existing_repo_names():
    """Return the set of repo names that already have any ContentSource row."""
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


def _render_confirmation(request, *, repo_name, is_private, webhook_secret,
                         detections, status=200):
    """Render the "Detected: ... Create N sources?" step."""
    context = {
        'repo_name': repo_name,
        'is_private': is_private,
        'webhook_secret': webhook_secret,
        'detections': detections,
        # Serialize detections so the confirm POST can rebuild the list
        # without re-walking the repo.
        'detections_json': json.dumps(detections),
    }
    return render(
        request,
        'studio/content_sources/confirm.html',
        context,
        status=status,
    )


def _detect_for_repo(request, repo_name):
    """Return ``(detections, error_response)``.

    ``error_response`` is non-None when something went wrong and the caller
    should return it directly. Otherwise ``detections`` is the list of
    auto-detected ``{content_type, content_path, summary}`` dicts.
    """
    try:
        detections = detect_content_sources(repo_name)
    except GitHubSyncError as exc:
        logger.warning('Auto-detect failed for %s: %s', repo_name, exc)
        messages.error(
            request,
            f'Could not inspect {repo_name} on GitHub: {exc}',
        )
        return None, _render_form(
            request,
            selected_repo=repo_name,
            status=400,
        )
    return detections, None


@staff_required
def content_source_create(request):
    """Show / handle the "Add content source" form.

    GET shows the repo dropdown. POST runs auto-detection, then either:
      - shows a confirmation page (the ``confirm`` step), OR
      - actually creates the rows when the user has just confirmed.
    """
    if request.method != 'POST':
        return _render_form(request)

    step = (request.POST.get('step') or 'detect').strip()
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
            f"A content source for '{repo_name}' already exists. Edit it in "
            'Django admin to add additional types.',
        )
        return _render_form(
            request,
            selected_repo=repo_name,
            webhook_secret=webhook_secret,
            status=400,
        )

    is_private = match['private']

    if step == 'confirm':
        # Second step: user has reviewed the detections and ticked which ones
        # to create. Re-decode the canonical detections from the hidden field
        # and intersect with the boxes the user actually checked.
        try:
            detections = json.loads(request.POST.get('detections_json') or '[]')
        except json.JSONDecodeError:
            detections = []
        if not isinstance(detections, list):
            detections = []

        selected_keys = set(request.POST.getlist('selected'))
        chosen = [
            d for d in detections
            if f"{d.get('content_type')}:{d.get('content_path')}"
            in selected_keys
        ]

        if not chosen:
            messages.error(
                request,
                'Pick at least one detected source to create.',
            )
            return _render_confirmation(
                request,
                repo_name=repo_name,
                is_private=is_private,
                webhook_secret=webhook_secret,
                detections=detections,
                status=400,
            )

        if not webhook_secret:
            webhook_secret = secrets.token_urlsafe(32)

        created = []
        for d in chosen:
            source = ContentSource.objects.create(
                repo_name=repo_name,
                content_type=d['content_type'],
                content_path=d['content_path'],
                webhook_secret=webhook_secret,
                is_private=is_private,
            )
            created.append(source)

        # Stash the secret + created-source summary in the session so the
        # follow-up "success" page can show the secret exactly once. The
        # secret is consumed (popped) on render, so navigating away or
        # refreshing leaves nothing recoverable from the UI -- matching the
        # form copy that promised "this is the only time it's shown".
        request.session['content_source_created'] = {
            'repo_name': repo_name,
            'webhook_secret': webhook_secret,
            'sources': [
                {
                    'content_type': s.content_type,
                    'content_path': s.content_path,
                }
                for s in created
            ],
        }
        return redirect('studio_content_source_created')

    # First step: run auto-detection and show the confirmation page.
    detections, error_response = _detect_for_repo(request, repo_name)
    if error_response is not None:
        return error_response

    if not detections:
        messages.error(
            request,
            'Could not detect a recognized content type in this repo. Add a '
            '`course.yaml` (single course), a `courses/` directory with one '
            '`course.yaml` per subdirectory (multi-course), articles with '
            '`date:` frontmatter, projects with `difficulty` + `author` '
            'frontmatter, or events with `start_datetime` YAML, then try '
            'again.',
        )
        return _render_form(
            request,
            selected_repo=repo_name,
            webhook_secret=webhook_secret,
            status=400,
        )

    return _render_confirmation(
        request,
        repo_name=repo_name,
        is_private=is_private,
        webhook_secret=webhook_secret,
        detections=detections,
    )


@staff_required
def content_source_created(request):
    """Show the webhook secret one time after a successful create.

    Reads the stash written by ``content_source_create`` from the session and
    pops it so the secret is only rendered once. If the user reloads or
    navigates back, the page redirects to the sync dashboard with a hint --
    the secret is no longer recoverable from the UI (it can still be read
    from the DB by an admin via ``ContentSource.webhook_secret`` if needed).
    """
    stash = request.session.pop('content_source_created', None)
    if not stash:
        messages.info(
            request,
            'The webhook secret is only displayed once, immediately after '
            'creating a content source. Edit the source in Django admin if '
            'you need to read it again.',
        )
        return redirect('studio_sync_dashboard')

    webhook_url = request.build_absolute_uri(reverse('github_webhook'))
    context = {
        'repo_name': stash.get('repo_name', ''),
        'webhook_secret': stash.get('webhook_secret', ''),
        'sources': stash.get('sources', []),
        'webhook_url': webhook_url,
    }
    return render(
        request,
        'studio/content_sources/created.html',
        context,
    )


@staff_required
@require_POST
def content_source_refresh(request):
    """Drop the cached repo list and reload the form with fresh data."""
    clear_installation_repositories_cache()
    messages.success(request, 'Repository list refreshed from GitHub.')
    return redirect(reverse('studio_content_source_create'))
