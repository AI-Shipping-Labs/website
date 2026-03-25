"""Studio views for article management."""

from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from content.models import Article
from studio.decorators import staff_required
from studio.utils import is_synced, get_github_edit_url


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

    return render(request, 'studio/articles/list.html', {
        'articles': articles,
        'status_filter': status_filter,
        'search': search,
    })


@staff_required
def article_edit(request, article_id):
    """Edit an existing article (read-only for synced items)."""
    article = get_object_or_404(Article, pk=article_id)
    synced = is_synced(article)

    if request.method == 'POST':
        if synced:
            return HttpResponseForbidden(
                'This content is managed in GitHub. Edit it there.'
            )

        article.title = request.POST.get('title', '').strip()
        article.slug = request.POST.get('slug', '').strip() or slugify(article.title)
        article.description = request.POST.get('description', '')
        article.content_markdown = request.POST.get('content_markdown', '')
        article.cover_image_url = request.POST.get('cover_image_url', '')
        article.author = request.POST.get('author', '')
        article.required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        article.tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []

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
    }
    return render(request, 'studio/articles/form.html', context)
