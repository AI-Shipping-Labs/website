from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404

from content.access import build_gating_context, can_access, get_required_tier_name
from content.models import Article, Recording, Project, Tutorial, CuratedLink, Download, TagRule


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


ACTIVITIES = [
    {
        'icon': 'book-open',
        'title': 'Exclusive Substack Content',
        'description': 'Full access to premium paywalled articles with practical AI insights, hands-on tutorials with code examples you can implement, and curated breakdowns of new AI tools and workflows to accelerate your projects.',
        'tiers': ['basic', 'main', 'premium'],
    },
    {
        'icon': 'eye',
        'title': 'Behind-the-Scenes Research',
        'description': 'Get exclusive access to ongoing research and experiments. See work-in-progress findings and early-stage ideas not available publicly.',
        'tiers': ['basic', 'main', 'premium'],
    },
    {
        'icon': 'file-edit',
        'title': 'Curated Social Content Collection',
        'description': 'Never miss valuable educational posts again. Get a curated collection of evergreen social media content you can reference anytime.',
        'tiers': ['basic', 'main', 'premium'],
    },
    {
        'icon': 'users',
        'title': 'Closed Community Access',
        'description': 'Connect with action-oriented builders who are shipping practical AI products. Network with motivated peers, collaborate on projects, and learn from practitioners who convert ideas into tangible contributions.',
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'message-circle-question',
        'title': 'Collaborative Problem-Solving & Mentorship',
        'description': 'Get help with implementation challenges and complex issues. Learn from practitioners at various career stages and receive guidance on technical problems you\'re facing.',
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'video',
        'title': 'Interactive Group Coding Sessions',
        'description': 'Join sessions where community members and hosts code live, working through real problems. Watch, participate, and engage with comments as you learn.',
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'folder-kanban',
        'title': 'Guided Project-Based Learning',
        'description': 'Get the structure and direction you need to make consistent progress. Follow curated project frameworks, share your progress with the community, and build practical AI products with clear milestones.',
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'trophy',
        'title': 'Community Hackathons',
        'description': "Turn ideas into shipped projects through focused hackathons. Get gentle external pressure and accountability to build, share your work, and learn from other builders' approaches. Many members emerge from hackathons as active contributors.",
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'briefcase',
        'title': 'Career Advancement Discussions',
        'description': 'Discuss your career questions and get feedback from experienced practitioners in the community. Share experiences, get advice on job searches, interviews, and career growth.',
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'star',
        'title': 'Personal Brand Development',
        'description': 'Share your project results publicly and strengthen your professional presence. Get guidance on showcasing your work, building in public, and demonstrating real-world impact. Especially valuable for career transitioners and early career professionals.',
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'percent',
        'title': 'Developer Productivity Tips & Workflows',
        'description': 'Get tips, workflows, and best practices to boost your productivity as a developer. Learn techniques to work more efficiently and effectively.',
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'file-edit',
        'title': 'Propose and Vote on Topics',
        'description': "Have a voice in the community's direction. Propose ideas and vote on future topics for content, workshops, and sessions.",
        'tiers': ['main', 'premium'],
    },
    {
        'icon': 'book-open',
        'title': 'Mini-Courses on Specialized Topics',
        'description': 'Access all mini-courses covering specialized topics like Python for Data & AI Engineering, and more. The collection is regularly updated with new courses.',
        'tiers': ['premium'],
    },
    {
        'icon': 'file-edit',
        'title': 'Vote on Course Topics',
        'description': 'Have a say in what gets taught next. Propose ideas and vote on upcoming mini-course topics to shape the curriculum.',
        'tiers': ['premium'],
    },
    {
        'icon': 'users',
        'title': 'Profile Teardowns',
        'description': "Get detailed feedback on your resume, LinkedIn, and GitHub profiles. Understand what works, what doesn't, and how to improve your professional presence.",
        'tiers': ['premium'],
    },
]


def about(request):
    """About page."""
    return render(request, 'content/about.html')


def activities(request):
    """Activities page."""
    # Count activities per tier
    basic_activities = [a for a in ACTIVITIES if 'basic' in a['tiers']]
    main_activities = [a for a in ACTIVITIES if 'main' in a['tiers']]
    premium_activities = [a for a in ACTIVITIES if 'premium' in a['tiers']]

    context = {
        'activities': ACTIVITIES,
        'basic_activities': basic_activities,
        'main_activities': main_activities,
        'premium_activities': premium_activities,
        'basic_count': len(basic_activities),
        'main_count': len(main_activities),
        'premium_count': len(premium_activities),
    }
    return render(request, 'content/activities.html', context)


def blog_list(request):
    """Blog listing page with optional tag filtering."""
    articles = Article.objects.filter(published=True)
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
    }
    context.update(build_gating_context(request.user, article, 'article'))
    return render(request, 'content/blog_detail.html', context)


def recordings_list(request):
    """Event recordings listing page with tag filtering and pagination."""
    recordings = Recording.objects.filter(published=True)
    selected_tags = _get_selected_tags(request)

    # Collect all tags from published recordings for the tag filter UI
    all_tags = set()
    for recording in recordings:
        if recording.tags:
            all_tags.update(recording.tags)
    all_tags = sorted(all_tags)

    # Filter by tags if provided (AND logic)
    recordings = _filter_by_tags(recordings, selected_tags)

    # Pagination: 20 recordings per page
    paginator = Paginator(recordings, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'recordings': page_obj,
        'page_obj': page_obj,
        'all_tags': all_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'is_paginated': page_obj.has_other_pages(),
        'base_path': '/event-recordings',
    }
    return render(request, 'content/recordings_list.html', context)


def recording_detail(request, slug):
    """Event recording detail page."""
    recording = get_object_or_404(Recording, slug=slug, published=True)
    tag_rules = _get_tag_rules_for_tags(recording.tags)
    context = {'recording': recording, 'tag_rules': tag_rules}
    context.update(build_gating_context(request.user, recording, 'recording'))
    return render(request, 'content/recording_detail.html', context)


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
        has_access = can_access(request.user, link)
        annotated_links.append({
            'link': link,
            'has_access': has_access,
            'url': link.url if has_access else None,
            'cta_message': (
                f'Upgrade to {link.required_level_tier_name} to access this resource'
                if not has_access else ''
            ),
        })

    # Map category keys to icon names (mirrors CuratedLink.category_icon_name)
    category_icons = {
        'tools': 'wrench',
        'models': 'cpu',
        'courses': 'graduation-cap',
        'other': 'folder-open',
    }

    # Group by category, preserving the canonical category order
    category_order = ['tools', 'models', 'courses', 'other']
    grouped = []
    for cat_key in category_order:
        cat_links = [a for a in annotated_links if a['link'].category == cat_key]
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
