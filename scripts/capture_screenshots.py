#!/usr/bin/env python3
"""
Capture screenshots of Django pages for issue documentation.

Usage:
    uv run python scripts/capture_screenshots.py --urls /account/ /blog/ --output .tmp/screenshots
    uv run python scripts/capture_screenshots.py --urls / /pricing --output .tmp/screenshots
    uv run python scripts/capture_screenshots.py --urls /projects --output .tmp/screenshots --viewport 393x851

This script:
1. Starts an owned Django dev server on an OS-assigned loopback port
2. Navigates to each URL with Playwright
3. Captures full-page screenshots
4. Saves them to the chosen output directory

Sharing the resulting PNGs is a separate step. Use the `upload-screenshot` CLI
from the `sandbox-screenshots` service to upload each file and surface the
returned CloudFront URL to the user. See `.claude/skills/screenshots/SKILL.md`
for the canonical procedure (install precondition, return shape, and the
`SCREENSHOT_UPLOAD_TOKEN` hygiene rule).

This is a manual QA/documentation helper. Its Django server intentionally uses
the configured development database (db.sqlite3 by default) and loads synced
content. Do not use it to create ad-hoc Playwright fixture rows; run
`uv run pytest playwright_tests/...` for test validation.
"""

import argparse
import errno
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Thread

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")


DJANGO_HOST = "127.0.0.1"
MAX_BIND_ATTEMPTS = 3
DEFAULT_VIEWPORT = {"width": 1280, "height": 720}


def parse_viewport(value):
    """Parse a WIDTHxHEIGHT viewport string for Playwright."""
    try:
        width_text, height_text = value.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("viewport must be WIDTHxHEIGHT, e.g. 1280x900") from exc

    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("viewport width and height must be positive integers")

    return {"width": width, "height": height}


def viewport_label(viewport):
    """Return a compact label for filenames and comments."""
    return f"{viewport['width']}x{viewport['height']}"


def _server_is_running(url):
    """Check readiness on a URL whose listener this invocation owns."""
    try:
        with urllib.request.urlopen(url, timeout=2):
            pass
        return True
    except (urllib.error.URLError, ConnectionError, OSError):
        return False


