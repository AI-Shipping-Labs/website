# Advisory Linting Ramp

Issue #610 adds expanded lint checks as advisory tooling. The existing blocking gate remains unchanged:

```bash
make lint
```

Run the staged report locally with:

```bash
make lint-advisory
```

The advisory command runs Ruff rule groups `B`, `BLE`, `C4`, `SIM`, `RET`, `ARG`, `T20`, and `C901` with `--exit-zero`, then prints trend metrics for max function length and broad exception counts.

## Current Staging Policy

- Keep `F`, `I`, and `PLC0415` as the only mandatory Ruff selections until advisory counts are reviewed.
- Treat `ruff-advisory.toml` as the staging config for new checks.
- Keep generated or framework-signature noise ignored narrowly in the advisory config:
  - migration callback arguments and migration progress prints;
  - Django admin, signal, check, and management-command callback signatures;
  - test/mock/fixture callback arguments;
  - CLI script and deploy-helper stdout output.
- Do not ignore broad exceptions or complex functions in core application code by default; those are the audit findings the report is meant to expose.

## Promotion Path

1. Track `make lint-advisory` output in CI for at least a few feature cycles without failing builds.
2. Fix low-risk autofixable findings opportunistically: `C4`, `RET`, simple `SIM`, and obvious `B` issues.
3. Promote low-noise production checks first by moving selected rules from `ruff-advisory.toml` into `pyproject.toml`.
4. For `T20`, keep scripts and deploy helpers ignored, then make app and test code mandatory once remaining prints are removed or converted to logging.
5. For `BLE001`, replace broad catches with specific exceptions in core paths. Where defensive broad catches remain, document why and log with traceback/context before adding a narrow ignore.
6. For `C901`, start mandatory enforcement with a high threshold around the current hotspots, then lower it gradually as large sync, payments, Studio, and view functions are split.
7. For `ARG`, convert truly unused parameters to underscore names only when signatures are not controlled by Django, mocks, or fixtures.

## Reading the Report

`make lint-advisory` is non-blocking by design. New feature work should not stop because this target reports findings, but new or touched code should avoid increasing broad exception counts, debug prints, or function complexity when reasonable.
