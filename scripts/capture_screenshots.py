#!/usr/bin/env python3
"""
Capture screenshots of Django pages for issue documentation.

Usage:
    uv run python scripts/capture_screenshots.py --urls /account/ /blog/ --issue 70
    uv run python scripts/capture_screenshots.py --urls / /pricing --output /tmp/screenshots

This script:
1. Starts the Django dev server (if not already running)
2. Navigates to each URL with Playwright
3. Captures full-page screenshots
4. Saves them to a temporary directory
5. Uploads them to the 'screenshots' orphan branch on GitHub
6. Posts a comment on the issue with embedded images
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")


DJANGO_HOST = "127.0.0.1"
DJANGO_PORT = 8766  # Different from test port to avoid conflicts
DJANGO_BASE_URL = f"http://{DJANGO_HOST}:{DJANGO_PORT}"
VIEWPORT = {"width": 1280, "height": 720}
SCREENSHOT_BRANCH = "screenshots"


def _server_is_running(url):
    """Check if the Django dev server is already running."""
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except (urllib.error.URLError, ConnectionError, OSError):
        return False


def _start_django_server():
    """Start Django dev server in a background thread."""
    import django
    django.setup()
    from django.core.management import call_command, execute_from_command_line

    call_command("migrate", "--run-syncdb", verbosity=0)
    call_command("load_content")

    original_argv = sys.argv
    sys.argv = [
        "manage.py", "runserver",
        f"{DJANGO_HOST}:{DJANGO_PORT}",
        "--noreload", "--insecure",
    ]
    thread = threading.Thread(
        target=execute_from_command_line,
        args=(sys.argv,),
        daemon=True,
    )
    sys.argv = original_argv
    thread.start()

    for _ in range(30):
        if _server_is_running(f"{DJANGO_BASE_URL}/"):
            return thread
        time.sleep(0.5)
    raise RuntimeError("Django dev server did not start in time")


def capture_screenshots(urls, output_dir, login_as=None):
    """Capture screenshots of the given URLs.

    Args:
        urls: List of URL paths (e.g., ["/account/", "/blog/"])
        output_dir: Directory to save screenshots
        login_as: Optional dict with 'email' and 'password' to log in first

    Returns:
        List of (url, filepath) tuples for captured screenshots
    """
    from playwright.sync_api import sync_playwright

    os.makedirs(output_dir, exist_ok=True)
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT, color_scheme="dark")
        page = context.new_page()

        try:
            if login_as:
                # Log in via the API endpoint directly
                page.goto(f"{DJANGO_BASE_URL}/accounts/login/", wait_until="networkidle")
                page.fill('#login-email', login_as['email'])
                page.fill('#login-password', login_as['password'])
                page.click('#login-submit')
                page.wait_for_timeout(2000)

            for url_path in urls:
                full_url = f"{DJANGO_BASE_URL}{url_path}"
                safe_name = url_path.strip("/").replace("/", "_") or "home"
                filepath = os.path.join(output_dir, f"{safe_name}.png")

                page.goto(full_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1000)
                page.screenshot(path=filepath, full_page=True)

                results.append((url_path, filepath))
                print(f"  Captured: {url_path} -> {filepath}")

        finally:
            browser.close()

    return results


def _upload_file_to_branch(filepath, dest_path, repo, branch=SCREENSHOT_BRANCH):
    """Upload a file to the screenshots orphan branch via GitHub Contents API.

    Uses stdin (--input -) to avoid 'argument list too long' errors with
    large base64 payloads.

    Args:
        filepath: Local path to the file to upload
        dest_path: Destination path in the repo (e.g., "issue-42/home.png")
        repo: GitHub repo in "owner/repo" format
        branch: Branch to upload to

    Returns:
        Raw URL to the uploaded file
    """
    with open(filepath, "rb") as f:
        b64_content = base64.b64encode(f.read()).decode("ascii")

    payload = json.dumps({
        "message": f"screenshot: {dest_path}",
        "content": b64_content,
        "branch": branch,
    })

    # Check if the file already exists (to get its SHA for updates)
    check_cmd = [
        "gh", "api",
        f"repos/{repo}/contents/{dest_path}",
        "--jq", ".sha",
        "-H", "Accept: application/vnd.github.v3+json",
        "--method", "GET",
        "-f", f"ref={branch}",
    ]
    existing_sha = None
    try:
        result = subprocess.run(check_cmd, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            existing_sha = result.stdout.strip()
    except Exception:
        pass

    if existing_sha:
        payload_dict = json.loads(payload)
        payload_dict["sha"] = existing_sha
        payload = json.dumps(payload_dict)

    # Upload via stdin to avoid argument length limits
    upload_cmd = [
        "gh", "api", "--method", "PUT",
        f"repos/{repo}/contents/{dest_path}",
        "--input", "-",
    ]

    result = subprocess.run(
        upload_cmd,
        input=payload,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  ERROR uploading {dest_path}: {result.stderr}", file=sys.stderr)
        raise RuntimeError(f"Failed to upload {dest_path}: {result.stderr}")

    owner, reponame = repo.split("/")
    raw_url = f"https://raw.githubusercontent.com/{owner}/{reponame}/{branch}/{dest_path}"
    print(f"  Uploaded: {raw_url}")
    return raw_url


def upload_to_issue(issue_number, screenshots, repo="AI-Shipping-Labs/website"):
    """Upload screenshots to the orphan branch and post a comment on the issue.

    Each screenshot is uploaded to issue-{N}/{safe_name}.png on the 'screenshots'
    branch, then a single comment with all images embedded is posted to the issue.

    Args:
        issue_number: GitHub issue number
        screenshots: List of (url_path, filepath) tuples
        repo: GitHub repo in "owner/repo" format
    """
    if not screenshots:
        return

    image_entries = []

    for url_path, filepath in screenshots:
        safe_name = url_path.strip("/").replace("/", "_") or "home"
        dest_path = f"issue-{issue_number}/{safe_name}.png"

        try:
            raw_url = _upload_file_to_branch(filepath, dest_path, repo)
            image_entries.append((url_path, raw_url))
        except RuntimeError as e:
            print(f"  Skipping {url_path}: {e}", file=sys.stderr)

    if not image_entries:
        print("No screenshots were uploaded successfully.", file=sys.stderr)
        return

    # Build a single comment with all screenshots
    body_lines = ["## Screenshots\n"]
    for url_path, raw_url in image_entries:
        safe_name = url_path.strip("/").replace("/", "_") or "home"
        body_lines.append(f"### `{url_path}`\n")
        body_lines.append(f"![{safe_name}]({raw_url})\n")

    body = "\n".join(body_lines)

    comment_cmd = [
        "gh", "issue", "comment", str(issue_number),
        "--repo", repo, "--body", body,
    ]
    subprocess.run(comment_cmd, check=True)
    print(f"Screenshot comment posted to issue #{issue_number}")


def main():
    parser = argparse.ArgumentParser(description="Capture screenshots of Django pages")
    parser.add_argument("--urls", nargs="+", required=True,
                        help="URL paths to screenshot (e.g., /account/ /blog/)")
    parser.add_argument("--issue", type=int,
                        help="GitHub issue number to upload screenshots to")
    parser.add_argument("--output", default=None,
                        help="Output directory (default: temp dir)")
    parser.add_argument("--login-email", default=None,
                        help="Email to log in as before capturing")
    parser.add_argument("--login-password", default="testpass123",
                        help="Password for login")
    parser.add_argument("--repo", default="AI-Shipping-Labs/website",
                        help="GitHub repo for issue upload")
    args = parser.parse_args()

    output_dir = args.output or tempfile.mkdtemp(prefix="screenshots_")
    print(f"Output directory: {output_dir}")

    # Start server if not running
    if not _server_is_running(f"{DJANGO_BASE_URL}/"):
        print("Starting Django dev server...")
        _start_django_server()
    else:
        print("Django dev server already running")

    login_as = None
    if args.login_email:
        login_as = {"email": args.login_email, "password": args.login_password}

    print(f"Capturing {len(args.urls)} screenshots...")
    screenshots = capture_screenshots(args.urls, output_dir, login_as=login_as)

    if args.issue:
        upload_to_issue(args.issue, screenshots, repo=args.repo)

    print(f"\nDone. {len(screenshots)} screenshots saved to {output_dir}")
    return screenshots


if __name__ == "__main__":
    main()
