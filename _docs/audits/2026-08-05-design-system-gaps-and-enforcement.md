# Design System Completeness and Enforceability Analysis

Scope: `_docs/design-system.md` against the canonical owners it names and the guard tests that enforce it. Goal: identify what a builder must hand-roll today (gaps), where the doc contradicts itself or the code (drift already inside the canon), and which automated guardrails would stop future drift.

Evidence base: full read of `_docs/design-system.md`, `content/tests/test_container_widths.py`, `content/tests/test_member_badges.py`, `content/tests/test_design_system_lint.py`, `templates/content/_info_card_classes.html`, `_content_card.html`, `_gated_access_card.html`, `_starting_soon_card.html`, `content/templatetags/member_badges.py`, `accounts/templatetags/accounts_extras.py`, plus repo-wide greps quantifying drift.

## 1. Gaps — roles a builder must hand-roll

The Partials and Component Index (`_docs/design-system.md:288-311`) is the enforcement backbone: "Hand-rolling markup or classes for a role owned by the index is a review-blocking defect" (`design-system.md:286`). That rule only bites for roles the index owns. The following roles have no owner, so every new page re-invents them — and measurably has.

### 1.1 Text inputs and textareas (highest-impact gap)

The Form Controls section (`design-system.md:486-516`) specifies only `<select>` chrome. There is no owner, class string, or doc row for `<input type="text|email|password|search">` or `<textarea>`. Measured result: at least 8 distinct hand-rolled input class strings coexist on non-Studio surfaces, e.g.

| Count | Variant |
|---|---|
| 12 | `w-full rounded-md border border-border bg-background px-4 py-2.5 text-base ... focus:ring-1 focus:ring-accent` |
| 5 | `w-full bg-secondary border border-border rounded-lg px-4 py-2 text-sm ... focus:ring-1 focus:ring-accent` |
| 2 | `w-full rounded-md border border-input bg-background px-3 py-2 text-sm ... focus:ring-2 focus:ring-ring` |
| 2 | `w-full rounded-lg border border-border bg-background px-4 py-2 ... focus-visible:ring-2 focus-visible:ring-accent` |
| 2 | `w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground` (no focus state at all) |

Radius (`rounded-md` vs `rounded-lg`), border token (`border-border` vs `border-input` — the doc defines `--input` at `design-system.md:37` but almost nothing uses it), focus recipe (`focus:ring-1` vs `focus:ring-2`, `ring-accent` vs `ring-ring`, `focus:` vs `focus-visible:`), and text size all drift. Also unspecified: label style (de facto `block text-sm font-medium text-foreground mb-1`, `templates/accounts/includes/_register_form.html:3`), help text, inline field-error text, required markers, and disabled states. Recommendation: add an `input_classes`/`field` owner (tag or class-string partial mirroring `_info_card_classes.html`) and a Form Fields doc section covering label/help/error anatomy.

### 1.2 Alerts, flash messages, and banners

`templates/_partials/messages.html` renders every Django flash message site-wide (`templates/base.html:532-534`) with dark-only colors: `bg-red-500/20 text-red-400`, `text-green-400`, `text-yellow-300` (`messages.html:17`). On the public light theme this violates the doc's own contrast rule (`design-system.md:44-52`) and its own destructive-button guidance that "`text-red-400` alone is not readable enough in the light theme" (`design-system.md:371-373`). The partial is not in the component index at all; neither are `templates/includes/announcement_banner.html`, `_unverified_email_banner.html`, or the inline `role="alert"` error divs in auth forms. A builder adding an inline alert has no recipe. Recommendation: index `_partials/messages.html` as the flash owner, fix its palette to the `text-<color>-800 dark:text-<color>-400` recipe, and add an Alerts section (info/success/warning/error, with icon, role, and aria-live rules).

### 1.3 Tabs and segmented controls

The doc owns the filter-pill/view-toggle class base (`design-system.md:551-557`) but says nothing about tab semantics. `templates/events/events_list.html:34-52` hand-rolls `role="tab"` + `aria-selected` on pill anchors (without `role="tablist"` on a wrapper contract, keyboard behavior, or a partial). Studio has its own ad-hoc tabs (`templates/studio/plans/_editor_body.html`, `studio/settings/dashboard.html`). No owner exists for "row of mutually exclusive views" as a component; only the class string is canonical.

### 1.4 Pagination

