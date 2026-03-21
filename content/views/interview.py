import os
from pathlib import Path

import frontmatter
import markdown
import yaml
from django.conf import settings
from django.http import Http404
from django.shortcuts import render


def _get_content_repo_dir():
    """Return the content repo directory path, or None if not configured/available."""
    repo_dir = getattr(settings, 'CONTENT_REPO_DIR', None)
    if repo_dir and Path(repo_dir).is_dir():
        return Path(repo_dir)
    return None


def _get_interview_dir():
    """Return the interview-questions directory, or None if unavailable."""
    repo = _get_content_repo_dir()
    if repo is None:
        return None
    interview_dir = repo / 'interview-questions'
    if interview_dir.is_dir():
        return interview_dir
    return None


def _parse_interview_file(filepath):
    """Parse an interview questions markdown file and return its metadata."""
    post = frontmatter.load(filepath)
    slug = filepath.stem  # filename without .md
    return {
        'slug': slug,
        'title': post.get('title', slug.replace('-', ' ').title()),
        'description': post.get('description', ''),
        'status': post.get('status', ''),
        'sections': post.get('sections', []),
        'body': post.content,
    }


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
        return None  # No DB data, fall back to disk

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
    # Try DB first
    categories = _get_categories_from_db()

    if categories is None:
        # Fall back to disk
        interview_dir = _get_interview_dir()
        if interview_dir is None:
            raise Http404("Interview questions content not available.")

        categories = []
        for slug in INTERVIEW_CATEGORY_ORDER:
            filepath = interview_dir / f'{slug}.md'
            if filepath.exists():
                categories.append(_parse_interview_file(filepath))

        # Also pick up any files not in the canonical order
        seen_slugs = set(INTERVIEW_CATEGORY_ORDER)
        for filepath in sorted(interview_dir.glob('*.md')):
            if filepath.stem not in seen_slugs:
                categories.append(_parse_interview_file(filepath))

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
    # Try DB first
    data = _get_category_from_db(slug)

    if data is None:
        # Fall back to disk
        interview_dir = _get_interview_dir()
        if interview_dir is None:
            raise Http404("Interview questions content not available.")

        filepath = interview_dir / f'{slug}.md'
        if not filepath.exists():
            raise Http404(f"Interview category '{slug}' not found.")

        data = _parse_interview_file(filepath)

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
