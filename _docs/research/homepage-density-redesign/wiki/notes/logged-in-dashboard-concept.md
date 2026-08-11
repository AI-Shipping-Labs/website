# Logged-in member dashboard concept

Artifact: [logged-in-dashboard-concept.png](../../output/logged-in-dashboard-concept.png)
Generated: 2026-08-11
Mode: built-in image generation
Inputs: clean logged-in dashboard capture as factual reference; preserved public-page concept as style/hierarchy reference

## Outcome

- [INFERENCE logged-in-dashboard-render,logged-in-dashboard-template] The concept makes the sprint plan the single dominant action and treats course and Book Club progress as the two resumable secondary tasks.
- [INFERENCE logged-in-dashboard-render] The concept reduces repeated bordered grids by using a narrow `Up next` rail, one Community strip, three editorial content rows, and one Explore icon row.
- [HUMAN] Book Club appears as first-class member progress for `Inference Engineering`, plus a separate upcoming discussion row.

## Final prompt used

```text
Use case: ui-mockup
Asset type: high-fidelity full-length desktop member dashboard redesign concept
Primary request: Redesign the LOGGED-IN AI Shipping Labs member dashboard so it feels calm, focused, and useful every day. It must answer one question immediately: "What should I do next?" Reduce density by prioritizing continuity and urgency, removing repeated card grids, and pushing broad discovery into compact links. This is an authenticated product dashboard, not a public marketing homepage.
Input images: Image 1 is the current authenticated Main-member dashboard and is the factual reference for available member content, brand, and navigation. Image 2 is the previously generated public-page concept and is a style/hierarchy reference only; preserve its generous whitespace, restrained borders, larger modules, and sparing neon-lime accent, but do not copy its marketing sections, pricing, testimonials, or signup CTAs.
Scene/backdrop: near-black authenticated web app with subtle charcoal surfaces, low-contrast borders, white/off-white typography, muted gray metadata, and neon-lime reserved for primary actions, progress, and small status signals.
Subject: A compact desktop dashboard with this information architecture in order: existing authenticated header; greeting with `Welcome back, Alex`, `Main`, and `Here's what to focus on this week.`; one dominant `Your next step` sprint card for `QA Active Community Sprint` with `5 of 8 checkpoints complete`, `Ship the evaluation pipeline`, `Due Friday`, `Open sprint plan`, and `View cohort`; an asymmetric work area with a wider `Continue` column containing `AI Hero: 7-Day AI Agents Crash-Course` and a Book Club progress card for `Inference Engineering` by Philip Kiely, plus a smaller `Up next` column with two events; a single Community strip for Active poll, Slack community, and Sprints & cohorts; three editorial `Latest from the community` rows; one compact Explore icon row for Courses, Workshops, Events, Resources, and Projects; compact footer.
Style/medium: realistic shippable member-product UI, refined editorial dashboard, crisp modern sans-serif type, polished Figma-style render, no concept-art effects, no photography.
Composition/framing: full-length desktop page at 1440px visual width, approximately one-and-a-half browser viewports tall; max-width 12-column content grid; generous section gaps; larger modules with fewer borders; no more than three items in any group.
Color palette: existing near-black/charcoal theme, off-white text, muted grays, and neon-lime used sparingly.
Constraints: make the sprint plan the obvious primary action; make Book Club a first-class ongoing-progress module rather than a generic quick-action tile; retain member navigation; keep copy short; use progressive disclosure; accessible contrast; one primary CTA per major area; implementable with the existing design system.
Avoid: marketing copy, pricing, testimonials, signup forms, public conversion CTAs, six equal quick-action cards, five-item content walls, repeated empty states, photos, book-cover art, section dots, excessive lime or borders, tiny text, equal emphasis, invented analytics charts, unrelated logos, or watermarks.
```

## Validation notes

- [FACT logged-in-dashboard-render] The generated hierarchy and required Book Club placement are present.
- [INFERENCE logged-in-dashboard-template] The event names and dates inside `Up next` are illustrative design content and require binding to real event/book data during implementation.
- [HUMAN] The concept is a product-direction artifact, not an approved implementation specification.
