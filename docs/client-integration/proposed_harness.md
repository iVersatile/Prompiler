# Integration Harness — Proposed Design (iteration-0)

> Status: **DRAFT**. Reframed from the iteration-0 baseline into a fresh
> standalone-project design. Still under active revision — unresolved
> counter-opinions and nice-to-haves are tracked in the last two sections.

## What this harness is (scope)

A **general-purpose, agent-team software-development loop**. It is domain-agnostic
and project-agnostic: a reusable machine for driving software work from a goal to a
mergeable PR with the least human intervention. It is **not** the myHealth↔prompiler
integration, and it is **not** bound to prompiler.

- **Its own tree / its own repo.** It is a standalone project, versioned in its own
  right, not a folder inside prompiler. That is the only framing consistent with
  reuse across (a) other prompiler clients and (b) projects with no prompiler
  relationship at all.
- **First pilot application:** the myHealth↔prompiler MCP integration. The pilot is
  how we prove the harness; it is not the harness's definition.
- **Borrows philosophy, not machinery.** It inherits *principles* proven in
  prompiler — fact-forcing before action, fail-closed on ambiguity, durable audit
  records — and rebuilds them as its own primitives. It does **not** sit on top of
  prompiler's `.scratch/STATE.md`, RULES.md gates, or the existing hook set.

## The agent team (3 agents)

Three peers, each in its own session, each with its own isolated context. The human
is none of them.

| Agent | Owns | Does |
|-------|------|------|
| **author** | the code repo(s) | implements; writes the code; raises clarifications when design has a gap/contradiction |
| **advisor** | backlog, requirements, plan, **priority** | design (plan + architecture); validates implementation against the design; code-reviews; accepts/rejects; suggests optimizations; **prototypes to settle assumptions** |
| **orchestrator** | the loop + its state | assigns tasks; drives the loop; enforces the gates; runs timers; produces the human action-list |

Quality authority is the **advisor** (design → validation → acceptance →
optimization all collapse here). Implementation is the **author**. Loop-driving and
gate-enforcement are the **orchestrator**.

## The loop (closing the circle)

A state machine, one work-item at a time:

```
BACKLOG → DESIGNED → ASSIGNED → IMPLEMENTED → VALIDATED → ACCEPTED → (PR) → MERGED
                 ↑__________________ REJECTED ____________|
                                  HALTED → human
```

Each arrow is a **guarded transition**: it fires only if its gate returns pass, and
it cannot fire without writing exactly one audit record. `REJECTED` kicks back to
DESIGNED/ASSIGNED with the failure reason attached. `HALTED` is the escape hatch —
any breach freezes the item and raises a human action.

## Prototyping = the trust mechanism

The core move that lets the loop run without a human refereeing every decision:

- **No design choice escalates to the human as a question if it can be settled
  empirically.** The advisor proposes → prototypes a throwaway spike that tests the
  load-bearing assumption → escalates only if the spike reveals a genuine fork that
  needs human judgment (a product/policy call, not a technical fact).
- It converts *"I think X, please confirm"* into *"I tested X, here is the evidence,
  here is the one thing only you can decide."*
- **author↔advisor disagreements are exhausted by a spike before either reaches the
  human.** This is prompiler's Fact-Forcing Gate lifted from *code edits* up to
  *design decisions*: a design is a genuine solution backed by evidence, not a claim.

## Prioritization (advisor-owned, human-free)

Priority is **not** a human touchpoint. The human seeds high-level goals; the
advisor translates them into a prioritized backlog and is the single source of truth
for ordering.

- **author proposes, advisor disposes.** When the author hits a technical constraint
  that forces reordering (item A is blocked on item B), the author does not argue
  priority — it raises the constraint on the work-item's clarification thread. The
  advisor either re-prioritizes (B before A) or maintains order and instructs how to
  unblock A without B.
- **Park & resume is durable state, not memory.** The author parks A
  (`state = PARKED`, with `blocked_by` + reason), pulls the advisor-designated next
  item, and the orchestrator auto-resumes A when its blocker clears.
- **Encode constraints as a DAG.** Work-items carry explicit `blocked_by: [item-id]`
  edges. The orchestrator computes a valid execution order from the DAG, so the
  author rarely has to "challenge" — it only escalates *newly discovered*
  dependencies the DAG didn't know about. This keeps priority drift out of the loop.

