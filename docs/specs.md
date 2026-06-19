# Agent Harness Lab — Product & Technical Specification

**Status:** Draft for team handoff
**Working title:** Agent Harness Lab (AHL)
**Author:** Vlad
**Spec style:** Requirements-first. This document states *what* the system must do and how we will know it's done. It deliberately avoids prescribing *how*. Candidate implementations are listed in Appendix A as non-binding references; the team owns the engineering choices.

---

## 1. Purpose

We are building tools — MCP servers and skills (today: `biotope`, `biocypher`, possibly `biochatter`; tomorrow: anything) — that only deliver value **when an LLM agent uses them inside a harness**. A tool that executes correctly in isolation can still be useless, confusing, or misused by an agent. The only way to observe that is to run a real harness against the tool over real data and inspect what happened.

AHL is the development environment that makes that observation **isolated, traceable, debuggable, and repeatable**. It lets an engineer launch a chosen harness (e.g. Claude Code, Goose) wired to a chosen set of our MCP servers/skills, hand it a task and a dataset, and then step through, trace, and replay everything the agent did — at the granularity of individual reasoning turns and tool calls.

AHL is **general-purpose**. Knowledge-graph tooling is the first consumer, not the design target. Nothing in the core may assume KGs, biology, or any specific tool.

## 2. Problem statement

Current practice (driving harnesses like Claude Code / Cursor by hand on a paid subscription) has four defects we must remove:

1. **Not isolated.** The agent can read arbitrary host files and reach arbitrary network endpoints, so behaviour is non-reproducible and benchmarks can be gamed (the agent finds answers outside the provided data).
2. **Not observable at the right granularity.** We can see chat output, but not a structured, inspectable record of each LLM turn and each tool call, with inputs/outputs/decisions.
3. **Not debuggable.** There is no way to pause at a step, inspect the exact request the agent issued, alter it, and continue — the equivalent of a source-level debugger, but for an agent trajectory.
4. **Not repeatable / cost-controlled.** Every iteration spends tokens and runs through a vendor subscription; we cannot re-run a trajectory deterministically or use our own (e.g. GCP/Vertex) credentials.

AHL exists to fix exactly these four.

## 3. Scope

### In scope
- A reusable, isolated runtime that launches a pluggable agent harness wired to a pluggable set of tools (MCP servers and/or skills) over a provided dataset and task.
- Full capture, tracing, and (mechanism TBD, see §10) live step-debugging and deterministic replay of agent runs — built on each harness's own on-disk logs/session state, not on a wire-level proxy.
- Provider routing to our own LLM credentials (Vertex/Gemini and others).
- A task/fixture format and a run orchestrator suitable for both interactive debugging and automated evaluation/benchmarking.
- Packaging as a reusable template/repo that teams can instantiate for any toolset.

### Out of scope (non-goals)
- Building our own agent reasoning loop / general coding agent. AHL drives existing harnesses; it does not replace them.
- Any domain logic (KG construction, biology, etc.). Those live in the tools under test, never in AHL.
- A production end-user runtime or UX. AHL is a development and evaluation environment.
- Authoring the tools themselves. AHL is the lab the tools are tested in.

## 4. Users and primary use cases

**Primary user:** our own engineers developing MCP/skill tools.

Representative use cases the system must support end-to-end:

- **U1 — Observe usefulness.** "Mount my MCP server, give the agent this task and dataset, and show me exactly how the agent used (or failed to use) my tools."
- **U2 — Debug a failure.** "The agent did the wrong thing at step 7. Let me pause there, see the exact prompt and tool call, change it, and continue."
- **U3 — Reproduce cheaply.** "Re-run yesterday's trajectory deterministically without spending tokens, so I can fix the tool side and confirm the fix."
- **U4 — Compare.** "Run the same task with a different model / different harness / different tool version and diff the trajectories and outcomes."
- **U5 — Benchmark with integrity.** "Run a frozen task suite under isolation that guarantees the agent could not have obtained answers from outside the provided data, and report pass/fail + cost + trace per task."
- **U6 — Reuse.** "Stand up the same lab for a completely different toolset in well under a day, changing only configuration and fixtures."

## 5. Definitions

