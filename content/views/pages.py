from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie

from content.access import (
    LEVEL_TO_TIER_NAME,
    build_gating_context,
    can_access,
    get_gated_reason,
    get_required_tier_name,
    get_user_level,
)
from content.models import Article, CuratedLink, Download, Project, TagRule, Tutorial
from content.tier_config import get_activities
from content.views.home import FAQ_ITEMS
from plans.models import Plan, Sprint, SprintEnrollment


def _get_selected_tags(request):
    """Extract selected tags from query params. Supports ?tag=X&tag=Y."""
    return [t.strip() for t in request.GET.getlist('tag') if t.strip()]


def _filter_by_tags(queryset, selected_tags):
    """Filter a queryset by multiple tags with AND logic.

    Returns a filtered queryset containing only items that have ALL selected tags.
    """
    if not selected_tags:
        return queryset
    # Filter items whose tags list contains all selected tags (AND logic)
    matching_ids = []
    for obj in queryset:
        obj_tags = set(obj.tags or [])
        if all(tag in obj_tags for tag in selected_tags):
            matching_ids.append(obj.pk)
    return queryset.filter(pk__in=matching_ids)


def _build_tag_filter_url(base_path, selected_tags, tag_to_add=None, tag_to_remove=None, extra_params=None):
    """Build a URL with tag query params.

    Used by templates to generate links for adding/removing tag filters.
    """
    tags = list(selected_tags)
    if tag_to_add and tag_to_add not in tags:
        tags.append(tag_to_add)
    if tag_to_remove and tag_to_remove in tags:
        tags.remove(tag_to_remove)
    params = []
    if extra_params:
        for key, val in extra_params.items():
            if val:
                params.append(f'{key}={val}')
    for tag in tags:
        params.append(f'tag={tag}')
    if params:
        return f'{base_path}?{"&".join(params)}'
    return base_path


def _get_tag_rules_for_tags(tags):
    """Return TagRule objects that match any of the given tags.

    Returns dict with 'after_content' and 'sidebar' lists.
    """
    if not tags:
        return {'after_content': [], 'sidebar': []}
    rules = TagRule.objects.filter(tag__in=tags)
    result = {'after_content': [], 'sidebar': []}
    for rule in rules:
        result[rule.position].append(rule)
    return result


def _get_activity_sprints(user):
    """Return public activity-hub sprint summaries for the current viewer."""
    statuses = ['active']
    if user.is_authenticated and user.is_staff:
        statuses.append('draft')

    sprints = Sprint.objects.filter(status__in=statuses).order_by(
        'start_date', 'name',
    )
    user_level = get_user_level(user) if user.is_authenticated else 0

    summaries = []
    for sprint in sprints:
        required_tier_name = LEVEL_TO_TIER_NAME.get(
            sprint.min_tier_level, 'Premium',
        )
        detail_url = reverse(
            'sprint_detail', kwargs={'sprint_slug': sprint.slug},
        )
        cta_url = detail_url
        cta_label = 'View sprint'

        if not user.is_authenticated:
            cta_url = f'{reverse("account_login")}?next={detail_url}'
            cta_label = 'Log in to join'
        else:
            viewer_plan = Plan.objects.filter(sprint=sprint, member=user).first()
            enrolled = SprintEnrollment.objects.filter(
                sprint=sprint, user=user,
            ).exists()
            eligible = user_level >= sprint.min_tier_level

            if enrolled and viewer_plan:
                cta_url = reverse(
                    'my_plan_detail',
                    kwargs={
                        'sprint_slug': sprint.slug,
                        'plan_id': viewer_plan.pk,
                    },
                )
                cta_label = 'Open my plan'
            elif enrolled:
                cta_url = reverse(
                    'cohort_board', kwargs={'sprint_slug': sprint.slug},
                )
                cta_label = 'Open cohort board'
            elif not eligible:
                cta_url = reverse('pricing')
                cta_label = f'Upgrade to {required_tier_name}'
            else:
                cta_label = 'View sprint'

        summaries.append({
            'sprint': sprint,
            'required_tier_name': required_tier_name,
            'cta_url': cta_url,
            'cta_label': cta_label,
        })

    return summaries


def about(request):
    """About page."""
    return render(request, 'content/about.html', {'faq_items': FAQ_ITEMS})


