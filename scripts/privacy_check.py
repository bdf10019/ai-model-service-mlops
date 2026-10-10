"""Scan deliverable source files; private runtime output is deliberately excluded."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private_home": re.compile(r"[A-Z]:[\\/]+Users[\\/]+", re.I),
    "machine_name": re.compile(r"LAPTOP-[A-Z0-9]{6,}", re.I),
    "access_key": re.compile(r"(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})"),
    "private_account": re.compile(r"bdf(?:11|10019)", re.I),
}


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    findings = []
    scanned = 0
    for name in result.stdout.decode("utf-8").split("\0"):
        if not name or name == "scripts/privacy_check.py":
            continue
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        scanned += 1
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append({"file": name, "type": label})
    report = {
        "scope": "git_deliverable_source_not_private_runtime",
        "scanned_files": scanned,
        "findings": findings,
        "passed": not findings,
        "limitations": "有限規則掃描；不是完整個資辨識或依賴漏洞稽核。",
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/privacy-check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"scanned_files": scanned, "findings": findings, "passed": not findings},
            ensure_ascii=True,
        )
    )
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