- **Harness** — an existing agent runtime that decides and acts in a loop (e.g. Claude Code, Goose). Treated as a black box driven via its headless/non-interactive interface.
- **Tool / capability** — an MCP server or a skill exposed to the harness.
- **LLM plane** — the request/response channel between the harness and the model provider. One agent reasoning *step* = one LLM turn here.
- **MCP plane** — the JSON-RPC channel between the harness and an MCP server. One *tool call* = one request/response here.
- **Trajectory** — the ordered sequence of LLM turns and tool calls produced by a single run.
- **Cassette** — a recorded trajectory (all intercepted requests/responses) that can be replayed deterministically.
- **Task / fixture** — a self-contained unit: a prompt/goal, an input dataset, and (optionally) expected results / checks.
- **Run** — one execution of a harness over a task with a given tool set, model, and mode.

## 6. Architectural model & guiding principles (constraints on all requirements)

AHL is, structurally, an **emulator + debugger for agent tools**. The correspondence is exact and the team should hold it in mind throughout (full table in Appendix C):

- the **tool under test** (MCP/skill) is the **app**;
- the **harness** (Claude Code, Goose) is the **device/OS runtime** the app runs on;
- the **LLM** is the **CPU** — an instruction stream we did not author and cannot single-step internally;
- **AHL** is the **emulator host + debug bridge + IDE** wrapped around all of it.

The principles below follow from that model. **Decision (supersedes earlier drafts): no wire-level proxy.** Every harness already writes a full local record of its own LLM turns and tool calls (chat/session JSONL, `logs.json`, etc., under its own home/config dir — see `src/ahl/gemini.py`, `src/ahl/opencode.py`). AHL gets observability by mounting those directories and parsing them, not by sitting on the wire between the harness and its provider or its MCP servers. This is simpler and avoids reimplementing every harness's and provider's wire protocol, but it changes what's achievable for live pause/mutate and replay — see the call-outs in FR-F and FR-G and open decision §10.6.

