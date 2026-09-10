"""Pure response contract for the browser suite's committed video fixture."""

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
CUE_FIXTURE_PATH = ROOT / "static" / "test_media" / "cue_fixture.mp4"
BYTE_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def fixture_range_response(fixture, requested_range):
    """Build a deterministic full or single-range response for ``fixture``."""
    fixture_size = len(fixture)
    match = BYTE_RANGE_RE.fullmatch(requested_range)
    if not match or not any(match.groups()):
        return {
            "status": 200,
            "content_type": "video/mp4",
            "headers": {
                "Accept-Ranges": "bytes",
                "Content-Length": str(fixture_size),
            },
            "body": fixture,
        }

    first, last = match.groups()
    if first:
        start = int(first)
        end = int(last) if last else fixture_size - 1
    else:
        suffix_length = int(last)
        start = max(0, fixture_size - suffix_length)
        end = fixture_size - 1
    end = min(end, fixture_size - 1)
    if start >= fixture_size or start > end:
        return {
            "status": 416,
            "headers": {"Content-Range": f"bytes */{fixture_size}"},
        }

    payload = fixture[start : end + 1]
    return {
        "status": 206,
        "content_type": "video/mp4",
        "headers": {
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(payload)),
            "Content-Range": f"bytes {start}-{end}/{fixture_size}",
        },
        "body": payload,
    }


class VideoFixtureRangeResponseTest(SimpleTestCase):
    def test_full_and_single_range_response_matrix(self):
        fixture = CUE_FIXTURE_PATH.read_bytes()
        fixture_size = len(fixture)
        cases = (
            ("no-range", "", 200, slice(None), None),
            ("invalid-unit", "not-a-range", 200, slice(None), None),
            ("empty-range", "bytes=-", 200, slice(None), None),
            ("bounded-range", "bytes=0-9", 206, slice(0, 10), "bytes 0-9/{size}"),
            ("open-range", "bytes=10-", 206, slice(10, None), "bytes 10-{last}/{size}"),
            ("suffix-range", "bytes=-10", 206, slice(-10, None), "bytes {suffix}-{last}/{size}"),
            ("past-end", "bytes={size}-", 416, None, "bytes */{size}"),
            ("reversed-range", "bytes=10-9", 416, None, "bytes */{size}"),
        )

        for name, requested_range, expected_status, body_slice, content_range in cases:
            with self.subTest(name=name):
                response = fixture_range_response(
                    fixture,
                    requested_range.format(size=fixture_size),
                )
                self.assertEqual(response["status"], expected_status)
                self.assertEqual(
                    response["headers"].get("Content-Range"),
                    content_range.format(
                        size=fixture_size,
                        last=fixture_size - 1,
                        suffix=fixture_size - 10,
                    )
                    if content_range
                    else None,
                )
                if body_slice is None:
                    self.assertNotIn("body", response)
                    continue

                expected_body = fixture[body_slice]
                self.assertEqual(response["body"], expected_body)
                self.assertEqual(response["headers"]["Accept-Ranges"], "bytes")
                self.assertEqual(
                    response["headers"]["Content-Length"],
                    str(len(expected_body)),
                )
