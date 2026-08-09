#!/usr/bin/env python3
"""Verify #1383's generated Tailwind and production static delivery contracts."""

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CSS_PATH = ROOT / "static/css/tailwind.css"
STATIC_ROOT = ROOT / "staticfiles"

# Files whose complete-string class producers are explicitly called out by the
# #1383 contract. Studio views are included below as a directory family.
PRODUCER_FILES = [
    ROOT / "accounts/templatetags/accounts_extras.py",
    ROOT / "content/templatetags/member_badges.py",
    ROOT / "content/models/project.py",
    ROOT / "content/models/download.py",
    ROOT / "content/views/courses.py",
    ROOT / "content/views/tags.py",
    ROOT / "studio/templatetags/studio_filters.py",
    ROOT / "plans/models.py",
    ROOT / "email_app/ses_explain.py",
]

PREFIXES = (
    "accent-",
    "align-",
    "animate-",
    "appearance-",
    "aspect-",
    "backdrop-",
    "basis-",
    "bg-",
    "blur-",
    "border-",
    "bottom-",
    "break-",
    "brightness-",
    "capitalize",
    "col-",
    "content-",
    "cursor-",
    "decoration-",
    "delay-",
    "divide-",
    "drop-shadow-",
    "duration-",
    "ease-",
    "fill-",
    "flex-",
    "font-",
    "from-",
    "gap-",
    "grayscale",
    "grid-",
    "grow-",
    "h-",
    "hover:",
    "inset-",
    "italic",
    "items-",
    "justify-",
    "leading-",
    "left-",
    "line-clamp-",
    "line-through",
    "max-",
    "mb-",
    "min-",
    "ml-",
    "mr-",
    "mt-",
    "mx-",
    "my-",
    "object-",
    "opacity-",
    "order-",
    "outline-",
    "overflow-",
    "p-",
    "pb-",
    "pl-",
    "placeholder-",
    "pr-",
    "pt-",
    "px-",
    "py-",
    "relative",
    "right-",
    "ring-",
    "rotate-",
    "rounded-",
    "row-",
    "scale-",
    "shadow-",
    "shrink-",
    "space-",
    "sr-",
    "stroke-",
    "table-",
    "text-",
    "to-",
    "top-",
    "tracking-",
    "transform",
    "transition-",
    "translate-",
    "truncate",
    "underline",
    "uppercase",
    "via-",
    "visible",
    "w-",
    "whitespace-",
    "z-",
)
BARE = {
    "absolute",
    "block",
    "border",
    "contents",
    "flex",
    "grid",
    "hidden",
    "inline",
    "inline-block",
    "inline-flex",
    "list-none",
    "overflow-hidden",
    "sticky",
    "table",
    "uppercase",
    "visible",
    "w-full",
}
NON_TAILWIND_WORDS = {
    "content-type",
    "left-anti-join",
    "max-width",
    "min-height",
    "top-level",
    "uppercased",
    "visible-input",
    "visible-text",
}


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _css_selector(class_name):
    return "." + re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", class_name)


def _is_tailwind_candidate(token):
    token = token.strip().lstrip("!-")
    if not token or token in NON_TAILWIND_WORDS or "_" in token or any(char in token for char in "{}<>=\"'`$"):
        return False
    # Strip variants only for classification; retain them for selector checks.
    base = token.split(":")[-1].lstrip("!-")
    return base in BARE or base.startswith(PREFIXES)