- **Host/target split — debugger and trace store live outside the target.** The target (harness + agent + tools) runs inside the sandbox. The debugger/control UI and the trace store live on the host and read the target's state via mounted log/session directories. The one exception is the provider API key: each harness talks to its provider directly, so its key is injected into the target's environment — there is no proxy to keep it host-side instead.
- **Observability from harness-native state, not wire interception.** AHL observes the agent's LLM turns and tool calls by parsing each harness's own log/session output. It must not depend on modifying or reverse-engineering harness internals — only on reading what the harness already writes, and on knowing where it writes it.
- **Isolation and observability are independent mechanisms.** Filesystem/network isolation (FR-A) constrains what the sandboxed harness can reach; it does not need to be in the traffic's path to be effective, because capture happens from on-disk logs, not from interposing on a connection.
- **Telemetry and control are separate channels.** Passive tracing (the `logcat` equivalent — here, the harness's own logs) is always-on and independent of whether a debugger is attached; interactive step-control (the debugger equivalent) is a distinct channel. Neither may depend on the other being active.
- **Determinism comes from replay, not from the run — mechanism now open.** Unlike a real emulator whose CPU is deterministic, our "CPU" (the LLM) is **not** reproducible, so record/replay would still be the only source of determinism *if* it can be built without a proxy to serve recorded responses in place of the provider. Recording (FR-G1) is just log capture and still lands at M2; deterministic replay (FR-G2) needs a mechanism that doesn't depend on intercepting the request before it reaches the provider — unresolved, see §10.6.
- **Snapshots/replay and fault injection are first-class, not bolt-ons — pending §10.6.** As in emulators (save-state, fake-GPS/sensor injection, network throttling), the ability to re-run from a recorded state and to inject altered conditions are core capabilities designed in from the start; for AHL specifically, both depend on the replay mechanism resolved in open decision §10.6.
- **No domain logic in the lab.** AHL must remain agnostic to what the tools do.
- **Configuration over code.** Swapping harness, model, tool set, or task must be configuration changes, not code changes.
- **Our credentials, our data.** All model calls use our own provider credentials (injected directly into the harness); all run data (traces, cassettes, artifacts) is stored where we control it.

---

## 7. Functional requirements

Each requirement has an ID, a statement, rationale, and acceptance criteria (AC). "Must" = required for v1; "Should" = required for v1.x; "May" = optional.

### A. Isolation & sandboxing

**FR-A1 — Filesystem isolation (Must).**
A run must execute with access limited to an explicitly declared set of paths: the task's dataset (read-only by default), a designated writable workspace, and nothing else on the host.
*Rationale:* reproducibility and benchmark integrity (U5).
*AC:* A run attempting to read a host path outside the declared set fails or is denied, and the attempt is recorded. Datasets are not mutated unless explicitly declared writable.

**FR-A2 — Network egress control (Must).**
A run must have default-deny egress with an explicit allowlist limited to the harness's own provider endpoint(s) and task-declared endpoints (e.g. a local database).
*Rationale:* prevents the agent from retrieving answers from outside the provided data.
*AC:* A run's attempt to reach a non-allowlisted host is denied and recorded. Egress policy must use allowlist (not denylist) semantics.

**FR-A3 — Reproducible environment (Must).**
The runtime environment (OS userland, tool versions, dependencies) must be declared and reproducible across machines and CI.
*AC:* Two engineers on different machines obtain byte-identical environments from the same declaration; the same applies in CI.

**FR-A4 — Isolation levels (Should).**
The system should offer at least two isolation strengths: a fast local mode for inner-loop development and a stronger mode (stronger kernel/VM-level isolation) for untrusted or CI runs, selectable by configuration.

**FR-A5 — Host/target split for control and trace tooling (Must).**
The debugger/control surface and the trace store must run on the host, outside the sandboxed target, and read the target's state via mounted log/session directories rather than sitting in the traffic path. The target carries the harness, agent, and tools, plus the one secret it needs to talk to its provider directly (the injected API key) — there is no broader secret surface to keep off the target.
*Rationale:* keeps debugging/observability tooling and persisted trace data under our control without requiring a wire-level proxy (see §6, NFR-3).
*AC:* Removing the debugger/control surface does not stop a run from executing and producing logs (control is host-side and optional); the target's filesystem and environment contain no secrets beyond the one provider key the harness needs.

### B. Harness integration (pluggable agents)

**FR-B1 — Pluggable harness (Must).**
AHL must drive at least two harnesses (target: Claude Code and Goose) through their non-interactive/headless interfaces, selectable by configuration, with no change to AHL core.
*AC:* `harness=claude` and `harness=goose` both run the same task against the same tools and produce comparable trajectories and traces.

**FR-B2 — Headless, scriptable invocation (Must).**
A run must be launchable non-interactively (prompt + config in, structured event stream + exit status out) suitable for scripting and CI.
*AC:* A run completes unattended and returns a machine-readable result (success/failure, artifacts, trace reference, exit code).

**FR-B3 — Live step event stream (Must).**
While a run executes, AHL must consume a structured, real-time event stream of the agent's steps (turns, tool calls, results) for display and control — e.g. tailing the harness's own log/session files as they're written, where the harness supports it.
*AC:* Each agent turn and tool call appears as a discrete structured event during execution, not only after completion.

**FR-B4 — Harness capability declaration (Should).**
Differences between harnesses (supported tool-config format, control surface, output format) must be captured in a per-harness adapter declaration so the core treats all harnesses uniformly.

### C. Tool / capability mounting

*Implementation: see `docs/capability-architecture.md` for the AHL-side adapter design and `docs/capability-format.md` for the bundle format these requirements assume.*

**FR-C1 — Mount arbitrary MCP servers (Must).**
A run must expose a configurable set of MCP servers to the harness, declared per task or per run.
*AC:* Changing the mounted server set is a configuration edit; the harness sees exactly the declared servers and no others.

**FR-C2 — Mount skills (Should).**
A run must be able to provide skills (recipes/workflows) to harnesses that support them, declared the same way as MCP servers.

**FR-C3 — Tool versioning (Must).**
The exact version/build of each mounted tool must be recorded with the run so results are attributable to a tool version (U4).
*AC:* Every run's record names the version of every tool it exposed.

**FR-C4 — Hot reload of tools under test (Should).**
During interactive development, an engineer must be able to modify a mounted tool's code and have the change take effect without tearing down the run/session.
*Rationale:* tight inner loop (U1/U2).
*AC:* Editing a tool's implementation and re-issuing a tool call exercises the new code without restarting the harness session.

### D. Provider routing & LLM call capture

**FR-D1 — Provider routing to our credentials (Must).**
All model calls must be routed to providers of our choice using our own credentials (Vertex/Gemini required; others pluggable). No dependency on a vendor subscription.
*AC:* A full run completes using only our Vertex/Gemini credentials; provider/model is selectable by configuration.

**FR-D2 — LLM call capture from harness-native logs (Must).**
Every LLM turn a harness makes during a run must be captured by reading that harness's own on-disk log/session files (e.g. Gemini CLI's `tmp/workspace/chats/*.jsonl` + `logs.json`, already parsed in `src/ahl/gemini.py`) rather than by intercepting wire traffic. Capture fidelity — how much of system prompt, tool definitions, and token/cost metadata is recoverable — is bounded by what each harness chooses to log, and must be documented per harness rather than assumed uniform.
*AC:* For any completed run, the LLM turns a harness logged are retrievable and normalized into one trace; gaps in what a given harness logs are documented, not silently dropped.

