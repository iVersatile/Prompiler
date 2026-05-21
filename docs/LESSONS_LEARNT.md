# Lessons Learnt — prompiler

Append-only. Indexed by tag. See `docs/RULES.md` §4 for the workflow.

---

## How to use this file

**Before fixing any bug or regression:**

1. Identify the surface area of the work (e.g., `compiler`, `adapter-claude`, `mcp-server`).
2. For each matching tag, run:
   ```
   grep -E "^- \*\*Tags:\*\* .*\b<tag>\b" docs/LESSONS_LEARNT.md
   ```
3. Read every matching entry in full.
4. Apply any relevant lesson. Cite it in the commit body (e.g., `Applying LL-007`).

**On user "add lesson" command:** append a new entry. Never edit an existing one — if a previous lesson is superseded, write a follow-up entry that references the original by ID and leave the original intact.

---

## Tag registry

Use the smallest set of tags that accurately classify the entry. Add a new tag only when none of the below fits.

| Tag | Surface area |
|-----|--------------|
| `compiler` | Spec → artefact compilation pipeline |
| `runtime` | Backend dispatch, retry, redaction |
| `adapter-claude` | Anthropic Claude adapter |
| `adapter-openai` | OpenAI adapter |
| `adapter-gemini` | Google Gemini adapter |
| `adapter-ollama` | Local Ollama adapter |
| `mcp-server` | MCP server (stdio + HTTP) |
| `cli` | `prompiler` CLI surface |
| `cassettes` | VCR-style fixture recording / playback |
| `container` | Dockerfile, image hardening, supply-chain |
| `ci` | GitHub Actions, matrix, caching, gates |
| `release` | Tagging, changelog, signing, SBOM |

---

## Entry template

Copy this block when appending a new entry. Increment `NNN` from the highest existing ID. Use ISO-8601 (`YYYY-MM-DD`) for the date.

```markdown
## LL-NNN — <short title>

- **Date:** YYYY-MM-DD
- **Tags:** `<surface-1>`, `<surface-2>`
- **Symptom:** What was observed
- **Root cause:** Why it happened
- **Fix:** What was changed
- **Prevention:** How to avoid repeating it
```

---

## Entries

_None yet._
