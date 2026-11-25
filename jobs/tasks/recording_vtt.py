"""Pure timestamped VTT caption parsing, independent of provider I/O."""

import re


# Cohesion exception: this existing linear caption state machine is kept
# intact when moved out of the provider task; parser behavior is unchanged.
def parse_vtt_to_text(raw):
    """Parse a WebVTT document into readable plain text.

    Strips the ``WEBVTT`` header, ``NOTE``/``STYLE``/``REGION`` blocks, cue
    numbering and timestamp lines, and inline tags (``<v Speaker>``,
    ``<c.class>``, inline ``<00:00:01.000>`` timestamps). Consecutive
    duplicate caption lines — Zoom's rolling captions repeat the current
    line until it changes — are collapsed to the last occurrence, keeping
    first-seen order otherwise. Blank lines and cue boundaries vanish, so
    the result is one caption line per line of text.
    """
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8-sig', errors='replace')
    else:
        raw = raw.removeprefix('\ufeff')

    lines = []
    in_note = False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if in_note:
            # NOTE/STYLE/REGION blocks run until the next blank line.
            if not line:
                in_note = False
            continue
        if not line:
            continue
        header = line.split(' ', 1)[0] if ' ' in line else line
        if header in ('WEBVTT', 'NOTE', 'STYLE', 'REGION'):
            # WEBVTT header may carry metadata on the same line; block
            # keywords (NOTE/STYLE/REGION) continue until a blank line.
            if header != 'WEBVTT':
                in_note = True
            continue
        if '-->' in line:
            # Cue timing line, e.g. "00:00:15.240 --> 00:00:18.660".
            continue
        if re.fullmatch(r'\d+', line):
            # Bare cue-number line.
            continue
        text = re.sub(r'<[^>]+>', '', line).strip()
        if not text:
            continue
        if lines and lines[-1] == text:
            # Rolling-caption repeat of the line we just kept.
            continue
        lines.append(text)
    return '\n'.join(lines)


