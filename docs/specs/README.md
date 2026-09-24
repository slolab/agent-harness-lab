# Milestone specs

Each AHL milestone has one spec here, named `<id>-<slug>.md`. The product-level spec is [`docs/specs.md`](../specs.md). The milestones of the current iteration (A1–A3) belong to the biotope-bench roadmap, [`docs/roadmap.md`](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md), whose decisions they follow.

## Lifecycle

1. **Draft.** The orchestrator writes the spec and opens a PR with it.
2. **Approved.** Vlad reviews the spec and merges it to `main`. Implementation starts only after that.
3. **Frozen.** The implementation PR links the spec and never edits it. A change needs Vlad's approval in a PR comment first. The orchestrator then commits it as `spec-change: …` (see "Specs are the contract" in [`CLAUDE.md`](../../CLAUDE.md)).

Specs have no status line: merged means approved. Implementation status is tracked in the iteration's tracking issue, currently [slolab/biotope-bench#1](https://github.com/slolab/biotope-bench/issues/1).

## Rules for writing a spec

- Each acceptance criterion describes observable behaviour, carries an id (`AC-1`, `AC-2`, …) and names its test tier: `unit`, `docker` or `live`.
- A criterion is testable as written. "Works well" is not a criterion. "Exits with code 124 and writes `result.json` with `status: timeout`" is.
- Non-goals are listed explicitly, so scope cannot grow unnoticed.
- Interfaces (CLI flags, file layouts, schemas) are specified exactly where other code depends on them, and left to the implementer where nothing does.
- AHL stays a generic tool: no benchmark-specific behaviour in an AHL spec.

## Template

```markdown
# <ID>: <title>

Repo: <repo> · Needs: <milestones> · Roadmap decisions: <numbers>

## Goal
One paragraph: what exists after this milestone that did not exist before.

## Scope
## Non-goals
## Design and interfaces
## Acceptance criteria
- **AC-1** (unit) …
## Test plan
## Risks and open questions
## Evidence required in the PR
```
