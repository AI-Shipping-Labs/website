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
def video_player(video_url, timestamps=None):
    """
    Render a video player component.

    Args:
        video_url: URL of the video (YouTube, Loom, or self-hosted mp4/webm)
        timestamps: Optional list of dicts with 'time_seconds' and 'label'

    Returns:
        Context dict for the video_player.html template.
    """
    return prepare_video_context(video_url, timestamps)
