# AISL content kinds (A7.2a)

Site-owned kinds registered from `content.kinds` at app ready, and by
`python -m community_base.content_sync.check --kinds content.kinds`.

## Member wiki storage

Public `wiki/` pages are the package `wiki` kind. They fill
`community_base.knowledge_base` and are public.

Member-gated pages live under `_wiki/` in the private wiki repository. They
are the site-owned `member_wiki` kind, stored in the `topics` app, gated at
Basic and above (issue 1688). The C7.12 `aisl-wiki` conversion profile that
writes `kind: wiki, path: wiki` must not be pointed at `_wiki/`. A7.3 converts
that section as `member_wiki`.

## Other site kinds

| Kind | Layout | Source collection |
|---|---|---|
| `workshop` | nested `workshop.yaml` | workshops-content |
| `project` | item directory `index.md` | content `projects/` |
| `curated_link` | flat `.md` | content `curated-links/` |
| `interview_question` | flat `.md` | content `interview-questions/` |

Entitlement keys stay under `extra` (D29). `events/*.yaml` is untouched (D30).

## Markdown extensions

`COMMUNITY_BASE["MARKDOWN_EXTENSIONS"]` appends `codehilite`,
`MermaidExtension`, `ExternalLinksExtension` and `EventWidgetExtension` to the
package list. Synced HTML still goes through the package sanitiser.