`templates/events/events_list.html` and `templates/notifications/notification_list.html` each hand-roll pager rows. No doc section, no partial, no rules for prev/next affordance, current-page treatment, or tap targets (pager controls plausibly qualify for `min-h-[44px]` under `design-system.md:562-574`, but the table does not mention them).

### 1.5 Loading, progress, and async states

The doc specifies empty states thoroughly (`design-system.md:465-483`) but has zero coverage of loading states: no skeleton, spinner, or `aria-busy` convention (grep for `animate-pulse|animate-spin` finds nothing on member surfaces — pages simply pop in, and the first builder who needs a spinner will invent one). Related: there is no client-side JS convention section at all, yet canonical partials ship inline scripts (`_starting_soon_card.html:49-71` countdown; `onboarding_chat.html`). Progress bars have exactly one narrowly-scoped owner (`_mobile_progress_bar.html`, `design-system.md:311`); a generic progress-bar recipe does not exist.

### 1.6 Multi-column and sidebar layout

`max-w-7xl` is documented as the tier for "sidebar-plus-content layouts" (`design-system.md:132`) — and that is the entire spec. Nothing defines the sanctioned column split (`lg:grid-cols-3` + `lg:col-span-2`? `lg:grid-cols-[1fr,20rem]`?), the gap, sticky-sidebar behavior, or the mobile stacking order. `templates/bookclub/book_detail.html` and `templates/home.html` each hand-roll different splits. The variable-height guidance (`design-system.md:177-183`) says when NOT to multi-column but not how to do it when you should.

### 1.7 Public tables

Comparison/progress list guidance exists (`design-system.md:184-189`) but only names Studio cell padding (`px-4 py-3`, `design-system.md:154`). Member-facing tables (`templates/plans/cohort_board.html:110`, `templates/bookclub/leaderboard.html:31`) hand-roll header style, row hover, dividers, and — notably — wrap in `rounded-2xl` containers, which the radius contract does not sanction.

### 1.8 Other unowned roles (shorter list)

| Role | Evidence |
|---|---|
| Public breadcrumbs / back links | Studio back-link spec exists (`design-system.md:419`); reader surfaces (`course_unit_detail.html`, `workshop_page_detail.html`, `module_overview.html`) hand-roll their own |
| Modals / dialogs / confirm patterns | No owner; only Studio `confirm()` mention (`design-system.md:447`) |
| Public dropdown/disclosure menus | Only Studio overflow menu is owned (`design-system.md:440-449`); `includes/_accordion.html` covers section accordions only |
| Avatars | `rounded-full` images hand-rolled per surface, no size scale |
| Error pages (404/500) and non-form error surfaces | Absent |
| Toasts (transient, dismissible) vs static flash | Only the static flash region exists; no dismiss affordance contract |
| Chat-bubble surface | `onboarding_chat.html:35-41` invented `rounded-2xl bg-muted/bg-accent px-4 py-3` bubbles with no doc backing |
| Bookclub surface family | `templates/bookclub/*` drifted wholesale to `rounded-2xl` cards (`reader_profile.html:34-52`, `index.html:30`, `book_detail.html:75-200`); `_proto_banner.html` suggests prototype status, but nothing in the doc quarantines prototype surfaces from the contract |

The `rounded-2xl` tier itself is a definitional gap: the radius contract says `rounded-2xl` is "full-page focus panels (out of scope here)" (`design-system.md:203`) — i.e. the doc explicitly declines to define the one radius tier that 21 template files now use (`host_management.html:20-38`, `password_reset.html:13`, `subscribe_form.html:10`, `cohort_board.html:42-225`, `email_app/*_result.html`, ...). "Out of scope" reads as "unregulated" to a prototyper. Either define full-page focus panels (with an owner list) or rescind the tier.

## 2. Inconsistencies and ambiguities

### 2.1 Canonical references that violate the rules they anchor

These are the most corrosive: the doc points builders at partials as the pattern to copy, and the partials themselves are off-contract.

