"""
Video utility functions for URL detection, timestamp formatting,
and markdown video embed processing.
"""

import re


# Patterns for detecting video sources
YOUTUBE_PATTERNS = [
    re.compile(r'https?://(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)'),
    re.compile(r'https?://youtu\.be/([a-zA-Z0-9_-]+)'),
    re.compile(r'https?://(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]+)'),
]

LOOM_PATTERN = re.compile(r'https?://(?:www\.)?loom\.com/share/([a-zA-Z0-9]+)')

SELF_HOSTED_PATTERN = re.compile(r'https?://\S+\.(mp4|webm)(\?\S*)?$', re.IGNORECASE)

# Pattern for detecting standalone video URLs in markdown (on their own line)
MARKDOWN_VIDEO_LINE = re.compile(
    r'^(?P<url>https?://(?:'
    r'(?:www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]+'
    r'|youtu\.be/[a-zA-Z0-9_-]+'
    r'|(?:www\.)?loom\.com/share/[a-zA-Z0-9]+'
    r'))$',
    re.MULTILINE,
)


def detect_video_source(url):
    """
    Detect the video source type from a URL.

    Returns a tuple of (source_type, video_id_or_url) where source_type is
    one of 'youtube', 'loom', 'self_hosted', or None.
    """
    if not url:
        return None, None

    url = url.strip()

    # Check YouTube patterns
    for pattern in YOUTUBE_PATTERNS:
        match = pattern.search(url)
        if match:
            return 'youtube', match.group(1)

    # Check Loom pattern
    match = LOOM_PATTERN.search(url)
    if match:
        return 'loom', match.group(1)

    # Check self-hosted video
    if SELF_HOSTED_PATTERN.search(url):
        return 'self_hosted', url

    return None, None


def get_youtube_embed_url(video_id):
    """Generate YouTube embed URL with API enabled."""
    return f'https://www.youtube.com/embed/{video_id}?enablejsapi=1'


def get_loom_embed_url(video_id, time_seconds=None):
    """Generate Loom embed URL, optionally with timestamp."""
    base = f'https://www.loom.com/embed/{video_id}'
    if time_seconds is not None:
        return f'{base}?t={time_seconds}'
    return base


def format_timestamp(time_seconds):
    """
    Format seconds into [MM:SS] or [H:MM:SS] display string.

    Returns formatted string like [02:05] or [1:13:00].
    """
    try:
        time_seconds = int(time_seconds)
    except (TypeError, ValueError):
        return '[00:00]'

    if time_seconds < 0:
        time_seconds = 0

    hours = time_seconds // 3600
    minutes = (time_seconds % 3600) // 60
    seconds = time_seconds % 60

    if hours > 0:
        return f'[{hours}:{minutes:02d}:{seconds:02d}]'
    return f'[{minutes:02d}:{seconds:02d}]'


def parse_time_input(time_str):
    """
    Parse a time string in MM:SS or H:MM:SS format to seconds.

    Returns integer seconds, or 0 if parsing fails.
    """
    if not time_str:
        return 0

    time_str = time_str.strip()
    parts = time_str.split(':')

    try:
        if len(parts) == 2:
            # MM:SS
            minutes, seconds = int(parts[0]), int(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 3:
            # H:MM:SS
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
            return hours * 3600 + minutes * 60 + seconds
    except (ValueError, IndexError):
        pass

    return 0


def prepare_video_context(video_url, timestamps=None):
    """
    Prepare template context for rendering a video player.

    Args:
        video_url: The URL of the video
        timestamps: Optional list of dicts with 'time_seconds' and 'label' keys

    Returns:
        Dict with all data needed to render the video player template.
    """
    source_type, video_id = detect_video_source(video_url)

    context = {
        'video_url': video_url,
        'source_type': source_type,
        'video_id': video_id,
        'embed_url': None,
        'timestamps': [],
        'has_timestamps': False,
    }

    if source_type == 'youtube' and video_id:
        context['embed_url'] = get_youtube_embed_url(video_id)
    elif source_type == 'loom' and video_id:
        context['embed_url'] = get_loom_embed_url(video_id)
    elif source_type == 'self_hosted':
        context['embed_url'] = video_url

    if timestamps:
        formatted_timestamps = []
        for ts in timestamps:
            time_seconds = ts.get('time_seconds', 0)
            label = ts.get('label', '')
            formatted_timestamps.append({
                'time_seconds': time_seconds,
                'label': label,
                'formatted_time': format_timestamp(time_seconds),
            })
        context['timestamps'] = formatted_timestamps
        context['has_timestamps'] = len(formatted_timestamps) > 0

    return context


def replace_video_urls_in_html(html_content):
    """
    Detect standalone YouTube and Loom URLs in HTML content and replace
    them with VideoPlayer embeds (without timestamps).

    This processes the HTML output from markdown rendering. It looks for
    paragraphs that contain only a video URL (i.e., <p>URL</p> pattern)
    and replaces them with the video player HTML.
    """
    if not html_content:
        return html_content

    # Match <p> tags containing only a video URL
    p_video_pattern = re.compile(
        r'<p>\s*(?P<url>https?://(?:'
        r'(?:www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]+'
        r'|youtu\.be/[a-zA-Z0-9_-]+'
        r'|(?:www\.)?loom\.com/share/[a-zA-Z0-9]+'
        r'))\s*</p>',
        re.IGNORECASE,
    )

    def replace_match(match):
        url = match.group('url')
        source_type, video_id = detect_video_source(url)

        if source_type == 'youtube' and video_id:
            embed_url = get_youtube_embed_url(video_id)
            return (
                f'<div class="video-player mb-8" data-source="youtube" data-video-id="{video_id}">'
                f'<div class="aspect-video rounded-lg overflow-hidden border border-border">'
                f'<iframe src="{embed_url}" class="w-full h-full" '
                f'allowfullscreen allow="accelerometer; autoplay; clipboard-write; '
                f'encrypted-media; gyroscope; picture-in-picture"></iframe>'
                f'</div></div>'
            )
        elif source_type == 'loom' and video_id:
            embed_url = get_loom_embed_url(video_id)
            return (
                f'<div class="video-player mb-8" data-source="loom" data-video-id="{video_id}">'
                f'<div class="aspect-video rounded-lg overflow-hidden border border-border">'
                f'<iframe src="{embed_url}" class="w-full h-full" '
                f'allowfullscreen></iframe>'
                f'</div></div>'
            )

        return match.group(0)

    return p_video_pattern.sub(replace_match, html_content)
