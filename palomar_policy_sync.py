#!/usr/bin/env python3
"""Sync vendored PalomarPolicy from PalomarRegistry/PalomarPolicy upstream."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

UPSTREAM = "PalomarRegistry/PalomarPolicy"
DEFAULT_BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{UPSTREAM}"

# Editorial audit inputs referenced by rubric.json.
POLICY_PATHS = [
    "rubric.json",
    "CONTRIBUTING.md",
    "prompts/00-classification.md",
    "prompts/01-metadata.md",
    "prompts/02-statement-alignment.md",
    "prompts/03-definition-fidelity.md",
    "prompts/04-literature-notability.md",
    "prompts/05-proof-account.md",
    "prompts/06-synthesis.md",
    "prompts/materiality.md",
    "taxonomies/classification-guide.md",
    "schemas/review.schema.json",
]

# LLM editorial prompts + rubric (git pin can move without changing these).
EDITORIAL_PROMPT_PATHS = [
    "rubric.json",
    "prompts/00-classification.md",
    "prompts/01-metadata.md",
    "prompts/02-statement-alignment.md",
    "prompts/03-definition-fidelity.md",
    "prompts/04-literature-notability.md",
    "prompts/05-proof-account.md",
    "prompts/06-synthesis.md",
    "prompts/materiality.md",
]


def policy_prompts_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for rel in EDITORIAL_PROMPT_PATHS:
        digest.update(rel.encode("utf-8"))
        path = root / rel
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"\0")
    return digest.hexdigest()


def policy_prompts_fingerprint_at_pin(pin: str) -> str:
    digest = hashlib.sha256()
    for rel in EDITORIAL_PROMPT_PATHS:
        digest.update(rel.encode("utf-8"))
        url = f"{RAW_BASE}/{pin}/{rel}"
        digest.update(fetch_text(url).encode("utf-8"))
    return digest.hexdigest()


def prior_policy_prompts_fingerprint(prior_report: dict[str, Any]) -> str | None:
    observations = prior_report.get("observations") or {}
    stored = observations.get("policy_prompts_fingerprint")
    if isinstance(stored, str) and stored:
        return stored
    pin = observations.get("policy_pin")
    if not isinstance(pin, str) or not pin:
        return None
    try:
        return policy_prompts_fingerprint_at_pin(pin.strip())
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def editorial_skip_decision(prior_report_path: Path, policy_root: Path) -> tuple[str, str]:
    """Return ('skip'|'run', human-readable reason)."""
    if not prior_report_path.is_file():
        return "run", "no prior preflight run report"

    try:
        prior_report = json.loads(prior_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "run", "prior preflight run report is unreadable"

    overall = prior_report.get("overall") or {}
    if overall.get("status") != "pass":
        return "run", "prior preflight run did not pass"

    editorial_phase = (prior_report.get("phases") or {}).get("editorial_audit") or {}
    if editorial_phase.get("status") != "pass":
        return "run", "prior run has no successful editorial audit"

    prior_fp = prior_policy_prompts_fingerprint(prior_report)
    if not prior_fp:
        return "run", "prior run has no policy prompt fingerprint"

    if not policy_root.is_dir() or not (policy_root / "rubric.json").is_file():
        return "run", "vendored policy is missing"

    current_fp = policy_prompts_fingerprint(policy_root)
    if current_fp != prior_fp:
        return "run", f"policy prompts changed ({prior_fp[:12]} -> {current_fp[:12]})"

    finished = prior_report.get("finished_at") or "unknown time"
    return "skip", f"policy prompts unchanged since prior pass ({finished})"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8")


def upstream_head_sha() -> str:
    data = fetch_json(f"https://api.github.com/repos/{UPSTREAM}/commits/{DEFAULT_BRANCH}")
    return data["sha"]


def read_pin(pin_path: Path) -> str | None:
    if not pin_path.is_file():
        return None
    pin = pin_path.read_text(encoding="utf-8").strip()
    return pin or None


def download_policy(commit: str, root: Path) -> None:
    for rel in POLICY_PATHS:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{RAW_BASE}/{commit}/{rel}"
        dest.write_text(fetch_text(url), encoding="utf-8")


def write_vendor_readme(root: Path, commit: str) -> None:
    readme = root / "README.md"
    readme.write_text(
        f"""# Vendored PalomarPolicy snapshot

Source: https://github.com/{UPSTREAM}
Commit: `{commit}`

