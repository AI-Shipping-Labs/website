---
name: ui-prototype
description: Build a fast, clickable UI prototype for a new feature to validate direction BEFORE implementing it. Use when the user says "prototype", "mock up the UI", "let's see the UI first", "figure out the mechanics before we build", or wants a throwaway UI on a branch. Deliberately skips the usual PROCESS.md agent pipeline and works inline; also files a raw tracking issue so nothing is forgotten.
metadata:
  short-description: Clickable UI prototype on a branch, inline, before implementation
---

# UI Prototype

Purpose: put a real, clickable UI in front of the user to agree on direction before writing the real feature. The prototype is throwaway scaffolding — it exists to figure out the mechanics (screens, flow, data shapes, copy), not to ship.

## When this applies

The user wants to see and click a UI for a not-yet-built feature. Signals: "prototype", "mock up", "let's do the UI first", "figure out the mechanics", "make sure we go in the right direction", "for now don't follow the process, do it inline".

## Two things happen in parallel

1. File a raw tracking issue (intake), then keep working. The issue is where the real feature and everything out of prototype scope is remembered.
2. Build the prototype inline on a branch. No PM/SWE/tester/oncall agents, no acceptance cycle. This is the one place the normal PROCESS.md pipeline is deliberately bypassed — say so explicitly to the user.

Do not launch the grooming/implementation pipeline from a prototype session. The issue you file is for a later, separate run.

## Process

### 1. Branch

Create a dedicated branch, e.g. `prototype/<feature>`. Commit as you go with clear messages. No PR — this is exploratory (real work later merges per the normal flow).

### 2. Design-system compliance is a HARD GATE (you self-enforce it)

This is the single most repeated correction: interface elements that don't follow the design system. In the normal pipeline a design reviewer catches this; the prototype flow skips that pipeline, so YOU are the gate. Hand-rolling markup or classes for a role the design system already owns is a review-blocking defect even when it renders identically (`_docs/design-system.md` says exactly this).

Run the design system's own procedure — `_docs/design-system.md` § "Before You Write a Class String" — for EVERY element, before writing it:

1. Identify the UI role (button, badge, card, CTA box, empty state, page frame, …).
2. Find its owner in the design system's "Partials and Component Index" / "Cards" role table.
3. Copy that owner's exact class string or `{% include %}` / tag. Only invent if there is truly no owner, and say so in a comment.

Element → owner quick map (never hand-roll these):

- Button → `{% button_classes %}`. Badge/pill → `{% member_badges %}` tags. Access/tier → `{% member_access_badge %}`.
- Card → the "Cards" role table + its partial: static → `_info_card_classes.html`; clickable → `_content_card.html`; gated → `_gated_access_card.html`; accent callout / CTA box → the CTA-box spec (§ "CTA boxes") and `_starting_soon_card.html`.
- Empty state → `{% member_empty_state %}`. Page width → the four "Spacing and Layout" tiers.

Drift tells — if you wrote any of these, you hand-rolled; stop and fix:

- `rounded-2xl` on anything that isn't a full-page focus panel (roles are `rounded-lg`; spotlight is `rounded-xl`).
- A `<span class="… rounded-full …">` pill, or a hand-written button class string, instead of the tag.
- An invented tone/status/width not in the registries.
- A CTA whose button isn't `{% button_classes %}`.

Self-check before showing the user: for each element, name the partial or role you copied. If you can't name it, it's hand-rolled — go back to step 1. Then reuse the canonical component below.

- Base and tokens: templates extend `base.html`. Color tokens are HSL CSS vars: `background`, `card`, `card-foreground`, `primary`, `muted`, `muted-foreground`, `accent`, `accent-foreground`, `border`. Use `hero-gradient`, `prose`, etc. Include `includes/header.html` and `includes/footer.html`.
- Buttons: `{% load accounts_extras %}` then `{% button_classes 'primary'|'secondary' size='sm'|'md'|'lg' extra='...' %}`. Never hardcode button classes.
- Badges/pills: `{% load member_badges %}` — this is the badge design system. Do NOT hand-roll `<span class="... rounded-full ...">` pills.
  - `{% member_access_badge required_level testid="..." %}` — the standard tier/access badge (lock icon + "Main or above" etc). Use this for access levels.
  - `{% member_status_badge "In progress" status="active" %}` — status pills. `status` maps to a semantic tone (active/open/registered -> green, upcoming -> blue/info, past/ended -> muted).
  - `{% member_label_badge "text" tone="muted"|"accent" icon="..." %}` — generic labels.
  - Tones live in `content/templatetags/member_badges.py` (`TONE_CLASSES`, `STATUS_TONES`). Check there before inventing a tone/status string.
