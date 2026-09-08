#!/usr/bin/env python3
"""Run PalomarPolicy editorial rubric via Cursor SDK."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from palomar_paths import project_root

ROOT = project_root()
CACHE_DIR = ROOT / ".cache/palomar-editorial"
STEPS_DIR = CACHE_DIR / "steps"
FAILURE_DUMP = CACHE_DIR / "last-cursor-failure.json"

# Palomar production editorial content uses gpt-5.6-sol; lighter passes use composer-2.5.
PRIMARY_MODEL = "gpt-5.6-sol"
ECONOMY_MODEL = "composer-2.5"

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

TOKENS_CANDIDATES = (
    ROOT.parent / "tokens_ssto.yaml",
    ROOT / "tokens_ssto.yaml",
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


def load_cursor_api_key() -> str:
    for name in ("CURSOR_API_KEY", "PALOMAR_CURSOR_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    for path in TOKENS_CANDIDATES:
        key = _read_key_from_tokens_file(path)
        if key:
            return key
    tried = ", ".join(str(p) for p in TOKENS_CANDIDATES)
    raise SystemExit(
        "FAIL: set CURSOR_API_KEY or add it to ../tokens_ssto.yaml for editorial audit.\n"
        f"Looked for token files: {tried}\n"
        f"Primary model: {PRIMARY_MODEL}; economy model: {ECONOMY_MODEL}."
    )


def _read_key_from_tokens_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    for pattern in (
        r"(?m)^CURSOR_API_KEY:\s*(\S+)",
        r"(?m)^cursor_api_key:\s*(\S+)",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip().strip("'\"")
    try:
        import yaml

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            key = (data.get("CURSOR_API_KEY") or data.get("cursor_api_key") or "").strip()
            if key:
                return key
    except Exception:
        pass
    return None


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


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            return to_json()
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def evidence_fingerprint() -> str:
    import hashlib

    digest = hashlib.sha256()
    for rel in (
        "Challenge.lean",
        "formalization.yaml",
        "comparator.json",
        "README.md",
        "PROVENANCE.md",
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


def dump_cursor_failure(model: str, result: Any, extra: dict[str, Any] | None = None) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "id": getattr(result, "id", None),
        "agent_id": getattr(result, "agent_id", None),
        "status": str(getattr(result, "status", None)),
        "result": getattr(result, "result", None),
        "duration_ms": getattr(result, "duration_ms", None),
        "created_at": getattr(result, "created_at", None),
        "usage": _jsonable(getattr(result, "usage", None)),
        "git": _jsonable(getattr(result, "git", None)),
        "extra": _jsonable(extra or {}),
    }
    FAILURE_DUMP.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return FAILURE_DUMP


def format_cursor_failure(model: str, result: Any) -> str:
    return (
        f"FAIL: Cursor run did not finish ({model}): "
        f"status={result.status} run={result.id} agent={result.agent_id} "
        f"duration_ms={result.duration_ms} result={result.result!r}"
    )


def cursor_prompt(api_key: str, model: str, system: str, user: str) -> str:
    from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

    prompt = (
        f"{system.strip()}\n\n---\n\n{user.strip()}\n\n"
        "Respond with one bare JSON object only. No markdown fences or surrounding prose."
    )
    retries = max(0, _env_int("PALOMAR_EDITORIAL_STEP_RETRIES", 0))
    disable_tools = not _env_flag("PALOMAR_EDITORIAL_ENABLE_TOOLS")
    last_message = f"FAIL: Cursor run did not finish ({model})"

    for attempt in range(retries + 1):
        try:
            options = AgentOptions(
                api_key=api_key,
                model=model,
                local=LocalAgentOptions(cwd=str(ROOT)),
                tools=[] if disable_tools else None,
            )
            agent = Agent.create(options)
        except CursorAgentError as err:
            if disable_tools and "tools" in (err.message or "").lower() and attempt == 0:
                print(f"NOTE: tools=[] rejected ({model}); retrying with default toolset", file=sys.stderr)
                disable_tools = False
                continue
            raise SystemExit(f"FAIL: Cursor API error ({model}): {err.message}") from err
        extra: dict[str, Any] = {}
        try:
            run = agent.send(prompt)
            result = run.wait()
            if str(result.status) != "finished" and hasattr(run, "supports"):
                try:
                    if run.supports("conversation"):
                        extra["conversation"] = run.conversation()
                except Exception as conv_err:
                    extra["conversation_error"] = str(conv_err)
        except CursorAgentError as err:
            last_message = f"FAIL: Cursor API error ({model}): {err.message}"
            if attempt < retries:
                print(f"RETRY: {last_message}", file=sys.stderr)
                continue
            raise SystemExit(last_message) from err
        finally:
            agent.close()

        if str(result.status) == "finished":
            body = (result.result or "").strip()
            if body:
                return body
            last_message = f"FAIL: empty Cursor response ({model}) run={result.id}"
        else:
            dump_cursor_failure(model, result, extra)
            last_message = format_cursor_failure(model, result)
            print(f"{last_message}\n  dump: {FAILURE_DUMP}", file=sys.stderr)
        if attempt < retries:
            print(f"RETRY: {model} attempt {attempt + 2}/{retries + 1}", file=sys.stderr)
            continue
        raise SystemExit(last_message)
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
    evidence: dict[str, Any] = {
        "step": step_id,
        "repository_commit": repo_commit,
        "comparator": cfg,
        "mechanical_report": mechanical,
        "previous_findings": [
            finding for result in prior for finding in result.get("findings", [])
        ],
    }
    files = {
        "formalization_metadata": ROOT / "formalization.yaml",
        "challenge_source": ROOT / "Challenge.lean",
        "solution_source": ROOT / "Solution.lean",
        "project_readme": ROOT / "README.md",
        "comparator_config": ROOT / "comparator.json",
        "lakefile": ROOT / "lakefile.toml" if (ROOT / "lakefile.toml").is_file() else ROOT / "lakefile.lean",
        "lean_toolchain": ROOT / "lean-toolchain",
        "provenance": ROOT / "PROVENANCE.md",
    }
    for key, path in files.items():
        if path.is_file():
            evidence[key] = read_text(path, limit=120_000 if key == "challenge_source" else 80_000)
    if (policy_dir / "taxonomies/classification-guide.md").is_file():
        evidence["classification_guide"] = read_text(policy_dir / "taxonomies/classification-guide.md", 40_000)
    evidence["declarations_checked_order"] = expected_declarations(cfg)
    return evidence


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
    raw = cursor_prompt(api_key, model, system, user)
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
    raw = cursor_prompt(api_key, model, system, user)
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

    api_key = load_cursor_api_key()
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
            policy_dir, materiality, step_results, mechanical, api_key
        )
        save_cached_step("synthesis", repo_commit, args.policy_pin, fingerprint, used_synth_model, synthesis)
    models_by_step["synthesis"] = used_synth_model
    syn_errors = validate_synthesis(synthesis, step_results, rubric)
    if syn_errors:
        raise SystemExit("FAIL: synthesis validation errors:\n  " + "\n  ".join(syn_errors))

    packet = {
        "policy_commit": args.policy_pin,
        "provider": "cursor_sdk",
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
