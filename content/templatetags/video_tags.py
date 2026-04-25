"""
Django template tags for rendering video players.

Usage in templates:
    {% load video_tags %}
    {% video_player video_url="https://youtube.com/watch?v=xxx" %}
    {% video_player video_url=recording.youtube_url timestamps=recording.timestamps %}
"""

from django import template

from .video_utils import prepare_video_context

register = template.Library()


@register.inclusion_tag('includes/video_player.html')
def video_player(video_url, timestamps=None, start_seconds=None):
    """
    Render a video player component.

    Args:
        video_url: URL of the video (YouTube, Loom, or self-hosted mp4/webm)
        timestamps: Optional list of dicts with 'time_seconds'/'label'
            (canonical) or 'time'/'title' (workshop YAML).
        start_seconds: Optional integer seconds to seek to on initial
            load. Used by the workshop video page when navigated to with
            a ``?t=MM:SS`` query string.

    Returns:
        Context dict for the video_player.html template.
    """
    return prepare_video_context(video_url, timestamps, start_seconds=start_seconds)