- Cards: match the info-card role (Issue #1339): `rounded-lg border border-border p-6` plus `bg-card` (or `bg-background` on a `bg-card` band). Icon chips are filled: `inline-flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10 text-accent` with a `h-5 w-5` lucide icon. Static (non-navigating) cards get NO `group`, hover, or arrow. See `templates/content/_info_card_classes.html`.
- Layout & width consistency: the design system fixes the outer page width — do NOT invent one. `_docs/design-system.md` § "Spacing and Layout" defines four sanctioned tiers, chosen by content shape, enforced by `content/tests/test_container_widths.py`: Frame `max-w-7xl` (index/grid/listing/marketing/dashboard — matches header + footer chrome), Detail `max-w-5xl` (detail pages with mixed layout: media + metadata + cards, e.g. event/course/workshop/plan), Reader `max-w-3xl` (long-form `.prose` and single-column forms), Narrow `max-w-2xl` (confirmations, single-purpose forms). Frame vs Detail is decided by whether content fills the width, not by the word "index": use Frame only when a multi-column grid, dense listing, table, or real sidebar layout actually spans 7xl; a single-column landing/hub (hero + a few stacked sections, or one primary card) is Detail `max-w-5xl` — at 7xl a lone column reads sparse. Pick the tier for the surface's shape and apply that one outer container to EVERY section so they line up on the same edges. Narrower inner columns are fine (a `max-w-3xl` intro paragraph inside a 7xl index) as long as the heading and sections still span the frame. Sibling cards in a row are the same size with copy of roughly the same length.
- CTA boxes / callouts: do not hand-roll. `_docs/design-system.md` has a "Cards" role-contract table (info / clickable / callout-action) and a "Partials and Component Index". Reuse the canonical partial — e.g. the accent CTA box is `templates/content/_starting_soon_card.html` (`rounded-lg border-accent/40 bg-accent/5 p-5 sm:p-6`, a responsive content-left / `{% button_classes %}`-button-right row). Match its exact classes so the box reads identically to the rest of the site; an approximated `rounded-2xl` accent card is the tell that you skipped this.
- How to discover: `grep -rn` in `templates/` and `*/templatetags/` for an existing partial/tag before building anything. Check the design-system "Partials and Component Index" first. If the site already renders the thing you need (a badge, a card, a CTA box, a leaderboard row), reuse that partial or copy its class string — don't approximate.

### 3. Access levels are standard tiers

Content-like surfaces carry a standard access level, rendered via `{% member_access_badge %}`. Numeric levels (`content/access.py`): Open=0, Registered=5, Basic=10, Main=20, Premium=30. Store the numeric `required_level` in prototype data and pass it to the badge. The exact tier is a grooming decision — note it as a placeholder.

### 4. Copy: member-facing, accurate, scoped

Write UI copy for members, not for us. Expect the user to iterate on wording several times — keep each change small and re-verify live.

- No internal jargon: don't reference internal mechanics or other internal features by name (e.g. don't say "just like community sprints"). Describe the value in the member's terms.
- Let mechanics be discovered, don't over-explain: if a feature (leaderboard, competition, gamification) is meant to be found by exploring, don't name it in the marketing copy — describe the benefit, not the mechanism.
- Keep each field scoped to its own subject: an entity's `description` should describe the entity itself, not the surrounding process (a book description is about the book; cadence/kickoff/logistics live in their own fields and UI).
- No filler or vague metaphors ("friendly momentum") — say the concrete thing.
- Never state something untrue about how the thing works (don't claim a cadence or behavior the team hasn't committed to). When unsure, ask or leave it out.
- No markdown in event-style descriptions/emails.

### 5. URL convention: no trailing slashes