def _select_loopback_port():
    """Ask the operating system for an available loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind((DJANGO_HOST, 0))
        return candidate.getsockname()[1]


def _bind_django_server(port):
    """Bind a local Django WSGI server before any readiness request."""
    import django
    django.setup()
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.servers.basehttp import (
        ThreadedWSGIServer,
        WSGIRequestHandler,
        get_internal_wsgi_application,
    )

    application = StaticFilesHandler(get_internal_wsgi_application())
    server = ThreadedWSGIServer(
        (DJANGO_HOST, port),
        WSGIRequestHandler,
        ipv6=False,
        allow_reuse_address=False,
    )
    server.set_app(application)
    return server


@dataclass
class _OwnedDjangoServer:
    server: object
    thread: Thread
    base_url: str

    def stop(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        if getattr(self.thread, "ident", None) is not None:
            self.thread.join(timeout=5)


def _start_django_server():
    """Start and return an owned Django server on an OS-assigned port."""
    last_collision = None
    for attempt in range(1, MAX_BIND_ATTEMPTS + 1):
        port = _select_loopback_port()
        try:
            server = _bind_django_server(port)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            last_collision = exc
            if attempt < MAX_BIND_ATTEMPTS:
                continue
            break

        base_url = f"http://{DJANGO_HOST}:{port}"
        thread = Thread(target=server.serve_forever, daemon=True)
        owned = _OwnedDjangoServer(server, thread, base_url)

        try:
            thread.start()
            for _ in range(30):
                if _server_is_running(f"{base_url}/"):
                    return owned
                if not thread.is_alive():
                    break
                time.sleep(0.5)
        except BaseException:
            owned.stop()
            raise

        owned.stop()
        raise RuntimeError(f"Django dev server did not start at {base_url}")

    raise RuntimeError(
        "Could not start the Django screenshot server after "
        f"{MAX_BIND_ATTEMPTS} loopback bind collisions; "
        "another local process claimed every candidate port"
    ) from last_collision


@contextmanager
def _owned_django_server():
    """Start the screenshot server and always release its listener."""
    owned = _start_django_server()
    try:
        yield owned
    finally:
        owned.stop()


def _migrate_database():
    """Apply local migrations before starting the screenshot server."""
    import django
    django.setup()
    from django.core.management import call_command

    call_command("migrate", "--run-syncdb", verbosity=0)


def capture_screenshots(urls, output_dir, login_as=None, viewport=None, *, base_url):
    """Capture screenshots of the given URLs.

    Args:
        urls: List of URL paths (e.g., ["/account/", "/blog/"])
        output_dir: Directory to save screenshots
        login_as: Optional dict with 'email' and 'password' to log in first
        viewport: Playwright viewport dict. Defaults to the historical 1280x720.
        base_url: Base URL of the Django server owned by this invocation.

    Returns:
        List of (url, filepath) tuples for captured screenshots
    """
    from playwright.sync_api import sync_playwright

    os.makedirs(output_dir, exist_ok=True)
    results = []
    viewport = viewport or DEFAULT_VIEWPORT
    label = viewport_label(viewport)
    suffix = "" if viewport == DEFAULT_VIEWPORT else f"_{label}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=viewport, color_scheme="dark")
        page = context.new_page()

        try:
            if login_as:
                # Log in via the API endpoint directly
                page.goto(f"{base_url}/accounts/login/", wait_until="networkidle")
                page.fill('#login-email', login_as['email'])
                page.fill('#login-password', login_as['password'])
                page.click('#login-submit')
                page.wait_for_timeout(2000)

            for url_path in urls:
                full_url = f"{base_url}{url_path}"
                safe_name = url_path.strip("/").replace("/", "_") or "home"
                filepath = os.path.join(output_dir, f"{safe_name}{suffix}.png")

                page.goto(full_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1000)
                page.screenshot(path=filepath, full_page=True)

                results.append((url_path, filepath))
                print(f"  Captured ({label}): {url_path} -> {filepath}")

        finally:
            browser.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="Capture screenshots of Django pages")
    parser.add_argument("--urls", nargs="+", required=True,
                        help="URL paths to screenshot (e.g., /account/ /blog/)")
    parser.add_argument("--output", default=None,
                        help="Output directory (default: .tmp/screenshots)")
    parser.add_argument("--viewport", type=parse_viewport, default=DEFAULT_VIEWPORT,
                        help="Viewport as WIDTHxHEIGHT (default: 1280x720)")
    parser.add_argument("--login-email", default=None,
                        help="Email to log in as before capturing")
    parser.add_argument("--login-password", default="testpass123",
                        help="Password for login")
    args = parser.parse_args()

    output_dir = args.output or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".tmp", "screenshots",
    )
    print(f"Output directory: {output_dir}")

    login_as = None
    if args.login_email:
        login_as = {"email": args.login_email, "password": args.login_password}

    _migrate_database()
    with _owned_django_server() as owned:
        print(f"Starting Django dev server at {owned.base_url}")
        print(f"Capturing {len(args.urls)} screenshots at {viewport_label(args.viewport)}...")
        screenshots = capture_screenshots(
            args.urls,
            output_dir,
            login_as=login_as,
            viewport=args.viewport,
            base_url=owned.base_url,
        )

    print(f"\nDone. {len(screenshots)} screenshots saved to {output_dir}")
    print(
        "To share with the user, upload each PNG via `upload-screenshot <file>` "
        "and surface the returned CloudFront URL. "
        "See .claude/skills/screenshots/SKILL.md."
    )
    return screenshots


if __name__ == "__main__":
    main()
