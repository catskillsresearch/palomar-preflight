#!/usr/bin/env python3
"""Matrix report of Palomar preflight status for sibling Lean repos."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from palomar_run_report import DEFAULT_OUT, load_report, status_glyph  # noqa: E402

PREFLIGHT_SCRIPT = Path("scripts/palomar_preflight.sh")
README_CANDIDATES = ("README.md", "Readme.md", "readme.md")
CANVAS_FILENAME = "palomar-sibling-matrix.canvas.tsx"

# Column order matches the overview table used in Palomar triage.
MATRIX_PHASES: list[tuple[str, str]] = [
    ("comparator_config", "Config"),
    ("challenge_imports", "Imports"),
    ("challenge_size", "Size"),
    ("lake_manifest", "Lake"),
    ("local_checks", "Local"),
    ("lake_build", "Build"),
    ("type_compare", "Type/closure"),
    ("comparator", "Comparator"),
    ("sorry_scan", "Sorry"),
    ("axioms", "Axioms"),
    ("patch_format", "Patch"),
    ("policy_sync", "Policy"),
    ("editorial_prechecks", "Ed. pre"),
    ("editorial_audit", "Ed. LLM"),
]

BADGE_PATTERNS = (
    re.compile(r"!\[Palomar\]\(https://img\.shields\.io/badge/Palomar", re.I),
    re.compile(r"img\.shields\.io/badge/Palomar-", re.I),
)


def has_palomar_badge(readme_path: Path) -> bool:
    if not readme_path.is_file():
        return False
    text = readme_path.read_text(encoding="utf-8", errors="replace")
    return any(pattern.search(text) for pattern in BADGE_PATTERNS)


def find_readme(repo: Path) -> Path | None:
    for name in README_CANDIDATES:
        path = repo / name
        if path.is_file():
            return path
    return None


def discover_repos(parent: Path) -> list[Path]:
    repos: list[Path] = []
    if not parent.is_dir():
        return repos
    for entry in sorted(parent.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        if not (entry / PREFLIGHT_SCRIPT).is_file():
            continue
        repos.append(entry)
    return repos


def cursor_project_slug(path: Path) -> str:
    return str(path.resolve()).lstrip("/").replace("/", "-").replace("_", "-")


def resolve_cursor_canvas_dir(workspace: Path | None = None) -> Path | None:
    if override := os.environ.get("PALOMAR_CANVAS_DIR"):
        canvas_dir = Path(override).expanduser()
        canvas_dir.mkdir(parents=True, exist_ok=True)
        return canvas_dir

    candidates: list[Path] = []
    for key in ("VSCODE_CWD", "CURSOR_WORKSPACE", "PWD"):
        raw = os.environ.get(key)
        if raw:
            candidates.append(Path(raw))
    if workspace:
        candidates.insert(0, workspace)
    candidates.append(Path.cwd())

    seen: set[str] = set()
    projects_root = Path.home() / ".cursor" / "projects"
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        slug = cursor_project_slug(resolved)
        canvas_dir = projects_root / slug / "canvases"
        if canvas_dir.parent.is_dir():
            canvas_dir.mkdir(parents=True, exist_ok=True)
            return canvas_dir
    return None


def phase_cell(report: dict, phase_id: str) -> str:
    phase = report.get("phases", {}).get(phase_id, {})
    status = phase.get("status", "not_run")
    if status == "skip":
        return "pass"
    if status == "not_run":
        return "—"
    return status_glyph(status)


def overall_cell(report: dict, has_report: bool) -> str:
    if not has_report:
        return "—"
    overall = report.get("overall", {})
    status = overall.get("status")
    if status in {"pass", "fail"}:
        return status
    return "—"


def row_tone(overall: str) -> str:
    if overall == "pass":
        return "success"
    if overall == "fail":
        return "danger"
    return "neutral"


def build_row(repo: Path, report_path: Path) -> dict[str, str]:
    has_report = report_path.is_file()
    report = load_report(report_path) if has_report else {}
    row: dict[str, str] = {"repo": repo.name}
    for phase_id, _label in MATRIX_PHASES:
        row[phase_id] = phase_cell(report, phase_id)
    row["overall"] = overall_cell(report, has_report)
    row["has_report"] = str(has_report)
    if has_report:
        row["report_mtime"] = str(report_path.stat().st_mtime)
        row["finished_at"] = report.get("finished_at") or ""
        row["failed_phase_id"] = (report.get("overall") or {}).get("failed_phase_id") or ""
    return row


def collect_matrix(
    parent: Path, *, include_badged: bool
) -> tuple[list[dict[str, str]], list[str]]:
    candidates = discover_repos(parent)
    rows: list[dict[str, str]] = []
    skipped_badged: list[str] = []

    for repo in candidates:
        readme = find_readme(repo)
        badged = has_palomar_badge(readme) if readme else False
        if badged and not include_badged:
            skipped_badged.append(repo.name)
            continue
        report_path = repo / DEFAULT_OUT
        rows.append(build_row(repo, report_path))

    return rows, skipped_badged


def html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Short column titles for the plan panel (full names in title= tooltips).
PLAN_COLUMN_LABELS: dict[str, str] = {
    "repo": "Repo",
    "comparator_config": "Conf",
    "challenge_imports": "Imp",
    "challenge_size": "Sz",
    "lake_manifest": "Lake",
    "local_checks": "Loc",
    "lake_build": "Bld",
    "type_compare": "Types",
    "comparator": "Cmp",
    "sorry_scan": "Sry",
    "axioms": "Ax",
    "patch_format": "Ptch",
    "policy_sync": "Pol",
    "editorial_prechecks": "EPre",
    "editorial_audit": "LLM",
    "overall": "All",
}

PLAN_COLUMN_TITLES: dict[str, str] = {
    "repo": "Repository",
    "comparator_config": "Validate Comparator configuration",
    "challenge_imports": "Challenge import discipline",
    "challenge_size": "Challenge surface size limits",
    "lake_manifest": "Lake manifest",
    "local_checks": "Project-specific mechanical checks",
    "lake_build": "Build Lean project",
    "type_compare": "Compare types and declaration-closure values",
    "comparator": "Palomar-pinned Comparator",
    "sorry_scan": "Reject proof holes in Solution",
    "axioms": "Permitted theorem axioms",
    "patch_format": "Patch formatting",
    "policy_sync": "Sync PalomarPolicy",
    "editorial_prechecks": "Editorial pre-checks",
    "editorial_audit": "Editorial LLM audit",
    "overall": "Overall preflight outcome",
}


def format_plan_body(
    rows: list[dict[str, str]],
    *,
    include_badged: bool,
    parent: Path,
    skipped_badged: list[str],
) -> str:
    phase_ids = [phase_id for phase_id, _ in MATRIX_PHASES]
    lines: list[str] = [
        "Legend: pass · fail · — (not reached / no report)",
        "",
    ]

    for row in rows:
        overall = row["overall"]
        if overall == "pass":
            overall_md = "**pass**"
        elif overall == "fail":
            overall_md = "**fail**"
        else:
            overall_md = "—"
        lines.append(f"### {row['repo']} · {overall_md}")
        tokens: list[str] = []
        for phase_id in phase_ids:
            label = PLAN_COLUMN_LABELS[phase_id]
            value = row[phase_id]
            if value == "pass":
                tokens.append(f"{label} **pass**")
            elif value == "fail":
                tokens.append(f"{label} **fail**")
            else:
                tokens.append(f"{label} —")
        lines.append("")
        lines.append(" · ".join(tokens))
        lines.append("")

    missing = [row["repo"] for row in rows if row["has_report"] == "False"]
    lines.append("---")
    lines.append("")
    lines.append(
        f"Scanned `{parent}`: {len(rows)} repo(s) with `{PREFLIGHT_SCRIPT.as_posix()}`"
        + (" (excluding Palomar badge READMEs)" if not include_badged else " (all)")
        + f"; report source `{DEFAULT_OUT.as_posix()}`."
    )
    if missing:
        lines.append("")
        lines.append("No saved preflight run report yet:")
        for name in missing:
            lines.append(f"- `{name}` — run `bash scripts/palomar_preflight.sh --mechanical-only`")
    if skipped_badged:
        lines.append("")
        lines.append("Skipped (Palomar badge in README): " + ", ".join(skipped_badged))
    return "\n".join(lines) + "\n"


def format_markdown(
    rows: list[dict[str, str]],
    *,
    include_badged: bool,
    parent: Path,
    skipped_badged: list[str],
) -> str:
    lines: list[str] = []
    headers = ["Repo"] + [label for _, label in MATRIX_PHASES] + ["Overall"]
    lines.append("Legend: pass · fail · — (not reached / no report)")
    lines.append("")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        cells = [row["repo"]] + [row[phase_id] for phase_id, _ in MATRIX_PHASES] + [row["overall"]]
        lines.append("| " + " | ".join(cells) + " |")

    missing = [row["repo"] for row in rows if row["has_report"] == "False"]
    lines.append("")
    lines.append(
        f"Scanned `{parent}`: {len(rows)} repo(s) with `{PREFLIGHT_SCRIPT.as_posix()}`"
        + (" (excluding Palomar badge READMEs)" if not include_badged else " (all)")
        + f"; report source `{DEFAULT_OUT.as_posix()}`."
    )
    if missing:
        lines.append("")
        lines.append("No saved preflight run report yet:")
        for name in missing:
            lines.append(f"- `{name}` — run `bash scripts/palomar_preflight.sh --mechanical-only`")
    if skipped_badged:
        lines.append("")
        lines.append("Skipped (Palomar badge in README): " + ", ".join(skipped_badged))
    return "\n".join(lines) + "\n"


def render_html_table(
    rows: list[dict[str, str]],
    *,
    parent: Path,
    include_badged: bool,
    skipped_badged: list[str],
) -> str:
    headers = ["Repo"] + [label for _, label in MATRIX_PHASES] + ["Overall"]
    phase_ids = [phase_id for phase_id, _ in MATRIX_PHASES]
    missing = [row["repo"] for row in rows if row["has_report"] == "False"]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_source = DEFAULT_OUT.as_posix()

    row_html: list[str] = []
    for row in rows:
        tone = row_tone(row["overall"])
        cls = "ok" if tone == "success" else "bad" if tone == "danger" else "na"
        cells = [row["repo"]] + [row[phase_id] for phase_id in phase_ids] + [row["overall"]]
        rendered_cells: list[str] = []
        for idx, cell in enumerate(cells):
            tag = "th" if idx == 0 else "td"
            scope = ' scope="row"' if idx == 0 else ""
            rendered_cells.append(
                f"<{tag}{scope}>{html_escape(cell)}</{tag}>"
            )
        row_html.append(f'<tr class="{cls}">{"".join(rendered_cells)}</tr>')

    missing_html = ""
    if missing:
        items = "".join(f"<li><code>{html_escape(name)}</code></li>" for name in missing)
        missing_html = (
            "<section class=\"callout\"><h2>Missing saved reports</h2>"
            "<p>Run <code>bash scripts/palomar_preflight.sh --mechanical-only</code> in each repo, then refresh.</p>"
            f"<ul>{items}</ul></section>"
        )

    skipped_html = ""
    if skipped_badged:
        skipped = ", ".join(html_escape(name) for name in skipped_badged)
        skipped_html = f"<p class=\"muted\">Skipped (Palomar badge in README): {skipped}</p>"

    header_cells = "".join(f"<th>{html_escape(col)}</th>" for col in headers)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Palomar sibling status</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f1116;
      --fg: #f5f7ff;
      --muted: #98a2b3;
      --grid: #2b3345;
      --ok: #133a24;
      --bad: #472023;
      --na: #1d2330;
      --head: #1a2030;
      --repo: #181d2b;
      --callout: #172033;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font: 14px/1.45 Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    }}
    .wrap {{
      max-width: min(98vw, 2200px);
      margin: 20px auto;
      padding: 0 14px 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    .meta {{
      color: var(--muted);
      margin: 0 0 14px;
    }}
    .table-shell {{
      border: 1px solid var(--grid);
      border-radius: 10px;
      overflow: auto;
      background: #111522;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
    }}
    table {{
      border-collapse: separate;
      border-spacing: 0;
      min-width: 1400px;
      width: 100%;
    }}
    th, td {{
      border-bottom: 1px solid var(--grid);
      border-right: 1px solid var(--grid);
      padding: 8px 10px;
      text-align: center;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }}
    thead th {{
      position: sticky;
      top: 0;
      background: var(--head);
      z-index: 2;
      font-weight: 650;
    }}
    tbody th {{
      position: sticky;
      left: 0;
      z-index: 1;
      text-align: left;
      background: var(--repo);
      min-width: 180px;
    }}
    tbody tr.ok td, tbody tr.ok th {{ background: color-mix(in oklab, var(--ok) 82%, transparent); }}
    tbody tr.bad td, tbody tr.bad th {{ background: color-mix(in oklab, var(--bad) 85%, transparent); }}
    tbody tr.na td, tbody tr.na th {{ background: color-mix(in oklab, var(--na) 82%, transparent); }}
    .callout {{
      margin-top: 16px;
      border: 1px solid #3a4c77;
      background: var(--callout);
      border-radius: 10px;
      padding: 12px 14px;
    }}
    .callout h2 {{
      margin: 0 0 8px;
      font-size: 16px;
    }}
    .callout p {{
      margin: 0 0 8px;
    }}
    .muted {{
      color: var(--muted);
      margin-top: 12px;
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Palomar sibling status</h1>
    <p class="meta">
      Legend: pass · fail · — (not reached / no report) &nbsp;|&nbsp;
      Source: <code>{html_escape(report_source)}</code><br />
      Scanned <code>{html_escape(str(parent))}</code> ({len(rows)} repo(s){"" if include_badged else ", excluding Palomar badge READMEs"}) · Generated {html_escape(generated_at)}
    </p>
    <div class="table-shell">
      <table>
        <thead><tr>{header_cells}</tr></thead>
        <tbody>{"".join(row_html)}</tbody>
      </table>
    </div>
    {missing_html}
    {skipped_html}
  </div>
</body>
</html>
"""
    return html