**FR-D3 — Model/cost control (Should).**
The system must allow selecting model per run and must surface per-run and per-step token usage and cost, where the harness's own logs report it.
*AC:* Each run reports total and per-step cost it could recover from the harness's logs; model is a configuration parameter.

### E. Tool-call capture

**FR-E1 — Tool-call capture from harness-native logs (Must).**
Every tool call a harness makes during a run must be captured by reading that harness's own log/session output, the same way as FR-D2, rather than by interposing on the MCP transport.
*AC:* For any completed run, the tool calls the harness logged are retrievable with arguments, result/error, and timing, to the extent the harness logs them.

**FR-E2 — No modification of tools under test (Must).**
Capture must not require modifying the MCP servers/skills under test, or the harness.
*AC:* An unmodified MCP server can be mounted and its calls appear in the trace, provided the harness logs them. Harnesses that under-log tool-call detail (e.g. name only, no arguments) are a documented per-harness gap, not a system-wide blocker — enabling a harness's own debug-logging flags, where available, is in scope.

### F. Live step-debugging

*Mechanism note: this group's "Must"s assumed a wire-level proxy that could hold a request before forwarding it. With no proxy (§6), pausing before an LLM turn or tool call reaches the provider/server has no obvious mechanism from log-watching alone. Treat this whole group as blocked on open decision §10.6 until a per-harness pause/inject mechanism (if any) is identified — do not build against it as if "Must" is already achievable.*

**FR-F1 — Breakpoints on boundary crossings (Must).**
An engineer must be able to pause a run at a chosen point — before/after an LLM turn and before/after a tool call — and inspect the exact payload.
*Rationale:* U2; the agent-equivalent of a source debugger, at the only granularity that exists (boundary crossings).
*AC:* With a breakpoint set, the run halts at that boundary and exposes the full pending payload until the engineer resumes.

**FR-F2 — Step / continue controls (Must).**
From a paused state, the engineer must be able to step to the next boundary crossing or continue to completion or to the next breakpoint.
*AC:* `step` advances exactly one boundary crossing; `continue` runs to the next breakpoint or end.

**FR-F3 — Payload mutation / fault injection (Should).**
From a paused state, the engineer must be able to edit the pending payload before it is forwarded — e.g. alter a tool result, inject an error, or force a different tool choice — and continue.
*Rationale:* a first-class emulator capability (cf. fake-GPS/sensor injection, network throttling): test how the agent and tools handle malformed or adversarial conditions without contriving real inputs.
*AC:* An edited payload is what the downstream party receives; the edit is recorded in the trajectory.

**FR-F4 — Control surface, independent of telemetry (Must).**
The debugger must be operable by a human during a run (e.g. a TUI, a small web UI, or an interactive console). It must be a **separate channel from tracing**: tracing (FR-H) runs whether or not the control surface is attached, and the control surface adds pause/step/mutate on top. The specific surface is unconstrained; the capability and the separation are required.
*AC:* A run executes and is fully traced with no control surface attached; attaching the control surface adds interactive control without changing what is traced.

### G. Record / replay (deterministic) — the system spine

*Because the LLM is non-deterministic (§6), this capability group would be the only source of reproducibility, if achievable. Recording (FR-G1) is just log capture and still lands at M2. FR-G2's "serve the recorded response instead of calling the provider" mechanism assumed a proxy in the request path; with no proxy, it is an open question (§10.6) whether/how deterministic replay is achievable at all, e.g. via a harness's own resume/replay feature if it has one. Don't treat FR-G2/G3 as solved.*

