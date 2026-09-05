#!/usr/bin/env python3
"""Read/write Palomar preflight run reports (phase outcomes + observations)."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "palomar-preflight-run-v1"
DEFAULT_OUT = Path(".cache/palomar-editorial/preflight-run.json")
OUTPUT_TAIL_LINES = 120
OUTPUT_TAIL_BYTES = 64_000

PHASE_TITLES: dict[str, str] = {
    "comparator_config": "Validate Comparator configuration",
    "challenge_imports": "Challenge import discipline (Mathlib only)",
    "challenge_size": "Challenge surface size limits",
    "lake_manifest": "Exactly one Lake manifest at repository root",
    "no_submodules": "Reject git submodules (Palomar cannot preserve them)",
    "local_checks": "Project-specific mechanical checks",
    "lake_build": "Build Lean project",
    "type_compare": "Compare Challenge/Solution types and declaration-closure values",
    "comparator": "Run Palomar-pinned Comparator",
    "sorry_scan": "Reject proof holes in Solution sources",
    "axioms": "Check permitted theorem axioms",
    "patch_format": "Check patch formatting",
    "policy_sync": "Sync PalomarPolicy to upstream latest",
    "editorial_prechecks": "Palomar editorial pre-checks",
    "mechanical_report": "Build local mechanical report",
    "editorial_audit": "Palomar editorial audit (LLM, gpt-5.6-sol + composer-2.5)",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_head(cwd: Path) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def git_remote_url(cwd: Path) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def load_report(path: Path) -> dict[str, Any]:
    if path.is_file():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def empty_phases() -> dict[str, dict[str, Any]]:
    phases: dict[str, dict[str, Any]] = {}
    for phase_id, title in PHASE_TITLES.items():
        phases[phase_id] = {
            "id": phase_id,
            "title": title,
            "status": "not_run",
        }
    return phases


def parse_status(exit_code: int, output: str) -> str:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if any(ln.startswith("FAIL:") for ln in lines):
        return "fail"
    if exit_code != 0:
        return "fail"
    if any(ln.startswith("OK:") for ln in lines):
        return "pass"
    if "Build completed successfully" in output:
        return "pass"
    if exit_code == 0:
        return "pass"
    return "fail"


def summarize_output(output: str) -> str | None:
    for ln in reversed(output.splitlines()):
        stripped = ln.strip()
        if stripped.startswith("OK:") or stripped.startswith("FAIL:"):
            return stripped[:500]
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        return None
    return lines[-1][:500]


def output_tail(output: str) -> list[str]:
    text = output
    if len(text.encode("utf-8")) > OUTPUT_TAIL_BYTES:
        text = text.encode("utf-8")[-OUTPUT_TAIL_BYTES:].decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-OUTPUT_TAIL_LINES:]


def cmd_init(args: argparse.Namespace) -> int:
    options = json.loads(args.options)
    project_root = Path(args.project_root).resolve()
    toolkit_root = Path(args.toolkit_root).resolve()
    out = Path(args.out)

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "started_at": utc_now(),
        "finished_at": None,
        "duration_seconds": None,
        "repository": {
            "root": str(project_root),
            "commit": git_head(project_root),
            "origin": git_remote_url(project_root),
        },
        "toolkit": {
            "root": str(toolkit_root),
            "commit": git_head(toolkit_root),
        },
        "report_path": str(out),
        "options": options,
        "overall": {
            "status": "running",
            "exit_code": None,
            "failed_phase_id": None,
            "message": None,
        },
        "phases": empty_phases(),
        "observations": {},
        "artifacts": {},
    }
    save_report(out, report)
    return 0


def cmd_phase_start(args: argparse.Namespace) -> int:
    out = Path(args.out)
    report = load_report(out)
    phase_id = args.id
    if phase_id not in report.get("phases", {}):
        report.setdefault("phases", {})[phase_id] = {
            "id": phase_id,
            "title": args.title or PHASE_TITLES.get(phase_id, phase_id),
            "status": "not_run",
        }
    phase = report["phases"][phase_id]
    phase["title"] = args.title or phase.get("title") or PHASE_TITLES.get(phase_id, phase_id)
    phase["status"] = "running"
    phase["started_at"] = utc_now()
    phase.pop("finished_at", None)
    phase.pop("duration_seconds", None)
    save_report(out, report)
    return 0


def cmd_phase_end(args: argparse.Namespace) -> int:
    out = Path(args.out)
    report = load_report(out)
    phase_id = args.id
    output = ""
    if args.output_file:
        output = Path(args.output_file).read_text(encoding="utf-8", errors="replace")
    exit_code = int(args.exit_code)
    status = args.status or parse_status(exit_code, output)
    started = report["phases"].get(phase_id, {}).get("started_at")
    finished = utc_now()
    duration = None
    if started:
        try:
            start_dt = datetime.fromisoformat(started)
            end_dt = datetime.fromisoformat(finished)
            duration = round((end_dt - start_dt).total_seconds(), 3)
        except ValueError:
            duration = None

    phase = report["phases"].setdefault(
        phase_id,
        {"id": phase_id, "title": PHASE_TITLES.get(phase_id, phase_id)},
    )
    phase.update(
        {
            "status": status,
            "exit_code": exit_code,
            "finished_at": finished,
            "duration_seconds": duration,
            "summary": summarize_output(output),
            "output_tail": output_tail(output),
        }
    )
    if args.note:
        phase["note"] = args.note
    save_report(out, report)
    return 0


def cmd_phase_skip(args: argparse.Namespace) -> int:
    out = Path(args.out)
    report = load_report(out)
    phase_id = args.id
    phase = report["phases"].setdefault(
        phase_id,
        {"id": phase_id, "title": PHASE_TITLES.get(phase_id, phase_id)},
    )
    phase.update(
        {
            "status": "skip",
            "finished_at": utc_now(),
            "summary": args.reason,
        }
    )
    save_report(out, report)
    return 0


def cmd_observe(args: argparse.Namespace) -> int:
    out = Path(args.out)
    report = load_report(out)
    value = json.loads(args.value)
    report.setdefault("observations", {})[args.key] = value
    save_report(out, report)
    return 0


def cmd_artifact(args: argparse.Namespace) -> int:
    out = Path(args.out)
    report = load_report(out)
    report.setdefault("artifacts", {})[args.key] = args.path
    save_report(out, report)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    out = Path(args.out)
    report = load_report(out)
    if not report:
        return 0

    finished = utc_now()
    report["finished_at"] = finished
    started = report.get("started_at")
    if started:
        try:
            start_dt = datetime.fromisoformat(started)
            end_dt = datetime.fromisoformat(finished)
            report["duration_seconds"] = round((end_dt - start_dt).total_seconds(), 3)
        except ValueError:
            pass

    exit_code = int(args.exit_code)
    failed_phase = args.failed_phase or None
    message = args.message or None

    if exit_code == 0 and not failed_phase:
        overall_status = "pass"
    else:
        overall_status = "fail"
        if not failed_phase:
            for phase_id, phase in report.get("phases", {}).items():
                if phase.get("status") == "fail":
                    failed_phase = phase_id
                    break

    report["overall"] = {
        "status": overall_status,
        "exit_code": exit_code,
        "failed_phase_id": failed_phase,
        "message": message,
    }

    # Attach editorial synthesis when review draft exists.
    project_root = Path(report.get("repository", {}).get("root", "."))
    observations = report.setdefault("observations", {})

    comparator_path = project_root / "comparator.json"
    if comparator_path.is_file():
        try:
            observations["comparator"] = json.loads(
                comparator_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            pass

    policy_pin = project_root / "vendor/PALOMAR_POLICY_PIN"
    if policy_pin.is_file():
        try:
            observations["policy_pin"] = policy_pin.read_text(encoding="utf-8").strip()
        except OSError:
            pass

    review_path = project_root / ".cache/palomar-editorial/review-draft.json"
    if review_path.is_file():
        try:
            draft = json.loads(review_path.read_text(encoding="utf-8"))
            synthesis = draft.get("synthesis", {})
            observations["editorial"] = {
                "synthesis_outcome": synthesis.get("outcome"),
                "synthesis_summary": synthesis.get("summary"),
                "review_draft_path": str(review_path.relative_to(project_root)),
            }
            report.setdefault("artifacts", {})["review_draft"] = str(
                review_path.relative_to(project_root)
            )
        except (OSError, json.JSONDecodeError):
            pass

    comparator_log = project_root / ".cache/palomar-comparator/last-run.log"
    if comparator_log.is_file():
        report.setdefault("artifacts", {})["comparator_last_run_log"] = str(
            comparator_log.relative_to(project_root)
        )

    save_report(out, report)
    if args.print_path:
        print(f"OK: preflight run report written to {out}")
    return 0


def status_glyph(status: str) -> str:
    return {
        "pass": "pass",
        "fail": "fail",
        "skip": "skip",
        "not_run": "—",
        "running": "…",
    }.get(status, status)


def cmd_print_table(args: argparse.Namespace) -> int:
    path = Path(args.report)
    report = load_report(path)
    if not report:
        print(f"error: no report at {path}", file=sys.stderr)
        return 1

    phase_ids = list(PHASE_TITLES.keys())
    if args.format == "markdown":
        header = "| Phase | Status | Summary |"
        sep = "| --- | --- | --- |"
        rows = [header, sep]
        for phase_id in phase_ids:
            phase = report.get("phases", {}).get(phase_id, {})
            title = phase.get("title", phase_id)
            status = status_glyph(phase.get("status", "not_run"))
            summary = (phase.get("summary") or "").replace("|", "\\|")
            rows.append(f"| {title} | {status} | {summary} |")
        overall = report.get("overall", {})
        rows.append("")
        rows.append(
            f"**Overall:** {overall.get('status', '?')} "
            f"(exit {overall.get('exit_code')}, "
            f"failed phase: {overall.get('failed_phase_id') or '—'})"
        )
        print("\n".join(rows))
    else:
        data = {
            "overall": report.get("overall"),
            "phases": {
                pid: report.get("phases", {}).get(pid, {}).get("status", "not_run")
                for pid in phase_ids
            },
        }
        print(json.dumps(data, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Palomar preflight run report")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--project-root", required=True)
    p_init.add_argument("--toolkit-root", required=True)
    p_init.add_argument("--options", default="{}")
    p_init.set_defaults(func=cmd_init)

    p_start = sub.add_parser("phase-start")
    p_start.add_argument("--id", required=True)
    p_start.add_argument("--title", default="")
    p_start.set_defaults(func=cmd_phase_start)

    p_end = sub.add_parser("phase-end")
    p_end.add_argument("--id", required=True)
    p_end.add_argument("--exit-code", required=True)
    p_end.add_argument("--status", choices=["pass", "fail", "skip"], default="")
    p_end.add_argument("--output-file", default="")
    p_end.add_argument("--note", default="")
    p_end.set_defaults(func=cmd_phase_end)

    p_skip = sub.add_parser("phase-skip")
    p_skip.add_argument("--id", required=True)
    p_skip.add_argument("--reason", default="")
    p_skip.set_defaults(func=cmd_phase_skip)

    p_obs = sub.add_parser("observe")
    p_obs.add_argument("--key", required=True)
    p_obs.add_argument("--value", required=True)
    p_obs.set_defaults(func=cmd_observe)

    p_art = sub.add_parser("artifact")
    p_art.add_argument("--key", required=True)
    p_art.add_argument("--path", required=True)
    p_art.set_defaults(func=cmd_artifact)

    p_fin = sub.add_parser("finalize")
    p_fin.add_argument("--exit-code", required=True)
    p_fin.add_argument("--failed-phase", default="")
    p_fin.add_argument("--message", default="")
    p_fin.add_argument("--print-path", action="store_true")
    p_fin.set_defaults(func=cmd_finalize)

    p_tbl = sub.add_parser("print-table")
    p_tbl.add_argument("--report", type=Path, default=DEFAULT_OUT)
    p_tbl.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_tbl.set_defaults(func=cmd_print_table)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