def activities(request):
    """Activities page."""
    all_activities = get_activities()
    activity_sprints = _get_activity_sprints(request.user)

    # Count activities per tier
    basic_activities = [a for a in all_activities if 'basic' in a['tiers']]
    main_activities = [a for a in all_activities if 'main' in a['tiers']]
    premium_activities = [a for a in all_activities if 'premium' in a['tiers']]

    context = {
        'activities': all_activities,
        'basic_activities': basic_activities,
        'main_activities': main_activities,
        'premium_activities': premium_activities,
        'basic_count': len(basic_activities),
        'main_count': len(main_activities),
        'premium_count': len(premium_activities),
        'activity_sprints': activity_sprints,
    }
    return render(request, 'content/activities.html', context)


def sprints_index(request):
    """Public community sprints discovery page."""
    context = {
        'activity_sprints': _get_activity_sprints(request.user),
    }
    return render(request, 'content/sprints_index.html', context)


@ensure_csrf_cookie
def blog_list(request):
    """Blog listing page with optional tag filtering."""
    articles = Article.objects.filter(published=True, page_type='blog')
    selected_tags = _get_selected_tags(request)

    # Collect all tags from published articles for the tag filter UI
    all_tags = set()
    for article in articles:
        if article.tags:
            all_tags.update(article.tags)
    all_tags = sorted(all_tags)

    # Filter by tags if provided (AND logic)
    articles = _filter_by_tags(articles, selected_tags)

    context = {
        'articles': articles,
        'all_tags': all_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'base_path': '/blog',
    }
    return render(request, 'content/blog_list.html', context)


def blog_detail(request, slug):
    """Blog post detail page with related articles."""
    article = get_object_or_404(Article, slug=slug, published=True)

    if article.page_type == 'learning_path':
        context = {
            'article': article,
            'title': article.title,
            'description': article.description,
            'learning_stages': article.data_json.get('learning_stages', []),
        }
        return render(request, 'content/learning_path_detail.html', context)

    related_articles = article.get_related_articles(limit=3)
    tag_rules = _get_tag_rules_for_tags(article.tags)
    context = {
        'article': article,
        'related_articles': related_articles,
        'tag_rules': tag_rules,
    }
    context.update(build_gating_context(request.user, article, 'article'))
    return render(request, 'content/blog_detail.html', context)


def projects_list(request):
    """Projects listing page with optional difficulty and tag filtering."""
    projects = Project.objects.filter(published=True)

    difficulty = request.GET.get('difficulty', '').strip()
    selected_tags = _get_selected_tags(request)

    # Collect all tags and difficulties from published projects for the filter UI
    all_tags = set()
    all_difficulties = set()
    for project in projects:
        if project.tags:
            all_tags.update(project.tags)
        if project.difficulty:
            all_difficulties.add(project.difficulty)
    all_tags = sorted(all_tags)
    all_difficulties = sorted(all_difficulties)

    # Filter by difficulty if provided
    if difficulty:
        projects = projects.filter(difficulty=difficulty)

    # Filter by tags if provided (AND logic)
    projects = _filter_by_tags(projects, selected_tags)

    context = {
        'projects': projects,
        'all_tags': all_tags,
        'all_difficulties': all_difficulties,
        'current_difficulty': difficulty,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'base_path': '/projects',
    }
    return render(request, 'content/projects_list.html', context)


def project_detail(request, slug):
    """Project detail page."""
    project = get_object_or_404(Project, slug=slug, published=True)
    tag_rules = _get_tag_rules_for_tags(project.tags)
    context = {'project': project, 'tag_rules': tag_rules}
    context.update(build_gating_context(request.user, project, 'project'))
    return render(request, 'content/project_detail.html', context)


