# Milestone specs

Each AHL milestone has one spec here, named `<id>-<slug>.md`. The product-level spec is [`docs/specs.md`](../specs.md). The milestones of the current iteration (A1–A3) belong to the biotope-bench roadmap, [`docs/roadmap.md`](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md), whose decisions they follow.

## Lifecycle

1. **Draft.** The orchestrator writes the spec and opens a PR with it.
2. **Approved.** Vlad reviews the spec and merges it to `main`. Implementation starts only after that.
3. **Frozen.** The implementation PR links the spec and never edits it. A change needs Vlad's approval in a PR comment first. The orchestrator then commits it as `spec-change: …` (see "Specs are the contract" in [`CLAUDE.md`](../../CLAUDE.md)).

Specs have no status line: merged means approved. Implementation status is tracked in the iteration's tracking issue, currently [slolab/biotope-bench#1](https://github.com/slolab/biotope-bench/issues/1).

## Rules for writing a spec

- A spec says **what and why**, not how. Most of it goes to the goal and its reasons, scope, non-goals, interfaces and acceptance criteria. The design is a short sketch.
- **Binding:** the goal, scope, non-goals, interfaces and acceptance criteria. **Guidance:** the design sketch and the risks. The implementer may depart from the sketch and explains why in the PR. That needs no spec change.
- **Interfaces** are specified exactly where other code or people depend on them: commands and flags, exit codes, file layouts, schemas and fields, config keys, and the Python API that scripts call. Everything else is the implementer's choice.
- Each **acceptance criterion** describes an observable outcome, carries an id (`AC-1`, `AC-2`, …) and names its test tier: `unit`, `docker` or `live`. It is testable as written. It states what must be true, not which modules, functions or algorithms produce it. Credit-spending tests are `live` only.
- **Non-goals** are listed explicitly, so scope cannot grow unnoticed.
- **Freedom to operate** says what the implementer decides, so nobody needs a spec change for an internal choice.
- A spec fits in about 80 lines. If it grows longer, it is probably describing the implementation.
- AHL stays a generic tool: no benchmark-specific behaviour in an AHL spec.

## Template

```markdown
# <ID>: <title>

Repo: <repo> · Needs: <milestones> · Roadmap decisions: <numbers>

## Goal and why
What exists afterwards that did not exist before, who needs it, and why.

## Scope
## Non-goals
## Interfaces
Contracts others rely on. Exact where they depend on it.
## Acceptance criteria
- **AC-1** (unit) …
## Freedom to operate
What the implementer decides.
## Design sketch (non-binding)
A few bullets of initial analysis: a likely approach, known pitfalls, pointers to existing code.
## Risks and open questions
## Evidence required in the PR
```
