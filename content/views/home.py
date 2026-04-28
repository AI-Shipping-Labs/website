from django.conf import settings
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from content.access import get_active_override, get_user_level
from content.models import (
    Article,
    Course,
    CourseAccess,
    CuratedLink,
    Enrollment,
    Project,
    Unit,
    UserContentCompletion,
    UserCourseProgress,
    Workshop,
    WorkshopPage,
)
from content.models.completion import CONTENT_TYPE_WORKSHOP_PAGE
from content.tier_config import get_tiers_with_features
from events.models import Event

TESTIMONIALS = [
    {
        'quote': 'This course helped me understand how to implement a RAG system in Python. From basic system-design of a RAG, to evaluating responses and implementing guardrails, the course gave me a great overview of the necessary skills for implementing and managing my own agent.',
        'name': 'Rolando',
        'role': 'AI Data Scientist',
        'company': 'AeroMexico',
    },
    {
        'quote': 'I highly recommend the AI Engineering Buildcamp. I learned a tremendous amount. The material is abundant, very well organized, and progresses in a logical and progressive manner. This made complex topics much easier to follow and digest. The instructor Alexey Grigorev is clearly very knowledgeable in the field, and also super helpful and responsive to questions.',
        'name': 'John',
        'role': 'AI Tutor',
        'company': 'Meta',
    },
    {
        'quote': 'Excellent, comprehensive, and modern course that elevated my knowledge of generative AI from RAG applications to well-evaluated, fully functioning agentic systems. Alexey Grigorev incorporated essential software engineering practices, especially unit testing and evaluation, teaching us how to systematically improve our agents.',
        'name': 'Yan',
        'role': 'Senior Data Scientist',
        'company': 'Virtualitics',
    },
    {
        'quote': "I really enjoyed this course! It made the process of building AI agents both accessible and exciting. The progression from RAG to agents, multi-agent systems, monitoring, and guardrails was clear and practical. I'm walking away inspired and full of new ideas to build on.",
        'name': 'Scott',
        'role': 'Principal Data Scientist, Applied AI',
        'company': 'interos.ai',
    },
    {
        'quote': 'The course provides an excellent introduction to the core tooling needed to develop an agentic tool. Worth the effort especially given the comprehensiveness of the options and solutions available in the course.',
        'name': 'Naveen',
        'role': 'Software Engineer',
        'company': '',
    },
    {
        'quote': 'Excellent course, it gets you practicing the concepts you need to know to work on agentic AI. The instructor is accessible, clear, and flexible.',
        'name': 'Nelson',
        'role': 'Practitioner',
        'company': '',
    },
]

FEATURES = [
    {
        'icon': 'hammer',
        'title': 'Learning by doing',
        'description': 'No passive consumption. Every activity is designed around building, shipping, and getting feedback on real work.',
    },
    {
        'icon': 'rocket',
        'title': 'Production-ready',
        'description': 'Focus on what actually works in production. Move from prototypes to reliable systems with battle-tested patterns.',
    },
    {
        'icon': 'users',
        'title': 'Build together',
        'description': 'Work alongside other practitioners. Hackathons, projects, and group problem-solving instead of isolated learning.',
    },
    {
        'icon': 'brain',
        'title': 'Calibrate your judgment',
        'description': 'Develop better instincts through peer feedback, expert guidance, and exposure to real-world decision-making patterns.',
    },
]


