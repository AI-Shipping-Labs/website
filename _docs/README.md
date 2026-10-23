# Documentation map

One owner per topic. When two documents overlap, the owner below wins and the
other links to it. Current code, configuration, and tests outrank any prose.

## Entry points

| Document | Owns |
|---|---|
| [`../README.md`](../README.md) | Contributor quick start: prerequisites, `make setup`, `make run`/`make dev`, test users |
| [`../AGENTS.md`](../AGENTS.md) | Mandatory agent rules: process routing, model and concurrency policy, production-data access, repository map, technology stack, configuration framework, testing and formatting rules (`CLAUDE.md` only routes to it) |
| [`../coding-standard.md`](../coding-standard.md) | Code structure, size caps, reuse, template, and refactoring rules |
| [`PROCESS.md`](PROCESS.md) | Issue lifecycle, roles, orchestrator duties, merge rules, mandatory gates |

## Evergreen references

| Topic | Owner |
|---|---|
| Worktree leases, cleanup, branch retirement and restore | [`process-worktree-lifecycle.md`](process-worktree-lifecycle.md) (required read before those operations) |
| Product, personas, tiers, terminology | [`product.md`](product.md) |
| Test design rules, affected-tests overview and escalation table, core and marker policies | [`testing-guidelines.md`](testing-guidelines.md) |
| Test databases, coverage gate, lexical ratchets, affected-tests rule chain, Playwright owner baseline, scheduling, concurrency, live tests | [`testing-operations.md`](testing-operations.md) (read the matching section before acting) |
| UI rules and template owners | [`design-system.md`](design-system.md), surface contracts such as [`design-contract-event-detail.md`](design-contract-event-detail.md), Studio display rules in [`studio-conventions.md`](studio-conventions.md) |
| Linting | [`lint.md`](lint.md) |
| Operator bring-up and Studio settings | [`configuration.md`](configuration.md), with per-integration runbooks in [`integrations/`](integrations/) |
| Infrastructure, deploy, recovery | [`setup.md`](setup.md), [`expand-contract-releases.md`](expand-contract-releases.md), [`boot-profiling.md`](boot-profiling.md), [`coolify-networking.md`](coolify-networking.md), [`coolify-deployment.md`](coolify-deployment.md) |
| Content authoring and sync | [`content.md`](content.md), [`course_yaml.md`](course_yaml.md), [`content-kinds.md`](content-kinds.md), [`event-widgets.md`](event-widgets.md) |
| APIs | [`api.md`](api.md), [`api-delete-policy.md`](api-delete-policy.md), [`api-export-sprint-plans.md`](api-export-sprint-plans.md), member API in [`../docs/member-api/plans.md`](../docs/member-api/plans.md), CLI in [`../asl_cli/README.md`](../asl_cli/README.md) |
| Operator field guides | [`studio-user-statuses.md`](studio-user-statuses.md), [`utm_links.md`](utm_links.md), [`course-db-import.md`](course-db-import.md), [`take-home-of-the-week.md`](take-home-of-the-week.md) |

Integration settings are declared in `integrations/settings_keys.py`; each
integration runbook documents its group's keys.

## Where each document class belongs

| Class | Location | Lifecycle |
|---|---|---|
| Entry point | Repository root, plus `PROCESS.md` | Short and navigational. Link to an owner instead of restating changing policy. |
| Evergreen reference | `_docs/` root and `_docs/integrations/` | Kept current with the code it describes. Changing behavior means changing its owner doc in the same issue. |
| Short-lived evidence (audits, plans, analyses, designer reports) | `_docs/audits/YYYY-MM-DD-<topic>.md`; multi-file research bundles under `_docs/research/`; the archived landing-page index in `_docs/landing-pages/` | Point-in-time. Not current product truth. Retention rules: [`audits/README.md`](audits/README.md). |
| Product decision record | `specs/` | Original requirements and decisions. Code and `product.md` describe current behavior; see [`../specs/README.md`](../specs/README.md). |
| Role guidance | `.claude/agents/<role>.md` | Role-specific steps only; shared lifecycle and test policy link to `PROCESS.md`, `testing-guidelines.md`, and `testing-operations.md`. |
| Skill | `.agents/skills/<name>/SKILL.md` (`.claude/skills` is a symlink); member API skill in `skills/` | Self-contained enough to execute safely: auth, production-data limits, and destructive-action guards stay at the point of use. |
| Component reference | Next to the component (`asl_cli/README.md`, `integrations/services/ai_eval/README.md`, `docs/member-api/`) | Kept current with that component. |

Excluded from this map: runtime email copy in `email_app/email_templates/`,
generated files (`static/css/tailwind.css`, `_docs/openapi.json`,
`_docs/member-openapi.json` are regenerated, not hand-edited), and
documentation owned by the content, infrastructure, and `community-base`
repositories.