**FR-G1 — Record trajectories (Must).**
Every run must be recorded by default as a cassette: the parsed harness-native log/session capture (FR-D2, FR-E1) saved alongside the run, sufficient to reconstruct the trajectory. Recording is part of capture, not a separate opt-in mode.
*AC:* Every completed run yields a cassette that fully identifies the sequence of requests and responses the harness logged.

**FR-G2 — Deterministic replay without token spend (Must, mechanism open — see §10.6).**
A recorded cassette must be replayable so that LLM responses are served from the recording rather than the provider, incurring no token cost, while the tool side executes for real.
*Rationale:* U3 — pay once to discover a trajectory; debug the tool side for free thereafter.
*AC:* Replaying a cassette reproduces the same LLM turns without contacting the provider; tool calls execute against the (possibly modified) tools.

**FR-G3 — Fork at step N (Should, depends on FR-G2 mechanism).**
An engineer must be able to replay a cassette up to a chosen step and then diverge (live, with real provider calls from that point).
*AC:* A replay can be branched at an arbitrary step; everything before is deterministic, everything after is live.

**FR-G4 — Trajectory regression tests (Should).**
Cassettes must be usable as fixtures in automated tests that assert properties of a trajectory (e.g. a given tool was called with given arguments; a failure no longer occurs).

### H. Tracing & observability

**FR-H1 — Unified trace per run, always-on (Must).**
LLM turns and tool calls parsed from a single run's harness-native logs must be correlated into one ordered, inspectable trace (a tree/timeline of turns and tool calls with inputs, outputs, timing, cost). Tracing is passive and always-on — it is the `logcat`-equivalent channel and must not require the debugger/control surface to be attached (see FR-F4).
*AC:* For any run, an engineer can open one view showing the complete ordered trajectory with drill-down into each step's payloads; the trace is produced identically whether or not a control surface was attached.

**FR-H2 — Persistent, queryable store (Must).**
Traces must persist across runs in a store we control and be queryable/filterable (by task, tool version, model, harness, outcome).
*AC:* Past runs are retrievable and comparable without re-execution.

**FR-H3 — Open instrumentation format (Should).**
Tracing should use an open, standard instrumentation format so additional planes/tools can emit into the same trace without bespoke integration.

**FR-H4 — Run comparison (Should).**
The system should support comparing two runs/trajectories (U4) — at minimum surfacing differing steps, outcomes, and cost.

### I. Task / dataset fixtures

**FR-I1 — Self-contained task format (Must).**
A task must be a self-contained, version-controllable unit declaring its prompt/goal, input dataset, mounted tools (or a reference to a tool set), and optional expected results/checks.
*AC:* A task directory can be copied/committed and run elsewhere without external state.

**FR-I2 — Read-only data by default (Must).**
Task datasets must be mounted read-only unless the task explicitly declares writable working data, to protect benchmark integrity and reproducibility.

**FR-I3 — Frozen benchmark suites (Should).**
Multiple tasks must be groupable into a named, frozen suite for repeatable evaluation (U5).

### J. Run orchestration

**FR-J1 — Single-command run (Must).**
Given a task, a tool set, a model, a harness, and a mode (interactive / record / replay / batch), the orchestrator must boot the isolated environment, launch the harness, drive it to completion, and collect outputs.
*AC:* One command (or one API call) takes those parameters and returns a run record (trace reference, artifacts, outcome, cost, exit status).

**FR-J2 — Artifact collection (Must).**
Files the agent produced in the writable workspace must be collected and associated with the run.
*AC:* A run's record links to all produced artifacts.

**FR-J3 — Batch / suite execution (Should).**
The orchestrator must run a suite of tasks unattended and aggregate results.

**FR-J4 — CI-runnable (Should).**
A run and a suite must be executable in CI with no interactive input.

### K. Evaluation & benchmarking

**FR-K1 — Per-task outcome (Must).**
For tasks that declare checks, a run must produce a pass/fail (or scored) outcome plus the trace and cost.
*AC:* A suite run yields a per-task result table with outcome, cost, and a link to each trace.

