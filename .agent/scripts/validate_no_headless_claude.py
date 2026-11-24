#!/usr/bin/env python3
"""Detect forbidden headless Claude invocations in product code.

Some products must not shell out to `claude -p` / `claude --print` from app or
scheduler code. This validator is robust when those directories do not exist:
missing targets are "no aplica", not an error.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGETS = [ROOT / "app", ROOT / "scheduler"]
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}
HEADLESS_CLAUDE_RE = re.compile(
    r"\bclaude\b[^\n]{0,120}(?:--print\b|\s-p\b|['\"]-p['\"])",
    re.IGNORECASE,
)


class Finding(NamedTuple):
    path: Path
    line_no: int
    line: str


def iter_text_files(target: Path):
    if not target.exists():
        return
    if target.is_file():
        yield target
        return
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        yield path


def find_headless_claude(targets: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for target in targets:
        for path in iter_text_files(Path(target)):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if HEADLESS_CLAUDE_RE.search(line):
                    findings.append(Finding(path=path, line_no=line_no, line=line.strip()))
    return findings


def main(argv: list[str]) -> int:
    targets = [Path(arg) for arg in argv[1:]] if len(argv) > 1 else DEFAULT_TARGETS
    findings = find_headless_claude(targets)
    if findings:
        print("NO HEADLESS CLAUDE: FAIL")
        for finding in findings:
            rel = finding.path.relative_to(ROOT) if finding.path.is_relative_to(ROOT) else finding.path
            print(f"- {rel}:{finding.line_no}: {finding.line}")
        return 1
    existing = [str(path) for path in targets if Path(path).exists()]
    if not existing:
        print("NO HEADLESS CLAUDE: NO APLICA (targets inexistentes)")
    else:
        print("NO HEADLESS CLAUDE: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
