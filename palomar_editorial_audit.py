#!/usr/bin/env python3
"""Run PalomarPolicy editorial rubric via pinned Codex (gpt-6-sol)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from palomar_paths import project_root

ROOT = project_root()
CACHE_DIR = ROOT / ".cache/palomar-editorial"
STEPS_DIR = CACHE_DIR / "steps"
FAILURE_DUMP = CACHE_DIR / "last-codex-failure.json"
CODEX_EVENTS = CACHE_DIR / "last-codex-events.jsonl"
PINNED_CODEX_VERSION = "codex-cli 0.147.0"

# Palomar's official editorial model. Every step uses it unless overridden.
PRIMARY_MODEL = "gpt-6-sol"
ECONOMY_MODEL = "gpt-6-sol"

PRIMARY_STEPS = frozenset(
    {
        "statement_alignment",
        "definition_fidelity",
        "literature_notability",
        "synthesis",
    }
)
ECONOMY_STEPS = frozenset(
    {
        "classification",
        "metadata",
        "proof_account",
    }
)

PROOF_ACCOUNT_TRIGGER = re.compile(
    r"informal proof|proof account|proof architecture|proof strategy",
    re.IGNORECASE,
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_text(path: Path, limit: int | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    if limit is not None and len(text) > limit:
        return text[:limit] + f"\n\n[truncated at {limit} characters]"
    return text


def parse_model_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("model output is not a JSON object")
    return json.loads(text[start : end + 1])


KEY_FILE_CANDIDATES = (
    ROOT.parent / "openai_key.txt",
    ROOT / "openai_key.txt",
    Path(__file__).resolve().parent.parent / "openai_key.txt",
)


def load_openai_api_key() -> str:
    for name in ("OPENAI_API_KEY", "PALOMAR_OPENAI_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    tried: list[str] = []
    for path in KEY_FILE_CANDIDATES:
        tried.append(str(path))
        if not path.is_file():
            continue
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    raise SystemExit(
        "FAIL: set OPENAI_API_KEY or put the key in ../openai_key.txt for the editorial audit.\n"
        f"Looked for key files: {', '.join(tried)}\n"
        f"Editorial model: {PRIMARY_MODEL}."
    )


def model_for_step(step_id: str) -> str:
    if step_id in PRIMARY_STEPS:
        return os.environ.get("PALOMAR_EDITORIAL_PRIMARY_MODEL", PRIMARY_MODEL).strip() or PRIMARY_MODEL
    if step_id in ECONOMY_STEPS:
        return os.environ.get("PALOMAR_EDITORIAL_ECONOMY_MODEL", ECONOMY_MODEL).strip() or ECONOMY_MODEL
    return PRIMARY_MODEL


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def evidence_fingerprint() -> str:
    import hashlib

    digest = hashlib.sha256()
    for rel in (
        "Challenge.lean",
        "formalization.yaml",
        "comparator.json",
        "README.md",
        "PROVENANCE.md",
        "arxiv.md",
        "Solution.lean",
    ):
        path = ROOT / rel
        digest.update(rel.encode())
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def step_cache_path(step_id: str) -> Path:
    return STEPS_DIR / f"{step_id}.json"


def load_cached_step(step_id: str, commit: str, policy_pin: str, fingerprint: str) -> dict | None:
    if _env_flag("PALOMAR_EDITORIAL_NO_RESUME"):
        return None
    path = step_cache_path(step_id)
    if not path.is_file():
        return None
    try:
        doc = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    result = doc.get("result")
    if not isinstance(result, dict):
        return None
    if (
        doc.get("step") != step_id
        or doc.get("commit") != commit
        or doc.get("policy_pin") != policy_pin
        or doc.get("fingerprint") != fingerprint
    ):
        return None
    return result


def save_cached_step(
    step_id: str, commit: str, policy_pin: str, fingerprint: str, model: str, result: dict
) -> None:
    STEPS_DIR.mkdir(parents=True, exist_ok=True)
    step_cache_path(step_id).write_text(
        json.dumps(
            {
                "commit": commit,
                "policy_pin": policy_pin,
                "fingerprint": fingerprint,
                "step": step_id,
                "model": model,
                "result": result,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def dump_codex_failure(
    model: str, message: str, stdout: str = "", stderr: str = ""
) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    FAILURE_DUMP.write_text(
        json.dumps(
            {
                "engine": "codex",
                "model": model,
                "error": message,
                "stdout_tail": stdout[-8000:],
                "stderr_tail": stderr[-8000:],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return FAILURE_DUMP


def result_schema(score_keys: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "step": {"type": "string"},
            "outcome": {"type": "string", "enum": ["neutral", "warning", "failure"]},
            "summary": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "string"}},
            "scores": {
                "type": "object",
                "properties": {
                    key: {"type": ["integer", "null"], "minimum": 1, "maximum": 5}
                    for key in score_keys
                },
                "required": score_keys,
                "additionalProperties": False,
            },
            "trust_level": {"type": ["string", "null"]},
            "sources_checked": {"type": "array", "items": {"type": "string"}},
            "declarations_checked": {"type": "array", "items": {"type": "string"}},
            "codes_checked": {"type": "array", "items": {"type": "string"}},
            "internal_notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "evidence": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["evidence", "message"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "step",
            "outcome",
            "summary",
            "findings",
            "scores",
            "trust_level",
            "sources_checked",
            "declarations_checked",
            "codes_checked",
            "internal_notes",
        ],
        "additionalProperties": False,
    }


def synthesis_schema(score_keys: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["neutral", "revision_required", "rejected"],
            },
            "summary": {"type": "string"},
            "scores": {
                "type": "object",
                "properties": {
                    key: {"type": ["integer", "null"], "minimum": 1, "maximum": 5}
                    for key in score_keys
                },
                "required": score_keys,
                "additionalProperties": False,
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
            "requested_changes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["outcome", "summary", "scores", "warnings", "requested_changes"],
        "additionalProperties": False,
    }


def codex_executable() -> Path:
    configured = os.environ.get("PALOMAR_CODEX", "").strip()
    path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parent
        / "codex-runtime"
        / "node_modules"
        / ".bin"
        / "codex"
    )
    if not path.is_file():
        raise SystemExit(f"FAIL: pinned Codex executable is missing: {path}")
    found = subprocess.run(
        [str(path), "--version"], capture_output=True, text=True, check=False
    )
    if found.returncode != 0 or found.stdout.strip() != PINNED_CODEX_VERSION:
        raise SystemExit(
            f"FAIL: expected {PINNED_CODEX_VERSION}, found "
            f"{found.stdout.strip() or found.stderr.strip()!r}"
        )
    return path


def codex_prompt(
    api_key: str,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> str:
    codex = codex_executable()
    retries = max(0, _env_int("PALOMAR_EDITORIAL_STEP_RETRIES", 0))
    last_message = f"FAIL: Codex run did not finish ({model})"
    for attempt in range(retries + 1):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="codex-pass-", dir=CACHE_DIR
        ) as temp_name:
            temp = Path(temp_name)
            schema_path = temp / "schema.json"
            output_path = temp / "message.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            prompt = (
                f"{system.strip()}\n\n---\n\n"
                "You are running in the submission repository with read-only tools. "
                "Inspect the current filesystem working tree directly; it is the "
                "authoritative evidence for this local audit even when it differs "
                "from HEAD. Do not use `git show`, a historical commit, or an "
                "index snapshot as the file source. Inspect Challenge.lean, "
                "formalization.yaml, README.md, PROVENANCE.md, arxiv.md, "
                "comparator.json, Solution.lean, and relevant Lean sources. "
                "Do not treat the evidence summary below as exhaustive.\n\n"
                f"{user.strip()}\n\n"
                "Return one JSON object conforming to the supplied schema."
            )
            command = [
                str(codex),
                "exec",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--ignore-user-config",
                "--json",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--cd",
                str(ROOT),
                "--model",
                model,
                "-c",
                'web_search="disabled"',
                "-c",
                "features.multi_agent=false",
                "-",
            ]
            reasoning = os.environ.get(
                "PALOMAR_EDITORIAL_REASONING_EFFORT", ""
            ).strip()
            if sys.platform.startswith("linux"):
                # Some local Linux hosts deny bubblewrap's loopback setup.
                # Landlock preserves the same read-only policy without a
                # network-namespace operation.
                command[-1:-1] = [
                    "-c",
                    "features.use_legacy_landlock=true",
                ]
            if reasoning:
                command[-1:-1] = [
                    "-c",
                    f"model_reasoning_effort={reasoning}",
                ]
            env = os.environ.copy()
            env["OPENAI_API_KEY"] = api_key
            env["CODEX_HOME"] = str(temp / "codex-home")
            (temp / "codex-home").mkdir()
            login = subprocess.run(
                [str(codex), "login", "--with-api-key"],
                input=api_key,
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            if login.returncode != 0:
                last_message = f"FAIL: Codex API-key login failed ({model})"
                dump_codex_failure(
                    model, last_message, login.stdout, login.stderr
                )
                raise SystemExit(
                    f"{last_message}\n  details: {FAILURE_DUMP}"
                )
            try:
                run = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=max(
                        60, _env_int("PALOMAR_EDITORIAL_STEP_TIMEOUT", 7200)
                    ),
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as err:
                last_message = f"FAIL: Codex run timed out ({model})"
                dump_codex_failure(
                    model,
                    last_message,
                    err.stdout or "",
                    err.stderr or "",
                )
                if attempt < retries:
                    print(f"RETRY: {last_message}", file=sys.stderr)
                    continue
                raise SystemExit(last_message) from err
            CODEX_EVENTS.write_text(run.stdout, encoding="utf-8")
            if run.returncode == 0 and output_path.is_file():
                body = output_path.read_text(encoding="utf-8").strip()
                if body:
                    return body
                last_message = f"FAIL: empty Codex response ({model})"
            else:
                last_message = (
                    f"FAIL: Codex run failed ({model}, exit {run.returncode})"
                )
            dump_codex_failure(model, last_message, run.stdout, run.stderr)
            if attempt < retries:
                print(f"RETRY: {last_message}", file=sys.stderr)
                continue
            raise SystemExit(
                f"{last_message}\n  details: {FAILURE_DUMP}"
            )
    raise SystemExit(last_message)


def load_comparator() -> dict:
    return load_json(ROOT / "comparator.json")


def expected_declarations(cfg: dict) -> list[str]:
    return list(cfg["theorem_names"]) + list(cfg.get("definition_names", []))


def expected_codes(formalization_yaml: str) -> list[str]:
    try:
        import yaml

        doc = yaml.safe_load(formalization_yaml)
        classification = doc.get("classification", {}) if isinstance(doc, dict) else {}
        codes: list[str] = []
        for item in classification.get("arxiv", []) or []:
            codes.append(f"arxiv:{item}")
        for item in classification.get("msc2020", []) or []:
            codes.append(f"msc2020:{item}")
        return codes
    except Exception:
        codes: list[str] = []
        for m in re.finditer(r"arxiv:\s*\[(.*?)\]", formalization_yaml, re.DOTALL):
            for item in re.findall(r"[\w.\-]+", m.group(1)):
                codes.append(f"arxiv:{item}")
        for m in re.finditer(r"msc2020:\s*\[(.*?)\]", formalization_yaml, re.DOTALL):
            for item in re.findall(r"[\w]+", m.group(1)):
                codes.append(f"msc2020:{item}")
        return codes


def has_proof_account(*texts: str) -> bool:
    return any(PROOF_ACCOUNT_TRIGGER.search(t) for t in texts)


def assemble_evidence(step_id: str, cfg: dict, policy_dir: Path, mechanical: dict, prior: list[dict]) -> dict:
    repo_commit = mechanical.get("repository", {}).get("commit") or "unknown"
    return {
        "step": step_id,
        "repository_commit": repo_commit,
        "comparator": cfg,
        "mechanical_report": mechanical,
        "policy_directory": str(policy_dir),
        "repository_root": str(ROOT),
        "previous_findings": [
            finding for result in prior for finding in result.get("findings", [])
        ],
        "declarations_checked_order": expected_declarations(cfg),
        "evidence_instruction": (
            "Inspect the complete read-only repository directly. Narrative evidence "
            "is not limited to this summary; arxiv.md is an eligible narrative source."
        ),
    }


def validate_step_result(result: dict, step: dict, cfg: dict, formalization_yaml: str) -> list[str]:
    errors: list[str] = []
    step_id = step["id"]
    required = {
        "step",
        "outcome",
        "summary",
        "findings",
        "scores",
        "trust_level",
        "sources_checked",
        "declarations_checked",
        "codes_checked",
        "internal_notes",
    }
    missing = required - set(result)
    if missing:
        errors.append(f"{step_id}: missing fields {sorted(missing)}")
    if result.get("step") != step_id:
        errors.append(f"{step_id}: step field mismatch {result.get('step')!r}")
    outcome = result.get("outcome")
    if outcome not in {"neutral", "warning", "failure"}:
        errors.append(f"{step_id}: invalid outcome {outcome!r}")

    if step.get("requires_declaration_coverage"):
        expected = expected_declarations(cfg)
        actual = result.get("declarations_checked")
        if actual != expected:
            errors.append(f"{step_id}: declarations_checked mismatch")

    if step.get("requires_classification_coverage"):
        expected = expected_codes(formalization_yaml)
        actual = result.get("codes_checked")
        if actual != expected:
            errors.append(f"{step_id}: codes_checked mismatch (expected {expected}, got {actual})")

    findings = result.get("findings", [])
    if outcome == "neutral" and findings:
        errors.append(f"{step_id}: neutral outcome must have empty findings")
    if outcome in {"warning", "failure"} and not findings:
        errors.append(f"{step_id}: {outcome} outcome requires at least one finding")

    for key in step.get("score_keys", []):
        score = result.get("scores", {}).get(key)
        if score is not None and not (isinstance(score, int) and 1 <= score <= 5):
            errors.append(f"{step_id}: score {key}={score!r} not integer 1-5")

    return errors


def validate_synthesis(synthesis: dict, step_results: list[dict], rubric: dict) -> list[str]:
    errors: list[str] = []
    required = {"outcome", "summary", "scores", "warnings", "requested_changes"}
    missing = required - set(synthesis)
    if missing:
        errors.append(f"synthesis: missing fields {sorted(missing)}")

    outcome = synthesis.get("outcome")
    if outcome not in {"neutral", "revision_required", "rejected"}:
        errors.append(f"synthesis: invalid outcome {outcome!r}")

    minimum = rubric.get("minimum_score", 4)
    registry_scores = rubric.get("registry_scores", [])
    for key in registry_scores:
        expected = None
        for result in step_results:
            val = result.get("scores", {}).get(key)
            if val is not None:
                expected = val
                break
        actual = synthesis.get("scores", {}).get(key)
        if expected is not None and actual != expected:
            errors.append(f"synthesis: score {key} must copy evidence check ({expected} != {actual})")

    notability = synthesis.get("scores", {}).get("notability")
    if notability is not None and notability < minimum:
        if outcome != "rejected":
            errors.append("synthesis: notability below minimum requires rejected outcome")

    failed_checks = [r for r in step_results if r.get("outcome") == "failure"]
    if failed_checks and outcome == "neutral":
        errors.append("synthesis: cannot be neutral when a check failed")

    if outcome == "revision_required" and not synthesis.get("requested_changes"):
        errors.append("synthesis: revision_required needs requested_changes")

    all_findings: list[str] = []
    for result in step_results:
        for finding in result.get("findings", []):
            if isinstance(finding, dict):
                all_findings.append(str(finding.get("message", finding)))
            else:
                all_findings.append(str(finding))
    if outcome == "neutral" and all_findings:
        errors.append("synthesis: neutral outcome cannot have material findings")

    return errors


def run_step(
    step: dict,
    policy_dir: Path,
    materiality: str,
    cfg: dict,
    mechanical: dict,
    prior: list[dict],
    api_key: str,
    formalization_yaml: str,
) -> tuple[dict, str]:
    step_id = step["id"]
    model = model_for_step(step_id)
    prompt_path = policy_dir / step["prompt"]
    prompt_text = read_text(prompt_path)
    system = materiality + "\n\n---\n\n" + prompt_text
    evidence = assemble_evidence(step_id, cfg, policy_dir, mechanical, prior)
    user = (
        "Evaluate the submission evidence below. Return one bare JSON object only.\n\n"
        + json.dumps(evidence, indent=2)
    )
    raw = codex_prompt(api_key, model, system, user, result_schema(step.get("score_keys", [])))
    result = parse_model_json(raw)
    errors = validate_step_result(result, step, cfg, formalization_yaml)
    if errors:
        raise SystemExit("FAIL: step validation errors:\n  " + "\n  ".join(errors))
    return result, model


def run_synthesis(
    policy_dir: Path,
    materiality: str,
    step_results: list[dict],
    mechanical: dict,
    api_key: str,
    score_keys: list[str],
) -> tuple[dict, str]:
    model = model_for_step("synthesis")
    prompt_text = read_text(policy_dir / "prompts/06-synthesis.md")
    system = materiality + "\n\n---\n\n" + prompt_text
    user = json.dumps(
        {
            "mechanical_report": mechanical,
            "all_previous_results": step_results,
            "submission": read_text(ROOT / "formalization.yaml", 80_000),
        },
        indent=2,
    )
    raw = codex_prompt(api_key, model, system, user, synthesis_schema(score_keys))
    return parse_model_json(raw), model


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Palomar editorial audit using vendored PalomarPolicy prompts."
    )
    parser.add_argument("--policy-dir", type=Path, default=Path("vendor/palomar-policy"))
    parser.add_argument("--policy-pin", required=True)
    parser.add_argument("--mechanical-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore cached successful editorial steps for this commit.",
    )
    args = parser.parse_args()
    if args.no_resume:
        os.environ["PALOMAR_EDITORIAL_NO_RESUME"] = "1"

    api_key = load_openai_api_key()
    policy_dir = args.policy_dir
    rubric = load_json(policy_dir / "rubric.json")
    materiality = read_text(policy_dir / "prompts/materiality.md")
    mechanical = load_json(args.mechanical_report)
    cfg = load_comparator()
    formalization_yaml = read_text(ROOT / "formalization.yaml")
    repo_commit = str(mechanical.get("repository", {}).get("commit") or "unknown")
    fingerprint = evidence_fingerprint()

    proof_texts = [
        read_text(ROOT / "Challenge.lean", 40_000),
        read_text(ROOT / "README.md", 40_000),
        formalization_yaml,
    ]
    if (ROOT / "Solution.lean").is_file():
        proof_texts.append(read_text(ROOT / "Solution.lean", 40_000))

    step_results: list[dict] = []
    models_by_step: dict[str, str] = {}
    for step in rubric["steps"]:
        if step["id"] == "synthesis":
            continue
        if step["id"] == "proof_account" and not has_proof_account(*proof_texts):
            print(f"SKIP: {step['id']} (no informal proof account detected)")
            continue
        model = model_for_step(step["id"])
        cached = load_cached_step(step["id"], repo_commit, args.policy_pin, fingerprint)
        if cached is not None:
            print(f"RESUME: editorial step {step['id']} ({model}, cached)")
            step_results.append(cached)
            models_by_step[step["id"]] = model
            print(f"  outcome={cached['outcome']} summary={cached['summary'][:120]}")
            continue
        print(f"RUN: editorial step {step['id']} ({model}) …")
        result, used_model = run_step(
            step, policy_dir, materiality, cfg, mechanical, step_results, api_key, formalization_yaml
        )
        save_cached_step(step["id"], repo_commit, args.policy_pin, fingerprint, used_model, result)
        step_results.append(result)
        models_by_step[step["id"]] = used_model
        print(f"  outcome={result['outcome']} summary={result['summary'][:120]}")

    synth_model = model_for_step("synthesis")
    cached_synth = load_cached_step("synthesis", repo_commit, args.policy_pin, fingerprint)
    if cached_synth is not None:
        print(f"RESUME: editorial synthesis ({synth_model}, cached)")
        synthesis, used_synth_model = cached_synth, synth_model
    else:
        print(f"RUN: editorial synthesis ({synth_model}) …")
        synthesis, used_synth_model = run_synthesis(
            policy_dir,
            materiality,
            step_results,
            mechanical,
            api_key,
            list(rubric.get("registry_scores", [])),
        )
        save_cached_step("synthesis", repo_commit, args.policy_pin, fingerprint, used_synth_model, synthesis)
    models_by_step["synthesis"] = used_synth_model
    syn_errors = validate_synthesis(synthesis, step_results, rubric)
    if syn_errors:
        raise SystemExit("FAIL: synthesis validation errors:\n  " + "\n  ".join(syn_errors))

    packet = {
        "policy_commit": args.policy_pin,
        "provider": "codex",
        "engine_version": PINNED_CODEX_VERSION,
        "models": {
            "primary_default": PRIMARY_MODEL,
            "economy_default": ECONOMY_MODEL,
            "by_step": models_by_step,
        },
        "checks": step_results,
        "synthesis": synthesis,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")

    outcome = synthesis.get("outcome")
    print(f"OK: editorial audit written to {args.out}")
    print(f"Synthesis outcome: {outcome}")
    if outcome != "neutral":
        print("Findings / warnings:")
        for warning in synthesis.get("warnings", []):
            print(f"  - {warning}")
        for change in synthesis.get("requested_changes", []):
            print(f"  * {change}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
