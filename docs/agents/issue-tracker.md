# Issue tracker: Local Markdown

Issues and PRDs for this repo live as markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The PRD is `.scratch/<feature-slug>/PRD.md`
- The session-state map is `.scratch/<feature-slug>/STATE.md` (see "Session restart" below)
- Implementation issues are `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- Triage state is recorded as a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings)
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Session restart

`STATE.md` is the per-feature **session-state map**. Its job is to orient a fresh
agent and point it at authoritative detail — **a map, not a context dump**. It
must not restate `docs/PLAN.md`, `docs/RULES.md`, or git history; it references
them. Restating causes overload and silent rot.

### Required sections

- **Resume read-order** — numbered list of files to read, STATE.md first, then
  the authoritative spec (`docs/PLAN.md`) and rules (`docs/RULES.md`).
- **Where we are** — one short paragraph: current phase, what is done, what is
  not started.
- **Status → PLAN.md line refs** — task checkboxes with line references into
  `docs/PLAN.md`. Do not copy the task detail; point to it.
- **Next** — the single next task, with its PLAN line range and concrete touch
  points (file:line). One task, not a backlog.
- **Carried rules** — standing constraints that survive across sessions (e.g.
  go/TDD protocol, never self-approve phase-done, hard constraints).

### Verify before trusting

STATE.md goes stale silently — it is workflow scratch, not authoritative, and is
not pruned when the work it describes ships. Before acting on any "Next" or
"in progress" claim, cross-check it against `docs/PLAN.md` checkbox state and
`git log`. If they disagree, trust PLAN.md + git and update STATE.md.

### Write-on-exit discipline

Update STATE.md at the end of a working session (or when the next task changes):
flip the status refs, rewrite "Next", refresh the `Updated:` date and branch.
Keep it pointer-only — if a line restates detail that lives in PLAN.md, cut it.

### Active-feature pointer

`.scratch/ACTIVE` is a one-line plaintext file holding the current feature slug
(e.g. `q1-multimodal`). It is the stable indirection so the session-start prompt
never needs editing when work moves to a new feature. Update this file when the
active feature changes; STATE.md and friends live under
`.scratch/<that-slug>/`.

### Reusable session-start prompt

Fully path-stable — resolves the active feature through `.scratch/ACTIVE`, so the
prompt itself never rots:

> Read `.scratch/ACTIVE` to get the active feature slug, then read
> `.scratch/<slug>/STATE.md` and follow its resume read-order. Fill the Reality
> log in `RESTART_EXPECTATIONS.md` for this restart (MATCH/MISS/STALE). Verify
> STATE's status claims against `docs/PLAN.md` checkboxes + `git log` before
> trusting. Read `docs/RULES.md` §1 + §8. Resume the task under "Next" via
> go/TDD. Confirm the plan before writing code.