FAQ_ITEMS = [
    {
        'question': 'Who is this community for?',
        'answer': "Action-oriented builders interested in AI engineering and AI tools who want to turn ideas into real projects. Whether you're learning Python or working as an ML engineer, if you have project ideas but need structure, focus, and accountability, this community is for you. We attract motivated learners who prefer learning by doing and builders who contribute back to the ecosystem.",
    },
    {
        'question': 'What makes this different from other tech communities?',
        'answer': 'We focus on helping you ship practical AI products, not just consume content. You get clear frameworks, direction, and gentle external pressure to make consistent progress on your projects. The community concentrates highly engaged builders in a focused environment centered on productivity, structured execution, and hands-on project work.',
    },
    {
        'question': 'I have a main job. Can I still participate?',
        'answer': 'Yes. The community is designed to help you make consistent progress on side projects even with limited time. You get the structure and accountability to stay focused and ship incrementally through projects, hackathons, and collaborative activities.',
    },
    {
        'question': 'What if I just want the content without community?',
        'answer': 'The Basic tier is designed exactly for this. You get access to exclusive content, tutorials, research, and curated materials without any expectation of community participation. Perfect for self-directed builders who learn at their own pace.',
    },
    {
        'question': "What's included in the Main tier?",
        'answer': 'Main tier gives you the structure, accountability, and peer support to ship your AI projects consistently. Includes everything in Basic, plus closed community access, collaborative problem-solving, interactive group coding sessions, guided projects, hackathons, career discussions, and the ability to propose and vote on topics.',
    },
    {
        'question': "What's included in the Premium tier?",
        'answer': 'Premium tier accelerates your growth with structured learning paths through mini-courses and personalized career guidance. Includes everything in Main, plus access to all mini-courses on specialized topics, the ability to vote on course topics, and professional profile teardowns (resume, LinkedIn, GitHub).',
    },
    {
        'question': 'How do I get started?',
        'answer': "Pick the tier that fits your needs, click the button to check out securely via Stripe, and you'll receive access details by email within 24 hours. You can start with any tier and upgrade or downgrade at any time.",
    },
    {
        'question': 'How does billing work?',
        'answer': "All payments are processed securely through Stripe. You can choose monthly or annual billing (annual saves ~17%). Stripe handles tax calculation automatically based on your location. You'll receive invoices and receipts by email after each payment.",
    },
    {
        'question': 'Can I cancel or change my subscription?',
        'answer': "Yes, you're in full control. You can cancel, upgrade, downgrade, or update your payment method at any time through the Stripe Customer Portal. If you cancel, you'll retain access until the end of your current billing period.",
    },
]

SECTION_NAV = [
    {'id': 'about', 'label': 'Philosophy'},
    {'id': 'tiers', 'label': 'Membership'},
    {'id': 'testimonials', 'label': 'Testimonials'},
    {'id': 'resources', 'label': 'Workshops'},
    {'id': 'blog', 'label': 'Blog'},
    {'id': 'projects', 'label': 'Projects'},
    {'id': 'collection', 'label': 'Curated Links'},
    {'id': 'newsletter', 'label': 'Newsletter'},
    {'id': 'faq', 'label': 'FAQ'},
]


@ensure_csrf_cookie
def home(request):
    """Homepage view.

    Authenticated users see a personalized dashboard.
    Anonymous users see the public marketing homepage.
    """
    if request.user.is_authenticated:
        return _dashboard(request)
    return _public_home(request)


def _public_home(request):
    """Render the public marketing homepage for anonymous users."""
    articles = Article.objects.filter(published=True)[:3]
    recordings = Event.objects.filter(
        published=True,
    ).exclude(
        recording_url='',
    ).exclude(
        recording_url__isnull=True,
    ).order_by('-start_datetime')[:3]
    projects = Project.objects.filter(published=True)[:3]
    curated_links = CuratedLink.objects.filter(published=True)[:6]

    # Add payment links to tiers
    stripe_links = settings.STRIPE_PAYMENT_LINKS
    tiers_with_links = []
    for tier in get_tiers_with_features():
        tier_copy = dict(tier)
        key = tier['stripe_key']
        tier_copy['payment_link_monthly'] = stripe_links.get(key, {}).get('monthly', '#')
        tier_copy['payment_link_annual'] = stripe_links.get(key, {}).get('annual', '#')
        tiers_with_links.append(tier_copy)

    context = {
        'articles': articles,
        'recordings': recordings,
        'projects': projects,
        'curated_links': curated_links,
        'testimonials': TESTIMONIALS,
        'features': FEATURES,
        'tiers': tiers_with_links,
        'faq_items': FAQ_ITEMS,
        'section_nav': SECTION_NAV,
    }
    return render(request, 'home.html', context)


