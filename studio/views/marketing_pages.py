"""Studio views for standalone marketing pages."""

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from content.models import MarketingPage
from studio.decorators import staff_required
from studio.utils import get_github_edit_url, is_synced
from studio.views.form_helpers import (
    parse_comma_separated_tags,
    reject_synced_content_post,
)


def _validation_errors(exc):
    if hasattr(exc, 'message_dict'):
        return exc.message_dict
    return {'__all__': exc.messages}


def _apply_form_values(page, post):
    page.title = post.get('title', '').strip()
    page.public_path = post.get('public_path', '').strip()
    page.description = post.get('description', '')
    page.meta_description = post.get('meta_description', '')
    page.content_markdown = post.get('content_markdown', '')
    page.cover_image_url = post.get('cover_image_url', '').strip()
    page.tags = parse_comma_separated_tags(post.get('tags', ''))
    page.status = post.get('status', 'draft')
    page.show_in_sitemap = post.get('show_in_sitemap') == 'on'
    page.nav_section = post.get('nav_section', 'none')
    page.nav_label = post.get('nav_label', '').strip()
    try:
        page.nav_order = int(post.get('nav_order', '0') or 0)
    except ValueError:
        raise ValidationError({'nav_order': 'Navigation order must be a number.'})


def _form_context(request, page, *, errors=None):
    synced = bool(page and page.pk and is_synced(page))
    preview_url = ''
    preview_regenerate_url = ''
    if page and page.pk:
        preview_url = request.build_absolute_uri(page.get_preview_url())
        preview_regenerate_url = reverse(
            'studio_marketing_page_regenerate_preview_token',
            kwargs={'page_id': page.pk},
        )
    return {
        'page_obj': page,
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(page) if page and page.pk else None,
        'preview_url': preview_url,
        'preview_regenerate_url': preview_regenerate_url,
        'errors': errors or {},
    }


@staff_required
def marketing_page_list(request):
    status_filter = request.GET.get('status', '').strip()
    search = request.GET.get('q', '').strip()

    pages = MarketingPage.objects.all().order_by('title')
    if status_filter in {'draft', 'published'}:
        pages = pages.filter(status=status_filter)
    if search:
        pages = pages.filter(
            Q(title__icontains=search)
            | Q(public_path__icontains=search)
            | Q(description__icontains=search)
        )

    return render(request, 'studio/marketing_pages/list.html', {
        'pages': pages,
        'status_filter': status_filter,
        'search': search,
    })


@staff_required
def marketing_page_new(request):
    page = MarketingPage()
    errors = {}
    if request.method == 'POST':
        try:
            _apply_form_values(page, request.POST)
            page.save()
            return redirect('studio_marketing_page_edit', page_id=page.pk)
        except ValidationError as exc:
            errors = _validation_errors(exc)

    return render(
        request,
        'studio/marketing_pages/form.html',
        _form_context(request, page, errors=errors),
    )


@staff_required
def marketing_page_edit(request, page_id):
    page = get_object_or_404(MarketingPage, pk=page_id)
    synced = is_synced(page)
    errors = {}

    if request.method == 'POST':
        if synced:
            return reject_synced_content_post()
        try:
            _apply_form_values(page, request.POST)
            page.save()
            return redirect('studio_marketing_page_edit', page_id=page.pk)
        except ValidationError as exc:
            errors = _validation_errors(exc)

    return render(
        request,
        'studio/marketing_pages/form.html',
        _form_context(request, page, errors=errors),
    )


@staff_required
@require_POST
def marketing_page_regenerate_preview_token(request, page_id):
    page = get_object_or_404(MarketingPage, pk=page_id)
    page.regenerate_preview_token()
    return redirect('studio_marketing_page_edit', page_id=page.pk)
