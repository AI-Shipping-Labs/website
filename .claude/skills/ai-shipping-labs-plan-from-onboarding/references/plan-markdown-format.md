# Plan Markdown Format

`scripts/import_sprint_plan_markdown.py` expects these `##` sections.

## Summary

Use labeled bullets:

```markdown
## Summary

- Current situation: ...
- Goal for the next 6 weeks: ...
- Main gap to close: ...
- Weekly time commitment: ...
- Why this plan is the right next step: ...
```

## Focus

```markdown
## Focus

- Main focus: ...
- Supporting focus: ...
- Supporting focus: ...
```

## Timeline

Use one `Week N:` block per sprint week. Bullets become checkpoints.

```markdown
## Timeline

Week 1:
- ...
- ...

Week 2:
- ...
```

## Resources

Each bullet is parsed by `plans.resource_display.parse_resource_bullet`. Keep bullets human-readable and include URLs when available.

```markdown
## Resources

- Open Food Facts API/docs — reference data source for the nutrition knowledge base: https://...
- FastAPI deployment reference — use only for the API layer: https://...
```

## Deliverables

```markdown
## Deliverables

- Public deployed application URL
- README explaining architecture and eval approach
- Short demo post or Loom walkthrough
```

## Accountability

Plain markdown text. Include check-in cadence and demo expectations.

## Next Steps

Bullets become next-step action rows.

## Internal Sections

These become internal member notes, not member-facing plan content:

```markdown
## Persona
...

## Background
...

## Initial Input
...

## Questions and Answers
...

## Internal Recommendations
...

## Internal Action Items
...

## Sources
...
```