def _dashboard(request):
    """Render the personalized dashboard for authenticated users."""
    from django.contrib.auth import get_user_model
    User = get_user_model()

    # Re-fetch user with select_related('tier') to avoid a lazy-load query
    # every time user.tier.name or user.tier.level is accessed.
    user = User.objects.select_related('tier').get(pk=request.user.pk)

    # --- Welcome banner ---
    # Fetch active tier override once; pass it to get_user_level to avoid a
    # duplicate DB query (get_user_level internally queries for the override too).
    active_override = get_active_override(user)
    user_level = get_user_level(user, active_override=active_override)

    tier_name = ''
    if user.tier_id:
        tier_name = user.tier.name

    # If there is an active override, show the override tier name with "(trial)"
    override_tier_name = ''
    if active_override is not None:
        override_tier_name = active_override.override_tier.name

    # --- Continue learning ---
    # Find courses where the user has progress (at least one unit accessed)
    # and compute completion percentage + last accessed unit.
    # A course is "in progress" if the user has at least one completed unit
    # but has not completed all units.
    #
    # Issue #365 — workshops with at least one completed page (and not yet
    # finished) are merged into the same list under a unified ``kind``
    # discriminator. Course-only users get the same ordering as before;
    # the merged list short-circuits the workshop branch when the user has
    # no workshop completions.
    in_progress_learning = _get_in_progress_learning(user, user_level)
    # Backward-compatible alias used by existing course-only tests
    # (test_dashboard.py, test_dashboard_performance.py): the unified
    # list filtered down to ``kind='course'`` items so the in-template
    # iteration and the assertion shape don't shift under those tests.
    in_progress_courses = [
        item for item in in_progress_learning if item.get('kind') == 'course'
    ]

    # --- Upcoming events ---
    upcoming_events = _get_upcoming_events(user)

    # --- Recent content ---
    recent_content = _get_recent_content(user_level)

    # --- Active polls ---
    active_polls = _get_active_polls(user_level)

    # --- Quick actions ---
    quick_actions = _get_quick_actions(user_level)

    # --- Notifications ---
    notifications = _get_notifications(user)

    # --- Slack community ---
    # Slack join link is based on user.tier.level (NOT overridden level)
    # because Slack access requires a paid subscription.
    from content.access import LEVEL_MAIN
    slack_invite_url = getattr(settings, 'SLACK_INVITE_URL', '')
    has_qualifying_tier = user.tier_id and user.tier.level >= LEVEL_MAIN
    # Issue #358: gate on the verified ``slack_member`` boolean rather
    # than ``slack_user_id`` (which is populated by Slack OAuth even
    # for users who never joined the workspace).
    show_slack_join = bool(
        slack_invite_url and has_qualifying_tier and not user.slack_member
    )
    slack_connected = bool(has_qualifying_tier and user.slack_member)

    context = {
        'tier_name': tier_name,
        'override_tier_name': override_tier_name,
        'in_progress_courses': in_progress_courses,
        'in_progress_learning': in_progress_learning,
        'upcoming_events': upcoming_events,
        'recent_content': recent_content,
        'active_polls': active_polls,
        'quick_actions': quick_actions,
        'notifications': notifications,
        'show_slack_join': show_slack_join,
        'slack_connected': slack_connected,
        'slack_invite_url': slack_invite_url,
    }
    return render(request, 'content/dashboard.html', context)