def resolve_cursor_plans_dir(workspace: Path | None = None) -> Path | None:
    if override := os.environ.get("PALOMAR_PLANS_DIR"):
        plans_dir = Path(override).expanduser()
        plans_dir.mkdir(parents=True, exist_ok=True)
        return plans_dir

    candidates: list[Path] = []
    for key in ("VSCODE_CWD", "CURSOR_WORKSPACE", "PWD"):
        raw = os.environ.get(key)
        if raw:
            candidates.append(Path(raw))
    if workspace:
        candidates.insert(0, workspace)
    candidates.append(Path.cwd())

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        plans_dir = resolved / ".cursor" / "plans"
        if (resolved / ".cursor").is_dir() or (resolved / ".git").is_dir() or True:
            plans_dir.mkdir(parents=True, exist_ok=True)
            return plans_dir
    return None


def write_plan(
    path: Path,
    rows: list[dict[str, str]],
    *,
    parent: Path,
    skipped_badged: list[str],
    include_badged: bool,
) -> None:
    body = format_plan_body(
        rows,
        include_badged=include_badged,
        parent=parent,
        skipped_badged=skipped_badged,
    ).rstrip()
    missing = [row["repo"] for row in rows if row["has_report"] == "False"]
    overview = (
        f"Preflight matrix for {len(rows)} repo(s) under {parent}. "
        + (f"Missing reports: {', '.join(missing)}." if missing else "All listed repos have saved reports.")
    )
    source = f"""---
name: Palomar sibling status
overview: {json.dumps(overview)[1:-1]}
todos: []
isProject: false
---

{body}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def write_canvas(
    path: Path,
    rows: list[dict[str, str]],
    *,
    parent: Path,
    skipped_badged: list[str],
) -> None:
    headers = ["Repo"] + [label for _, label in MATRIX_PHASES] + ["Overall"]
    table_rows = [
        [row["repo"]] + [row[phase_id] for phase_id, _ in MATRIX_PHASES] + [row["overall"]]
        for row in rows
    ]
    tones = [row_tone(row["overall"]) for row in rows]
    missing = [row["repo"] for row in rows if row["has_report"] == "False"]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    missing_block = ""
    if missing:
        items = "\n".join(f"          <Text as=\"span\" size=\"small\">• {name}</Text>" for name in missing)
        missing_block = f"""
      <Callout tone="info" title="Missing saved reports">
        <Stack gap={{4}}>
{items}
          <Text size="small" tone="secondary">
            Run bash scripts/palomar_preflight.sh --mechanical-only in each repo, then refresh this panel.
          </Text>
        </Stack>
      </Callout>"""

    skipped_block = ""
    if skipped_badged:
        skipped_block = f"""
      <Text tone="secondary" size="small">
        Skipped (Palomar badge in README): {", ".join(skipped_badged)}
      </Text>"""

    source = f"""
