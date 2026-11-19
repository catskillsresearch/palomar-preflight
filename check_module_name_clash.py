#!/usr/bin/env python3
# Copyright (c) 2026 Lars Warren Ericson. All rights reserved.
"""Fail if the Challenge/Solution module's top-level name clashes with a dependency.

Palomar renders the Challenge with Verso, which locates a module's source by its
top-level name. If `challenge_module` (or `solution_module`) in comparator.json
starts with a name that is also a top-level entry of a Lake dependency package
(a `<Name>.lean` file or a `<Name>/` directory at the package root) or is the
package's own name, Palomar verification reports e.g.

    the Challenge module's top-level name 'Challenge' is also a top-level entry
    of the dependency 'MathFin', which is where Verso would look for its source

This check reproduces that rule locally. Packages come from lake-manifest.json
(`dir` for path dependencies, otherwise `<packagesDir>/<name>`).

Usage (from the project root):

    python3 <palomar-preflight>/check_module_name_clash.py
    python3 <palomar-preflight>/check_module_name_clash.py --modules Challenge Solution
    python3 <palomar-preflight>/check_module_name_clash.py --comparator old-comparator.json
    python3 <palomar-preflight>/check_module_name_clash.py --root /path/to/project
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def top_level_entries(pkg_dir: Path) -> set[str]:
    names: set[str] = set()
    for entry in pkg_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            names.add(entry.name)
        elif entry.suffix == ".lean":
            names.add(entry.stem)
    return names


def packages(root: Path) -> list[tuple[str, Path]]:
    manifest = root / "lake-manifest.json"
    if not manifest.is_file():
        raise SystemExit(f"FAIL: {manifest} not found")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    packages_dir = root / data.get("packagesDir", ".lake/packages")
    out: list[tuple[str, Path]] = []
    for pkg in data.get("packages", []):
        name = pkg.get("name")
        if not name:
            continue
        if pkg.get("type") == "path" and pkg.get("dir"):
            pkg_dir = root / pkg["dir"]
        else:
            pkg_dir = packages_dir / name
        out.append((name, pkg_dir))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument(
        "--modules",
        nargs="+",
        help="module names to check, Challenge first then Solution (default: "
        "challenge_module and solution_module from comparator.json)",
    )
    ap.add_argument(
        "--comparator",
        type=Path,
        help="comparator.json to read (default: <root>/comparator.json)",
    )
    args = ap.parse_args()
    root = args.root.resolve()

    if args.modules:
        roles = ["Challenge", "Solution"]
        modules = [
            (roles[i] if i < len(roles) else "extra", m)
            for i, m in enumerate(args.modules)
        ]
    else:
        cfg_path = args.comparator or (root / "comparator.json")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        modules = [
            ("Challenge", cfg["challenge_module"]),
            ("Solution", cfg["solution_module"]),
        ]

    pkgs = packages(root)
    missing = [name for name, d in pkgs if not d.is_dir()]
    if missing:
        print(
            "FAIL: dependency packages not checked out (run `lake build` or "
            "`lake update` first): " + ", ".join(missing)
        )
        return 1
    entries = {name: top_level_entries(d) for name, d in pkgs}

    clashes: list[str] = []
    for role, module in modules:
        top = module.split(".", 1)[0]
        for name in sorted(entries):
            if top == name or top in entries[name]:
                where = "the package name" if top == name else "a top-level entry"
                clashes.append(
                    f"the {role} module's top-level name {top!r} (from {module!r}) is "
                    f"also {where} of the dependency {name!r}, which is where Verso "
                    "would look for its source"
                )
    if clashes:
        print("FAIL: module name clashes with a Lake dependency:")
        for c in clashes:
            print(f"  - {c}")
        print(
            "Next: move the module under a top-level directory that no dependency "
            "also has at its top level (e.g. <Project>Comparator/Challenge.lean), "
            "update challenge_module / solution_module in comparator.json and the "
            "lakefile lean_lib roots, then commit."
        )
        return 1
    tops = sorted({m.split(".", 1)[0] for _, m in modules})
    print(
        f"OK: module top-level name(s) {', '.join(tops)} do not clash with "
        f"{len(pkgs)} Lake dependency package(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
