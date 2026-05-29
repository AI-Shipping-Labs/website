"""Standalone AI-eval runner (issue #809).

``python manage.py run_ai <feedback|onboarding> --input <fixture>
[--mock|--live] [--out <dir>] [--model <name>] [--suite <dir>]``

Boots Django settings so :func:`integrations.config.get_config` can
resolve LLM config from DB/env, then invokes one of the two
Django-independent AI callables (#805 feedback synthesis, #804 onboarding
turn) against either a stub LLM (``--mock``, the default -- no network, no
key) or the real configured provider (``--live``, gated on
``llm.is_enabled()``). Each run writes ``output.json`` + ``trace.json``;
``--suite`` runs a folder of fixtures and writes a ``summary.json`` plus a
printed results table.

This command does NOT start the server, render templates, or require an
HTTP request -- it is operator/developer tooling. The API key is never
printed or written.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integrations.config import get_config
from integrations.services import llm
from integrations.services.ai_eval import runner
from integrations.services.ai_eval.mock_llm import patch_llm
from integrations.services.ai_eval.runner import FixtureError
from integrations.services.ai_eval.trace import FileTraceSink

# Errors the callables raise that this command reports cleanly (non-zero)
# rather than letting Django stack-trace them.
from integrations.services.feedback_synthesis import (
    FeedbackSynthesisEmpty,
    FeedbackSynthesisUnavailable,
)
from integrations.services.llm import LLMError


def _utc_timestamp():
    """Return a filesystem-safe UTC timestamp for the run dir / trace."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S-%fZ')