import {{ Callout, H1, Stack, Table, Text, type TableRowTone }} from "cursor/canvas";

const headers = {json.dumps(headers)};
const rows = {json.dumps(table_rows)};
const rowTone: Array<TableRowTone | undefined> = {json.dumps(tones)};

export default function PalomarSiblingMatrix() {{
  return (
    <Stack gap={{16}} style={{{{ padding: 24, maxWidth: "100%" }}}}>
      <Stack gap={{6}}>
        <H1>Palomar sibling status</H1>
        <Text tone="secondary">
          Legend: pass · fail · — (not reached / no report). Source: {DEFAULT_OUT.as_posix()}.
        </Text>
        <Text tone="tertiary" size="small">
          Generated {generated_at} · scanned {json.dumps(str(parent))}
        </Text>
      </Stack>
      <Table
        headers={{headers}}
        rows={{rows}}
        rowTone={{rowTone}}
        striped
        stickyHeader
      />{missing_block}{skipped_block}
    </Stack>
  );
}}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source.strip() + "\n", encoding="utf-8")


def write_html(
    path: Path,
    rows: list[dict[str, str]],
    *,
    parent: Path,
    skipped_badged: list[str],
    include_badged: bool,
) -> None:
    source = render_html_table(
        rows,
        parent=parent,
        include_badged=include_badged,
        skipped_badged=skipped_badged,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report Palomar preflight phase status for sibling repos that have "
            "scripts/palomar_preflight.sh and no Palomar registry badge in README."
        )
    )
    default_parent = SCRIPT_DIR.parent
    parser.add_argument(
        "--parent-dir",
        type=Path,
        default=default_parent,
        help=f"Directory containing Lean repos (default: {default_parent})",
    )
    parser.add_argument(
        "--include-badged",
        action="store_true",
        help="Also include repos whose README already has a Palomar registry badge",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--write-plan",
        type=Path,
        metavar="PATH",
        help="Write a Cursor plan report (.plan.md) to PATH",
    )
    parser.add_argument(
        "--write-canvas",
        type=Path,
        metavar="PATH",
        help="Write a Cursor Canvas (.canvas.tsx) to PATH",
    )
    parser.add_argument(
        "--write-html",
        type=Path,
        metavar="PATH",
        help="Write a standalone HTML table report to PATH",
    )
    args = parser.parse_args()
    parent = args.parent_dir.resolve()

    rows, skipped_badged = collect_matrix(parent, include_badged=args.include_badged)

    if not rows and not skipped_badged:
        print(f"No repos with {PREFLIGHT_SCRIPT.as_posix()} under {parent}", file=sys.stderr)
        return 1

    if args.write_plan:
        write_plan(
            args.write_plan,
            rows,
            parent=parent,
            skipped_badged=skipped_badged,
            include_badged=args.include_badged,
        )
        print(str(args.write_plan))
        return 0

    if args.write_canvas:
        write_canvas(args.write_canvas, rows, parent=parent, skipped_badged=skipped_badged)
        print(str(args.write_canvas))
        return 0

    if args.write_html:
        write_html(
            args.write_html,
            rows,
            parent=parent,
            skipped_badged=skipped_badged,
            include_badged=args.include_badged,
        )
        print(str(args.write_html))
        return 0

    if args.format == "json":
        payload = {
            "parent_dir": str(parent),
            "include_badged": args.include_badged,
            "report_path": DEFAULT_OUT.as_posix(),
            "legend": {"pass": "pass", "fail": "fail", "—": "not_run_or_skip_or_missing_report"},
            "columns": [{"id": pid, "label": label} for pid, label in MATRIX_PHASES] + [
                {"id": "overall", "label": "Overall"}
            ],
            "repos": rows,
            "skipped_badged": skipped_badged,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(
            format_markdown(
                rows,
                include_badged=args.include_badged,
                parent=parent,
                skipped_badged=skipped_badged,
            ),
            end="",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