**FR-K2 — Integrity guarantee (Must).**
Benchmark results must be produced under FR-A1/FR-A2 isolation, so a result is attributable only to the provided data and tools.
*AC:* A benchmark run's record demonstrates that egress was locked to the declared allowlist and data was read-only.

**FR-K3 — Regression tracking (Should).**
The system should track outcomes for a suite across tool versions/models/runs to show whether changes improve or regress behaviour.

### L. Reusability / templating

*Implementation: see `docs/capability-architecture.md` for the harness-adapter extension-point design (FR-L2).*

**FR-L1 — Instantiable template (Must).**
The whole lab must be distributable as a template that a team can instantiate for an arbitrary toolset, changing only configuration and fixtures — no core code edits.
*AC:* A new toolset's lab is stood up and runs its first task in well under a day, touching only config and a task directory.

**FR-L2 — Clear extension points (Must).**
Adding a new harness adapter, a new provider, or a new tool must each be a localized, documented change at a defined extension point.

**FR-L3 — Documentation (Must).**
The template must ship with documentation sufficient for an engineer unfamiliar with AHL to create a task, run it, debug it, record/replay it, and read its trace.

---

## 8. Non-functional requirements

- **NFR-1 Generality.** Core must contain zero domain assumptions. Verified by standing up a non-KG toolset (FR-L1) with no core changes.
- **NFR-2 Performance (inner loop).** Pause/step/continue and hot reload must be fast enough for interactive use (sub-second control response; tool reload without session restart).
- **NFR-3 Security.** Each harness's provider API key is injected directly into its sandboxed container, since the harness talks to its provider directly with no proxy in between. No other secrets should be present in, or reachable from, the target. Debugger/control tooling and the trace store run host-side and never need the key themselves.
- **NFR-4 Portability.** Must run on engineers' machines (macOS and Linux) and in CI (Linux) from the same declaration.
- **NFR-5 Cost control.** Replay (FR-G2) must incur zero provider cost; live runs must report cost so spend is always visible.
- **NFR-6 Robustness of isolation.** Egress control must use allowlist semantics and must not be defeatable by trivial evasion (e.g. path/host tricks); validated by an explicit escape test (see milestones).
- **NFR-7 Data ownership.** All traces, cassettes, and artifacts persist in storage we control.

## 9. Acceptance milestones (phased definition of done)

**M1 — Isolated run, host/target split.** A harness runs a trivial task in the sandbox using our Vertex/Gemini credentials, with debugger/control UI and trace store host-side and only the harness's own injected provider key as a secret inside the target. Egress is default-deny with an allowlist limited to the harness's provider endpoint and task-declared endpoints; an explicit escape test confirms the agent cannot reach a non-allowlisted host or a host path outside the declared set. *(FR-A1, A2, A3, A5, B1, B2, D1)*

**M2 — Observable & recorded run.** The same run produces a unified, persisted, always-on trace covering every LLM turn and tool call the harness logged, with inputs/outputs/cost where recoverable; one mounted MCP server's calls appear as correlated steps; and the run is recorded by default as a cassette (the parsed log capture). Tracing works with no control surface attached. *(FR-B3, C1, C3, D2, E1, E2, G1, H1, H2)*

**M3 — Deterministic replay (the spine) — blocked on open decision §10.6.** A recorded cassette replays with zero provider cost, reproducing the LLM turns while tools execute for real; forking at a step is supported. This was meant to precede interactive debugging because replay would be our only source of determinism, but the mechanism for both M3 and M5 now depends on resolving §10.6 first. *(FR-G2, G3, G4)*

**M4 — Orchestrated task format.** A self-contained task (prompt + read-only dataset + mounted tools + checks) runs via a single command and returns a run record with outcome, artifacts, cost, trace, and cassette. *(FR-C, I1, I2, J1, J2, K1, K2)*

**M5 — Debuggable run.** On a live run or a replay, an engineer can set a breakpoint on a boundary crossing, inspect the pending payload, step/continue, and (target) mutate the payload before forwarding (fault injection) — all via a control surface separate from tracing. *(FR-F1, F2, F3, F4, C4)*

**M6 — Reusable template.** The lab is published as a template; a second, non-KG toolset is instantiated and runs its first task without core code changes, with documentation sufficient for an unfamiliar engineer. *(FR-L1, L2, L3, NFR-1)*

