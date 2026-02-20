"""Studio views for article CRUD."""

from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.text import slugify

from content.models import Article
from studio.decorators import staff_required


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
def article_create(request):
    """Create a new article."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        slug = request.POST.get('slug', '').strip() or slugify(title)
        description = request.POST.get('description', '')
        content_markdown = request.POST.get('content_markdown', '')
        cover_image_url = request.POST.get('cover_image_url', '')
        date_str = request.POST.get('date', '')
        author = request.POST.get('author', '')
        status = request.POST.get('status', 'draft')
        required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []

        published = status == 'published'
        date = timezone.now().date()
        if date_str:
            try:
                from datetime import datetime
                date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        article = Article.objects.create(
            title=title,
            slug=slug,
            description=description,
            content_markdown=content_markdown,
            cover_image_url=cover_image_url,
            date=date,
            author=author,
            published=published,
            required_level=required_level,
            tags=tags,
        )
        return redirect('studio_article_edit', article_id=article.pk)

    return render(request, 'studio/articles/form.html', {
        'article': None,
        'form_action': 'create',
    })


@staff_required
def article_edit(request, article_id):
    """Edit an existing article."""
    article = get_object_or_404(Article, pk=article_id)

    if request.method == 'POST':
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

    return render(request, 'studio/articles/form.html', {
        'article': article,
        'form_action': 'edit',
    })
