import markdown
from django.http import Http404
from django.shortcuts import render


# Canonical ordering of interview question categories
INTERVIEW_CATEGORY_ORDER = [
    'theory',
    'coding',
    'system-design',
    'project-deep-dive',
    'behavioral',
    'home-assignments',
]


def _get_categories_from_db():
    """Load interview categories from the database, ordered canonically."""
    from content.models import InterviewCategory

    all_categories = list(InterviewCategory.objects.all())
    if not all_categories:
        return []

    # Build a lookup by slug
    by_slug = {cat.slug: cat for cat in all_categories}

    result = []
    seen = set()

    # Add in canonical order first
    for slug in INTERVIEW_CATEGORY_ORDER:
        if slug in by_slug:
            cat = by_slug[slug]
            result.append({
                'slug': cat.slug,
                'title': cat.title,
                'description': cat.description,
                'status': cat.status,
                'sections': cat.sections_json,
                'body': cat.body_markdown,
            })
            seen.add(slug)

    # Add any remaining categories not in canonical order
    for cat in all_categories:
        if cat.slug not in seen:
            result.append({
                'slug': cat.slug,
                'title': cat.title,
                'description': cat.description,
                'status': cat.status,
                'sections': cat.sections_json,
                'body': cat.body_markdown,
            })

    return result


def _get_category_from_db(slug):
    """Load a single interview category from the database."""
    from content.models import InterviewCategory

    try:
        cat = InterviewCategory.objects.get(slug=slug)
        return {
            'slug': cat.slug,
            'title': cat.title,
            'description': cat.description,
            'status': cat.status,
            'sections': cat.sections_json,
            'body': cat.body_markdown,
        }
    except InterviewCategory.DoesNotExist:
        return None


def interview_hub(request):
    """Hub page listing all interview question categories."""
    categories = _get_categories_from_db()

    if not categories:
        raise Http404("Interview questions content not available.")

    context = {
        'categories': categories,
    }
    return render(request, 'content/interview_hub.html', context)


def _render_markdown(text):
    """Render markdown text to HTML."""
    return markdown.markdown(
        text,
        extensions=[
            'fenced_code',
            'codehilite',
            'tables',
            'attr_list',
            'md_in_html',
        ],
    )


def interview_detail(request, slug):
    """Detail page for a specific interview question category."""
    data = _get_category_from_db(slug)

    if data is None:
        raise Http404(f"Interview category '{slug}' not found.")

    # If it's a coming-soon page, show 404
    if data['status'] == 'coming-soon':
        raise Http404(f"Interview category '{slug}' is coming soon.")

    # Split body at <!-- after-questions --> marker
    body = data['body']
    before_questions_html = ''
    after_questions_html = ''

    if '<!-- after-questions -->' in body:
        parts = body.split('<!-- after-questions -->', 1)
        before_questions_html = _render_markdown(parts[0].strip())
        after_questions_html = _render_markdown(parts[1].strip())
    else:
        before_questions_html = _render_markdown(body.strip())

    # Render section intros as markdown too
    sections = data['sections']
    for section in sections:
        if section.get('intro'):
            section['intro_html'] = _render_markdown(section['intro'])

    context = {
        'category': data,
        'sections': sections,
        'before_questions_html': before_questions_html,
        'after_questions_html': after_questions_html,
    }
    return render(request, 'content/interview_detail.html', context)
