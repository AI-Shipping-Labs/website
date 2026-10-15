# Coding Standard

The short, evergreen coding standard for contributors and coding agents.
Operational detail lives in the linked owner documents; this file does not
repeat it.

## Scope

- These rules govern new and materially changed handwritten code.
- Existing untouched code is not required to become compliant because this
  document exists. Do not start repository-wide cleanups in its name.
- Generated files, vendored code, migrations, and framework-mandated
  signatures (Django callbacks, admin hooks, management commands, test
  fixtures) may follow their owning tool or framework constraints.

## How to read this document

- `Mandatory practices`, `Templates`, and `Refactoring` are repository
  rules. Reviewers treat a violation as a defect to fix before merge.
- `Design guidance` explains how to satisfy the rules well. It informs
  judgment and is not a merge gate on its own.

## Mandatory practices

### Readability

- Prefer explicit control flow over clever expressions.
- Do not use ternary expressions (`a if cond else b`). Use an `if` block.
- Comprehensions are allowed only in the simple form: one loop and one
  transformation. No filters, nested loops, nested comprehensions, or
  combined conditions. Use a named loop or a helper for anything more.
- Avoid deeply nested conditionals. Use guard clauses and early returns,
  and extract helpers when branches accumulate.

### Size caps

- New handwritten functions are at most 30 lines.
- Source files are at most 300 lines.
- Do not grow an existing function or file that already exceeds its cap.
  When materially changing it, split it along cohesive responsibilities.
- An exception is allowed only when a tool, framework, or cohesion
  constraint requires it, and the reason is documented in a comment next to
  the oversized unit.

### Reuse first

- Before writing new logic, search the repository for similar services,
  helpers, template tags, components, and partials.
- When the existing owner has the same semantics, reuse or extend it.
- Apply DRY to repeated business rules and presentation patterns: one rule,
  one owner.
- Do not merge code that only looks alike. Behavior with materially
  different semantics keeps separate owners.

### Django structure

- Each business rule has one clear domain owner (a model method, service, or
  helper module). Views, APIs, jobs, and Studio call that owner instead of
  re-implementing the policy.
- Keep views and API handlers thin: parse input, authorize, call domain
  logic, format the response.
- When validation, writes, and side effects form one state transition, keep
  them inside an explicit `transaction.atomic()` boundary. Defer external
  side effects with `transaction.on_commit(...)` when they must not run on
  rollback.
- Use folder-based `models/`, `views/`, `admin/`, and `tests/` packages,
  never single `models.py` or `tests.py` files. The full layout lives in
  `.claude/agents/software-engineer.md` (`App Structure`).

### Configuration

- Read runtime-configurable values with `get_config(...)` and
  `is_enabled(...)` from `integrations/config.py`.
- Register every such key in `integrations/settings_registry.py` so it is
  editable from Studio settings.
- Do not introduce direct runtime configuration reads through `os.environ`
  or `settings.X`.

### Errors

- Catch specific exceptions.
- A broad catch is allowed only at an external boundary where failure must
  not propagate. Explain why in a comment, and log enough context plus the
  traceback (`logger.exception(...)`) to diagnose it.
- Do not swallow errors silently.

### Security and trust boundaries

- Validate input and authorize every request on the server. Client-side
  checks are a convenience, never the control.
- Treat external and user-provided data as untrusted, including webhook
  payloads, synced content, and API bodies.
- Escape or sanitize HTML before rendering it. Do not mark untrusted input
  safe.
- Never log secrets, tokens, or credentials.

### Queries

- Prevent avoidable N+1 queries with `select_related(...)`,
  `prefetch_related(...)`, or annotations.
- Paginate or bound every collection that can grow without limit.

### Tests

- Every code change ships with tests.
- Write one authoritative behavior test at the cheapest layer that proves
  the contract.
- Every assertion must fail when the behavior is broken.
- Rules and examples live in `_docs/testing-guidelines.md`.

### Tooling

- Use `uv` for packages and commands (`uv add`, `uv run ...`).
- Run `make lint` before handoff. See `_docs/lint.md`.
- Choose local test scope with `scripts/affected_tests.py` and run it with
  `make test-affected`, as described in `_docs/testing-guidelines.md`.
- Follow the issue workflow in `_docs/PROCESS.md`.

### Naming and comments

- Use names that state what a value or function is for.
- Comments explain constraints, reasons, and non-obvious decisions. Do not
  comment what the code already says.

## Templates

- Keep business logic and data shaping in Python. Templates render prepared
  values and use only simple presentation conditionals and loops.
- Before adding markup for an established pattern, look for the existing
  owning partial or template tag. The index lives in
  `_docs/design-system.md`.
- Use Django `{% include %}` for repeated visual patterns, so one partial
  owns the design and pages stay consistent. Extract a new partial when a
  pattern repeats and none exists.
- Pass explicit context to reusable partials (`{% include ... with ... %}`).
- Keep feature-specific differences outside the shared partial when their
  semantics differ.

## Refactoring

- Identify the current behavior and confirm an authoritative test covers it
  before changing structure.
- When coverage is missing, add the behavior test first, and prove it
  passes before and after the refactor.
- The same rule applies to template refactors. Cover the user-visible
  behavior or rendered contract at the appropriate Django or Playwright
  layer before extracting, moving, or consolidating template markup.
- Keep behavior-preserving refactors separate from intentional behavior
  changes, so failures and review scope stay clear.

## Design guidance

- When splitting to meet a cap, split by responsibility rather than at an
  arbitrary line; a helper should have a name a reviewer understands
  without reading its body.
- Prefer a small, well-named function over a comment that explains a block.
- Introduce an abstraction when the second or third real caller needs the
  same semantics, not in anticipation of one.
- Keep domain owners free of request objects so views, APIs, jobs, and
  Studio can share them.

## References

| Document | Owns |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Project rules, stack, local setup |
| [`.claude/agents/software-engineer.md`](.claude/agents/software-engineer.md) | Django app layout |
| [`_docs/PROCESS.md`](_docs/PROCESS.md) | Issue pipeline and agent roles |
| [`_docs/lint.md`](_docs/lint.md) | Ruff gates and advisory report |
| [`_docs/testing-guidelines.md`](_docs/testing-guidelines.md) | Test rules, test layers, affected tests |
| [`_docs/design-system.md`](_docs/design-system.md) | Template owners and UI rules |
