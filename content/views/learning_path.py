from pathlib import Path

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


def _get_learning_path_from_db(slug):
    """Load a learning path from the database by slug."""
    from content.models import LearningPath

    try:
        return LearningPath.objects.get(slug=slug)
    except LearningPath.DoesNotExist:
        return None


def learning_path_ai_engineer(request):
    """Visual learning path page for AI engineers."""
    # Try DB first
    lp = _get_learning_path_from_db('ai-engineer')

    if lp is not None:
        data = lp.data_json
    else:
        # Fall back to disk
        repo = _get_content_repo_dir()
        if repo is None:
            raise Http404("Learning path content not available.")

        data_path = repo / 'learning-path' / 'ai-engineer' / 'data.yaml'
        if not data_path.exists():
            raise Http404("AI engineer learning path data not found.")

        with open(data_path, 'r') as f:
            data = yaml.safe_load(f)

    context = {
        'title': data.get('title', 'AI Engineer Learning Path'),
        'description': data.get('description', ''),
        'skill_categories': data.get('skill_categories', []),
        'tool_categories': data.get('tool_categories', []),
        'responsibilities': data.get('responsibilities', {}),
        'portfolio_projects': data.get('portfolio_projects', []),
        'learning_stages': data.get('learning_stages', []),
    }
    return render(request, 'content/learning_path_ai_engineer.html', context)