def _get_in_progress_courses(user, user_level):
    """Return courses the user is enrolled in but hasn't finished.

    Issue #236 — driven by ``Enrollment`` rows, not by inferring "in
    progress" from completed-unit counts. A course appears here when:

    - There's an active ``Enrollment`` (``unenrolled_at IS NULL``).
    - The user's current tier still meets ``course.required_level``
      (the enrollment is preserved, just hidden from the dashboard
      until the tier qualifies again).
    - The course has at least one unit and the user hasn't completed
      every unit yet.

    Sorted by most recent activity: latest ``UserCourseProgress.completed_at``
    on the course, falling back to the enrollment's ``enrolled_at`` for
    courses where the user has enrolled but not yet completed anything.

    Implementation note: keeps the query count constant regardless of
    enrollment count (N+1 guarded by ``content/tests/test_dashboard_performance.py``).
    """
    # Pull active enrollments + the course in a single query.
    enrollments = list(
        Enrollment.objects
        .filter(user=user, unenrolled_at__isnull=True)
        .select_related('course')
    )

    if not enrollments:
        return []

    course_by_id: dict[int, Course] = {}
    enrolled_at_by_course: dict[int, object] = {}
    for enr in enrollments:
        course_by_id[enr.course_id] = enr.course
        enrolled_at_by_course[enr.course_id] = enr.enrolled_at

    course_ids = list(course_by_id.keys())

    # Batch-fetch total unit counts for all enrolled courses (one query).
    unit_counts = dict(
        Course.objects.filter(id__in=course_ids).annotate(
            unit_count=Count('modules__units')
        ).values_list('id', 'unit_count')
    )

    # Per-course completed unit ids + last completion timestamp + the
    # unit that was completed last. One query for all enrolled courses.
    progress_qs = (
        UserCourseProgress.objects
        .filter(
            user=user,
            unit__module__course_id__in=course_ids,
            completed_at__isnull=False,
        )
        .select_related('unit__module')
    )
    completed_by_course: dict[int, set[int]] = {cid: set() for cid in course_ids}
    last_completed_by_course: dict[int, object] = {}
    last_unit_by_course: dict[int, Unit] = {}
    for prog in progress_qs:
        cid = prog.unit.module.course_id
        completed_by_course.setdefault(cid, set()).add(prog.unit_id)
        prev = last_completed_by_course.get(cid)
        if prev is None or prog.completed_at > prev:
            last_completed_by_course[cid] = prog.completed_at
            last_unit_by_course[cid] = prog.unit

    # Batch-fetch all units for the enrolled courses in a single query,
    # ordered canonically. We resolve next_unit in Python — no per-course
    # DB calls (N+1 guarded by test_dashboard_performance.py).
    units_by_course: dict[int, list[Unit]] = {cid: [] for cid in course_ids}
    units_qs = (
        Unit.objects.filter(module__course_id__in=course_ids)
        .select_related('module')
        .order_by('module__sort_order', 'sort_order')
    )
    for unit in units_qs:
        units_by_course[unit.module.course_id].append(unit)

    # Batch-fetch individual CourseAccess grants for all enrolled courses
    # in a single query. Avoids N+1 from calling can_access() per course
    # (issue #346): can_access() falls back to a CourseAccess.exists()
    # query whenever the user's tier level is below required_level.
    granted_course_ids = set(
        CourseAccess.objects.filter(
            user=user, course_id__in=course_ids,
        ).values_list('course_id', flat=True)
    )

    result = []
    for cid, course in course_by_id.items():
        # Hide enrollments the user no longer has access to. Mirrors the
        # logic of content.access.can_access(): tier level must meet
        # required_level, OR the user must hold an individual
        # CourseAccess grant (purchase or admin grant). Both inputs are
        # already batched above — no per-course DB call here.
        if course.required_level > user_level and cid not in granted_course_ids:
            continue
        total = unit_counts.get(cid, 0)
        if total == 0:
            # Course has no units — skip silently. Defensive: ought not to
            # happen for a published course but we don't want a divide-by-zero
            # below either.
            continue
        completed_unit_ids = completed_by_course.get(cid, set())
        completed_count = len(completed_unit_ids)
        if completed_count >= total:
            # Fully completed — out of "in progress".
            continue
        percentage = int((completed_count / total) * 100)
        # Resolve next_unit: first unit in canonical order not in
        # completed_unit_ids. With a fresh enrollment (zero completions)
        # this is the very first unit.
        next_unit = None
        for unit in units_by_course.get(cid, []):
            if unit.id not in completed_unit_ids:
                next_unit = unit
                break
        # last_unit / last_completed_at fall back to enrolled_at + None
        # for users who haven't completed anything yet.
        last_completed_at = last_completed_by_course.get(cid)
        last_unit = last_unit_by_course.get(cid)
        # Sort key: prefer the most recent completion; fall back to the
        # enrollment timestamp so freshly-enrolled (zero-progress) courses
        # still slot into the list in a sensible order.
        sort_key = last_completed_at or enrolled_at_by_course[cid]
        result.append({
            'kind': 'course',
            'course': course,
            'completed_count': completed_count,
            'total_units': total,
            'percentage': percentage,
            'last_unit': last_unit,
            'last_completed_at': last_completed_at,
            'next_unit': next_unit,
            'enrolled_at': enrolled_at_by_course[cid],
            '_sort_key': sort_key,
        })

    result.sort(key=lambda x: x['_sort_key'], reverse=True)
    for item in result:
        del item['_sort_key']
    return result