## Context & state model (per-agent isolation)

Each agent runs in its **own session with its own context window**. No shared
context. This is good for isolation and token economy, but it forces a discipline:

- **Durable artifacts are the only cross-agent channel.** Agents do not "remember"
  each other — they read state from disk: the work-item files, the per-item
  clarification thread, the backlog, the audit log. (This is the same principle that
  eliminates the inter-agent messenger.)
- **Per-agent private to-do list in memory.** Each agent keeps a private task list /
  scratchpad in its own memory namespace. It is the agent's *decomposition* of the
  assigned item — never the authoritative assignment.
- **Cold-start tolerance.** Any agent can be killed and restarted and must resume
  from durable state alone. On cold-start it re-reads, in order: (1) the
  orchestrator's authoritative assignment, (2) its own private to-do, (3) the
  work-item history — then continues.

**Challenges to design against:**

- **Authoritative-vs-private conflict.** If the private to-do disagrees with the
  orchestrator's assignment (e.g., the item was reassigned), the agent reconciles to
  the authoritative source and drops the stale private to-do. Ownership rule:
  orchestrator owns *assignment* state; the agent owns only its *private
  decomposition*.
- **Mid-action crash → dirty tree.** Cold-start must inspect `git status`; if tree
  state is ambiguous/unreadable, **HALT** (this fail-closed-on-unreadable-tree
  stance is one thing worth taking from prompiler).
- **Idempotency.** Resumed sub-steps must be checkpointed or idempotent so a
  re-run doesn't double-apply. Work-items need fine-grained, checkpointed sub-steps.
- **Context overflow on long items.** Each agent needs a compaction strategy — a
  durable progress summary in its scratchpad — so cold-start doesn't replay the
  whole history.
- **Handoff race.** Only one agent "holds" an item at a time; the state machine +
  orchestrator lock guarantee single-writer ownership per state.

## Clarification & idle mechanics

**Clarification channel (author→advisor): content peer-to-peer, control with the
orchestrator.** The clarification *content* goes author↔advisor directly, in a
**work-item-scoped, append-only thread** that is part of the audit trail — not
ephemeral chat, not a message relayed by the orchestrator (relaying would
re-introduce the messenger fatigue). The orchestrator subscribes to one bit only:
the item's `BLOCKED-ON-CLARIFICATION` flag, which drives its timers and loop-health
tracking.

**Author-idle: orchestrator is the trigger-owner, event-driven with a timer
backstop.** "Idle" is three different conditions:

- **idle-because-done** → orchestrator pulls the next backlog item (event: author
  emits `handoff`).
- **idle-because-blocked** → orchestrator confirms the clarification reached the
  advisor; the advisor's own idle is watched the same way.
- **idle-because-stalled** (silent hang) → heartbeat/timeout trips the circuit
  breaker → retry, then HALT→human.

So *who* = the orchestrator (it owns the loop); *when* = on every state change, plus
a timeout backstop for silence.

## Auto-applied guardrails (free the human from low-level labor)

Each fatigue maps to a mechanism, never to a human:

- **command-confirmation fatigue** → a declarative allowlist + sandbox; an
  out-of-policy command **auto-HALTs**, it never prompts a human per-command.
- **validation-gate fatigue** → validation is **deterministic, exit 0/1**, run by
  the orchestrator/advisor; the human never hand-runs it.
- **hard-floor-enforcer role** → the floor is **code** (transition preconditions),
  not a checklist a human polices.
- **inter-agent-messenger role** → agents read/write the durable work-item artifacts
  directly.

## The hard-floor (must ALL hold before the loop is *trusted* unattended)

The non-negotiable bar. No autonomy until every one is true:

1. **Deterministic validation** — every item carries a machine-checkable pass/fail
   (a command that exits 0/1). No "looks good" judgment in the gate. → **verifiable**
2. **Reversibility** — branch-per-item; never edits the trunk; never self-approves
   merge/tag/push. The loop's terminal output is a *PR*. This boundary is the
   subject of a later autonomy experiment (see nice-to-haves).
