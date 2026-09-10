import json
import re
from pathlib import Path

import yaml
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "static/css/tailwind.css"

DYNAMIC_CLASSES = {
    # Product button helper: all sizes and variants.
    "min-h-[44px]",
    "px-3",
    "py-1.5",
    "px-4",
    "py-2",
    "px-6",
    "py-3",
    "bg-accent",
    "text-accent-foreground",
    "bg-secondary",
    "text-foreground",
    "text-red-700",
    "dark:text-red-400",
    # Member and content badges.
    "bg-green-500/15",
    "text-green-800",
    "dark:text-green-400",
    "bg-yellow-500/15",
    "text-yellow-800",
    "dark:text-yellow-400",
    "bg-red-500/15",
    "text-red-800",
    "dark:text-red-400",
    "bg-purple-500/20",
    "text-purple-400",
    "bg-orange-500/20",
    # Studio lifecycle/severity/tier producers.
    "bg-sky-500/15",
    "text-sky-300",
    "bg-emerald-500/15",
    "text-emerald-300",
    "bg-amber-500/15",
    "text-amber-300",
    # Sole operator-authored runtime token family.
    "from-accent/30",
    "from-blue-500/30",
    # First-party JavaScript-only state classes.
    "bg-amber-500",
    "bg-emerald-500",
    "translate-x-0.5",
    "translate-x-5",
}


def _escaped_selector(class_name):
    return "." + re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", class_name)


