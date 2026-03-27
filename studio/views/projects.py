"""Studio views for project moderation."""

from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from content.models import Project
from studio.decorators import staff_required
from studio.utils import get_github_edit_url, is_synced


@staff_required
def project_list(request):
    """List all projects with status filter for moderation."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('q', '')

    projects = Project.objects.all()
    if status_filter:
        projects = projects.filter(status=status_filter)
    if search:
        projects = projects.filter(title__icontains=search)

    return render(request, 'studio/projects/list.html', {
        'projects': projects,
        'status_filter': status_filter,
        'search': search,
        'pending_count': Project.objects.filter(status='pending_review').count(),
    })


@staff_required
def project_review(request, project_id):
    """Review a project submission (read-only for synced items)."""
    project = get_object_or_404(Project, pk=project_id)
    synced = is_synced(project)

    if request.method == 'POST':
        if synced:
            return HttpResponseForbidden(
                'This content is managed in GitHub. Edit it there.'
            )
        action = request.POST.get('action', '')
        if action == 'approve':
            project.approve()
        elif action == 'reject':
            project.reject()
        return redirect('studio_project_list')

    return render(request, 'studio/projects/review.html', {
        'project': project,
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(project),
    })