This directory is a plain-file copy of the Palomar editorial contract
(prompts, rubric, CONTRIBUTING, classification guide, review schema).
It is **not** a git submodule.

## Sync

`scripts/palomar_policy_sync.py` checks upstream `{DEFAULT_BRANCH}` before
editorial preflight and refreshes this tree when a newer commit exists.

## Revert a bad upstream pull

Before committing audit results:

```bash
git checkout -- vendor/palomar-policy vendor/PALOMAR_POLICY_PIN
```

Use `scripts/palomar_preflight.sh --no-policy-sync` to audit against the
currently committed snapshot without contacting upstream.
""",
        encoding="utf-8",
    )


def diff_summary(old_root: Path | None, new_root: Path, old_pin: str | None, new_pin: str) -> list[str]:
    lines: list[str] = []
    if old_pin and old_pin != new_pin:
        lines.append(f"policy pin: {old_pin} -> {new_pin}")
    if old_root is None or not old_root.is_dir():
        lines.append(f"installed {len(POLICY_PATHS)} policy files at {new_root}")
        return lines
    changed: list[str] = []
    for rel in POLICY_PATHS:
        old_path = old_root / rel
        new_path = new_root / rel
        old_text = old_path.read_text(encoding="utf-8") if old_path.is_file() else None
        new_text = new_path.read_text(encoding="utf-8") if new_path.is_file() else None
        if old_text != new_text:
            changed.append(rel)
    if changed:
        lines.append("changed files:")
        lines.extend(f"  {path}" for path in changed)
    return lines


def cmd_editorial_skip_check(args: argparse.Namespace) -> int:
    action, reason = editorial_skip_decision(args.prior_report, args.root)
    current_fp = policy_prompts_fingerprint(args.root)
    if action == "skip":
        print(f"OK: skip editorial — {reason}")
        print(f"policy_prompts_fingerprint={current_fp}")
        return 0
    print(f"OK: run editorial — {reason}")
    print(f"policy_prompts_fingerprint={current_fp}")
    return 2


def cmd_sync(args: argparse.Namespace) -> int:
    root: Path = args.root
    pin_path: Path = args.pin

    if args.no_sync:
        if not root.is_dir() or not (root / "rubric.json").is_file():
            print(f"FAIL: missing vendored policy under {root}", file=sys.stderr)
            return 1
        pin = read_pin(pin_path)
        if not pin:
            print(f"FAIL: missing pin file {pin_path}", file=sys.stderr)
            return 1
        print(f"OK: using committed policy pin {pin} (--no-sync)")
        return 0

    try:
        latest = upstream_head_sha()
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as err:
        print(f"FAIL: could not query upstream {UPSTREAM}: {err}", file=sys.stderr)
        return 1

    current = read_pin(pin_path)
    if current == latest and (root / "rubric.json").is_file():
        print(f"OK: policy pin matches upstream ({latest})")
        return 0

    backup_root = root.with_name(root.name + ".sync-backup")
    if root.is_dir():
        if backup_root.exists():
            shutil.rmtree(backup_root)
        shutil.copytree(root, backup_root)
    old_root = backup_root if backup_root.is_dir() else None

    root.mkdir(parents=True, exist_ok=True)
    try:
        download_policy(latest, root)
        write_vendor_readme(root, latest)
        pin_path.write_text(latest + "\n", encoding="utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        print(f"FAIL: policy download failed: {err}", file=sys.stderr)
        return 1

    for line in diff_summary(old_root, root, current, latest):
        print(line)
    print(f"OK: updated PalomarPolicy to {latest}")
    if old_root and old_root.is_dir():
        shutil.rmtree(old_root, ignore_errors=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync vendored PalomarPolicy from upstream.")
    parser.add_argument("--root", type=Path, default=Path("vendor/palomar-policy"))
    parser.add_argument("--pin", type=Path, default=Path("vendor/PALOMAR_POLICY_PIN"))
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="skip upstream check; require existing vendored policy",
    )
    parser.add_argument(
        "--editorial-skip-check",
        action="store_true",
        help="compare policy prompt fingerprint to a prior preflight run report",
    )
    parser.add_argument(
        "--prior-report",
        type=Path,
        default=Path(".cache/palomar-editorial/preflight-run.json"),
        help="prior preflight-run.json for --editorial-skip-check",
    )
    args = parser.parse_args()
    if args.editorial_skip_check:
        return cmd_editorial_skip_check(args)
    return cmd_sync(args)


if __name__ == "__main__":
    raise SystemExit(main())