class Command(BaseCommand):
    help = (
        'Run an AI callable (feedback|onboarding) standalone against a stub '
        '(--mock, default) or the real provider (--live), writing output.json '
        '+ trace.json for review.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'callable',
            choices=runner.CALLABLES,
            help='Which AI callable to run: feedback or onboarding.',
        )
        parser.add_argument(
            '--input',
            help='Path to a single JSON/YAML fixture (single-run mode).',
        )
        parser.add_argument(
            '--suite',
            help='Path to a directory of fixtures to run as a batch.',
        )
        parser.add_argument(
            '--mock',
            action='store_true',
            help='Use a scripted stub LLM (default). No network, no key.',
        )
        parser.add_argument(
            '--live',
            action='store_true',
            help='Use the real configured provider. Requires llm.is_enabled().',
        )
        parser.add_argument(
            '--out',
            help='Output directory (default: .tmp/ai_runs/<callable>/<ts>/).',
        )
        parser.add_argument(
            '--model',
            help='Override model name (defaults to configured LLM_MODEL).',
        )

    def handle(self, *args, **options):
        callable_name = options['callable']
        use_live = options['live']
        use_mock = options['mock']
        input_path = options['input']
        suite_path = options['suite']
        model = options['model'] or get_config('LLM_MODEL', '') or None

        if use_live and use_mock:
            raise CommandError(
                '--mock and --live are mutually exclusive; pass at most one.'
            )
        # Mock is the default: anything that is not an explicit --live is mock.
        live = use_live
        if live and not llm.is_enabled():
            raise CommandError(
                'LLM not configured: set LLM_API_KEY / LLM_PROVIDER (and '
                'optionally LLM_MODEL / LLM_BASE_URL) to use --live.'
            )

        if not input_path and not suite_path:
            raise CommandError(
                'Provide --input <fixture> for a single run or --suite <dir> '
                'for a batch run.'
            )
        if input_path and suite_path:
            raise CommandError(
                '--input and --suite are mutually exclusive.'
            )

        provider = get_config('LLM_PROVIDER', 'anthropic') or 'anthropic'
        mode = 'live' if live else 'mock'
        self.stdout.write(
            f'Running "{callable_name}" in {mode} mode '
            f'(provider={provider}, model={model or "(default)"}).'
        )

        if suite_path:
            return self._run_suite(
                callable_name, suite_path, options['out'],
                provider=provider, model=model, live=live,
            )
        return self._run_single(
            callable_name, input_path, options['out'],
            provider=provider, model=model, live=live,
        )

    # --- single run ---

    def _run_single(self, callable_name, input_path, out, *, provider, model, live):
        out_dir = self._resolve_out_dir(out, callable_name)
        ok, headline, error = self._run_one_fixture(
            callable_name, input_path, out_dir,
            provider=provider, model=model, live=live,
        )
        if not ok:
            raise CommandError(
                f'Run failed ({error["type"]}): {error["message"]}\n'
                f'Trace written to {out_dir / "trace.json"}'
            )
        self.stdout.write(self.style.SUCCESS(
            f'OK. Output: {out_dir / "output.json"} | '
            f'Trace: {out_dir / "trace.json"}'
        ))
        for key, value in headline.items():
            self.stdout.write(f'  {key}: {value}')

    # --- suite run ---

    def _run_suite(self, callable_name, suite_path, out, *, provider, model, live):
        suite_dir = Path(suite_path)
        if not suite_dir.is_dir():
            raise CommandError(f'Suite directory not found: {suite_dir}')
        fixtures = sorted(
            p for p in suite_dir.iterdir()
            if p.suffix.lower() in ('.json', '.yaml', '.yml')
        )
        if not fixtures:
            raise CommandError(
                f'No .json/.yaml/.yml fixtures found in {suite_dir}.'
            )

        suite_out = self._resolve_out_dir(out, callable_name, suite=True)
        rows = []
        for fixture in fixtures:
            fixture_out = suite_out / fixture.stem
            ok, headline, error = self._run_one_fixture(
                callable_name, fixture, fixture_out,
                provider=provider, model=model, live=live,
            )
            latency = self._read_latency(fixture_out)
            rows.append({
                'fixture': fixture.name,
                'status': 'ok' if ok else 'error',
                'latency_seconds': latency,
                'headline': headline if ok else None,
                'error': error,
                'out_dir': str(fixture_out),
            })

        summary = {
            'callable': callable_name,
            'mode': 'live' if live else 'mock',
            'provider': provider,
            'model': model,
            'timestamp_utc': _utc_timestamp(),
            'results': rows,
        }
        summary_path = suite_out / 'summary.json'
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str),
            encoding='utf-8',
        )

        self._print_table(rows)
        self.stdout.write(self.style.SUCCESS(
            f'Suite complete. Summary: {summary_path}'
        ))

    def _print_table(self, rows):
        self.stdout.write('')
        header = f'{"fixture":<28} {"status":<7} {"latency_s":<10}'
        self.stdout.write(header)
        self.stdout.write('-' * len(header))
        for row in rows:
            latency = row['latency_seconds']
            latency_str = f'{latency:.3f}' if latency is not None else '-'
            self.stdout.write(
                f'{row["fixture"]:<28} {row["status"]:<7} {latency_str:<10}'
            )

    # --- shared per-fixture execution ---

    def _run_one_fixture(self, callable_name, fixture_path, out_dir, *,
                         provider, model, live):
        """Run one fixture, writing output.json + trace.json into out_dir.

        Returns ``(ok, headline, error)``. On failure the trace (including
        the captured error) is still written. Never raises for callable
        errors -- those become a clean non-zero exit upstream.
        """
        fixture_path = Path(fixture_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        sink = FileTraceSink(
            callable_name=callable_name,
            provider=provider,
            model=model,
            timestamp_utc=_utc_timestamp(),
        )

        def _invoke():
            data = runner.load_fixture(fixture_path)
            return runner.run_callable(
                callable_name, data, trace=sink, model=model,
                source=str(fixture_path),
            )

        try:
            if live:
                result, headline = _invoke()
            else:
                with patch_llm():
                    result, headline = _invoke()
        except FixtureError as exc:
            error = {'type': 'FixtureError', 'message': str(exc)}
            sink.error = error
            sink.write(out_dir / 'trace.json')
            self.stderr.write(self.style.ERROR(f'  {fixture_path.name}: {exc}'))
            return False, None, error
        except (LLMError, FeedbackSynthesisUnavailable, FeedbackSynthesisEmpty) as exc:
            error = {'type': type(exc).__name__, 'message': str(exc)}
            sink.write(out_dir / 'trace.json')
            self.stderr.write(self.style.ERROR(
                f'  {fixture_path.name}: {type(exc).__name__}: {exc}'
            ))
            return False, None, error

        (out_dir / 'output.json').write_text(
            json.dumps(
                result.model_dump(mode='json'), indent=2,
                ensure_ascii=False, default=str,
            ),
            encoding='utf-8',
        )
        sink.write(out_dir / 'trace.json')
        return True, headline, None

    # --- helpers ---

    def _resolve_out_dir(self, out, callable_name, *, suite=False):
        if out:
            return Path(out)
        ts = _utc_timestamp()
        base = Path('.tmp') / 'ai_runs' / callable_name
        if suite:
            return base / f'suite-{ts}'
        return base / ts

    @staticmethod
    def _read_latency(fixture_out):
        trace_file = fixture_out / 'trace.json'
        if not trace_file.exists():
            return None
        try:
            data = json.loads(trace_file.read_text(encoding='utf-8'))
        except Exception:
            return None
        return data.get('latency_seconds')
