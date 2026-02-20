import json

from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from django.views.decorators.http import require_POST, require_GET

from content.access import can_access, get_user_level
from content.models import Project, Download


@require_POST
def submit_project(request):
    """Community project submission endpoint.

    Authenticated users can submit a project for admin review.
    Creates a Project with status='pending_review' and published=False.
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {'error': 'Invalid JSON'},
            status=400,
        )

    title = data.get('title', '').strip()
    if not title:
        return JsonResponse(
            {'error': 'Title is required'},
            status=400,
        )

    description = data.get('description', '').strip()
    if not description:
        return JsonResponse(
            {'error': 'Description is required'},
            status=400,
        )

    # Generate a unique slug from the title
    base_slug = slugify(title)
    slug = base_slug
    counter = 1
    while Project.objects.filter(slug=slug).exists():
        slug = f'{base_slug}-{counter}'
        counter += 1

    difficulty = data.get('difficulty', '').strip()
    valid_difficulties = [c[0] for c in Project.DIFFICULTY_CHOICES]
    if difficulty and difficulty not in valid_difficulties:
        return JsonResponse(
            {'error': f'Invalid difficulty. Must be one of: {", ".join(valid_difficulties)}'},
            status=400,
        )

    tags = data.get('tags', [])
    if not isinstance(tags, list):
        return JsonResponse(
            {'error': 'Tags must be a list'},
            status=400,
        )

    from django.utils import timezone

    project = Project(
        title=title,
        slug=slug,
        description=description,
        content_markdown=data.get('content_markdown', ''),
        difficulty=difficulty,
        tags=tags,
        source_code_url=data.get('source_code_url', ''),
        demo_url=data.get('demo_url', ''),
        cover_image_url=data.get('cover_image_url', ''),
        author=request.user.get_full_name() or request.user.email,
        date=timezone.now().date(),
        status='pending_review',
        published=False,
        submitter=request.user,
    )
    project.save()

    return JsonResponse({
        'id': project.pk,
        'slug': project.slug,
        'status': project.status,
        'message': 'Project submitted for review',
    }, status=201)


@require_GET
def download_file(request, slug):
    """File download endpoint.

    Streams the file if the user has access. Returns 403 otherwise.
    For lead magnet downloads (required_level=0), anonymous users get 401
    with requires_email=true so the frontend can show an email signup form.

    On success, increments download_count and redirects to the file_url.
    """
    download = get_object_or_404(Download, slug=slug, published=True)

    # Lead magnet flow: required_level 0 but user is anonymous
    if download.required_level == 0 and not request.user.is_authenticated:
        return JsonResponse(
            {
                'error': 'Email signup required',
                'requires_email': True,
                'download_slug': slug,
            },
            status=401,
        )

    # Gated download: user does not have sufficient access level
    if not can_access(request.user, download):
        return JsonResponse(
            {'error': 'Insufficient access level'},
            status=403,
        )

    # User is authorized: increment download count and redirect to file
    download.increment_download_count()
    return HttpResponseRedirect(download.file_url)
