"""``eval-report.html`` emitter — render an eval report dict to a static dashboard (P4).

Pure renderer: consumes the dict produced by
:func:`prompiler.eval.report_json.build_report` and returns a self-contained HTML
document (inline CSS + one inline vanilla-JS block for per-case table sort/filter).
No JS framework, no external assets, no network calls — the file opens straight from
disk. Every value sourced from the report is HTML-escaped to prevent injection.

Mirrors the ``report_json`` API: :func:`build_html_report` returns the string and
:func:`write_html_report` persists it. Output is deterministic so it can be pinned by
a golden-file snapshot test.

The layout is responsive (verified at 320 / 768 / 1440) and semantic/a11y-oriented:
``lang`` on ``<html>``, a single ``<h1>``, ``<th scope>`` on every table header, and an
``aria-label`` on the filter input. The inline JS is well under the 5 kB budget.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

_DOCTYPE = "<!DOCTYPE html>"


def _fmt(value: float) -> str:
    """Format a metric float deterministically to 4 decimals."""
    return f"{value:.4f}"


def _esc(value: object) -> str:
    """HTML-escape any scalar; ``None`` renders as an em dash."""
    if value is None:
        return "—"
    return escape(str(value))


_CSS = """\
:root {
  --bg: #ffffff;
  --fg: #1a1a1a;
  --muted: #5a5a5a;
  --border: #d8d8d8;
  --surface: #f6f6f6;
  --match: #1a7f37;
  --mismatch: #b42318;
  --missing: #9a6700;
  --extra: #6941c6;
  --accent: #0b5cad;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 1rem;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  line-height: 1.5;
  color: var(--fg);
  background: var(--bg);
}
h1 { font-size: 1.5rem; margin: 0 0 0.25rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 0.75rem; }
.meta { color: var(--muted); font-size: 0.9rem; }
.meta dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.15rem 0.75rem;
  margin: 0.75rem 0 0;
}
.meta dt { font-weight: 600; }
.meta dd { margin: 0; word-break: break-all; }
.cards { display: grid; gap: 0.75rem; grid-template-columns: 1fr; }
.card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem 1rem;
  background: var(--surface);
}
.card .label { color: var(--muted); font-size: 0.8rem; text-transform: uppercase; }
.card .value { font-size: 1.6rem; font-variant-numeric: tabular-nums; }
.controls { margin: 0.5rem 0; }
.controls input {
  width: 100%;
  padding: 0.5rem 0.6rem;
  font: inherit;
  border: 1px solid var(--border);
  border-radius: 6px;
}
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
caption { text-align: left; color: var(--muted); font-size: 0.85rem; padding: 0.25rem 0; }
th, td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
th[aria-sort] { cursor: pointer; user-select: none; }
th[aria-sort]::after { content: " ↕"; color: var(--muted); }
th[aria-sort="ascending"]::after { content: " ↑"; color: var(--accent); }
th[aria-sort="descending"]::after { content: " ↓"; color: var(--accent); }
.status { font-weight: 600; }
.status-match { color: var(--match); }
.status-mismatch { color: var(--mismatch); }
.status-missing { color: var(--missing); }
.status-extra { color: var(--extra); }
.ok-true { color: var(--match); font-weight: 600; }
.ok-false { color: var(--mismatch); font-weight: 600; }
.diff-list { margin: 0; padding-left: 1.1rem; }
.diff-list li { margin: 0.1rem 0; }
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
@media (min-width: 768px) {
  body { padding: 1.5rem 2rem; }
  .cards { grid-template-columns: repeat(3, 1fr); }
}
@media (min-width: 1440px) {
  body { max-width: 1200px; margin: 0 auto; padding: 2rem 3rem; }
}
"""

_JS = """\
(function () {
  function cellText(row, i) {
    return (row.children[i].dataset.sort ?? row.children[i].textContent).trim();
  }
  function compare(a, b) {
    var na = parseFloat(a), nb = parseFloat(b);
    if (!isNaN(na) && !isNaN(nb)) { return na - nb; }
    return a.localeCompare(b);
  }
  document.querySelectorAll("table.sortable th[aria-sort]").forEach(function (th) {
    th.addEventListener("click", function () {
      var table = th.closest("table");
      var headers = Array.prototype.slice.call(th.parentNode.children);
      var idx = headers.indexOf(th);
      var asc = th.getAttribute("aria-sort") !== "ascending";
      headers.forEach(function (h) { h.setAttribute("aria-sort", "none"); });
      th.setAttribute("aria-sort", asc ? "ascending" : "descending");
      var body = table.tBodies[0];
      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (r1, r2) {
        var c = compare(cellText(r1, idx), cellText(r2, idx));
        return asc ? c : -c;
      });
      rows.forEach(function (r) { body.appendChild(r); });
    });
  });
  var filter = document.getElementById("case-filter");
  if (filter) {
    var rows = document.querySelectorAll("#per-case-table tbody tr");
    filter.addEventListener("input", function () {
      var q = filter.value.toLowerCase();
      rows.forEach(function (r) {
        r.style.display = r.textContent.toLowerCase().indexOf(q) === -1 ? "none" : "";
      });
    });
  }
})();
"""


def _render_meta(report: dict[str, Any]) -> str:
    rows = [
        ("Spec", report.get("spec")),
        ("Spec hash", report.get("spec_hash")),
        ("Backend", report.get("backend")),
        ("Model", report.get("model")),
        ("Timestamp", report.get("timestamp")),
        ("Fixture", report.get("fixture_path")),
    ]
    items = "\n".join(
        f"      <dt>{_esc(label)}</dt>\n      <dd>{_esc(value)}</dd>" for label, value in rows
    )
    return f'  <div class="meta">\n    <dl>\n{items}\n    </dl>\n  </div>'


def _render_aggregate(report: dict[str, Any]) -> str:
    agg = report.get("aggregate") or {}
    fuzzy = agg.get("fuzzy_f1")
    cards = [
        ("Precision", _fmt(float(agg.get("precision", 0.0)))),
        ("Recall", _fmt(float(agg.get("recall", 0.0)))),
        ("F1", _fmt(float(agg.get("f1", 0.0)))),
        ("Exact F1", _fmt(float(agg.get("exact_f1", agg.get("f1", 0.0))))),
        ("Fuzzy F1", _esc(fuzzy) if fuzzy is None else _fmt(float(fuzzy))),
    ]
    body = "\n".join(
        f'      <div class="card">\n'
        f'        <div class="label">{_esc(label)}</div>\n'
        f'        <div class="value">{value}</div>\n'
        f"      </div>"
        for label, value in cards
    )
    return (
        '  <section aria-labelledby="aggregate-heading">\n'
        '    <h2 id="aggregate-heading">Aggregate</h2>\n'
        f'    <div class="cards">\n{body}\n    </div>\n'
        "  </section>"
    )


def _render_per_field(report: dict[str, Any]) -> str:
    per_field = report.get("per_field") or {}
    if not per_field:
        rows = '      <tr><td colspan="4">No fields scored.</td></tr>'
    else:
        rows = "\n".join(
            f"      <tr>\n"
            f"        <td>{_esc(field)}</td>\n"
            f'        <td data-sort="{_fmt(float(score.get("p", 0.0)))}">'
            f"{_fmt(float(score.get('p', 0.0)))}</td>\n"
            f'        <td data-sort="{_fmt(float(score.get("r", 0.0)))}">'
            f"{_fmt(float(score.get('r', 0.0)))}</td>\n"
            f'        <td data-sort="{_fmt(float(score.get("f1", 0.0)))}">'
            f"{_fmt(float(score.get('f1', 0.0)))}</td>\n"
            f"      </tr>"
            for field, score in per_field.items()
        )
    return (
        '  <section aria-labelledby="per-field-heading">\n'
        '    <h2 id="per-field-heading">Per-field scores</h2>\n'
        '    <div class="table-wrap">\n'
        '    <table class="sortable">\n'
        "      <caption>Click a column header to sort.</caption>\n"
        "      <thead>\n"
        "        <tr>\n"
        '          <th scope="col" aria-sort="none">Field</th>\n'
        '          <th scope="col" aria-sort="none">P</th>\n'
        '          <th scope="col" aria-sort="none">R</th>\n'
        '          <th scope="col" aria-sort="none">F1</th>\n'
        "        </tr>\n"
        "      </thead>\n"
        f"      <tbody>\n{rows}\n      </tbody>\n"
        "    </table>\n"
        "    </div>\n"
        "  </section>"
    )


def _render_diff_list(diffs: list[dict[str, Any]]) -> str:
    if not diffs:
        return "—"
    items = "\n".join(
        f'          <li><span class="status status-{_esc(d.get("status"))}">'
        f"{_esc(d.get('status'))}</span> "
        f"<code>{_esc(d.get('field'))}</code>: "
        f"expected {_esc(d.get('expected'))}, predicted {_esc(d.get('predicted'))}</li>"
        for d in diffs
    )
    return f'\n        <ul class="diff-list">\n{items}\n        </ul>\n      '


def _render_per_case(report: dict[str, Any]) -> str:
    per_case = report.get("per_case") or []
    if not per_case:
        rows = '      <tr><td colspan="3">No cases.</td></tr>'
    else:
        rendered = []
        for case in per_case:
            ok = bool(case.get("ok"))
            ok_cls = "ok-true" if ok else "ok-false"
            ok_text = "pass" if ok else "fail"
            rendered.append(
                f"      <tr>\n"
                f"        <td>{_esc(case.get('name'))}</td>\n"
                f'        <td data-sort="{"1" if ok else "0"}">'
                f'<span class="{ok_cls}">{ok_text}</span></td>\n'
                f"        <td>{_render_diff_list(case.get('diff') or [])}</td>\n"
                f"      </tr>"
            )
        rows = "\n".join(rendered)
    return (
        '  <section aria-labelledby="per-case-heading">\n'
        '    <h2 id="per-case-heading">Per-case results</h2>\n'
        '    <div class="controls">\n'
        '      <input id="case-filter" type="search" placeholder="Filter cases…"'
        ' aria-label="Filter cases">\n'
        "    </div>\n"
        '    <div class="table-wrap">\n'
        '    <table id="per-case-table" class="sortable">\n'
        "      <caption>Click Name or Status to sort; use the box above to filter.</caption>\n"
        "      <thead>\n"
        "        <tr>\n"
        '          <th scope="col" aria-sort="none">Name</th>\n'
        '          <th scope="col" aria-sort="none">Status</th>\n'
        '          <th scope="col">Diff</th>\n'
        "        </tr>\n"
        "      </thead>\n"
        f"      <tbody>\n{rows}\n      </tbody>\n"
        "    </table>\n"
        "    </div>\n"
        "  </section>"
    )


def _render_usage(report: dict[str, Any]) -> str:
    usage = report.get("usage")
    if usage is None:
        body = "    <p>Usage metrics were not captured for this run.</p>"
    else:
        rows = [
            ("Input tokens", usage.get("input_tokens")),
            ("Output tokens", usage.get("output_tokens")),
            ("Cost estimate (USD)", usage.get("cost_estimate_usd")),
        ]
        items = "\n".join(
            f"      <dt>{_esc(label)}</dt>\n      <dd>{_esc(value)}</dd>" for label, value in rows
        )
        body = f'    <div class="meta">\n      <dl>\n{items}\n      </dl>\n    </div>'
    return (
        '  <section aria-labelledby="usage-heading">\n'
        '    <h2 id="usage-heading">Usage</h2>\n'
        f"{body}\n"
        "  </section>"
    )


def build_html_report(report: dict[str, Any]) -> str:
    """Render a report dict (from ``build_report``) to a self-contained HTML document.

    The output is deterministic and contains no external references, so it can be
    opened directly from disk and pinned by a golden-file snapshot test.
    """
    title = f"Eval report — {_esc(report.get('spec'))}"
    parts = [
        _DOCTYPE,
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        f"  <title>{title}</title>",
        f"  <style>\n{_CSS}  </style>",
        "</head>",
        "<body>",
        "  <header>",
        f"    <h1>{title}</h1>",
        _render_meta(report),
        "  </header>",
        _render_aggregate(report),
        _render_per_field(report),
        _render_per_case(report),
        _render_usage(report),
        f"  <script>\n{_JS}  </script>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts) + "\n"


def write_html_report(report: dict[str, Any], path: str | Path) -> None:
    """Write the rendered HTML report to ``path`` as UTF-8."""
    Path(path).write_text(build_html_report(report), encoding="utf-8")