def _get_in_progress_workshops(user, user_level):
    """Return workshops the user has started but not finished (issue #365).

    A workshop appears here when:

    - The user has at least one ``UserContentCompletion`` row with
      ``content_type='workshop_page'`` whose ``object_id`` resolves to
      a page on a published workshop.
    - The user's effective tier still meets
      ``workshop.pages_required_level`` (mirrors the course
      tier-recheck path; we don't surface workshops the user can no
      longer access).
    - At least one page on the workshop is not yet completed.

    There is no workshop-level enrollment table — completing a page is
    the implicit "I am taking this workshop" signal. This mirrors the
    course auto-enroll-on-progress behaviour.

    Sorted by most-recent ``completed_at`` desc. The unified list
    sorts again across kinds in :func:`_get_in_progress_learning`.

    Implementation note: keeps the query count constant. Total query
    cost (read by the next test ``InProgressLearningQueryCountTest``):
      1. Fetch all completion rows for this user
      2. Fetch the workshops for those rows + all their pages
    """
    completion_qs = UserContentCompletion.objects.filter(
        user=user,
        content_type=CONTENT_TYPE_WORKSHOP_PAGE,
    ).order_by('-completed_at')
    completions = list(completion_qs)
    if not completions:
        return []

    completed_page_ids: set[int] = set()
    last_completion_at_by_page: dict[int, object] = {}
    for c in completions:
        completed_page_ids.add(c.object_id)
        prev = last_completion_at_by_page.get(c.object_id)
        if prev is None or c.completed_at > prev:
            last_completion_at_by_page[c.object_id] = c.completed_at

    # Resolve the pages -> workshops via prefetch so we can iterate
    # ``workshop.pages`` from the cache rather than firing per-workshop
    # queries.
    pages_qs = (
        WorkshopPage.objects
        .filter(pk__in=completed_page_ids)
        .select_related('workshop')
    )
    workshop_ids = {p.workshop_id for p in pages_qs}
    if not workshop_ids:
        return []

    workshops = list(
        Workshop.objects
        .filter(pk__in=workshop_ids, status='published')
        .prefetch_related('pages')
    )

    result = []
    for workshop in workshops:
        # Tier recheck — hide workshops the user can no longer access.
        if user_level < workshop.pages_required_level:
            continue
        all_pages = list(workshop.pages.all())
        if not all_pages:
            continue
        total = len(all_pages)
        completed_count = sum(
            1 for p in all_pages if p.id in completed_page_ids
        )
        if completed_count == 0:
            # Defensive: completion rows existed but none matched a
            # current page — likely the page was deleted. Skip.
            continue
        if completed_count >= total:
            # Fully completed — out of "in progress".
            continue
        percentage = int((completed_count / total) * 100)

        # Resolve next_page: first page in sort order without a
        # completion row.
        next_page = None
        last_page = None
        last_completed_at = None
        for page in all_pages:
            if page.id not in completed_page_ids and next_page is None:
                next_page = page
            ts = last_completion_at_by_page.get(page.id)
            if ts is not None and (
                last_completed_at is None or ts > last_completed_at
            ):
                last_completed_at = ts
                last_page = page

        result.append({
            'kind': 'workshop',
            'workshop': workshop,
            'completed_count': completed_count,
            'total_units': total,
            'percentage': percentage,
            'last_page': last_page,
            'last_completed_at': last_completed_at,
            'next_page': next_page,
            '_sort_key': last_completed_at,
        })

    result.sort(
        key=lambda x: x['_sort_key'] or 0,
        reverse=True,
    )
    for item in result:
        del item['_sort_key']
    return result