def collection_list(request):
    """Curated links listing page with tag filtering and category grouping.

    Links are grouped by category and sorted by sort_order within each group.
    Gated links have their URL hidden from anonymous/insufficient-tier users.
    """
    links = CuratedLink.objects.filter(published=True)
    selected_tags = _get_selected_tags(request)

    # Collect all tags from published links for the tag filter UI
    all_tags = set()
    for link in links:
        if link.tags:
            all_tags.update(link.tags)
    all_tags = sorted(all_tags)

    # Filter by tags if provided (AND logic)
    links = _filter_by_tags(links, selected_tags)

    # Build per-link access info and strip URLs from gated links
    annotated_links = []
    for link in links:
        gated_reason = get_gated_reason(request.user, link)
        has_access = not gated_reason or gated_reason == 'unverified_email'
        url = link.url if not gated_reason else None
        if gated_reason == 'unverified_email':
            url = f'/resources/{link.pk}/go'
        annotated_links.append({
            'link': link,
            'has_access': has_access,
            'url': url,
            'cta_message': (
                f'Upgrade to {link.required_level_tier_name} to access this resource'
                if gated_reason == 'insufficient_tier' else ''
            ),
        })

    # Map visible section keys to icon names. Keys here drive both the
    # rendered section order and the badge icon shown on each card. The
    # legacy `tools` and `models` categories are intentionally absent —
    # rows with those categories fold into `other` (issue #524).
    category_icons = {
        'workshops': 'graduation-cap',
        'courses': 'book-open',
        'articles': 'file-text',
        'other': 'folder-open',
    }

    # Group by display category, preserving the canonical visible order.
    # Existing rows with category in {'tools', 'models'} render under the
    # `other` section without mutating the stored value (issue #524).
    category_order = ['workshops', 'courses', 'articles', 'other']
    legacy_other_categories = {'tools', 'models'}
    grouped = []
    for cat_key in category_order:
        if cat_key == 'other':
            cat_links = [
                a for a in annotated_links
                if a['link'].category == 'other'
                or a['link'].category in legacy_other_categories
            ]
        else:
            cat_links = [
                a for a in annotated_links
                if a['link'].category == cat_key
            ]
        if cat_links:
            grouped.append({
                'key': cat_key,
                'label': CuratedLink.CATEGORY_LABELS.get(cat_key, cat_key),
                'description': CuratedLink.CATEGORY_DESCRIPTIONS.get(cat_key, ''),
                'icon': category_icons.get(cat_key, 'folder-open'),
                'links': cat_links,
            })

    context = {
        'grouped_categories': grouped,
        'all_tags': all_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'base_path': '/resources',
    }
    return render(request, 'content/collection_list.html', context)


def curated_link_go(request, link_id):
    """Redirect to a curated link or render the verification gate for free users."""
    link = get_object_or_404(CuratedLink, pk=link_id, published=True)
    gating = build_gating_context(request.user, link, 'curated_link')
    if not gating['is_gated']:
        return redirect(link.url)
    if gating.get('gated_reason') == 'unverified_email':
        return render(
            request,
            'content/curated_link_verify_required.html',
            {'link': link, **gating},
        )
    return redirect('/pricing')


def tutorials_list(request):
    """Tutorials listing page."""
    tutorials = Tutorial.objects.filter(published=True)
    return render(request, 'content/tutorials_list.html', {'tutorials': tutorials})


def tutorial_detail(request, slug):
    """Tutorial detail page."""
    tutorial = get_object_or_404(Tutorial, slug=slug, published=True)
    tag_rules = _get_tag_rules_for_tags(tutorial.tags)
    context = {'tutorial': tutorial, 'tag_rules': tag_rules}
    context.update(build_gating_context(request.user, tutorial, 'tutorial'))
    return render(request, 'content/tutorial_detail.html', context)


def downloads_list(request):
    """Downloadable resources listing page with optional tag filtering."""
    downloads = Download.objects.filter(published=True)
    selected_tags = _get_selected_tags(request)

    # Collect all tags from published downloads for the tag filter UI
    all_tags = set()
    for download in downloads:
        if download.tags:
            all_tags.update(download.tags)
    all_tags = sorted(all_tags)

    # Filter by tags if provided (AND logic)
    downloads = _filter_by_tags(downloads, selected_tags)

    # Annotate each download with access info for the template
    annotated_downloads = []
    for download in downloads:
        has_access = can_access(request.user, download)
        is_lead_magnet = download.required_level == 0
        is_anonymous = not request.user.is_authenticated
        gated_reason = get_gated_reason(request.user, download)
        if gated_reason == 'unverified_email':
            has_access = True

        annotated_downloads.append({
            'download': download,
            'has_access': has_access,
            'is_lead_magnet': is_lead_magnet,
            'show_email_form': is_lead_magnet and is_anonymous,
            'cta_message': (
                f'Upgrade to {get_required_tier_name(download.required_level)} to download'
                if not has_access and not is_lead_magnet else ''
            ),
        })

    context = {
        'downloads': annotated_downloads,
        'all_tags': all_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'base_path': '/downloads',
    }
    return render(request, 'content/downloads_list.html', context)
