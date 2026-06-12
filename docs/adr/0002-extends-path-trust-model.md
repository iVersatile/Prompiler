# ADR 0002 — `extends` path resolution: trusted authoring surface, no confinement

- **Status:** Accepted
- **Date:** 2026-06-12
- **Phase:** pre-Q4 (Streaming) hardening — surfaced by the RULES §6 phase-start gate review

## Context

`_resolve_parent_path` in `src/prompiler/spec/loader.py` resolves a spec's
`extends` reference relative to the child file's directory; absolute references
are used as-is. There is no boundary or traversal check:

```python
ref = Path(extends_ref)
if ref.suffix == "":
    ref = ref.with_suffix(".yaml")
if ref.is_absolute():
    return ref
return child_path.parent / ref
```

The Q4 phase-start gate review flagged this as a **LOW** finding: a spec's
`extends` value can point at any file the invoking user can read
(`extends: /etc/secrets.yaml`, `extends: ../../../config.yaml`), and a parse
failure on that file leaks its existence and shape through the `SpecLoadError`
message.

Every `extends` reference in the repository today — in tests
(`tests/test_spec_loader.py`) and examples — uses a **bare, same-directory**
ref (`base_invoice`, `gp`, `p`). No `../` or absolute `extends` exists in-tree.

## Decision

**No confinement.** Treat `extends` as a *trusted authoring surface*, analogous
to a Python `import` or a Make `include`: the reference is source written by the
spec author, and resolution is bounded by the invoking user's own filesystem
permissions. No privilege boundary is crossed in any shipping code path —
`prompiler` reads only what the user running it can already read.

Three alternatives were considered and rejected for now:

1. **Confine resolved paths to the entry spec's root directory** (`is_relative_to`).
   Breaks the legitimate "shared base in a sibling directory" layout, e.g.
   `specs/invoices/child.yaml` → `extends: ../base/common.yaml`.
2. **Reject absolute refs and any `..` component.** Same breakage, more strictly.
3. **Opt-in `root: Path | None` parameter on `load_spec`.** Adds confinement
   machinery that no current caller would use — speculative generality (YAGNI),
   since there is no multi-tenant / CI "compile untrusted specs" path in
   `docs/PRD.md` today.

## Consequences

- **Positive:** Relative `../` and absolute `extends` refs keep working; no
  legitimate spec layout is broken; no unused confinement code to maintain.
- **Positive:** The lack of a boundary check is now a *recorded decision*, not an
  oversight — a future reader at `_resolve_parent_path` sees a docstring pointer
  to this ADR and won't "fix" it into a regression.
- **Negative / accepted:** A hypothetical future caller that compiles untrusted,
  third-party specs would read attacker-chosen files within the invoking user's
  permission set, and could probe file existence via error messages. That risk
  is explicitly **out of scope** until such a caller exists.

## Revisit when

Supersede this ADR (with ADR 0003, adding confinement) the moment a
**multi-tenant or CI "compile untrusted specs" entrypoint** is introduced —
i.e. specs authored by someone other than the user invoking `prompiler`. The
preferred fix at that point is alternative 3 (an opt-in `root` enforced only at
that entrypoint), so single-user local compilation stays unconfined.