def _get_in_progress_learning(user, user_level):
    """Return a unified Continue-Learning list (issue #365).

    Merges :func:`_get_in_progress_courses` and
    :func:`_get_in_progress_workshops` and sorts by most-recent
    activity descending. Each item carries a ``kind`` discriminator
    (``'course'`` or ``'workshop'``) so the dashboard template branches
    once per row.

    The implementation deliberately concatenates the two specialised
    helpers rather than rewriting them as a single query, because:

    - ``UserCourseProgress`` and ``UserContentCompletion`` live in
      different tables with no shared FK.
    - The course path already has a stable, N+1-safe query plan
      (verified by ``test_dashboard_performance.py``); keeping that
      function intact avoids regressing the course-only path while we
      bolt on the workshop one.
    """
    course_items = _get_in_progress_courses(user, user_level)
    workshop_items = _get_in_progress_workshops(user, user_level)
    if not workshop_items:
        # Pure course path — preserves item ordering exactly.
        return course_items

    def _activity_key(item):
        if item['kind'] == 'course':
            return item['last_completed_at'] or item['enrolled_at']
        return item['last_completed_at']

    merged = course_items + workshop_items
    merged.sort(key=_activity_key, reverse=True)
    return merged


def _get_upcoming_events(user):
    """Return the next 3 events the user is registered for."""
    from events.models import EventRegistration
    now = timezone.now()
    registrations = EventRegistration.objects.filter(
        user=user,
        event__start_datetime__gt=now,
        event__status='upcoming',
    ).select_related('event').order_by('event__start_datetime')[:3]
    return [reg.event for reg in registrations]


def _get_recent_content(user_level):
    """Return latest 5 published articles/recordings the user can access."""
    # Get accessible articles
    articles = list(
        Article.objects.filter(
            published=True,
            required_level__lte=user_level,
        ).order_by('-date')[:5]
    )

    # Get accessible recordings (events with recording_url)
    recordings = list(
        Event.objects.filter(
            published=True,
            required_level__lte=user_level,
        ).exclude(
            recording_url='',
        ).exclude(
            recording_url__isnull=True,
        ).order_by('-start_datetime')[:5]
    )

    # Merge and sort by date, take top 5
    combined = []
    for article in articles:
        combined.append({
            'type': 'article',
            'title': article.title,
            'description': article.description,
            'url': article.get_absolute_url(),
            'date': article.date,
            'icon': 'file-text',
        })
    for recording in recordings:
        combined.append({
            'type': 'recording',
            'title': recording.title,
            'description': recording.description,
            'url': recording.get_absolute_url(),
            'date': recording.start_datetime.date(),
            'icon': 'video',
        })

    import datetime as dt
    def _sort_key(x):
        val = x['date']
        if isinstance(val, dt.datetime):
            return val
        if isinstance(val, dt.date):
            return dt.datetime.combine(val, dt.time.min, tzinfo=dt.timezone.utc)
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    combined.sort(key=_sort_key, reverse=True)
    return combined[:5]


def _get_active_polls(user_level):
    """Return up to 2 open polls the user can participate in."""
    from voting.models import Poll
    now = timezone.now()
    polls = Poll.objects.filter(
        status='open',
        required_level__lte=user_level,
    ).filter(
        Q(closes_at__isnull=True) | Q(closes_at__gt=now),
    ).order_by('-created_at')[:2]
    return list(polls)


def _get_quick_actions(user_level):
    """Build quick action cards based on user's tier level."""
    from content.access import LEVEL_MAIN
    actions = [
        {
            'title': 'Browse Courses',
            'description': 'Explore structured learning paths',
            'url': '/courses',
            'icon': 'book-open',
        },
        {
            'title': 'View Recordings',
            'description': 'Watch event recordings and workshops',
            'url': '/events?filter=past',
            'icon': 'video',
        },
    ]
    if user_level >= LEVEL_MAIN:
        actions.append({
            'title': 'Community',
            'description': 'Connect with other builders',
            'url': '/community',
            'icon': 'users',
        })
    actions.append({
        'title': 'Submit Project',
        'description': 'Share your work with the community',
        'url': '/projects',
        'icon': 'rocket',
    })
    return actions


def _get_notifications(user):
    """Return latest 5 unread notifications for the user."""
    from notifications.models import Notification
    return list(
        Notification.objects.filter(
            user=user,
            read=False,
        ).order_by('-created_at')[:5]
    )
