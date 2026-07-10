"""Studio views for article management."""

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from content.models import Article
from studio.decorators import staff_required
from studio.services.banner_panel import banner_panel_context
from studio.utils import get_github_edit_url, is_synced, studio_pagination_context
from studio.views.form_helpers import (
    parse_comma_separated_tags,
    reject_synced_content_post,
)


@staff_required
def article_list(request):
    """List all articles with status filter."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('q', '')

    articles = Article.objects.all()
    if status_filter == 'published':
        articles = articles.filter(published=True)
    elif status_filter == 'draft':
        articles = articles.filter(published=False)
    if search:
        articles = articles.filter(title__icontains=search)
    pager = studio_pagination_context(request, articles)

    return render(request, 'studio/articles/list.html', {
        'articles': pager['page'].object_list,
        'status_filter': status_filter,
        'search': search,
        **pager,
    })


@staff_required
def article_edit(request, article_id):
    """Edit an existing article (read-only for synced items)."""
    article = get_object_or_404(Article, pk=article_id)
    synced = is_synced(article)

    if request.method == 'POST':
        if synced:
            return reject_synced_content_post()

        article.title = request.POST.get('title', '').strip()
        article.slug = request.POST.get('slug', '').strip() or slugify(article.title)
        article.description = request.POST.get('description', '')
        article.content_markdown = request.POST.get('content_markdown', '')
        article.cover_image_url = request.POST.get('cover_image_url', '')
        article.author = request.POST.get('author', '')
        article.required_level = int(request.POST.get('required_level', 0))
        article.tags = parse_comma_separated_tags(request.POST.get('tags', ''))

        date_str = request.POST.get('date', '')
        if date_str:
            try:
                from datetime import datetime
                article.date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        status = request.POST.get('status', 'draft')
        if status == 'published' and not article.published:
            article.publish()
        elif status == 'draft' and article.published:
            article.unpublish()
        else:
            article.save()

        return redirect('studio_article_edit', article_id=article.pk)

    context = {
        'article': article,
        'form_action': 'edit',
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(article),
        'notify_url': reverse('studio_article_notify', kwargs={'article_id': article.pk}),
        'announce_url': reverse('studio_article_announce_slack', kwargs={'article_id': article.pk}),
        'preview_url': request.build_absolute_uri(article.get_preview_url()),
        'preview_regenerate_url': reverse(
            'studio_article_regenerate_preview_token',
            kwargs={'article_id': article.pk},
        ),
        # Issues #788/#931: banner / social-image panel.
        **banner_panel_context(
            content_type='article',
            record=article,
            regenerate_url_name='studio_article_regenerate_banner',
            upload_url_name='studio_article_upload_banner',
            remove_url_name='studio_article_remove_banner',
            url_kwarg='article_id',
        ),
    }
    return render(request, 'studio/articles/form.html', context)


@staff_required
@require_POST
def article_regenerate_preview_token(request, article_id):
    """Rotate an article's private draft preview URL."""
    article = get_object_or_404(Article, pk=article_id)
    article.regenerate_preview_token()
    return redirect('studio_article_edit', article_id=article.pk)
