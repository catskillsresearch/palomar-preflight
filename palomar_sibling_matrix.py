#!/usr/bin/env python3
"""Matrix report of Palomar preflight status for sibling Lean repos."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from palomar_run_report import DEFAULT_OUT, load_report, status_glyph  # noqa: E402

PREFLIGHT_SCRIPT = Path("scripts/palomar_preflight.sh")
README_CANDIDATES = ("README.md", "Readme.md", "readme.md")

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


def phase_cell(report: dict, phase_id: str) -> str:
    phase = report.get("phases", {}).get(phase_id, {})
    status = phase.get("status", "not_run")
    if status in {"not_run", "skip"}:
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


def build_row(repo: Path, report_path: Path) -> dict[str, str]:
    has_report = report_path.is_file()
    report = load_report(report_path) if has_report else {}
    row: dict[str, str] = {"repo": repo.name}
    for phase_id, _label in MATRIX_PHASES:
        row[phase_id] = phase_cell(report, phase_id)
    row["overall"] = overall_cell(report, has_report)
    row["has_report"] = str(has_report)
    if has_report:
        row["report_mtime"] = report_path.stat().st_mtime
        row["finished_at"] = report.get("finished_at") or ""
        row["failed_phase_id"] = (report.get("overall") or {}).get("failed_phase_id") or ""
    return row


def print_markdown(rows: list[dict[str, str]], *, include_badged: bool, parent: Path) -> None:
    headers = ["Repo"] + [label for _, label in MATRIX_PHASES] + ["Overall"]
    print("Legend: pass · fail · — (not reached / no report)")
    print()
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        cells = [row["repo"]] + [row[phase_id] for phase_id, _ in MATRIX_PHASES] + [row["overall"]]
        print("| " + " | ".join(cells) + " |")

    missing = [row["repo"] for row in rows if row["has_report"] == "False"]
    print()
    print(
        f"Scanned `{parent}`: {len(rows)} repo(s) with `{PREFLIGHT_SCRIPT.as_posix()}`"
        + (" (excluding Palomar badge READMEs)" if not include_badged else " (all)")
        + f"; report source `{DEFAULT_OUT.as_posix()}`."
    )
    if missing:
        print()
        print("No saved preflight run report yet:")
        for name in missing:
            print(f"- `{name}` — run `bash scripts/palomar_preflight.sh --mechanical-only`")


def print_json(rows: list[dict[str, str]], parent: Path, include_badged: bool) -> None:
    payload = {
        "parent_dir": str(parent),
        "include_badged": include_badged,
        "report_path": DEFAULT_OUT.as_posix(),
        "legend": {"pass": "pass", "fail": "fail", "—": "not_run_or_skip_or_missing_report"},
        "columns": [{"id": pid, "label": label} for pid, label in MATRIX_PHASES] + [
            {"id": "overall", "label": "Overall"}
        ],
        "repos": rows,
    }
    print(json.dumps(payload, indent=2))


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
    args = parser.parse_args()
    parent = args.parent_dir.resolve()

    candidates = discover_repos(parent)
    rows: list[dict[str, str]] = []
    skipped_badged: list[str] = []

    for repo in candidates:
        readme = find_readme(repo)
        badged = has_palomar_badge(readme) if readme else False
        if badged and not args.include_badged:
            skipped_badged.append(repo.name)
            continue
        report_path = repo / DEFAULT_OUT
        rows.append(build_row(repo, report_path))

    if not rows and not skipped_badged:
        print(f"No repos with {PREFLIGHT_SCRIPT.as_posix()} under {parent}", file=sys.stderr)
        return 1

    if args.format == "json":
        print_json(rows, parent, args.include_badged)
    else:
        print_markdown(rows, include_badged=args.include_badged, parent=parent)
        if skipped_badged:
            print()
            print("Skipped (Palomar badge in README):", ", ".join(skipped_badged))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