3. **Bounded scope** — each item declares allowed paths; an agent that exceeds them
   HALTs (fail-closed).
4. **Complete audit trail** — no transition without a durable record; if logging
   fails, the loop halts. → **auditable**
5. **Human escape hatch** — HALT + raise a human action on any gate failure, scope
   breach, unresolvable design fork, or N retries. Human can stop/resume.
6. **Replayable inputs** — external LLM/MCP calls are cassette-backed in validation
   so runs are deterministic and CI-safe. → **repeatable**

## Iteration-0, scoped to the three steps

**Step 1 — make the design work, close the circle.**
Walking skeleton: define the work-item schema (incl. `blocked_by`, `state`,
clarification thread), the stage contracts, the validation gate, and the acceptance
gate. Run **one item** design→accept with a **human at every handoff**. No autonomy.
*Exit:* one item reaches ACCEPTED with a complete audit trail, human-stepped.

**Step 2 — make the loop work, repeatedly, with logging/auditing/notification.**
Add the loop driver (auto-advances on green), per-agent context isolation +
cold-start resume, structured per-transition logging (JSONL), a queryable audit
aggregate, and notifications on halt/completion/escalation. Add circuit breakers
(max retries, scope-exceeded → halt). Human now only at the hard-floor boundary.
*Exit:* N items run unattended, full logs + notifications, halts safely on failure.

**Step 3 — make the loop smoother + higher quality, from feedback.**
Feed step-2 logs back: capture validation-failure patterns into design, track
metrics (pass rate, retry count, time-per-stage, human-intervention rate), run the
advisor↔author prototype iteration for quality, and tune hard-floor thresholds
against observed false-accept/false-reject.
*Exit:* measurable improvement vs the step-2 baseline (↑ pass rate, ↓
human-intervention rate).

## Human role (what's left after the above)

Three touchpoints, and the standing intent is to shrink even these:

1. **Seed goals.** The advisor turns them into a prioritized backlog. (Prioritization
   itself is advisor-owned — see above.)
2. **Release boundary.** A human merges/releases. The *value* of this gate is an open
   question (see nice-to-haves) — it must buy something agents cannot produce, not
   make the human a git-operator.
3. **HALT escalations.** Genuine forks that prototyping could not resolve.

## Open decisions (still to resolve)

1. **Work-item granularity** — one item = one PR-sized change, or finer? Finer =
   more loop iterations, smaller blast radius per item.
2. **Notification channel** — push sink / chat sink / file + terminal for
   iteration-0?
3. **First pilot slice** — should step-1's item be a throwaway, or the genuinely-first
   integration slice (e.g., the appointment EntitySpec round-trip over MCP)?

## Nice-to-haves (note now, experiment once the loop runs effectively)

These are deliberately deferred until the harness is running reliably.

1. **Autonomy levels + verified trust boundary.** Define graded autonomy
   (L0 human-at-every-handoff → … → unattended-to-PR). Increase a level only by
   *evidence* that the trust boundary held — e.g., an automated check proving no
   agent action fell outside its declared scope/allowlist over N items. Goal: raise
   autonomy without ever letting an agent cross the boundary undetected.
2. **The value of the release gate (open question to answer).** Is human release-gating
   **preventive** (catch what automation missed — regressions, scope creep, security)
   or **contributive** (inject product/strategic judgment automation cannot encode)?
   Stated intent: the human must not be a git-operator. Working lean — the gate should
   be *contributive*; anything *preventive* should be automated (e2e scenarios,
   security scans) so it doesn't need a human. If the human is doing prevention at the
   gate, that is a signal of an automation gap to close. Decide explicitly *what* the
   gate prevents/contributes, and *against what*.
3. **Human↔agent communication mechanics.** The human receives an **action-list from
   the orchestrator** (single pane). Open: does the human then talk to each agent via
   3 separate sessions, or only ever to the orchestrator (which routes to
   author/advisor via the same durable artifacts)? Working lean — single-pane via the
   orchestrator action-list by default, with direct work-item threads for the
   occasional design conversation with the advisor; the human should not juggle three
   live sessions.
4. **Visualization.** A dashboard of test-coverage, integration progress, and loop
   metrics (pass rate, retries, time-per-stage, human-intervention rate). Note for
   later; discuss along the way.