**M7 — Benchmark & compare.** A frozen suite runs unattended (locally and in CI), yields a per-task result table, supports comparing two runs (model/harness/tool-version), and tracks regressions across runs. *(FR-B1 second harness, D3, H4, I3, J3, J4, K3)*

## 10. Open decisions (to resolve before/at M1)

1. **Implementation language for the log/trace parser**: TypeScript, Python, or Rust. Trade-offs: proximity to each harness's log formats and the MCP tooling ecosystem vs. proximity to our eval/observability SDKs. (`src/ahl/gemini.py`, `src/ahl/opencode.py` are Python today.)
2. ~~Build vs. adopt for the LLM gateway.~~ **Superseded** — no gateway/proxy; provider routing is direct env-var injection (`src/ahl/harness.py`), already implemented.
3. **Live debug control surface.** TUI vs. web UI vs. interactive console for FR-F4 — moot until §10.6 resolves whether live pause/mutate is achievable at all.
4. **Isolation backend** for the two levels in FR-A4 (fast local vs. CI/untrusted).
5. **Trace store deployment** (self-hosted service vs. embedded) and retention policy.
6. **Deterministic replay and live pause/mutate without a proxy (new — blocks M3, M5).** FR-F and FR-G assumed a wire-level proxy that could hold a request before it reached the provider/MCP server, both to pause/mutate it live and to serve a recorded response in place of a live call. That mechanism is gone (§6). Before treating FR-F/FR-G as "Must," investigate: (a) whether any target harness exposes its own resume/replay or session-fork feature we could drive instead of building one; (b) whether a thin per-harness shim (not a general proxy) is acceptable for the harnesses we actually need this for; (c) whether to scope replay down to "replay the trace for inspection" (no token cost, but also no live tool re-execution) as a lesser but achievable goal if (a)/(b) don't pan out.

## 11. Risks

- **Harness opacity.** A harness may expose an insufficient headless/event interface, limiting FR-B3 or live control. Mitigation: per-harness adapters (FR-B4); fall back to log-based observability where in-loop control is unavailable.
- **Log capture fidelity varies by harness.** Capture depends entirely on what each harness chooses to write to its own logs/session files — coverage, granularity (full request bodies vs. summarized turns), and streaming-response handling differ per harness and per harness version. Mitigation: validate and document capture fidelity per harness explicitly at M2; treat under-logging harnesses as a per-harness gap, not a system-wide blocker.
- **Isolation evasion.** Allowlist enforcement must resist trivial bypass (NFR-6). Mitigation: explicit escape test in M1, allowlist-only semantics.
- **Replay may not be fully achievable without a proxy.** See open decision §10.6. Mitigation: scope down to trace-replay-for-inspection if live, zero-cost LLM replay isn't achievable per harness.
- **Replay drift.** Tool/environment changes can make a cassette no longer apply cleanly (FR-G2/G4, if/once achievable). Mitigation: record tool versions (FR-C3); treat replay mismatches as test signal.

---

## Appendix A — Candidate implementations (non-binding)

These map requirements to known OSS options to save the team discovery time. None is mandated.

- **Isolation (FR-A):** Anthropic's sandbox-runtime (`srt`, bubblewrap/Seatbelt) for fast local; devcontainer with default-deny `iptables` for CI; microVM sandboxes for the stronger level.
- **Harness headless (FR-B):** Claude Code `--print --output-format stream-json` (and `--input-format stream-json` for multi-turn); Goose `goose run`. The harness talks to its provider directly (real key injected, no gateway in between).
- **LLM call capture & routing (FR-D, H):** no gateway/proxy — provider routing is direct env-var injection (`src/ahl/harness.py`, already implemented); capture is parsing each harness's own log/session files into the unified trace (`src/ahl/gemini.py`, `src/ahl/opencode.py` are the first two parsers; `claude`/`agy` are next, per `TODO`).
- **Tool-call capture + hot reload (FR-E, C4):** capture is parsing harness-native logs, same as above, wherever the harness logs tool calls in enough detail; the official MCP Inspector remains useful for manual tool inspection during development. Hot reload (FR-C4) is independent of capture and still needs its own design.
- **Tracing store (FR-H):** a self-hosted, open observability platform with OTEL ingestion and a trace-tree UI, fed from the parsed harness logs rather than from a proxy.
- **Templating (FR-L):** a GitHub template repo and/or a parameterized scaffolding tool.