def _python_string_literals(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _producer_classes():
    values = []
    files = [*PRODUCER_FILES, *(ROOT / "studio/views").glob("*.py")]
    # Form/widget constants can live in any first-party app. Scanning files
    # named forms/widgets is intentionally narrower than all Python prose.
    files.extend(
        path
        for path in ROOT.rglob("*.py")
        if any(part in {"forms", "widgets"} for part in path.relative_to(ROOT).parts)
        and not {"tests", "migrations", ".venv"}.intersection(path.relative_to(ROOT).parts)
    )
    for path in sorted(set(files)):
        values.extend(_python_string_literals(path))

    for path in (ROOT / "static/js").rglob("*.js"):
        if "vendor" in path.relative_to(ROOT / "static").parts:
            continue
        # Class strings in first-party JS are simple quoted/backtick literals;
        # interpolated fragments are rejected separately by the source test.
        values.extend(match[1] for match in re.findall(r"(['\"`])((?:\\.|(?!\1).)*)\1", path.read_text()))

    classes = set()
    for value in values:
        for token in value.split():
            token = token.strip(",;()[]")
            if _is_tailwind_candidate(token):
                classes.add(token)
    return classes


def verify_bundle():
    if not CSS_PATH.is_file():
        raise AssertionError(f"missing generated bundle: {CSS_PATH.relative_to(ROOT)}")
    css = CSS_PATH.read_text()
    if "@tailwind " in css or len(css) < 20_000:
        raise AssertionError("generated CSS is absent or not a compiled Tailwind bundle")
    if css.count("\n") > 2:
        raise AssertionError("generated CSS is not minified")

    missing = sorted(name for name in _producer_classes() if _css_selector(name) not in css)
    if missing:
        raise AssertionError("compiled CSS is missing producer selectors: " + ", ".join(missing))
    return css


def verify_determinism():
    before = _sha256(CSS_PATH)
    subprocess.run(["npm", "run", "css:build"], cwd=ROOT, check=True)
    after = _sha256(CSS_PATH)
    if before != after:
        raise AssertionError(f"CSS build is not deterministic: {before} != {after}")
    return after


def verify_collected():
    manifest = json.loads((STATIC_ROOT / "staticfiles.json").read_text())
    hashed_name = manifest["paths"]["css/tailwind.css"]
    if not re.fullmatch(r"css/tailwind\.[0-9a-f]{12}\.css", hashed_name):
        raise AssertionError(f"CSS path is not content-hashed: {hashed_name}")
    hashed_path = STATIC_ROOT / hashed_name
    for suffix in ("", ".gz", ".br"):
        if not Path(f"{hashed_path}{suffix}").is_file():
            raise AssertionError(f"missing collected artifact: {hashed_name}{suffix}")

    # Exercise the real Django/WhiteNoise middleware configuration. Its
    # manifest-aware immutable test is what turns a hashed URL from the generic
    # 60-second cache policy into the ten-year immutable policy.
    os.environ.update(
        DEBUG="False",
        SECRET_KEY="issue-1383-static-verification-only",
        ALLOWED_HOSTS="testserver,localhost",
        DJANGO_SETTINGS_MODULE="website.settings",
    )
    import django
    from django.test import Client

    django.setup()
    response = Client().get(f"/static/{hashed_name}", HTTP_ACCEPT_ENCODING="br, gzip")
    headers = dict(response.headers)
    if response.status_code != 200:
        raise AssertionError(f"WhiteNoise did not serve hashed CSS: {response.status_code} {headers}")
    if response.headers.get("Content-Encoding") != "br":
        raise AssertionError(f"WhiteNoise did not negotiate Brotli: {headers}")
    cache = response.headers.get("Cache-Control", "")
    if "max-age=315360000" not in cache or "immutable" not in cache:
        raise AssertionError(f"hashed CSS lacks immutable cache policy: {cache!r}")
    return hashed_name, headers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="rebuild once and compare hashes")
    parser.add_argument("--collected", action="store_true", help="verify manifest/compression/serving")
    args = parser.parse_args()

    css = verify_bundle()
    digest = verify_determinism() if args.rebuild else _sha256(CSS_PATH)
    print(f"Tailwind bundle OK: {len(css.encode())} bytes sha256={digest}")
    if args.collected:
        hashed_name, headers = verify_collected()
        print(
            f"Collected CSS OK: {hashed_name} encoding={headers['Content-Encoding']} cache={headers['Cache-Control']}"
        )


if __name__ == "__main__":
    main()
