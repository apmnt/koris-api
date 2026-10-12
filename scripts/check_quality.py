#!/usr/bin/env python3
"""Enforce clean Ruff/type checks against the recorded warning baseline."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "quality-baseline.json"


def warning_counts(report: dict[str, object]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for diagnostic in cast(list[dict[str, object]], report["generalDiagnostics"]):
        if diagnostic["severity"] == "warning":
            path = Path(str(diagnostic["file"])).relative_to(ROOT).as_posix()
            counts[f"{path}:{diagnostic.get('rule', 'unknown')}"] += 1
    return counts


def main() -> int:
    lint = subprocess.run(
        ["ruff", "check", "src", "tests"], cwd=ROOT, check=False
    )
    checked = subprocess.run(
        ["basedpyright", "--outputjson"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if not checked.stdout.strip():
        print(checked.stderr)
        return 1
    report = cast(dict[str, object], json.loads(checked.stdout))
    summary = cast(dict[str, int], report["summary"])
    errors = summary["errorCount"]
    warnings = warning_counts(report)
    baseline = cast(dict[str, object], json.loads(BASELINE.read_text()))
    limits = cast(dict[str, int], baseline["warnings_by_path_and_rule"])
    increased = {
        key: count - limits.get(key, 0)
        for key, count in warnings.items()
        if count > limits.get(key, 0)
    }
    print(f"basedpyright: {errors} errors, {sum(warnings.values())} visible warnings")
    for key, increase in sorted(increased.items()):
        print(f"+{increase} warnings: {key}")
    if errors:
        for diagnostic in cast(list[dict[str, object]], report["generalDiagnostics"]):
            if diagnostic["severity"] == "error":
                path = Path(str(diagnostic["file"])).relative_to(ROOT)
                print(f"{path}: {diagnostic['message']}")
    return 1 if lint.returncode or errors or increased else 0


if __name__ == "__main__":
    raise SystemExit(main())
