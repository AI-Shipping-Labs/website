"""Anonymous deployed sitemap.xml smoke check (Issues #656 and #1377).

Read-only, anonymous test that runs against
``PLAYWRIGHT_BASE_URL=https://dev.aishippinglabs.com``. Does NOT seed
the local Django ORM, inject session cookies, or submit forms.

The deployed scenario is deliberately read-only and has no ORM fixtures,
session cookies, ``local_only`` marker, or ``creates_data`` marker.
"""

import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import pytest

from playwright_tests.conftest import base_url_is_local, goto_with_retry
from scripts.browser_journey_policy import browser_journey


@pytest.mark.core
@browser_journey
def test_sitemap_xml_is_served(django_server, page):
    """Every deployed sitemap entry stays on its configured deployment."""
    response = goto_with_retry(page, f"{django_server}/sitemap.xml")
    assert response.status == 200, f"/sitemap.xml returned {response.status}"
    assert response.headers["content-type"].startswith("application/xml")

    try:
        root = ET.fromstring(response.body())
    except ET.ParseError as exc:
        raise AssertionError("Sitemap response is malformed XML") from exc

    locations = [(node.text or "").strip() for node in root.findall(".//{*}loc")]
    assert locations and all(locations), "Sitemap has no non-blank <loc> entries"

    if base_url_is_local():
        from django.conf import settings

        expected = urlsplit(settings.SITE_BASE_URL)
    else:
        expected = urlsplit(django_server)
    unexpected = {
        f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "<relative>"
        for location in locations
        if (parsed := urlsplit(location))[:2] != expected[:2]
    }
    assert not unexpected, f"Sitemap has {len(locations)} entries with unexpected origin(s): {sorted(unexpected)}"
