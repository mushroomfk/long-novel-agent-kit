#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATH_MARKERS = (
    "/Users/liuqingxing",
    "/private/tmp",
    "/var/folders/",
    "\\Users\\liuqingxing",
)
FORBIDDEN_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".novel-agent"}
FORBIDDEN_FILE_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_RUNTIME_NAMES = {
    "long-novel-agent.exe",
    "long-novel-agent-mcp.exe",
}


def iter_files() -> Iterable[Path]:
    for path in ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_file():
            yield path


def fail(message: str) -> None:
    raise SystemExit(f"adversarial release check failed: {message}")


def check_no_generated_artifacts() -> None:
    bad_dirs = [path for path in ROOT.rglob("*") if path.is_dir() and path.name in FORBIDDEN_DIR_NAMES and ".git" not in path.parts]
    if bad_dirs:
        fail("generated or private state directories found: " + ", ".join(str(path.relative_to(ROOT)) for path in bad_dirs[:12]))

    bad_files: list[Path] = []
    for path in iter_files():
        if path.suffix in FORBIDDEN_FILE_SUFFIXES or path.name in FORBIDDEN_RUNTIME_NAMES:
            bad_files.append(path)
        if path.name in {"long-novel-agent", "long-novel-agent-mcp"} and "runtime" in path.parts:
            bad_files.append(path)
    if bad_files:
        fail("generated runtime/cache files found: " + ", ".join(str(path.relative_to(ROOT)) for path in bad_files[:12]))


def check_no_local_paths() -> None:
    offenders: list[str] = []
    text_suffixes = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".py", ".toml", ".html", ".sh", ".ps1", ".cmd"}
    for path in iter_files():
        if path.relative_to(ROOT).as_posix() == "scripts/adversarial_release_check.py":
            continue
        if path.suffix.lower() not in text_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in LOCAL_PATH_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {marker}")
                break
    if offenders:
        fail("local machine paths leaked into repository: " + "; ".join(offenders[:12]))


def check_json_files() -> None:
    for path in iter_files():
        suffix = path.suffix.lower()
        if suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")
        elif suffix == ".jsonl":
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    fail(f"invalid JSONL in {path.relative_to(ROOT)} line {index}: {exc}")


def check_python_compile() -> None:
    for name in ["cli.py", "server.py", "install.py", "scripts/verify_agent_kit.py", "scripts/adversarial_release_check.py"]:
        path = ROOT / name
        if not path.exists():
            fail(f"missing required Python file: {name}")
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")


def run_full_regression() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/verify_agent_kit.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=360,
        check=False,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        fail(f"full regression failed with exit code {proc.returncode}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        fail(f"full regression did not return JSON: {exc}")
    if payload.get("ok") is not True:
        fail("full regression returned ok=false")
    results = payload.get("results") if isinstance(payload.get("results"), dict) else {}
    required = [
        "example",
        "desktop_handoff_relay",
        "longform_continuity_chain",
        "longform_twenty_chapter_stress",
        "desktop_verification_evidence_example",
        "proposal_guardrails",
        "desktop_pack",
        "desktop_handoff_bundle",
        "mcp_read_only",
        "mcp_tool_profiles",
        "author_confirmation",
        "adversarial_cases",
    ]
    missing = [key for key in required if key not in results]
    if missing:
        fail("full regression missing result sections: " + ", ".join(missing))


def main() -> int:
    checks = [
        ("generated artifacts", check_no_generated_artifacts),
        ("local paths", check_no_local_paths),
        ("json files", check_json_files),
        ("python compile", check_python_compile),
        ("full regression", run_full_regression),
    ]
    passed: list[str] = []
    for label, func in checks:
        func()
        passed.append(label)
    print(json.dumps({"ok": True, "checks": passed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
