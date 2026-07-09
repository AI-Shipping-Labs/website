from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
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
from plans.models import Plan, Sprint, SprintEnrollment


def _record_resource_view_if_accessible(
    request, obj, object_type, object_id, url_name, *url_args,
):
    """Record a `resource_view` only when the member can access ``obj``.

    Shared by the public content detail views (issue #773). Records the
    PUBLIC content URL the member saw so staff can click through from the
    CRM timeline. No-op for anonymous users or gated teasers (``can_access``
    False). Defensive — ``record_resource_view`` never raises.
    """
    from analytics.activity import _safe_public_url, record_resource_view

    if not request.user.is_authenticated:
        return
    if not can_access(request.user, obj):
        return
    record_resource_view(
        request.user,
        object_type=object_type,
        object_id=object_id,
        title=getattr(obj, 'title', '') or str(object_id),
        target_url=_safe_public_url(url_name, *url_args),
    )


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


def _clean_guest_surface_text(value):
    """Normalize stale sync-managed text before rendering public pages."""
    if value is None:
        return ''
    return str(value).replace('\\"', '"').replace("\\'", "'").strip()


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


def _build_sprint_summaries(sprints, user):
    """Return card presentation summaries for ``sprints`` and ``user``."""
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


def _get_activity_sprints(user):
    """Return public activity-hub sprint summaries for the current viewer."""
    today = timezone.localdate()
    sprints = []

    active_sprints = Sprint.objects.filter(status='active').order_by(
        'start_date', 'name',
    )
    for sprint in active_sprints:
        if sprint.start_date <= today <= sprint.end_date:
            sprints.append(sprint)

    if user.is_authenticated and user.is_staff:
        sprints.extend(
            Sprint.objects.filter(status='draft').order_by('start_date', 'name')
        )

    return _build_sprint_summaries(sprints, user)


def _sprint_section_title(label, count):
    noun = 'sprint' if count == 1 else 'sprints'
    return f'{label} {noun}'


def _build_sprints_index_sections(user):
    """Return date-derived Current/Future/Past sections for ``/sprints``."""
    statuses = ['active', 'completed']
    if user.is_authenticated and user.is_staff:
        statuses.append('draft')

    sprints = list(Sprint.objects.filter(status__in=statuses))
    today = timezone.localdate()

    current = []
    future = []
    past = []
    for sprint in sprints:
        if sprint.start_date <= today <= sprint.end_date:
            current.append(sprint)
        elif today < sprint.start_date:
            future.append(sprint)
        elif today > sprint.end_date:
            past.append(sprint)

    current.sort(key=lambda sprint: (sprint.start_date, sprint.name, sprint.id))
    future.sort(key=lambda sprint: (sprint.start_date, sprint.name, sprint.id))
    past.sort(
        key=lambda sprint: (
            -sprint.end_date.toordinal(),
            -sprint.start_date.toordinal(),
            sprint.name,
            sprint.id,
        )
    )

    section_defs = [
        ('current', 'Current', 'No sprint is running right now.', current),
        ('future', 'Future', 'No future sprints are scheduled yet.', future),
        ('past', 'Past', 'No past sprints yet.', past),
    ]
    return [
        {
            'key': key,
            'title': _sprint_section_title(label, len(section_sprints)),
            'empty_message': empty_message,
            'items': _build_sprint_summaries(section_sprints, user),
        }
        for key, label, empty_message, section_sprints in section_defs
    ]


def about(request):
    """About page."""
    return render(request, 'content/about.html')


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
    sprint_sections = _build_sprints_index_sections(request.user)
    context = {
        'sprint_sections': sprint_sections,
        'has_visible_sprints': any(
            section['items'] for section in sprint_sections
        ),
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

    related_articles = article.get_related_articles(limit=3)
    tag_rules = _get_tag_rules_for_tags(article.tags)
    context = {
        'article': article,
        'related_articles': related_articles,
        'tag_rules': tag_rules,
        'learning_stages': article.data_json.get('learning_stages', []),
    }
    context.update(build_gating_context(request.user, article, 'article'))
    _record_resource_view_if_accessible(
        request, article, 'article', article.slug, 'blog_detail', slug,
    )
    return render(request, 'content/blog_detail.html', context)


def blog_preview(request, preview_token):
    """Private draft article preview by high-entropy token."""
    article = get_object_or_404(Article, preview_token=preview_token)
    if article.published:
        return redirect(article.get_absolute_url())

    response = render(
        request,
        'content/blog_detail.html',
        {
            'article': article,
            'related_articles': Article.objects.none(),
            'tag_rules': _get_tag_rules_for_tags(article.tags),
            'is_gated': False,
            'draft_preview': True,
        },
    )
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    return response


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
    _record_resource_view_if_accessible(
        request, project, 'project', project.slug, 'project_detail', slug,
    )
    return render(request, 'content/project_detail.html', context)


def collection_list(request):
    """Curated links listing page with tag filtering and category grouping.

    Links are grouped by category and sorted by sort_order within each group.
    Gated links have their URL hidden from anonymous/insufficient-tier users.
    """
    category_order = ['workshops', 'courses', 'articles', 'other']
    links = CuratedLink.objects.filter(
        published=True,
        category__in=category_order,
    )
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
        link.title = _clean_guest_surface_text(link.title)
        link.description = _clean_guest_surface_text(link.description)
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

    # Map visible canonical section keys to icon names. Keys here drive
    # both the rendered section order and the badge icon shown on each card.
    category_icons = {
        'workshops': 'graduation-cap',
        'courses': 'book-open',
        'articles': 'file-text',
        'other': 'folder-open',
    }

    # Group by canonical category, preserving the canonical visible order.
    grouped = []
    for cat_key in category_order:
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
        # The member can access and is being redirected to the resource —
        # record the view (issue #773). target_url is the public go URL so
        # staff land on the same redirect the member followed.
        _record_resource_view_if_accessible(
            request, link, 'curated_link', link.pk, 'curated_link_go', link.pk,
        )
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
    _record_resource_view_if_accessible(
        request, tutorial, 'tutorial', tutorial.slug, 'tutorial_detail', slug,
    )
    return render(request, 'content/tutorial_detail.html', context)


@ensure_csrf_cookie
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
    verify_downloads_email = False
    for download in downloads:
        has_access = can_access(request.user, download)
        is_lead_magnet = download.required_level == 0
        is_anonymous = not request.user.is_authenticated
        gated_reason = get_gated_reason(request.user, download)
        requires_email_verification = gated_reason == 'unverified_email'
        if gated_reason == 'unverified_email':
            has_access = False
            verify_downloads_email = True

        annotated_downloads.append({
            'download': download,
            'has_access': has_access,
            'is_lead_magnet': is_lead_magnet,
            'show_email_form': is_lead_magnet and is_anonymous,
            'requires_email_verification': requires_email_verification,
            'cta_message': (
                f'Upgrade to {get_required_tier_name(download.required_level)} to download'
                if (
                    not has_access
                    and not is_lead_magnet
                    and not requires_email_verification
                ) else ''
            ),
        })

    context = {
        'downloads': annotated_downloads,
        'all_tags': all_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'base_path': '/downloads',
        'verify_downloads_email': verify_downloads_email,
    }
    if verify_downloads_email:
        from content.access import build_verify_email_context
        context.update(build_verify_email_context(request.user))
    return render(request, 'content/downloads_list.html', context)
