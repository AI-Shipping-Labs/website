from django.shortcuts import render, get_object_or_404

from content.models import Article, Recording, Project, Tutorial, CuratedLink


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
    """Blog listing page."""
    articles = Article.objects.filter(published=True)
    return render(request, 'content/blog_list.html', {'articles': articles})


def blog_detail(request, slug):
    """Blog post detail page."""
    article = get_object_or_404(Article, slug=slug, published=True)
    return render(request, 'content/blog_detail.html', {'article': article})


def recordings_list(request):
    """Event recordings listing page."""
    recordings = Recording.objects.filter(published=True)
    return render(request, 'content/recordings_list.html', {'recordings': recordings})


def recording_detail(request, slug):
    """Event recording detail page."""
    recording = get_object_or_404(Recording, slug=slug, published=True)
    return render(request, 'content/recording_detail.html', {'recording': recording})


def projects_list(request):
    """Projects listing page."""
    projects = Project.objects.filter(published=True)
    return render(request, 'content/projects_list.html', {'projects': projects})


def project_detail(request, slug):
    """Project detail page."""
    project = get_object_or_404(Project, slug=slug, published=True)
    return render(request, 'content/project_detail.html', {'project': project})


def collection_list(request):
    """Curated links listing page."""
    links = CuratedLink.objects.filter(published=True)
    categories = [
        {'key': 'tools', 'label': 'Tools'},
        {'key': 'models', 'label': 'Models'},
        {'key': 'courses', 'label': 'Courses'},
        {'key': 'other', 'label': 'Other'},
    ]
    return render(request, 'content/collection_list.html', {
        'links': links,
        'categories': categories,
    })


def tutorials_list(request):
    """Tutorials listing page."""
    tutorials = Tutorial.objects.filter(published=True)
    return render(request, 'content/tutorials_list.html', {'tutorials': tutorials})


def tutorial_detail(request, slug):
    """Tutorial detail page."""
    tutorial = get_object_or_404(Tutorial, slug=slug, published=True)
    return render(request, 'content/tutorial_detail.html', {'tutorial': tutorial})