## Appendix B — The two observability planes (orienting model)

The agent's reasoning cannot be breakpointed internally; it is a closed process. But every step it takes is something the harness itself already records to disk:

- **LLM plane** — harness ↔ provider. One reasoning turn = one request/response; tool decisions appear as structured tool-call blocks. Most harnesses persist this in their own session/chat logs (e.g. Gemini CLI's `tmp/workspace/chats/*.jsonl`).
- **MCP plane** — harness ↔ MCP server. One tool call = one request/response. Harnesses that support MCP typically log tool invocations alongside the LLM-plane log, though detail varies by harness.

AHL captures both planes by mounting and parsing each harness's own log/session directory after (or during) a run, normalizing into one trace — not by sitting on the wire between the harness and its provider or its MCP servers. The egress allowlist (FR-A2) is just isolation; it has no role in observability under this design.

## Appendix C — Prior art: AHL as an emulator + debugger

AHL deliberately reuses the architecture of mature emulator/embedded toolchains (Android emulator + `adb`, iOS Simulator, QEMU snapshots, `gdbserver`/JTAG remote debugging). The team should treat that body of practice as the reference design. The correspondence:

| Emulator / embedded world | AHL |
|---|---|
| Real device + production runtime | Real deployed agent + live provider |
| Emulator / simulator | AHL sandboxed run environment |
| App under test | Our MCP server / skill |
| CPU + instruction stream | The LLM / agent reasoning (black box) |
| OS / device runtime | The harness (Claude Code, Goose) |
| `adb` / debug bridge | Mounted harness log/session directories + trace parser |
| `gdbserver` stub on target / `gdb` on host | Harness's own log writer in sandbox / debugger UI on host reading mounted logs |
| `logcat` / Console (passive, always-on) | Unified, always-on trace store (FR-H) |
| Emulator snapshot / save-state / time-travel | Cassette / record-replay / fork-at-step (FR-G, mechanism open — §10.6) |
| Fake GPS, injected events, network throttling | Payload mutation / fault injection (FR-F3, mechanism open — §10.6) |
| Emulator NAT + proxy | Egress allowlist (FR-A2) — no LLM-plane proxy |
| Pinned system image | Pinned environment + recorded tool versions (FR-A3, C3) |
| Device-farm matrix | Run matrix across models / harnesses / tool versions (M7) |
| Headless emulator on CI | Headless run in CI (FR-J4) |

**Topology rules inherited (see §6):**

1. *Debugger and trace store live on the host and read mounted logs; the target carries the harness plus the one provider key it needs.* (FR-A5, NFR-3)
2. *Telemetry and control are separate channels* — `logcat` ≠ debugger. (FR-H1, FR-F4)
3. *Same image, two surfaces* — one run definition, interactive locally or headless in CI. (FR-B2, J4)

**The one inherited assumption that does NOT hold:** an emulator's CPU is deterministic, so snapshots are a convenience. Our "CPU" is a non-deterministic LLM, so record/replay would be the only route to determinism *if* achievable without a proxy in the request path — which is now an open question (§10.6) rather than a settled spine. Recording (log capture, M2) still stands; replay (M3) is blocked on resolving that question.


---
I can learn Rust here — cheaply — precisely because the architecture is boundary-based: every component talks over HTTP / JSON-RPC / OTEL / the filesystem, so the system is polyglot for free. Write one bounded, self-contained, latency-insensitive piece in Rust and lose nothing:

Best Rust candidate: the orchestrator CLI (boots the sandbox, manages processes, collects artifacts, ships as a single static binary — clap/tokio are a pleasant learning project, and a static binary is genuinely nice to distribute to the team).
The trace parser / log normalizer => Python (closest to each harness's own log formats and our eval/observability tooling).
Worst place to start in Rust: deterministic replay, once a mechanism is even chosen (open decision §10.6) — it's the least settled part of the system.

Net: use Docker for isolation. Build the system in Python for speed and ecosystem. Scratch the Rust itch on the orchestrator CLI — bounded, off the critical path, a real binary to show for it — and leave replay for last.