| # | Contract | Reality |
|---|---|---|
| 1 | CTA boxes: "the action is always a `{% button_classes %}` button — never a hand-rolled link styled as a button", reference `_starting_soon_card.html` (`design-system.md:233,237`) | `_starting_soon_card.html:40-45` hand-rolls its CTA: `rounded-lg bg-accent px-5 py-3 ...` — wrong radius (`rounded-lg` vs the tag's `rounded-md`) and a padding pair (`px-5 py-3`) that exists in no size in the scale (`accounts_extras.py:41-45`) |
| 2 | Eyebrows use `tracking-widest`, never `tracking-wider` (`design-system.md:56`) | `_starting_soon_card.html:23` uses `tracking-wider` — and it is frozen as accepted debt in the lint BASELINE (`test_design_system_lint.py:197`). The doc's named CTA-box reference is itself baselined debt |
| 3 | Eliminated affordances include "`arrow-up-right` on the gated CTA" (`design-system.md:229`) | `_gated_access_card.html:96` still renders `<i data-lucide="arrow-up-right">` on the gated CTA |
| 4 | Gated partial "must use `{% button_classes %}` for CTA chrome when an implementation issue next touches it ... The scoped partial/template migration that adopted this contract has shipped" (`design-system.md:582`) | `_gated_access_card.html:92-97` hand-rolls `rounded-lg bg-accent px-6 py-3 ...`. The two sentences contradict each other: "when next touched" (future) vs "has shipped" (done). A reader cannot tell whether the hand-rolled button is a defect or sanctioned |
| 5 | CTA-box padding is `p-6` or `p-6 sm:p-8` (`design-system.md:233`); callout role padding is `p-6`, spotlight `p-5 sm:p-8` (`design-system.md:214`) | `_starting_soon_card.html:19` uses `p-5 sm:p-6` — a pair documented nowhere |
| 6 | Compact status badges must use `bg-<color>-500/15 text-<color>-800 dark:text-<color>-400` (`design-system.md:44-46`); `member_badges.TONE_CLASSES` complies (`member_badges.py:30-35`) | The gated card's "Free with sign-in" chip hand-rolls `bg-green-500/15 ... text-green-500` (`_gated_access_card.html:54`) — single-theme text color, and a hand-rolled pill for a meaning `member_access_badge` owns (`design-system.md:297`) |
| 7 | `rounded-2xl` = full-page focus panels only (`design-system.md:203`) | `_gated_access_card.html:41` uses `rounded-2xl` for its icon badge inside a `rounded-lg` card. Decorative icon-container radius is simply unspecified |

### 2.2 Doc-internal and doc-vs-code contradictions

| # | Contradiction |
|---|---|
| 8 | Destructive button color: prose says `text-red-700 dark:text-red-400` and warns `text-red-400` alone fails light theme (`design-system.md:371-373`); the "rendered variant cluster" block 35 lines later says `border border-red-500/30 bg-transparent text-red-400 hover:bg-red-500/10` (`design-system.md:408`). Code agrees with the prose (`accounts_extras.py:55-58`); the copy-paste block at 405-409 is stale. Anyone following the doc's own "if a rendering context cannot invoke the tag ... copy it whole" instruction (`design-system.md:397`) copies the wrong string. `DesignSystemButtonExamplesContractTest` (`test_design_system_lint.py:499-531`) pins the prose but not this block |
| 9 | Canonical badge shape is `px-2.5 py-0.5 text-xs` (`design-system.md:522`), i.e. the `xs` size — but the mandated card access badge uses `sm` (`design-system.md:297`), which is `px-3 py-1` (`member_badges.py:13`, asserted in `test_member_badges.py:77`). Two "canonical" geometries with no rule for choosing |
| 10 | Tone table (`design-system.md:524-532`) lists 6 tones and says Yellow = Draft. Code ships more semantics: `warning` also covers `pending`, `submitted`, `starting_soon`, `ending_soon`, and a `purple` tone for `certified` (`member_badges.py:35,38-56`, asserted at `test_member_badges.py:132`). Purple appears nowhere in the doc; a builder choosing a tone for a new "expiring soon" state cannot find yellow-for-imminent in the doc |
| 11 | Focus ring token: canonical ring is `focus-visible:ring-accent` (`design-system.md:590`), but the canonical filter-pill base uses `focus-visible:ring-ring` (`design-system.md:554`), and `--ring` is documented only as "when not using accent" (`design-system.md:38`) with no rule for when that is. Inputs split the same way (1.1 above) |
| 12 | Marketing rhythm: `py-12 sm:py-20 lg:py-28` is "the only marketing-section rhythm" yet two other blessed rhythms follow immediately (`design-system.md:143-145`), with no rule for which pages are "marketing" vs "reader/detail" vs "hero/detail". The tier is enforceable only if the page class is decidable |
| 13 | Card grid gap: doc forbids `gap-5` and `gap-8` on repeated card grids (`design-system.md:146`); the lint enforces only `gap-5` (`test_design_system_lint.py:151-155`). 5 non-Studio `grid ... gap-8` class attributes exist today |
| 14 | Shadow rule: "repeated cards never carry a shadow ... `shadow-xl` is reserved for the highlighted tier card" (`design-system.md:219`), but 9 non-Studio `shadow-sm` usages exist and nothing guards the rule |
| 15 | `test_container_widths.py:48-53` exempts `templates/events/host_management_denied.html` under a comment describing "these two nav bars" — it is a page, not a nav bar; the exemption rationale is stale |

### 2.3 Ambiguities that invite drift

- "Purpose-specific page-layout gaps that are not card grids may use the spacing their layout requires" (`design-system.md:146`) — an escape hatch wide enough to justify any gap anywhere; a reviewer cannot falsify a claim of "purpose-specific".
- The `--ring` vs `accent` split (see #11) means two builders produce two focus styles, both citing the doc.
- Prototype surfaces (bookclub) have no documented exemption or quarantine, so their `rounded-2xl` dialect reads as precedent — exactly what `design-system.md:42` ("Existing usage is not precedent by itself") tries to prevent, but that sentence is scoped to colors.

## 3. Enforceability

### 3.1 What is covered today

| Guard | Contract covered | Mechanism |
|---|---|---|
| `content/tests/test_container_widths.py` | 4 width tiers + standard gutter | Structural discovery (every template extending `base.html`), audited-page pinning, stale-registry self-check, matcher self-tests. The gold standard here |
| `content/tests/test_design_system_lint.py` | 6 signatures: deprecated gated include, `px-5 py-2.5`, public `font-bold`, public `tracking-wider`, `grid gap-5`, hand-rolled `p-12 text-center` empty state | Shrink-only per-file baseline ratchet with comment/script masking, self-checks, boundary examples, stale-allowance detection |
| `content/tests/test_member_badges.py` | Badge tones/labels/metadata + 17 named templates load `member_badges` + Studio/member badge separation | Renderer assertions + per-template source assertions |
| `studio/tests/test_form_components.GlobalSelectStyleTest` | Every `<select>` carries `app-select`/`studio-select` | Full-template walk (`design-system.md:516`) |
| `accounts/tests/test_template_date_vocabulary.py` | No raw `\|date:` filters | Full-template walk (`design-system.md:91`) |
| Others | Stacked Studio headers (`test_stacked_headers_1278.py`), status contrast (`test_status_contrast_1279.py`), cover fallbacks, dashboard component conformance, doc-code sync for workshop media and button prose (`test_design_system_lint.py:499-607`) | Various |

Assessment: the infrastructure is excellent — discovery-based structural tests plus a shrink-only ratchet with a hard "never add ignores" policy. The problem is coverage: the ratchet enforces 6 signatures while the doc states at least a dozen more objectively regex-able prohibitions. Everything in section 2 drifted in areas with no rule. The biggest unguarded contracts, ranked by measured drift:

1. Hand-rolled buttons — 88 literal non-Studio `class` attributes contain `bg-accent` + `text-accent-foreground` + a `px-*` token. `{% button_classes %}` call sites contain no literal color tokens, so every one of these is by definition not the tag.
2. `rounded-2xl` — 21 files, unregulated tier.
3. `rounded-full` pills — 219 non-Studio, non-`member_badge` lines (most are legit chips/avatars/filter pills, but badge-shaped ones among them are exactly rule-`design-system.md:520` violations, e.g. `_gated_access_card.html:49,54`).
4. `grid gap-8` (5 hits), `shadow-sm` (9 hits), forbidden marketing rhythm (0 hits today — cheap to pin at zero now).
5. Doc-vs-code sync for the rendered button strings (inconsistency #8).

### 3.2 Proposed guardrails

The highest-leverage move is not a new test file: extend `RULES` in `content/tests/test_design_system_lint.py`, because the baseline/ratchet/self-check/masking machinery is already built and its exception policy (`test_design_system_lint.py:6-16`) is already socialized. Concrete additions:

```python
# --- additions to content/tests/test_design_system_lint.py ---

def _class_attributes_with_any(required: tuple[str, ...], any_of: tuple[str, ...]):
    """All of ``required`` plus at least one of ``any_of`` as whole tokens."""
    required_res = tuple(re.compile(rf"(?<!\S){re.escape(t)}(?!\S)") for t in required)
    any_res = tuple(re.compile(rf"(?<!\S){re.escape(t)}(?!\S)") for t in any_of)

    def matcher(source):
        for match in _quoted_html_class_attributes(source):
            value = match.group("value")
            if all(p.search(value) for p in required_res) and any(
                p.search(value) for p in any_res
            ):
                yield match

    return matcher


# Sanctioned pill class strings that legitimately live outside member_badges
# (_docs/design-system.md -> Pills, Badges, and Chips: tag chips + filter pills).
_SANCTIONED_PILL_RES = (
    re.compile(r"(?<!\S)min-h-\[44px\](?!\S)"),  # page-level filter pill / view toggle
    re.compile(r"(?<!\S)bg-secondary(?!\S)"),    # static/clickable tag chip recipe
)


def _handrolled_pill_matcher(source):
    """rounded-full + badge geometry, outside the sanctioned chip recipes."""
    geometry = (
        re.compile(r"(?<!\S)rounded-full(?!\S)"),
        re.compile(r"(?<!\S)text-xs(?!\S)"),
        re.compile(r"(?<!\S)font-medium(?!\S)"),
    )
    for match in _quoted_html_class_attributes(source):
        value = match.group("value")
        if not all(p.search(value) for p in geometry):
            continue
        if any(p.search(value) for p in _SANCTIONED_PILL_RES):
            continue  # documented tag chip or filter pill, not a badge
        yield match


RULES = (
    # ... existing six rules ...
    Rule(
        # 88 current occurrences -> seed baseline, then shrink-only.
        "handrolled_primary_button",
        "Buttons",  # design-system.md: every non-Studio CTA uses {% button_classes %}
        _class_attributes_with_any(
            required=("bg-accent", "text-accent-foreground"),
            any_of=("px-3", "px-4", "px-5", "px-6"),
        ),
        # Studio has its own documented primary chrome; emails cannot run tags.
        excluded_prefixes=("templates/studio/", "templates/emails/"),
    ),
    Rule(
        # 21 files today. rounded-2xl is 'full-page focus panels' only; every
        # current use gets a baseline entry, and any NEW one must either use a
        # sanctioned card radius or come with a design-system change first.
        "rounded_2xl_outside_focus_panels",
        "Cards (radius contract)",
        _class_attributes_with("rounded-2xl"),
        excluded_prefixes=("templates/studio/", "templates/emails/"),
    ),
    Rule(
        "handrolled_pill_outside_member_badges",
        "Pills, Badges, and Chips",
        _handrolled_pill_matcher,
        excluded_prefixes=(
            "templates/studio/",
            "templates/emails/",
            "templates/includes/member_badge.html",  # the owner itself
        ),
    ),
    Rule(
        "grid_gap8",
        "Spacing and Layout",  # gap-6 grids / gap-4 rows only
        _class_attributes_with("grid", "gap-8"),
    ),
    Rule(
        "forbidden_marketing_rhythm",
        "Spacing and Layout",  # py-16 sm:py-24 lg:py-32 is forbidden
        _class_attributes_with("py-16", "sm:py-24", "lg:py-32"),
        # zero occurrences today: pin at zero, no baseline entries ever
    ),
    Rule(
        "repeated_card_shadow",
        "Cards (shadow rule)",
        _class_attributes_with("rounded-lg", "shadow-sm"),
        excluded_prefixes=("templates/studio/", "templates/emails/"),
    ),
)
```

Each new rule needs the standard companions the file already requires: one positive sample in `test_rule_self_checks` and negative boundary examples in `test_rule_boundary_examples`, e.g.

```python
# self-check samples
"handrolled_primary_button": '<a class="rounded-md bg-accent px-4 py-2 text-accent-foreground">Go</a>',
"rounded_2xl_outside_focus_panels": '<div class="rounded-2xl border border-border p-6">x</div>',
"handrolled_pill_outside_member_badges": (
    '<span class="inline-flex rounded-full bg-green-500/15 px-2.5 py-1 text-xs font-medium text-green-500">Free</span>'
),

# boundary examples (must NOT match)
("handrolled_primary_button", "templates/public.html",
 '<a class="{% button_classes \'primary\' %}">Go</a>', 0),          # tag call, no literal tokens
("handrolled_primary_button", "templates/public.html",
 '<span class="bg-accent/10 text-accent px-2.5">badge</span>', 0),  # accent badge, not a button
("handrolled_pill_outside_member_badges", "templates/public.html",
 '<a class="inline-flex min-h-[44px] items-center rounded-full px-4 py-2 text-sm font-medium">All</a>', 0),  # filter pill
("handrolled_pill_outside_member_badges", "templates/public.html",
 '<span class="inline-flex rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground">tag</span>', 0),  # tag chip
("rounded_2xl_outside_focus_panels", "templates/studio/page.html",
 '<div class="rounded-2xl">x</div>', 0),
```

False-positive risks and scoping:

| Rule | Risk | Mitigation |
|---|---|---|
| `handrolled_primary_button` | `bg-accent` + `text-accent-foreground` on a non-button (selected filter-pill state uses exactly these, `design-system.md:557`) | The `any_of` padding requirement excludes most; the selected pill (`px-4 py-2`) does collide — either add `rounded-full` as a disqualifier or accept baseline entries for the handful of filter-pill templates. Do not try to catch hand-rolled secondary buttons in v1 (`border border-border bg-transparent` is too common on non-buttons) |
| `rounded_2xl_outside_focus_panels` | Genuine full-page focus panels (`password_reset.html`, `*_result.html` interstitials) are arguably sanctioned | That is the point: the baseline freezes them; defining the panel tier in the doc then lets you convert baseline entries into an explicit sanctioned-owner list. Chat bubbles (`onboarding_chat.html`) also live in the baseline until the doc grants them a role |
| `handrolled_pill_outside_member_badges` | Avatars/dots (`rounded-full` without `text-xs font-medium`) — excluded by geometry. Legit one-off pills passed via `extra_class` — none observed. Chips built with `bg-secondary` but wrong padding would slip through | Accept: the rule targets badge-meaning pills (the `design-system.md:520` "never inline a pill whose meaning an owning tag covers" contract), not all circles. Tighten later by whitelisting the two exact chip strings instead of the `bg-secondary` token |
| `repeated_card_shadow` | A sanctioned singular surface with a soft shadow | Only `shadow-xl` on the tier card is sanctioned (`design-system.md:219`); `rounded-lg` + `shadow-sm` has no legitimate reading. Low risk |

Two further guards outside the ratchet:

1. Doc-code sync for rendered button strings (catches inconsistency #8 permanently): in `DesignSystemButtonExamplesContractTest`, assert the doc's variant-cluster code blocks byte-match the source of truth:

```python
def test_doc_variant_clusters_match_button_tag_source(self):
    from accounts.templatetags.accounts_extras import (
        PRODUCT_BUTTON_VARIANT_CLASSES,
    )
    design_system = (Path(settings.BASE_DIR) / "_docs/design-system.md").read_text(encoding="utf-8")
    for variant, classes in PRODUCT_BUTTON_VARIANT_CLASSES.items():
        with self.subTest(variant=variant):
            self.assertIn(
                classes,
                design_system,
                f"design-system.md's rendered {variant} cluster drifted from "
                f"accounts_extras.PRODUCT_BUTTON_VARIANT_CLASSES",
            )
```

This fails today on `destructive` (doc line 408 says `text-red-400`, code says `text-red-700 dark:text-red-400`) — fix the doc line as part of landing it.

2. Canonical-partial fidelity pins (catches section 2.1 regressions): once `_starting_soon_card.html` and `_gated_access_card.html` are migrated, pin them the way `AUDITED_PAGE_WIDTHS` pins pages:

```python
CANONICAL_PARTIAL_REQUIREMENTS = {
    "templates/content/_starting_soon_card.html": ["{% button_classes "],
    "templates/content/_gated_access_card.html": ["{% button_classes "],
}
CANONICAL_PARTIAL_PROHIBITIONS = {
    "templates/content/_gated_access_card.html": ['data-lucide="arrow-up-right"'],
    "templates/content/_starting_soon_card.html": ["tracking-wider"],
}
```

### 3.3 Doc changes that make enforcement possible

- Define the `rounded-2xl` focus-panel tier (owner list) or delete the tier.
- Resolve the `design-system.md:582` "when next touched" vs "has shipped" contradiction; if the gated CTA is still legacy, say so and remove "has shipped".
- Add `warning`/`purple` rows and the imminent-state semantics to the tone table so it matches `member_badges.STATUS_TONES`.
- State the `ring-accent` vs `ring-ring` decision rule (proposal: `ring-accent` everywhere except inside components whose established string already uses `ring-ring`; new components always `ring-accent`).
- Add sections for the roles in 1.1-1.7 (inputs first).
- Add a prototype-surface quarantine clause (bookclub) so drifted prototypes are explicitly non-precedent and excluded or baselined in guards.

## 4. Design-system compliance checklist for a diff

Apply to every PR/worktree diff touching `templates/` (non-Studio unless noted). Each item is checkable by grep over the diff or by running the named test.

1. Run the guards: `uv run python manage.py test content.tests.test_container_widths content.tests.test_design_system_lint content.tests.test_member_badges studio.tests.test_form_components accounts.tests.test_template_date_vocabulary --parallel`.
2. New page template: outer frame is one of `max-w-7xl|5xl|3xl|2xl` with `mx-auto px-4 sm:px-6 lg:px-8`, tier chosen per `design-system.md:130-135`.
3. `git diff -U0 -- 'templates/**.html' | grep -E '^\+' | grep -nE 'bg-accent[^/]'` — every hit inside a `class="..."` that also has `text-accent-foreground` must be a `{% button_classes %}` call, not literal classes.
4. `grep -nE '\+.*rounded-(xl|2xl|full)'` on the diff — `rounded-xl` only on tier/pricing or the home featured-sprint spotlight; `rounded-2xl` never in new code without a design-system change; `rounded-full` only via `member_badges` tags, the two documented chip strings, the filter-pill base, or an avatar.
5. `grep -nE '\+.*(px-5 py-2\.5|py-2\.5 px-5|font-bold|tracking-wider[^s])'` — must be empty.
6. New badge/pill: is its meaning covered by `member_access_badge` / `member_tier_badge` / `member_status_badge` / `member_label_badge`? Then it must use the tag (`design-system.md:520`). Green only for success; Past/Free never green.
7. New card: identify its role in the role table (`design-system.md:209-215`); clickable cards go through `_content_card.html`, static cards through `_info_card_classes.html`; no hover/`group` on static cards; no `shadow-*` on repeated cards; grid gap is `gap-6` (cards) or `gap-4` (rows).
8. New empty state: `{% member_empty_state %}` (member/public) or `{% studio_empty_state %}` (Studio); never a hand-rolled `p-12 text-center` box.
9. New `<select>`: carries `app-select` or `studio-select`. New text input/textarea: copy the dominant existing string exactly (`w-full rounded-md border border-border bg-background px-4 py-2.5 text-base ... focus:ring-1 focus:ring-accent`) until an owner exists — do not invent a ninth variant.
10. New date/time output: uses a `date_formatting` helper or the event-time services; no raw `|date:"..."`.
11. Every new interactive element: canonical `focus-visible` ring (`design-system.md:590`); 44px minimums per the tap-target table; icon-only controls have `aria-label`.
12. Copy: sentence case for headings/buttons/tabs/titles; gate vocabulary is `X or above required` / `Premium required` only.
13. Gated content: renders `content/_gated_access_card.html` with `required_tier_name`; never a second gated dialect.
14. Screenshots: desktop + mobile, light + dark, for any UI-visible change (`design-system.md:634`).

## Summary of priorities

1. Extend the `test_design_system_lint.py` ratchet with `handrolled_primary_button`, `rounded_2xl_outside_focus_panels`, and `handrolled_pill_outside_member_badges` (plus the cheap `grid_gap8`, `forbidden_marketing_rhythm`, `repeated_card_shadow`) — the infrastructure already exists; only rules and baselines are missing.
2. Fix the canon: migrate `_starting_soon_card.html` and `_gated_access_card.html` CTAs to `{% button_classes %}`, drop `arrow-up-right`, fix the free-badge contrast, then pin the partials.
3. Fill the two loudest spec gaps: text-input/field chrome (8 variants in the wild) and the flash/alert palette (light-theme contrast bug in `_partials/messages.html`).
4. Fix doc line 408 (stale destructive cluster) and add the doc-code sync assertion so rendered strings can never drift silently again.
