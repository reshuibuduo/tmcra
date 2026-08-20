#!/usr/bin/env python3
"""Scan tracked text files for high-confidence credentials before publishing."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_SUFFIXES = {
    ".7z",
    ".aab",
    ".apk",
    ".bin",
    ".dmg",
    ".exe",
    ".gguf",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pt",
    ".tar",
    ".webp",
    ".whl",
    ".zip",
}

HIGH_CONFIDENCE_RULES = {
    "private key": re.compile(r"-----BEGIN (?:DSA |EC |OPENSSH |PGP |RSA )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"(?:gh[oprsu]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})"),
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
    "JWT bearer": re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"),
}
GENERIC_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|auth[_-]?token)"
    r"\s*[=:]\s*[\"']([^\"'\r\n]{8,})[\"']"
)
PLACEHOLDER_MARKERS = (
    "${",
    "<",
    "change-me",
    "changeme",
    "dummy",
    "example",
    "fake",
    "placeholder",
    "replace",
    "sample",
    "test",
    "your-",
    "your_",
    "xxxxx",
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [ROOT / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        relative_parts = path.relative_to(ROOT).parts
        is_fixture_or_docs = (
            path.suffix.lower() in {".md", ".rst"}
            or any(part.lower() in {"test", "tests", "fixtures"} for part in relative_parts)
            or path.name.lower().startswith(("test_", "test-"))
        )
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in HIGH_CONFIDENCE_RULES.items():
                if pattern.search(line):
                    findings.append(f"{relative}:{line_number}: {label}")
            if not is_fixture_or_docs:
                for match in GENERIC_ASSIGNMENT.finditer(line):
                    if not looks_like_placeholder(match.group(1)):
                        findings.append(
                            f"{relative}:{line_number}: assigned credential-like value"
                        )

    if findings:
        print("Tracked-file secret scan failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        print("Replace credentials with environment-variable references before publishing.", file=sys.stderr)
        return 1

    print("Tracked-file secret scan passed (high-confidence credential patterns).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
