"""Template helpers for GitHub links."""

from django import template

from content.services.github_commit import github_commit_url

register = template.Library()


@register.simple_tag
def commit_url(submission):
    """URL of the submission's pinned commit on GitHub, or '' when unavailable."""
    return github_commit_url(submission.project_url, submission.commit_id)