class TailwindSourceContractTest(SimpleTestCase):
    """Keep the CDN replacement reproducible from a clean checkout (#1383)."""

    def test_base_uses_generated_static_bundle_and_no_play_cdn(self):
        source = (ROOT / "templates/base.html").read_text()

        self.assertIn("{% static 'css/tailwind.css' %}", source)
        self.assertNotIn("cdn.tailwindcss.com", source)
        self.assertNotIn("tailwind.config =", source)
        # swap (not optional): optional was permanently dropping Inter to the
        # system fallback when it did not load within ~100ms, making pages look
        # smaller and cramped. swap guarantees Inter renders once loaded.
        self.assertIn("display=swap", source)
        self.assertNotIn("display=optional", source)
        # The blocking pre-paint theme script remains before the stylesheet.
        self.assertLess(source.index("localStorage.getItem('theme')"), source.index("css/tailwind.css"))

    def test_compiled_bundle_contains_every_dynamic_product_selector(self):
        css = CSS_PATH.read_text()
        missing = sorted(
            name
            for name in DYNAMIC_CLASSES
            if _escaped_selector(name) not in css
        )

        self.assertEqual(missing, [])

    def test_tailwind_version_and_locked_commands_are_exact(self):
        package = json.loads((ROOT / "package.json").read_text())
        lock = json.loads((ROOT / "package-lock.json").read_text())

        self.assertEqual(package["devDependencies"]["tailwindcss"], "3.4.17")
        self.assertEqual(lock["packages"]["node_modules/tailwindcss"]["version"], "3.4.17")
        self.assertIn("--minify", package["scripts"]["css:build"])
        self.assertIn("--watch", package["scripts"]["css:watch"])

    def test_content_scan_is_first_party_and_excludes_generated_or_vendor_code(self):
        config = (ROOT / "tailwind.config.js").read_text()

        for required in ("./templates/**/*.html", "./**/*.py", "./static/js/**/*.js"):
            self.assertIn(required, config)
        for excluded in (
            "!./**/tests/**",
            "!./tests/**",
            "!./playwright_tests/**",
            "!./**/migrations/**",
            "!./node_modules/**",
            "!./staticfiles/**",
            "!./static/vendor/**",
        ):
            self.assertIn(excluded, config)
        self.assertNotIn("./static/**/*.js", config)

    def test_runtime_token_is_refactored_without_a_safelist(self):
        config = (ROOT / "tailwind.config.js").read_text()
        models = (ROOT / "bookclub/models.py").read_text()

        self.assertNotIn("safelist", config)
        self.assertIn("def cover_accent_class", models)
        self.assertIn("'from-blue-500/30': 'from-blue-500/30'", models)

    def test_no_tailwind_utility_is_built_from_a_runtime_fragment(self):
        utility = r"(?:bg|text|border|ring|from|to|via|grid|col|row|p[trblxy]?|m[trblxy]?|w|h|gap|rounded|shadow|opacity|translate|scale|rotate)-"
        offenders = []
        for path in (ROOT / "templates").rglob("*.html"):
            text = path.read_text(errors="ignore")
            class_values = [
                double or single
                for double, single in re.findall(r'class\s*=\s*"([^"]*)"|class\s*=\s*\'([^\']*)\'', text)
            ]
            if any(re.search(r"(?:^|\s)(?:[a-z-]+:)*" + utility + r"[^\s]*\{\{", value) for value in class_values):
                offenders.append(str(path.relative_to(ROOT)))
        excluded_parts = {
            "tests",
            "playwright_tests",
            "migrations",
            ".tmp",
            ".venv",
            "venv",
            "node_modules",
            "staticfiles",
        }
        for path in ROOT.rglob("*.py"):
            if excluded_parts.intersection(path.relative_to(ROOT).parts):
                continue
            text = path.read_text(errors="ignore")
            if re.search(rf'f["\'][^"\']*(?<![A-Za-z0-9_-]){utility}\{{', text):
                offenders.append(str(path.relative_to(ROOT)))
        for path in (ROOT / "static/js").rglob("*.js"):
            if "vendor" in path.relative_to(ROOT / "static").parts:
                continue
            text = path.read_text(errors="ignore")
            if re.search(r"(?<![A-Za-z0-9_-])" + utility + r"[^`]*\$\{", text):
                offenders.append(str(path.relative_to(ROOT)))

        self.assertEqual(offenders, [])

    def test_build_and_delivery_are_wired_into_supported_paths(self):
        makefile = (ROOT / "Makefile").read_text()
        procfile = (ROOT / "Procfile.dev").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()

        for target in ("run: migrate css-build", "run2: migrate css-build", "dev: migrate css-build"):
            self.assertIn(target, makefile)
        for target in ("test-playwright: css-build", "test-playwright-core: css-build"):
            self.assertIn(target, makefile)
        self.assertIn("css: npm run css:watch", procfile)
        self.assertIn("FROM node:24-slim AS css-builder", dockerfile)
        self.assertIn("COPY --from=css-builder /app/static/css/tailwind.css", dockerfile)
        self.assertEqual(dockerfile.count("FROM node:"), 1)

        for workflow, native_job in (("ci.yml", "unit-tests"), ("deploy-dev.yml", "test")):
            config = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
            steps = config["jobs"][native_job]["steps"]
            steps_by_name = {step["name"]: step for step in steps}
            self.assertEqual(steps_by_name["Set up Node"]["uses"], "actions/setup-node@v4", workflow)
            self.assertEqual(steps_by_name["Build Tailwind CSS"]["run"], "make css-build", workflow)
            step_names = list(steps_by_name)
            self.assertLess(
                step_names.index("Build Tailwind CSS"),
                step_names.index("Run unit and integration tests"),
                workflow,
            )

        scheduled = (ROOT / ".github/workflows" / "scheduled-playwright.yml").read_text()
        self.assertIn("actions/setup-node@v4", scheduled)
        self.assertIn("make css-build", scheduled)

    def test_generated_outputs_are_ignored_and_brotli_is_installed(self):
        gitignore = (ROOT / ".gitignore").read_text()
        pyproject = (ROOT / "pyproject.toml").read_text()

        self.assertIn("static/css/tailwind.css", gitignore)
        self.assertIn("node_modules/", gitignore)
        self.assertIn("whitenoise[brotli]", pyproject)
