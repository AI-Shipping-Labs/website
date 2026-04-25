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


def get_video_thumbnail_url(video_url):
    """Return a static thumbnail URL for a video, or None if unsupported.

    YouTube exposes ``hqdefault.jpg`` for every video at a stable URL; Loom
    exposes ``i.loom.com/.../with-play.gif`` (we use the static jpg form).
    Self-hosted videos have no convenient thumbnail endpoint, so we return
    None and let the caller render a generic placeholder.

    Used by the locked-lesson teaser (issue #248) so we can show the user
    *what* they'd be watching without auto-loading the video player.
    """
    source_type, video_id = detect_video_source(video_url)
    if source_type == 'youtube' and video_id:
        return f'https://img.youtube.com/vi/{video_id}/hqdefault.jpg'
    if source_type == 'loom' and video_id:
        return f'https://cdn.loom.com/sessions/thumbnails/{video_id}-with-play.jpg'
    return None


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


# Strict integer-component pattern: digits only, no signs, no whitespace
# inside a component. Used by parse_video_timestamp.
_TIMESTAMP_COMPONENT = re.compile(r'^\d+$')


def parse_video_timestamp(value):
    """Parse a ``MM:SS`` or ``H:MM:SS`` workshop timestamp into seconds.

    Strict parser used for ``WorkshopPage.video_start`` and the ``?t=``
    query param on the workshop video page. Unlike ``parse_time_input``,
    malformed input raises ``ValueError`` so callers can decide whether
    to log a warning, skip the value, or fall back to 0.

    Accepts (returns int seconds):
      "0:00", "00:00", "0:00:00", "16:00", "1:23:45", "1:00:00"

    Rejects (raises ``ValueError``):
      "", "  ", None, "16", "1:2:3:4", "1:60", "abc:def", "-1:00",
      "1:60:00" (minutes >= 60 in H:MM:SS form)

    Each component must be all digits (zero-padded or bare). Negative
    values, signs, and non-digit characters are rejected. In ``MM:SS``
    form, both components may be >= 60 (an old YAML quirk where authors
    wrote ``"75:00"`` instead of ``"1:15:00"``); in ``H:MM:SS`` form,
    minutes and seconds must be < 60.
    """
    if value is None:
        raise ValueError('timestamp is None')
    if not isinstance(value, str):
        raise ValueError(f'timestamp must be a string, got {type(value).__name__}')
    s = value.strip()
    if not s:
        raise ValueError('timestamp is empty')

    parts = s.split(':')
    if len(parts) not in (2, 3):
        raise ValueError(
            f'timestamp {value!r} must have 2 or 3 components separated by ":"'
        )

    for part in parts:
        if not _TIMESTAMP_COMPONENT.match(part):
            raise ValueError(
                f'timestamp component {part!r} is not a non-negative integer'
            )

    if len(parts) == 2:
        minutes, seconds = int(parts[0]), int(parts[1])
        return minutes * 60 + seconds

    hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
    if minutes >= 60:
        raise ValueError(
            f'timestamp {value!r}: minutes must be < 60 in H:MM:SS form'
        )
    if seconds >= 60:
        raise ValueError(
            f'timestamp {value!r}: seconds must be < 60 in H:MM:SS form'
        )
    return hours * 3600 + minutes * 60 + seconds


def append_query_param(url, key, value):
    """Return ``url`` with ``?key=value`` (or ``&key=value``) appended.

    Used to add a ``start=N`` parameter to ``recording_embed_url`` on the
    fallback iframe path of the workshop video page. Returns ``url``
    unchanged when ``url`` is empty or ``value`` is ``None``.
    """
    if not url or value is None:
        return url
    sep = '&' if '?' in url else '?'
    return f'{url}{sep}{key}={value}'


def normalize_timestamps(timestamps):
    """Normalize a heterogeneous list of timestamp dicts into a uniform shape.

    Two shapes circulate in the codebase:

    - Recording / course-unit shape: ``{time_seconds: int, label: str}``
      (the canonical admin/JSON storage format).
    - Workshop YAML shape: ``{time: "MM:SS", title: str}`` (authored in
      ``workshop.yaml`` and stored verbatim on the linked Event).

    Each input dict is converted to ``{time_seconds, label, formatted_time}``.
    Entries with an unparseable ``time`` string are skipped (returning a
    zero-row would mislead the click-to-seek handler).
    """
    if not timestamps:
        return []

    normalized = []
    for ts in timestamps:
        if not isinstance(ts, dict):
            continue

        # Resolve the integer time. Prefer time_seconds when present
        # because it's already in the right shape; fall back to parsing
        # the workshop-style "time" string.
        if 'time_seconds' in ts:
            try:
                time_seconds = int(ts.get('time_seconds') or 0)
            except (TypeError, ValueError):
                continue
        elif 'time' in ts:
            try:
                time_seconds = parse_video_timestamp(ts.get('time'))
            except ValueError:
                continue
        else:
            continue

        if time_seconds < 0:
            continue

        # Label can come from either key. Prefer "label" (canonical) and
        # fall back to "title" (workshop YAML).
        label = ts.get('label') or ts.get('title') or ''

        normalized.append({
            'time_seconds': time_seconds,
            'label': label,
            'formatted_time': format_timestamp(time_seconds),
        })
    return normalized


def prepare_video_context(video_url, timestamps=None, start_seconds=None):
    """
    Prepare template context for rendering a video player.

    Args:
        video_url: The URL of the video
        timestamps: Optional list of dicts with 'time_seconds'/'label'
            (canonical) or 'time'/'title' (workshop YAML) keys.
        start_seconds: Optional integer seconds to seek to on initial
            load. Propagated to the YouTube ``playerVars.start`` and the
            Loom ``?t=`` URL parameter.

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
        'start_seconds': start_seconds,
    }

    if source_type == 'youtube' and video_id:
        context['embed_url'] = get_youtube_embed_url(video_id)
    elif source_type == 'loom' and video_id:
        context['embed_url'] = get_loom_embed_url(
            video_id,
            time_seconds=start_seconds,
        )
    elif source_type == 'self_hosted':
        context['embed_url'] = video_url

    formatted_timestamps = normalize_timestamps(timestamps)
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