The site normalizes to slash-less canonical URLs via `integrations.middleware.RemoveTrailingSlashMiddleware`. Define URL patterns WITHOUT trailing slashes (`path("books", ...)`, `path("books/<slug:slug>", ...)`). A trailing-slash pattern 301s to the slash-less form and then 404s.

### 6. Nav lives in two places

`templates/includes/header.html` defines the primary nav TWICE — desktop dropdown and mobile menu. To add a link (e.g. under Resources) you must edit BOTH blocks and preserve the distinct `data-testid` prefixes (`nav-resources-link-*` and `mobile-nav-resources-link-*`). Verify both render. (A de-duplication refactor is tracked separately; until it lands, edit both.)

### 7. Seed from real data

When the feature has a real-world anchor, pull real data rather than inventing it. Check prod (`uv run asl ...`, see `ai-shipping-labs-prod-api`) and follow links to their source (e.g. take the announced event, then fetch the linked book page for the real table of contents). Keep it clearly fake where it must be (fake users/notes), real where it anchors direction (title, author, chapters).

### 8. Prototype mechanics (keep it throwaway)

- A lightweight module, NOT a full app: `foo/__init__.py` (with a header comment saying PROTOTYPE ONLY), `foo/prototype_data.py` (all context hardcoded), `foo/views.py` (plain function views rendering templates), `foo/urls.py`. No models, no migrations, no auth, no writes, no INSTALLED_APPS entry needed — plain views + templates found via APP_DIRS/global `templates/`.
- Wire it into the root URLconf (`website/urls.py`) with a comment marking it prototype-only.
- Templates in `templates/foo/`, extending `base.html`, including header/footer.
- Add a small fixed "UI prototype — data is fake" banner partial so it is never mistaken for real.

### 9. Show every viewer state, not just the happy path

The user will want to see how the surface looks to different viewers — at minimum authenticated (member) vs logged-out (guest), and often a paid/gated tier and staff/organizer role. Build these in from the start:

- Drive the state from the real `request.user.is_authenticated`, but add a `?view=member` / `?view=guest` override so both can be previewed without actually logging in (the prototype has no real auth).
- Add a small role toggle to the prototype banner so the user can flip states in one click.
- Gate member-only affordances behind the flag: progress/streak, "your note", mark-as-read, profile-visibility toggle, comment inputs, organizer/staff actions. Guests get a membership/sign-in CTA in each of those spots and see public social-proof (read counts, leaderboard) but not a personal "You" row.

### 10. Verify visually with Playwright

`manage.py check`, then run the server and screenshot every page (dark mode). Dismiss the site's "Optional analytics" consent modal before shooting (`get_by_role("button", name="Keep analytics off").click()`) or it overlaps content. Read the screenshots and confirm the design matches the site before showing the user.

### Gotcha: "it still looks old" after a template edit

Local `.env` usually sets `DEBUG=False`, which enables Django's cached template loader. Template edits then do NOT appear until the server is restarted. After editing a template, restart the dev server (kill + rerun `manage.py runserver`). If the user says a change "still looks old", this is almost always why — restart, then hard-refresh.

## The tracking issue

File it raw with the `needs grooming` label (orchestrator files intake; PM grooms later — do not groom inline). Capture the whole feature AND everything out of prototype scope so it is not forgotten, for example:

- Admin API and member API surfaces (prototypes have neither).
- Event/event-series linkage and any per-chapter/per-item events (constrain them to the linked series only if that is the intent).
- Data-source decision: is the entity git-content-synced (slugs must be content-derivable) or Studio-managed (auto-ids fine)? See `project_content_vs_studio_sources`.
- Summaries / aggregations / gamification and any "at the end" deliverables.
- Standard access-level enforcement.
- Reuse notes: which existing subsystems the real build should lean on (tiers, sprints leaderboard, events, comments, design-system tags).

Also file separate small cleanup issues for real problems you trip over (e.g. nav duplication), labeled `needs grooming`, without fixing them in the prototype.

## Deliverable to the user

Branch name, the routes to click, screenshots, the tracking issue link, and any placeholders/liberties you took (invented copy, guessed tier) called out explicitly so the user can correct direction.
