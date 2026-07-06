#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


KIT_DIR = Path(__file__).resolve().parents[1]
CLI = KIT_DIR / "cli.py"
SERVER = KIT_DIR / "server.py"
INSTALL = KIT_DIR / "install.py"
REVIEW_PANEL = KIT_DIR / "assets" / "review-panel.html"
EXAMPLE = KIT_DIR / "examples" / "mini-gaoxia-project"
HANDOFF_EXAMPLE = KIT_DIR / "examples" / "desktop-handoff-relay"
DESKTOP_EVIDENCE_EXAMPLE = KIT_DIR / "examples" / "desktop-verification-evidence"
DESKTOP_PACK_SNAPSHOT = KIT_DIR / "examples" / "desktop-pack-snapshot" / "snapshot.json"
PACK_MANIFEST_FILE = "pack-manifest.json"
LONGFORM_CHAIN_EXAMPLE = KIT_DIR / "examples" / "longform-continuity-chain"
ADVERSARIAL_CASES = KIT_DIR / "examples" / "adversarial-cases" / "cases.json"
TEST_AUTHOR_CONFIRMATION = "Author approved this verification write for long-novel-agent regression."
AUTHOR_CONFIRMATION_CLI_COMMANDS = {
    "source-intake",
    "add-source",
    "add-research",
    "resolve-conflict",
    "add-fact",
    "update-fact",
    "supersede-fact",
    "record-chapter",
    "record-style",
    "update-after-write",
    "propose-after-write",
    "apply-after-write",
}
ISO_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
SHA256_RE = re.compile(r"\b[a-f0-9]{64}\b")
PROJECT_ID_RE = re.compile(r"\bnovel-[a-f0-9]{16}\b")
JSON_BYTES_FIELD_RE = re.compile(r'("bytes"\s*:\s*)\d+')


def desktop_pack_snapshot_replacements(project: Path, output_dir: Path, config: Path, target: Path) -> list[tuple[str, str]]:
    candidates = [
        (project, "{{PROJECT_DIR}}"),
        (output_dir, "{{OUTPUT_DIR}}"),
        (config, "{{INSTALL_CONFIG}}"),
        (target, "{{INSTALL_TARGET}}"),
        (KIT_DIR, "{{KIT_DIR}}"),
    ]
    replacements: dict[str, str] = {}
    for path, token in candidates:
        for value in {str(path), str(path.resolve())}:
            if value:
                replacements[value] = token
    return sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True)


def normalize_desktop_pack_snapshot_text(text: str, replacements: list[tuple[str, str]]) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for value, token in replacements:
        normalized = normalized.replace(value, token)
    normalized = ISO_TIMESTAMP_RE.sub("{{TIMESTAMP}}", normalized)
    normalized = SHA256_RE.sub("{{SHA256}}", normalized)
    normalized = PROJECT_ID_RE.sub("{{PROJECT_ID}}", normalized)
    normalized = JSON_BYTES_FIELD_RE.sub(r"\g<1>0", normalized)
    return normalized


def normalize_desktop_pack_manifest_snapshot(text: str, replacements: list[tuple[str, str]]) -> str:
    payload = json.loads(text)
    payload["generated_at"] = "{{TIMESTAMP}}"
    for entry in payload.get("files", []) if isinstance(payload.get("files"), list) else []:
        if not isinstance(entry, dict):
            continue
        if "sha256" in entry:
            entry["sha256"] = "{{SHA256}}"
        if "bytes" in entry:
            entry["bytes"] = 0
    return normalize_desktop_pack_snapshot_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", replacements)


def compute_desktop_pack_snapshot(output_dir: Path, file_names: list[str], replacements: list[tuple[str, str]]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for name in file_names:
        path = output_dir / name
        assert_true(path.exists(), f"desktop-pack snapshot source missing {name}")
        if name == PACK_MANIFEST_FILE:
            normalized = normalize_desktop_pack_manifest_snapshot(path.read_text(encoding="utf-8"), replacements)
        else:
            normalized = normalize_desktop_pack_snapshot_text(path.read_text(encoding="utf-8"), replacements)
        encoded = normalized.encode("utf-8")
        files.append(
            {
                "name": name,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
                "lines": normalized.count("\n") + (0 if normalized.endswith("\n") or not normalized else 1),
            }
        )
    return {
        "type": "desktop_pack_snapshot",
        "version": 1,
        "normalization": ["absolute_paths", "iso_timestamps", "line_endings"],
        "files": files,
    }


def verify_desktop_pack_snapshot(output_dir: Path, pack: dict[str, Any], project: Path, config: Path, target: Path) -> dict[str, Any]:
    assert_true(DESKTOP_PACK_SNAPSHOT.exists(), "desktop-pack snapshot file missing")
    expected_snapshot = json.loads(DESKTOP_PACK_SNAPSHOT.read_text(encoding="utf-8"))
    assert_true(expected_snapshot.get("type") == "desktop_pack_snapshot", "desktop-pack snapshot wrong type")
    snapshot_files = expected_snapshot.get("files")
    assert_true(isinstance(snapshot_files, list) and snapshot_files, "desktop-pack snapshot has no files")
    snapshot_names = [str(item.get("name") or "") for item in snapshot_files if isinstance(item, dict)]
    written_names = sorted(str(item.get("name") or "") for item in pack.get("written_files", []) if isinstance(item, dict))
    assert_true(snapshot_names == written_names, "desktop-pack snapshot file list differs from generated files")
    replacements = desktop_pack_snapshot_replacements(project, output_dir, config, target)
    actual_snapshot = compute_desktop_pack_snapshot(output_dir, snapshot_names, replacements)
    assert_true(actual_snapshot["files"] == snapshot_files, "desktop-pack output snapshot mismatch")
    digest_source = json.dumps(actual_snapshot["files"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "file_count": len(snapshot_files),
        "sha256": hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
    }


def cli_args_with_author_confirmation(args: tuple[str, ...]) -> list[str]:
    prepared = list(args)
    if not prepared:
        return prepared
    command = prepared[0]
    if command not in AUTHOR_CONFIRMATION_CLI_COMMANDS:
        return prepared
    if "--author-confirmation" in prepared:
        return prepared
    if command == "source-intake" and "--apply" not in prepared:
        return prepared
    prepared.extend(["--author-confirmation", TEST_AUTHOR_CONFIRMATION])
    return prepared


def run_cli(*args: str, check: bool = True, auto_author: bool = True) -> subprocess.CompletedProcess[str]:
    prepared = cli_args_with_author_confirmation(args) if auto_author else list(args)
    return subprocess.run([sys.executable, str(CLI), *prepared], text=True, capture_output=True, check=check)


def load_stdout(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(proc.stdout)


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


def current_kit_version() -> str:
    text = CLI.read_text(encoding="utf-8")
    match = re.search(r"(?m)^KIT_VERSION\s*=\s*[\"']([^\"']+)[\"']", text)
    assert_true(match is not None, "KIT_VERSION missing from cli.py")
    return match.group(1)


def verify_desktop_commands_schema_payload(payload: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    assert_true(schema.get("title") == "Long Novel Agent Desktop Pack Commands", "commands schema title mismatch")
    assert_true(schema.get("type") == "object", "commands schema should describe an object")
    top_required = set(schema.get("required") or [])
    for key in [
        "$schema",
        "type",
        "generated_at",
        "project_dir",
        "platform",
        "mode",
        "target_chapter",
        "output_dir",
        "command_count",
        "commands",
        "by_key",
        "groups",
        "by_stage",
        "write_scope_counts",
        "safety_notes",
    ]:
        assert_true(key in top_required, f"commands schema missed required field {key}")
        assert_true(key in payload, f"commands JSON missed required field {key}")
    assert_true(payload["$schema"] == "commands.schema.json", "commands JSON should point to local schema")
    assert_true(payload["type"] == "desktop_pack_commands", "commands JSON wrong type")
    assert_true(isinstance(payload["generated_at"], str) and payload["generated_at"], "commands JSON generated_at should be a string")
    assert_true(isinstance(payload["project_dir"], str) and payload["project_dir"], "commands JSON project_dir should be a string")
    assert_true(isinstance(payload["platform"], str) and payload["platform"], "commands JSON platform should be a string")
    assert_true(payload["mode"] in {"read-only", "writer"}, "commands JSON mode should be read-only or writer")
    assert_true(isinstance(payload["target_chapter"], int) and payload["target_chapter"] >= 1, "commands JSON target_chapter should be positive integer")
    assert_true(isinstance(payload["output_dir"], str) and payload["output_dir"], "commands JSON output_dir should be a string")
    assert_true(isinstance(payload["command_count"], int) and payload["command_count"] >= 1, "commands JSON command_count should be positive integer")
    assert_true(isinstance(payload["commands"], list) and payload["commands"], "commands JSON commands should be a non-empty list")
    assert_true(payload["command_count"] == len(payload["commands"]), "commands JSON command_count should equal commands length")
    assert_true(isinstance(payload["by_key"], dict), "commands JSON by_key should be an object")
    assert_true(isinstance(payload["groups"], list) and payload["groups"], "commands JSON groups should be non-empty list")
    assert_true(isinstance(payload["by_stage"], dict) and payload["by_stage"], "commands JSON by_stage should be a non-empty object")
    assert_true(isinstance(payload["write_scope_counts"], dict), "commands JSON write_scope_counts should be an object")
    assert_true(isinstance(payload["safety_notes"], list) and payload["safety_notes"], "commands JSON safety_notes should be non-empty list")

    defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
    command_schema = defs.get("command") if isinstance(defs.get("command"), dict) else {}
    command_required = set(command_schema.get("required") or [])
    expected_command_fields = {
        "key",
        "command",
        "stage",
        "title",
        "writes_project_state",
        "writes_install_config",
        "writes_pack_files",
        "requires_review",
        "requires_author_confirmation",
        "input_files",
        "output_files",
        "notes",
    }
    assert_true(expected_command_fields <= command_required, "commands schema missed command required fields")
    properties = command_schema.get("properties") if isinstance(command_schema.get("properties"), dict) else {}
    stage_schema = properties.get("stage") if isinstance(properties.get("stage"), dict) else {}
    stage_values = set(stage_schema.get("enum") or [])
    expected_stages = {"install", "verify", "initialize", "source_intake", "evidence", "writing", "writer_mode", "maintenance", "pack", "other"}
    assert_true(expected_stages <= stage_values, "commands schema missed expected stage enum values")
    allowed_fields = set(properties)
    by_key: dict[str, str] = {}
    for entry in payload["commands"]:
        assert_true(isinstance(entry, dict), "commands JSON entry should be object")
        assert_true(set(entry) <= allowed_fields, "commands JSON entry has fields outside schema")
        assert_true(expected_command_fields <= set(entry), "commands JSON entry missed required fields")
        assert_true(isinstance(entry["key"], str) and entry["key"], "commands JSON entry key should be string")
        assert_true(isinstance(entry["command"], str) and entry["command"], "commands JSON entry command should be string")
        assert_true(entry["stage"] in stage_values, "commands JSON entry stage not allowed by schema")
        assert_true(isinstance(entry["title"], str) and entry["title"], "commands JSON entry title should be string")
        for bool_key in ["writes_project_state", "writes_install_config", "writes_pack_files", "requires_review", "requires_author_confirmation"]:
            assert_true(isinstance(entry[bool_key], bool), f"commands JSON entry {bool_key} should be boolean")
        for list_key in ["input_files", "output_files", "notes"]:
            assert_true(isinstance(entry[list_key], list), f"commands JSON entry {list_key} should be list")
            assert_true(all(isinstance(item, str) for item in entry[list_key]), f"commands JSON entry {list_key} should contain strings")
        by_key[entry["key"]] = entry["command"]
    assert_true(payload["by_key"] == by_key, "commands JSON by_key should match commands list")
    command_keys = set(by_key)
    grouped_keys: list[str] = []
    group_required = {
        "id",
        "title",
        "purpose",
        "read_first",
        "command_count",
        "command_keys",
        "writes_project_state",
        "writes_install_config",
        "writes_pack_files",
        "requires_review",
        "requires_author_confirmation",
    }
    for group in payload["groups"]:
        assert_true(isinstance(group, dict), "commands JSON group should be object")
        assert_true(group_required <= set(group), "commands JSON group missed required fields")
        assert_true(group.get("id") != "uncategorized", "commands JSON has uncategorized commands")
        group_keys = group.get("command_keys")
        assert_true(isinstance(group_keys, list) and group_keys, "commands JSON group command_keys should be non-empty list")
        assert_true(group.get("command_count") == len(group_keys), "commands JSON group command_count mismatch")
        assert_true(all(key in command_keys for key in group_keys), "commands JSON group references missing command key")
        grouped_keys.extend(str(key) for key in group_keys)
        for bool_key in ["writes_project_state", "writes_install_config", "writes_pack_files", "requires_review", "requires_author_confirmation"]:
            expected = any(bool(next(entry for entry in payload["commands"] if entry["key"] == key)[bool_key]) for key in group_keys)
            assert_true(group[bool_key] == expected, f"commands JSON group {bool_key} mismatch")
    assert_true(set(grouped_keys) == command_keys, "commands JSON groups should cover every command")
    assert_true(len(grouped_keys) == len(set(grouped_keys)), "commands JSON groups should not duplicate command keys")

    expected_by_stage: dict[str, list[str]] = {}
    for entry in payload["commands"]:
        expected_by_stage.setdefault(entry["stage"], []).append(entry["key"])
    assert_true(payload["by_stage"] == expected_by_stage, "commands JSON by_stage should match commands list")
    expected_counts = {
        "read_only": sum(
            1
            for entry in payload["commands"]
            if not entry["writes_project_state"]
            and not entry["writes_install_config"]
            and not entry["writes_pack_files"]
        ),
        "writes_project_state": sum(1 for entry in payload["commands"] if entry["writes_project_state"]),
        "writes_install_config": sum(1 for entry in payload["commands"] if entry["writes_install_config"]),
        "writes_pack_files": sum(1 for entry in payload["commands"] if entry["writes_pack_files"]),
        "requires_review": sum(1 for entry in payload["commands"] if entry["requires_review"]),
        "requires_author_confirmation": sum(1 for entry in payload["commands"] if entry["requires_author_confirmation"]),
    }
    assert_true(payload["write_scope_counts"] == expected_counts, "commands JSON write_scope_counts should match commands list")
    return {
        "schema_title": schema["title"],
        "stage_count": len(stage_values),
        "command_count": len(payload["commands"]),
        "group_count": len(payload["groups"]),
        "read_only_count": expected_counts["read_only"],
    }


def verify_desktop_commands_index_schema_payload(payload: dict[str, Any], schema: dict[str, Any], commands_payload: dict[str, Any]) -> dict[str, Any]:
    assert_true(schema.get("title") == "Long Novel Agent Desktop Pack Commands Index", "commands index schema title mismatch")
    assert_true(schema.get("type") == "object", "commands index schema should describe an object")
    top_required = set(schema.get("required") or [])
    for key in [
        "$schema",
        "type",
        "generated_at",
        "project_dir",
        "output_dir",
        "platform",
        "mode",
        "target_chapter",
        "local_only",
        "requires_server",
        "writes_project_state",
        "writes_install_config",
        "writes_pack_files",
        "records_real_client_evidence",
        "command_count",
        "group_count",
        "read_only_count",
        "write_scope_counts",
        "groups",
        "by_stage",
        "safety_notes",
        "source_files",
    ]:
        assert_true(key in top_required, f"commands index schema missed required field {key}")
        assert_true(key in payload, f"commands index JSON missed required field {key}")
    assert_true(payload["$schema"] == "commands-index.schema.json", "commands index JSON should point to local schema")
    assert_true(payload["type"] == "desktop_pack_commands_index", "commands index JSON wrong type")
    assert_true(payload["local_only"] is True, "commands index JSON should be local only")
    assert_true(payload["requires_server"] is False, "commands index JSON should not require a server")
    assert_true(payload["writes_project_state"] is False, "commands index JSON should be read-only")
    assert_true(payload["writes_install_config"] is False, "commands index JSON should not write install config")
    assert_true(payload["writes_pack_files"] is False, "commands index JSON should not write pack files")
    assert_true(payload["records_real_client_evidence"] is False, "commands index JSON should not record client evidence")
    assert_true(payload["command_count"] == commands_payload["command_count"], "commands index command count should match commands JSON")
    assert_true(payload["group_count"] == len(commands_payload["groups"]), "commands index group count should match commands JSON")
    assert_true(payload["read_only_count"] == commands_payload["write_scope_counts"]["read_only"], "commands index read-only count should match commands JSON")
    assert_true(payload["write_scope_counts"] == commands_payload["write_scope_counts"], "commands index write scope counts should match commands JSON")
    assert_true(payload["by_stage"] == commands_payload["by_stage"], "commands index by_stage should match commands JSON")
    assert_true({"commands.json", "commands.schema.json", "commands-index.schema.json", "pack-index.json"} <= set(payload["source_files"]), "commands index source files should include schemas and pack index")
    groups = payload.get("groups")
    assert_true(isinstance(groups, list) and groups, "commands index groups should be non-empty")
    commands_groups = {item["id"]: item for item in commands_payload["groups"]}
    grouped_keys: list[str] = []
    for group in groups:
        assert_true(isinstance(group, dict), "commands index group should be object")
        group_id = group.get("id")
        assert_true(group_id in commands_groups, f"commands index group {group_id} missing in commands JSON")
        command_group = commands_groups[group_id]
        assert_true(group.get("command_keys") == command_group.get("command_keys"), f"commands index group {group_id} keys should match commands JSON")
        assert_true(group.get("command_count") == len(group.get("command_keys", [])), f"commands index group {group_id} command_count mismatch")
        commands = group.get("commands")
        assert_true(isinstance(commands, list), f"commands index group {group_id} commands should be list")
        assert_true(len(commands) == len(group.get("command_keys", [])), f"commands index group {group_id} command summaries mismatch")
        grouped_keys.extend(str(key) for key in group.get("command_keys", []))
    assert_true(len(grouped_keys) == payload["command_count"], "commands index grouped key count should match command_count")
    assert_true(len(grouped_keys) == len(set(grouped_keys)), "commands index groups should not duplicate command keys")
    return {
        "schema_title": schema["title"],
        "command_count": payload["command_count"],
        "group_count": payload["group_count"],
        "read_only_count": payload["read_only_count"],
    }


def verify_desktop_pack_json_schemas(
    pack_index: dict[str, Any],
    pack_index_schema: dict[str, Any],
    commands_index: dict[str, Any],
    commands_index_schema: dict[str, Any],
    local_summary: dict[str, Any],
    local_summary_schema: dict[str, Any],
    first_three: dict[str, Any],
    first_three_schema: dict[str, Any],
    user_steps: dict[str, Any],
    user_steps_schema: dict[str, Any],
    desktop_next_step: dict[str, Any],
    next_step_schema: dict[str, Any],
    desktop_diagnostics: dict[str, Any],
    diagnostics_schema: dict[str, Any],
    desktop_pack_readiness: dict[str, Any],
    pack_readiness_schema: dict[str, Any],
    starter_brief: dict[str, Any],
    starter_brief_schema: dict[str, Any],
    agent_startup_prompt: dict[str, Any],
    agent_startup_prompt_schema: dict[str, Any],
    troubleshooting: dict[str, Any],
    troubleshooting_schema: dict[str, Any],
    agent_takeover: dict[str, Any],
    agent_takeover_schema: dict[str, Any],
    author_actions: dict[str, Any],
    author_actions_schema: dict[str, Any],
    acceptance_review: dict[str, Any],
    acceptance_review_schema: dict[str, Any],
    desktop_evidence_session: dict[str, Any],
    evidence_session_schema: dict[str, Any],
    desktop_evidence_runbook: dict[str, Any],
    evidence_runbook_schema: dict[str, Any],
    desktop_evidence_review: dict[str, Any],
    evidence_review_schema: dict[str, Any],
    desktop_evidence_gaps: dict[str, Any],
    writer_mode_approval: dict[str, Any],
    writer_mode_approval_schema: dict[str, Any],
    project_wizard: dict[str, Any],
    project_wizard_schema: dict[str, Any],
    source_intake: dict[str, Any],
    source_schema: dict[str, Any],
    chapter_session: dict[str, Any],
    chapter_schema: dict[str, Any],
    handoff_report: dict[str, Any],
    handoff_schema: dict[str, Any],
    results: dict[str, Any],
    results_schema: dict[str, Any],
) -> dict[str, Any]:
    assert_true(pack_index_schema.get("title") == "Long Novel Agent Desktop Pack Index", "pack index schema title mismatch")
    assert_true(commands_index_schema.get("title") == "Long Novel Agent Desktop Pack Commands Index", "commands index schema title mismatch")
    assert_true(local_summary_schema.get("title") == "Long Novel Agent Local Summary", "local summary schema title mismatch")
    assert_true(first_three_schema.get("title") == "Long Novel Agent First Three", "first three schema title mismatch")
    assert_true(user_steps_schema.get("title") == "Long Novel Agent User Steps", "user steps schema title mismatch")
    assert_true(next_step_schema.get("title") == "Long Novel Agent Desktop Next Step", "desktop next-step schema title mismatch")
    assert_true(diagnostics_schema.get("title") == "Long Novel Agent Desktop Diagnostics", "desktop diagnostics schema title mismatch")
    assert_true(pack_readiness_schema.get("title") == "Long Novel Agent Desktop Pack Readiness", "desktop pack readiness schema title mismatch")
    assert_true(starter_brief_schema.get("title") == "Long Novel Agent Starter Brief", "starter brief schema title mismatch")
    assert_true(agent_startup_prompt_schema.get("title") == "Long Novel Agent Startup Prompt", "agent startup prompt schema title mismatch")
    assert_true(troubleshooting_schema.get("title") == "Long Novel Agent Desktop Pack Troubleshooting", "desktop pack troubleshooting schema title mismatch")
    assert_true(agent_takeover_schema.get("title") == "Long Novel Agent Takeover Packet", "agent takeover schema title mismatch")
    assert_true(author_actions_schema.get("title") == "Long Novel Agent Author Actions", "author actions schema title mismatch")
    assert_true(acceptance_review_schema.get("title") == "Long Novel Agent Acceptance Review", "acceptance review schema title mismatch")
    assert_true(evidence_session_schema.get("title") == "Long Novel Agent Desktop Evidence Session", "desktop evidence session schema title mismatch")
    assert_true(evidence_runbook_schema.get("title") == "Long Novel Agent Desktop Evidence Runbook", "desktop evidence runbook schema title mismatch")
    assert_true(evidence_review_schema.get("title") == "Long Novel Agent Desktop Evidence Review", "desktop evidence review schema title mismatch")
    assert_true(writer_mode_approval_schema.get("title") == "Long Novel Agent Writer Mode Approval", "writer mode approval schema title mismatch")
    assert_true(project_wizard_schema.get("title") == "Long Novel Agent Project Wizard", "project wizard schema title mismatch")
    assert_true(source_schema.get("title") == "Long Novel Agent Source Intake", "source intake schema title mismatch")
    assert_true(chapter_schema.get("title") == "Long Novel Agent Chapter Session", "chapter session schema title mismatch")
    assert_true(handoff_schema.get("title") == "Long Novel Agent Handoff Report", "handoff report schema title mismatch")
    assert_true(results_schema.get("title") == "Long Novel Agent Desktop Verification Results", "desktop results schema title mismatch")

    index_required = set(pack_index_schema.get("required") or [])
    for key in ["$schema", "type", "version", "generated_at", "kit_version", "local_only", "requires_server", "writes_project_state", "writes_install_config", "records_real_client_evidence", "project", "status", "entrypoints", "startup_order", "machine_files", "commands", "write_boundaries", "files"]:
        assert_true(key in index_required, f"pack index schema missed required field {key}")
        assert_true(key in pack_index, f"pack index JSON missed required field {key}")
    assert_true(pack_index["$schema"] == "pack-index.schema.json", "pack index JSON should point to local schema")
    assert_true(pack_index["type"] == "desktop_pack_index", "pack index JSON wrong type")
    assert_true(pack_index["local_only"] is True, "pack index should be local only")
    assert_true(pack_index["requires_server"] is False, "pack index should not require a server")
    assert_true(pack_index["writes_project_state"] is False, "pack index should not write project state")
    assert_true(pack_index["writes_install_config"] is False, "pack index should not write install config")
    assert_true(pack_index["records_real_client_evidence"] is False, "pack index should not record client evidence")
    assert_true(pack_index["project"]["target_chapter"] >= 1, "pack index target chapter should be positive")
    entrypoint_names = {item.get("name") for item in pack_index.get("entrypoints", []) if isinstance(item, dict)}
    assert_true({"pack-index.json", "first-three.md", "first-three.json", "first-three.html", "first-three.schema.json", "START_HERE.md", "local-summary.md", "local-summary.json", "local-summary.html", "user-steps.md", "user-steps.json", "user-steps.html", "starter-brief.md", "starter-brief.json", "desktop-next-step.json", "desktop-diagnostics.md", "desktop-diagnostics.json", "desktop-diagnostics.schema.json", "desktop-pack-readiness.json", "agent-startup-prompt.json", "troubleshooting.json", "troubleshooting.html", "agent-takeover.json", "author-actions.json", "acceptance-review.json", "acceptance-review.html", "desktop-evidence-review.html", "index.html", "agent-start.md", "commands.json"} <= entrypoint_names, "pack index missed core entrypoints")
    startup_reads = [item.get("read") for item in pack_index.get("startup_order", []) if isinstance(item, dict)]
    assert_true(startup_reads[:3] == ["pack-index.json", "first-three.json", "START_HERE.md"], "pack index startup order mismatch")
    assert_true("first-three.json" in startup_reads, "pack index startup order missed first three JSON")
    assert_true("local-summary.json" in startup_reads, "pack index startup order missed local summary JSON")
    assert_true("user-steps.json" in startup_reads, "pack index startup order missed user steps JSON")
    assert_true("starter-brief.json" in startup_reads, "pack index startup order missed starter brief JSON")
    assert_true("desktop-next-step.json" in startup_reads, "pack index startup order missed desktop next step JSON")
    assert_true("desktop-diagnostics.json" in startup_reads, "pack index startup order missed desktop diagnostics JSON")
    assert_true("desktop-pack-readiness.json" in startup_reads, "pack index startup order missed desktop pack readiness JSON")
    assert_true("agent-startup-prompt.json" in startup_reads, "pack index startup order missed agent startup prompt JSON")
    assert_true("troubleshooting.json" in startup_reads, "pack index startup order missed troubleshooting JSON")
    assert_true("agent-takeover.json" in startup_reads, "pack index startup order missed agent takeover JSON")
    assert_true("author-actions.json" in startup_reads, "pack index startup order missed author actions JSON")
    assert_true("acceptance-review.json" in startup_reads, "pack index startup order missed acceptance review JSON")
    assert_true("desktop-evidence-session.json" in startup_reads, "pack index startup order missed desktop evidence session JSON")
    assert_true("desktop-evidence-runbook.json" in startup_reads, "pack index startup order missed desktop evidence runbook JSON")
    assert_true("desktop-evidence-review.json" in startup_reads, "pack index startup order missed desktop evidence review JSON")
    assert_true("desktop-evidence-gaps.json" in startup_reads, "pack index startup order missed desktop evidence gaps JSON")
    assert_true("writer-mode-approval.json" in startup_reads, "pack index startup order missed writer mode approval JSON")
    machine_files = pack_index.get("machine_files") if isinstance(pack_index.get("machine_files"), dict) else {}
    navigation_names = {item.get("name") for item in machine_files.get("navigation", []) if isinstance(item, dict)}
    status_names = {item.get("name") for item in machine_files.get("status", []) if isinstance(item, dict)}
    evidence_names = {item.get("name") for item in machine_files.get("evidence", []) if isinstance(item, dict)}
    assert_true("first-three.json" in navigation_names, "pack index missed first three JSON in navigation files")
    assert_true("first-three.md" in navigation_names, "pack index missed first three markdown in navigation files")
    assert_true("first-three.html" in navigation_names, "pack index missed first three HTML in navigation files")
    assert_true("first-three.schema.json" in navigation_names, "pack index missed first three schema in navigation files")
    assert_true("local-summary.json" in navigation_names, "pack index missed local summary JSON in navigation files")
    assert_true("local-summary.html" in navigation_names, "pack index missed local summary HTML in navigation files")
    assert_true("user-steps.json" in navigation_names, "pack index missed user steps JSON in navigation files")
    assert_true("user-steps.md" in navigation_names, "pack index missed user steps markdown in navigation files")
    assert_true("user-steps.html" in navigation_names, "pack index missed user steps HTML in navigation files")
    assert_true("starter-brief.json" in navigation_names, "pack index missed starter brief JSON in navigation files")
    assert_true("starter-brief.schema.json" in navigation_names, "pack index missed starter brief schema in navigation files")
    assert_true("desktop-diagnostics.json" in navigation_names, "pack index missed desktop diagnostics JSON in navigation files")
    assert_true("desktop-diagnostics.md" in navigation_names, "pack index missed desktop diagnostics markdown in navigation files")
    assert_true("desktop-diagnostics.schema.json" in navigation_names, "pack index missed desktop diagnostics schema in navigation files")
    assert_true("agent-startup-prompt.json" in navigation_names, "pack index missed agent startup prompt JSON in navigation files")
    assert_true("agent-startup-prompt.schema.json" in navigation_names, "pack index missed agent startup prompt schema in navigation files")
    assert_true("troubleshooting.json" in navigation_names, "pack index missed troubleshooting JSON in navigation files")
    assert_true("troubleshooting.html" in navigation_names, "pack index missed troubleshooting HTML in navigation files")
    assert_true("troubleshooting.schema.json" in navigation_names, "pack index missed troubleshooting schema in navigation files")
    assert_true("agent-takeover.json" in navigation_names, "pack index missed agent takeover JSON in navigation files")
    assert_true("agent-takeover.schema.json" in navigation_names, "pack index missed agent takeover schema in navigation files")
    assert_true("author-actions.json" in navigation_names, "pack index missed author actions JSON in navigation files")
    assert_true("author-actions.schema.json" in navigation_names, "pack index missed author actions schema in navigation files")
    assert_true("acceptance-review.json" in navigation_names, "pack index missed acceptance review JSON in navigation files")
    assert_true("acceptance-review.html" in navigation_names, "pack index missed acceptance review HTML in navigation files")
    assert_true("acceptance-review.schema.json" in navigation_names, "pack index missed acceptance review schema in navigation files")
    assert_true("writer-mode-approval.json" in navigation_names, "pack index missed writer mode approval JSON in navigation files")
    assert_true("writer-mode-approval.schema.json" in navigation_names, "pack index missed writer mode approval schema in navigation files")
    assert_true("desktop-next-step.json" in status_names, "pack index missed desktop next step status file")
    assert_true("desktop-diagnostics.json" in status_names, "pack index missed desktop diagnostics status file")
    assert_true("first-three.json" in status_names, "pack index missed first three status file")
    assert_true("local-summary.json" in status_names, "pack index missed local summary status file")
    assert_true("user-steps.json" in status_names, "pack index missed user steps status file")
    assert_true("desktop-pack-readiness.json" in status_names, "pack index missed desktop pack readiness status file")
    assert_true("starter-brief.json" in status_names, "pack index missed starter brief status file")
    assert_true("agent-startup-prompt.json" in status_names, "pack index missed agent startup prompt status file")
    assert_true("agent-takeover.json" in status_names, "pack index missed agent takeover status file")
    assert_true("author-actions.json" in status_names, "pack index missed author actions status file")
    assert_true("acceptance-review.json" in status_names, "pack index missed acceptance review status file")
    assert_true("desktop-evidence-session.json" in evidence_names, "pack index missed desktop evidence session file")
    assert_true("desktop-evidence-session.schema.json" in evidence_names, "pack index missed desktop evidence session schema in evidence files")
    assert_true("desktop-evidence-runbook.json" in evidence_names, "pack index missed desktop evidence runbook file")
    assert_true("desktop-evidence-runbook.md" in evidence_names, "pack index missed desktop evidence runbook markdown file")
    assert_true("desktop-evidence-review.json" in evidence_names, "pack index missed desktop evidence review file")
    assert_true("desktop-evidence-review.html" in evidence_names, "pack index missed desktop evidence review HTML file")
    assert_true("desktop-evidence-review.schema.json" in evidence_names, "pack index missed desktop evidence review schema in evidence files")
    assert_true("desktop-evidence-gaps.json" in evidence_names, "pack index missed desktop evidence gaps file")
    assert_true("desktop-evidence-gaps.md" in evidence_names, "pack index missed desktop evidence gaps markdown file")
    assert_true("writer-mode-approval.json" in evidence_names, "pack index missed writer mode approval JSON in evidence files")
    assert_true("writer-mode-approval.schema.json" in evidence_names, "pack index missed writer mode approval schema in evidence files")
    index_schema_names = {item.get("name") for item in machine_files.get("schemas", []) if isinstance(item, dict)}
    assert_true("pack-index.schema.json" in index_schema_names, "pack index missed its schema file")
    assert_true("commands-index.schema.json" in index_schema_names, "pack index missed commands index schema file")
    assert_true("first-three.schema.json" in index_schema_names, "pack index missed first three schema file")
    assert_true("local-summary.schema.json" in index_schema_names, "pack index missed local summary schema file")
    assert_true("user-steps.schema.json" in index_schema_names, "pack index missed user steps schema file")
    assert_true("starter-brief.schema.json" in index_schema_names, "pack index missed starter brief schema file")
    assert_true("desktop-next-step.schema.json" in index_schema_names, "pack index missed desktop next-step schema file")
    assert_true("desktop-diagnostics.schema.json" in index_schema_names, "pack index missed desktop diagnostics schema file")
    assert_true("desktop-pack-readiness.schema.json" in index_schema_names, "pack index missed desktop pack readiness schema file")
    assert_true("agent-startup-prompt.schema.json" in index_schema_names, "pack index missed agent startup prompt schema file")
    assert_true("troubleshooting.schema.json" in index_schema_names, "pack index missed troubleshooting schema file")
    assert_true("agent-takeover.schema.json" in index_schema_names, "pack index missed agent takeover schema file")
    assert_true("author-actions.schema.json" in index_schema_names, "pack index missed author actions schema file")
    assert_true("acceptance-review.schema.json" in index_schema_names, "pack index missed acceptance review schema file")
    assert_true("desktop-evidence-session.schema.json" in index_schema_names, "pack index missed desktop evidence session schema file")
    assert_true("desktop-evidence-review.schema.json" in index_schema_names, "pack index missed desktop evidence review schema file")
    assert_true("writer-mode-approval.schema.json" in index_schema_names, "pack index missed writer mode approval schema file")
    pack_index_files = pack_index.get("files") if isinstance(pack_index.get("files"), dict) else {}
    pack_index_present_files = set(pack_index_files.get("present") or [])
    assert_true("commands-index.schema.json" in pack_index_present_files, "pack index files list missed commands index schema")
    assert_true("desktop-next-step.schema.json" in pack_index_present_files, "pack index files list missed desktop next-step schema")
    assert_true("desktop-diagnostics.md" in pack_index_present_files and "desktop-diagnostics.json" in pack_index_present_files and "desktop-diagnostics.schema.json" in pack_index_present_files, "pack index files list missed desktop diagnostics files")
    assert_true("desktop-pack-readiness.schema.json" in pack_index_present_files, "pack index files list missed desktop pack readiness schema")
    assert_true("first-three.md" in pack_index_present_files and "first-three.json" in pack_index_present_files and "first-three.html" in pack_index_present_files and "first-three.schema.json" in pack_index_present_files, "pack index files list missed first three files")
    assert_true("local-summary.md" in pack_index_present_files and "local-summary.json" in pack_index_present_files and "local-summary.html" in pack_index_present_files and "local-summary.schema.json" in pack_index_present_files, "pack index files list missed local summary files")
    assert_true(
        "user-steps.md" in pack_index_present_files
        and "user-steps.json" in pack_index_present_files
        and "user-steps.html" in pack_index_present_files
        and "user-steps.schema.json" in pack_index_present_files,
        "pack index files list missed user steps files",
    )
    assert_true("starter-brief.json" in pack_index_present_files, "pack index files list missed starter brief JSON")
    assert_true("starter-brief.schema.json" in pack_index_present_files, "pack index files list missed starter brief schema")
    assert_true("agent-startup-prompt.json" in pack_index_present_files, "pack index files list missed agent startup prompt JSON")
    assert_true("agent-startup-prompt.schema.json" in pack_index_present_files, "pack index files list missed agent startup prompt schema")
    assert_true("troubleshooting.json" in pack_index_present_files, "pack index files list missed troubleshooting JSON")
    assert_true("troubleshooting.html" in pack_index_present_files, "pack index files list missed troubleshooting HTML")
    assert_true("troubleshooting.schema.json" in pack_index_present_files, "pack index files list missed troubleshooting schema")
    assert_true("agent-takeover.json" in pack_index_present_files, "pack index files list missed agent takeover JSON")
    assert_true("agent-takeover.schema.json" in pack_index_present_files, "pack index files list missed agent takeover schema")
    assert_true(
        "acceptance-review.md" in pack_index_present_files
        and "acceptance-review.json" in pack_index_present_files
        and "acceptance-review.html" in pack_index_present_files
        and "acceptance-review.schema.json" in pack_index_present_files,
        "pack index files list missed acceptance review files",
    )
    assert_true("desktop-evidence-session.schema.json" in pack_index_present_files, "pack index files list missed desktop evidence session schema")
    assert_true("desktop-evidence-runbook.md" in pack_index_present_files and "desktop-evidence-runbook.json" in pack_index_present_files, "pack index files list missed desktop evidence runbook files")
    assert_true(
        "desktop-evidence-review.md" in pack_index_present_files
        and "desktop-evidence-review.json" in pack_index_present_files
        and "desktop-evidence-review.html" in pack_index_present_files
        and "desktop-evidence-review.schema.json" in pack_index_present_files,
        "pack index files list missed desktop evidence review files",
    )
    assert_true("writer-mode-approval.json" in pack_index_present_files, "pack index files list missed writer mode approval JSON")
    assert_true("writer-mode-approval.schema.json" in pack_index_present_files, "pack index files list missed writer mode approval schema")
    important_commands = pack_index.get("commands", {}).get("important") if isinstance(pack_index.get("commands"), dict) else []
    important_by_key = {item.get("key"): item for item in important_commands if isinstance(item, dict)}
    assert_true(important_by_key.get("local_summary", {}).get("present") is True, "pack index should list local_summary as an important command")
    assert_true(important_by_key.get("desktop_user_steps", {}).get("present") is True, "pack index should list desktop_user_steps as an important command")
    assert_true(important_by_key.get("agent_startup_prompt", {}).get("present") is True, "pack index should list agent_startup_prompt as an important command")
    assert_true(important_by_key.get("desktop_next_step", {}).get("present") is True, "pack index should list desktop_next_step as an important command")
    assert_true(important_by_key.get("desktop_diagnostics", {}).get("present") is True, "pack index should list desktop_diagnostics as an important command")
    assert_true(important_by_key.get("desktop_evidence_runbook", {}).get("present") is True, "pack index should list desktop_evidence_runbook as an important command")
    assert_true(important_by_key.get("record_agent_activity", {}).get("writes_project_state") is True, "pack index should mark activity record as project-state write")
    boundaries = pack_index.get("write_boundaries") if isinstance(pack_index.get("write_boundaries"), dict) else {}
    assert_true(boundaries.get("pack_index_is_read_only") is True, "pack index schema should require read-only index")
    assert_true("record_agent_activity" in boundaries.get("read_only_agents_must_not_execute", []), "pack index should list activity write boundary")

    diagnostics_required = set(diagnostics_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "platform", "mode", "tool_profile", "project_dir", "target_chapter", "config_path", "pack_dir", "status", "next_step_status", "primary_action", "checks", "blockers", "review_items", "warnings", "recommended_actions", "recommended_commands", "reports", "notes"]:
        assert_true(key in diagnostics_required, f"desktop diagnostics schema missed required field {key}")
        assert_true(key in desktop_diagnostics, f"desktop diagnostics JSON missed required field {key}")
    assert_true(desktop_diagnostics["$schema"] == "desktop-diagnostics.schema.json", "desktop diagnostics JSON should point to local schema")
    assert_true(desktop_diagnostics["type"] == "desktop_diagnostics", "desktop diagnostics JSON wrong type")
    assert_true(desktop_diagnostics["local_only"] is True, "desktop diagnostics should be local only")
    assert_true(desktop_diagnostics["requires_server"] is False, "desktop diagnostics should not require server")
    assert_true(desktop_diagnostics["would_write_project_state"] is False, "desktop diagnostics should not write project state")
    assert_true(desktop_diagnostics["would_write_install_config"] is False, "desktop diagnostics should not write install config")
    assert_true(desktop_diagnostics["would_write_pack_files"] is False, "desktop diagnostics should not write pack files")
    assert_true(desktop_diagnostics["records_real_client_evidence"] is False, "desktop diagnostics should not record real client evidence")
    assert_true(isinstance(desktop_diagnostics["target_chapter"], int) and desktop_diagnostics["target_chapter"] >= 1, "desktop diagnostics target chapter should be positive")
    assert_true(desktop_diagnostics["status"] in {"ready", "ready_with_warnings", "needs_review", "blocked"}, "desktop diagnostics status should be allowed")
    diagnostics_checks = {item.get("id"): item for item in desktop_diagnostics.get("checks", []) if isinstance(item, dict)}
    assert_true({"local_mcp_config", "project_state", "project_readiness", "author_review_queue", "real_desktop_client_evidence"} <= set(diagnostics_checks), "desktop diagnostics missed core checks")
    for check_id, item in diagnostics_checks.items():
        assert_true(isinstance(item.get("ok"), bool), f"desktop diagnostics check {check_id} missed boolean ok")
        assert_true(item.get("severity") in {"blocker", "review", "warning"}, f"desktop diagnostics check {check_id} missed severity")
        assert_true(bool(item.get("message")), f"desktop diagnostics check {check_id} missed message")
    assert_true(isinstance(desktop_diagnostics.get("reports"), dict), "desktop diagnostics missed reports object")
    assert_true("desktop_next_step" in desktop_diagnostics["reports"], "desktop diagnostics missed nested desktop next step report")
    assert_true(any("read-only" in item.lower() or "read only" in item.lower() for item in desktop_diagnostics.get("notes", [])), "desktop diagnostics notes should state read-only behavior")

    local_summary_required = set(local_summary_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "status", "headline", "project_dir", "output_dir", "platform", "mode", "tool_profile", "target_chapter", "summary", "cards", "quick_links", "read_first", "commands", "next_actions", "read_errors", "notes"]:
        assert_true(key in local_summary_required, f"local summary schema missed required field {key}")
        assert_true(key in local_summary, f"local summary JSON missed required field {key}")
    assert_true(local_summary["$schema"] == "local-summary.schema.json", "local summary JSON should point to local schema")
    assert_true(local_summary["type"] == "desktop_local_summary", "local summary JSON wrong type")
    assert_true(local_summary["local_only"] is True, "local summary should be local only")
    assert_true(local_summary["requires_server"] is False, "local summary should not require server")
    assert_true(local_summary["would_write_project_state"] is False, "local summary should be read-only")
    assert_true(local_summary["would_write_install_config"] is False, "local summary should not write install config")
    assert_true(local_summary["would_write_pack_files"] is False, "local summary should not write pack files")
    assert_true(local_summary["records_real_client_evidence"] is False, "local summary should not record real client evidence")
    assert_true(isinstance(local_summary["target_chapter"], int) and local_summary["target_chapter"] >= 1, "local summary target chapter should be positive")
    assert_true(local_summary["status"] in {"needs_pack_repair", "needs_initialization", "needs_fresh_pack", "needs_author_review", "needs_real_client_evidence", "ready_with_warnings", "ready_to_start"}, "local summary status should be allowed")
    local_summary_summary = local_summary.get("summary") if isinstance(local_summary.get("summary"), dict) else {}
    for key in ["project_readiness", "desktop_next_step_status", "desktop_pack_readiness_status", "pack_freshness_status", "chapter_session_freshness_status", "author_review_queue_status", "desktop_evidence_review_status", "blocker_count", "warning_count"]:
        assert_true(key in local_summary_summary, f"local summary missed summary field {key}")
    local_summary_cards = {item.get("id") for item in local_summary.get("cards", []) if isinstance(item, dict)}
    assert_true({"local_install", "pack_integrity", "freshness", "project_state", "author_review", "real_client_evidence"} <= local_summary_cards, "local summary missed required status cards")
    expected_quick_links = {"five_step_path", "inspect_pack", "initialize_project", "author_actions", "acceptance_review", "collect_evidence", "start_chapter"}
    local_summary_quick_links = {item.get("id"): item for item in local_summary.get("quick_links", []) if isinstance(item, dict)}
    assert_true(expected_quick_links <= set(local_summary_quick_links), "local summary missed required quick links")
    assert_true(sum(1 for item in local_summary_quick_links.values() if item.get("recommended") is True) == 1, "local summary should mark exactly one recommended quick link")
    for link_id in expected_quick_links:
        item = local_summary_quick_links[link_id]
        assert_true(bool(item.get("href")) and bool(item.get("file")), f"local summary quick link {link_id} missed href or file")
        assert_true(item.get("writes_project_state") is False, f"local summary quick link {link_id} should not write project state")
        assert_true(item.get("writes_install_config") is False, f"local summary quick link {link_id} should not write install config")
        assert_true(item.get("writes_pack_files") is False, f"local summary quick link {link_id} should not write pack files")
        assert_true(item.get("records_real_client_evidence") is False, f"local summary quick link {link_id} should not record evidence")
    local_summary_commands = {item.get("key"): item for item in local_summary.get("commands", []) if isinstance(item, dict)}
    for key in ["local_summary", "pack_doctor_json", "desktop_user_steps", "desktop_next_step", "quickstart_from_wizard_dry_run", "author_actions", "acceptance_review", "desktop_evidence_runbook", "desktop_evidence_review", "prepare_session"]:
        assert_true(local_summary_commands.get(key, {}).get("present") is True, f"local summary missed command reference {key}")
    assert_true(isinstance(local_summary.get("next_actions"), list) and local_summary["next_actions"], "local summary missed next actions")
    local_summary_defs = local_summary_schema.get("$defs") if isinstance(local_summary_schema.get("$defs"), dict) else {}
    assert_true("card" in local_summary_defs, "local summary schema missed card definition")
    assert_true("quickLink" in local_summary_defs, "local summary schema missed quickLink definition")
    assert_true("commandRef" in local_summary_defs, "local summary schema missed commandRef definition")

    first_three_required = set(first_three_schema.get("required") or [])
    for key in ["$schema", "type", "ok", "generated_at", "kit_version", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "output_dir", "platform", "mode", "tool_profile", "target_chapter", "status", "headline", "primary_action", "summary", "files", "read_after_three", "write_boundaries", "notes"]:
        assert_true(key in first_three_required, f"first three schema missed required field {key}")
        assert_true(key in first_three, f"first three JSON missed required field {key}")
    assert_true(first_three["$schema"] == "first-three.schema.json", "first three JSON should point to local schema")
    assert_true(first_three["type"] == "desktop_first_three", "first three JSON wrong type")
    assert_true(first_three["local_only"] is True, "first three should be local only")
    assert_true(first_three["requires_server"] is False, "first three should not require server")
    assert_true(first_three["would_write_project_state"] is False, "first three should be read-only")
    assert_true(first_three["would_write_install_config"] is False, "first three should not write install config")
    assert_true(first_three["would_write_pack_files"] is False, "first three should not write pack files")
    assert_true(first_three["records_real_client_evidence"] is False, "first three should not record real client evidence")
    assert_true(isinstance(first_three["target_chapter"], int) and first_three["target_chapter"] >= 1, "first three target chapter should be positive")
    first_three_files = first_three.get("files") if isinstance(first_three.get("files"), list) else []
    first_three_file_names = {item.get("file") for item in first_three_files if isinstance(item, dict)}
    assert_true(len(first_three_files) == 3, "first three should list exactly three files")
    assert_true({"user-steps.html", "local-summary.html", "agent-startup-prompt.md"} <= first_three_file_names, "first three missed expected files")
    first_three_boundaries = first_three.get("write_boundaries") if isinstance(first_three.get("write_boundaries"), dict) else {}
    assert_true(first_three_boundaries.get("first_three_is_read_only") is True, "first three missed read-only boundary")
    assert_true(first_three_boundaries.get("do_not_execute_writer_commands_from_this_file") is True, "first three missed writer command boundary")
    first_three_defs = first_three_schema.get("$defs") if isinstance(first_three_schema.get("$defs"), dict) else {}
    assert_true("fileEntry" in first_three_defs, "first three schema missed fileEntry definition")

    user_steps_required = set(user_steps_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "output_dir", "platform", "mode", "tool_profile", "target_chapter", "status", "headline", "primary_action", "summary", "step_count", "steps", "read_first", "write_boundaries", "source_files"]:
        assert_true(key in user_steps_required, f"user steps schema missed required field {key}")
        assert_true(key in user_steps, f"user steps JSON missed required field {key}")
    assert_true(user_steps["$schema"] == "user-steps.schema.json", "user steps JSON should point to local schema")
    assert_true(user_steps["type"] == "desktop_user_steps", "user steps JSON wrong type")
    assert_true(user_steps["local_only"] is True, "user steps should be local only")
    assert_true(user_steps["requires_server"] is False, "user steps should not require server")
    assert_true(user_steps["would_write_project_state"] is False, "user steps should be read-only")
    assert_true(user_steps["would_write_install_config"] is False, "user steps should not write install config")
    assert_true(user_steps["would_write_pack_files"] is False, "user steps should not write pack files")
    assert_true(user_steps["records_real_client_evidence"] is False, "user steps should not record real client evidence")
    assert_true(isinstance(user_steps["target_chapter"], int) and user_steps["target_chapter"] >= 1, "user steps target chapter should be positive")
    assert_true(user_steps["step_count"] == 5 and len(user_steps["steps"]) == 5, "user steps should contain five steps")
    assert_true([item.get("index") for item in user_steps["steps"]] == [1, 2, 3, 4, 5], "user steps should be ordered from 1 to 5")
    user_step_files = set(user_steps.get("source_files") or [])
    assert_true({"user-steps.md", "user-steps.json", "user-steps.html", "user-steps.schema.json"} <= user_step_files, "user steps missed source files")
    user_steps_boundaries = user_steps.get("write_boundaries") if isinstance(user_steps.get("write_boundaries"), dict) else {}
    assert_true(user_steps_boundaries.get("user_steps_is_read_only") is True, "user steps missed read-only boundary")
    assert_true(user_steps_boundaries.get("remote_connector_only_platforms_are_outside_this_local_flow") is True, "user steps missed remote connector boundary")
    user_steps_defs = user_steps_schema.get("$defs") if isinstance(user_steps_schema.get("$defs"), dict) else {}
    assert_true("step" in user_steps_defs, "user steps schema missed step definition")
    assert_true("commandRef" in user_steps_defs, "user steps schema missed commandRef definition")

    commands_index_required = set(commands_index_schema.get("required") or [])
    for key in ["$schema", "type", "generated_at", "project_dir", "output_dir", "platform", "mode", "target_chapter", "local_only", "requires_server", "writes_project_state", "writes_install_config", "writes_pack_files", "records_real_client_evidence", "command_count", "group_count", "read_only_count", "write_scope_counts", "groups", "by_stage", "safety_notes", "source_files"]:
        assert_true(key in commands_index_required, f"commands index schema missed required field {key}")
        assert_true(key in commands_index, f"commands index JSON missed required field {key}")
    assert_true(commands_index["$schema"] == "commands-index.schema.json", "commands index JSON should point to local schema")
    assert_true(commands_index["type"] == "desktop_pack_commands_index", "commands index JSON wrong type")
    assert_true(commands_index["local_only"] is True and commands_index["requires_server"] is False, "commands index JSON should describe a local static file")
    assert_true(commands_index["writes_project_state"] is False, "commands index JSON should be read-only")
    assert_true(commands_index["writes_install_config"] is False, "commands index JSON should not write install config")
    assert_true(commands_index["writes_pack_files"] is False, "commands index JSON should not write pack files")
    assert_true(commands_index["records_real_client_evidence"] is False, "commands index JSON should not record desktop evidence")
    assert_true(isinstance(commands_index["groups"], list) and commands_index["groups"], "commands index JSON should have groups")
    assert_true(commands_index["group_count"] == len(commands_index["groups"]), "commands index JSON group_count should match groups")
    assert_true({"commands.json", "commands.schema.json", "commands-index.schema.json", "pack-index.json"} <= set(commands_index["source_files"]), "commands index JSON missed source file references")

    next_step_required = set(next_step_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "platform", "mode", "tool_profile", "project_dir", "target_chapter", "status", "primary_action", "actions", "state_initialized", "verification_status", "recommended_commands", "notes"]:
        assert_true(key in next_step_required, f"desktop next-step schema missed required field {key}")
        assert_true(key in desktop_next_step, f"desktop-next-step JSON missed required field {key}")
    assert_true(desktop_next_step["$schema"] == "desktop-next-step.schema.json", "desktop-next-step JSON should point to local schema")
    assert_true(desktop_next_step["type"] == "desktop_next_step", "desktop-next-step JSON wrong type")
    assert_true(desktop_next_step["local_only"] is True and desktop_next_step["requires_server"] is False, "desktop-next-step JSON should describe local-only static decision")
    assert_true(desktop_next_step["would_write_project_state"] is False, "desktop-next-step JSON should be read-only")
    assert_true(desktop_next_step["would_write_install_config"] is False, "desktop-next-step JSON should not write install config")
    assert_true(desktop_next_step["would_write_pack_files"] is False, "desktop-next-step JSON should not write pack files")
    assert_true(desktop_next_step["records_real_client_evidence"] is False, "desktop-next-step JSON should not record desktop evidence")
    assert_true(isinstance(desktop_next_step["target_chapter"], int) and desktop_next_step["target_chapter"] >= 1, "desktop-next-step JSON target chapter should be positive")
    assert_true(isinstance(desktop_next_step.get("primary_action"), dict) and desktop_next_step["primary_action"].get("id"), "desktop-next-step JSON missed primary action")
    assert_true(isinstance(desktop_next_step.get("actions"), list) and desktop_next_step["actions"], "desktop-next-step JSON missed follow-up actions")
    action_defs = next_step_schema.get("$defs", {}).get("action", {}).get("required", [])
    assert_true({"id", "title", "reason", "command", "file", "writes_project_state", "writes_install_config", "writes_pack_files", "records_real_client_evidence", "requires_author_confirmation", "requires_review"} <= set(action_defs), "desktop next-step schema missed action required fields")

    pack_readiness_required = set(pack_readiness_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "platform", "mode", "project_dir", "output_dir", "target_chapter", "status", "summary", "checks", "missing_files", "missing_commands", "recommended_commands", "next_actions"]:
        assert_true(key in pack_readiness_required, f"desktop pack readiness schema missed required field {key}")
        assert_true(key in desktop_pack_readiness, f"desktop pack readiness JSON missed required field {key}")
    assert_true(desktop_pack_readiness["$schema"] == "desktop-pack-readiness.schema.json", "desktop pack readiness JSON should point to local schema")
    assert_true(desktop_pack_readiness["type"] == "desktop_pack_readiness", "desktop pack readiness JSON wrong type")
    assert_true(desktop_pack_readiness["status"] in {"ready_to_share", "ready_with_warnings", "needs_initialization", "needs_author_review", "needs_real_client_evidence", "needs_pack_repair", "blocked"}, "desktop pack readiness status not allowed")
    assert_true(desktop_pack_readiness["local_only"] is True, "desktop pack readiness should be local only")
    assert_true(desktop_pack_readiness["requires_server"] is False, "desktop pack readiness should not require server")
    assert_true(desktop_pack_readiness["would_write_project_state"] is False, "desktop pack readiness should be read-only")
    assert_true(desktop_pack_readiness["would_write_install_config"] is False, "desktop pack readiness should not write install config")
    assert_true(desktop_pack_readiness["would_write_pack_files"] is False, "desktop pack readiness should not write pack files")
    assert_true(desktop_pack_readiness["records_real_client_evidence"] is False, "desktop pack readiness should not record real client evidence")
    assert_true(isinstance(desktop_pack_readiness["target_chapter"], int) and desktop_pack_readiness["target_chapter"] >= 1, "desktop pack readiness target chapter should be positive")
    assert_true(isinstance(desktop_pack_readiness["checks"], list) and desktop_pack_readiness["checks"], "desktop pack readiness missed checks")
    readiness_check_ids = {item.get("id") for item in desktop_pack_readiness["checks"] if isinstance(item, dict)}
    assert_true({"required_files", "required_commands", "local_only_boundaries", "project_initialized", "real_client_evidence", "pack_freshness", "chapter_session_freshness"} <= readiness_check_ids, "desktop pack readiness missed core checks")
    assert_true(isinstance(desktop_pack_readiness["missing_files"], list), "desktop pack readiness missing_files should be list")
    assert_true(isinstance(desktop_pack_readiness["missing_commands"], list), "desktop pack readiness missing_commands should be list")

    starter_brief_required = set(starter_brief_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "output_dir", "platform", "mode", "target_chapter", "status", "can_draft", "summary", "next_action", "do_first", "first_files", "read_before_writing", "commands", "write_boundaries", "source_files"]:
        assert_true(key in starter_brief_required, f"starter brief schema missed required field {key}")
        assert_true(key in starter_brief, f"starter brief JSON missed required field {key}")
    assert_true(starter_brief["$schema"] == "starter-brief.schema.json", "starter brief JSON should point to local schema")
    assert_true(starter_brief["type"] == "starter_brief", "starter brief JSON wrong type")
    assert_true(starter_brief["status"] in {"needs_pack_repair", "needs_initialization", "needs_continuity_fix", "needs_author_review", "ready_to_start", "ready_for_read_only_start"}, "starter brief status not allowed")
    assert_true(starter_brief["local_only"] is True, "starter brief should be local only")
    assert_true(starter_brief["requires_server"] is False, "starter brief should not require server")
    assert_true(starter_brief["would_write_project_state"] is False, "starter brief should be read-only")
    assert_true(starter_brief["would_write_install_config"] is False, "starter brief should not write install config")
    assert_true(starter_brief["would_write_pack_files"] is False, "starter brief should not write pack files")
    assert_true(starter_brief["records_real_client_evidence"] is False, "starter brief should not record real client evidence")
    assert_true(isinstance(starter_brief["target_chapter"], int) and starter_brief["target_chapter"] >= 1, "starter brief target chapter should be positive")
    starter_first_files = {item.get("file") for item in starter_brief.get("first_files", []) if isinstance(item, dict)}
    assert_true({"starter-brief.json", "agent-startup-prompt.json", "agent-takeover.json", "metadata.json", "chapter-session.json", "commands-index.json"} <= starter_first_files, "starter brief missed first files")
    starter_before = set(starter_brief.get("read_before_writing") or [])
    assert_true({"chapter-session.json", "continuity-audit.json", "author-review-queue.json", "handoff-brief.json", "metadata.json", "agent-takeover.json", "commands-index.json"} <= starter_before, "starter brief missed before-writing files")
    assert_true(starter_brief["summary"].get("pack_freshness_status") == "fresh", "starter brief missed pack freshness summary")
    assert_true(starter_brief["summary"].get("chapter_session_freshness_status") == "fresh", "starter brief missed chapter session freshness summary")
    starter_commands = starter_brief.get("commands") if isinstance(starter_brief.get("commands"), dict) else {}
    for key in ["starter_brief", "desktop_pack_readiness", "pack_freshness", "chapter_session_freshness", "agent_startup_prompt", "agent_takeover", "prepare_session", "context_brief"]:
        assert_true(starter_commands.get(key, {}).get("present") is True, f"starter brief missed command {key}")
    starter_boundaries = starter_brief.get("write_boundaries") if isinstance(starter_brief.get("write_boundaries"), dict) else {}
    assert_true(starter_boundaries.get("starter_brief_is_read_only") is True, "starter brief missed read-only boundary")
    assert_true(starter_boundaries.get("do_not_write_project_state_from_this_file") is True, "starter brief missed no-write boundary")
    assert_true(starter_boundaries.get("writer_actions_need_author_confirmation") is True, "starter brief missed author confirmation boundary")
    assert_true("record_agent_activity" in starter_boundaries.get("read_only_agents_must_not_execute", []), "starter brief should block read-only activity writes")
    assert_true("starter-brief.md" in starter_brief["source_files"], "starter brief missed markdown source file")
    assert_true("starter-brief.schema.json" in starter_brief["source_files"], "starter brief missed schema source file")
    starter_defs = starter_brief_schema.get("$defs") if isinstance(starter_brief_schema.get("$defs"), dict) else {}
    assert_true("fileRef" in starter_defs, "starter brief schema missed fileRef definition")
    assert_true("commandRef" in starter_defs, "starter brief schema missed commandRef definition")
    starter_summary_required = set((starter_brief_schema.get("properties") or {}).get("summary", {}).get("required", []))
    assert_true("chapter_session_freshness_status" in starter_summary_required, "starter brief schema missed chapter session freshness summary")

    agent_startup_required = set(agent_startup_prompt_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "platform", "mode", "target_chapter", "status", "copy_prompt", "summary", "read_first", "tool_gates", "write_boundaries", "commands", "source_files"]:
        assert_true(key in agent_startup_required, f"agent startup prompt schema missed required field {key}")
        assert_true(key in agent_startup_prompt, f"agent startup prompt JSON missed required field {key}")
    assert_true(agent_startup_prompt["$schema"] == "agent-startup-prompt.schema.json", "agent startup prompt JSON should point to local schema")
    assert_true(agent_startup_prompt["type"] == "agent_startup_prompt", "agent startup prompt JSON wrong type")
    assert_true(agent_startup_prompt["local_only"] is True, "agent startup prompt should be local only")
    assert_true(agent_startup_prompt["requires_server"] is False, "agent startup prompt should not require server")
    assert_true(agent_startup_prompt["would_write_project_state"] is False, "agent startup prompt should be read-only")
    assert_true(agent_startup_prompt["would_write_install_config"] is False, "agent startup prompt should not write install config")
    assert_true(agent_startup_prompt["would_write_pack_files"] is False, "agent startup prompt should not write pack files")
    assert_true(agent_startup_prompt["records_real_client_evidence"] is False, "agent startup prompt should not record real client evidence")
    assert_true(isinstance(agent_startup_prompt["target_chapter"], int) and agent_startup_prompt["target_chapter"] >= 1, "agent startup prompt target chapter should be positive")
    assert_true(agent_startup_prompt["status"] in {"ready_to_prompt", "ready_for_read_only_start", "needs_author_review", "needs_continuity_fix", "needs_initialization", "needs_pack_repair"}, "agent startup prompt status not allowed")
    assert_true(isinstance(agent_startup_prompt["copy_prompt"], str) and "build_agent_startup_prompt" in agent_startup_prompt["copy_prompt"], "agent startup prompt missed copy prompt instructions")
    startup_read_files = {item.get("file") for item in agent_startup_prompt.get("read_first", []) if isinstance(item, dict)}
    assert_true({"agent-startup-prompt.json", "agent-takeover.json", "metadata.json", "desktop-next-step.json", "chapter-session.json", "commands-index.json", "commands.json"} <= startup_read_files, "agent startup prompt missed read-first files")
    assert_true(agent_startup_prompt["summary"].get("pack_freshness_status") == "fresh", "agent startup prompt missed pack freshness summary")
    assert_true(agent_startup_prompt["summary"].get("chapter_session_freshness_status") == "fresh", "agent startup prompt missed chapter session freshness summary")
    startup_summary_required = set((agent_startup_prompt_schema.get("properties") or {}).get("summary", {}).get("required", []))
    assert_true("chapter_session_freshness_status" in startup_summary_required, "agent startup prompt schema missed chapter session freshness summary")
    startup_boundaries = agent_startup_prompt.get("write_boundaries") if isinstance(agent_startup_prompt.get("write_boundaries"), dict) else {}
    assert_true(startup_boundaries.get("agent_startup_prompt_is_read_only") is True, "agent startup prompt missed read-only boundary")
    assert_true(startup_boundaries.get("do_not_write_project_state_from_this_prompt") is True, "agent startup prompt missed no-write boundary")
    assert_true("record_agent_activity" in startup_boundaries.get("read_only_agents_must_not_execute", []), "agent startup prompt should block read-only activity writes")
    startup_commands = agent_startup_prompt.get("commands") if isinstance(agent_startup_prompt.get("commands"), dict) else {}
    for key in ["agent_startup_prompt", "pack_freshness", "chapter_session_freshness", "write_session_check", "desktop_next_step", "prepare_session", "context_brief", "chapter_delivery", "record_agent_activity"]:
        assert_true(startup_commands.get(key, {}).get("present") is True, f"agent startup prompt missed command {key}")
    assert_true("agent-startup-prompt.md" in agent_startup_prompt["source_files"], "agent startup prompt missed markdown source file")
    assert_true("agent-startup-prompt.schema.json" in agent_startup_prompt["source_files"], "agent startup prompt missed schema source file")

    troubleshooting_required = set(troubleshooting_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "output_dir", "platform", "mode", "target_chapter", "result_file", "first_checks", "symptoms", "when_to_regenerate", "write_boundaries", "source_files"]:
        assert_true(key in troubleshooting_required, f"troubleshooting schema missed required field {key}")
        assert_true(key in troubleshooting, f"troubleshooting JSON missed required field {key}")
    assert_true(troubleshooting["$schema"] == "troubleshooting.schema.json", "troubleshooting JSON should point to local schema")
    assert_true(troubleshooting["type"] == "desktop_pack_troubleshooting", "troubleshooting JSON wrong type")
    assert_true(troubleshooting["local_only"] is True, "troubleshooting JSON should be local only")
    assert_true(troubleshooting["requires_server"] is False, "troubleshooting JSON should not require a server")
    assert_true(troubleshooting["would_write_project_state"] is False, "troubleshooting JSON should be read-only")
    assert_true(troubleshooting["would_write_install_config"] is False, "troubleshooting JSON should not write install config")
    assert_true(troubleshooting["would_write_pack_files"] is False, "troubleshooting JSON should not write pack files")
    assert_true(troubleshooting["records_real_client_evidence"] is False, "troubleshooting JSON should not record real client evidence")
    assert_true(isinstance(troubleshooting["target_chapter"], int) and troubleshooting["target_chapter"] >= 1, "troubleshooting target chapter should be positive")
    assert_true(isinstance(troubleshooting["first_checks"], list) and troubleshooting["first_checks"], "troubleshooting JSON missed first checks")
    first_check_command_keys = {
        item.get("command", {}).get("key")
        for item in troubleshooting["first_checks"]
        if isinstance(item, dict) and isinstance(item.get("command"), dict)
    }
    assert_true("desktop_troubleshooting" in first_check_command_keys, "troubleshooting JSON missed self-refresh command reference")
    assert_true(isinstance(troubleshooting["symptoms"], list) and len(troubleshooting["symptoms"]) >= 10, "troubleshooting JSON missed symptom guide")
    symptom_ids = {item.get("id") for item in troubleshooting["symptoms"] if isinstance(item, dict)}
    assert_true({"mcp_server_missing", "gui_tools_unavailable", "mode_mismatch", "project_not_initialized", "context_missing_sources", "continuity_check_failed", "result_json_not_recordable", "pack_write_refused", "write_lock_active", "evidence_safety_warning"} <= symptom_ids, "troubleshooting JSON missed core symptoms")
    blocker_ids = {item.get("id") for item in troubleshooting["symptoms"] if isinstance(item, dict) and item.get("severity") == "blocker"}
    assert_true({"mcp_server_missing", "gui_tools_unavailable", "mode_mismatch", "result_json_not_recordable", "write_lock_active"} <= blocker_ids, "troubleshooting JSON missed blocker severity for setup or evidence failures")
    troubleshooting_boundaries = troubleshooting.get("write_boundaries") if isinstance(troubleshooting.get("write_boundaries"), dict) else {}
    assert_true(troubleshooting_boundaries.get("troubleshooting_is_read_only") is True, "troubleshooting JSON missed read-only boundary")
    assert_true(troubleshooting_boundaries.get("do_not_write_project_state_from_this_file") is True, "troubleshooting JSON missed project-state no-write boundary")
    assert_true(troubleshooting_boundaries.get("do_not_write_install_config_from_this_file") is True, "troubleshooting JSON missed install-config no-write boundary")
    assert_true(troubleshooting_boundaries.get("do_not_record_real_client_evidence_from_this_file") is True, "troubleshooting JSON missed evidence no-record boundary")
    assert_true("record_desktop_check" in troubleshooting_boundaries.get("read_only_agents_must_not_execute", []), "troubleshooting JSON should block read-only evidence writes")
    assert_true("troubleshooting.md" in troubleshooting["source_files"], "troubleshooting JSON missed markdown source file")
    assert_true("troubleshooting.schema.json" in troubleshooting["source_files"], "troubleshooting JSON missed schema source file")

    agent_takeover_required = set(agent_takeover_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "platform", "mode", "target_chapter", "status", "can_draft", "can_write_durable_state", "summary", "first_read", "draft_gate", "write_boundaries", "handoff", "commands", "next_actions", "source_files"]:
        assert_true(key in agent_takeover_required, f"agent takeover schema missed required field {key}")
        assert_true(key in agent_takeover, f"agent takeover JSON missed required field {key}")
    assert_true(agent_takeover["$schema"] == "agent-takeover.schema.json", "agent takeover JSON should point to local schema")
    assert_true(agent_takeover["type"] == "agent_takeover", "agent takeover JSON wrong type")
    assert_true(agent_takeover["local_only"] is True, "agent takeover should be local only")
    assert_true(agent_takeover["requires_server"] is False, "agent takeover should not require a server")
    assert_true(agent_takeover["would_write_project_state"] is False, "agent takeover should be read-only")
    assert_true(agent_takeover["would_write_install_config"] is False, "agent takeover should not write install config")
    assert_true(agent_takeover["would_write_pack_files"] is False, "agent takeover should not write pack files")
    assert_true(agent_takeover["records_real_client_evidence"] is False, "agent takeover should not record desktop evidence")
    assert_true(agent_takeover["can_write_durable_state"] is False, "agent takeover should not authorize durable state writes")
    assert_true(isinstance(agent_takeover["target_chapter"], int) and agent_takeover["target_chapter"] >= 1, "agent takeover target chapter should be positive")
    assert_true(agent_takeover["status"] in {"ready_to_draft", "ready_with_review_items", "ready_for_read_only_start", "needs_author_review", "needs_continuity_fix", "needs_initialization", "needs_pack_repair"}, "agent takeover status not allowed")
    first_read_files = {item.get("file") for item in agent_takeover.get("first_read", []) if isinstance(item, dict)}
    assert_true({"agent-takeover.json", "metadata.json", "desktop-next-step.json", "project-status.json", "chapter-session.json", "handoff-brief.json", "commands-index.json", "commands.json"} <= first_read_files, "agent takeover missed first-read files")
    assert_true(agent_takeover["summary"].get("pack_freshness_status") == "fresh", "agent takeover missed pack freshness summary")
    assert_true(agent_takeover["summary"].get("chapter_session_freshness_status") == "fresh", "agent takeover missed chapter session freshness summary")
    takeover_summary_required = set((agent_takeover_schema.get("properties") or {}).get("summary", {}).get("required", []))
    assert_true("chapter_session_freshness_status" in takeover_summary_required, "agent takeover schema missed chapter session freshness summary")
    draft_gate = agent_takeover.get("draft_gate") if isinstance(agent_takeover.get("draft_gate"), dict) else {}
    assert_true(draft_gate.get("requires_chapter_context") is True, "agent takeover should require chapter context")
    assert_true(draft_gate.get("required_context_file") == "chapter-session.json", "agent takeover context file mismatch")
    takeover_boundaries = agent_takeover.get("write_boundaries") if isinstance(agent_takeover.get("write_boundaries"), dict) else {}
    assert_true(takeover_boundaries.get("agent_takeover_is_read_only") is True, "agent takeover missed read-only boundary")
    assert_true(takeover_boundaries.get("do_not_write_project_state_from_this_file") is True, "agent takeover missed no-write boundary")
    assert_true("record_agent_activity" in takeover_boundaries.get("read_only_agents_must_not_execute", []), "agent takeover should block read-only activity writes")
    takeover_commands = agent_takeover.get("commands") if isinstance(agent_takeover.get("commands"), dict) else {}
    for key in ["desktop_next_step", "pack_freshness", "chapter_session_freshness", "write_session_check", "prepare_session", "handoff_readiness", "handoff_integrity", "chapter_delivery", "chapter_acceptance_plan", "record_agent_activity", "author_review_queue"]:
        assert_true(takeover_commands.get(key, {}).get("present") is True, f"agent takeover missed command {key}")
    assert_true("agent-takeover.md" in agent_takeover["source_files"], "agent takeover missed markdown source file")
    assert_true("agent-takeover.schema.json" in agent_takeover["source_files"], "agent takeover missed schema source file")

    author_actions_required = set(author_actions_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "output_dir", "platform", "mode", "target_chapter", "status", "summary", "action_groups", "decision_options", "write_boundaries", "source_files", "notes"]:
        assert_true(key in author_actions_required, f"author actions schema missed required field {key}")
        assert_true(key in author_actions, f"author actions JSON missed required field {key}")
    assert_true(author_actions["$schema"] == "author-actions.schema.json", "author actions JSON should point to local schema")
    assert_true(author_actions["type"] == "author_actions", "author actions JSON wrong type")
    assert_true(author_actions["status"] in {"blocked", "needs_initialization", "needs_revision", "needs_author_review", "needs_real_client_evidence", "ready_for_author_review", "ready_for_read_only_work"}, "author actions status not allowed")
    assert_true(author_actions["local_only"] is True, "author actions should be local only")
    assert_true(author_actions["requires_server"] is False, "author actions should not require server")
    assert_true(author_actions["would_write_project_state"] is False, "author actions should be read-only")
    assert_true(author_actions["would_write_install_config"] is False, "author actions should not write install config")
    assert_true(author_actions["would_write_pack_files"] is False, "author actions should not write pack files")
    assert_true(author_actions["records_real_client_evidence"] is False, "author actions should not record real client evidence")
    assert_true(isinstance(author_actions["target_chapter"], int) and author_actions["target_chapter"] >= 1, "author actions target chapter should be positive")
    action_groups = author_actions.get("action_groups") if isinstance(author_actions.get("action_groups"), list) else []
    action_group_ids = {item.get("id") for item in action_groups if isinstance(item, dict)}
    assert_true({"start_here", "author_review", "draft_review", "desktop_evidence", "writer_mode"} <= action_group_ids, "author actions missed core action groups")
    assert_true(len(author_actions["decision_options"]) >= 5, "author actions missed decision options")
    action_boundaries = author_actions.get("write_boundaries") if isinstance(author_actions.get("write_boundaries"), dict) else {}
    assert_true(action_boundaries.get("author_actions_is_read_only") is True, "author actions missed read-only boundary")
    assert_true("record_desktop_check" in action_boundaries.get("read_only_agents_must_not_execute", []), "author actions should block read-only evidence writes")
    assert_true("author-actions.md" in author_actions["source_files"], "author actions missed markdown source file")
    assert_true("author-actions.schema.json" in author_actions["source_files"], "author actions missed schema source file")

    acceptance_review_required = set(acceptance_review_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "output_dir", "platform", "mode", "target_chapter", "status", "summary", "acceptance_paths", "writer_guard", "write_boundaries", "source_files", "notes"]:
        assert_true(key in acceptance_review_required, f"acceptance review schema missed required field {key}")
        assert_true(key in acceptance_review, f"acceptance review JSON missed required field {key}")
    assert_true(acceptance_review["$schema"] == "acceptance-review.schema.json", "acceptance review JSON should point to local schema")
    assert_true(acceptance_review["type"] == "acceptance_review", "acceptance review JSON wrong type")
    assert_true(acceptance_review["status"] in {"needs_pack_repair", "needs_project_initialization", "needs_revision", "needs_author_review", "ready_for_author_review"}, "acceptance review status not allowed")
    assert_true(acceptance_review["local_only"] is True, "acceptance review should be local only")
    assert_true(acceptance_review["requires_server"] is False, "acceptance review should not require server")
    assert_true(acceptance_review["would_write_project_state"] is False, "acceptance review should be read-only")
    assert_true(acceptance_review["would_write_install_config"] is False, "acceptance review should not write install config")
    assert_true(acceptance_review["would_write_pack_files"] is False, "acceptance review should not write pack files")
    assert_true(acceptance_review["records_real_client_evidence"] is False, "acceptance review should not record real client evidence")
    assert_true(isinstance(acceptance_review["target_chapter"], int) and acceptance_review["target_chapter"] >= 1, "acceptance review target chapter should be positive")
    acceptance_paths = acceptance_review.get("acceptance_paths") if isinstance(acceptance_review.get("acceptance_paths"), list) else []
    acceptance_path_ids = {item.get("id") for item in acceptance_paths if isinstance(item, dict)}
    assert_true({"single_chapter_acceptance", "chapter_range_acceptance"} <= acceptance_path_ids, "acceptance review missed single or range path")
    for path in acceptance_paths:
        if not isinstance(path, dict):
            continue
        assert_true(path.get("requires_author_confirmation") is True, f"acceptance path {path.get('id')} should require author confirmation")
        assert_true(path.get("requires_write_session_check") is True, f"acceptance path {path.get('id')} should require write-session check")
        assert_true(path.get("durable_writes_not_performed_by_this_file") is True, f"acceptance path {path.get('id')} should stay read-only")
    review_boundaries = acceptance_review.get("write_boundaries") if isinstance(acceptance_review.get("write_boundaries"), dict) else {}
    assert_true(review_boundaries.get("acceptance_review_is_read_only") is True, "acceptance review missed read-only boundary")
    assert_true(review_boundaries.get("does_not_record_chapters") is True, "acceptance review should not record chapters")
    assert_true(review_boundaries.get("does_not_apply_proposals") is True, "acceptance review should not apply proposals")
    assert_true(review_boundaries.get("writer_commands_need_explicit_author_confirmation") is True, "acceptance review missed author confirmation boundary")
    review_guard = acceptance_review.get("writer_guard") if isinstance(acceptance_review.get("writer_guard"), dict) else {}
    assert_true("guard_value_sources" in review_guard, "acceptance review missed guard value sources")
    review_commands = [
        command
        for path in acceptance_paths
        if isinstance(path, dict)
        for command in (path.get("commands") if isinstance(path.get("commands"), list) else [])
        if isinstance(command, dict)
    ]
    review_command_keys = {command.get("key") for command in review_commands}
    assert_true({"chapter_acceptance_plan", "chapter_range_acceptance_plan", "write_session_check"} <= review_command_keys, "acceptance review missed acceptance command refs")
    assert_true("acceptance-review.md" in acceptance_review["source_files"], "acceptance review missed markdown source file")
    assert_true("acceptance-review.schema.json" in acceptance_review["source_files"], "acceptance review missed schema source file")

    evidence_session_required = set(evidence_session_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "requires_real_desktop_client", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "platform", "mode", "project_dir", "target_chapter", "result_file", "imported_result_file", "evidence_file", "sanitized_evidence_file", "required_check_ids", "allowed_statuses", "checks", "copy_prompts", "workflow", "commands", "write_boundaries", "notes"]:
        assert_true(key in evidence_session_required, f"desktop evidence session schema missed required field {key}")
        assert_true(key in desktop_evidence_session, f"desktop evidence session JSON missed required field {key}")
    assert_true(desktop_evidence_session["$schema"] == "desktop-evidence-session.schema.json", "desktop evidence session JSON should point to local schema")
    assert_true(desktop_evidence_session["type"] == "desktop_evidence_session", "desktop evidence session JSON wrong type")
    assert_true(desktop_evidence_session["local_only"] is True, "desktop evidence session should be local only")
    assert_true(desktop_evidence_session["requires_server"] is False, "desktop evidence session should not require a server")
    assert_true(desktop_evidence_session["requires_real_desktop_client"] is True, "desktop evidence session should require real desktop client proof")
    assert_true(desktop_evidence_session["would_write_project_state"] is False, "desktop evidence session should be read-only")
    assert_true(desktop_evidence_session["would_write_install_config"] is False, "desktop evidence session should not write install config")
    assert_true(desktop_evidence_session["would_write_pack_files"] is False, "desktop evidence session should not write pack files")
    assert_true(desktop_evidence_session["records_real_client_evidence"] is False, "desktop evidence session should not record real client evidence")
    assert_true(isinstance(desktop_evidence_session["target_chapter"], int) and desktop_evidence_session["target_chapter"] >= 1, "desktop evidence session target chapter should be positive")
    assert_true(isinstance(desktop_evidence_session["required_check_ids"], list) and desktop_evidence_session["required_check_ids"], "desktop evidence session missed required check ids")
    assert_true(isinstance(desktop_evidence_session["checks"], list) and desktop_evidence_session["checks"], "desktop evidence session missed checks")
    assert_true(isinstance(desktop_evidence_session["copy_prompts"], list) and desktop_evidence_session["copy_prompts"], "desktop evidence session missed copy prompts")
    assert_true(isinstance(desktop_evidence_session["workflow"], list) and desktop_evidence_session["workflow"], "desktop evidence session missed workflow")
    command_defs = evidence_session_schema.get("$defs", {}).get("commandRef", {}).get("required", [])
    assert_true({"key", "command", "present"} <= set(command_defs), "desktop evidence session schema missed command ref required fields")
    boundaries = desktop_evidence_session.get("write_boundaries") if isinstance(desktop_evidence_session.get("write_boundaries"), dict) else {}
    assert_true(boundaries.get("recording_requires_reviewed_real_client_evidence") is True, "desktop evidence session should require reviewed real-client evidence before recording")

    evidence_runbook_required = set(evidence_runbook_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "requires_real_desktop_client", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "platform", "mode", "tool_profile", "project_dir", "target_chapter", "evidence_file", "sanitized_evidence_file", "result_file", "imported_result_file", "required_check_ids", "allowed_statuses", "desktop_step_count", "desktop_steps", "preflight", "after_gui", "result_json_contract", "safety_notes"]:
        assert_true(key in evidence_runbook_required, f"desktop evidence runbook schema missed required field {key}")
        assert_true(key in desktop_evidence_runbook, f"desktop evidence runbook JSON missed required field {key}")
    assert_true(desktop_evidence_runbook["$schema"] == "desktop-evidence-runbook.schema.json", "desktop evidence runbook JSON should point to local schema")
    assert_true(desktop_evidence_runbook["type"] == "desktop_evidence_runbook", "desktop evidence runbook JSON wrong type")
    assert_true(desktop_evidence_runbook["local_only"] is True, "desktop evidence runbook should be local only")
    assert_true(desktop_evidence_runbook["requires_server"] is False, "desktop evidence runbook should not require a server")
    assert_true(desktop_evidence_runbook["requires_real_desktop_client"] is True, "desktop evidence runbook should require real desktop client proof")
    assert_true(desktop_evidence_runbook["would_write_project_state"] is False, "desktop evidence runbook should be read-only")
    assert_true(desktop_evidence_runbook["would_write_install_config"] is False, "desktop evidence runbook should not write install config")
    assert_true(desktop_evidence_runbook["would_write_pack_files"] is False, "desktop evidence runbook should not write pack files")
    assert_true(desktop_evidence_runbook["records_real_client_evidence"] is False, "desktop evidence runbook should not record real client evidence")
    assert_true(isinstance(desktop_evidence_runbook["target_chapter"], int) and desktop_evidence_runbook["target_chapter"] >= 1, "desktop evidence runbook target chapter should be positive")
    assert_true(isinstance(desktop_evidence_runbook["required_check_ids"], list) and desktop_evidence_runbook["required_check_ids"], "desktop evidence runbook missed required check ids")
    desktop_steps = desktop_evidence_runbook.get("desktop_steps") if isinstance(desktop_evidence_runbook.get("desktop_steps"), list) else []
    assert_true(desktop_evidence_runbook.get("desktop_step_count") == len(desktop_steps), "desktop evidence runbook step count mismatch")
    assert_true(len(desktop_steps) >= len(desktop_evidence_runbook["required_check_ids"]), "desktop evidence runbook should cover required checks")
    step_ids = {item.get("check_id") for item in desktop_steps if isinstance(item, dict)}
    assert_true(set(desktop_evidence_runbook["required_check_ids"]) <= step_ids, "desktop evidence runbook missed required check steps")
    runbook_after_gui = desktop_evidence_runbook.get("after_gui") if isinstance(desktop_evidence_runbook.get("after_gui"), list) else []
    assert_true(any(isinstance(item, dict) and item.get("id") == "record" and item.get("writes_project_state") is True for item in runbook_after_gui), "desktop evidence runbook missed final record gate")
    runbook_contract = desktop_evidence_runbook.get("result_json_contract") if isinstance(desktop_evidence_runbook.get("result_json_contract"), dict) else {}
    assert_true(runbook_contract.get("passed_requires_specific_evidence") is True, "desktop evidence runbook should require specific evidence for passed checks")
    runbook_command_defs = evidence_runbook_schema.get("$defs", {}).get("commandRef", {}).get("required", [])
    assert_true({"key", "command", "present"} <= set(runbook_command_defs), "desktop evidence runbook schema missed command ref required fields")

    evidence_review_required = set(evidence_review_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "status", "local_only", "requires_server", "requires_real_desktop_client", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "platform", "mode", "project_dir", "target_chapter", "source", "required_total", "required_passed_with_evidence", "missing_required_check_ids", "safety_warnings", "evidence_session", "evidence_packet", "saved_verification", "next_actions", "recommended_commands"]:
        assert_true(key in evidence_review_required, f"desktop evidence review schema missed required field {key}")
        assert_true(key in desktop_evidence_review, f"desktop evidence review JSON missed required field {key}")
    assert_true(desktop_evidence_review["$schema"] == "desktop-evidence-review.schema.json", "desktop evidence review JSON should point to local schema")
    assert_true(desktop_evidence_review["type"] == "desktop_evidence_review", "desktop evidence review JSON wrong type")
    assert_true(desktop_evidence_review["status"] in {"ready_to_record", "needs_revision", "needs_evidence", "recorded", "recorded_elsewhere"}, "desktop evidence review status not allowed")
    assert_true(desktop_evidence_review["local_only"] is True, "desktop evidence review should be local only")
    assert_true(desktop_evidence_review["requires_server"] is False, "desktop evidence review should not require a server")
    assert_true(desktop_evidence_review["requires_real_desktop_client"] is True, "desktop evidence review should require real desktop client proof")
    assert_true(desktop_evidence_review["would_write_project_state"] is False, "desktop evidence review should be read-only")
    assert_true(desktop_evidence_review["would_write_install_config"] is False, "desktop evidence review should not write install config")
    assert_true(desktop_evidence_review["would_write_pack_files"] is False, "desktop evidence review should not write pack files")
    assert_true(desktop_evidence_review["records_real_client_evidence"] is False, "desktop evidence review should not record real client evidence")
    assert_true(isinstance(desktop_evidence_review["target_chapter"], int) and desktop_evidence_review["target_chapter"] >= 1, "desktop evidence review target chapter should be positive")
    assert_true(isinstance(desktop_evidence_review["required_total"], int) and desktop_evidence_review["required_total"] >= 1, "desktop evidence review missed required total")
    assert_true(isinstance(desktop_evidence_review["required_passed_with_evidence"], int), "desktop evidence review passed count should be integer")
    review_session = desktop_evidence_review.get("evidence_session") if isinstance(desktop_evidence_review.get("evidence_session"), dict) else {}
    assert_true(isinstance(review_session.get("required_check_ids"), list) and review_session["required_check_ids"], "desktop evidence review missed session required check ids")
    saved = desktop_evidence_review.get("saved_verification") if isinstance(desktop_evidence_review.get("saved_verification"), dict) else {}
    assert_true(isinstance(saved.get("exact"), dict) and isinstance(saved.get("reusable"), dict), "desktop evidence review missed saved verification summaries")
    review_commands = desktop_evidence_review.get("recommended_commands") if isinstance(desktop_evidence_review.get("recommended_commands"), dict) else {}
    for key in ["desktop_evidence_review", "desktop_evidence_session", "desktop_evidence_packet", "desktop_results_doctor", "record_desktop_check", "list_desktop_checks"]:
        assert_true(isinstance(review_commands.get(key), str) and review_commands[key], f"desktop evidence review missed command {key}")
    assert_true(isinstance(review_commands.get("desktop_evidence_gaps"), str) and review_commands["desktop_evidence_gaps"], "desktop evidence review missed desktop_evidence_gaps command")
    review_defs = evidence_review_schema.get("$defs", {}).get("evidenceSessionSummary", {}).get("required", [])
    assert_true({"required_check_ids", "copy_prompt_count", "result_file", "evidence_file"} <= set(review_defs), "desktop evidence review schema missed evidence session summary fields")

    for key in [
        "ok",
        "type",
        "generated_at",
        "status",
        "local_only",
        "requires_server",
        "requires_real_desktop_client",
        "would_write_project_state",
        "would_write_install_config",
        "would_write_pack_files",
        "records_real_client_evidence",
        "platform",
        "mode",
        "project_dir",
        "target_chapter",
        "source",
        "required_total",
        "required_passed_with_evidence",
        "summary",
        "gaps",
        "evidence_review",
        "next_actions",
        "recommended_commands",
    ]:
        assert_true(key in desktop_evidence_gaps, f"desktop evidence gaps JSON missed required field {key}")
    assert_true(desktop_evidence_gaps["type"] == "desktop_evidence_gaps", "desktop evidence gaps JSON wrong type")
    assert_true(desktop_evidence_gaps["status"] in {"ready_to_record", "needs_revision", "needs_evidence", "recorded", "recorded_elsewhere"}, "desktop evidence gaps status not allowed")
    assert_true(desktop_evidence_gaps["local_only"] is True, "desktop evidence gaps should be local only")
    assert_true(desktop_evidence_gaps["requires_server"] is False, "desktop evidence gaps should not require a server")
    assert_true(desktop_evidence_gaps["requires_real_desktop_client"] is True, "desktop evidence gaps should require real desktop client proof")
    assert_true(desktop_evidence_gaps["would_write_project_state"] is False, "desktop evidence gaps should be read-only")
    assert_true(desktop_evidence_gaps["would_write_install_config"] is False, "desktop evidence gaps should not write install config")
    assert_true(desktop_evidence_gaps["would_write_pack_files"] is False, "desktop evidence gaps should not write pack files")
    assert_true(desktop_evidence_gaps["records_real_client_evidence"] is False, "desktop evidence gaps should not record real client evidence")
    assert_true(isinstance(desktop_evidence_gaps["target_chapter"], int) and desktop_evidence_gaps["target_chapter"] >= 1, "desktop evidence gaps target chapter should be positive")
    assert_true(isinstance(desktop_evidence_gaps["required_total"], int) and desktop_evidence_gaps["required_total"] >= 1, "desktop evidence gaps missed required total")
    assert_true(isinstance(desktop_evidence_gaps["gaps"], list) and desktop_evidence_gaps["gaps"], "desktop evidence gaps missed per-check gaps")
    gap_commands = desktop_evidence_gaps.get("recommended_commands") if isinstance(desktop_evidence_gaps.get("recommended_commands"), dict) else {}
    for key in ["desktop_evidence_gaps", "desktop_evidence_review", "desktop_evidence_packet", "desktop_results_doctor", "record_desktop_check"]:
        assert_true(isinstance(gap_commands.get(key), str) and gap_commands[key], f"desktop evidence gaps missed command {key}")

    writer_approval_required = set(writer_mode_approval_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "local_only", "requires_server", "requires_real_desktop_client", "would_write_project_state", "would_write_install_config", "would_write_pack_files", "records_real_client_evidence", "project_dir", "platform", "current_pack_mode", "target_mode", "target_chapter", "author_confirmation", "required_check_ids", "writer_gate_check_ids", "write_scope", "must_not_write", "do_not_enable_if", "recommended_flow", "commands", "write_boundaries", "source_files"]:
        assert_true(key in writer_approval_required, f"writer mode approval schema missed required field {key}")
        assert_true(key in writer_mode_approval, f"writer mode approval JSON missed required field {key}")
    assert_true(writer_mode_approval["$schema"] == "writer-mode-approval.schema.json", "writer mode approval JSON should point to local schema")
    assert_true(writer_mode_approval["type"] == "writer_mode_approval", "writer mode approval JSON wrong type")
    assert_true(writer_mode_approval["local_only"] is True, "writer mode approval should be local only")
    assert_true(writer_mode_approval["requires_server"] is False, "writer mode approval should not require a server")
    assert_true(writer_mode_approval["requires_real_desktop_client"] is True, "writer mode approval should require real desktop client proof")
    assert_true(writer_mode_approval["would_write_project_state"] is False, "writer mode approval should be read-only")
    assert_true(writer_mode_approval["would_write_install_config"] is False, "writer mode approval should not write install config")
    assert_true(writer_mode_approval["would_write_pack_files"] is False, "writer mode approval should not write pack files")
    assert_true(writer_mode_approval["records_real_client_evidence"] is False, "writer mode approval should not record desktop evidence")
    assert_true(writer_mode_approval["target_mode"] == "writer", "writer mode approval target mode should be writer")
    assert_true(isinstance(writer_mode_approval["target_chapter"], int) and writer_mode_approval["target_chapter"] >= 1, "writer mode approval target chapter should be positive")
    assert_true("write_session_preflight_gate" in writer_mode_approval["writer_gate_check_ids"], "writer mode approval missed write session preflight gate")
    assert_true("proposal_readiness_gate" in writer_mode_approval["writer_gate_check_ids"], "writer mode approval missed proposal readiness gate")
    assert_true("acceptance_write_gate" in writer_mode_approval["writer_gate_check_ids"], "writer mode approval missed acceptance write gate")
    assert_true(".novel-agent/proposals.jsonl" in writer_mode_approval["write_scope"], "writer mode approval missed proposal write scope")
    assert_true("Gaoxia frontend" in writer_mode_approval["must_not_write"], "writer mode approval missed main app write boundary")
    assert_true("writer-mode-approval.md" in writer_mode_approval["source_files"], "writer mode approval missed markdown source file")
    assert_true("writer-mode-approval.schema.json" in writer_mode_approval["source_files"], "writer mode approval missed schema source file")
    writer_commands = writer_mode_approval.get("commands") if isinstance(writer_mode_approval.get("commands"), dict) else {}
    for key in ["writer_mode_readiness", "writer_mcp_config_preview", "writer_install_apply", "writer_desktop_checklist", "desktop_results_doctor", "record_desktop_check"]:
        command_ref = writer_commands.get(key) if isinstance(writer_commands.get(key), dict) else {}
        assert_true(command_ref.get("present") is True and isinstance(command_ref.get("command"), str) and command_ref["command"], f"writer mode approval missed command {key}")
    flow_item_required = set(
        (((writer_mode_approval_schema.get("properties") or {}).get("recommended_flow") or {}).get("items") or {}).get("required") or []
    )
    assert_true({"step", "title", "file", "command_key", "writes"} <= flow_item_required, "writer mode approval schema missed flow step required fields")

    wizard_required = set(project_wizard_schema.get("required") or [])
    for key in ["$schema", "version", "project_dir", "project", "agent", "rules", "source_material", "install", "options", "instructions"]:
        assert_true(key in wizard_required, f"project wizard schema missed required field {key}")
        assert_true(key in project_wizard, f"project wizard JSON missed required field {key}")
    assert_true(project_wizard["$schema"] == "project-wizard.schema.json", "project wizard JSON should point to local schema")
    assert_true(isinstance(project_wizard["project"], dict), "project wizard project should be object")
    assert_true(project_wizard["project"]["source_mode"] in {"auto", "init", "import-gaoxia"}, "project wizard source_mode should be allowed")
    assert_true(isinstance(project_wizard["project"]["target_chapters"], int) and project_wizard["project"]["target_chapters"] >= 1, "project wizard target_chapters should be positive integer")
    assert_true(isinstance(project_wizard["agent"], dict), "project wizard agent should be object")
    assert_true(project_wizard["agent"]["mode"] in {"read-only", "writer"}, "project wizard agent mode should be read-only or writer")
    assert_true(isinstance(project_wizard["agent"]["capabilities"], list), "project wizard agent capabilities should be list")
    wizard_defs = project_wizard_schema.get("$defs") if isinstance(project_wizard_schema.get("$defs"), dict) else {}
    for key in ["project", "agent", "rules", "sourceMaterial", "install", "options"]:
        assert_true(key in wizard_defs, f"project wizard schema missed {key} definition")
    source_mode_values = set(((wizard_defs.get("project") or {}).get("properties") or {}).get("source_mode", {}).get("enum") or [])
    assert_true({"auto", "init", "import-gaoxia"} <= source_mode_values, "project wizard schema missed source_mode enum values")

    source_required = set(source_schema.get("required") or [])
    for key in ["$schema", "version", "type", "project_dir", "agent", "instructions", "sources", "research", "conflicts", "facts"]:
        assert_true(key in source_required, f"source intake schema missed required field {key}")
        assert_true(key in source_intake, f"source intake JSON missed required field {key}")
    assert_true(source_intake["$schema"] == "source-intake.schema.json", "source intake JSON should point to local schema")
    assert_true(source_intake["type"] == "source_intake", "source intake JSON wrong type")
    assert_true(source_intake["agent"]["mode"] in {"read-only", "writer"}, "source intake agent mode should be read-only or writer")
    for section in ["sources", "research", "conflicts", "facts"]:
        assert_true(isinstance(source_intake[section], list), f"source intake {section} should be list")
    source_defs = source_schema.get("$defs") if isinstance(source_schema.get("$defs"), dict) else {}
    assert_true("chapterNumber" in source_defs, "source intake schema missed chapter number definition")
    assert_true("sourceEntry" in source_defs, "source intake schema missed source entry definition")
    assert_true("researchEntry" in source_defs, "source intake schema missed research entry definition")
    assert_true("conflictEntry" in source_defs, "source intake schema missed conflict entry definition")
    assert_true("factEntry" in source_defs, "source intake schema missed fact entry definition")

    chapter_required = set(chapter_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "project_dir", "state_dir", "target_chapter", "platform", "mode", "status", "warnings", "recommended_commands", "project_state_fingerprint"]:
        assert_true(key in chapter_required, f"chapter session schema missed required field {key}")
        assert_true(key in chapter_session, f"chapter session JSON missed required field {key}")
    assert_true(chapter_session["$schema"] == "chapter-session.schema.json", "chapter session JSON should point to local schema")
    assert_true(chapter_session["type"] in {"chapter_session_packet", "chapter_session_pending"}, "chapter session JSON wrong type")
    assert_true(isinstance(chapter_session["target_chapter"], int) and chapter_session["target_chapter"] >= 1, "chapter session target chapter should be positive integer")
    assert_true(chapter_session["mode"] in {"read-only", "writer"}, "chapter session mode should be read-only or writer")
    assert_true(isinstance(chapter_session["project_state_fingerprint"], dict), "chapter session JSON missed project state fingerprint")
    chapter_type_values = set((chapter_schema.get("properties") or {}).get("type", {}).get("enum") or [])
    assert_true({"chapter_session_packet", "chapter_session_pending"} <= chapter_type_values, "chapter session schema missed type enum values")
    if chapter_session["type"] == "chapter_session_packet":
        assert_true(chapter_session["status"] in {"ready", "needs_attention"}, "chapter session packet status should be ready or needs_attention")
        assert_true("chapter_context" in chapter_session or "chapter_context_summary" in chapter_session, "chapter session packet should include context or summary")
        gates = chapter_session.get("tool_gates") if isinstance(chapter_session.get("tool_gates"), dict) else {}
        assert_true(bool(gates.get("before_draft")), "chapter session packet missed before_draft gates")
        assert_true(bool(gates.get("before_ready")), "chapter session packet missed before_ready gates")
        assert_true(isinstance(chapter_session.get("handoff_report"), dict), "chapter session packet missed handoff report")
        assert_true(isinstance(chapter_session.get("health"), dict), "chapter session packet missed health")
    else:
        assert_true(chapter_session["status"] == "not_initialized", "pending chapter session should use not_initialized status")
        assert_true(bool(chapter_session.get("next_actions")), "pending chapter session missed next actions")

    handoff_required = set(handoff_schema.get("required") or [])
    for key in ["$schema", "ok", "type", "generated_at", "project_dir", "state_dir", "target_chapter", "read_only", "source", "recommended_commands"]:
        assert_true(key in handoff_required, f"handoff report schema missed required field {key}")
        assert_true(key in handoff_report, f"handoff report JSON missed required field {key}")
    assert_true(handoff_report["$schema"] == "handoff-report.schema.json", "handoff report JSON should point to local schema")
    assert_true(handoff_report["type"] == "handoff_report", "handoff report JSON wrong type")
    assert_true(isinstance(handoff_report["target_chapter"], int) and handoff_report["target_chapter"] >= 1, "handoff report target chapter should be positive integer")
    assert_true(handoff_report["read_only"] is True, "handoff report JSON should be read-only")
    assert_true((handoff_schema.get("properties") or {}).get("type", {}).get("const") == "handoff_report", "handoff report schema should require handoff_report type")
    assert_true((handoff_schema.get("properties") or {}).get("read_only", {}).get("const") is True, "handoff report schema should require read_only true")
    assert_true(isinstance(handoff_report.get("recommended_commands"), list) and handoff_report["recommended_commands"], "handoff report JSON missed recommended commands")
    if handoff_report.get("ok") is True:
        assert_true(isinstance(handoff_report.get("next_agent_brief"), dict), "ready handoff report JSON missed next agent brief")
        assert_true(isinstance(handoff_report.get("required_workflow"), list) and handoff_report["required_workflow"], "ready handoff report JSON missed required workflow")
        assert_true("chapter_context" in handoff_report or "chapter_context_summary" in handoff_report, "ready handoff report JSON missed context or summary")
    else:
        assert_true(handoff_report.get("status") == "pending", "pending handoff report JSON should use pending status")
        assert_true(isinstance(handoff_report.get("next_actions"), list) and handoff_report["next_actions"], "pending handoff report JSON missed next actions")

    results_required = set(results_schema.get("required") or [])
    for key in ["$schema", "checks"]:
        assert_true(key in results_required, f"desktop results schema missed required field {key}")
        assert_true(key in results, f"desktop results JSON missed required field {key}")
    assert_true(results["$schema"] == "desktop-results.schema.json", "desktop results JSON should point to local schema")
    checks = results.get("checks")
    assert_true(isinstance(checks, list) and checks, "desktop results checks should be non-empty list")
    status_values = set(
        ((results_schema.get("$defs") or {}).get("check") or {}).get("properties", {}).get("status", {}).get("enum") or []
    )
    assert_true({"passed", "failed", "skipped", "not_checked"} <= status_values, "desktop results schema missed status enum values")
    for entry in checks:
        assert_true(isinstance(entry, dict), "desktop results check should be object")
        for key in ["id", "status", "evidence", "notes"]:
            assert_true(key in entry, f"desktop results check missed {key}")
        assert_true(entry["status"] in status_values, "desktop results check status not allowed by schema")
    return {
        "schema_count": 22,
        "project_source_mode": project_wizard["project"]["source_mode"],
        "source_sections": 4,
        "handoff_target_chapter": handoff_report["target_chapter"],
        "result_check_count": len(checks),
        "chapter_session_type": chapter_session["type"],
        "local_summary_status": local_summary["status"],
        "local_summary_schema_title": local_summary_schema["title"],
        "first_three_status": first_three["status"],
        "first_three_file_count": len(first_three.get("files", [])),
        "first_three_schema_title": first_three_schema["title"],
        "user_steps_status": user_steps["status"],
        "user_steps_schema_title": user_steps_schema["title"],
        "starter_brief_status": starter_brief["status"],
        "starter_brief_schema_title": starter_brief_schema["title"],
        "agent_startup_prompt_status": agent_startup_prompt["status"],
        "agent_startup_prompt_read_first_count": len(agent_startup_prompt["read_first"]),
        "desktop_diagnostics_status": desktop_diagnostics["status"],
        "desktop_diagnostics_check_count": len(desktop_diagnostics["checks"]),
        "troubleshooting_symptom_count": len(troubleshooting["symptoms"]),
        "agent_takeover_status": agent_takeover["status"],
        "agent_takeover_first_read_count": len(agent_takeover["first_read"]),
        "author_actions_status": author_actions["status"],
        "author_actions_action_group_count": len(author_actions["action_groups"]),
        "writer_mode_approval_required_checks": len(writer_mode_approval["required_check_ids"]),
        "writer_mode_approval_flow_steps": len(writer_mode_approval["recommended_flow"]),
    }


def verify_example_import() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-example-") as tmp:
        project = Path(tmp) / "mini-gaoxia-project"
        shutil.copytree(EXAMPLE, project)
        missing_audit = load_stdout(run_cli("import-audit", str(project), "--chapter", "2"))
        assert_true(missing_audit["type"] == "gaoxia_import_audit", "import-audit returned wrong type before import")
        assert_true(missing_audit["status"] == "not_imported", "import-audit should require import before state exists")
        imported = load_stdout(run_cli("import-gaoxia", str(project)))
        import_audit = load_stdout(run_cli("import-audit", str(project), "--chapter", "2"))
        import_audit_md = run_cli("import-audit", str(project), "--chapter", "2", "--format", "markdown")
        context = load_stdout(run_cli("build-context", str(project), "--chapter", "2"))
        bad = project / "chapters" / "002.md"
        bad.write_text("林追主动暴露身份，并把铜钥匙交给白石商会。", encoding="utf-8")
        check = load_stdout(run_cli("check-chapter", str(project), "--chapter", "2", "--file", str(bad)))
        readiness = load_stdout(run_cli("chapter-readiness", str(project), "--chapter", "2", "--file", str(bad)))
        readiness_md = run_cli("chapter-readiness", str(project), "--chapter", "2", "--file", str(bad), "--format", "markdown")
        revision_prompt = load_stdout(run_cli("chapter-revision-prompt", str(project), "--chapter", "2", "--file", str(bad)))
        revision_prompt_md = run_cli("chapter-revision-prompt", str(project), "--chapter", "2", "--file", str(bad), "--format", "markdown")
        revised = project / "chapters" / "002.revised.md"
        revised.write_text("林追把铜钥匙贴身收好，只把潮湿库房里的钥匙来历记进暗账，继续隐瞒身份。", encoding="utf-8")
        revision_compare = load_stdout(run_cli("chapter-revision-compare", str(project), "--chapter", "2", "--before", str(bad), "--after", str(revised)))
        revision_compare_md = run_cli("chapter-revision-compare", str(project), "--chapter", "2", "--before", str(bad), "--after", str(revised), "--format", "markdown")
        delivery = load_stdout(run_cli("chapter-delivery", str(project), "--chapter", "2", "--file", str(bad)))
        delivery_md = run_cli("chapter-delivery", str(project), "--chapter", "2", "--file", str(bad), "--format", "markdown")
        chapter_acceptance = load_stdout(run_cli("chapter-acceptance-plan", str(project), "--chapter", "2", "--file", str(bad)))
        chapter_acceptance_md = run_cli("chapter-acceptance-plan", str(project), "--chapter", "2", "--file", str(bad), "--author-confirmation", "Author accepted chapter 2 for verification.", "--format", "markdown")
        range_readiness = load_stdout(run_cli("chapter-range-readiness", str(project), "--start", "1", "--end", "2", "--draft-dir", "chapters"))
        range_readiness_md = run_cli("chapter-range-readiness", str(project), "--start", "1", "--end", "2", "--draft-dir", "chapters", "--format", "markdown")
        range_delivery = load_stdout(run_cli("chapter-range-delivery", str(project), "--start", "1", "--end", "2", "--handoff-chapter", "3", "--draft-dir", "chapters"))
        range_delivery_md = run_cli("chapter-range-delivery", str(project), "--start", "1", "--end", "2", "--handoff-chapter", "3", "--draft-dir", "chapters", "--format", "markdown")
        acceptance_plan = load_stdout(run_cli("chapter-range-acceptance-plan", str(project), "--start", "1", "--end", "2", "--handoff-chapter", "3", "--draft-dir", "chapters"))
        acceptance_plan_md = run_cli("chapter-range-acceptance-plan", str(project), "--start", "1", "--end", "2", "--handoff-chapter", "3", "--draft-dir", "chapters", "--author-confirmation", "Author accepted chapters 1-2 for verification.", "--format", "markdown")
        issue_types = {item["type"] for item in check["issues"]}
        assert_true(imported["counts"]["facts"] >= 7, "example import did not create enough facts")
        assert_true(import_audit["status"] == "ready", f"import-audit should be ready after import, got {import_audit['status']}")
        assert_true(import_audit["summary"]["imported_chapters"] == 1, "import-audit missed imported chapter count")
        assert_true(import_audit["summary"]["visible_imported_sources"] >= 1, "import-audit missed visible imported sources")
        assert_true("Long Novel Agent Gaoxia Import Audit" in import_audit_md.stdout, "import-audit markdown missing title")
        source_chapter = project / "chapters" / "001.md"
        source_chapter.write_text(source_chapter.read_text(encoding="utf-8") + "\n\n导入后源章节被作者改写。\n", encoding="utf-8")
        stale_import_audit = load_stdout(run_cli("import-audit", str(project), "--chapter", "2"))
        assert_true(stale_import_audit["status"] == "needs_reimport", "import-audit should detect changed imported source chapter")
        assert_true(stale_import_audit["summary"]["stale_chapters"] == 1, "import-audit missed stale imported chapter count")
        assert_true(context["previous_chapter"]["chapter"] == 1, "chapter 2 context missed chapter 1")
        assert_true("forbidden_phrase" in issue_types, "example check missed forbidden phrase")
        assert_true("fact_conflict" in issue_types, "example check missed fact conflict")
        assert_true(readiness["type"] == "chapter_readiness", "chapter-readiness returned wrong type")
        assert_true(readiness["status"] == "needs_revision", "chapter-readiness should require revision for bad chapter")
        assert_true(readiness["summary"]["must_fix"] >= 1, "chapter-readiness missed must-fix items")
        assert_true("Long Novel Agent Chapter Readiness" in readiness_md.stdout, "chapter-readiness markdown missing title")
        assert_true("Must Fix Before Acceptance" in readiness_md.stdout, "chapter-readiness markdown missed must-fix section")
        assert_true(revision_prompt["type"] == "chapter_revision_prompt", "chapter-revision-prompt returned wrong type")
        assert_true(revision_prompt["read_only"] is True and revision_prompt["writes_project_state"] is False, "chapter-revision-prompt should be read-only")
        assert_true(revision_prompt["status"] == "needs_revision", "chapter-revision-prompt should reflect readiness status")
        assert_true(revision_prompt["steps"], "chapter-revision-prompt missed revision steps")
        assert_true("prompt_markdown" in revision_prompt, "chapter-revision-prompt missed copyable prompt")
        assert_true("Long Novel Agent Chapter Revision Prompt" in revision_prompt_md.stdout, "chapter-revision-prompt markdown missing title")
        assert_true(
            any(step.get("fact_contexts") for step in revision_prompt.get("steps", []) if isinstance(step, dict)),
            "chapter-revision-prompt missed fact contexts",
        )
        assert_true(revision_compare["type"] == "chapter_revision_comparison", "chapter-revision-compare returned wrong type")
        assert_true(revision_compare["read_only"] is True and revision_compare["writes_project_state"] is False, "chapter-revision-compare should be read-only")
        assert_true(revision_compare["summary"]["resolved"] >= 1, "chapter-revision-compare missed resolved issues")
        assert_true(
            revision_compare["summary"]["after"]["must_fix"] < revision_compare["summary"]["before"]["must_fix"],
            "chapter-revision-compare should show fewer must-fix issues after revision",
        )
        assert_true(revision_compare["revision_prompt_after"]["type"] == "chapter_revision_prompt", "chapter-revision-compare missed after revision prompt")
        assert_true("Long Novel Agent Chapter Revision Comparison" in revision_compare_md.stdout, "chapter-revision-compare markdown missing title")
        assert_true(delivery["type"] == "chapter_delivery_packet", "chapter-delivery returned wrong type")
        assert_true(delivery["read_only"] is True and delivery["writes_project_state"] is False, "chapter-delivery should be read-only")
        assert_true(delivery["status"] == "needs_revision", "chapter-delivery should require revision for bad chapter")
        assert_true(delivery["readiness"]["type"] == "chapter_readiness", "chapter-delivery missed readiness report")
        assert_true(delivery["revision_prompt"]["type"] == "chapter_revision_prompt", "chapter-delivery missed revision prompt")
        assert_true(delivery["handoff_report"]["type"] == "handoff_report", "chapter-delivery missed handoff report")
        assert_true("record_chapter" in delivery["write_operations_not_performed"], "chapter-delivery should disclose skipped writes")
        assert_true("Long Novel Agent Chapter Delivery" in delivery_md.stdout, "chapter-delivery markdown missing title")
        assert_true("Copyable Revision Prompt" in delivery_md.stdout, "chapter-delivery markdown missed revision prompt")
        assert_true("Write Operations Not Performed" in delivery_md.stdout, "chapter-delivery markdown missed write boundary section")
        assert_true(chapter_acceptance["type"] == "chapter_acceptance_plan", "chapter-acceptance-plan returned wrong type")
        assert_true(chapter_acceptance["read_only"] is True and chapter_acceptance["writes_project_state"] is False, "chapter-acceptance-plan should be read-only")
        assert_true(chapter_acceptance["status"] in {"needs_revision", "needs_author_confirmation"}, "chapter-acceptance-plan should not be ready for bad chapter without confirmation")
        assert_true(chapter_acceptance["author_confirmation_present"] is False, "chapter-acceptance-plan should detect missing author confirmation")
        assert_true("record_chapter" in chapter_acceptance["write_operations_not_performed"], "chapter-acceptance-plan should disclose skipped writes")
        assert_true(chapter_acceptance["pre_write_review"]["requires_write_session_check"] is True, "chapter-acceptance-plan should require write-session check before writer commands")
        assert_true(
            ".novel-agent/chapters.jsonl" in chapter_acceptance["pre_write_review"]["state_files_that_may_change"],
            "chapter-acceptance-plan pre-write review should list chapter state file",
        )
        assert_true(
            any(item.get("operation") == "apply_after_write_updates" and item.get("requires_proposal_readiness") for item in chapter_acceptance["pre_write_review"]["durable_write_operations"]),
            "chapter-acceptance-plan pre-write review should require proposal readiness before apply",
        )
        assert_true("prepare_session_for_write_guard" in chapter_acceptance["recommended_commands"], "chapter-acceptance-plan should include prepare-session guard command")
        assert_true("write_session_check" in chapter_acceptance["recommended_commands"], "chapter-acceptance-plan should include write-session-check command")
        assert_true(
            "project_identity.project_id" in chapter_acceptance["pre_write_review"]["guard_value_sources"]["project_id"],
            "chapter-acceptance-plan pre-write review should explain project_id source",
        )
        assert_true("--expected-context-hash" in chapter_acceptance["recommended_commands"]["record_chapter_after_author_acceptance"], "record chapter command should include expected context guard")
        assert_true("Long Novel Agent Chapter Acceptance Plan" in chapter_acceptance_md.stdout, "chapter-acceptance-plan markdown missing title")
        assert_true("Required Write Order" in chapter_acceptance_md.stdout, "chapter-acceptance-plan markdown missed write order")
        assert_true("Pre-Write Review" in chapter_acceptance_md.stdout, "chapter-acceptance-plan markdown missed pre-write review")
        assert_true("Guard Value Sources" in chapter_acceptance_md.stdout, "chapter-acceptance-plan markdown missed guard value sources")
        assert_true("write_session_check" in chapter_acceptance_md.stdout, "chapter-acceptance-plan markdown missed write-session command")
        assert_true(range_readiness["type"] == "chapter_range_readiness", "chapter-range-readiness returned wrong type")
        assert_true(range_readiness["status"] == "needs_revision", "chapter-range-readiness should require revision for bad chapter")
        assert_true(range_readiness["summary"]["chapters"] == 2, "chapter-range-readiness missed chapter count")
        assert_true(2 in range_readiness["summary"]["problem_chapters"], "chapter-range-readiness missed problem chapter")
        assert_true("Long Novel Agent Chapter Range Readiness" in range_readiness_md.stdout, "chapter-range-readiness markdown missing title")
        assert_true(range_delivery["type"] == "chapter_range_delivery_packet", "chapter-range-delivery returned wrong type")
        assert_true(range_delivery["read_only"] is True and range_delivery["writes_project_state"] is False, "chapter-range-delivery should be read-only")
        assert_true(range_delivery["status"] in {"blocked", "needs_revision"}, "chapter-range-delivery should require revision for bad chapter")
        assert_true(range_delivery["chapter_range_readiness"]["type"] == "chapter_range_readiness", "chapter-range-delivery missed range readiness")
        assert_true(range_delivery["handoff_range_report"]["type"] == "handoff_range_report", "chapter-range-delivery missed handoff range report")
        assert_true("record_chapter" in range_delivery["write_operations_not_performed"], "chapter-range-delivery should disclose skipped writes")
        assert_true("Long Novel Agent Chapter Range Delivery" in range_delivery_md.stdout, "chapter-range-delivery markdown missing title")
        assert_true("Write Operations Not Performed" in range_delivery_md.stdout, "chapter-range-delivery markdown missed write boundary section")
        assert_true(acceptance_plan["type"] == "chapter_range_acceptance_plan", "chapter-range-acceptance-plan returned wrong type")
        assert_true(acceptance_plan["read_only"] is True and acceptance_plan["writes_project_state"] is False, "chapter-range-acceptance-plan should be read-only")
        assert_true(acceptance_plan["status"] in {"blocked", "needs_revision", "needs_author_confirmation"}, "chapter-range-acceptance-plan should not be ready for bad chapter without confirmation")
        assert_true(acceptance_plan["author_confirmation_present"] is False, "chapter-range-acceptance-plan should detect missing author confirmation")
        assert_true("record_chapter" in acceptance_plan["write_operations_not_performed"], "chapter-range-acceptance-plan should disclose skipped writes")
        assert_true(acceptance_plan["pre_write_review"]["requires_per_chapter_write_session_check"] is True, "chapter-range-acceptance-plan should require per-chapter write-session checks")
        assert_true(
            ".novel-agent/chapters.jsonl" in acceptance_plan["pre_write_review"]["state_files_that_may_change"],
            "chapter-range-acceptance-plan pre-write review should list chapter state file",
        )
        assert_true(
            any(item.get("operation") == "apply_after_write_updates" and item.get("requires_proposal_readiness") for item in acceptance_plan["pre_write_review"]["durable_write_operations"]),
            "chapter-range-acceptance-plan pre-write review should require proposal readiness before apply",
        )
        assert_true("prepare_session_for_write_guard_1" in acceptance_plan["recommended_commands"], "chapter-range-acceptance-plan should include per-chapter prepare-session guard command")
        assert_true("write_session_check_1" in acceptance_plan["recommended_commands"], "chapter-range-acceptance-plan should include per-chapter write-session-check command")
        assert_true("--expected-context-hash" in acceptance_plan["recommended_commands"]["record_chapter_1_after_author_acceptance"], "range record chapter command should include expected context guard")
        assert_true("Long Novel Agent Chapter Range Acceptance Plan" in acceptance_plan_md.stdout, "chapter-range-acceptance-plan markdown missing title")
        assert_true("Required Write Order" in acceptance_plan_md.stdout, "chapter-range-acceptance-plan markdown missed write order")
        assert_true("Pre-Write Review" in acceptance_plan_md.stdout, "chapter-range-acceptance-plan markdown missed pre-write review")
        assert_true("Per-Chapter Write Guards" in acceptance_plan_md.stdout, "chapter-range-acceptance-plan markdown missed per-chapter write guards")
        return {
            "facts": imported["counts"]["facts"],
            "issue_types": sorted(issue_types),
            "readiness_status": readiness["status"],
            "revision_prompt_steps": len(revision_prompt["steps"]),
            "revision_compare_status": revision_compare["status"],
            "revision_compare_resolved": revision_compare["summary"]["resolved"],
            "delivery_status": delivery["status"],
            "chapter_acceptance_status": chapter_acceptance["status"],
            "chapter_acceptance_pre_write_review": bool(chapter_acceptance.get("pre_write_review")),
            "range_readiness_status": range_readiness["status"],
            "range_delivery_status": range_delivery["status"],
            "range_acceptance_status": acceptance_plan["status"],
            "range_acceptance_pre_write_review": bool(acceptance_plan.get("pre_write_review")),
            "import_audit_status": import_audit["status"],
            "stale_import_audit_status": stale_import_audit["status"],
        }


def verify_desktop_handoff_relay_example() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-handoff-relay-") as tmp:
        fixture = Path(tmp) / "desktop-handoff-relay"
        shutil.copytree(HANDOFF_EXAMPLE, fixture)
        project = Path(tmp) / "relay-project"
        run_cli(
            "init-wizard",
            str(project),
            "--title",
            "Desktop Handoff Relay",
            "--target-chapters",
            "6",
            "--required",
            "ledger page is missing",
            "--forbidden",
            "copper key leaves Mara",
            "--agent-id",
            "agent-a-codex",
            "--platform",
            "codex",
            "--capabilities",
            "mcp,cli,file-read,file-write",
        )
        chapter1 = fixture / "agent-a-chapter-001.md"
        run_cli(
            "record-chapter",
            str(project),
            "--chapter",
            "1",
            "--file",
            str(chapter1),
            "--handoff",
            "Agent B should trace the missing ledger page without moving the copper key away from Mara.",
        )
        proposed = load_stdout(run_cli("propose-after-write", str(project), "--chapter", "1", "--file", str(chapter1), "--updates", str(fixture / "agent-a-updates.json")))
        assert_true(proposed["validation"]["ok"], "handoff relay proposal should validate")
        proposal_id = proposed["proposal"]["id"]
        validation = load_stdout(run_cli("validate-proposal", str(project), "--proposal-id", proposal_id))
        assert_true(validation["validation"]["ok"], "handoff relay saved proposal should validate")
        diff = load_stdout(run_cli("diff-proposal", str(project), "--proposal-id", proposal_id))
        assert_true(diff["diff"]["facts"]["create"], "handoff relay diff should create facts")
        applied = load_stdout(run_cli("apply-after-write", str(project), "--proposal-id", proposal_id))
        assert_true(applied["applied"]["facts"] >= 2, "handoff relay apply missed facts")

        run_cli("declare-agent", str(project), "--agent-id", "agent-b-cursor", "--platform", "cursor", "--capabilities", "mcp,cli,file-read,file-write")
        checklist = load_stdout(run_cli("desktop-checklist", str(project), "--platform", "cursor", "--mode", "writer", "--chapter", "2", "--format", "json"))
        results = [
            {
                **item,
                "status": "passed",
                "evidence": (
                    "relay example evidence for write_session_preflight_gate: "
                    "check_write_session returned status=ready before durable writer commands"
                    if item["id"] == "write_session_preflight_gate"
                    else
                    "relay example evidence for proposal_readiness_gate: "
                    "build_proposal_readiness returned ready_to_apply before apply_after_write_updates"
                    if item["id"] == "proposal_readiness_gate"
                    else
                    "relay example evidence for acceptance_write_gate: "
                    "record_chapter and apply_after_write_updates included author_confirmation"
                    if item["id"] == "acceptance_write_gate"
                    else
                    "relay example evidence for range_readiness_gate: "
                    "build_chapter_range_readiness returned chapter_range_readiness before handoff"
                    if item["id"] == "range_readiness_gate"
                    else f"relay example evidence for {item['id']}"
                ),
            }
            for item in checklist["manual_result_template"]
        ]
        desktop = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                json.dumps({"checks": results}, ensure_ascii=False),
                "--agent-id",
                "agent-b-cursor",
                "--client-version",
                "relay-fixture",
            )
        )
        assert_true(desktop["validation"]["ok"], "handoff relay desktop verification should pass")
        readiness = load_stdout(
            run_cli(
                "handoff-readiness",
                str(project),
                "--chapter",
                "2",
                "--incoming-agent-id",
                "agent-b-cursor",
                "--incoming-platform",
                "cursor",
                "--mode",
                "writer",
            )
        )
        assert_true(readiness["status"] == "ready", f"handoff relay readiness should be ready, got {readiness['status']}")
        assert_true(not readiness["missing_capabilities"], "handoff relay should have no missing capabilities")
        readiness_md = run_cli(
            "handoff-readiness",
            str(project),
            "--chapter",
            "2",
            "--incoming-agent-id",
            "agent-b-cursor",
            "--incoming-platform",
            "cursor",
            "--mode",
            "writer",
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Handoff Readiness" in readiness_md.stdout, "handoff relay markdown missing title")
        session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "2", "--platform", "cursor", "--mode", "writer"))
        assert_true(session["target_chapter"] == 2, "handoff relay session target mismatch")
        assert_true(any(row.get("subject") == "copper key" for row in session["chapter_context"]["facts"]), "handoff relay context missed copper key fact")
        chapter2 = fixture / "agent-b-draft-002.md"
        check = load_stdout(run_cli("check-chapter", str(project), "--chapter", "2", "--file", str(chapter2)))
        assert_true(check["status"] == "pass", f"handoff relay chapter 2 should pass, got {check['status']}")
        run_cli("record-chapter", str(project), "--chapter", "2", "--file", str(chapter2), "--handoff", "Next agent should question the night clerk before opening the locked cabinet.")
        chapter3 = load_stdout(run_cli("build-context", str(project), "--chapter", "3"))
        assert_true(chapter3["previous_chapter"]["chapter"] == 2, "handoff relay chapter 3 context missed agent B chapter")
        return {
            "readiness": readiness["status"],
            "facts_applied": applied["applied"]["facts"],
            "desktop_checks": desktop["record"]["required_passed_with_evidence"],
            "chapter2_status": check["status"],
        }


def verify_longform_continuity_chain_example() -> dict[str, Any]:
    readme = LONGFORM_CHAIN_EXAMPLE / "README.md"
    assert_true(readme.exists(), "longform continuity chain README is missing")
    with tempfile.TemporaryDirectory(prefix="long-novel-continuity-chain-") as tmp:
        project = Path(tmp) / "chain-project"
        run_cli(
            "init-wizard",
            str(project),
            "--title",
            "Longform Continuity Chain",
            "--target-chapters",
            "12",
            "--required",
            "河灯纸买主",
            "--forbidden",
            "打开钟楼密室",
            "--future-marker",
            "账册缺页真相",
            "--agent-id",
            "chain-agent-a",
            "--platform",
            "codex",
            "--capabilities",
            "mcp,cli,file-read,file-write",
        )
        applied_facts = 0
        for chapter in range(1, 7):
            chapter_file = LONGFORM_CHAIN_EXAMPLE / f"chapter-{chapter:03d}.md"
            updates_file = LONGFORM_CHAIN_EXAMPLE / f"updates-{chapter:03d}.json"
            assert_true(chapter_file.exists(), f"longform chain chapter {chapter} is missing")
            assert_true(updates_file.exists(), f"longform chain updates {chapter} is missing")
            run_cli(
                "record-chapter",
                str(project),
                "--chapter",
                str(chapter),
                "--file",
                str(chapter_file),
                "--handoff",
                f"Continue from accepted chapter {chapter}; preserve longform continuity facts.",
            )
            proposed = load_stdout(
                run_cli(
                    "propose-after-write",
                    str(project),
                    "--chapter",
                    str(chapter),
                    "--file",
                    str(chapter_file),
                    "--updates",
                    str(updates_file),
                )
            )
            assert_true(proposed["validation"]["ok"], f"longform chain proposal {chapter} should validate")
            proposal_id = proposed["proposal"]["id"]
            diff = load_stdout(run_cli("diff-proposal", str(project), "--proposal-id", proposal_id))
            assert_true(diff["diff"]["facts"]["create"], f"longform chain proposal {chapter} should create facts")
            applied = load_stdout(run_cli("apply-after-write", str(project), "--proposal-id", proposal_id))
            assert_true(applied["applied"]["facts"] >= 1, f"longform chain apply {chapter} missed facts")
            applied_facts += int(applied["applied"]["facts"])

        range_readiness = load_stdout(
            run_cli(
                "chapter-range-readiness",
                str(project),
                "--start",
                "4",
                "--end",
                "6",
                "--draft-dir",
                str(LONGFORM_CHAIN_EXAMPLE),
                "--file-template",
                "chapter-{chapter:03d}.md",
            )
        )
        assert_true(range_readiness["status"] in {"ready", "ready_with_notes"}, "longform chain chapters 4-6 should be ready or ready with notes")
        assert_true(range_readiness["summary"]["chapters"] == 3, "longform chain range readiness should inspect three chapters")
        assert_true(not range_readiness["summary"]["problem_chapters"], "longform chain range readiness should have no problem chapters")

        context7 = load_stdout(run_cli("build-context", str(project), "--chapter", "7"))
        assert_true(context7["previous_chapter"]["chapter"] == 6, "chapter 7 context missed chapter 6 tail")
        facts = context7["facts"]
        assert_true(any(row.get("subject") == "墨钥" and row.get("object") == "黎星" for row in facts), "chapter 7 context missed prop holder fact")
        assert_true(any(row.get("subject") == "黎星/秦疏" and row.get("object") == "盟友" for row in facts), "chapter 7 context missed relationship fact")
        assert_true(any(row.get("subject") == "秦疏" and row.get("object") == "活着" for row in facts), "chapter 7 context missed life state fact")
        assert_true(any(row.get("subject") == "银铃送信人" and row.get("object") == "白石旧渡信封" for row in facts), "chapter 7 context missed latest clue fact")
        assert_true(any(row.get("title") == "白石旧渡信封" for row in context7["open_debts"]), "chapter 7 context missed latest open debt")
        assert_true("第 7 章只能追查白石旧渡的信封来源" in context7["handoff"], "chapter 7 context missed handoff")
        session7 = load_stdout(run_cli("prepare-session", str(project), "--chapter", "7", "--platform", "codex", "--mode", "writer"))
        assert_true(session7["chapter_context"]["previous_chapter"]["chapter"] == 6, "chapter 7 session missed previous chapter")

        bad = LONGFORM_CHAIN_EXAMPLE / "bad-chapter-007.md"
        check = load_stdout(run_cli("check-chapter", str(project), "--chapter", "7", "--file", str(bad)))
        issue_types = {item["type"] for item in check["issues"]}
        expected_issues = {
            "missing_required",
            "forbidden_phrase",
            "future_marker",
            "fact_conflict",
            "prop_ownership_conflict",
            "relationship_state_conflict",
            "location_state_conflict",
            "life_state_conflict",
        }
        assert_true(expected_issues <= issue_types, f"longform chain missed expected issue types: {sorted(expected_issues - issue_types)}")
        check_plan = check.get("revision_plan", [])
        assert_true(check_plan, "check-chapter should return a revision_plan for bad chapter")
        assert_true(check_plan[0].get("group") == "canon_fact_conflicts", "revision_plan should start with canon fact conflicts")
        assert_true(check_plan[0].get("status") == "must_fix", "canon fact revision step should be must_fix")
        fact_issue_types = {
            "fact_conflict",
            "prop_ownership_conflict",
            "relationship_state_conflict",
            "location_state_conflict",
            "life_state_conflict",
        }
        fact_context_issues = [item for item in check["issues"] if item.get("type") in fact_issue_types]
        assert_true(fact_context_issues, "longform chain bad chapter should include fact-backed issues")
        for item in fact_context_issues:
            context = item.get("fact_context")
            assert_true(isinstance(context, dict), f"fact-backed issue missed fact_context: {item.get('type')}")
            assert_true(context.get("id") == item.get("fact_id"), f"fact_context id should match fact_id for {item.get('type')}")
            assert_true(context.get("expected_text"), f"fact_context missed expected text for {item.get('type')}")
            assert_true(context.get("subject") and context.get("object"), f"fact_context missed subject/object for {item.get('type')}")
        prop_context = next((item.get("fact_context") for item in fact_context_issues if item.get("type") == "prop_ownership_conflict"), {})
        assert_true(prop_context.get("subject") == "墨钥" and prop_context.get("object") == "黎星", "prop conflict fact_context should identify the recorded holder")
        readiness = load_stdout(run_cli("chapter-readiness", str(project), "--chapter", "7", "--file", str(bad)))
        readiness_fact_items = [
            item
            for item in readiness.get("must_fix", [])
            if isinstance(item, dict) and isinstance(item.get("fact_context"), dict)
        ]
        assert_true(readiness_fact_items, "chapter readiness should preserve fact_context in compact issue items")
        readiness_plan = readiness.get("revision_plan", [])
        assert_true(readiness_plan, "chapter readiness should return a revision_plan")
        assert_true(readiness_plan[0].get("group") == "canon_fact_conflicts", "chapter readiness revision_plan should start with canon fact conflicts")
        assert_true(readiness_plan[0].get("fact_contexts"), "chapter readiness revision_plan should include fact_contexts")
        readiness_plan_groups = {item.get("group") for item in readiness_plan if isinstance(item, dict)}
        assert_true(
            {"canon_fact_conflicts", "chapter_boundaries", "required_content"} <= readiness_plan_groups,
            "chapter readiness revision_plan missed expected repair groups",
        )
        revision_prompt = load_stdout(run_cli("chapter-revision-prompt", str(project), "--chapter", "7", "--file", str(bad)))
        assert_true(revision_prompt["type"] == "chapter_revision_prompt", "longform revision prompt returned wrong type")
        assert_true(revision_prompt["steps"], "longform revision prompt missed steps")
        assert_true(revision_prompt["steps"][0].get("group") == "canon_fact_conflicts", "longform revision prompt should start with canon facts")
        assert_true(
            any(step.get("fact_contexts") for step in revision_prompt.get("steps", []) if isinstance(step, dict)),
            "longform revision prompt missed fact contexts",
        )
        assert_true("Long Novel Agent Chapter Revision Prompt" in revision_prompt.get("prompt_markdown", ""), "longform revision prompt missed copyable text")
        revised_dir = project / "chapters"
        revised_dir.mkdir(parents=True, exist_ok=True)
        revised = revised_dir / "007.revised.md"
        revised.write_text(
            "黎星收好墨钥，没有交给旁人。秦疏仍活着，与黎星保持盟友关系。两人只追查白石旧渡的信封来源，银铃送信人的线索仍未揭开。",
            encoding="utf-8",
        )
        revision_compare = load_stdout(run_cli("chapter-revision-compare", str(project), "--chapter", "7", "--before", str(bad), "--after", str(revised)))
        assert_true(revision_compare["type"] == "chapter_revision_comparison", "longform revision compare returned wrong type")
        assert_true(revision_compare["read_only"] is True and revision_compare["writes_project_state"] is False, "longform revision compare should be read-only")
        assert_true(revision_compare["summary"]["resolved"] >= 1, "longform revision compare missed resolved issues")
        assert_true(
            revision_compare["summary"]["after"]["must_fix"] < revision_compare["summary"]["before"]["must_fix"],
            "longform revision compare should reduce must-fix issues",
        )
        assert_true(check["status"] != "pass", "longform chain bad chapter should not pass")
        return {
            "chapters_recorded": 6,
            "facts_applied": applied_facts,
            "chapter7_facts": len(context7["facts"]),
            "range_readiness_status": range_readiness["status"],
            "open_debts": len(context7["open_debts"]),
            "fact_context_issue_count": len(fact_context_issues),
            "readiness_fact_context_items": len(readiness_fact_items),
            "revision_plan_steps": len(check_plan),
            "readiness_revision_plan_steps": len(readiness_plan),
            "revision_prompt_steps": len(revision_prompt["steps"]),
            "revision_compare_status": revision_compare["status"],
            "revision_compare_resolved": revision_compare["summary"]["resolved"],
            "bad_issue_types": sorted(issue_types),
        }


def verify_longform_twenty_chapter_stress() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-20-chapter-stress-") as tmp:
        project = Path(tmp) / "twenty-chapter-project"
        chapters_dir = Path(tmp) / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        run_cli(
            "init-wizard",
            str(project),
            "--title",
            "Longform Twenty Chapter Stress",
            "--target-chapters",
            "30",
            "--required",
            "河灯纸买主",
            "--forbidden",
            "打开钟楼密室",
            "--future-marker",
            "账册缺页真相",
            "--agent-id",
            "stress-agent-a",
            "--platform",
            "codex",
            "--capabilities",
            "mcp,cli,file-read,file-write,long-context",
        )
        clue_chain = [
            "河灯纸灰",
            "雾桥暗号",
            "银铃送信人",
            "白石旧渡信封",
            "旧渡蜡封",
            "渡口账房",
            "旧税栈后堂",
            "盐墨印泥",
            "白石商会空印契",
            "青纹账袋",
            "潮档馆副册",
            "渡船刻痕",
            "夜市茶摊证词",
            "旧巡检名牌",
            "南岸灯绳",
            "灰线封条",
            "暗码铜片",
            "回声码头账房",
            "空印契尾页",
            "契尾见证人",
            "终局见证人",
        ]
        applied_facts = 0
        for chapter in range(1, 21):
            clue = clue_chain[chapter - 1]
            next_clue = clue_chain[chapter]
            safe_location = "潮档馆后门"
            clue_evidence = f"黎星追查河灯纸买主留下的线索，确认第 {chapter} 枚线索是「{clue}」。"
            life_evidence = "秦疏活着，在潮档馆后门与黎星保持盟友关系。"
            prop_evidence = "黎星把墨钥贴身收好，没有交给旁人。"
            handoff_evidence = f"她只把{next_clue}写进交接，继续封存钟楼旧门，也不说出缺页答案。"
            location_evidence = f"黎星把线索带到{safe_location}复核。"
            extra_evidence = "陆衡被登记在钟楼密室外的旧名单里。"
            chapter_text = "\n\n".join(
                [
                    f"# 第 {chapter} 章",
                    clue_evidence,
                    life_evidence,
                    prop_evidence,
                    location_evidence,
                    extra_evidence if chapter == 1 else "",
                    handoff_evidence,
                ]
            ).replace("\n\n\n", "\n\n")
            chapter_file = chapters_dir / f"{chapter:03d}.md"
            chapter_file.write_text(chapter_text, encoding="utf-8")
            facts = [
                {
                    "kind": "event",
                    "subject": f"第 {chapter} 枚线索",
                    "predicate": "points_to",
                    "object": next_clue,
                    "chapter_range": f"{chapter}+",
                    "evidence": clue_evidence,
                },
                {
                    "kind": "prop",
                    "subject": "墨钥",
                    "predicate": "holder",
                    "object": "黎星",
                    "chapter_range": f"{chapter}+",
                    "forbidden_phrases": ["墨钥交给白石商会", "墨钥交给陆衡"],
                    "evidence": prop_evidence,
                },
                {
                    "kind": "relationship",
                    "subject": "黎星/秦疏",
                    "predicate": "relation",
                    "object": "盟友",
                    "chapter_range": f"{chapter}+",
                    "evidence": life_evidence,
                },
                {
                    "kind": "life_state",
                    "subject": "秦疏",
                    "predicate": "status",
                    "object": "活着",
                    "chapter_range": f"{chapter}+",
                    "evidence": life_evidence,
                },
                {
                    "kind": "location",
                    "subject": "黎星",
                    "predicate": "location",
                    "object": safe_location,
                    "chapter_range": f"{chapter}+",
                    "evidence": location_evidence,
                },
            ]
            if chapter == 1:
                facts.append(
                    {
                        "kind": "location",
                        "subject": "陆衡",
                        "predicate": "location",
                        "object": "钟楼密室",
                        "chapter_range": "1+",
                        "evidence": extra_evidence,
                    }
                )
            updates = {
                "facts": facts,
                "characters": [
                    {
                        "name": "黎星",
                        "state": f"持有墨钥，追查到{clue}，下一步查{next_clue}。",
                        "evidence": prop_evidence,
                    },
                    {
                        "name": "秦疏",
                        "state": "活着，与黎星保持盟友关系。",
                        "evidence": life_evidence,
                    },
                ],
                "debts": [
                    {
                        "title": next_clue,
                        "detail": f"第 {chapter + 1} 章只能追查{next_clue}，不能揭示缺页答案。",
                        "status": "open",
                        "evidence": handoff_evidence,
                    }
                ],
                "handoff": f"第 {chapter + 1} 章只能追查{next_clue}；墨钥仍归黎星；秦疏必须活着；不得揭示缺页答案。",
            }
            updates_file = chapters_dir / f"updates-{chapter:03d}.json"
            updates_file.write_text(json.dumps(updates, ensure_ascii=False, indent=2), encoding="utf-8")
            run_cli(
                "record-chapter",
                str(project),
                "--chapter",
                str(chapter),
                "--file",
                str(chapter_file),
                "--handoff",
                updates["handoff"],
            )
            proposed = load_stdout(
                run_cli(
                    "propose-after-write",
                    str(project),
                    "--chapter",
                    str(chapter),
                    "--file",
                    str(chapter_file),
                    "--updates",
                    str(updates_file),
                )
            )
            assert_true(proposed["validation"]["ok"], f"20-chapter stress proposal {chapter} should validate")
            proposal_id = proposed["proposal"]["id"]
            applied = load_stdout(run_cli("apply-after-write", str(project), "--proposal-id", proposal_id))
            assert_true(applied["applied"]["facts"] >= 5, f"20-chapter stress apply {chapter} missed facts")
            applied_facts += int(applied["applied"]["facts"])

        range_readiness = load_stdout(
            run_cli(
                "chapter-range-readiness",
                str(project),
                "--start",
                "18",
                "--end",
                "20",
                "--draft-dir",
                str(chapters_dir),
                "--file-template",
                "{chapter:03d}.md",
            )
        )
        assert_true(range_readiness["status"] in {"ready", "ready_with_notes"}, "20-chapter stress chapters 18-20 should be ready")
        assert_true(range_readiness["summary"]["chapters"] == 3, "20-chapter stress range readiness should inspect three chapters")
        assert_true(not range_readiness["summary"]["problem_chapters"], "20-chapter stress range readiness should have no problem chapters")

        context21 = load_stdout(run_cli("build-context", str(project), "--chapter", "21"))
        assert_true(context21["previous_chapter"]["chapter"] == 20, "chapter 21 context missed chapter 20 tail")
        facts21 = context21["facts"]
        assert_true(any(row.get("subject") == "第 20 枚线索" and row.get("object") == "终局见证人" for row in facts21), "chapter 21 context missed chapter 20 clue")
        assert_true(any(row.get("subject") == "墨钥" and row.get("object") == "黎星" for row in facts21), "chapter 21 context missed prop holder")
        assert_true(any(row.get("subject") == "黎星/秦疏" and row.get("object") == "盟友" for row in facts21), "chapter 21 context missed relationship")
        assert_true(any(row.get("subject") == "秦疏" and row.get("object") == "活着" for row in facts21), "chapter 21 context missed life state")
        assert_true(any(row.get("title") == "终局见证人" for row in context21["open_debts"]), "chapter 21 context missed latest open debt")
        assert_true("第 21 章只能追查终局见证人" in context21["handoff"], "chapter 21 context missed handoff")
        budget21 = context21.get("context_budget") if isinstance(context21.get("context_budget"), dict) else {}
        assert_true(budget21.get("type") == "chapter_context_budget", "chapter 21 context missed context budget")
        assert_true(budget21.get("status") == "warning", "20-chapter stress context budget should warn on high fact count")
        assert_true(budget21.get("needs_compression") is True, "20-chapter stress context budget should recommend compression")
        assert_true((budget21.get("counts") or {}).get("facts") == len(facts21), "chapter 21 context budget fact count mismatch")
        assert_true((budget21.get("total") or {}).get("chars", 0) > 0, "chapter 21 context budget missed char estimate")
        assert_true(budget21.get("sections") and budget21["sections"][0].get("chars", 0) >= budget21["sections"][-1].get("chars", 0), "chapter 21 context budget sections should be sorted by size")
        brief21 = context21.get("context_brief") if isinstance(context21.get("context_brief"), dict) else {}
        assert_true(brief21.get("type") == "chapter_context_brief", "chapter 21 context missed context brief")
        assert_true(brief21.get("read_this_first") is True, "chapter 21 context brief should be marked read-first")
        assert_true((brief21.get("counts") or {}).get("facts_total") == len(facts21), "chapter 21 context brief fact total mismatch")
        assert_true((brief21.get("counts") or {}).get("facts_in_brief") == 24, "chapter 21 context brief should cap high-priority facts")
        assert_true((brief21.get("counts") or {}).get("open_debts_total") == len(context21["open_debts"]), "chapter 21 context brief debt total mismatch")
        assert_true((brief21.get("counts") or {}).get("open_debts_in_brief") == 12, "chapter 21 context brief should cap open debts")
        assert_true((brief21.get("size") or {}).get("chars", 0) < (budget21.get("total") or {}).get("chars", 0), "chapter 21 context brief should be smaller than full context budget")
        high_priority = brief21.get("high_priority_facts") if isinstance(brief21.get("high_priority_facts"), list) else []
        assert_true(
            any(row.get("kind") == "life_state" and row.get("subject") == "秦疏" and row.get("object") == "活着" for row in high_priority),
            "chapter 21 context brief missed life state",
        )
        assert_true(
            any(row.get("kind") == "prop" and row.get("subject") == "墨钥" and row.get("object") == "黎星" for row in high_priority),
            "chapter 21 context brief missed prop holder",
        )
        session21 = load_stdout(run_cli("prepare-session", str(project), "--chapter", "21", "--platform", "codex", "--mode", "writer"))
        assert_true(session21["chapter_context"]["previous_chapter"]["chapter"] == 20, "chapter 21 session missed previous chapter")
        assert_true(session21["chapter_context"]["context_budget"]["status"] == "warning", "chapter 21 session missed context budget warning")
        assert_true(session21["chapter_context"]["context_brief"]["type"] == "chapter_context_brief", "chapter 21 session missed context brief")
        assert_true(any("chapter context budget is warning" in str(item) for item in session21.get("warnings", [])), "chapter 21 session warnings missed context budget")
        summary21 = load_stdout(run_cli("prepare-session", str(project), "--chapter", "21", "--platform", "codex", "--mode", "writer", "--summary-only"))
        assert_true(
            summary21["chapter_context_summary"]["context_brief"]["type"] == "chapter_context_brief",
            "chapter 21 summary session missed context brief",
        )

        bad = chapters_dir / "021.bad.md"
        bad.write_text(
            "黎星把墨钥交给白石商会，又在钟楼密室宣布账册缺页真相。\n\n"
            "秦疏与黎星决裂后当场死亡。\n\n"
            "众人打开钟楼密室，终局见证人不再需要追查。",
            encoding="utf-8",
        )
        check = load_stdout(run_cli("check-chapter", str(project), "--chapter", "21", "--file", str(bad)))
        issue_types = {item["type"] for item in check["issues"]}
        expected_issues = {
            "missing_required",
            "forbidden_phrase",
            "future_marker",
            "fact_conflict",
            "prop_ownership_conflict",
            "relationship_state_conflict",
            "location_state_conflict",
            "life_state_conflict",
        }
        assert_true(expected_issues <= issue_types, f"20-chapter stress missed expected issue types: {sorted(expected_issues - issue_types)}")
        fact_context_issues = [
            item
            for item in check["issues"]
            if item.get("type") in {
                "fact_conflict",
                "prop_ownership_conflict",
                "relationship_state_conflict",
                "location_state_conflict",
                "life_state_conflict",
            }
        ]
        assert_true(fact_context_issues, "20-chapter stress bad chapter should include fact-backed issues")
        assert_true(all(isinstance(item.get("fact_context"), dict) for item in fact_context_issues), "20-chapter stress fact issues need fact_context")
        readiness = load_stdout(run_cli("chapter-readiness", str(project), "--chapter", "21", "--file", str(bad)))
        assert_true(readiness["status"] == "needs_revision", "20-chapter stress bad chapter should need revision")
        return {
            "chapters_recorded": 20,
            "facts_applied": applied_facts,
            "chapter21_facts": len(facts21),
            "chapter21_open_debts": len(context21["open_debts"]),
            "context_budget_status": budget21.get("status"),
            "context_budget_chars": (budget21.get("total") or {}).get("chars", 0),
            "context_brief_chars": (brief21.get("size") or {}).get("chars", 0),
            "context_brief_facts": (brief21.get("counts") or {}).get("facts_in_brief", 0),
            "context_brief_open_debts": (brief21.get("counts") or {}).get("open_debts_in_brief", 0),
            "range_readiness_status": range_readiness["status"],
            "bad_issue_types": sorted(issue_types),
            "fact_context_issue_count": len(fact_context_issues),
            "readiness_status": readiness["status"],
        }


def verify_context_brief_chapter_range_priority() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-context-brief-priority-") as tmp:
        project = Path(tmp) / "brief-priority"
        run_cli("init", str(project), "--title", "Brief Priority")
        run_cli(
            "add-fact",
            str(project),
            "--kind",
            "prop",
            "--subject",
            "A无边界旧钥",
            "--predicate",
            "holder",
            "--object",
            "旧看守",
        )
        run_cli(
            "add-fact",
            str(project),
            "--kind",
            "prop",
            "--subject",
            "Z本章范围钥",
            "--predicate",
            "holder",
            "--object",
            "目标章主角",
            "--chapter-range",
            "12-14",
        )
        context = load_stdout(run_cli("build-context", str(project), "--chapter", "13"))
        brief = context.get("context_brief") if isinstance(context.get("context_brief"), dict) else {}
        high_priority = brief.get("high_priority_facts") if isinstance(brief.get("high_priority_facts"), list) else []
        prop_rows = [row for row in high_priority if isinstance(row, dict) and row.get("kind") == "prop"]
        assert_true(len(prop_rows) >= 2, "context brief priority smoke should include both prop facts")
        assert_true(prop_rows[0].get("subject") == "Z本章范围钥", "context brief should prioritize chapter-bounded fact over unbounded fact")
        assert_true(prop_rows[0].get("chapter_distance") == 0, "context brief should expose chapter distance for chapter-bounded fact")
        assert_true(isinstance(prop_rows[0].get("chapter_ranges"), list) and prop_rows[0]["chapter_ranges"], "context brief should preserve structured chapter ranges")
        assert_true(prop_rows[1].get("subject") == "A无边界旧钥", "context brief should keep unbounded fact after chapter-bounded fact")
        brief_only = load_stdout(run_cli("context-brief", str(project), "--chapter", "13"))
        brief_only_rows = brief_only.get("high_priority_facts") if isinstance(brief_only.get("high_priority_facts"), list) else []
        assert_true(brief_only.get("type") == "chapter_context_brief", "context-brief should return a chapter context brief")
        assert_true("characters" not in brief_only and "facts" not in brief_only, "context-brief should not return full chapter context tables")
        assert_true(brief_only_rows[0].get("subject") == "Z本章范围钥", "context-brief should preserve chapter-range priority")
        assert_true(brief_only.get("context_budget_summary", {}).get("status") in {"ok", "warning", "critical"}, "context-brief should include budget summary")
        brief_markdown = run_cli("context-brief", str(project), "--chapter", "13", "--format", "markdown").stdout
        assert_true("# Chapter 13 Context Brief" in brief_markdown, "context-brief markdown should use brief renderer")
        assert_true("Z本章范围钥" in brief_markdown, "context-brief markdown should include prioritized fact")
        session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "13", "--summary-only"))
        summary_rows = session["chapter_context_summary"]["context_brief"]["high_priority_facts"]
        assert_true(summary_rows[0].get("subject") == "Z本章范围钥", "summary context brief should preserve chapter-range priority")
        return {
            "target_chapter": 13,
            "first_fact": prop_rows[0].get("subject"),
            "first_fact_distance": prop_rows[0].get("chapter_distance"),
            "first_fact_ranges": len(prop_rows[0].get("chapter_ranges") or []),
            "unbounded_fact_position": 2,
            "context_brief_cli_type": brief_only.get("type"),
            "context_brief_cli_chars": (brief_only.get("size") or {}).get("chars", 0),
        }


def verify_desktop_verification_evidence_example() -> dict[str, Any]:
    results_path = DESKTOP_EVIDENCE_EXAMPLE / "desktop-codex-read-only-chapter-2-results.json"
    weak_results_path = DESKTOP_EVIDENCE_EXAMPLE / "desktop-codex-read-only-weak-evidence-results.json"
    writer_results_path = DESKTOP_EVIDENCE_EXAMPLE / "desktop-cursor-writer-chapter-2-results.json"
    writer_missing_preflight_results_path = DESKTOP_EVIDENCE_EXAMPLE / "desktop-cursor-writer-missing-write-session-preflight-results.json"
    writer_missing_author_results_path = DESKTOP_EVIDENCE_EXAMPLE / "desktop-cursor-writer-missing-author-confirmation-results.json"
    writer_confirmation_only_results_path = DESKTOP_EVIDENCE_EXAMPLE / "desktop-cursor-writer-confirmation-only-results.json"
    record_path = DESKTOP_EVIDENCE_EXAMPLE / "desktop_verifications.jsonl"
    readme_path = DESKTOP_EVIDENCE_EXAMPLE / "README.md"
    transcript_path = DESKTOP_EVIDENCE_EXAMPLE / "codex-read-only-evidence.txt"
    writer_transcript_path = DESKTOP_EVIDENCE_EXAMPLE / "cursor-writer-author-confirmation-evidence.txt"
    assert_true(readme_path.exists(), "desktop evidence example README is missing")
    assert_true(weak_results_path.exists(), "desktop weak evidence example results are missing")
    assert_true(writer_missing_preflight_results_path.exists(), "desktop writer missing write-session preflight example results are missing")
    assert_true(writer_missing_author_results_path.exists(), "desktop writer missing author-confirmation example results are missing")
    assert_true(writer_confirmation_only_results_path.exists(), "desktop writer confirmation-only example results are missing")
    assert_true(transcript_path.exists(), "desktop evidence transcript example is missing")
    assert_true(writer_transcript_path.exists(), "desktop writer evidence transcript example is missing")
    results_payload = json.loads(results_path.read_text(encoding="utf-8"))
    weak_results_payload = json.loads(weak_results_path.read_text(encoding="utf-8"))
    writer_payload = json.loads(writer_results_path.read_text(encoding="utf-8"))
    writer_missing_preflight_payload = json.loads(writer_missing_preflight_results_path.read_text(encoding="utf-8"))
    writer_missing_author_payload = json.loads(writer_missing_author_results_path.read_text(encoding="utf-8"))
    writer_confirmation_only_payload = json.loads(writer_confirmation_only_results_path.read_text(encoding="utf-8"))
    checks = results_payload.get("checks") if isinstance(results_payload, dict) else []
    weak_checks = weak_results_payload.get("checks") if isinstance(weak_results_payload, dict) else []
    writer_checks = writer_payload.get("checks") if isinstance(writer_payload, dict) else []
    writer_missing_preflight_checks = writer_missing_preflight_payload.get("checks") if isinstance(writer_missing_preflight_payload, dict) else []
    writer_missing_author_checks = writer_missing_author_payload.get("checks") if isinstance(writer_missing_author_payload, dict) else []
    writer_confirmation_only_checks = writer_confirmation_only_payload.get("checks") if isinstance(writer_confirmation_only_payload, dict) else []
    assert_true(isinstance(checks, list) and checks, "desktop evidence example results are empty")
    assert_true(isinstance(weak_checks, list) and weak_checks, "desktop weak evidence example results are empty")
    assert_true(isinstance(writer_checks, list) and writer_checks, "desktop writer evidence example results are empty")
    assert_true(isinstance(writer_missing_preflight_checks, list) and writer_missing_preflight_checks, "desktop writer missing write-session preflight example results are empty")
    assert_true(isinstance(writer_missing_author_checks, list) and writer_missing_author_checks, "desktop writer missing author-confirmation example results are empty")
    assert_true(isinstance(writer_confirmation_only_checks, list) and writer_confirmation_only_checks, "desktop writer confirmation-only example results are empty")
    assert_true(all(item.get("status") == "passed" for item in checks if isinstance(item, dict)), "desktop evidence example should contain passed checks")
    assert_true(all(item.get("status") == "passed" for item in weak_checks if isinstance(item, dict)), "desktop weak evidence example should contain passed checks")
    assert_true(all(item.get("status") == "passed" for item in writer_checks if isinstance(item, dict)), "desktop writer evidence example should contain passed checks")
    assert_true(all(item.get("status") == "passed" for item in writer_missing_preflight_checks if isinstance(item, dict)), "desktop writer missing write-session preflight example should contain passed checks")
    assert_true(all(item.get("status") == "passed" for item in writer_missing_author_checks if isinstance(item, dict)), "desktop writer missing author-confirmation example should contain passed checks")
    assert_true(all(item.get("status") == "passed" for item in writer_confirmation_only_checks if isinstance(item, dict)), "desktop writer confirmation-only example should contain passed checks")
    assert_true(all(str(item.get("evidence") or "").strip() for item in checks if isinstance(item, dict)), "desktop evidence example checks need evidence")
    assert_true(all(str(item.get("evidence") or "").strip() for item in writer_checks if isinstance(item, dict)), "desktop writer evidence example checks need evidence")
    acceptance_check = next((item for item in writer_checks if item.get("id") == "acceptance_write_gate"), {})
    write_session_check = next((item for item in writer_checks if item.get("id") == "write_session_preflight_gate"), {})
    proposal_readiness_check = next((item for item in writer_checks if item.get("id") == "proposal_readiness_gate"), {})
    range_check = next((item for item in checks if item.get("id") == "range_readiness_gate"), {})
    writer_range_check = next((item for item in writer_checks if item.get("id") == "range_readiness_gate"), {})
    assert_true("build_chapter_range_readiness" in str(range_check.get("evidence") or ""), "desktop evidence should mention build_chapter_range_readiness")
    assert_true("build_chapter_range_readiness" in str(writer_range_check.get("evidence") or ""), "desktop writer evidence should mention build_chapter_range_readiness")
    assert_true("check_write_session" in str(write_session_check.get("evidence") or ""), "desktop writer evidence should mention check_write_session")
    assert_true("build_proposal_readiness" in str(proposal_readiness_check.get("evidence") or ""), "desktop writer evidence should mention build_proposal_readiness")
    assert_true("author_confirmation" in str(acceptance_check.get("evidence") or ""), "desktop writer evidence should mention author_confirmation")
    record_rows = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert_true(len(record_rows) == 2, "desktop evidence example should contain read-only and writer JSONL records")
    sample_record = next((row for row in record_rows if row.get("mode") == "read-only"), {})
    writer_record = next((row for row in record_rows if row.get("mode") == "writer"), {})
    assert_true(sample_record.get("ok"), "desktop evidence example record should be ok")
    assert_true(writer_record.get("ok"), "desktop writer evidence example record should be ok")
    assert_true(sample_record.get("required_passed_with_evidence") == sample_record.get("required_total"), "desktop evidence example record should pass all required checks")
    assert_true(writer_record.get("required_passed_with_evidence") == writer_record.get("required_total") == 11, "desktop writer evidence example record should pass all required checks")
    with tempfile.TemporaryDirectory(prefix="long-novel-desktop-evidence-") as tmp:
        project = Path(tmp) / "novel"
        run_cli("init", str(project), "--title", "desktop-evidence-example")
        checklist = load_stdout(run_cli("desktop-checklist", str(project), "--platform", "codex", "--mode", "read-only", "--chapter", "2", "--format", "json"))
        expected_ids = [item["id"] for item in checklist["manual_result_template"]]
        observed_ids = [item["id"] for item in checks]
        observed_weak_ids = [item["id"] for item in weak_checks]
        assert_true(observed_ids == expected_ids, "desktop evidence example check ids do not match generated checklist")
        assert_true(observed_weak_ids == expected_ids, "desktop weak evidence example check ids do not match generated checklist")
        writer_checklist = load_stdout(run_cli("desktop-checklist", str(project), "--platform", "cursor", "--mode", "writer", "--chapter", "2", "--format", "json"))
        expected_writer_ids = [item["id"] for item in writer_checklist["manual_result_template"]]
        observed_writer_ids = [item["id"] for item in writer_checks]
        observed_writer_missing_preflight_ids = [item["id"] for item in writer_missing_preflight_checks]
        observed_writer_missing_author_ids = [item["id"] for item in writer_missing_author_checks]
        observed_writer_confirmation_only_ids = [item["id"] for item in writer_confirmation_only_checks]
        assert_true(observed_writer_ids == expected_writer_ids, "desktop writer evidence example check ids do not match generated checklist")
        assert_true(observed_writer_missing_preflight_ids == expected_writer_ids, "desktop writer missing write-session preflight example check ids do not match generated checklist")
        assert_true(observed_writer_missing_author_ids == expected_writer_ids, "desktop writer missing author-confirmation example check ids do not match generated checklist")
        assert_true(observed_writer_confirmation_only_ids == expected_writer_ids, "desktop writer confirmation-only example check ids do not match generated checklist")
        imported = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                str(transcript_path),
                "--format",
                "json",
            )
        )
        assert_true(imported["ok"], "desktop evidence transcript should import as complete")
        assert_true(imported["matched_check_ids"] == expected_ids, "desktop evidence transcript import missed expected checks")
        imported_writer = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--evidence",
                str(writer_transcript_path),
                "--format",
                "json",
            )
        )
        assert_true(imported_writer["ok"], "desktop writer evidence transcript should import as complete")
        assert_true(imported_writer["matched_check_ids"] == expected_writer_ids, "desktop writer evidence transcript import missed expected checks")
        recorded = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                str(results_path),
                "--agent-id",
                "codex-local-demo",
                "--client-version",
                "Codex Desktop redacted example",
            )
        )
        assert_true(recorded["validation"]["ok"], "desktop evidence example should record successfully")
        recorded_writer = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                str(writer_results_path),
                "--agent-id",
                "cursor-local-demo",
                "--client-version",
                "Cursor redacted example",
            )
        )
        assert_true(recorded_writer["validation"]["ok"], "desktop writer evidence example should record successfully")
        status = load_stdout(run_cli("project-status", str(project), "--platform", "codex"))
        assert_true(status["verification_status"]["real_desktop_client"]["status"] == "passed", "desktop evidence example should mark real client evidence passed")
        writer_status = load_stdout(run_cli("project-status", str(project), "--platform", "cursor"))
        assert_true(writer_status["verification_status"]["real_desktop_client"]["status"] == "passed", "desktop writer evidence example should mark real client evidence passed")
        writer_readiness_missing_author_run = run_cli(
            "writer-mode-readiness",
            str(project),
            "--platform",
            "codex",
            "--chapter",
            "2",
            "--format",
            "json",
            check=False,
        )
        assert_true(writer_readiness_missing_author_run.returncode == 1, "writer-mode-readiness should return non-zero without author confirmation")
        writer_readiness_missing_author = load_stdout(writer_readiness_missing_author_run)
        assert_true(writer_readiness_missing_author["status"] == "needs_author_review", "writer-mode-readiness should require author review before writer mode")
        assert_true(not writer_readiness_missing_author["would_write_project_state"], "writer-mode-readiness must not write project state")
        assert_true(not writer_readiness_missing_author["would_write_install_config"], "writer-mode-readiness must not write install config")
        writer_readiness = load_stdout(
            run_cli(
                "writer-mode-readiness",
                str(project),
                "--platform",
                "codex",
                "--chapter",
                "2",
                "--author-confirmation",
                "Author approved enabling writer MCP mode for this desktop agent.",
                "--format",
                "json",
            )
        )
        assert_true(writer_readiness["ok"], "writer-mode-readiness should allow writer mode after read-only proof and author confirmation")
        assert_true(writer_readiness["status"] in {"ready_to_enable_writer", "ready_with_warnings"}, "writer-mode-readiness returned unexpected ready status")
        assert_true(writer_readiness["read_only_verification"]["status"] == "passed", "writer-mode-readiness missed read-only real client proof")
        writer_readiness_md = run_cli(
            "writer-mode-readiness",
            str(project),
            "--platform",
            "codex",
            "--chapter",
            "2",
            "--author-confirmation",
            "Author approved enabling writer MCP mode for this desktop agent.",
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Writer Mode Readiness" in writer_readiness_md.stdout, "writer-mode-readiness markdown missed title")
        assert_true("writer-mode-approval.md" in writer_readiness_md.stdout, "writer-mode-readiness markdown missed approval guidance")
        cursor_writer_readiness = load_stdout(
            run_cli(
                "writer-mode-readiness",
                str(project),
                "--platform",
                "cursor",
                "--chapter",
                "2",
                "--format",
                "json",
            )
        )
        assert_true(cursor_writer_readiness["status"] == "writer_verified", "writer-mode-readiness should recognize saved writer proof")
        listed = load_stdout(run_cli("list-desktop-checks", str(project), "--platform", "codex"))
        assert_true(len(listed["records"]) == 1, "desktop evidence example should create one verification record")
        listed_writer = load_stdout(run_cli("list-desktop-checks", str(project), "--platform", "cursor"))
        assert_true(len(listed_writer["records"]) == 1, "desktop writer evidence example should create one verification record")
        writer_missing_preflight_payload = json.loads(json.dumps(writer_payload, ensure_ascii=False))
        for item in writer_missing_preflight_payload["checks"]:
            if item.get("id") == "write_session_preflight_gate":
                item["evidence"] = "Before durable writer commands, the desktop agent reported the current project ID, state hash, chapter context hash, and status looked ready."
        missing_preflight_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                json.dumps(writer_missing_preflight_payload, ensure_ascii=False),
                "--agent-id",
                "cursor-local-demo",
                "--client-version",
                "Cursor redacted example",
            )
        )
        assert_true(not missing_preflight_record["validation"]["ok"], "desktop writer check should reject write-session preflight evidence without check_write_session")
        assert_true(
            any(item.get("type") == "missing_write_session_preflight_evidence" for item in missing_preflight_record["validation"]["issues"]),
            "desktop writer check should report missing write-session preflight evidence",
        )
        missing_preflight_sample_doctor_run = run_cli(
            "desktop-results-doctor",
            str(project),
            "--platform",
            "cursor",
            "--mode",
            "writer",
            "--chapter",
            "2",
            "--results",
            str(writer_missing_preflight_results_path),
            "--format",
            "json",
            check=False,
        )
        assert_true(missing_preflight_sample_doctor_run.returncode != 0, "desktop writer missing write-session preflight sample should fail desktop-results-doctor")
        missing_preflight_sample_doctor = load_stdout(missing_preflight_sample_doctor_run)
        assert_true(missing_preflight_sample_doctor["status"] == "needs_revision", "desktop writer missing write-session preflight sample should need revision")
        assert_true(
            any(item.get("type") == "missing_write_session_preflight_evidence" for item in missing_preflight_sample_doctor["issues"]),
            "desktop writer missing write-session preflight sample should report missing_write_session_preflight_evidence",
        )
        missing_preflight_sample_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                str(writer_missing_preflight_results_path),
                "--agent-id",
                "cursor-local-demo",
                "--client-version",
                "Cursor missing write-session preflight example",
            )
        )
        assert_true(not missing_preflight_sample_record["validation"]["ok"], "desktop writer missing write-session preflight sample should record as invalid")
        assert_true(
            any(item.get("type") == "missing_write_session_preflight_evidence" for item in missing_preflight_sample_record["validation"]["issues"]),
            "desktop writer missing write-session preflight sample record should report missing_write_session_preflight_evidence",
        )
        writer_missing_author_payload = json.loads(json.dumps(writer_payload, ensure_ascii=False))
        for item in writer_missing_author_payload["checks"]:
            if item.get("id") == "acceptance_write_gate":
                item["evidence"] = "After author acceptance, the desktop agent called record_chapter, propose_after_write_updates, build_proposal_readiness, and apply_after_write_updates."
        missing_author_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                json.dumps(writer_missing_author_payload, ensure_ascii=False),
                "--agent-id",
                "cursor-local-demo",
                "--client-version",
                "Cursor redacted example",
            )
        )
        assert_true(not missing_author_record["validation"]["ok"], "desktop writer check should reject acceptance evidence without author_confirmation")
        assert_true(
            any(item.get("type") == "missing_author_confirmation_evidence" for item in missing_author_record["validation"]["issues"]),
            "desktop writer check should report missing author_confirmation evidence",
        )
        missing_author_sample_doctor_run = run_cli(
            "desktop-results-doctor",
            str(project),
            "--platform",
            "cursor",
            "--mode",
            "writer",
            "--chapter",
            "2",
            "--results",
            str(writer_missing_author_results_path),
            "--format",
            "json",
            check=False,
        )
        assert_true(missing_author_sample_doctor_run.returncode != 0, "desktop writer missing author-confirmation sample should fail desktop-results-doctor")
        missing_author_sample_doctor = load_stdout(missing_author_sample_doctor_run)
        assert_true(missing_author_sample_doctor["status"] == "needs_revision", "desktop writer missing author-confirmation sample should need revision")
        assert_true(
            any(item.get("type") == "missing_author_confirmation_evidence" for item in missing_author_sample_doctor["issues"]),
            "desktop writer missing author-confirmation sample should report missing_author_confirmation_evidence",
        )
        missing_author_sample_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                str(writer_missing_author_results_path),
                "--agent-id",
                "cursor-local-demo",
                "--client-version",
                "Cursor missing author-confirmation example",
            )
        )
        assert_true(not missing_author_sample_record["validation"]["ok"], "desktop writer missing author-confirmation sample should record as invalid")
        assert_true(
            any(item.get("type") == "missing_author_confirmation_evidence" for item in missing_author_sample_record["validation"]["issues"]),
            "desktop writer missing author-confirmation sample record should report missing_author_confirmation_evidence",
        )
        writer_confirmation_only_payload = json.loads(json.dumps(writer_payload, ensure_ascii=False))
        for item in writer_confirmation_only_payload["checks"]:
            if item.get("id") == "acceptance_write_gate":
                item["evidence"] = "After author acceptance, the desktop agent reported author_confirmation=REDACTED."
        confirmation_only_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                json.dumps(writer_confirmation_only_payload, ensure_ascii=False),
                "--agent-id",
                "cursor-local-demo",
                "--client-version",
                "Cursor redacted example",
            )
        )
        assert_true(not confirmation_only_record["validation"]["ok"], "desktop writer check should reject author confirmation without durable write command")
        assert_true(
            any(item.get("type") == "missing_acceptance_write_operation_evidence" for item in confirmation_only_record["validation"]["issues"]),
            "desktop writer check should report missing acceptance write operation evidence",
        )
        confirmation_only_sample_doctor_run = run_cli(
            "desktop-results-doctor",
            str(project),
            "--platform",
            "cursor",
            "--mode",
            "writer",
            "--chapter",
            "2",
            "--results",
            str(writer_confirmation_only_results_path),
            "--format",
            "json",
            check=False,
        )
        assert_true(confirmation_only_sample_doctor_run.returncode != 0, "desktop writer confirmation-only sample should fail desktop-results-doctor")
        confirmation_only_sample_doctor = load_stdout(confirmation_only_sample_doctor_run)
        assert_true(confirmation_only_sample_doctor["status"] == "needs_revision", "desktop writer confirmation-only sample should need revision")
        assert_true(
            any(item.get("type") == "missing_acceptance_write_operation_evidence" for item in confirmation_only_sample_doctor["issues"]),
            "desktop writer confirmation-only sample should report missing_acceptance_write_operation_evidence",
        )
        confirmation_only_sample_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                str(writer_confirmation_only_results_path),
                "--agent-id",
                "cursor-local-demo",
                "--client-version",
                "Cursor confirmation-only example",
            )
        )
        assert_true(not confirmation_only_sample_record["validation"]["ok"], "desktop writer confirmation-only sample should record as invalid")
        assert_true(
            any(item.get("type") == "missing_acceptance_write_operation_evidence" for item in confirmation_only_sample_record["validation"]["issues"]),
            "desktop writer confirmation-only sample record should report missing_acceptance_write_operation_evidence",
        )
        writer_failed_status = load_stdout(run_cli("project-status", str(project), "--platform", "cursor"))
        writer_failed_real_status = writer_failed_status["verification_status"]["real_desktop_client"]
        assert_true(writer_failed_real_status["status"] == "failed", "project-status should expose failed writer desktop evidence")
        assert_true(
            any(item.get("type") == "missing_acceptance_write_operation_evidence" for item in writer_failed_real_status.get("issue_summary", [])),
            "project-status should expose writer acceptance write operation issue summary",
        )
        writer_failed_status_md = run_cli("project-status", str(project), "--platform", "cursor", "--format", "markdown")
        assert_true("missing_acceptance_write_operation_evidence" not in writer_failed_status_md.stdout, "project-status markdown should show user-facing issue text, not only raw type")
        assert_true("durable write command" in writer_failed_status_md.stdout, "project-status markdown missed writer operation issue")
        handoff_failed_md = run_cli(
            "handoff-readiness",
            str(project),
            "--chapter",
            "2",
            "--incoming-platform",
            "cursor",
            "--mode",
            "writer",
            "--format",
            "markdown",
        )
        assert_true("durable write command" in handoff_failed_md.stdout, "handoff-readiness markdown missed writer operation issue")
        writer_missing_readiness_payload = json.loads(json.dumps(writer_payload, ensure_ascii=False))
        for item in writer_missing_readiness_payload["checks"]:
            if item.get("id") == "proposal_readiness_gate":
                item["evidence"] = "Proposal looked ready in the desktop client before apply."
        missing_readiness_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--chapter",
                "2",
                "--results",
                json.dumps(writer_missing_readiness_payload, ensure_ascii=False),
                "--agent-id",
                "cursor-local-demo",
                "--client-version",
                "Cursor redacted example",
            )
        )
        assert_true(not missing_readiness_record["validation"]["ok"], "desktop writer check should reject proposal readiness evidence without build_proposal_readiness")
        assert_true(
            any(item.get("type") == "missing_proposal_readiness_evidence" for item in missing_readiness_record["validation"]["issues"]),
            "desktop writer check should report missing proposal readiness evidence",
        )
        missing_range_payload = json.loads(json.dumps(results_payload, ensure_ascii=False))
        for item in missing_range_payload["checks"]:
            if item.get("id") == "range_readiness_gate":
                item["evidence"] = "Recent draft chapters looked acceptable in the desktop client."
        missing_range_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                json.dumps(missing_range_payload, ensure_ascii=False),
                "--agent-id",
                "codex-local-demo",
                "--client-version",
                "Codex Desktop redacted example",
            )
        )
        assert_true(not missing_range_record["validation"]["ok"], "desktop read-only check should reject range readiness evidence without build_chapter_range_readiness")
        assert_true(
            any(item.get("type") == "missing_range_readiness_evidence" for item in missing_range_record["validation"]["issues"]),
            "desktop read-only check should report missing range readiness evidence",
        )
        weak_evidence_payload = json.loads(json.dumps(results_payload, ensure_ascii=False))
        weak_evidence_payload["checks"][0]["evidence"] = "ok"
        weak_evidence_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                json.dumps(weak_evidence_payload, ensure_ascii=False),
                "--agent-id",
                "codex-local-demo",
                "--client-version",
                "Codex Desktop redacted example",
            )
        )
        assert_true(not weak_evidence_record["validation"]["ok"], "desktop read-only check should reject generic evidence")
        assert_true(
            any(item.get("type") == "weak_evidence" for item in weak_evidence_record["validation"]["issues"]),
            "desktop read-only check should report weak evidence",
        )
        weak_sample_doctor_run = run_cli(
            "desktop-results-doctor",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--results",
            str(weak_results_path),
            "--format",
            "json",
            check=False,
        )
        assert_true(weak_sample_doctor_run.returncode != 0, "desktop weak evidence sample should fail desktop-results-doctor")
        weak_sample_doctor = load_stdout(weak_sample_doctor_run)
        assert_true(weak_sample_doctor["status"] == "needs_revision", "desktop weak evidence sample should need revision")
        assert_true(
            any(item.get("type") == "weak_evidence" for item in weak_sample_doctor["issues"]),
            "desktop weak evidence sample should report weak_evidence",
        )
        weak_sample_record = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                str(weak_results_path),
                "--agent-id",
                "codex-local-demo",
                "--client-version",
                "Codex Desktop weak evidence example",
            )
        )
        assert_true(not weak_sample_record["validation"]["ok"], "desktop weak evidence sample should record as invalid")
        assert_true(
            any(item.get("type") == "weak_evidence" for item in weak_sample_record["validation"]["issues"]),
            "desktop weak evidence sample record should report weak_evidence",
        )
        return {
            "checks": len(checks),
            "imported_checks": len(imported["matched_check_ids"]),
            "writer_checks": len(writer_checks),
            "writer_imported_checks": len(imported_writer["matched_check_ids"]),
            "writer_author_confirmation": True,
            "writer_missing_write_session_preflight_rejected": True,
            "writer_missing_write_session_preflight_sample_rejected": True,
            "writer_missing_author_confirmation_rejected": True,
            "writer_missing_author_confirmation_sample_rejected": True,
            "writer_confirmation_only_rejected": True,
            "writer_confirmation_only_sample_rejected": True,
            "writer_missing_proposal_readiness_rejected": True,
            "range_readiness_evidence_rejected": True,
            "weak_evidence_rejected": True,
            "weak_evidence_sample_rejected": True,
            "writer_status_issue_summary": True,
            "writer_readiness_missing_author": writer_readiness_missing_author["status"],
            "writer_readiness_status": writer_readiness["status"],
            "writer_readiness_saved_writer_status": cursor_writer_readiness["status"],
            "record_ok": recorded["validation"]["ok"],
            "writer_record_ok": recorded_writer["validation"]["ok"],
            "status": status["verification_status"]["real_desktop_client"]["status"],
            "writer_status": writer_status["verification_status"]["real_desktop_client"]["status"],
        }


def verify_proposal_guardrails() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-proposal-") as tmp:
        project = Path(tmp) / "novel"
        chapters = project / "chapters"
        chapters.mkdir(parents=True)
        run_cli("init", str(project), "--title", "proposal-test")
        schema = load_stdout(run_cli("proposal-schema"))
        assert_true(schema["schema"]["title"] == "Long Novel Agent Post-Write Proposal", "proposal schema title mismatch")
        run_cli("add-fact", str(project), "--kind", "prop", "--subject", "铜钥匙", "--predicate", "holder", "--object", "林追")
        chapter = chapters / "002.md"
        chapter.write_text("林追把铜钥匙收回袖中，发现账册缺页。", encoding="utf-8")

        missing_evidence = {
            "facts": [{"kind": "event", "subject": "第 2 章", "predicate": "result", "object": "林追发现账册缺页", "chapter_range": "2+"}],
            "handoff": "下一章追查账册缺页。",
        }
        proposal = load_stdout(
            run_cli("propose-after-write", str(project), "--chapter", "2", "--file", str(chapter), "--updates", json.dumps(missing_evidence, ensure_ascii=False))
        )
        assert_true(not proposal["validation"]["ok"], "proposal without evidence should fail validation")
        missing_readiness = load_stdout(run_cli("proposal-readiness", str(project), "--proposal-id", proposal["proposal"]["id"]))
        assert_true(missing_readiness["type"] == "proposal_readiness", "proposal-readiness returned wrong type")
        assert_true(missing_readiness["status"] == "needs_revision", "proposal-readiness should require revision for missing evidence")
        assert_true(not missing_readiness["ready_to_apply"], "proposal-readiness should block missing-evidence apply")
        missing_readiness_md = run_cli("proposal-readiness", str(project), "--proposal-id", proposal["proposal"]["id"], "--format", "markdown")
        assert_true("Long Novel Agent Proposal Readiness" in missing_readiness_md.stdout, "proposal-readiness markdown missing title")
        failed_apply = run_cli("apply-after-write", str(project), "--proposal-id", proposal["proposal"]["id"], check=False)
        assert_true(failed_apply.returncode != 0, "apply should reject proposal without evidence")

        wrong_evidence = {
            "facts": [
                {
                    "kind": "event",
                    "subject": "第 2 章",
                    "predicate": "result",
                    "object": "林追发现账册缺页",
                    "chapter_range": "2+",
                    "evidence": "林追把铜钥匙交给白石商会。",
                }
            ],
            "handoff": "下一章追查账册缺页。",
        }
        wrong = load_stdout(
            run_cli("propose-after-write", str(project), "--chapter", "2", "--file", str(chapter), "--updates", json.dumps(wrong_evidence, ensure_ascii=False))
        )
        wrong_types = {item["type"] for item in wrong["validation"]["issues"]}
        assert_true("evidence_not_in_chapter" in wrong_types, "proposal evidence outside chapter text should fail validation")

        inline_relative = {
            "chapter": 2,
            "file": "chapters/002.md",
            "facts": [
                {
                    "kind": "event",
                    "subject": "第 2 章",
                    "predicate": "result",
                    "object": "林追发现账册缺页",
                    "chapter_range": "2+",
                    "evidence": "正文里不存在的证据。",
                }
            ],
            "handoff": "下一章追查账册缺页。",
        }
        inline_validation = load_stdout(run_cli("validate-proposal", str(project), "--updates", json.dumps(inline_relative, ensure_ascii=False)))
        inline_types = {item["type"] for item in inline_validation["validation"]["issues"]}
        assert_true("evidence_not_in_chapter" in inline_types, "inline proposal relative file evidence should be checked")

        conflicting = {
            "facts": [{"kind": "prop", "subject": "铜钥匙", "predicate": "holder", "object": "白石商会", "evidence": "正文写成铜钥匙交给白石商会。"}],
            "handoff": "冲突提案。",
        }
        conflict_validation = load_stdout(run_cli("validate-proposal", str(project), "--updates", json.dumps(conflicting, ensure_ascii=False)))
        conflict_types = {item["type"] for item in conflict_validation["validation"]["issues"]}
        assert_true("fact_conflicts_existing" in conflict_types, "conflicting fact was not rejected")

        valid_updates = {
            "facts": [{"kind": "event", "subject": "第 2 章", "predicate": "result", "object": "林追发现账册缺页", "chapter_range": "2+", "evidence": "林追把铜钥匙收回袖中，发现账册缺页。"}],
            "characters": [{"name": "林追", "state": "保留铜钥匙，确认账册缺页", "evidence": "林追把铜钥匙收回袖中，发现账册缺页。"}],
            "debts": [{"title": "账册缺页", "detail": "缺页去向未解释。", "status": "open", "evidence": "发现账册缺页。"}],
            "handoff": "下一章追查账册缺页。",
        }
        template = load_stdout(run_cli("proposal-template", str(project), "--chapter", "2", "--file", str(chapter)))
        assert_true(template["template"]["facts"][0]["chapter_range"] == "2+", "proposal template missed chapter range")
        assert_true(template["schema"]["name"] == "proposal.schema.json", "proposal template missed schema reference")
        assert_true("context_summary" in template and "chapter_tail" in template, "proposal template missed context summary or chapter tail")

        valid = load_stdout(run_cli("propose-after-write", str(project), "--chapter", "2", "--file", str(chapter), "--updates", json.dumps(valid_updates, ensure_ascii=False)))
        assert_true(valid["validation"]["ok"], "valid proposal failed validation")
        review = load_stdout(run_cli("proposal-review", str(project), "--proposal-id", valid["proposal"]["id"]))
        assert_true(review["type"] == "proposal_review", "proposal-review returned wrong type")
        assert_true(review["ready_to_apply"], "proposal-review should mark valid proposal ready to apply")
        assert_true(review["summary"]["facts_create"] == 1, "proposal-review missed fact create count")
        readiness = load_stdout(run_cli("proposal-readiness", str(project), "--proposal-id", valid["proposal"]["id"]))
        assert_true(readiness["type"] == "proposal_readiness", "proposal-readiness returned wrong type for valid proposal")
        assert_true(readiness["status"] == "ready", "proposal-readiness should mark clean proposal ready")
        assert_true(readiness["ready_to_apply"], "proposal-readiness should allow clean proposal")
        assert_true("apply_after_author_confirmation" in readiness["recommended_commands"], "proposal-readiness missed apply command")
        readiness_md = run_cli("proposal-readiness", str(project), "--proposal-id", valid["proposal"]["id"], "--format", "markdown")
        assert_true("Long Novel Agent Proposal Readiness" in readiness_md.stdout, "proposal-readiness markdown missing title for valid proposal")
        review_md = run_cli("proposal-review", str(project), "--proposal-id", valid["proposal"]["id"], "--format", "markdown")
        assert_true("Long Novel Agent Proposal Review" in review_md.stdout, "proposal-review markdown missing title")
        review_dir = Path(tmp) / "proposal-review"
        review_written = load_stdout(run_cli("proposal-review", str(project), "--proposal-id", valid["proposal"]["id"], "--output-dir", str(review_dir)))
        written_names = {item["name"] for item in review_written["written_files"]}
        assert_true({"README.md", "proposal-review.html", "proposal-review.json", "proposal.json", "validation.json", "diff.json", "commands.txt"} <= written_names, "proposal-review missed written files")
        assert_true("Long Novel Agent Proposal Review" in (review_dir / "proposal-review.html").read_text(encoding="utf-8"), "proposal-review HTML missing title")
        diff = load_stdout(run_cli("diff-proposal", str(project), "--proposal-id", valid["proposal"]["id"]))
        assert_true(diff["diff"]["facts"]["create"], "diff missed fact create")
        high_risk_chapter = chapters / "003.md"
        high_risk_chapter.write_text("顾临死亡，林追确认账册缺页仍未解释。", encoding="utf-8")
        high_risk_updates = {
            "facts": [{"kind": "life_status", "subject": "顾临", "predicate": "life_status", "object": "死亡", "chapter_range": "3+", "evidence": "顾临死亡"}],
            "handoff": "下一章处理顾临死亡造成的局面。",
        }
        high_risk = load_stdout(
            run_cli("propose-after-write", str(project), "--chapter", "3", "--file", str(high_risk_chapter), "--updates", json.dumps(high_risk_updates, ensure_ascii=False))
        )
        high_risk_readiness = load_stdout(run_cli("proposal-readiness", str(project), "--proposal-id", high_risk["proposal"]["id"]))
        assert_true(high_risk_readiness["status"] == "needs_author_review", "high-risk proposal should require author review")
        assert_true(high_risk_readiness["author_review_required"], "high-risk proposal readiness missed author review flag")
        assert_true(not high_risk_readiness["ready_to_apply"], "high-risk proposal should not be directly ready")
        assert_true(high_risk_readiness["apply_allowed_after_author_review"], "high-risk proposal should be allowed after author review")
        author_queue = load_stdout(
            run_cli(
                "author-review-queue",
                str(project),
                "--chapter",
                "3",
                "--platform",
                "codex",
                "--mode",
                "writer",
                "--include-writer-mode",
                "--start",
                "2",
                "--end",
                "3",
                "--handoff-chapter",
                "4",
                "--draft-dir",
                "chapters",
            )
        )
        author_queue_md = run_cli(
            "author-review-queue",
            str(project),
            "--chapter",
            "3",
            "--platform",
            "codex",
            "--mode",
            "writer",
            "--include-writer-mode",
            "--start",
            "2",
            "--end",
            "3",
            "--handoff-chapter",
            "4",
            "--draft-dir",
            "chapters",
            "--format",
            "markdown",
        )
        queue_categories = {item.get("category") for item in author_queue.get("items", [])}
        assert_true(author_queue["type"] == "author_review_queue", "author-review-queue returned wrong type")
        assert_true(author_queue["read_only"] is True and author_queue["writes_project_state"] is False, "author-review-queue should be read-only")
        assert_true(author_queue["status"] in {"blocked", "needs_author_review"}, "author-review-queue should flag high-risk or writer-mode review")
        assert_true(author_queue["summary"]["blockers"] >= 1, "author-review-queue should report blocker count for high-risk or writer-mode review")
        assert_true("proposal" in queue_categories, "author-review-queue missed pending proposal item")
        assert_true("range_acceptance" in queue_categories, "author-review-queue missed range acceptance item")
        assert_true("writer_mode" in queue_categories, "author-review-queue missed writer mode item")
        assert_true(
            any(item.get("proposal_id") == high_risk["proposal"]["id"] for item in author_queue.get("items", [])),
            "author-review-queue missed high-risk proposal id",
        )
        assert_true("Long Novel Agent Author Review Queue" in author_queue_md.stdout, "author-review-queue markdown missing title")
        review_blocked_acceptance = load_stdout(
            run_cli(
                "chapter-range-acceptance-plan",
                str(project),
                "--start",
                "2",
                "--end",
                "3",
                "--handoff-chapter",
                "4",
                "--draft-dir",
                "chapters",
                "--author-confirmation",
                "Author accepted chapters 2-3 but high-risk proposal still needs review.",
            )
        )
        assert_true(review_blocked_acceptance["status"] in {"blocked", "needs_author_review"}, "acceptance plan should block or require author review for high-risk pending proposal")
        assert_true(
            any("High-risk pending proposal" in str(item) for item in review_blocked_acceptance.get("review_items", [])),
            "acceptance plan should surface high-risk pending proposal review item",
        )
        assert_true(not review_blocked_acceptance["ready_to_record_chapters"], "acceptance plan should block durable writes while author review is pending")
        record_steps_allowed = [
            step.get("allowed_now")
            for plan in review_blocked_acceptance.get("chapter_plans", [])
            for step in plan.get("steps", [])
            if step.get("id") in {"record_chapter", "propose_after_write", "apply_after_write"}
        ]
        assert_true(record_steps_allowed and not any(record_steps_allowed), "acceptance plan should mark writer steps unavailable during author review")
        applied = load_stdout(run_cli("apply-after-write", str(project), "--proposal-id", valid["proposal"]["id"]))
        assert_true(applied["applied"]["facts"] == 1, "apply did not write fact")
        assert_true(applied.get("snapshot", {}).get("id"), "apply did not create rollback snapshot")
        applied_readiness = load_stdout(run_cli("proposal-readiness", str(project), "--proposal-id", valid["proposal"]["id"]))
        assert_true(applied_readiness["status"] == "already_applied", "proposal-readiness should detect already applied proposal")
        context = load_stdout(run_cli("build-context", str(project), "--chapter", "3"))
        assert_true(any(row.get("subject") == "第 2 章" for row in context["facts"]), "chapter 3 context missed applied fact")

        rejected = load_stdout(run_cli("propose-after-write", str(project), "--chapter", "2", "--file", str(chapter), "--updates", json.dumps(valid_updates, ensure_ascii=False)))
        reject = load_stdout(run_cli("reject-proposal", str(project), "--proposal-id", rejected["proposal"]["id"], "--reason", "test rejection"))
        assert_true(reject["proposal"]["status"] == "rejected", "reject-proposal did not mark proposal rejected")
        rejected_readiness = load_stdout(run_cli("proposal-readiness", str(project), "--proposal-id", rejected["proposal"]["id"]))
        assert_true(rejected_readiness["status"] == "rejected", "proposal-readiness should detect rejected proposal")
        rejected_apply = run_cli("apply-after-write", str(project), "--proposal-id", rejected["proposal"]["id"], check=False)
        assert_true(rejected_apply.returncode != 0, "apply should reject rejected proposal")
        schema_bad = {"facts": {"subject": "bad"}, "handoff": 123}
        schema_validation = load_stdout(run_cli("validate-proposal", str(project), "--updates", json.dumps(schema_bad, ensure_ascii=False)))
        schema_types = {item["type"] for item in schema_validation["validation"]["issues"]}
        assert_true("schema_type" in schema_types, "proposal schema type errors should be reported")
        return {
            "valid_proposal": valid["proposal"]["id"],
            "diff_fact_creates": len(diff["diff"]["facts"]["create"]),
            "template_chapter": template["template"]["chapter"],
            "proposal_review_ready": review["ready_to_apply"],
            "proposal_readiness_status": readiness["status"],
            "missing_evidence_readiness_status": missing_readiness["status"],
            "high_risk_readiness_status": high_risk_readiness["status"],
            "author_review_queue_status": author_queue["status"],
            "author_review_queue_items": author_queue["summary"]["total"],
            "review_blocked_acceptance_status": review_blocked_acceptance["status"],
            "applied_readiness_status": applied_readiness["status"],
            "rejected_readiness_status": rejected_readiness["status"],
            "proposal_review_written_files": len(written_names),
            "apply_snapshot": applied["snapshot"]["id"],
            "schema_title": schema["schema"]["title"],
        }


def verify_continuity_audit() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-continuity-audit-") as tmp:
        project = Path(tmp) / "novel"
        chapters = project / "chapters"
        chapters.mkdir(parents=True)
        run_cli("init", str(project), "--title", "continuity-audit")
        chapter1 = chapters / "001.md"
        chapter3 = chapters / "003.md"
        chapter1.write_text("林追把铜钥匙收进袖中，记下北码头钟声慢了三息。", encoding="utf-8")
        chapter3.write_text("第三章里，林追仍握着铜钥匙，确认北码头钟声慢了三息。", encoding="utf-8")
        run_cli("record-chapter", str(project), "--chapter", "1", "--file", str(chapter1), "--handoff", "第二章要查北码头钟声。")
        run_cli("record-chapter", str(project), "--chapter", "3", "--file", str(chapter3), "--handoff", "第四章继续查钟声。")
        run_cli("add-source", str(project), "--summary", "无章节边界的资料摘要。", "--title", "Unbounded note")
        run_cli("add-fact", str(project), "--kind", "prop", "--subject", "铜钥匙", "--predicate", "holder", "--object", "林追")
        updates = {
            "facts": [
                {
                    "kind": "event",
                    "subject": "第 3 章",
                    "predicate": "result",
                    "object": "北码头钟声慢了三息",
                    "chapter_range": "3+",
                    "evidence": "确认北码头钟声慢了三息。",
                }
            ],
            "handoff": "第四章继续查钟声。",
        }
        proposal = load_stdout(run_cli("propose-after-write", str(project), "--chapter", "3", "--file", str(chapter3), "--updates", json.dumps(updates, ensure_ascii=False)))
        audit = load_stdout(run_cli("continuity-audit", str(project), "--chapter", "4", "--platform", "codex"))
        assert_true(audit["type"] == "continuity_audit", "continuity-audit returned wrong type")
        assert_true(audit["status"] == "needs_review", f"continuity-audit should require review, got {audit['status']}")
        assert_true(audit["summary"]["missing_chapters"] == 1, "continuity-audit missed chapter gap")
        assert_true(audit["summary"]["pending_proposals"] == 1, "continuity-audit missed pending proposal")
        assert_true(audit["summary"]["facts_without_evidence"] >= 1, "continuity-audit missed fact evidence warning")
        assert_true(audit["action_plan"]["summary"]["review"] >= 1, "continuity-audit missed review action plan")
        assert_true(audit["action_plan"]["summary"]["improve"] >= 1, "continuity-audit missed improve action plan")
        assert_true(any(action.get("source_check") == "pending_proposals_clear" for action in audit["action_plan"]["review"]), "continuity-audit missed pending proposal action")
        assert_true(any(action.get("source_check") == "fact_evidence_present" for action in audit["action_plan"]["improve"]), "continuity-audit missed fact evidence action")
        assert_true(any("proposal-review" in command for command in audit["recommended_commands"]), "continuity-audit missed proposal-review command")
        audit_md = run_cli("continuity-audit", str(project), "--chapter", "4", "--platform", "codex", "--format", "markdown")
        assert_true("Long Novel Agent Continuity Audit" in audit_md.stdout, "continuity-audit markdown missing title")
        assert_true("## Action Plan" in audit_md.stdout and "### Review" in audit_md.stdout, "continuity-audit markdown missing action plan")
        status = load_stdout(run_cli("project-status", str(project), "--chapter", "4", "--platform", "codex"))
        assert_true(any("continuity-audit" in command for command in status["recommended_commands"]), "project-status missed continuity-audit command")
        return {
            "status": audit["status"],
            "missing_chapters": audit["summary"]["missing_chapters"],
            "pending_proposals": audit["summary"]["pending_proposals"],
            "facts_without_evidence": audit["summary"]["facts_without_evidence"],
            "review_actions": audit["action_plan"]["summary"]["review"],
            "improve_actions": audit["action_plan"]["summary"]["improve"],
            "proposal_id": proposal["proposal"]["id"],
        }


def verify_init_wizard_handoff_and_install_doctor() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-wizard-") as tmp:
        project = Path(tmp) / "novel with space"
        dry = load_stdout(
            run_cli(
                "init-wizard",
                str(project),
                "--title",
                "wizard-test",
                "--target-chapters",
                "12",
                "--required",
                "账册缺页",
                "--forbidden",
                "铜钥匙交给白石商会",
                "--future-marker",
                "潮师真身",
                "--style-note",
                "对白短，动作明确。",
                "--source-summary",
                "旧稿摘要：林追持有铜钥匙。",
                "--source-path",
                "old-draft.md",
                "--chapter-range",
                "1-3",
                "--agent-id",
                "codex-local",
                "--platform",
                "codex",
                "--capabilities",
                "mcp,cli,file-read,file-write",
                "--dry-run",
            )
        )
        assert_true(dry["dry_run"], "init-wizard dry-run should not write")
        assert_true("update-rules" in dry["plan"]["operations"], "init-wizard plan missed rule update")
        template_md = run_cli("agent-template", "--platform", "codex", "--mode", "writer", "--format", "markdown")
        assert_true("build_chapter_context" in template_md.stdout, "agent-template markdown missed context gate")
        assert_true("record_chapter" in template_md.stdout, "agent-template markdown missed acceptance gate")
        actual = load_stdout(
            run_cli(
                "init-wizard",
                str(project),
                "--title",
                "wizard-test",
                "--target-chapters",
                "12",
                "--required",
                "账册缺页",
                "--forbidden",
                "铜钥匙交给白石商会",
                "--future-marker",
                "潮师真身",
                "--style-note",
                "对白短，动作明确。",
                "--source-summary",
                "旧稿摘要：林追持有铜钥匙。",
                "--source-path",
                "old-draft.md",
                "--chapter-range",
                "1-3",
                "--agent-id",
                "codex-local",
                "--platform",
                "codex",
                "--capabilities",
                "mcp,cli,file-read,file-write",
            )
        )
        assert_true(actual["ok"], "init-wizard did not complete")
        context = load_stdout(run_cli("build-context", str(project), "--chapter", "1"))
        assert_true("账册缺页" in context["rules"]["required_phrases"], "init-wizard required phrase missing from context")
        assert_true(context["sources"], "init-wizard source summary missing from context")
        chapters = project / "chapters"
        chapters.mkdir()
        chapter = chapters / "001.md"
        chapter.write_text("林追把铜钥匙收回袖中，发现账册缺页。", encoding="utf-8")
        run_cli("record-chapter", str(project), "--chapter", "1", "--file", str(chapter), "--handoff", "下一章追查账册缺页。")
        report = load_stdout(run_cli("handoff-report", str(project), "--chapter", "2"))
        assert_true(report["type"] == "handoff_report", "handoff-report returned wrong type")
        assert_true(report["target_chapter"] == 2, "handoff-report target chapter mismatch")
        assert_true("下一章追查账册缺页" in report["handoff"], "handoff-report missed latest chapter handoff")
        assert_true(report["recommended_commands"], "handoff-report missed recommended commands")
        brief = report.get("next_agent_brief")
        assert_true(isinstance(brief, dict), "handoff-report missed next agent brief")
        assert_true(brief.get("copy_prompt") and "chapter 2" in brief["copy_prompt"], "handoff-report brief missed copy prompt")
        assert_true("record_agent_activity" in {item.get("key") for item in brief.get("first_commands", []) if isinstance(item, dict)}, "handoff-report brief missed activity command")
        assert_true(".novel-agent/facts.jsonl" in brief.get("must_read", []), "handoff-report brief missed fact ledger")
        report_md = run_cli("handoff-report", str(project), "--chapter", "2", "--format", "markdown")
        assert_true("Long Novel Agent Handoff Report" in report_md.stdout, "handoff-report markdown missing title")
        assert_true("Next Agent Brief" in report_md.stdout, "handoff-report markdown missed next agent brief")
        assert_true("Write Boundaries" in report_md.stdout, "handoff-report markdown missed write boundaries")
        assert_true("proposal-template, propose-after-write" in report_md.stdout, "handoff-report markdown split workflow text incorrectly")
        session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "2", "--platform", "codex", "--mode", "writer"))
        assert_true(session["type"] == "chapter_session_packet", "prepare-session returned wrong type")
        assert_true(session["target_chapter"] == 2, "prepare-session target chapter mismatch")
        assert_true("chapter_context" in session, "prepare-session missed full chapter context")
        assert_true(session["project_identity"]["project_id"], "prepare-session missed project identity")
        assert_true(session["chapter_context_fingerprint"]["sha256"], "prepare-session missed chapter context fingerprint")
        assert_true(
            any("--expected-project-id" in command and "--expected-state-sha256" in command for command in session["recommended_commands"]),
            "prepare-session writer commands missed write session guard arguments",
        )
        assert_true(session["author_review_queue"]["type"] == "author_review_queue", "prepare-session missed author review queue")
        assert_true(
            any("author-review-queue" in command for command in session["recommended_commands"]),
            "prepare-session missed author review queue command",
        )
        assert_true("check_chapter" in session["tool_gates"]["before_ready"], "prepare-session missed before-ready gate")
        assert_true("record_chapter" in session["tool_gates"]["after_author_acceptance"], "prepare-session missed writer acceptance gate")
        session_summary = load_stdout(run_cli("prepare-session", str(project), "--chapter", "2", "--summary-only"))
        assert_true("chapter_context_summary" in session_summary and "chapter_context" not in session_summary, "prepare-session summary mode should omit full context")
        assert_true("author_review_queue" in session_summary, "prepare-session summary mode should keep author review queue")
        session_md = run_cli("prepare-session", str(project), "--chapter", "2", "--format", "markdown")
        assert_true("Long Novel Agent Chapter Session" in session_md.stdout, "prepare-session markdown missing title")
        assert_true("Before Ready" in session_md.stdout, "prepare-session markdown missed tool gates")
        assert_true("Author Review Queue" in session_md.stdout, "prepare-session markdown missed author review queue")
        config = Path(tmp) / "mcp.json"
        target = Path(tmp) / "skills"
        subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--target",
                str(target),
                "--mcp-config",
                str(config),
                "--force",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        doctor = load_stdout(run_cli("doctor", str(project), "--install-config", str(config), "--start-mcp-test"))
        check_names = {item["name"]: item for item in doctor["checks"]}
        assert_true(check_names["install_config_server:long-novel-agent"]["ok"], "doctor missed installed MCP server entry")
        assert_true(check_names["install_config_installed_kit"]["ok"], "doctor missed installed kit version check")
        installed_detail = check_names["install_config_installed_kit"]["detail"]
        assert_true(installed_detail["installed_version"] == current_kit_version(), "doctor installed kit version mismatch")
        assert_true(installed_detail["expected_version"] == current_kit_version(), "doctor expected kit version mismatch")
        assert_true(installed_detail["skill_content_matches_source"], "doctor installed skill content mismatch")
        assert_true(bool(installed_detail["installed_skill_sha256"]), "doctor missed installed skill sha256")
        assert_true(bool(installed_detail["expected_skill_sha256"]), "doctor missed source skill sha256")
        assert_true(check_names["install_config_mcp_start"]["ok"], "doctor failed to start installed MCP server")
        mcp_start_detail = check_names["install_config_mcp_start"]["detail"]
        assert_true(mcp_start_detail["prepare_chapter_session"]["ok"], "doctor MCP smoke missed prepare_chapter_session call")
        assert_true(mcp_start_detail["prepare_chapter_session"]["target_chapter"] == 2, "doctor MCP smoke target chapter mismatch")
        coverage = check_names.get("continuity_coverage", {}).get("detail", {})
        assert_true(coverage.get("target_chapter") == 2, "doctor continuity coverage target chapter mismatch")
        assert_true(coverage.get("chapters", {}).get("recorded") == 1, "doctor continuity coverage missed recorded chapter")
        assert_true(coverage.get("recommended_commands"), "doctor continuity coverage missed recommended commands")
        return {
            "rules": len(context["rules"]["required_phrases"]),
            "sources": len(context["sources"]),
            "handoff_target": report["target_chapter"],
            "doctor_checks": len(doctor["checks"]),
        }


def verify_quickstart() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-quickstart-") as tmp:
        project = Path(tmp) / "novel with space"
        dry = load_stdout(
            run_cli(
                "quickstart",
                str(project),
                "--title",
                "quickstart-dry",
                "--platform",
                "codex",
                "--required",
                "账册缺页",
                "--dry-run",
            )
        )
        assert_true(dry["type"] == "quickstart_report", "quickstart dry-run returned wrong type")
        assert_true(dry["dry_run"], "quickstart dry-run flag missing")
        assert_true(not (project / ".novel-agent").exists(), "quickstart dry-run should not write state")
        assert_true(dry["selected_source"] == "init", "quickstart dry-run auto source should choose init for new project")
        quickstart_command = dry["recommended_commands"][0]
        assert_true(
            shlex.split(quickstart_command)[3] == str(project.resolve()),
            "quickstart recommended command should preserve project paths with spaces",
        )

        actual = load_stdout(
            run_cli(
                "quickstart",
                str(project),
                "--title",
                "quickstart-test",
                "--target-chapters",
                "8",
                "--platform",
                "codex",
                "--required",
                "账册缺页",
                "--source-summary",
                "旧稿摘要：林追保管铜钥匙。",
                "--source-path",
                "old-draft.md",
                "--summary-only",
            )
        )
        assert_true(actual["ok"], "quickstart init flow failed")
        assert_true(actual["selected_source"] == "init", "quickstart init flow selected wrong source")
        assert_true(actual["chapter_session"]["type"] == "chapter_session_packet", "quickstart missed chapter session")
        assert_true("chapter_context_summary" in actual["chapter_session"], "quickstart summary-only should omit full chapter context")
        context = load_stdout(run_cli("build-context", str(project), "--chapter", "1"))
        assert_true("账册缺页" in context["rules"]["required_phrases"], "quickstart rule missing from context")
        assert_true(context["sources"], "quickstart source summary missing from context")
        audit = load_stdout(run_cli("list-audit", str(project), "--limit", "20"))
        assert_true(any(row.get("operation") == "quickstart" for row in audit["audit"]), "quickstart audit row missing")

        markdown_project = Path(tmp) / "markdown-novel"
        markdown = run_cli("quickstart", str(markdown_project), "--title", "markdown-test", "--dry-run", "--format", "markdown")
        assert_true("Long Novel Agent Quickstart" in markdown.stdout, "quickstart markdown missing title")
        assert_true("Planned Operations" in markdown.stdout, "quickstart markdown missing planned operations")

        gaoxia = Path(tmp) / "mini-gaoxia-project"
        shutil.copytree(EXAMPLE, gaoxia)
        imported = load_stdout(run_cli("quickstart", str(gaoxia), "--source", "auto", "--platform", "codex", "--summary-only"))
        assert_true(imported["selected_source"] == "import-gaoxia", "quickstart should auto-import Gaoxia project")
        assert_true(imported["bootstrap"]["bootstrap"]["counts"]["chapters"] >= 1, "quickstart Gaoxia import missed chapters")
        assert_true(imported["target_chapter"] == 2, "quickstart Gaoxia import should prepare next chapter")
        return {
            "new_project_target": actual["target_chapter"],
            "gaoxia_target": imported["target_chapter"],
            "audit_count": len(audit["audit"]),
        }


def verify_desktop_setup_guide() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-desktop-setup-") as tmp:
        project = Path(tmp) / "novel with space"
        markdown = run_cli("desktop-setup", str(project), "--platform", "codex", "--mode", "read-only", "--format", "markdown")
        assert_true("Long Novel Agent Desktop Setup" in markdown.stdout, "desktop setup markdown missing title")
        assert_true("--mcp-config auto" in markdown.stdout, "desktop setup should suggest Codex auto config")
        assert_true("--read-only" in markdown.stdout, "desktop setup should show read-only server mode")
        assert_true("quickstart" in markdown.stdout and "doctor" in markdown.stdout, "desktop setup missed project startup commands")
        assert_true("--upgrade" in markdown.stdout and "--uninstall" in markdown.stdout, "desktop setup missed maintenance commands")
        assert_true("desktop-checklist" in markdown.stdout, "desktop setup missed desktop client checklist command")
        core_setup = load_stdout(
            run_cli(
                "desktop-setup",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--tool-profile",
                "core",
                "--format",
                "json",
            )
        )
        assert_true(core_setup["tool_profile"] == "core", "desktop setup missed requested tool profile")
        assert_true("--tool-profile core" in core_setup["commands"]["print_mcp_config"], "desktop setup print command missed core tool profile")
        assert_true("--tool-profile core" in core_setup["commands"]["merge_mcp_config"], "desktop setup merge command missed core tool profile")
        assert_true("--tool-profile core" in core_setup["commands"]["server_command"], "desktop setup server command missed core tool profile")
        assert_true("--tool-profile core" in core_setup["commands"]["desktop_checklist"], "desktop setup checklist command missed core tool profile")

        config = Path(tmp) / "cursor mcp.json"
        writer = load_stdout(
            run_cli(
                "desktop-setup",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "writer",
                "--install-config",
                str(config),
                "--format",
                "json",
            )
        )
        assert_true(writer["type"] == "desktop_setup_guide", "desktop setup returned wrong type")
        assert_true(writer["mcp_mode"] == "writer", "desktop setup writer mode not reflected")
        assert_true("--writer-mcp" in writer["commands"]["merge_mcp_config"], "desktop setup writer install command missed --writer-mcp")
        assert_true("--upgrade" in writer["commands"]["upgrade_install"], "desktop setup writer upgrade command missed --upgrade")
        assert_true("--uninstall" in writer["commands"]["uninstall"], "desktop setup writer uninstall command missed --uninstall")
        assert_true(str(config) in writer["commands"]["doctor"], "desktop setup doctor missed explicit config path")
        assert_true(shlex.split(writer["commands"]["quickstart"])[3] == str(project.resolve()), "desktop setup quickstart should preserve project paths with spaces")
        home_config = load_stdout(run_cli("desktop-setup", str(project), "--platform", "cursor", "--install-config", "~/.cursor/mcp.json", "--format", "json"))
        assert_true(home_config["config_path"].startswith(str(Path.home())), "desktop setup should expand home-relative config paths")
        assert_true("~" not in home_config["commands"]["doctor"], "desktop setup doctor command should not keep raw ~ paths")

        generic = load_stdout(run_cli("desktop-setup", str(project), "--platform", "generic", "--mode", "read-only", "--format", "json"))
        assert_true(not generic["commands"]["merge_mcp_config"], "generic setup should not invent an MCP config path")
        assert_true(generic["warnings"], "generic setup should warn about explicit config path")
        return {
            "codex_markdown": True,
            "core_tool_profile": core_setup["tool_profile"],
            "writer_mode": writer["mcp_mode"],
            "generic_warnings": len(generic["warnings"]),
        }


def verify_desktop_onboarding_guide() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-desktop-onboard-") as tmp:
        project = Path(tmp) / "novel with space"
        new_project = load_stdout(
            run_cli(
                "desktop-onboard",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "3",
                "--format",
                "json",
            )
        )
        assert_true(new_project["type"] == "desktop_onboarding_guide", "desktop-onboard returned wrong type")
        assert_true(new_project["target_chapter"] == 3, "desktop-onboard missed explicit target chapter")
        step_ids = [item["id"] for item in new_project["steps"]]
        for step_id in ("install_local_mcp", "initialize_project", "verify_local_setup", "verify_real_client", "open_chapter_session"):
            assert_true(step_id in step_ids, f"desktop-onboard missed {step_id}")
        assert_true(not new_project["state_exists"], "desktop-onboard should detect missing state")
        assert_true("quickstart" in new_project["commands"]["quickstart_dry_run"], "desktop-onboard missed quickstart dry-run command")
        assert_true("--chapter 3" in new_project["commands"]["desktop_checklist"], "desktop-onboard checklist should include target chapter")
        markdown = run_cli("desktop-onboard", str(project), "--platform", "codex", "--mode", "read-only", "--chapter", "3", "--format", "markdown")
        assert_true("Long Novel Agent Desktop Onboarding" in markdown.stdout, "desktop-onboard markdown missing title")
        assert_true("Command Shortcuts" in markdown.stdout, "desktop-onboard markdown missing command shortcuts")
        guide_path = project / "guide.md"
        written = load_stdout(
            run_cli(
                "desktop-onboard",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "3",
                "--write-guide",
                str(guide_path),
                "--format",
                "json",
            )
        )
        assert_true(guide_path.exists(), "desktop-onboard did not write guide")
        assert_true(written["guide_written_path"] == str(guide_path), "desktop-onboard guide path mismatch")
        assert_true("Long Novel Agent Desktop Onboarding" in guide_path.read_text(encoding="utf-8"), "desktop-onboard wrote unexpected guide content")

        run_cli("init", str(project), "--title", "Onboarded")
        initialized = load_stdout(run_cli("desktop-onboard", str(project), "--platform", "codex", "--mode", "read-only", "--chapter", "3", "--format", "json"))
        by_id = {item["id"]: item for item in initialized["steps"]}
        assert_true(initialized["state_exists"], "desktop-onboard should detect initialized state")
        assert_true(by_id["initialize_project"]["status"] == "done", "desktop-onboard should mark project init done")
        assert_true(by_id["open_chapter_session"]["status"] == "ready", "desktop-onboard should mark chapter session ready")
        return {
            "step_count": len(new_project["steps"]),
            "target_chapter": new_project["target_chapter"],
            "guide_written": guide_path.exists(),
            "initialized_state": initialized["state_exists"],
        }


def verify_desktop_bootstrap() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-desktop-bootstrap-") as tmp:
        project = Path(tmp) / "novel with space"
        config = Path(tmp) / "codex.toml"
        target = Path(tmp) / "skills"
        dry = load_stdout(
            run_cli(
                "desktop-bootstrap",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--install-config",
                str(config),
                "--install-target",
                str(target),
                "--chapter",
                "2",
                "--format",
                "json",
            )
        )
        assert_true(dry["type"] == "desktop_bootstrap_report", "desktop-bootstrap returned wrong type")
        assert_true(not dry["applied"], "desktop-bootstrap without --apply should not be marked applied")
        assert_true(dry["ok"], "desktop-bootstrap dry plan should be runnable with explicit config")
        assert_true(not config.exists(), "desktop-bootstrap dry plan should not write MCP config")
        assert_true(not (project / ".novel-agent").exists(), "desktop-bootstrap dry plan should not write project state")
        assert_true("--target" in dry["steps"][0]["command"], "desktop-bootstrap install command missed custom target")
        assert_true("quickstart" in dry["steps"][1]["command"], "desktop-bootstrap missed quickstart step")
        markdown = run_cli(
            "desktop-bootstrap",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--install-config",
            str(config),
            "--install-target",
            str(target),
            "--chapter",
            "2",
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Desktop Bootstrap" in markdown.stdout, "desktop-bootstrap markdown missing title")
        assert_true("Applied: False" in markdown.stdout, "desktop-bootstrap markdown should show dry plan")
        generic = load_stdout(run_cli("desktop-bootstrap", str(Path(tmp) / "generic"), "--platform", "generic", "--format", "json"))
        assert_true(not generic["ok"], "desktop-bootstrap generic plan should require explicit config before apply")
        applied = load_stdout(
            run_cli(
                "desktop-bootstrap",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--install-config",
                str(config),
                "--install-target",
                str(target),
                "--chapter",
                "2",
                "--apply",
                "--write-guide",
                "--format",
                "json",
            )
        )
        statuses = {item["id"]: item["status"] for item in applied["steps"]}
        assert_true(applied["ok"], "desktop-bootstrap apply should complete")
        assert_true(all(status == "done" for status in statuses.values()), "desktop-bootstrap apply should complete every step")
        assert_true(config.exists(), "desktop-bootstrap apply should write MCP config")
        assert_true((target / "long-novel-agent" / "server.py").exists(), "desktop-bootstrap apply should install skill files")
        assert_true((project / ".novel-agent" / "manifest.json").exists(), "desktop-bootstrap apply should initialize project state")
        assert_true((project / "long-novel-agent-onboarding.md").exists(), "desktop-bootstrap apply should write onboarding guide")
        return {
            "step_count": len(applied["steps"]),
            "target_chapter": applied["target_chapter"],
            "state_exists": (project / ".novel-agent").exists(),
            "guide_written": (project / "long-novel-agent-onboarding.md").exists(),
        }


def verify_desktop_pack() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-desktop-pack-") as tmp:
        project = Path(tmp) / "novel with space"
        config = Path(tmp) / "codex.toml"
        target = Path(tmp) / "skills"
        output_dir = Path(tmp) / "pack out"
        run_cli("init", str(project), "--title", "Packed Novel")
        pack = load_stdout(
            run_cli(
                "desktop-pack",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--install-config",
                str(config),
                "--install-target",
                str(target),
                "--chapter",
                "2",
                "--output-dir",
                str(output_dir),
                "--format",
                "json",
            )
        )
        assert_true(pack["type"] == "desktop_pack", "desktop-pack returned wrong type")
        assert_true(pack["target_chapter"] == 2, "desktop-pack missed target chapter")
        assert_true(pack["output_dir"] == str(output_dir.resolve()), "desktop-pack output dir mismatch")
        expected = {
            "index.html",
            "pack-index.json",
            "pack-index.schema.json",
            "START_HERE.md",
            "first-three.md",
            "first-three.json",
            "first-three.schema.json",
            "first-three.html",
            "local-summary.md",
            "local-summary.json",
            "local-summary.html",
            "local-summary.schema.json",
            "user-steps.md",
            "user-steps.json",
            "user-steps.schema.json",
            "user-steps.html",
            "starter-brief.md",
            "starter-brief.json",
            "starter-brief.schema.json",
            "next-step.md",
            "desktop-next-step.md",
            "desktop-next-step.json",
            "desktop-next-step.schema.json",
            "desktop-diagnostics.md",
            "desktop-diagnostics.json",
            "desktop-diagnostics.schema.json",
            "desktop-pack-readiness.md",
            "desktop-pack-readiness.json",
            "desktop-pack-readiness.schema.json",
            "agent-startup-prompt.md",
            "agent-startup-prompt.json",
            "agent-startup-prompt.schema.json",
            "agent-takeover.md",
            "agent-takeover.json",
            "agent-takeover.schema.json",
            "author-actions.md",
            "author-actions.json",
            "author-actions.schema.json",
            "acceptance-review.md",
            "acceptance-review.json",
            "acceptance-review.html",
            "acceptance-review.schema.json",
            "quick-install.md",
            "open-local.md",
            "open-local.sh",
            "open-local.ps1",
            "open-local.cmd",
            "open-local.command",
            "install-local.md",
            "install-local.sh",
            "install-local.ps1",
            "install-local.cmd",
            "install-local.command",
            "upgrade-local.md",
            "upgrade-local.sh",
            "upgrade-local.ps1",
            "upgrade-local.cmd",
            "upgrade-local.command",
            "uninstall-local.md",
            "uninstall-local.sh",
            "uninstall-local.ps1",
            "uninstall-local.cmd",
            "uninstall-local.command",
            "local-checks.md",
            "local-checks.sh",
            "local-checks.ps1",
            "local-checks.cmd",
            "local-checks.command",
            "archive-local.md",
            "archive-local.sh",
            "archive-local.ps1",
            "archive-local.cmd",
            "archive-local.command",
            "writer-mode-approval.md",
            "writer-mode-approval.json",
            "writer-mode-approval.schema.json",
            "client-compatibility.md",
            "client-compatibility.json",
            "pack-manifest.json",
            "verify-pack.py",
            "pack-schema-check.md",
            "pack-schema-check.json",
            "pack-doctor.md",
            "pack-doctor.json",
            "troubleshooting.md",
            "troubleshooting.json",
            "troubleshooting.html",
            "troubleshooting.schema.json",
            "README.md",
            "agent-start.md",
            "agent-start-codex.md",
            "agent-start-cursor.md",
            "agent-start-claude-desktop.md",
            "agent-start-generic.md",
            "project-wizard.md",
            "project-wizard.json",
            "project-wizard.schema.json",
            "source-intake.md",
            "source-intake.json",
            "source-intake.schema.json",
            "source-intake-review.html",
            "platforms/README.md",
            "platforms/codex.md",
            "platforms/cursor.md",
            "platforms/claude-desktop.md",
            "platforms/generic-json.md",
            "mcp-configs/README.md",
            "mcp-configs/codex-read-only.toml",
            "mcp-configs/codex-writer.toml",
            "mcp-configs/cursor-read-only.json",
            "mcp-configs/cursor-writer.json",
            "mcp-configs/claude-desktop-read-only.json",
            "mcp-configs/claude-desktop-writer.json",
            "mcp-configs/generic-json-read-only.json",
            "mcp-configs/generic-json-writer.json",
            "onboarding.md",
            "setup.md",
            "bootstrap-plan.md",
            "checklist.md",
            "project-status.md",
            "project-status.json",
            "continuity-audit.md",
            "continuity-audit.json",
            "author-review-queue.md",
            "author-review-queue.json",
            "handoff-report.json",
            "handoff-report.schema.json",
            "handoff-brief.md",
            "handoff-brief.json",
            "chapter-session.md",
            "chapter-session.json",
            "chapter-session.schema.json",
            "desktop-matrix.md",
            "desktop-matrix.json",
            "commands.txt",
            "commands-index.md",
            "commands-index.json",
            "commands-index.schema.json",
            "commands.json",
            "commands.schema.json",
            "desktop-evidence-session.md",
            "desktop-evidence-session.json",
            "desktop-evidence-session.schema.json",
            "desktop-evidence-runbook.md",
            "desktop-evidence-runbook.json",
            "desktop-evidence-runbook.schema.json",
            "desktop-evidence-review.md",
            "desktop-evidence-review.json",
            "desktop-evidence-review.html",
            "desktop-evidence-review.schema.json",
            "desktop-evidence-gaps.md",
            "desktop-evidence-gaps.json",
            "real-client-walkthrough.md",
            "client-prompts.md",
            "result-json-guide.md",
            "desktop-results.schema.json",
            "evidence-to-results.md",
            "desktop-evidence.txt",
            pack["result_file"],
            "metadata.json",
        }
        written = {item["name"] for item in pack["written_files"]}
        assert_true(expected <= written, "desktop-pack missed expected files")
        for name in expected:
            assert_true((output_dir / name).exists(), f"desktop-pack did not write {name}")
        readme = (output_dir / "README.md").read_text(encoding="utf-8")
        start_here = (output_dir / "START_HERE.md").read_text(encoding="utf-8")
        first_three_md = (output_dir / "first-three.md").read_text(encoding="utf-8")
        first_three_json = json.loads((output_dir / "first-three.json").read_text(encoding="utf-8"))
        first_three_schema = json.loads((output_dir / "first-three.schema.json").read_text(encoding="utf-8"))
        first_three_html = (output_dir / "first-three.html").read_text(encoding="utf-8")
        local_summary_md = (output_dir / "local-summary.md").read_text(encoding="utf-8")
        local_summary_json = json.loads((output_dir / "local-summary.json").read_text(encoding="utf-8"))
        local_summary_html = (output_dir / "local-summary.html").read_text(encoding="utf-8")
        local_summary_schema = json.loads((output_dir / "local-summary.schema.json").read_text(encoding="utf-8"))
        user_steps_md = (output_dir / "user-steps.md").read_text(encoding="utf-8")
        user_steps_json = json.loads((output_dir / "user-steps.json").read_text(encoding="utf-8"))
        user_steps_schema = json.loads((output_dir / "user-steps.schema.json").read_text(encoding="utf-8"))
        user_steps_html = (output_dir / "user-steps.html").read_text(encoding="utf-8")
        next_step = (output_dir / "next-step.md").read_text(encoding="utf-8")
        desktop_next_step_md = (output_dir / "desktop-next-step.md").read_text(encoding="utf-8")
        desktop_next_step_json = json.loads((output_dir / "desktop-next-step.json").read_text(encoding="utf-8"))
        desktop_next_step_schema = json.loads((output_dir / "desktop-next-step.schema.json").read_text(encoding="utf-8"))
        desktop_diagnostics_md = (output_dir / "desktop-diagnostics.md").read_text(encoding="utf-8")
        desktop_diagnostics_json = json.loads((output_dir / "desktop-diagnostics.json").read_text(encoding="utf-8"))
        desktop_diagnostics_schema = json.loads((output_dir / "desktop-diagnostics.schema.json").read_text(encoding="utf-8"))
        quick_install = (output_dir / "quick-install.md").read_text(encoding="utf-8")
        open_local = (output_dir / "open-local.md").read_text(encoding="utf-8")
        open_local_script = (output_dir / "open-local.sh").read_text(encoding="utf-8")
        open_local_ps1 = (output_dir / "open-local.ps1").read_text(encoding="utf-8")
        open_local_cmd = (output_dir / "open-local.cmd").read_text(encoding="utf-8")
        open_local_command = (output_dir / "open-local.command").read_text(encoding="utf-8")
        install_local = (output_dir / "install-local.md").read_text(encoding="utf-8")
        install_local_script = (output_dir / "install-local.sh").read_text(encoding="utf-8")
        install_local_ps1 = (output_dir / "install-local.ps1").read_text(encoding="utf-8")
        install_local_cmd = (output_dir / "install-local.cmd").read_text(encoding="utf-8")
        install_local_command = (output_dir / "install-local.command").read_text(encoding="utf-8")
        upgrade_local = (output_dir / "upgrade-local.md").read_text(encoding="utf-8")
        upgrade_local_script = (output_dir / "upgrade-local.sh").read_text(encoding="utf-8")
        upgrade_local_ps1 = (output_dir / "upgrade-local.ps1").read_text(encoding="utf-8")
        upgrade_local_cmd = (output_dir / "upgrade-local.cmd").read_text(encoding="utf-8")
        upgrade_local_command = (output_dir / "upgrade-local.command").read_text(encoding="utf-8")
        uninstall_local = (output_dir / "uninstall-local.md").read_text(encoding="utf-8")
        uninstall_local_script = (output_dir / "uninstall-local.sh").read_text(encoding="utf-8")
        uninstall_local_ps1 = (output_dir / "uninstall-local.ps1").read_text(encoding="utf-8")
        uninstall_local_cmd = (output_dir / "uninstall-local.cmd").read_text(encoding="utf-8")
        uninstall_local_command = (output_dir / "uninstall-local.command").read_text(encoding="utf-8")
        local_checks = (output_dir / "local-checks.md").read_text(encoding="utf-8")
        local_checks_script = (output_dir / "local-checks.sh").read_text(encoding="utf-8")
        local_checks_ps1 = (output_dir / "local-checks.ps1").read_text(encoding="utf-8")
        local_checks_cmd = (output_dir / "local-checks.cmd").read_text(encoding="utf-8")
        local_checks_command = (output_dir / "local-checks.command").read_text(encoding="utf-8")
        archive_local = (output_dir / "archive-local.md").read_text(encoding="utf-8")
        archive_local_script = (output_dir / "archive-local.sh").read_text(encoding="utf-8")
        archive_local_ps1 = (output_dir / "archive-local.ps1").read_text(encoding="utf-8")
        archive_local_cmd = (output_dir / "archive-local.cmd").read_text(encoding="utf-8")
        archive_local_command = (output_dir / "archive-local.command").read_text(encoding="utf-8")
        powershell_wrappers = {
            "install-local.ps1": install_local_ps1,
            "upgrade-local.ps1": upgrade_local_ps1,
            "uninstall-local.ps1": uninstall_local_ps1,
            "local-checks.ps1": local_checks_ps1,
            "archive-local.ps1": archive_local_ps1,
        }
        powershell_preview_wrappers = {
            "install-local.ps1": install_local_ps1,
            "upgrade-local.ps1": upgrade_local_ps1,
            "uninstall-local.ps1": uninstall_local_ps1,
            "archive-local.ps1": archive_local_ps1,
        }
        windows_cmd_wrappers = {
            "install-local.cmd": install_local_cmd,
            "upgrade-local.cmd": upgrade_local_cmd,
            "uninstall-local.cmd": uninstall_local_cmd,
            "local-checks.cmd": local_checks_cmd,
            "archive-local.cmd": archive_local_cmd,
        }
        windows_preview_wrappers = {
            "install-local.cmd": install_local_cmd,
            "upgrade-local.cmd": upgrade_local_cmd,
            "uninstall-local.cmd": uninstall_local_cmd,
            "archive-local.cmd": archive_local_cmd,
        }
        for name, content in windows_cmd_wrappers.items():
            assert_true("where python >nul 2>nul" in content, f"{name} should detect python")
            assert_true("where py >nul 2>nul" in content, f"{name} should detect Windows py launcher")
            assert_true('set "PYTHON_BIN=py -3"' in content, f"{name} should fall back to py -3")
            assert_true("Cannot find Python 3" in content, f"{name} should guard missing Python before executing")
            assert_true("PYTHON_BIN=python or PYTHON_BIN=py -3" in content, f"{name} should document PYTHON_BIN override")
        for name, content in windows_preview_wrappers.items():
            assert_true("Python command: %PYTHON_BIN%" in content, f"{name} preview should show selected Python command")
            assert_true("Python command: not found" in content, f"{name} preview should warn when Python is missing")
        for name, content in powershell_wrappers.items():
            assert_true("$env:PYTHON_BIN" in content, f"{name} should honor PYTHON_BIN")
            assert_true("Get-Command python" in content, f"{name} should detect python")
            assert_true("Get-Command py" in content, f"{name} should detect Windows py launcher")
            assert_true("Cannot find Python 3" in content, f"{name} should guard missing Python before executing")
            assert_true("Invoke-LongNovelPython" in content, f"{name} should use the shared PowerShell Python invoker")
        for name, content in powershell_preview_wrappers.items():
            assert_true("Default mode is preview only" in content, f"{name} preview should be default")
            assert_true("PowerShell command:" in content, f"{name} preview should show command")
            assert_true("Python command:" in content, f"{name} preview should show selected Python command")
        writer_approval = (output_dir / "writer-mode-approval.md").read_text(encoding="utf-8")
        writer_mode_approval_json = json.loads((output_dir / "writer-mode-approval.json").read_text(encoding="utf-8"))
        writer_mode_approval_schema = json.loads((output_dir / "writer-mode-approval.schema.json").read_text(encoding="utf-8"))
        acceptance_review_md = (output_dir / "acceptance-review.md").read_text(encoding="utf-8")
        acceptance_review_json = json.loads((output_dir / "acceptance-review.json").read_text(encoding="utf-8"))
        acceptance_review_html = (output_dir / "acceptance-review.html").read_text(encoding="utf-8")
        acceptance_review_schema = json.loads((output_dir / "acceptance-review.schema.json").read_text(encoding="utf-8"))
        client_compatibility = (output_dir / "client-compatibility.md").read_text(encoding="utf-8")
        client_compatibility_json = json.loads((output_dir / "client-compatibility.json").read_text(encoding="utf-8"))
        troubleshooting = (output_dir / "troubleshooting.md").read_text(encoding="utf-8")
        troubleshooting_json = json.loads((output_dir / "troubleshooting.json").read_text(encoding="utf-8"))
        troubleshooting_html = (output_dir / "troubleshooting.html").read_text(encoding="utf-8")
        troubleshooting_schema = json.loads((output_dir / "troubleshooting.schema.json").read_text(encoding="utf-8"))
        index_html = (output_dir / "index.html").read_text(encoding="utf-8")
        pack_index_json = json.loads((output_dir / "pack-index.json").read_text(encoding="utf-8"))
        pack_index_schema = json.loads((output_dir / "pack-index.schema.json").read_text(encoding="utf-8"))
        agent_startup_prompt = (output_dir / "agent-startup-prompt.md").read_text(encoding="utf-8")
        agent_startup_prompt_json = json.loads((output_dir / "agent-startup-prompt.json").read_text(encoding="utf-8"))
        agent_startup_prompt_schema = json.loads((output_dir / "agent-startup-prompt.schema.json").read_text(encoding="utf-8"))
        agent_start = (output_dir / "agent-start.md").read_text(encoding="utf-8")
        agent_start_codex = (output_dir / "agent-start-codex.md").read_text(encoding="utf-8")
        agent_start_cursor = (output_dir / "agent-start-cursor.md").read_text(encoding="utf-8")
        agent_start_claude = (output_dir / "agent-start-claude-desktop.md").read_text(encoding="utf-8")
        agent_start_generic = (output_dir / "agent-start-generic.md").read_text(encoding="utf-8")
        project_wizard = (output_dir / "project-wizard.md").read_text(encoding="utf-8")
        project_wizard_json = json.loads((output_dir / "project-wizard.json").read_text(encoding="utf-8"))
        project_wizard_schema = json.loads((output_dir / "project-wizard.schema.json").read_text(encoding="utf-8"))
        source_intake_md = (output_dir / "source-intake.md").read_text(encoding="utf-8")
        source_intake_review = (output_dir / "source-intake-review.html").read_text(encoding="utf-8")
        source_intake_json_path = output_dir / "source-intake.json"
        source_intake_json = json.loads(source_intake_json_path.read_text(encoding="utf-8"))
        source_intake_schema = json.loads((output_dir / "source-intake.schema.json").read_text(encoding="utf-8"))
        platform_index = (output_dir / "platforms" / "README.md").read_text(encoding="utf-8")
        codex_guide = (output_dir / "platforms" / "codex.md").read_text(encoding="utf-8")
        cursor_guide = (output_dir / "platforms" / "cursor.md").read_text(encoding="utf-8")
        claude_guide = (output_dir / "platforms" / "claude-desktop.md").read_text(encoding="utf-8")
        generic_guide = (output_dir / "platforms" / "generic-json.md").read_text(encoding="utf-8")
        config_readme = (output_dir / "mcp-configs" / "README.md").read_text(encoding="utf-8")
        codex_config = (output_dir / "mcp-configs" / "codex-read-only.toml").read_text(encoding="utf-8")
        cursor_config = json.loads((output_dir / "mcp-configs" / "cursor-read-only.json").read_text(encoding="utf-8"))
        claude_writer_config = json.loads((output_dir / "mcp-configs" / "claude-desktop-writer.json").read_text(encoding="utf-8"))
        commands = (output_dir / "commands.txt").read_text(encoding="utf-8")
        commands_index_md = (output_dir / "commands-index.md").read_text(encoding="utf-8")
        commands_index_json = json.loads((output_dir / "commands-index.json").read_text(encoding="utf-8"))
        commands_index_schema = json.loads((output_dir / "commands-index.schema.json").read_text(encoding="utf-8"))
        commands_json = json.loads((output_dir / "commands.json").read_text(encoding="utf-8"))
        commands_schema = json.loads((output_dir / "commands.schema.json").read_text(encoding="utf-8"))
        pack_manifest = json.loads((output_dir / "pack-manifest.json").read_text(encoding="utf-8"))
        verify_pack_script = (output_dir / "verify-pack.py").read_text(encoding="utf-8")
        pack_schema_check_md = (output_dir / "pack-schema-check.md").read_text(encoding="utf-8")
        pack_schema_check_json = json.loads((output_dir / "pack-schema-check.json").read_text(encoding="utf-8"))
        pack_doctor_md = (output_dir / "pack-doctor.md").read_text(encoding="utf-8")
        pack_doctor_json = json.loads((output_dir / "pack-doctor.json").read_text(encoding="utf-8"))
        verify_pack_run = subprocess.run(
            [sys.executable, str(output_dir / "verify-pack.py"), "--json"],
            cwd=output_dir,
            text=True,
            capture_output=True,
            check=True,
        )
        verify_pack_result = json.loads(verify_pack_run.stdout)
        verify_pack_cli = load_stdout(run_cli("verify-pack", str(output_dir), "--format", "json"))
        schema_check_file_before = (output_dir / "pack-schema-check.json").read_text(encoding="utf-8")
        pack_schema_check_cli = load_stdout(run_cli("pack-schema-check", str(output_dir), "--format", "json"))
        schema_check_file_after = (output_dir / "pack-schema-check.json").read_text(encoding="utf-8")
        pack_schema_check_markdown = run_cli("pack-schema-check", str(output_dir), "--format", "markdown")
        pack_freshness_cli = load_stdout(run_cli("pack-freshness", str(output_dir), "--format", "json"))
        pack_freshness_markdown = run_cli("pack-freshness", str(output_dir), "--format", "markdown")
        chapter_session_freshness_cli = load_stdout(run_cli("chapter-session-freshness", str(output_dir), "--format", "json"))
        chapter_session_freshness_markdown = run_cli("chapter-session-freshness", str(output_dir), "--format", "markdown")
        pack_doctor_report = load_stdout(run_cli("pack-doctor", str(output_dir), "--format", "json"))
        refreshed_pack_doctor_path = Path(tmp) / "pack-doctor.refreshed.json"
        refreshed_pack_doctor_report = load_stdout(run_cli("pack-doctor", str(output_dir), "--format", "json", "--output", str(refreshed_pack_doctor_path)))
        refreshed_pack_doctor_json = json.loads(refreshed_pack_doctor_path.read_text(encoding="utf-8"))
        local_summary_file_before = (output_dir / "local-summary.json").read_text(encoding="utf-8")
        local_summary_cli = load_stdout(run_cli("local-summary", str(output_dir), "--format", "json"))
        local_summary_file_after = (output_dir / "local-summary.json").read_text(encoding="utf-8")
        local_summary_markdown = run_cli("local-summary", str(output_dir), "--format", "markdown")
        user_steps_file_before = (output_dir / "user-steps.json").read_text(encoding="utf-8")
        user_steps_cli = load_stdout(run_cli("desktop-user-steps", str(output_dir), "--format", "json"))
        user_steps_file_after = (output_dir / "user-steps.json").read_text(encoding="utf-8")
        user_steps_markdown = run_cli("desktop-user-steps", str(output_dir), "--format", "markdown")
        user_steps_html_cli = run_cli("desktop-user-steps", str(output_dir), "--format", "html")
        readiness_file_before = (output_dir / "desktop-pack-readiness.json").read_text(encoding="utf-8")
        desktop_pack_readiness_cli = load_stdout(run_cli("desktop-pack-readiness", str(output_dir), "--format", "json"))
        readiness_file_after = (output_dir / "desktop-pack-readiness.json").read_text(encoding="utf-8")
        desktop_pack_readiness_markdown = run_cli("desktop-pack-readiness", str(output_dir), "--format", "markdown")
        starter_brief_file_before = (output_dir / "starter-brief.json").read_text(encoding="utf-8")
        starter_brief_cli = load_stdout(run_cli("starter-brief", str(output_dir), "--format", "json"))
        starter_brief_file_after = (output_dir / "starter-brief.json").read_text(encoding="utf-8")
        starter_brief_cli_markdown = run_cli("starter-brief", str(output_dir), "--format", "markdown")
        agent_startup_prompt_file_before = (output_dir / "agent-startup-prompt.json").read_text(encoding="utf-8")
        agent_startup_prompt_cli = load_stdout(run_cli("agent-startup-prompt-from-pack", str(output_dir), "--format", "json"))
        agent_startup_prompt_file_after = (output_dir / "agent-startup-prompt.json").read_text(encoding="utf-8")
        agent_startup_prompt_cli_markdown = run_cli("agent-startup-prompt-from-pack", str(output_dir), "--format", "markdown")
        agent_takeover_file_before = (output_dir / "agent-takeover.json").read_text(encoding="utf-8")
        agent_takeover_cli = load_stdout(run_cli("agent-takeover-from-pack", str(output_dir), "--format", "json"))
        agent_takeover_file_after = (output_dir / "agent-takeover.json").read_text(encoding="utf-8")
        agent_takeover_cli_markdown = run_cli("agent-takeover-from-pack", str(output_dir), "--format", "markdown")
        troubleshooting_file_before = (output_dir / "troubleshooting.json").read_text(encoding="utf-8")
        troubleshooting_cli = load_stdout(run_cli("troubleshooting-from-pack", str(output_dir), "--format", "json"))
        troubleshooting_file_after = (output_dir / "troubleshooting.json").read_text(encoding="utf-8")
        troubleshooting_cli_markdown = run_cli("troubleshooting-from-pack", str(output_dir), "--format", "markdown")
        author_actions_file_before = (output_dir / "author-actions.json").read_text(encoding="utf-8")
        author_actions_cli = load_stdout(run_cli("author-actions", str(output_dir), "--format", "json"))
        author_actions_file_after = (output_dir / "author-actions.json").read_text(encoding="utf-8")
        author_actions_cli_markdown = run_cli("author-actions", str(output_dir), "--format", "markdown")
        acceptance_review_file_before = (output_dir / "acceptance-review.json").read_text(encoding="utf-8")
        acceptance_review_cli = load_stdout(run_cli("acceptance-review-from-pack", str(output_dir), "--format", "json"))
        acceptance_review_file_after = (output_dir / "acceptance-review.json").read_text(encoding="utf-8")
        acceptance_review_cli_markdown = run_cli("acceptance-review-from-pack", str(output_dir), "--format", "markdown")
        acceptance_review_cli_html = run_cli("acceptance-review-from-pack", str(output_dir), "--format", "html")
        archive_output_dir = Path(tmp) / "pack archive out"
        archive_pack = load_stdout(
            run_cli(
                "desktop-pack",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--install-config",
                str(config),
                "--install-target",
                str(target),
                "--chapter",
                "2",
                "--output-dir",
                str(archive_output_dir),
                "--archive",
                "--format",
                "json",
            )
        )
        archive_info = archive_pack.get("archive") if isinstance(archive_pack.get("archive"), dict) else {}
        archive_path = Path(str(archive_info.get("path") or ""))
        assert_true(archive_pack.get("archive_requested") is True, "desktop-pack archive run should mark archive requested")
        assert_true(archive_pack.get("archive_path") == str(archive_output_dir.with_suffix(".zip").resolve()), "desktop-pack archive path mismatch")
        assert_true(archive_path.exists(), "desktop-pack --archive did not write zip")
        archive_bytes = archive_path.read_bytes()
        assert_true(hashlib.sha256(archive_bytes).hexdigest() == archive_info.get("sha256"), "desktop-pack archive sha256 mismatch")
        assert_true(archive_info.get("file_count") == len(archive_pack["written_files"]), "desktop-pack archive file count mismatch")
        archive_metadata = json.loads((archive_output_dir / "metadata.json").read_text(encoding="utf-8"))
        assert_true(archive_metadata.get("archive_requested") is True, "desktop-pack archive metadata missed archive request")
        assert_true(archive_metadata.get("archive_path") == str(archive_path), "desktop-pack archive metadata path mismatch")
        with zipfile.ZipFile(archive_path) as archive_zip:
            archive_names = set(archive_zip.namelist())
        archive_root = str(archive_info.get("root_name") or archive_output_dir.name)
        assert_true(f"{archive_root}/START_HERE.md" in archive_names, "desktop-pack archive missed START_HERE.md")
        assert_true(f"{archive_root}/pack-manifest.json" in archive_names, "desktop-pack archive missed pack manifest")
        assert_true(f"{archive_root}/metadata.json" in archive_names, "desktop-pack archive missed metadata")
        assert_true(not any(name.endswith(".zip") for name in archive_names), "desktop-pack archive should not include nested zip")
        corrupt_pack_dir = Path(tmp) / "pack corrupt"
        shutil.copytree(output_dir, corrupt_pack_dir)
        (corrupt_pack_dir / "README.md").unlink()
        corrupt_verify_cli_run = subprocess.run(
            [sys.executable, str(CLI), "verify-pack", str(corrupt_pack_dir), "--format", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        corrupt_verify_cli = json.loads(corrupt_verify_cli_run.stdout)
        corrupt_doctor_cli = load_stdout(run_cli("pack-doctor", str(corrupt_pack_dir), "--format", "json"))
        setup = (output_dir / "setup.md").read_text(encoding="utf-8")
        checklist = (output_dir / "checklist.md").read_text(encoding="utf-8")
        project_status_json = json.loads((output_dir / "project-status.json").read_text(encoding="utf-8"))
        continuity_audit = (output_dir / "continuity-audit.md").read_text(encoding="utf-8")
        continuity_audit_json = json.loads((output_dir / "continuity-audit.json").read_text(encoding="utf-8"))
        author_review_queue = (output_dir / "author-review-queue.md").read_text(encoding="utf-8")
        author_review_queue_json = json.loads((output_dir / "author-review-queue.json").read_text(encoding="utf-8"))
        handoff_report_json = json.loads((output_dir / "handoff-report.json").read_text(encoding="utf-8"))
        handoff_report_schema = json.loads((output_dir / "handoff-report.schema.json").read_text(encoding="utf-8"))
        handoff_brief = (output_dir / "handoff-brief.md").read_text(encoding="utf-8")
        handoff_brief_json = json.loads((output_dir / "handoff-brief.json").read_text(encoding="utf-8"))
        chapter_session = (output_dir / "chapter-session.md").read_text(encoding="utf-8")
        chapter_session_json = json.loads((output_dir / "chapter-session.json").read_text(encoding="utf-8"))
        chapter_session_schema = json.loads((output_dir / "chapter-session.schema.json").read_text(encoding="utf-8"))
        desktop_matrix = (output_dir / "desktop-matrix.md").read_text(encoding="utf-8")
        desktop_matrix_json = json.loads((output_dir / "desktop-matrix.json").read_text(encoding="utf-8"))
        desktop_evidence_session = (output_dir / "desktop-evidence-session.md").read_text(encoding="utf-8")
        desktop_evidence_session_json = json.loads((output_dir / "desktop-evidence-session.json").read_text(encoding="utf-8"))
        desktop_evidence_session_schema = json.loads((output_dir / "desktop-evidence-session.schema.json").read_text(encoding="utf-8"))
        desktop_evidence_runbook = (output_dir / "desktop-evidence-runbook.md").read_text(encoding="utf-8")
        desktop_evidence_runbook_json = json.loads((output_dir / "desktop-evidence-runbook.json").read_text(encoding="utf-8"))
        desktop_evidence_runbook_schema = json.loads((output_dir / "desktop-evidence-runbook.schema.json").read_text(encoding="utf-8"))
        desktop_evidence_review = (output_dir / "desktop-evidence-review.md").read_text(encoding="utf-8")
        desktop_evidence_review_json = json.loads((output_dir / "desktop-evidence-review.json").read_text(encoding="utf-8"))
        desktop_evidence_review_html = (output_dir / "desktop-evidence-review.html").read_text(encoding="utf-8")
        desktop_evidence_review_schema = json.loads((output_dir / "desktop-evidence-review.schema.json").read_text(encoding="utf-8"))
        desktop_evidence_gaps = (output_dir / "desktop-evidence-gaps.md").read_text(encoding="utf-8")
        desktop_evidence_gaps_json = json.loads((output_dir / "desktop-evidence-gaps.json").read_text(encoding="utf-8"))
        desktop_pack_readiness = (output_dir / "desktop-pack-readiness.md").read_text(encoding="utf-8")
        desktop_pack_readiness_json = json.loads((output_dir / "desktop-pack-readiness.json").read_text(encoding="utf-8"))
        desktop_pack_readiness_schema = json.loads((output_dir / "desktop-pack-readiness.schema.json").read_text(encoding="utf-8"))
        starter_brief = (output_dir / "starter-brief.md").read_text(encoding="utf-8")
        starter_brief_json = json.loads((output_dir / "starter-brief.json").read_text(encoding="utf-8"))
        starter_brief_schema = json.loads((output_dir / "starter-brief.schema.json").read_text(encoding="utf-8"))
        agent_takeover = (output_dir / "agent-takeover.md").read_text(encoding="utf-8")
        agent_takeover_json = json.loads((output_dir / "agent-takeover.json").read_text(encoding="utf-8"))
        agent_takeover_schema = json.loads((output_dir / "agent-takeover.schema.json").read_text(encoding="utf-8"))
        author_actions = (output_dir / "author-actions.md").read_text(encoding="utf-8")
        author_actions_json = json.loads((output_dir / "author-actions.json").read_text(encoding="utf-8"))
        author_actions_schema = json.loads((output_dir / "author-actions.schema.json").read_text(encoding="utf-8"))
        real_client_walkthrough = (output_dir / "real-client-walkthrough.md").read_text(encoding="utf-8")
        client_prompts = (output_dir / "client-prompts.md").read_text(encoding="utf-8")
        result_json_guide = (output_dir / "result-json-guide.md").read_text(encoding="utf-8")
        results_schema = json.loads((output_dir / "desktop-results.schema.json").read_text(encoding="utf-8"))
        result_json = json.loads((output_dir / pack["result_file"]).read_text(encoding="utf-8"))
        evidence_to_results = (output_dir / "evidence-to-results.md").read_text(encoding="utf-8")
        desktop_pack_snapshot_summary = verify_desktop_pack_snapshot(output_dir, pack, project, config, target)
        assert_true(pack_index_json["type"] == "desktop_pack_index", "desktop-pack index JSON wrong type")
        assert_true(pack_index_json["$schema"] == "pack-index.schema.json", "desktop-pack index JSON missed local schema")
        assert_true(pack_index_schema["title"] == "Long Novel Agent Desktop Pack Index", "desktop-pack index schema title mismatch")
        assert_true(pack_index_json["kit_version"] == current_kit_version(), "desktop-pack index JSON missed current kit version")
        assert_true(pack_index_json["project"]["target_chapter"] == 2, "desktop-pack index JSON missed target chapter")
        assert_true(pack_index_json["local_only"] is True and pack_index_json["requires_server"] is False, "desktop-pack index JSON should describe local-only pack")
        assert_true(pack_index_json["writes_project_state"] is False, "desktop-pack index JSON should be read-only")
        assert_true(pack_index_json["status"]["desktop_next_step_status"] == desktop_next_step_json["status"], "desktop-pack index JSON missed desktop next step status")
        assert_true(pack_index_json["status"]["desktop_next_step_primary_action"] == desktop_next_step_json["primary_action"]["id"], "desktop-pack index JSON missed desktop next step primary action")
        assert_true(pack_index_json["status"]["desktop_diagnostics_status"] == desktop_diagnostics_json["status"], "desktop-pack index JSON missed desktop diagnostics status")
        assert_true(pack_index_json["status"]["desktop_diagnostics_check_count"] == len(desktop_diagnostics_json["checks"]), "desktop-pack index JSON missed desktop diagnostics check count")
        assert_true(pack_index_json["status"]["first_three_status"] == first_three_json["status"], "desktop-pack index JSON missed first three status")
        assert_true(pack_index_json["status"]["first_three_file_count"] == len(first_three_json["files"]), "desktop-pack index JSON missed first three file count")
        assert_true(pack_index_json["status"]["user_steps_status"] == user_steps_json["status"], "desktop-pack index JSON missed user steps status")
        entrypoint_names = {item.get("name") for item in pack_index_json.get("entrypoints", []) if isinstance(item, dict)}
        assert_true({"pack-index.json", "first-three.md", "first-three.json", "first-three.html", "first-three.schema.json", "START_HERE.md", "local-summary.md", "local-summary.json", "local-summary.html", "user-steps.md", "user-steps.json", "user-steps.html", "starter-brief.md", "starter-brief.json", "desktop-next-step.md", "desktop-next-step.json", "desktop-diagnostics.md", "desktop-diagnostics.json", "desktop-diagnostics.schema.json", "desktop-pack-readiness.json", "agent-startup-prompt.md", "agent-startup-prompt.json", "troubleshooting.md", "troubleshooting.json", "troubleshooting.html", "agent-takeover.md", "agent-takeover.json", "author-actions.md", "author-actions.json", "acceptance-review.md", "acceptance-review.json", "acceptance-review.html", "desktop-evidence-session.md", "desktop-evidence-session.json", "desktop-evidence-runbook.md", "desktop-evidence-runbook.json", "desktop-evidence-review.md", "desktop-evidence-review.json", "desktop-evidence-review.html", "desktop-evidence-gaps.md", "desktop-evidence-gaps.json", "writer-mode-approval.md", "writer-mode-approval.json", "index.html", "agent-start.md", "commands-index.md", "commands-index.json", "commands.json"} <= entrypoint_names, "desktop-pack index JSON missed core entrypoints")
        startup_reads = [item.get("read") for item in pack_index_json.get("startup_order", []) if isinstance(item, dict)]
        assert_true(startup_reads[:3] == ["pack-index.json", "first-three.json", "START_HERE.md"], "desktop-pack index JSON startup order should start with pack index, first-three, and START_HERE")
        assert_true("first-three.json" in startup_reads, "desktop-pack index JSON startup order missed first three JSON")
        assert_true("local-summary.json" in startup_reads, "desktop-pack index JSON startup order missed local summary JSON")
        assert_true("user-steps.json" in startup_reads, "desktop-pack index JSON startup order missed user steps JSON")
        assert_true("starter-brief.json" in startup_reads, "desktop-pack index JSON startup order missed starter brief JSON")
        assert_true("desktop-next-step.json" in startup_reads, "desktop-pack index JSON startup order missed desktop next step JSON")
        assert_true("desktop-diagnostics.json" in startup_reads, "desktop-pack index JSON startup order missed desktop diagnostics JSON")
        assert_true("desktop-pack-readiness.json" in startup_reads, "desktop-pack index JSON startup order missed desktop pack readiness JSON")
        assert_true("agent-startup-prompt.json" in startup_reads, "desktop-pack index JSON startup order missed agent startup prompt JSON")
        assert_true("troubleshooting.json" in startup_reads, "desktop-pack index JSON startup order missed troubleshooting JSON")
        assert_true("agent-takeover.json" in startup_reads, "desktop-pack index JSON startup order missed agent takeover JSON")
        assert_true("author-actions.json" in startup_reads, "desktop-pack index JSON startup order missed author actions JSON")
        assert_true("acceptance-review.json" in startup_reads, "desktop-pack index JSON startup order missed acceptance review JSON")
        assert_true("desktop-evidence-session.json" in startup_reads, "desktop-pack index JSON startup order missed desktop evidence session JSON")
        assert_true("desktop-evidence-runbook.json" in startup_reads, "desktop-pack index JSON startup order missed desktop evidence runbook JSON")
        assert_true("desktop-evidence-review.json" in startup_reads, "desktop-pack index JSON startup order missed desktop evidence review JSON")
        assert_true("desktop-evidence-gaps.json" in startup_reads, "desktop-pack index JSON startup order missed desktop evidence gaps JSON")
        assert_true("writer-mode-approval.json" in startup_reads, "desktop-pack index JSON startup order missed writer mode approval JSON")
        assert_true("commands-index.json" in startup_reads, "desktop-pack index JSON startup order missed commands index JSON")
        machine_files = pack_index_json.get("machine_files") if isinstance(pack_index_json.get("machine_files"), dict) else {}
        navigation_names = {item.get("name") for item in machine_files.get("navigation", []) if isinstance(item, dict)}
        status_names = {item.get("name") for item in machine_files.get("status", []) if isinstance(item, dict)}
        schema_names = {item.get("name") for item in machine_files.get("schemas", []) if isinstance(item, dict)}
        evidence_names = {item.get("name") for item in machine_files.get("evidence", []) if isinstance(item, dict)}
        present_names = set((pack_index_json.get("files") or {}).get("present") or []) if isinstance(pack_index_json.get("files"), dict) else set()
        assert_true({"pack-index.json", "first-three.md", "first-three.json", "first-three.html", "first-three.schema.json", "local-summary.md", "local-summary.json", "local-summary.html", "local-summary.schema.json", "user-steps.md", "user-steps.json", "user-steps.html", "user-steps.schema.json", "starter-brief.md", "starter-brief.json", "starter-brief.schema.json", "desktop-diagnostics.md", "desktop-diagnostics.json", "desktop-diagnostics.schema.json", "agent-startup-prompt.json", "agent-startup-prompt.schema.json", "troubleshooting.json", "troubleshooting.html", "troubleshooting.schema.json", "agent-takeover.json", "agent-takeover.schema.json", "author-actions.json", "author-actions.schema.json", "acceptance-review.json", "acceptance-review.html", "acceptance-review.schema.json", "writer-mode-approval.json", "writer-mode-approval.schema.json", "commands-index.md", "commands-index.json", "commands.json", "metadata.json"} <= navigation_names, "desktop-pack index JSON missed navigation files")
        assert_true({"project-status.json", "first-three.json", "local-summary.json", "user-steps.json", "starter-brief.json", "desktop-next-step.json", "desktop-diagnostics.json", "desktop-pack-readiness.json", "agent-startup-prompt.json", "agent-takeover.json", "author-actions.json", "acceptance-review.json", "continuity-audit.json", "author-review-queue.json", "handoff-report.json", "chapter-session.json"} <= status_names, "desktop-pack index JSON missed status files")
        assert_true({"desktop-evidence-session.json", "desktop-evidence-session.schema.json", "desktop-evidence-session.md", "desktop-evidence-runbook.json", "desktop-evidence-runbook.schema.json", "desktop-evidence-runbook.md", "desktop-evidence-review.json", "desktop-evidence-review.schema.json", "desktop-evidence-review.md", "desktop-evidence-review.html", "desktop-evidence-gaps.json", "desktop-evidence-gaps.md", "writer-mode-approval.json", "writer-mode-approval.schema.json", "desktop-evidence.txt", pack["result_file"], "desktop-results.schema.json"} <= evidence_names, "desktop-pack index JSON missed evidence files")
        assert_true({"pack-index.schema.json", "first-three.schema.json", "local-summary.schema.json", "user-steps.schema.json", "starter-brief.schema.json", "commands-index.schema.json", "commands.schema.json", "desktop-next-step.schema.json", "desktop-diagnostics.schema.json", "desktop-pack-readiness.schema.json", "agent-startup-prompt.schema.json", "troubleshooting.schema.json", "agent-takeover.schema.json", "author-actions.schema.json", "acceptance-review.schema.json", "desktop-evidence-session.schema.json", "desktop-evidence-runbook.schema.json", "desktop-evidence-review.schema.json", "writer-mode-approval.schema.json", "chapter-session.schema.json", "handoff-report.schema.json", "desktop-results.schema.json"} <= navigation_names | schema_names, "desktop-pack index JSON missed schema references")
        assert_true("commands-index.schema.json" in present_names, "desktop-pack index JSON files list missed commands index schema")
        assert_true("desktop-next-step.schema.json" in present_names, "desktop-pack index JSON files list missed desktop next step schema")
        assert_true("desktop-diagnostics.md" in present_names and "desktop-diagnostics.json" in present_names and "desktop-diagnostics.schema.json" in present_names, "desktop-pack index JSON files list missed desktop diagnostics files")
        assert_true("desktop-pack-readiness.schema.json" in present_names, "desktop-pack index JSON files list missed desktop pack readiness schema")
        assert_true("first-three.md" in present_names and "first-three.json" in present_names and "first-three.html" in present_names and "first-three.schema.json" in present_names, "desktop-pack index JSON files list missed first three files")
        assert_true("local-summary.md" in present_names and "local-summary.json" in present_names and "local-summary.html" in present_names and "local-summary.schema.json" in present_names, "desktop-pack index JSON files list missed local summary files")
        assert_true("user-steps.md" in present_names and "user-steps.json" in present_names and "user-steps.html" in present_names and "user-steps.schema.json" in present_names, "desktop-pack index JSON files list missed user steps files")
        assert_true("starter-brief.md" in present_names and "starter-brief.json" in present_names and "starter-brief.schema.json" in present_names, "desktop-pack index JSON files list missed starter brief files")
        assert_true("agent-startup-prompt.json" in present_names, "desktop-pack index JSON files list missed agent startup prompt JSON")
        assert_true("agent-startup-prompt.schema.json" in present_names, "desktop-pack index JSON files list missed agent startup prompt schema")
        assert_true("troubleshooting.json" in present_names, "desktop-pack index JSON files list missed troubleshooting JSON")
        assert_true("troubleshooting.html" in present_names, "desktop-pack index JSON files list missed troubleshooting HTML")
        assert_true("troubleshooting.schema.json" in present_names, "desktop-pack index JSON files list missed troubleshooting schema")
        assert_true("agent-takeover.json" in present_names, "desktop-pack index JSON files list missed agent takeover JSON")
        assert_true("agent-takeover.schema.json" in present_names, "desktop-pack index JSON files list missed agent takeover schema")
        assert_true("author-actions.json" in present_names, "desktop-pack index JSON files list missed author actions JSON")
        assert_true("author-actions.schema.json" in present_names, "desktop-pack index JSON files list missed author actions schema")
        assert_true("acceptance-review.json" in present_names, "desktop-pack index JSON files list missed acceptance review JSON")
        assert_true("acceptance-review.html" in present_names, "desktop-pack index JSON files list missed acceptance review HTML")
        assert_true("acceptance-review.schema.json" in present_names, "desktop-pack index JSON files list missed acceptance review schema")
        assert_true("desktop-evidence-session.schema.json" in present_names, "desktop-pack index JSON files list missed desktop evidence session schema")
        assert_true("desktop-evidence-runbook.md" in present_names and "desktop-evidence-runbook.json" in present_names, "desktop-pack index JSON files list missed desktop evidence runbook files")
        assert_true("desktop-evidence-review.md" in present_names and "desktop-evidence-review.json" in present_names and "desktop-evidence-review.html" in present_names and "desktop-evidence-review.schema.json" in present_names, "desktop-pack index JSON files list missed desktop evidence review files")
        assert_true("desktop-evidence-gaps.md" in present_names and "desktop-evidence-gaps.json" in present_names, "desktop-pack index JSON files list missed desktop evidence gaps files")
        assert_true("writer-mode-approval.json" in present_names, "desktop-pack index JSON files list missed writer mode approval JSON")
        assert_true("writer-mode-approval.schema.json" in present_names, "desktop-pack index JSON files list missed writer mode approval schema")
        important_commands = pack_index_json.get("commands", {}).get("important") if isinstance(pack_index_json.get("commands"), dict) else []
        important_by_key = {item.get("key"): item for item in important_commands if isinstance(item, dict)}
        for key in ["open_pack_index", "local_summary", "desktop_user_steps", "starter_brief", "verify_pack", "pack_schema_check", "pack_freshness", "chapter_session_freshness", "agent_startup_prompt", "desktop_next_step", "desktop_diagnostics", "prepare_session", "context_brief", "handoff_readiness", "handoff_report", "chapter_revision_prompt", "chapter_revision_compare", "chapter_delivery", "desktop_results_doctor", "record_agent_activity"]:
            assert_true(important_by_key.get(key, {}).get("present") is True, f"desktop-pack index JSON missed important command {key}")
        assert_true(important_by_key["record_agent_activity"]["writes_project_state"] is True, "desktop-pack index JSON should mark record_agent_activity as writing project state")
        boundaries = pack_index_json.get("write_boundaries") if isinstance(pack_index_json.get("write_boundaries"), dict) else {}
        assert_true(boundaries.get("pack_index_is_read_only") is True, "desktop-pack index JSON missed read-only boundary")
        assert_true("record_agent_activity" in boundaries.get("read_only_agents_must_not_execute", []), "desktop-pack index JSON missed read-only activity write boundary")
        assert_true("Long Novel Agent Desktop Pack" in readme, "desktop-pack README missing title")
        assert_true("pack-index.json" in readme, "desktop-pack README missed pack index file")
        assert_true("pack-index.schema.json" in readme, "desktop-pack README missed pack index schema file")
        assert_true("local-summary.md" in readme and "local-summary.json" in readme and "local-summary.html" in readme and "local-summary.schema.json" in readme, "desktop-pack README missed local summary files")
        assert_true("user-steps.md" in readme and "user-steps.json" in readme and "user-steps.html" in readme, "desktop-pack README missed user steps files")
        assert_true("starter-brief.md" in readme and "starter-brief.json" in readme, "desktop-pack README missed starter brief files")
        assert_true("starter-brief.schema.json" in readme, "desktop-pack README missed starter brief schema file")
        assert_true("desktop-next-step.md" in readme and "desktop-next-step.json" in readme, "desktop-pack README missed desktop next step files")
        assert_true("desktop-next-step.schema.json" in readme, "desktop-pack README missed desktop next step schema file")
        assert_true("desktop-diagnostics.md" in readme and "desktop-diagnostics.json" in readme, "desktop-pack README missed desktop diagnostics files")
        assert_true("desktop-diagnostics.schema.json" in readme, "desktop-pack README missed desktop diagnostics schema file")
        assert_true("desktop-pack-readiness.md" in readme and "desktop-pack-readiness.json" in readme, "desktop-pack README missed desktop pack readiness files")
        assert_true("desktop-pack-readiness.schema.json" in readme, "desktop-pack README missed desktop pack readiness schema file")
        assert_true("pack-schema-check.md" in readme and "pack-schema-check.json" in readme, "desktop-pack README missed pack schema check files")
        assert_true("agent-startup-prompt.md" in readme and "agent-startup-prompt.json" in readme, "desktop-pack README missed agent startup prompt files")
        assert_true("agent-startup-prompt.schema.json" in readme, "desktop-pack README missed agent startup prompt schema file")
        assert_true("troubleshooting.md" in readme and "troubleshooting.json" in readme and "troubleshooting.html" in readme, "desktop-pack README missed troubleshooting files")
        assert_true("troubleshooting.schema.json" in readme, "desktop-pack README missed troubleshooting schema file")
        assert_true("agent-takeover.md" in readme and "agent-takeover.json" in readme, "desktop-pack README missed agent takeover files")
        assert_true("agent-takeover.schema.json" in readme, "desktop-pack README missed agent takeover schema file")
        assert_true("author-actions.md" in readme and "author-actions.json" in readme, "desktop-pack README missed author actions files")
        assert_true("author-actions.schema.json" in readme, "desktop-pack README missed author actions schema file")
        assert_true("acceptance-review.html" in readme and "acceptance-review.md" in readme and "acceptance-review.json" in readme, "desktop-pack README missed acceptance review files")
        assert_true("acceptance-review.schema.json" in readme, "desktop-pack README missed acceptance review schema file")
        assert_true("desktop-evidence-session.md" in readme and "desktop-evidence-session.json" in readme, "desktop-pack README missed desktop evidence session files")
        assert_true("desktop-evidence-session.schema.json" in readme, "desktop-pack README missed desktop evidence session schema file")
        assert_true("desktop-evidence-runbook.md" in readme and "desktop-evidence-runbook.json" in readme, "desktop-pack README missed desktop evidence runbook files")
        assert_true("desktop-evidence-review.html" in readme and "desktop-evidence-review.md" in readme and "desktop-evidence-review.json" in readme, "desktop-pack README missed desktop evidence review files")
        assert_true("desktop-evidence-review.schema.json" in readme, "desktop-pack README missed desktop evidence review schema file")
        assert_true("desktop-evidence-gaps.md" in readme and "desktop-evidence-gaps.json" in readme, "desktop-pack README missed desktop evidence gaps files")
        assert_true("writer-mode-approval.json" in readme, "desktop-pack README missed writer approval JSON file")
        assert_true("writer-mode-approval.schema.json" in readme, "desktop-pack README missed writer approval schema file")
        assert_true("Long Novel Agent Desktop Diagnostics" in desktop_diagnostics_md, "desktop-pack desktop diagnostics markdown missed title")
        assert_true(desktop_diagnostics_json["type"] == "desktop_diagnostics", "desktop-pack desktop diagnostics JSON wrong type")
        assert_true(desktop_diagnostics_json["$schema"] == "desktop-diagnostics.schema.json", "desktop-pack desktop diagnostics JSON missed local schema")
        assert_true(desktop_diagnostics_json["target_chapter"] == 2, "desktop-pack desktop diagnostics target mismatch")
        assert_true(desktop_diagnostics_json["would_write_project_state"] is False, "desktop-pack desktop diagnostics should be read-only")
        assert_true(desktop_diagnostics_json["records_real_client_evidence"] is False, "desktop-pack desktop diagnostics should not record evidence")
        assert_true(len(desktop_diagnostics_json["checks"]) >= 5, "desktop-pack desktop diagnostics missed core checks")
        assert_true(desktop_diagnostics_schema["title"] == "Long Novel Agent Desktop Diagnostics", "desktop-pack desktop diagnostics schema title mismatch")
        assert_true(desktop_evidence_session_json["type"] == "desktop_evidence_session", "desktop-pack desktop evidence session JSON wrong type")
        assert_true(desktop_evidence_session_json["$schema"] == "desktop-evidence-session.schema.json", "desktop-pack desktop evidence session JSON missed local schema")
        assert_true(desktop_evidence_session_json["target_chapter"] == 2, "desktop-pack desktop evidence session target mismatch")
        assert_true(desktop_evidence_session_json["requires_real_desktop_client"] is True, "desktop-pack desktop evidence session should require real client")
        assert_true(desktop_evidence_session_json["would_write_project_state"] is False, "desktop-pack desktop evidence session should be read-only")
        assert_true(desktop_evidence_session_json["records_real_client_evidence"] is False, "desktop-pack desktop evidence session should not record evidence")
        assert_true(len(desktop_evidence_session_json["checks"]) >= 8, "desktop-pack desktop evidence session missed checks")
        assert_true(len(desktop_evidence_session_json["copy_prompts"]) >= 6, "desktop-pack desktop evidence session missed copy prompts")
        assert_true("Long Novel Agent Desktop Evidence Session" in desktop_evidence_session, "desktop-pack desktop evidence session markdown missed title")
        assert_true(desktop_evidence_session_schema["title"] == "Long Novel Agent Desktop Evidence Session", "desktop-pack desktop evidence session schema title mismatch")
        assert_true("desktop_results_doctor" in desktop_evidence_session, "desktop-pack desktop evidence session markdown missed results doctor")
        assert_true("record_desktop_check" in desktop_evidence_session, "desktop-pack desktop evidence session markdown missed record command")
        assert_true(desktop_evidence_runbook_json["type"] == "desktop_evidence_runbook", "desktop-pack desktop evidence runbook JSON wrong type")
        assert_true(desktop_evidence_runbook_json["$schema"] == "desktop-evidence-runbook.schema.json", "desktop-pack desktop evidence runbook JSON missed local schema")
        assert_true(desktop_evidence_runbook_json["target_chapter"] == 2, "desktop-pack desktop evidence runbook target mismatch")
        assert_true(desktop_evidence_runbook_json["requires_real_desktop_client"] is True, "desktop-pack desktop evidence runbook should require real client")
        assert_true(desktop_evidence_runbook_json["would_write_project_state"] is False, "desktop-pack desktop evidence runbook should be read-only")
        assert_true(desktop_evidence_runbook_json["records_real_client_evidence"] is False, "desktop-pack desktop evidence runbook should not record evidence")
        assert_true(desktop_evidence_runbook_json["desktop_step_count"] >= 8, "desktop-pack desktop evidence runbook missed GUI steps")
        assert_true(any(item.get("id") == "record" and item.get("writes_project_state") is True for item in desktop_evidence_runbook_json["after_gui"]), "desktop-pack desktop evidence runbook missed record gate")
        assert_true("Long Novel Agent Desktop Evidence Runbook" in desktop_evidence_runbook, "desktop-pack desktop evidence runbook markdown missed title")
        assert_true(desktop_evidence_runbook_schema["title"] == "Long Novel Agent Desktop Evidence Runbook", "desktop-pack desktop evidence runbook schema title mismatch")
        assert_true("desktop-results-doctor" in desktop_evidence_runbook or "imported_desktop_results_doctor" in desktop_evidence_runbook, "desktop-pack desktop evidence runbook markdown missed result doctor")
        assert_true(desktop_evidence_review_json["type"] == "desktop_evidence_review", "desktop-pack desktop evidence review JSON wrong type")
        assert_true(desktop_evidence_review_json["$schema"] == "desktop-evidence-review.schema.json", "desktop-pack desktop evidence review JSON missed local schema")
        assert_true(desktop_evidence_review_json["target_chapter"] == 2, "desktop-pack desktop evidence review target mismatch")
        assert_true(desktop_evidence_review_json["requires_real_desktop_client"] is True, "desktop-pack desktop evidence review should require real client")
        assert_true(desktop_evidence_review_json["would_write_project_state"] is False, "desktop-pack desktop evidence review should be read-only")
        assert_true(desktop_evidence_review_json["would_write_install_config"] is False, "desktop-pack desktop evidence review should not write install config")
        assert_true(desktop_evidence_review_json["would_write_pack_files"] is False, "desktop-pack desktop evidence review should not write pack files")
        assert_true(desktop_evidence_review_json["records_real_client_evidence"] is False, "desktop-pack desktop evidence review should not record evidence")
        assert_true(desktop_evidence_review_json["status"] in {"ready_to_record", "needs_revision", "needs_evidence", "recorded", "recorded_elsewhere"}, "desktop-pack desktop evidence review status mismatch")
        assert_true(desktop_evidence_review_schema["title"] == "Long Novel Agent Desktop Evidence Review", "desktop-pack desktop evidence review schema title mismatch")
        assert_true("Long Novel Agent Desktop Evidence Review" in desktop_evidence_review, "desktop-pack desktop evidence review markdown missed title")
        assert_true("desktop_evidence_session" in desktop_evidence_review or "desktop-evidence-session" in desktop_evidence_review, "desktop-pack desktop evidence review markdown missed evidence session command")
        assert_true("record_desktop_check" in desktop_evidence_review, "desktop-pack desktop evidence review markdown missed record command")
        assert_true("Long Novel Agent Desktop Evidence Review" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed title")
        assert_true("Recommended Commands" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed commands section")
        assert_true("record_desktop_check" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed record command")
        assert_true("desktop-evidence-review.json" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed JSON link")
        assert_true("Local File Recheck" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed local file recheck")
        assert_true('id="resultsFile"' in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed results file input")
        assert_true('id="evidenceFile"' in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed evidence file input")
        assert_true('id="localResultsPreview"' in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed local results preview")
        assert_true('id="copyResultsPreview"' in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed preview copy button")
        assert_true('id="downloadResultsPreview"' in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed preview download button")
        assert_true("Nothing is uploaded, written to the novel project, or recorded" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed local-only warning")
        assert_true("function parseStructuredEvidenceText" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed structured evidence parser")
        assert_true("function downloadResultsPreview" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed preview downloader")
        assert_true("function analyzeLoadedFiles" in desktop_evidence_review_html, "desktop-pack desktop evidence review HTML missed local analyzer")
        assert_true(desktop_evidence_gaps_json["type"] == "desktop_evidence_gaps", "desktop-pack desktop evidence gaps JSON wrong type")
        assert_true(desktop_evidence_gaps_json["target_chapter"] == 2, "desktop-pack desktop evidence gaps target mismatch")
        assert_true(desktop_evidence_gaps_json["requires_real_desktop_client"] is True, "desktop-pack desktop evidence gaps should require real client")
        assert_true(desktop_evidence_gaps_json["would_write_project_state"] is False, "desktop-pack desktop evidence gaps should be read-only")
        assert_true(desktop_evidence_gaps_json["would_write_install_config"] is False, "desktop-pack desktop evidence gaps should not write install config")
        assert_true(desktop_evidence_gaps_json["would_write_pack_files"] is False, "desktop-pack desktop evidence gaps should not write pack files")
        assert_true(desktop_evidence_gaps_json["records_real_client_evidence"] is False, "desktop-pack desktop evidence gaps should not record evidence")
        assert_true(desktop_evidence_gaps_json["status"] in {"ready_to_record", "needs_revision", "needs_evidence", "recorded", "recorded_elsewhere"}, "desktop-pack desktop evidence gaps status mismatch")
        assert_true(isinstance(desktop_evidence_gaps_json.get("gaps"), list) and desktop_evidence_gaps_json["gaps"], "desktop-pack desktop evidence gaps missed gap list")
        assert_true("Long Novel Agent Desktop Evidence Gaps" in desktop_evidence_gaps, "desktop-pack desktop evidence gaps markdown missed title")
        assert_true("desktop_evidence_gaps" in desktop_evidence_gaps or "desktop-evidence-gaps" in desktop_evidence_gaps, "desktop-pack desktop evidence gaps markdown missed command")
        assert_true(local_summary_json["type"] == "desktop_local_summary", "desktop-pack local summary JSON wrong type")
        assert_true(local_summary_json["$schema"] == "local-summary.schema.json", "desktop-pack local summary JSON missed local schema")
        assert_true(local_summary_json["target_chapter"] == 2, "desktop-pack local summary target mismatch")
        assert_true(local_summary_json["local_only"] is True, "desktop-pack local summary should be local only")
        assert_true(local_summary_json["requires_server"] is False, "desktop-pack local summary should not require a server")
        assert_true(local_summary_json["would_write_project_state"] is False, "desktop-pack local summary should not write project state")
        assert_true(local_summary_json["would_write_install_config"] is False, "desktop-pack local summary should not write install config")
        assert_true(local_summary_json["would_write_pack_files"] is False, "desktop-pack local summary should not write pack files")
        assert_true(local_summary_json["records_real_client_evidence"] is False, "desktop-pack local summary should not record evidence")
        assert_true(local_summary_json["status"] in {"needs_pack_repair", "needs_initialization", "needs_fresh_pack", "needs_author_review", "needs_real_client_evidence", "ready_with_warnings", "ready_to_start"}, "desktop-pack local summary status mismatch")
        local_summary_cards = {item.get("id") for item in local_summary_json.get("cards", []) if isinstance(item, dict)}
        assert_true({"local_install", "pack_integrity", "freshness", "project_state", "author_review", "real_client_evidence"} <= local_summary_cards, "desktop-pack local summary missed status cards")
        expected_local_summary_quick_links = {"five_step_path", "inspect_pack", "initialize_project", "author_actions", "acceptance_review", "collect_evidence", "start_chapter"}
        local_summary_quick_links = {item.get("id"): item for item in local_summary_json.get("quick_links", []) if isinstance(item, dict)}
        assert_true(expected_local_summary_quick_links <= set(local_summary_quick_links), "desktop-pack local summary missed quick links")
        assert_true(sum(1 for item in local_summary_quick_links.values() if item.get("recommended") is True) == 1, "desktop-pack local summary should mark one quick link as recommended")
        for link_id in expected_local_summary_quick_links:
            item = local_summary_quick_links[link_id]
            assert_true(bool(item.get("href")) and bool(item.get("file")), f"desktop-pack local summary quick link {link_id} missed href or file")
            assert_true(item.get("writes_project_state") is False, f"desktop-pack local summary quick link {link_id} should not write project state")
            assert_true(item.get("writes_install_config") is False, f"desktop-pack local summary quick link {link_id} should not write install config")
            assert_true(item.get("writes_pack_files") is False, f"desktop-pack local summary quick link {link_id} should not write pack files")
            assert_true(item.get("records_real_client_evidence") is False, f"desktop-pack local summary quick link {link_id} should not record evidence")
        local_summary_commands = {item.get("key"): item for item in local_summary_json.get("commands", []) if isinstance(item, dict)}
        assert_true(local_summary_commands.get("local_summary", {}).get("present") is True, "desktop-pack local summary missed local_summary command")
        assert_true(local_summary_commands.get("pack_doctor_json", {}).get("present") is True, "desktop-pack local summary missed pack doctor command")
        assert_true(local_summary_commands.get("desktop_user_steps", {}).get("present") is True, "desktop-pack local summary missed user steps command")
        assert_true(local_summary_commands.get("quickstart_from_wizard_dry_run", {}).get("present") is True, "desktop-pack local summary missed quickstart command")
        assert_true(local_summary_commands.get("author_actions", {}).get("present") is True, "desktop-pack local summary missed author actions command")
        assert_true(local_summary_commands.get("acceptance_review", {}).get("present") is True, "desktop-pack local summary missed acceptance review command")
        assert_true(local_summary_commands.get("desktop_evidence_runbook", {}).get("present") is True, "desktop-pack local summary missed evidence runbook command")
        assert_true(local_summary_cli["type"] == "desktop_local_summary", "local-summary CLI returned wrong type")
        assert_true(local_summary_cli["source"] == "desktop_pack_directory", "local-summary CLI missed directory source")
        assert_true(local_summary_cli["target_chapter"] == 2, "local-summary CLI target mismatch")
        assert_true(local_summary_file_before == local_summary_file_after, "local-summary CLI should not rewrite generated JSON")
        assert_true("Local Desktop Summary" in local_summary_md, "desktop-pack local summary markdown missed title")
        assert_true("Quick Links" in local_summary_md, "desktop-pack local summary markdown missed quick links")
        assert_true("Open Five-Step Path" in local_summary_md, "desktop-pack local summary markdown missed user path quick link")
        assert_true("Start Chapter Session" in local_summary_md, "desktop-pack local summary markdown missed chapter quick link")
        assert_true("Local Desktop Summary" in local_summary_html, "desktop-pack local summary HTML missed title")
        assert_true("Quick Links" in local_summary_html, "desktop-pack local summary HTML missed quick links")
        assert_true("Open Five-Step Path" in local_summary_html, "desktop-pack local summary HTML missed user path quick link")
        assert_true("Start Chapter Session" in local_summary_html, "desktop-pack local summary HTML missed chapter quick link")
        assert_true("Recommended" in local_summary_html, "desktop-pack local summary HTML missed recommended marker")
        assert_true(local_summary_schema["title"] == "Long Novel Agent Local Summary", "desktop-pack local summary schema title mismatch")
        assert_true("local_summary" in local_summary_markdown.stdout, "local-summary CLI markdown missed command key")
        assert_true(first_three_json["type"] == "desktop_first_three", "desktop-pack first three JSON wrong type")
        assert_true(first_three_json["$schema"] == "first-three.schema.json", "desktop-pack first three JSON missed local schema")
        assert_true(first_three_json["target_chapter"] == 2, "desktop-pack first three target mismatch")
        assert_true(first_three_json["local_only"] is True, "desktop-pack first three should be local only")
        assert_true(first_three_json["requires_server"] is False, "desktop-pack first three should not require server")
        assert_true(first_three_json["would_write_project_state"] is False, "desktop-pack first three should not write project state")
        assert_true(first_three_json["would_write_install_config"] is False, "desktop-pack first three should not write install config")
        assert_true(first_three_json["would_write_pack_files"] is False, "desktop-pack first three should not write pack files")
        assert_true(first_three_json["records_real_client_evidence"] is False, "desktop-pack first three should not record evidence")
        first_three_file_names = {item.get("file") for item in first_three_json.get("files", []) if isinstance(item, dict)}
        assert_true({"user-steps.html", "local-summary.html", "agent-startup-prompt.md"} <= first_three_file_names, "desktop-pack first three missed expected file list")
        assert_true(first_three_json["write_boundaries"]["first_three_is_read_only"] is True, "desktop-pack first three missed read-only boundary")
        assert_true(first_three_schema["title"] == "Long Novel Agent First Three", "desktop-pack first three schema title mismatch")
        assert_true("First Three Files" in first_three_md, "desktop-pack first three markdown missed title")
        assert_true("First Three Files" in first_three_html and "<!doctype html>" in first_three_html, "desktop-pack first three HTML missed title or doctype")
        assert_true("user-steps.html" in first_three_md and "local-summary.html" in first_three_md and "agent-startup-prompt.md" in first_three_md, "desktop-pack first three markdown missed file names")
        assert_true(user_steps_json["type"] == "desktop_user_steps", "desktop-pack user steps JSON wrong type")
        assert_true(user_steps_json["$schema"] == "user-steps.schema.json", "desktop-pack user steps JSON missed local schema")
        assert_true(user_steps_json["target_chapter"] == 2, "desktop-pack user steps target mismatch")
        assert_true(user_steps_json["local_only"] is True, "desktop-pack user steps should be local only")
        assert_true(user_steps_json["requires_server"] is False, "desktop-pack user steps should not require server")
        assert_true(user_steps_json["would_write_project_state"] is False, "desktop-pack user steps should not write project state")
        assert_true(user_steps_json["would_write_install_config"] is False, "desktop-pack user steps should not write install config")
        assert_true(user_steps_json["would_write_pack_files"] is False, "desktop-pack user steps should not write pack files")
        assert_true(user_steps_json["records_real_client_evidence"] is False, "desktop-pack user steps should not record evidence")
        assert_true(user_steps_json["step_count"] == 5 and len(user_steps_json.get("steps", [])) == 5, "desktop-pack user steps should contain five steps")
        assert_true(user_steps_json["status"] in {"needs_pack_repair", "needs_fresh_pack", "needs_initialization", "needs_real_client_evidence", "needs_author_review", "ready_with_warnings", "ready_to_start"}, "desktop-pack user steps status mismatch")
        assert_true("user-steps.schema.json" in user_steps_json["source_files"], "desktop-pack user steps JSON missed schema source file")
        assert_true(user_steps_schema["title"] == "Long Novel Agent User Steps", "desktop-pack user steps schema title mismatch")
        assert_true("User Steps" in user_steps_md, "desktop-pack user steps markdown missed title")
        assert_true("User Steps" in user_steps_html and "<!doctype html>" in user_steps_html, "desktop-pack user steps HTML missed title or doctype")
        assert_true(user_steps_cli["type"] == "desktop_user_steps", "desktop-user-steps CLI returned wrong type")
        assert_true(user_steps_cli["source"] == "desktop_pack_directory", "desktop-user-steps CLI missed directory source")
        assert_true(user_steps_cli["target_chapter"] == 2, "desktop-user-steps CLI target mismatch")
        assert_true(user_steps_cli["step_count"] == 5, "desktop-user-steps CLI step count mismatch")
        assert_true(user_steps_cli["status"] == user_steps_json["status"], "desktop-user-steps CLI status mismatch")
        assert_true(user_steps_file_before == user_steps_file_after, "desktop-user-steps CLI should not rewrite generated JSON")
        assert_true("User Steps" in user_steps_markdown.stdout, "desktop-user-steps CLI markdown missed title")
        assert_true("desktop_user_steps" in user_steps_markdown.stdout, "desktop-user-steps CLI markdown missed command key")
        assert_true("User Steps" in user_steps_html_cli.stdout and "<!doctype html>" in user_steps_html_cli.stdout, "desktop-user-steps CLI HTML missed title or doctype")
        assert_true(desktop_pack_readiness_json["type"] == "desktop_pack_readiness", "desktop-pack readiness JSON wrong type")
        assert_true(desktop_pack_readiness_json["$schema"] == "desktop-pack-readiness.schema.json", "desktop-pack readiness JSON missed local schema")
        assert_true(desktop_pack_readiness_json["target_chapter"] == 2, "desktop-pack readiness target mismatch")
        assert_true(desktop_pack_readiness_json["local_only"] is True, "desktop-pack readiness should be local only")
        assert_true(desktop_pack_readiness_json["requires_server"] is False, "desktop-pack readiness should not require server")
        assert_true(desktop_pack_readiness_json["would_write_project_state"] is False, "desktop-pack readiness should be read-only")
        assert_true(desktop_pack_readiness_json["would_write_install_config"] is False, "desktop-pack readiness should not write install config")
        assert_true(desktop_pack_readiness_json["would_write_pack_files"] is False, "desktop-pack readiness should not write pack files")
        assert_true(desktop_pack_readiness_json["records_real_client_evidence"] is False, "desktop-pack readiness should not record evidence")
        assert_true(desktop_pack_readiness_json["status"] in {"ready_to_share", "ready_with_warnings", "needs_initialization", "needs_author_review", "needs_real_client_evidence", "needs_pack_repair", "blocked"}, "desktop-pack readiness status mismatch")
        assert_true(not desktop_pack_readiness_json["missing_files"], "desktop-pack readiness should not report missing files")
        assert_true(not desktop_pack_readiness_json["missing_commands"], "desktop-pack readiness should not report missing commands")
        assert_true(desktop_pack_readiness_json["summary"]["pack_freshness_status"] == "fresh", "desktop-pack readiness should record fresh pack state")
        assert_true(desktop_pack_readiness_json["summary"]["chapter_session_freshness_status"] == "fresh", "desktop-pack readiness should record fresh chapter session state")
        readiness_check_ids = {item.get("id") for item in desktop_pack_readiness_json["checks"] if isinstance(item, dict)}
        assert_true("chapter_session_freshness" in readiness_check_ids, "desktop-pack readiness should include chapter session freshness check")
        assert_true(desktop_pack_readiness_cli["type"] == "desktop_pack_readiness", "desktop-pack readiness CLI returned wrong type")
        assert_true(desktop_pack_readiness_cli["source"] == "desktop_pack_directory", "desktop-pack readiness CLI missed directory source")
        assert_true(desktop_pack_readiness_cli["target_chapter"] == 2, "desktop-pack readiness CLI target mismatch")
        assert_true(desktop_pack_readiness_cli["summary"]["command_count"] == commands_json["command_count"], "desktop-pack readiness CLI command count mismatch")
        assert_true(desktop_pack_readiness_cli["summary"]["pack_freshness_status"] == "fresh", "desktop-pack readiness CLI missed fresh state")
        assert_true(desktop_pack_readiness_cli["summary"]["chapter_session_freshness_status"] == "fresh", "desktop-pack readiness CLI missed fresh chapter session state")
        assert_true(not desktop_pack_readiness_cli["missing_files"], "desktop-pack readiness CLI should not report missing files")
        assert_true(not desktop_pack_readiness_cli["missing_commands"], "desktop-pack readiness CLI should not report missing commands")
        assert_true(readiness_file_before == readiness_file_after, "desktop-pack readiness CLI should not rewrite generated readiness JSON")
        assert_true("Desktop Pack Readiness" in desktop_pack_readiness_markdown.stdout, "desktop-pack readiness CLI markdown missed title")
        assert_true("desktop-pack-readiness" in desktop_pack_readiness_markdown.stdout, "desktop-pack readiness CLI markdown missed refresh command")
        assert_true(desktop_pack_readiness_schema["title"] == "Long Novel Agent Desktop Pack Readiness", "desktop-pack readiness schema title mismatch")
        assert_true("Desktop Pack Readiness" in desktop_pack_readiness, "desktop-pack readiness markdown missed title")
        assert_true("real desktop-client evidence" in desktop_pack_readiness, "desktop-pack readiness markdown missed real evidence boundary")
        assert_true(pack_schema_check_json["type"] == "desktop_pack_schema_check", "desktop-pack schema check JSON wrong type")
        assert_true(pack_schema_check_json["ok"] is True, "desktop-pack schema check JSON should pass generated pack")
        assert_true(pack_schema_check_json["status"] == "ready", "desktop-pack schema check JSON should be ready")
        assert_true(pack_schema_check_json["local_only"] is True, "desktop-pack schema check should be local only")
        assert_true(pack_schema_check_json["would_write_project_state"] is False, "desktop-pack schema check should not write project state")
        assert_true(pack_schema_check_json["would_write_install_config"] is False, "desktop-pack schema check should not write install config")
        assert_true(pack_schema_check_json["would_write_pack_files"] is False, "desktop-pack schema check should not write pack files")
        assert_true(pack_schema_check_json["records_real_client_evidence"] is False, "desktop-pack schema check should not record desktop evidence")
        assert_true(pack_schema_check_json["summary"]["target_count"] >= 20, "desktop-pack schema check should validate generated JSON targets")
        assert_true(pack_schema_check_json["summary"]["failed"] == 0, "desktop-pack schema check should not report failed targets")
        assert_true(pack_schema_check_json["summary"]["schema_issue_count"] == 0, "desktop-pack schema check should not report schema issues")
        assert_true(pack_schema_check_json["summary"]["reference_issue_count"] == 0, "desktop-pack schema check should not report reference issues")
        assert_true(pack_schema_check_json["summary"]["issue_count"] == 0, "desktop-pack schema check should not report issues")
        assert_true(pack_schema_check_json["cross_reference"]["ok"] is True, "desktop-pack schema check cross-reference check should pass")
        assert_true(pack_schema_check_json["cross_reference"]["summary"]["checked_file_refs"] >= 100, "desktop-pack schema check should inspect pack-index file references")
        assert_true(pack_schema_check_json["cross_reference"]["summary"]["checked_command_refs"] >= 30, "desktop-pack schema check should inspect important command references")
        assert_true(pack_schema_check_json["cross_reference"]["summary"]["command_count"] == commands_json["command_count"], "desktop-pack schema check command cross-reference count mismatch")
        assert_true({"commands.json", "local-summary.json", "starter-brief.json", "chapter-session.json"} <= {item.get("json_file") for item in pack_schema_check_json["targets"] if isinstance(item, dict)}, "desktop-pack schema check missed key JSON targets")
        assert_true(pack_schema_check_cli["type"] == "desktop_pack_schema_check", "desktop-pack schema check CLI returned wrong type")
        assert_true(pack_schema_check_cli["ok"] is True, "desktop-pack schema check CLI should pass generated pack")
        assert_true(pack_schema_check_cli["summary"]["target_count"] == pack_schema_check_json["summary"]["target_count"], "desktop-pack schema check CLI target count mismatch")
        assert_true(pack_schema_check_cli["summary"]["reference_issue_count"] == 0, "desktop-pack schema check CLI should not report reference issues")
        assert_true(schema_check_file_before == schema_check_file_after, "desktop-pack schema check CLI should not rewrite generated JSON")
        assert_true("Desktop Pack Schema Check" in pack_schema_check_markdown.stdout, "desktop-pack schema check CLI markdown missed title")
        assert_true("Cross References" in pack_schema_check_markdown.stdout, "desktop-pack schema check CLI markdown missed cross-reference section")
        assert_true("Desktop Pack Schema Check" in pack_schema_check_md, "desktop-pack schema check markdown missed title")
        assert_true("Cross References" in pack_schema_check_md, "desktop-pack schema check markdown missed cross-reference section")
        assert_true(pack_freshness_cli["type"] == "desktop_pack_freshness", "desktop-pack freshness CLI returned wrong type")
        assert_true(pack_freshness_cli["ok"] is True, "desktop-pack freshness CLI should pass generated pack")
        assert_true(pack_freshness_cli["status"] == "fresh", "desktop-pack freshness CLI should report fresh generated pack")
        assert_true(pack_freshness_cli["source"] == "desktop_pack_directory", "desktop-pack freshness CLI missed directory source")
        assert_true(pack_freshness_cli["comparison"]["ok"] is True, "desktop-pack freshness comparison should pass")
        assert_true(pack_freshness_cli["would_write_project_state"] is False, "desktop-pack freshness should not write project state")
        assert_true(pack_freshness_cli["would_write_pack_files"] is False, "desktop-pack freshness should not write pack files")
        assert_true("Desktop Pack Freshness" in pack_freshness_markdown.stdout, "desktop-pack freshness CLI markdown missed title")
        assert_true(chapter_session_freshness_cli["type"] == "chapter_session_freshness", "chapter session freshness CLI returned wrong type")
        assert_true(chapter_session_freshness_cli["ok"] is True, "chapter session freshness CLI should pass generated pack")
        assert_true(chapter_session_freshness_cli["status"] == "fresh", "chapter session freshness CLI should report fresh generated session")
        assert_true(chapter_session_freshness_cli["target_chapter"] == 2, "chapter session freshness CLI target mismatch")
        assert_true(chapter_session_freshness_cli["source"] == "desktop_pack_directory", "chapter session freshness CLI missed directory source")
        assert_true(chapter_session_freshness_cli["comparison"]["ok"] is True, "chapter session freshness comparison should pass")
        assert_true(chapter_session_freshness_cli["would_write_project_state"] is False, "chapter session freshness should not write project state")
        assert_true(chapter_session_freshness_cli["would_write_pack_files"] is False, "chapter session freshness should not write pack files")
        assert_true("Chapter Session Freshness" in chapter_session_freshness_markdown.stdout, "chapter session freshness CLI markdown missed title")
        assert_true(starter_brief_json["type"] == "starter_brief", "desktop-pack starter brief JSON wrong type")
        assert_true(starter_brief_json["$schema"] == "starter-brief.schema.json", "desktop-pack starter brief JSON missed local schema")
        assert_true(starter_brief_json["target_chapter"] == 2, "desktop-pack starter brief target mismatch")
        assert_true(starter_brief_json["local_only"] is True, "desktop-pack starter brief should be local only")
        assert_true(starter_brief_json["requires_server"] is False, "desktop-pack starter brief should not require server")
        assert_true(starter_brief_json["would_write_project_state"] is False, "desktop-pack starter brief should be read-only")
        assert_true(starter_brief_json["would_write_install_config"] is False, "desktop-pack starter brief should not write install config")
        assert_true(starter_brief_json["would_write_pack_files"] is False, "desktop-pack starter brief should not write pack files")
        assert_true(starter_brief_json["records_real_client_evidence"] is False, "desktop-pack starter brief should not record evidence")
        assert_true("agent-startup-prompt.json" in [item["file"] for item in starter_brief_json["first_files"]], "desktop-pack starter brief missed startup prompt first file")
        assert_true("metadata.json" in [item["file"] for item in starter_brief_json["first_files"]], "desktop-pack starter brief missed metadata first file")
        assert_true("chapter-session.json" in starter_brief_json["read_before_writing"], "desktop-pack starter brief missed chapter session before-writing file")
        assert_true("metadata.json" in starter_brief_json["read_before_writing"], "desktop-pack starter brief missed metadata before-writing file")
        assert_true(starter_brief_json["summary"].get("pack_freshness_status") == "fresh", "desktop-pack starter brief missed pack freshness summary")
        assert_true(starter_brief_json["summary"].get("chapter_session_freshness_status") == "fresh", "desktop-pack starter brief missed chapter session freshness summary")
        assert_true(starter_brief_json["commands"]["starter_brief"]["present"] is True, "desktop-pack starter brief missed self-refresh command")
        assert_true(starter_brief_json["commands"]["pack_freshness"]["present"] is True, "desktop-pack starter brief missed pack freshness command")
        assert_true(starter_brief_json["commands"]["chapter_session_freshness"]["present"] is True, "desktop-pack starter brief missed chapter session freshness command")
        assert_true("record_agent_activity" in starter_brief_json["write_boundaries"]["read_only_agents_must_not_execute"], "desktop-pack starter brief missed read-only activity write boundary")
        assert_true("starter-brief.schema.json" in starter_brief_json["source_files"], "desktop-pack starter brief missed schema source file")
        assert_true(starter_brief_schema["title"] == "Long Novel Agent Starter Brief", "desktop-pack starter brief schema title mismatch")
        assert_true(starter_brief_cli["type"] == "starter_brief", "starter-brief CLI returned wrong type")
        assert_true(starter_brief_cli["$schema"] == "starter-brief.schema.json", "starter-brief CLI missed local schema")
        assert_true(starter_brief_cli["source"] == "desktop_pack_directory", "starter-brief CLI missed directory source")
        assert_true(starter_brief_cli["target_chapter"] == 2, "starter-brief CLI target mismatch")
        assert_true(starter_brief_cli["status"] == starter_brief_json["status"], "starter-brief CLI status mismatch")
        assert_true(starter_brief_cli["would_write_project_state"] is False, "starter-brief CLI should be read-only")
        assert_true(starter_brief_cli["would_write_pack_files"] is False, "starter-brief CLI should not write pack files")
        assert_true(starter_brief_file_before == starter_brief_file_after, "starter-brief CLI should not rewrite generated JSON")
        assert_true("Starter Brief" in starter_brief, "desktop-pack starter brief markdown missed title")
        assert_true("Starter Brief" in starter_brief_cli_markdown.stdout, "starter-brief CLI markdown missed title")
        assert_true("Read Before Writing" in starter_brief_cli_markdown.stdout, "starter-brief CLI markdown missed before-writing section")
        assert_true(agent_startup_prompt_json["type"] == "agent_startup_prompt", "desktop-pack agent startup prompt JSON wrong type")
        assert_true(agent_startup_prompt_json["$schema"] == "agent-startup-prompt.schema.json", "desktop-pack agent startup prompt JSON missed local schema")
        assert_true(agent_startup_prompt_json["target_chapter"] == 2, "desktop-pack agent startup prompt target mismatch")
        assert_true(agent_startup_prompt_json["local_only"] is True, "desktop-pack agent startup prompt should be local only")
        assert_true(agent_startup_prompt_json["requires_server"] is False, "desktop-pack agent startup prompt should not require server")
        assert_true(agent_startup_prompt_json["would_write_project_state"] is False, "desktop-pack agent startup prompt should be read-only")
        assert_true(agent_startup_prompt_json["would_write_install_config"] is False, "desktop-pack agent startup prompt should not write install config")
        assert_true(agent_startup_prompt_json["would_write_pack_files"] is False, "desktop-pack agent startup prompt should not write pack files")
        assert_true(agent_startup_prompt_json["records_real_client_evidence"] is False, "desktop-pack agent startup prompt should not record evidence")
        assert_true("build_agent_startup_prompt" in agent_startup_prompt_json["copy_prompt"], "desktop-pack agent startup prompt missed MCP startup command")
        assert_true("pack_freshness" in agent_startup_prompt_json["copy_prompt"], "desktop-pack agent startup prompt missed freshness instruction")
        assert_true("chapter_session_freshness" in agent_startup_prompt_json["copy_prompt"], "desktop-pack agent startup prompt missed chapter session freshness instruction")
        assert_true("metadata.json" in [item["file"] for item in agent_startup_prompt_json["read_first"]], "desktop-pack agent startup prompt missed metadata read-first")
        assert_true("chapter-session.json" in [item["file"] for item in agent_startup_prompt_json["read_first"]], "desktop-pack agent startup prompt missed chapter session read-first")
        assert_true("starter-brief.json" in [item["file"] for item in agent_startup_prompt_json["read_first"]], "desktop-pack agent startup prompt missed starter brief read-first")
        assert_true(agent_startup_prompt_json["summary"].get("pack_freshness_status") == "fresh", "desktop-pack agent startup prompt missed pack freshness summary")
        assert_true(agent_startup_prompt_json["summary"].get("chapter_session_freshness_status") == "fresh", "desktop-pack agent startup prompt missed chapter session freshness summary")
        assert_true(agent_startup_prompt_json["commands"]["agent_startup_prompt"]["present"] is True, "desktop-pack agent startup prompt missed its command")
        assert_true(agent_startup_prompt_json["commands"]["pack_freshness"]["present"] is True, "desktop-pack agent startup prompt missed pack freshness command")
        assert_true(agent_startup_prompt_json["commands"]["chapter_session_freshness"]["present"] is True, "desktop-pack agent startup prompt missed chapter session freshness command")
        assert_true(agent_startup_prompt_json["commands"]["context_brief"]["present"] is True, "desktop-pack agent startup prompt missed context brief command")
        assert_true("record_agent_activity" in agent_startup_prompt_json["write_boundaries"]["read_only_agents_must_not_execute"], "desktop-pack agent startup prompt missed read-only activity write boundary")
        assert_true("agent-startup-prompt.md" in agent_startup_prompt_json["source_files"], "desktop-pack agent startup prompt missed markdown source file")
        assert_true("agent-startup-prompt.schema.json" in agent_startup_prompt_json["source_files"], "desktop-pack agent startup prompt missed schema source file")
        assert_true(agent_startup_prompt_schema["title"] == "Long Novel Agent Startup Prompt", "desktop-pack agent startup prompt schema title mismatch")
        assert_true("Agent Startup Prompt" in agent_startup_prompt, "desktop-pack agent startup prompt markdown missed title")
        assert_true("Copy Prompt" in agent_startup_prompt, "desktop-pack agent startup prompt markdown missed copy prompt")
        assert_true("Tool Gates" in agent_startup_prompt, "desktop-pack agent startup prompt markdown missed tool gates")
        assert_true(agent_startup_prompt_cli["type"] == "agent_startup_prompt", "agent-startup-prompt CLI returned wrong type")
        assert_true(agent_startup_prompt_cli["source"] == "desktop_pack_directory", "agent-startup-prompt CLI missed directory source")
        assert_true(agent_startup_prompt_cli["target_chapter"] == 2, "agent-startup-prompt CLI target mismatch")
        assert_true(agent_startup_prompt_cli["status"] == agent_startup_prompt_json["status"], "agent-startup-prompt CLI status mismatch")
        assert_true(agent_startup_prompt_cli["would_write_project_state"] is False, "agent-startup-prompt CLI should be read-only")
        assert_true(agent_startup_prompt_file_before == agent_startup_prompt_file_after, "agent-startup-prompt CLI should not rewrite generated JSON")
        assert_true("Agent Startup Prompt" in agent_startup_prompt_cli_markdown.stdout, "agent-startup-prompt CLI markdown missed title")
        assert_true("Copy Prompt" in agent_startup_prompt_cli_markdown.stdout, "agent-startup-prompt CLI markdown missed copy prompt")
        assert_true(agent_takeover_cli["type"] == "agent_takeover", "agent-takeover CLI returned wrong type")
        assert_true(agent_takeover_cli["source"] == "desktop_pack_directory", "agent-takeover CLI missed directory source")
        assert_true(agent_takeover_cli["target_chapter"] == 2, "agent-takeover CLI target mismatch")
        assert_true(agent_takeover_cli["status"] == agent_takeover_json["status"], "agent-takeover CLI status mismatch")
        assert_true(agent_takeover_cli["would_write_project_state"] is False, "agent-takeover CLI should be read-only")
        assert_true(agent_takeover_cli["would_write_pack_files"] is False, "agent-takeover CLI should not write pack files")
        assert_true(agent_takeover_cli["commands"]["agent_takeover"]["present"] is True, "agent-takeover CLI missed self-refresh command")
        assert_true(agent_takeover_file_before == agent_takeover_file_after, "agent-takeover CLI should not rewrite generated JSON")
        assert_true("Agent Takeover" in agent_takeover_cli_markdown.stdout, "agent-takeover CLI markdown missed title")
        assert_true("Write Boundaries" in agent_takeover_cli_markdown.stdout, "agent-takeover CLI markdown missed write boundaries")
        assert_true(troubleshooting_cli["type"] == "desktop_pack_troubleshooting", "troubleshooting CLI returned wrong type")
        assert_true(troubleshooting_cli["source"] == "desktop_pack_directory", "troubleshooting CLI missed directory source")
        assert_true(troubleshooting_cli["target_chapter"] == 2, "troubleshooting CLI target mismatch")
        assert_true(troubleshooting_cli["would_write_project_state"] is False, "troubleshooting CLI should be read-only")
        assert_true(troubleshooting_cli["would_write_pack_files"] is False, "troubleshooting CLI should not write pack files")
        assert_true(len(troubleshooting_cli["symptoms"]) == len(troubleshooting_json["symptoms"]), "troubleshooting CLI symptom count mismatch")
        assert_true(any(item.get("command", {}).get("key") == "desktop_troubleshooting" for item in troubleshooting_cli["first_checks"]), "troubleshooting CLI missed self-refresh command reference")
        assert_true(troubleshooting_file_before == troubleshooting_file_after, "troubleshooting CLI should not rewrite generated troubleshooting JSON")
        assert_true("Local Desktop Troubleshooting" in troubleshooting_cli_markdown.stdout, "troubleshooting CLI markdown missed title")
        assert_true("Write Boundaries" in troubleshooting_cli_markdown.stdout, "troubleshooting CLI markdown missed write boundaries")
        assert_true(agent_takeover_json["type"] == "agent_takeover", "desktop-pack agent takeover JSON wrong type")
        assert_true(agent_takeover_json["$schema"] == "agent-takeover.schema.json", "desktop-pack agent takeover JSON missed local schema")
        assert_true(agent_takeover_json["target_chapter"] == 2, "desktop-pack agent takeover target mismatch")
        assert_true(agent_takeover_json["local_only"] is True, "desktop-pack agent takeover should be local only")
        assert_true(agent_takeover_json["requires_server"] is False, "desktop-pack agent takeover should not require server")
        assert_true(agent_takeover_json["would_write_project_state"] is False, "desktop-pack agent takeover should be read-only")
        assert_true(agent_takeover_json["can_write_durable_state"] is False, "desktop-pack agent takeover should not authorize durable writes")
        assert_true("metadata.json" in [item["file"] for item in agent_takeover_json["first_read"]], "desktop-pack agent takeover missed metadata first-read")
        assert_true("chapter-session.json" in [item["file"] for item in agent_takeover_json["first_read"]], "desktop-pack agent takeover missed chapter session first-read")
        assert_true("starter-brief.json" in [item["file"] for item in agent_takeover_json["first_read"]], "desktop-pack agent takeover missed starter brief first-read")
        assert_true(agent_takeover_json["summary"].get("pack_freshness_status") == "fresh", "desktop-pack agent takeover missed pack freshness summary")
        assert_true(agent_takeover_json["summary"].get("chapter_session_freshness_status") == "fresh", "desktop-pack agent takeover missed chapter session freshness summary")
        assert_true(agent_takeover_json["draft_gate"]["requires_chapter_context"] is True, "desktop-pack agent takeover missed chapter context gate")
        assert_true("record_agent_activity" in agent_takeover_json["write_boundaries"]["read_only_agents_must_not_execute"], "desktop-pack agent takeover missed read-only activity write boundary")
        assert_true(agent_takeover_json["commands"]["agent_takeover"]["present"] is True, "desktop-pack agent takeover missed its refresh command")
        assert_true(agent_takeover_json["commands"]["pack_freshness"]["present"] is True, "desktop-pack agent takeover missed pack freshness command")
        assert_true(agent_takeover_json["commands"]["chapter_session_freshness"]["present"] is True, "desktop-pack agent takeover missed chapter session freshness command")
        assert_true(agent_takeover_json["commands"]["prepare_session"]["present"] is True, "desktop-pack agent takeover missed prepare session command")
        assert_true(agent_takeover_json["commands"]["handoff_integrity"]["present"] is True, "desktop-pack agent takeover missed handoff integrity command")
        assert_true("agent-takeover.md" in agent_takeover_json["source_files"], "desktop-pack agent takeover missed markdown source file")
        assert_true("agent-takeover.schema.json" in agent_takeover_json["source_files"], "desktop-pack agent takeover missed schema source file")
        assert_true(agent_takeover_schema["title"] == "Long Novel Agent Takeover Packet", "desktop-pack agent takeover schema title mismatch")
        assert_true("Agent Takeover" in agent_takeover, "desktop-pack agent takeover markdown missed title")
        assert_true("Draft Gate" in agent_takeover, "desktop-pack agent takeover markdown missed draft gate")
        assert_true("Write Boundaries" in agent_takeover, "desktop-pack agent takeover markdown missed write boundaries")
        assert_true(author_actions_json["type"] == "author_actions", "desktop-pack author actions JSON wrong type")
        assert_true(author_actions_json["$schema"] == "author-actions.schema.json", "desktop-pack author actions JSON missed local schema")
        assert_true(author_actions_json["target_chapter"] == 2, "desktop-pack author actions target mismatch")
        assert_true(author_actions_json["local_only"] is True, "desktop-pack author actions should be local only")
        assert_true(author_actions_json["requires_server"] is False, "desktop-pack author actions should not require server")
        assert_true(author_actions_json["would_write_project_state"] is False, "desktop-pack author actions should be read-only")
        assert_true({"start_here", "author_review", "draft_review", "desktop_evidence", "writer_mode"} <= {item["id"] for item in author_actions_json["action_groups"]}, "desktop-pack author actions missed action groups")
        assert_true("record_desktop_check" in author_actions_json["write_boundaries"]["read_only_agents_must_not_execute"], "desktop-pack author actions missed read-only evidence boundary")
        assert_true("author-actions.md" in author_actions_json["source_files"], "desktop-pack author actions missed markdown source file")
        assert_true("author-actions.schema.json" in author_actions_json["source_files"], "desktop-pack author actions missed schema source file")
        assert_true(author_actions_schema["title"] == "Long Novel Agent Author Actions", "desktop-pack author actions schema title mismatch")
        assert_true("Author Actions" in author_actions, "desktop-pack author actions markdown missed title")
        assert_true("Action Groups" in author_actions, "desktop-pack author actions markdown missed action groups")
        assert_true("Write Boundaries" in author_actions, "desktop-pack author actions markdown missed write boundaries")
        assert_true(author_actions_cli["type"] == "author_actions", "author-actions CLI returned wrong type")
        assert_true(author_actions_cli["source"] == "desktop_pack_directory", "author-actions CLI missed directory source")
        assert_true(author_actions_cli["target_chapter"] == 2, "author-actions CLI target mismatch")
        assert_true(author_actions_cli["status"] == author_actions_json["status"], "author-actions CLI status mismatch")
        assert_true({"start_here", "author_review", "draft_review", "desktop_evidence", "writer_mode"} <= {item["id"] for item in author_actions_cli["action_groups"]}, "author-actions CLI missed action groups")
        assert_true(any(command.get("key") == "author_actions" and command.get("present") is True for group in author_actions_cli["action_groups"] for command in group.get("commands", [])), "author-actions CLI should reference author_actions command")
        assert_true(author_actions_file_before == author_actions_file_after, "author-actions CLI should not rewrite generated author-actions JSON")
        assert_true("Author Actions" in author_actions_cli_markdown.stdout, "author-actions CLI markdown missed title")
        assert_true("Action Groups" in author_actions_cli_markdown.stdout, "author-actions CLI markdown missed action groups")
        assert_true(acceptance_review_json["type"] == "acceptance_review", "desktop-pack acceptance review JSON wrong type")
        assert_true(acceptance_review_json["$schema"] == "acceptance-review.schema.json", "desktop-pack acceptance review JSON missed local schema")
        assert_true(acceptance_review_json["target_chapter"] == 2, "desktop-pack acceptance review target mismatch")
        assert_true(acceptance_review_json["local_only"] is True, "desktop-pack acceptance review should be local only")
        assert_true(acceptance_review_json["requires_server"] is False, "desktop-pack acceptance review should not require server")
        assert_true(acceptance_review_json["would_write_project_state"] is False, "desktop-pack acceptance review should be read-only")
        assert_true(acceptance_review_json["would_write_install_config"] is False, "desktop-pack acceptance review should not write install config")
        assert_true(acceptance_review_json["would_write_pack_files"] is False, "desktop-pack acceptance review should not write pack files")
        assert_true(acceptance_review_json["records_real_client_evidence"] is False, "desktop-pack acceptance review should not record real client evidence")
        assert_true(acceptance_review_json["status"] in {"needs_pack_repair", "needs_project_initialization", "needs_revision", "needs_author_review", "ready_for_author_review"}, "desktop-pack acceptance review status mismatch")
        acceptance_path_ids = {item.get("id") for item in acceptance_review_json.get("acceptance_paths", []) if isinstance(item, dict)}
        assert_true({"single_chapter_acceptance", "chapter_range_acceptance"} <= acceptance_path_ids, "desktop-pack acceptance review missed acceptance paths")
        acceptance_boundaries = acceptance_review_json.get("write_boundaries") if isinstance(acceptance_review_json.get("write_boundaries"), dict) else {}
        assert_true(acceptance_boundaries.get("acceptance_review_is_read_only") is True, "desktop-pack acceptance review missed read-only boundary")
        assert_true(acceptance_boundaries.get("writer_commands_need_explicit_author_confirmation") is True, "desktop-pack acceptance review missed author confirmation boundary")
        acceptance_guard = acceptance_review_json.get("writer_guard") if isinstance(acceptance_review_json.get("writer_guard"), dict) else {}
        assert_true(len(acceptance_guard.get("must_run_before_durable_writes", [])) >= 3, "desktop-pack acceptance review missed writer guard checks")
        assert_true("acceptance-review.md" in acceptance_review_json["source_files"], "desktop-pack acceptance review missed markdown source file")
        assert_true("acceptance-review.schema.json" in acceptance_review_json["source_files"], "desktop-pack acceptance review missed schema source file")
        assert_true(acceptance_review_schema["title"] == "Long Novel Agent Acceptance Review", "desktop-pack acceptance review schema title mismatch")
        assert_true("Acceptance Review" in acceptance_review_md, "desktop-pack acceptance review markdown missed title")
        assert_true("Acceptance Paths" in acceptance_review_md, "desktop-pack acceptance review markdown missed acceptance paths")
        assert_true("Writer Guard" in acceptance_review_md, "desktop-pack acceptance review markdown missed writer guard")
        assert_true("Long Novel Agent Acceptance Review" in acceptance_review_html, "desktop-pack acceptance review HTML missed title")
        assert_true("Raw Packet" in acceptance_review_html, "desktop-pack acceptance review HTML missed raw packet")
        assert_true(acceptance_review_cli["type"] == "acceptance_review", "acceptance-review CLI returned wrong type")
        assert_true(acceptance_review_cli["source"] == "desktop_pack_directory", "acceptance-review CLI missed directory source")
        assert_true(acceptance_review_cli["target_chapter"] == 2, "acceptance-review CLI target mismatch")
        assert_true(acceptance_review_cli["status"] == acceptance_review_json["status"], "acceptance-review CLI status mismatch")
        assert_true(acceptance_review_file_before == acceptance_review_file_after, "acceptance-review CLI should not rewrite generated JSON")
        assert_true("Acceptance Review" in acceptance_review_cli_markdown.stdout, "acceptance-review CLI markdown missed title")
        assert_true("Acceptance Paths" in acceptance_review_cli_markdown.stdout, "acceptance-review CLI markdown missed acceptance paths")
        assert_true("<!doctype html>" in acceptance_review_cli_html.stdout, "acceptance-review CLI HTML missed doctype")
        assert_true("Long Novel Agent Acceptance Review" in acceptance_review_cli_html.stdout, "acceptance-review CLI HTML missed title")
        assert_true(desktop_next_step_schema["title"] == "Long Novel Agent Desktop Next Step", "desktop-pack desktop next step schema title mismatch")
        assert_true(desktop_next_step_json["$schema"] == "desktop-next-step.schema.json", "desktop-pack desktop next step JSON missed local schema")
        assert_true(desktop_next_step_json["type"] == "desktop_next_step", "desktop-pack desktop next step JSON wrong type")
        assert_true(desktop_next_step_json["tool_profile"] == "full", "desktop-pack desktop next step JSON missed default tool profile")
        assert_true(desktop_next_step_json["target_chapter"] == 2, "desktop-pack desktop next step JSON target mismatch")
        assert_true(desktop_next_step_json["would_write_project_state"] is False, "desktop-pack desktop next step JSON should be read-only")
        assert_true(desktop_next_step_json["would_write_install_config"] is False, "desktop-pack desktop next step JSON should not write install config")
        assert_true(desktop_next_step_json["would_write_pack_files"] is False, "desktop-pack desktop next step JSON should not write pack files")
        assert_true(desktop_next_step_json["records_real_client_evidence"] is False, "desktop-pack desktop next step JSON should not record real client evidence")
        assert_true(desktop_next_step_json["primary_action"]["id"], "desktop-pack desktop next step JSON missed primary action")
        assert_true("Long Novel Agent Desktop Next Step" in desktop_next_step_md, "desktop-pack desktop next step markdown missed title")
        assert_true(desktop_next_step_json["primary_action"]["id"] in desktop_next_step_md, "desktop-pack desktop next step markdown missed primary action id")
        assert_true("MCP tool profile: full" in desktop_next_step_md, "desktop-pack desktop next step markdown missed tool profile")
        assert_true("MCP tool profile: full" in readme, "desktop-pack README missed tool profile")
        assert_true("Tool profile detail" in readme, "desktop-pack README missed tool profile detail")
        assert_true("--tool-profile core" in readme and "--tool-profile writing" in readme, "desktop-pack README missed tool profile choice guidance")
        assert_true("next-step.md" in readme, "desktop-pack README missed next-step file")
        assert_true("quick-install.md" in readme, "desktop-pack README missed quick install file")
        assert_true("install-local.md" in readme, "desktop-pack README missed local install file")
        assert_true("install-local.sh" in readme, "desktop-pack README missed local install script")
        assert_true("install-local.ps1" in readme, "desktop-pack README missed PowerShell install wrapper")
        assert_true("install-local.cmd" in readme, "desktop-pack README missed Windows install wrapper")
        assert_true("install-local.command" in readme, "desktop-pack README missed macOS install launcher")
        assert_true("upgrade-local.md" in readme, "desktop-pack README missed local upgrade file")
        assert_true("upgrade-local.sh" in readme, "desktop-pack README missed local upgrade script")
        assert_true("upgrade-local.ps1" in readme, "desktop-pack README missed PowerShell upgrade wrapper")
        assert_true("upgrade-local.cmd" in readme, "desktop-pack README missed Windows upgrade wrapper")
        assert_true("upgrade-local.command" in readme, "desktop-pack README missed macOS upgrade launcher")
        assert_true("uninstall-local.md" in readme, "desktop-pack README missed local uninstall file")
        assert_true("uninstall-local.sh" in readme, "desktop-pack README missed local uninstall script")
        assert_true("uninstall-local.ps1" in readme, "desktop-pack README missed PowerShell uninstall wrapper")
        assert_true("uninstall-local.cmd" in readme, "desktop-pack README missed Windows uninstall wrapper")
        assert_true("uninstall-local.command" in readme, "desktop-pack README missed macOS uninstall launcher")
        assert_true("local-checks.md" in readme, "desktop-pack README missed local checks file")
        assert_true("local-checks.sh" in readme, "desktop-pack README missed local checks script")
        assert_true("local-checks.ps1" in readme, "desktop-pack README missed PowerShell checks wrapper")
        assert_true("local-checks.cmd" in readme, "desktop-pack README missed Windows checks wrapper")
        assert_true("local-checks.command" in readme, "desktop-pack README missed macOS checks launcher")
        assert_true("archive-local.md" in readme, "desktop-pack README missed local archive file")
        assert_true("archive-local.sh" in readme, "desktop-pack README missed local archive script")
        assert_true("archive-local.ps1" in readme, "desktop-pack README missed PowerShell archive wrapper")
        assert_true("archive-local.cmd" in readme, "desktop-pack README missed Windows archive wrapper")
        assert_true("archive-local.command" in readme, "desktop-pack README missed macOS archive launcher")
        assert_true("handoff-brief.md" in readme, "desktop-pack README missed handoff brief file")
        assert_true(handoff_brief_json.get("type") == "handoff_brief", "desktop-pack handoff brief JSON returned wrong type")
        assert_true(handoff_brief_json.get("read_only") is True, "desktop-pack handoff brief should be read-only")
        assert_true("Copy Prompt" in handoff_brief, "desktop-pack handoff brief markdown missed copy prompt")
        assert_true("Write Boundaries" in handoff_brief, "desktop-pack handoff brief markdown missed write boundaries")
        assert_true("writer-mode-approval.md" in readme, "desktop-pack README missed writer approval file")
        assert_true("writer-mode-approval.json" in readme, "desktop-pack README missed writer approval JSON file")
        assert_true("writer-mode-approval.schema.json" in readme, "desktop-pack README missed writer approval schema file")
        assert_true("client-compatibility.md" in readme, "desktop-pack README missed client compatibility file")
        assert_true("client-compatibility.json" in readme, "desktop-pack README missed client compatibility JSON file")
        assert_true("pack-manifest.json" in readme, "desktop-pack README missed pack manifest file")
        assert_true("verify-pack.py" in readme, "desktop-pack README missed verify pack script")
        assert_true("pack-doctor.md" in readme, "desktop-pack README missed pack doctor file")
        assert_true("pack-doctor.json" in readme, "desktop-pack README missed pack doctor JSON file")
        assert_true("local-summary.md" in readme and "local-summary.json" in readme and "local-summary.html" in readme and "local-summary.schema.json" in readme, "desktop-pack README missed local summary files")
        assert_true("user-steps.schema.json" in readme, "desktop-pack README missed user steps schema file")
        assert_true("pack_doctor" in readme, "desktop-pack README missed pack doctor command")
        assert_true("pack_doctor_json" in readme, "desktop-pack README missed pack doctor JSON command")
        assert_true("desktop_results_doctor" in readme and "desktop-results-doctor" in readme, "desktop-pack README missed results doctor command")
        assert_true("desktop_evidence_packet" in readme and "desktop-evidence-packet" in readme, "desktop-pack README missed evidence packet command")
        assert_true("desktop_evidence_review" in readme and "desktop-evidence-review" in readme, "desktop-pack README missed evidence review command")
        assert_true("python verify-pack.py --json" in readme, "desktop-pack README missed verify pack command")
        assert_true("verify_pack_cli" in readme, "desktop-pack README missed CLI verify fallback")
        assert_true("desktop_pack_archive" in readme and "archive-pack" in readme, "desktop-pack README missed archive command")
        assert_true("commands-index.md" in readme, "desktop-pack README missed commands index markdown file")
        assert_true("commands-index.json" in readme, "desktop-pack README missed commands index JSON file")
        assert_true("commands-index.schema.json" in readme, "desktop-pack README missed commands index schema file")
        assert_true("commands.json" in readme, "desktop-pack README missed structured commands file")
        assert_true("commands.schema.json" in readme, "desktop-pack README missed commands schema file")
        assert_true("first-three.md" in readme and "first-three.json" in readme and "first-three.html" in readme and "first-three.schema.json" in readme, "desktop-pack README missed first three files")
        assert_true("project-wizard.schema.json" in readme, "desktop-pack README missed project wizard schema file")
        assert_true("source-intake.schema.json" in readme, "desktop-pack README missed source intake schema file")
        assert_true("chapter-session.schema.json" in readme, "desktop-pack README missed chapter session schema file")
        assert_true("handoff-report.schema.json" in readme, "desktop-pack README missed handoff report schema file")
        assert_true("desktop-results.schema.json" in readme, "desktop-pack README missed desktop results schema file")
        assert_true("troubleshooting.md" in readme and "troubleshooting.html" in readme, "desktop-pack README missed troubleshooting files")
        assert_true("continuity-audit.md" in readme, "desktop-pack README missed continuity audit file")
        assert_true("continuity-audit.json" in readme, "desktop-pack README missed continuity audit JSON file")
        assert_true("project-status.json" in readme, "desktop-pack README missed project status JSON file")
        assert_true("author-review-queue.md" in readme, "desktop-pack README missed author review queue file")
        assert_true("chapter-session.md" in readme, "desktop-pack README missed chapter session file")
        assert_true("real-client-walkthrough.md" in readme, "desktop-pack README missed real client walkthrough file")
        assert_true("client-prompts.md" in readme, "desktop-pack README missed client prompts file")
        assert_true("result-json-guide.md" in readme, "desktop-pack README missed result JSON guide file")
        assert_true("evidence-to-results.md" in readme, "desktop-pack README missed evidence-to-results file")
        assert_true("Start Here" in start_here, "desktop-pack START_HERE missing title")
        assert_true("MCP tool profile: full" in start_here, "desktop-pack START_HERE missed tool profile")
        assert_true("Confirm the MCP tool profile before installing" in start_here, "desktop-pack START_HERE missed tool profile install guidance")
        assert_true("--tool-profile core" in start_here and "--tool-profile writing" in start_here, "desktop-pack START_HERE missed smaller tool profile guidance")
        assert_true("pack-index.json" in start_here, "desktop-pack START_HERE missed pack index file")
        assert_true("pack-index.schema.json" in start_here, "desktop-pack START_HERE missed pack index schema file")
        assert_true("first-three.md" in start_here and "first-three.json" in start_here and "first-three.html" in start_here and "first-three.schema.json" in start_here, "desktop-pack START_HERE missed first three files")
        assert_true("local-summary.md" in start_here and "local-summary.json" in start_here and "local-summary.html" in start_here and "local-summary.schema.json" in start_here, "desktop-pack START_HERE missed local summary files")
        assert_true("user-steps.schema.json" in start_here, "desktop-pack START_HERE missed user steps schema file")
        assert_true("next-step.md" in start_here, "desktop-pack START_HERE missed next-step file")
        assert_true("quick-install.md" in start_here, "desktop-pack START_HERE missed quick install file")
        assert_true("install-local.md" in start_here, "desktop-pack START_HERE missed local install file")
        assert_true("install-local.sh" in start_here, "desktop-pack START_HERE missed local install script")
        assert_true("install-local.ps1" in start_here, "desktop-pack START_HERE missed PowerShell install wrapper")
        assert_true("install-local.cmd" in start_here, "desktop-pack START_HERE missed Windows install wrapper")
        assert_true("install-local.command" in start_here, "desktop-pack START_HERE missed macOS install launcher")
        assert_true("upgrade-local.md" in start_here, "desktop-pack START_HERE missed local upgrade file")
        assert_true("upgrade-local.sh" in start_here, "desktop-pack START_HERE missed local upgrade script")
        assert_true("upgrade-local.ps1" in start_here, "desktop-pack START_HERE missed PowerShell upgrade wrapper")
        assert_true("upgrade-local.cmd" in start_here, "desktop-pack START_HERE missed Windows upgrade wrapper")
        assert_true("upgrade-local.command" in start_here, "desktop-pack START_HERE missed macOS upgrade launcher")
        assert_true("uninstall-local.md" in start_here, "desktop-pack START_HERE missed local uninstall file")
        assert_true("uninstall-local.sh" in start_here, "desktop-pack START_HERE missed local uninstall script")
        assert_true("uninstall-local.ps1" in start_here, "desktop-pack START_HERE missed PowerShell uninstall wrapper")
        assert_true("uninstall-local.cmd" in start_here, "desktop-pack START_HERE missed Windows uninstall wrapper")
        assert_true("uninstall-local.command" in start_here, "desktop-pack START_HERE missed macOS uninstall launcher")
        assert_true("local-checks.md" in start_here, "desktop-pack START_HERE missed local checks file")
        assert_true("local-checks.sh" in start_here, "desktop-pack START_HERE missed local checks script")
        assert_true("local-checks.ps1" in start_here, "desktop-pack START_HERE missed PowerShell checks wrapper")
        assert_true("local-checks.cmd" in start_here, "desktop-pack START_HERE missed Windows checks wrapper")
        assert_true("local-checks.command" in start_here, "desktop-pack START_HERE missed macOS checks launcher")
        assert_true("archive-local.md" in start_here, "desktop-pack START_HERE missed local archive file")
        assert_true("archive-local.sh" in start_here, "desktop-pack START_HERE missed local archive script")
        assert_true("archive-local.ps1" in start_here, "desktop-pack START_HERE missed PowerShell archive wrapper")
        assert_true("archive-local.cmd" in start_here, "desktop-pack START_HERE missed Windows archive wrapper")
        assert_true("writer-mode-approval.md" in start_here, "desktop-pack START_HERE missed writer approval file")
        assert_true("writer-mode-approval.json" in start_here, "desktop-pack START_HERE missed writer approval JSON file")
        assert_true("writer-mode-approval.schema.json" in start_here, "desktop-pack START_HERE missed writer approval schema file")
        assert_true("client-compatibility.md" in start_here, "desktop-pack START_HERE missed client compatibility file")
        assert_true("client-compatibility.json" in start_here, "desktop-pack START_HERE missed client compatibility JSON file")
        assert_true("pack-manifest.json" in start_here, "desktop-pack START_HERE missed pack manifest file")
        assert_true("verify-pack.py" in start_here, "desktop-pack START_HERE missed verify pack script")
        assert_true("pack-doctor.md" in start_here, "desktop-pack START_HERE missed pack doctor file")
        assert_true("pack-doctor.json" in start_here, "desktop-pack START_HERE missed pack doctor JSON file")
        assert_true("pack_doctor" in start_here, "desktop-pack START_HERE missed pack doctor command")
        assert_true("pack_doctor_json" in start_here, "desktop-pack START_HERE missed pack doctor JSON command")
        assert_true("desktop_results_doctor" in start_here and "desktop-results-doctor" in start_here, "desktop-pack START_HERE missed results doctor command")
        assert_true("desktop_evidence_packet" in start_here and "desktop-evidence-packet" in start_here, "desktop-pack START_HERE missed evidence packet command")
        assert_true("desktop_evidence_review" in start_here and "desktop-evidence-review" in start_here, "desktop-pack START_HERE missed evidence review command")
        assert_true("desktop_evidence_gaps" in start_here and "desktop-evidence-gaps" in start_here, "desktop-pack START_HERE missed evidence gaps command")
        assert_true("python verify-pack.py --json" in start_here, "desktop-pack START_HERE missed verify pack command")
        assert_true("verify_pack_cli" in start_here, "desktop-pack START_HERE missed CLI verify fallback")
        assert_true("desktop_pack_archive" in start_here, "desktop-pack START_HERE missed archive command")
        assert_true("commands-index.md" in start_here, "desktop-pack START_HERE missed commands index markdown file")
        assert_true("commands-index.json" in start_here, "desktop-pack START_HERE missed commands index JSON file")
        assert_true("commands-index.schema.json" in start_here, "desktop-pack START_HERE missed commands index schema file")
        assert_true("commands.json" in start_here, "desktop-pack START_HERE missed structured commands file")
        assert_true("commands.schema.json" in start_here, "desktop-pack START_HERE missed commands schema file")
        assert_true("project-wizard.schema.json" in start_here, "desktop-pack START_HERE missed project wizard schema file")
        assert_true("source-intake.schema.json" in start_here, "desktop-pack START_HERE missed source intake schema file")
        assert_true("chapter-session.schema.json" in start_here, "desktop-pack START_HERE missed chapter session schema file")
        assert_true("handoff-report.schema.json" in start_here, "desktop-pack START_HERE missed handoff report schema file")
        assert_true("desktop-results.schema.json" in start_here, "desktop-pack START_HERE missed desktop results schema file")
        assert_true("troubleshooting.md" in start_here and "troubleshooting.html" in start_here, "desktop-pack START_HERE missed troubleshooting files")
        assert_true("continuity-audit.md" in start_here, "desktop-pack START_HERE missed continuity audit file")
        assert_true("continuity-audit.json" in start_here, "desktop-pack START_HERE missed continuity audit JSON file")
        assert_true("project-status.json" in start_here, "desktop-pack START_HERE missed project status JSON file")
        assert_true("author-review-queue.md" in start_here, "desktop-pack START_HERE missed author review queue file")
        assert_true("handoff-report.json" in start_here, "desktop-pack START_HERE missed handoff report JSON file")
        assert_true("chapter-session.md" in start_here, "desktop-pack START_HERE missed chapter session file")
        assert_true("real-client-walkthrough.md" in start_here, "desktop-pack START_HERE missed real client walkthrough file")
        assert_true("client-prompts.md" in start_here, "desktop-pack START_HERE missed client prompts file")
        assert_true("result-json-guide.md" in start_here, "desktop-pack START_HERE missed result JSON guide file")
        assert_true("evidence-to-results.md" in start_here, "desktop-pack START_HERE missed evidence-to-results file")
        assert_true("desktop-matrix.md" in start_here, "desktop-pack START_HERE missed desktop matrix file")
        assert_true("desktop-matrix.json" in start_here, "desktop-pack START_HERE missed desktop matrix JSON file")
        assert_true("desktop-next-step.md" in start_here, "desktop-pack START_HERE missed desktop next step markdown file")
        assert_true("desktop-next-step.json" in start_here, "desktop-pack START_HERE missed desktop next step JSON file")
        assert_true("agent-startup-prompt.md" in start_here and "agent-startup-prompt.json" in start_here, "desktop-pack START_HERE missed agent startup prompt files")
        assert_true("agent-startup-prompt.schema.json" in start_here, "desktop-pack START_HERE missed agent startup prompt schema file")
        assert_true("desktop-evidence-session.md" in start_here, "desktop-pack START_HERE missed desktop evidence session markdown file")
        assert_true("desktop-evidence-session.json" in start_here, "desktop-pack START_HERE missed desktop evidence session JSON file")
        assert_true("desktop-evidence-runbook.md" in start_here, "desktop-pack START_HERE missed desktop evidence runbook markdown file")
        assert_true("desktop-evidence-runbook.json" in start_here, "desktop-pack START_HERE missed desktop evidence runbook JSON file")
        assert_true("desktop-evidence-review.html" in start_here, "desktop-pack START_HERE missed desktop evidence review HTML file")
        assert_true("desktop-evidence-review.md" in start_here, "desktop-pack START_HERE missed desktop evidence review markdown file")
        assert_true("desktop-evidence-review.json" in start_here, "desktop-pack START_HERE missed desktop evidence review JSON file")
        assert_true("desktop-evidence-gaps.md" in start_here, "desktop-pack START_HERE missed desktop evidence gaps markdown file")
        assert_true("desktop-evidence-gaps.json" in start_here, "desktop-pack START_HERE missed desktop evidence gaps JSON file")
        assert_true("Next Step" in next_step, "desktop-pack next-step missing title")
        assert_true("MCP tool profile: full" in next_step, "desktop-pack next-step missed tool profile")
        assert_true("The desktop client shows too many MCP tools" in next_step, "desktop-pack next-step missed tool profile downsizing path")
        assert_true("Choose By Situation" in next_step, "desktop-pack next-step missed situation table")
        assert_true("First time using this pack" in next_step, "desktop-pack next-step missed first install path")
        assert_true("refresh an older local install" in next_step, "desktop-pack next-step missed upgrade path")
        assert_true("remove the local MCP entry" in next_step, "desktop-pack next-step missed uninstall path")
        assert_true("check whether this pack is usable" in next_step, "desktop-pack next-step missed pack doctor path")
        assert_true("pack_doctor" in next_step, "desktop-pack next-step missed pack doctor command")
        assert_true("pack_doctor_json" in next_step, "desktop-pack next-step missed pack doctor JSON command")
        assert_true("one file to share" in next_step and "desktop_pack_archive" in next_step, "desktop-pack next-step missed archive path")
        assert_true("desktop_next_step" in next_step, "desktop-pack next-step missed dynamic next-step command")
        assert_true("agent_startup_prompt" in next_step and "agent-startup-prompt.json" in next_step, "desktop-pack next-step missed agent startup prompt path")
        assert_true("You want writer mode" in next_step, "desktop-pack next-step missed writer approval path")
        assert_true("desktop client is usable" in next_step, "desktop-pack next-step missed client compatibility path")
        assert_true("desktop-matrix.json" in next_step, "desktop-pack next-step missed desktop matrix JSON file")
        assert_true("Project is not initialized" in next_step, "desktop-pack next-step missed wizard path")
        assert_true("Desktop agent parsed source material" in next_step, "desktop-pack next-step missed source intake path")
        assert_true("handoff-report.json" in next_step, "desktop-pack next-step missed handoff report JSON file")
        assert_true("about to draft or revise" in next_step, "desktop-pack next-step missed chapter session path")
        assert_true("desktop-evidence-session.md" in next_step and "desktop-evidence-session.json" in next_step, "desktop-pack next-step missed desktop evidence session path")
        assert_true("desktop-evidence-runbook.md" in next_step and "desktop-evidence-runbook.json" in next_step, "desktop-pack next-step missed desktop evidence runbook path")
        assert_true("desktop-evidence-review.html" in next_step and "desktop-evidence-review.md" in next_step and "desktop-evidence-review.json" in next_step, "desktop-pack next-step missed desktop evidence review path")
        assert_true("desktop-evidence-gaps.md" in next_step and "desktop-evidence-gaps.json" in next_step, "desktop-pack next-step missed desktop evidence gaps path")
        assert_true("You copied evidence text" in next_step, "desktop-pack next-step missed evidence import path")
        assert_true("Result JSON is ready" in next_step, "desktop-pack next-step missed record path")
        assert_true("desktop_results_doctor" in next_step and "imported_desktop_results_doctor" in next_step, "desktop-pack next-step missed results doctor path")
        assert_true("desktop_evidence_packet" in next_step or "desktop-evidence-packet" in next_step, "desktop-pack next-step missed evidence packet path")
        assert_true("bash install-local.sh --yes" in next_step, "desktop-pack next-step missed install command")
        assert_true("install-local.cmd" in next_step, "desktop-pack next-step missed Windows install command")
        assert_true("archive-local.cmd --yes" in next_step, "desktop-pack next-step missed Windows archive command")
        assert_true("### Shareable Zip" in next_step, "desktop-pack next-step missed archive command section")
        assert_true("writer_pack" in next_step, "desktop-pack next-step missed writer pack command")
        assert_true("ingest_evidence" in next_step or "ingest-desktop-evidence" in next_step, "desktop-pack next-step missed evidence ingest command")
        assert_true("record_imported_desktop_check" in next_step or "record-desktop-check" in next_step, "desktop-pack next-step missed record command")
        assert_true("commands.json" in next_step, "desktop-pack next-step missed structured commands file")
        assert_true("commands.schema.json" in next_step, "desktop-pack next-step missed commands schema file")
        assert_true("project-status.json" in next_step, "desktop-pack next-step missed project status JSON file")
        assert_true("does not write project state" in next_step, "desktop-pack next-step missed read-only boundary")
        assert_true("Quick Install" in quick_install, "desktop-pack quick install missing title")
        assert_true("desktop-bootstrap" in quick_install, "desktop-pack quick install missed bootstrap command")
        assert_true("doctor" in quick_install, "desktop-pack quick install missed local verification command")
        assert_true("record-desktop-check" in quick_install, "desktop-pack quick install missed record command")
        assert_true("desktop-evidence-session.md" in quick_install, "desktop-pack quick install missed desktop evidence session file")
        assert_true("desktop-evidence-session.json" in quick_install, "desktop-pack quick install missed desktop evidence session JSON file")
        assert_true("desktop-evidence-runbook.md" in quick_install, "desktop-pack quick install missed desktop evidence runbook file")
        assert_true("desktop-evidence-runbook.json" in quick_install, "desktop-pack quick install missed desktop evidence runbook JSON file")
        assert_true("desktop-evidence-review.html" in quick_install, "desktop-pack quick install missed desktop evidence review HTML file")
        assert_true("desktop-evidence-review.md" in quick_install, "desktop-pack quick install missed desktop evidence review file")
        assert_true("desktop-evidence-review.json" in quick_install, "desktop-pack quick install missed desktop evidence review JSON file")
        assert_true("desktop-evidence-review.schema.json" in quick_install, "desktop-pack quick install missed desktop evidence review schema file")
        assert_true("desktop-evidence-gaps.md" in quick_install, "desktop-pack quick install missed desktop evidence gaps markdown file")
        assert_true("desktop-evidence-gaps.json" in quick_install, "desktop-pack quick install missed desktop evidence gaps JSON file")
        assert_true("first-three.html" in quick_install, "desktop-pack quick install missed first three file")
        assert_true("local-summary.md" in quick_install and "local-summary.json" in quick_install and "local-summary.html" in quick_install and "local-summary.schema.json" in quick_install, "desktop-pack quick install missed local summary files")
        assert_true("real-client-walkthrough.md" in quick_install, "desktop-pack quick install missed real client walkthrough file")
        assert_true("client-prompts.md" in quick_install, "desktop-pack quick install missed client prompts file")
        assert_true("install-local.md" in quick_install, "desktop-pack quick install missed local install file")
        assert_true("bash install-local.sh --yes" in quick_install, "desktop-pack quick install missed local install script command")
        assert_true("install-local.ps1" in quick_install, "desktop-pack quick install missed PowerShell install wrapper")
        assert_true("install-local.cmd" in quick_install, "desktop-pack quick install missed Windows install wrapper")
        assert_true("install-local.command" in quick_install, "desktop-pack quick install missed macOS install launcher")
        assert_true("upgrade-local.sh --yes" in quick_install, "desktop-pack quick install missed local upgrade script command")
        assert_true("upgrade-local.ps1" in quick_install, "desktop-pack quick install missed PowerShell upgrade wrapper")
        assert_true("upgrade-local.cmd" in quick_install, "desktop-pack quick install missed Windows upgrade wrapper")
        assert_true("upgrade-local.command" in quick_install, "desktop-pack quick install missed macOS upgrade launcher")
        assert_true("uninstall-local.sh --yes" in quick_install, "desktop-pack quick install missed local uninstall script command")
        assert_true("uninstall-local.ps1" in quick_install, "desktop-pack quick install missed PowerShell uninstall wrapper")
        assert_true("uninstall-local.cmd" in quick_install, "desktop-pack quick install missed Windows uninstall wrapper")
        assert_true("uninstall-local.command" in quick_install, "desktop-pack quick install missed macOS uninstall launcher")
        assert_true("local-checks.md" in quick_install, "desktop-pack quick install missed local checks file")
        assert_true("local-checks.sh" in quick_install, "desktop-pack quick install missed local checks script")
        assert_true("local-checks.ps1" in quick_install, "desktop-pack quick install missed PowerShell checks wrapper")
        assert_true("local-checks.cmd" in quick_install, "desktop-pack quick install missed Windows checks wrapper")
        assert_true("local-checks.command" in quick_install, "desktop-pack quick install missed macOS checks launcher")
        assert_true("archive-local.md" in quick_install, "desktop-pack quick install missed local archive file")
        assert_true("archive-local.sh" in quick_install, "desktop-pack quick install missed local archive script")
        assert_true("archive-local.ps1" in quick_install, "desktop-pack quick install missed PowerShell archive wrapper")
        assert_true("archive-local.cmd" in quick_install, "desktop-pack quick install missed Windows archive wrapper")
        assert_true("archive-local.command" in quick_install, "desktop-pack quick install missed macOS archive launcher")
        assert_true("writer-mode-approval.md" in quick_install, "desktop-pack quick install missed writer approval file")
        assert_true("writer-mode-approval.json" in quick_install, "desktop-pack quick install missed writer approval JSON file")
        assert_true("writer-mode-approval.schema.json" in quick_install, "desktop-pack quick install missed writer approval schema file")
        assert_true("client-compatibility.md" in quick_install, "desktop-pack quick install missed client compatibility file")
        assert_true("client-compatibility.json" in quick_install, "desktop-pack quick install missed client compatibility JSON file")
        assert_true("commands.json" in quick_install, "desktop-pack quick install missed structured commands file")
        assert_true("commands.schema.json" in quick_install, "desktop-pack quick install missed commands schema file")
        assert_true("starter-brief.schema.json" in quick_install, "desktop-pack quick install missed starter brief schema file")
        assert_true("pack-doctor.md" in quick_install, "desktop-pack quick install missed pack doctor file")
        assert_true("pack-doctor.json" in quick_install, "desktop-pack quick install missed pack doctor JSON file")
        assert_true("pack_doctor" in quick_install, "desktop-pack quick install missed pack doctor command")
        assert_true("pack_doctor_json" in quick_install, "desktop-pack quick install missed pack doctor JSON command")
        assert_true("desktop-results-doctor" in quick_install, "desktop-pack quick install missed results doctor command")
        assert_true("desktop-evidence-packet" in quick_install, "desktop-pack quick install missed evidence packet command")
        assert_true("desktop-evidence-review" in quick_install, "desktop-pack quick install missed evidence review command")
        assert_true("desktop-evidence-gaps" in quick_install, "desktop-pack quick install missed evidence gaps command")
        assert_true("desktop_pack_archive" in quick_install, "desktop-pack quick install missed archive command")
        assert_true("open-local.md" in quick_install, "desktop-pack quick install missed open local guide")
        assert_true("bash open-local.sh" in quick_install, "desktop-pack quick install missed open local script")
        assert_true("project-wizard.schema.json" in quick_install, "desktop-pack quick install missed project wizard schema file")
        assert_true("source-intake.schema.json" in quick_install, "desktop-pack quick install missed source intake schema file")
        assert_true("chapter-session.schema.json" in quick_install, "desktop-pack quick install missed chapter session schema file")
        assert_true("handoff-report.schema.json" in quick_install, "desktop-pack quick install missed handoff report schema file")
        assert_true("desktop-results.schema.json" in quick_install, "desktop-pack quick install missed desktop results schema file")
        assert_true("troubleshooting.md" in quick_install and "troubleshooting.html" in quick_install, "desktop-pack quick install missed troubleshooting files")
        assert_true("result-json-guide.md" in quick_install, "desktop-pack quick install missed result JSON guide file")
        assert_true("evidence-to-results.md" in quick_install, "desktop-pack quick install missed evidence-to-results file")
        assert_true("next-step.md" in quick_install, "desktop-pack quick install missed next-step file")
        assert_true("ready_to_record" in quick_install, "desktop-pack quick install missed results doctor ready status")
        assert_true("Remote connector-only platforms are outside this local desktop flow" in quick_install, "desktop-pack quick install missed remote boundary")
        assert_true("Open Local" in open_local, "desktop-pack open local guide missing title")
        assert_true("open_pack_index" in open_local, "desktop-pack open local guide missed command key")
        assert_true("powershell_open_launcher" in open_local, "desktop-pack open local guide missed PowerShell key")
        assert_true("windows_open_launcher" in open_local, "desktop-pack open local guide missed Windows key")
        assert_true("macos_open_launcher" in open_local, "desktop-pack open local guide missed macOS key")
        assert_true("does not install the skill" in open_local, "desktop-pack open local guide missed install boundary")
        assert_true("first-three.html" in open_local, "desktop-pack open local guide missed first three HTML")
        assert_true("user-steps.html" in open_local, "desktop-pack open local guide missed user steps HTML")
        assert_true(open_local_script.startswith("#!/usr/bin/env bash"), "desktop-pack open local script missing shebang")
        assert_true("Long Novel Agent desktop pack opener" in open_local_script, "desktop-pack open local script missing label")
        assert_true("open \"$FIRST_THREE_FILE\"" in open_local_script or "xdg-open \"$FIRST_THREE_FILE\"" in open_local_script, "desktop-pack open local script missed browser open")
        assert_true("$USER_STEPS_FILE" in open_local_script, "desktop-pack open local script missed user steps follow-up file")
        assert_true("Detailed dashboard" in open_local_script and "$INDEX_FILE" in open_local_script, "desktop-pack open local script missed dashboard path")
        assert_true("does not install, change MCP config, write .novel-agent/" in open_local_script, "desktop-pack open local script missed read-only boundary")
        assert_true("--yes" not in open_local_script, "desktop-pack open local script should not expose apply flag")
        assert_true(os.access(output_dir / "open-local.sh", os.X_OK), "desktop-pack open local script should be executable")
        assert_true(open_local_ps1.startswith("$ErrorActionPreference = 'Stop'"), "desktop-pack open local PowerShell wrapper missing error mode")
        assert_true("Start-Process -FilePath $FirstThreeFile" in open_local_ps1, "desktop-pack open local PowerShell wrapper missed browser open")
        assert_true("$UserStepsFile" in open_local_ps1, "desktop-pack open local PowerShell wrapper missed user steps follow-up file")
        assert_true("Detailed dashboard" in open_local_ps1 and "$IndexFile" in open_local_ps1, "desktop-pack open local PowerShell wrapper missed dashboard path")
        assert_true("does not install, change MCP config, write .novel-agent/" in open_local_ps1, "desktop-pack open local PowerShell wrapper missed read-only boundary")
        assert_true("--yes" not in open_local_ps1, "desktop-pack open local PowerShell wrapper should not expose apply flag")
        assert_true(open_local_cmd.startswith("@echo off"), "desktop-pack open local CMD wrapper missing batch header")
        assert_true('start "" "%FIRST_THREE_FILE%"' in open_local_cmd, "desktop-pack open local CMD wrapper missed browser open")
        assert_true("%USER_STEPS_FILE%" in open_local_cmd, "desktop-pack open local CMD wrapper missed user steps follow-up file")
        assert_true("Detailed dashboard" in open_local_cmd and "%INDEX_FILE%" in open_local_cmd, "desktop-pack open local CMD wrapper missed dashboard path")
        assert_true("does not install, change MCP config, write .novel-agent" in open_local_cmd, "desktop-pack open local CMD wrapper missed read-only boundary")
        assert_true("--yes" not in open_local_cmd, "desktop-pack open local CMD wrapper should not expose apply flag")
        assert_true(open_local_command.startswith("#!/usr/bin/env bash"), "desktop-pack open local macOS launcher missing shebang")
        assert_true("Long Novel Agent macOS desktop pack opener" in open_local_command, "desktop-pack open local macOS launcher missing label")
        assert_true('TARGET_SCRIPT="${SCRIPT_DIR}/open-local.sh"' in open_local_command, "desktop-pack open local macOS launcher should target sibling script")
        assert_true("Type YES" not in open_local_command, "desktop-pack open local macOS launcher should not ask for apply confirmation")
        assert_true(os.access(output_dir / "open-local.command", os.X_OK), "desktop-pack open local macOS launcher should be executable")
        assert_true("Pack Doctor" in pack_doctor_md, "desktop-pack pack doctor markdown missing title")
        assert_true("Refresh JSON Report" in pack_doctor_md, "desktop-pack pack doctor markdown missed JSON refresh section")
        assert_true("pack-doctor.json" in pack_doctor_md, "desktop-pack pack doctor markdown missed JSON report")
        assert_true("pack-manifest.json" in pack_doctor_md, "desktop-pack pack doctor missed manifest check")
        assert_true("commands.json" in pack_doctor_md, "desktop-pack pack doctor missed commands check")
        assert_true("script permissions" in pack_doctor_md, "desktop-pack pack doctor missed permission check")
        assert_true("does not write `.novel-agent/`" in pack_doctor_md, "desktop-pack pack doctor missed project-state boundary")
        assert_true("does not change local MCP config" in pack_doctor_md, "desktop-pack pack doctor missed MCP boundary")
        assert_true(index_html.count("Pack Doctor JSON Checks") == 1, "desktop-pack index missed pack doctor JSON check UI")
        assert_true("packDoctorFile" in index_html, "desktop-pack index missed pack doctor input")
        assert_true("validatePackDoctorReport" in index_html, "desktop-pack index missed pack doctor validation")
        assert_true(index_html.count("Starter Brief JSON Checks") == 1, "desktop-pack index missed starter brief JSON check UI")
        assert_true("starterBriefFile" in index_html, "desktop-pack index missed starter brief input")
        assert_true("starter-brief.schema.json" in index_html, "desktop-pack index missed starter brief schema")
        assert_true("validateStarterBriefPack" in index_html, "desktop-pack index missed starter brief validation")
        assert_true("Install Local" in install_local, "desktop-pack local install markdown missing title")
        assert_true("bash install-local.sh" in install_local, "desktop-pack local install markdown missed preview command")
        assert_true("bash install-local.sh --yes" in install_local, "desktop-pack local install markdown missed apply command")
        assert_true("install-local.command" in install_local, "desktop-pack local install markdown missed macOS launcher")
        assert_true("desktop-bootstrap" in install_local, "desktop-pack local install markdown missed bootstrap command")
        assert_true("local-checks.sh" in install_local, "desktop-pack local install markdown missed local checks")
        assert_true("does not prove the real GUI desktop client" in install_local, "desktop-pack local install markdown missed GUI boundary")
        assert_true(install_local_script.startswith("#!/usr/bin/env bash"), "desktop-pack local install script missing shebang")
        assert_true("Long Novel Agent local installer" in install_local_script, "desktop-pack local install script missing label")
        assert_true("Default mode is preview only" in install_local_script, "desktop-pack local install script missed dry-run default")
        assert_true("--yes" in install_local_script, "desktop-pack local install script missed apply flag")
        assert_true("desktop-bootstrap" in install_local_script, "desktop-pack local install script missed bootstrap command")
        assert_true("SCRIPT_DIR=" in install_local_script, "desktop-pack local install script missed script-dir lookup")
        assert_true('CHECK_SCRIPT="${SCRIPT_DIR}/local-checks.sh"' in install_local_script, "desktop-pack local install script should locate local checks next to itself")
        assert_true('bash "$CHECK_SCRIPT"' in install_local_script, "desktop-pack local install script should call local checks by path")
        assert_true("local-checks.sh" in install_local_script, "desktop-pack local install script missed local checks command")
        assert_true("does not prove GUI desktop-client MCP access" in install_local_script, "desktop-pack local install script missed GUI boundary")
        assert_true(os.access(output_dir / "install-local.sh", os.X_OK), "desktop-pack local install script should be executable")
        assert_true(install_local_cmd.startswith("@echo off"), "desktop-pack Windows install wrapper missing batch header")
        assert_true("Long Novel Agent Windows local installer" in install_local_cmd, "desktop-pack Windows install wrapper missing label")
        assert_true("Default mode is preview only" in install_local_cmd, "desktop-pack Windows install wrapper missed dry-run default")
        assert_true("--yes" in install_local_cmd, "desktop-pack Windows install wrapper missed apply flag")
        assert_true("desktop-bootstrap" in install_local_cmd, "desktop-pack Windows install wrapper missed bootstrap command")
        assert_true('%SCRIPT_DIR%\\local-checks.cmd' in install_local_cmd, "desktop-pack Windows install wrapper should locate local checks next to itself")
        assert_true(".novel-agent/ project state" in install_local_cmd, "desktop-pack Windows install wrapper missed project-state write scope")
        assert_true("does not prove GUI desktop-client MCP access" in install_local_cmd, "desktop-pack Windows install wrapper missed GUI boundary")
        assert_true(install_local_command.startswith("#!/usr/bin/env bash"), "desktop-pack macOS install launcher missing shebang")
        assert_true("macOS double-click launcher: Install Local" in install_local_command, "desktop-pack macOS install launcher missing label")
        assert_true('TARGET_SCRIPT="${SCRIPT_DIR}/install-local.sh"' in install_local_command, "desktop-pack macOS install launcher should target sibling script")
        assert_true("Type YES" in install_local_command, "desktop-pack macOS install launcher missed apply confirmation")
        assert_true('bash "$TARGET_SCRIPT" --yes' in install_local_command, "desktop-pack macOS install launcher missed apply call")
        assert_true(os.access(output_dir / "install-local.command", os.X_OK), "desktop-pack macOS install launcher should be executable")
        install_preview = subprocess.run(
            ["bash", str(output_dir / "install-local.sh")],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("Default mode is preview only" in install_preview.stdout, "desktop-pack local install script preview missed dry-run default")
        assert_true("No files are written" in install_preview.stdout, "desktop-pack local install script preview should not write")
        assert_true("local-checks.sh file next to install-local.sh" in install_preview.stdout, "desktop-pack local install script preview missed path-aware local checks note")
        install_command_preview = subprocess.run(
            ["bash", str(output_dir / "install-local.command")],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("macOS double-click launcher: Install Local" in install_command_preview.stdout, "desktop-pack macOS install launcher preview missed label")
        assert_true("Default mode is preview only" in install_command_preview.stdout, "desktop-pack macOS install launcher should call preview")
        assert_true("No apply action was run" in install_command_preview.stdout, "desktop-pack macOS install launcher should not apply without YES")
        assert_true("Upgrade Local" in upgrade_local, "desktop-pack local upgrade markdown missing title")
        assert_true("bash upgrade-local.sh" in upgrade_local, "desktop-pack local upgrade markdown missed preview command")
        assert_true("bash upgrade-local.sh --yes" in upgrade_local, "desktop-pack local upgrade markdown missed apply command")
        assert_true("upgrade-local.command" in upgrade_local, "desktop-pack local upgrade markdown missed macOS launcher")
        assert_true("--upgrade" in upgrade_local, "desktop-pack local upgrade markdown missed upgrade command")
        assert_true("does not run quickstart" in upgrade_local, "desktop-pack local upgrade markdown missed quickstart boundary")
        assert_true("does not delete `.novel-agent/` project state" in upgrade_local, "desktop-pack local upgrade markdown missed state preservation note")
        assert_true(upgrade_local_script.startswith("#!/usr/bin/env bash"), "desktop-pack local upgrade script missing shebang")
        assert_true("Long Novel Agent local upgrader" in upgrade_local_script, "desktop-pack local upgrade script missing label")
        assert_true("Default mode is preview only" in upgrade_local_script, "desktop-pack local upgrade script missed dry-run default")
        assert_true("--yes" in upgrade_local_script, "desktop-pack local upgrade script missed apply flag")
        assert_true("--upgrade" in upgrade_local_script, "desktop-pack local upgrade script missed upgrade command")
        assert_true("install.py --upgrade" in upgrade_local_script, "desktop-pack local upgrade script missed upgrade execution label")
        assert_true("does not run quickstart and does not delete .novel-agent/ project state" in upgrade_local_script, "desktop-pack local upgrade script missed state preservation note")
        assert_true("rm -rf" not in upgrade_local_script, "desktop-pack local upgrade script should not use raw recursive deletion")
        assert_true(os.access(output_dir / "upgrade-local.sh", os.X_OK), "desktop-pack local upgrade script should be executable")
        assert_true(upgrade_local_cmd.startswith("@echo off"), "desktop-pack Windows upgrade wrapper missing batch header")
        assert_true("Long Novel Agent Windows local upgrader" in upgrade_local_cmd, "desktop-pack Windows upgrade wrapper missing label")
        assert_true("Default mode is preview only" in upgrade_local_cmd, "desktop-pack Windows upgrade wrapper missed dry-run default")
        assert_true("--yes" in upgrade_local_cmd, "desktop-pack Windows upgrade wrapper missed apply flag")
        assert_true("--upgrade" in upgrade_local_cmd, "desktop-pack Windows upgrade wrapper missed upgrade command")
        assert_true("install.py --upgrade" in upgrade_local_cmd, "desktop-pack Windows upgrade wrapper missed upgrade execution label")
        assert_true("does not run quickstart and does not delete .novel-agent/ project state" in upgrade_local_cmd, "desktop-pack Windows upgrade wrapper missed state preservation note")
        assert_true("macOS double-click launcher: Upgrade Local" in upgrade_local_command, "desktop-pack macOS upgrade launcher missing label")
        assert_true('TARGET_SCRIPT="${SCRIPT_DIR}/upgrade-local.sh"' in upgrade_local_command, "desktop-pack macOS upgrade launcher should target sibling script")
        assert_true("Type YES" in upgrade_local_command, "desktop-pack macOS upgrade launcher missed apply confirmation")
        assert_true('bash "$TARGET_SCRIPT" --yes' in upgrade_local_command, "desktop-pack macOS upgrade launcher missed apply call")
        assert_true(os.access(output_dir / "upgrade-local.command", os.X_OK), "desktop-pack macOS upgrade launcher should be executable")
        upgrade_preview = subprocess.run(
            ["bash", str(output_dir / "upgrade-local.sh")],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("Default mode is preview only" in upgrade_preview.stdout, "desktop-pack local upgrade script preview missed dry-run default")
        assert_true("No files are changed" in upgrade_preview.stdout, "desktop-pack local upgrade script preview should not change files")
        assert_true("--upgrade" in upgrade_preview.stdout, "desktop-pack local upgrade script preview missed upgrade command")
        upgrade_command_preview = subprocess.run(
            ["bash", str(output_dir / "upgrade-local.command")],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("macOS double-click launcher: Upgrade Local" in upgrade_command_preview.stdout, "desktop-pack macOS upgrade launcher preview missed label")
        assert_true("Default mode is preview only" in upgrade_command_preview.stdout, "desktop-pack macOS upgrade launcher should call preview")
        assert_true("No apply action was run" in upgrade_command_preview.stdout, "desktop-pack macOS upgrade launcher should not apply without YES")
        assert_true("Uninstall Local" in uninstall_local, "desktop-pack local uninstall markdown missing title")
        assert_true("bash uninstall-local.sh" in uninstall_local, "desktop-pack local uninstall markdown missed preview command")
        assert_true("bash uninstall-local.sh --yes" in uninstall_local, "desktop-pack local uninstall markdown missed apply command")
        assert_true("uninstall-local.command" in uninstall_local, "desktop-pack local uninstall markdown missed macOS launcher")
        assert_true("--uninstall" in uninstall_local, "desktop-pack local uninstall markdown missed uninstall command")
        assert_true("does not delete `.novel-agent/` project state" in uninstall_local, "desktop-pack local uninstall markdown missed state preservation note")
        assert_true(uninstall_local_script.startswith("#!/usr/bin/env bash"), "desktop-pack local uninstall script missing shebang")
        assert_true("Long Novel Agent local uninstaller" in uninstall_local_script, "desktop-pack local uninstall script missing label")
        assert_true("Default mode is preview only" in uninstall_local_script, "desktop-pack local uninstall script missed dry-run default")
        assert_true("--yes" in uninstall_local_script, "desktop-pack local uninstall script missed apply flag")
        assert_true("--uninstall" in uninstall_local_script, "desktop-pack local uninstall script missed uninstall command")
        assert_true("install.py --uninstall" in uninstall_local_script, "desktop-pack local uninstall script missed uninstall execution label")
        assert_true("does not delete .novel-agent/ project state" in uninstall_local_script, "desktop-pack local uninstall script missed state preservation note")
        assert_true("rm -rf" not in uninstall_local_script, "desktop-pack local uninstall script should not use raw recursive deletion")
        assert_true(os.access(output_dir / "uninstall-local.sh", os.X_OK), "desktop-pack local uninstall script should be executable")
        assert_true(uninstall_local_cmd.startswith("@echo off"), "desktop-pack Windows uninstall wrapper missing batch header")
        assert_true("Long Novel Agent Windows local uninstaller" in uninstall_local_cmd, "desktop-pack Windows uninstall wrapper missing label")
        assert_true("Default mode is preview only" in uninstall_local_cmd, "desktop-pack Windows uninstall wrapper missed dry-run default")
        assert_true("--yes" in uninstall_local_cmd, "desktop-pack Windows uninstall wrapper missed apply flag")
        assert_true("--uninstall" in uninstall_local_cmd, "desktop-pack Windows uninstall wrapper missed uninstall command")
        assert_true("install.py --uninstall" in uninstall_local_cmd, "desktop-pack Windows uninstall wrapper missed uninstall execution label")
        assert_true("does not delete .novel-agent/ project state" in uninstall_local_cmd, "desktop-pack Windows uninstall wrapper missed state preservation note")
        assert_true("macOS double-click launcher: Uninstall Local" in uninstall_local_command, "desktop-pack macOS uninstall launcher missing label")
        assert_true('TARGET_SCRIPT="${SCRIPT_DIR}/uninstall-local.sh"' in uninstall_local_command, "desktop-pack macOS uninstall launcher should target sibling script")
        assert_true("Type YES" in uninstall_local_command, "desktop-pack macOS uninstall launcher missed apply confirmation")
        assert_true('bash "$TARGET_SCRIPT" --yes' in uninstall_local_command, "desktop-pack macOS uninstall launcher missed apply call")
        assert_true(os.access(output_dir / "uninstall-local.command", os.X_OK), "desktop-pack macOS uninstall launcher should be executable")
        uninstall_preview = subprocess.run(
            ["bash", str(output_dir / "uninstall-local.sh")],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("Default mode is preview only" in uninstall_preview.stdout, "desktop-pack local uninstall script preview missed dry-run default")
        assert_true("No files are changed" in uninstall_preview.stdout, "desktop-pack local uninstall script preview should not change files")
        assert_true("--uninstall" in uninstall_preview.stdout, "desktop-pack local uninstall script preview missed uninstall command")
        uninstall_command_preview = subprocess.run(
            ["bash", str(output_dir / "uninstall-local.command")],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("macOS double-click launcher: Uninstall Local" in uninstall_command_preview.stdout, "desktop-pack macOS uninstall launcher preview missed label")
        assert_true("Default mode is preview only" in uninstall_command_preview.stdout, "desktop-pack macOS uninstall launcher should call preview")
        assert_true("No apply action was run" in uninstall_command_preview.stdout, "desktop-pack macOS uninstall launcher should not apply without YES")
        assert_true("Local Checks" in local_checks, "desktop-pack local checks markdown missing title")
        assert_true("bash local-checks.sh" in local_checks, "desktop-pack local checks markdown missed script command")
        assert_true("local-checks.command" in local_checks, "desktop-pack local checks markdown missed macOS launcher")
        assert_true("desktop_verify" in local_checks, "desktop-pack local checks markdown missed desktop verify step")
        assert_true("project_status" in local_checks, "desktop-pack local checks markdown missed project status step")
        assert_true("continuity_audit" in local_checks, "desktop-pack local checks markdown missed continuity audit step")
        assert_true("prepare_session" in local_checks, "desktop-pack local checks markdown missed prepare session step")
        assert_true("desktop_checklist" in local_checks, "desktop-pack local checks markdown missed checklist step")
        assert_true("does not prove the real GUI desktop client" in local_checks, "desktop-pack local checks markdown missed GUI boundary")
        assert_true(local_checks_script.startswith("#!/usr/bin/env bash"), "desktop-pack local checks script missing shebang")
        assert_true("Long Novel Agent local desktop checks" in local_checks_script, "desktop-pack local checks script missing label")
        assert_true("desktop-verify" in local_checks_script, "desktop-pack local checks script missed desktop-verify command")
        assert_true("project-status" in local_checks_script, "desktop-pack local checks script missed project-status command")
        assert_true("continuity-audit" in local_checks_script, "desktop-pack local checks script missed continuity-audit command")
        assert_true("prepare-session" in local_checks_script, "desktop-pack local checks script missed prepare-session command")
        assert_true("desktop-checklist" in local_checks_script, "desktop-pack local checks script missed desktop-checklist command")
        assert_true("does not prove GUI desktop-client MCP access" in local_checks_script, "desktop-pack local checks script missed GUI boundary")
        assert_true(os.access(output_dir / "local-checks.sh", os.X_OK), "desktop-pack local checks script should be executable")
        assert_true(local_checks_cmd.startswith("@echo off"), "desktop-pack Windows checks wrapper missing batch header")
        assert_true("Long Novel Agent Windows local desktop checks" in local_checks_cmd, "desktop-pack Windows checks wrapper missing label")
        assert_true("desktop-verify" in local_checks_cmd, "desktop-pack Windows checks wrapper missed desktop-verify command")
        assert_true("project-status" in local_checks_cmd, "desktop-pack Windows checks wrapper missed project-status command")
        assert_true("continuity-audit" in local_checks_cmd, "desktop-pack Windows checks wrapper missed continuity-audit command")
        assert_true("prepare-session" in local_checks_cmd, "desktop-pack Windows checks wrapper missed prepare-session command")
        assert_true("desktop-checklist" in local_checks_cmd, "desktop-pack Windows checks wrapper missed desktop-checklist command")
        assert_true("does not prove GUI desktop-client MCP access" in local_checks_cmd, "desktop-pack Windows checks wrapper missed GUI boundary")
        assert_true("macOS double-click launcher: Local Checks" in local_checks_command, "desktop-pack macOS checks launcher missing label")
        assert_true('TARGET_SCRIPT="${SCRIPT_DIR}/local-checks.sh"' in local_checks_command, "desktop-pack macOS checks launcher should target sibling script")
        assert_true("does not record real GUI desktop-client evidence" in local_checks_command, "desktop-pack macOS checks launcher missed GUI evidence boundary")
        assert_true(os.access(output_dir / "local-checks.command", os.X_OK), "desktop-pack macOS checks launcher should be executable")
        assert_true("Archive Local" in archive_local, "desktop-pack archive local markdown missing title")
        assert_true("bash archive-local.sh" in archive_local, "desktop-pack archive local markdown missed preview command")
        assert_true("bash archive-local.sh --yes" in archive_local, "desktop-pack archive local markdown missed apply command")
        assert_true("archive-local.command" in archive_local, "desktop-pack archive local markdown missed macOS launcher")
        assert_true("archive-pack" in archive_local, "desktop-pack archive local markdown missed archive-pack command")
        assert_true("does not write `.novel-agent/`" in archive_local, "desktop-pack archive local markdown missed project-state boundary")
        assert_true(archive_local_script.startswith("#!/usr/bin/env bash"), "desktop-pack archive local script missing shebang")
        assert_true("Long Novel Agent desktop pack archiver" in archive_local_script, "desktop-pack archive local script missing label")
        assert_true("Default mode is preview only" in archive_local_script, "desktop-pack archive local script missed dry-run default")
        assert_true("archive-pack" in archive_local_script, "desktop-pack archive local script missed archive-pack command")
        assert_true('PYTHON_BIN="${PYTHON_BIN:-python3}"' in archive_local_script, "desktop-pack archive local script missed Python selector")
        assert_true('"$PYTHON_BIN" "$CLI_PATH" archive-pack "$SCRIPT_DIR" --force --format markdown' in archive_local_script, "desktop-pack archive local script should target its own directory")
        assert_true("does not write .novel-agent/" in archive_local_script, "desktop-pack archive local script missed project-state boundary")
        assert_true(os.access(output_dir / "archive-local.sh", os.X_OK), "desktop-pack archive local script should be executable")
        assert_true(archive_local_cmd.startswith("@echo off"), "desktop-pack Windows archive wrapper missing batch header")
        assert_true("Long Novel Agent Windows desktop pack archiver" in archive_local_cmd, "desktop-pack Windows archive wrapper missing label")
        assert_true("Default mode is preview only" in archive_local_cmd, "desktop-pack Windows archive wrapper missed dry-run default")
        assert_true("--yes" in archive_local_cmd, "desktop-pack Windows archive wrapper missed apply flag")
        assert_true('archive-pack "%SCRIPT_DIR%" --force --format markdown' in archive_local_cmd, "desktop-pack Windows archive wrapper should target its own directory")
        assert_true("does not write .novel-agent/" in archive_local_cmd, "desktop-pack Windows archive wrapper missed project-state boundary")
        assert_true("macOS double-click launcher: Archive Local" in archive_local_command, "desktop-pack macOS archive launcher missing label")
        assert_true('TARGET_SCRIPT="${SCRIPT_DIR}/archive-local.sh"' in archive_local_command, "desktop-pack macOS archive launcher should target sibling script")
        assert_true("Type YES" in archive_local_command, "desktop-pack macOS archive launcher missed apply confirmation")
        assert_true('bash "$TARGET_SCRIPT" --yes' in archive_local_command, "desktop-pack macOS archive launcher missed apply call")
        assert_true(os.access(output_dir / "archive-local.command", os.X_OK), "desktop-pack macOS archive launcher should be executable")
        archive_local_zip = output_dir.with_suffix(".zip")
        assert_true(not archive_local_zip.exists(), "desktop-pack local archive zip should not exist before archive script")
        archive_preview = subprocess.run(
            ["bash", str(output_dir / "archive-local.sh")],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("Default mode is preview only" in archive_preview.stdout, "desktop-pack archive local preview missed dry-run default")
        assert_true("archive-pack" in archive_preview.stdout, "desktop-pack archive local preview missed archive-pack command")
        assert_true(not archive_local_zip.exists(), "desktop-pack archive local preview should not write zip")
        archive_command_preview = subprocess.run(
            ["bash", str(output_dir / "archive-local.command")],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("macOS double-click launcher: Archive Local" in archive_command_preview.stdout, "desktop-pack macOS archive launcher preview missed label")
        assert_true("Default mode is preview only" in archive_command_preview.stdout, "desktop-pack macOS archive launcher should call preview")
        assert_true("No apply action was run" in archive_command_preview.stdout, "desktop-pack macOS archive launcher should not apply without YES")
        assert_true(not archive_local_zip.exists(), "desktop-pack macOS archive launcher preview should not write zip")
        archive_apply = subprocess.run(
            ["bash", str(output_dir / "archive-local.sh"), "--yes"],
            cwd=Path(tmp),
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("Long Novel Agent Desktop Pack Archive" in archive_apply.stdout, "desktop-pack archive local apply missed markdown report")
        assert_true(archive_local_zip.exists(), "desktop-pack archive local apply did not write zip")
        with zipfile.ZipFile(archive_local_zip) as local_archive_zip:
            local_archive_names = set(local_archive_zip.namelist())
        assert_true(f"{output_dir.name}/START_HERE.md" in local_archive_names, "desktop-pack archive local zip missed START_HERE.md")
        assert_true(f"{output_dir.name}/archive-local.sh" in local_archive_names, "desktop-pack archive local zip missed archive-local.sh")
        assert_true("Writer Mode Approval" in writer_approval, "desktop-pack writer approval missing title")
        assert_true("read-only" in writer_approval and "writer" in writer_approval, "desktop-pack writer approval missed mode language")
        assert_true(".novel-agent/" in writer_approval, "desktop-pack writer approval missed state boundary")
        assert_true("author_confirmation" in writer_approval, "desktop-pack writer approval missed MCP author confirmation")
        assert_true("--author-confirmation" in writer_approval, "desktop-pack writer approval missed CLI author confirmation")
        assert_true("write_session_preflight_gate" in writer_approval, "desktop-pack writer approval missed write session preflight gate")
        assert_true("check_write_session" in writer_approval, "desktop-pack writer approval missed write session preflight tool")
        assert_true("proposal_readiness_gate" in writer_approval, "desktop-pack writer approval missed proposal readiness gate")
        assert_true("build_proposal_readiness" in writer_approval, "desktop-pack writer approval missed proposal readiness tool")
        assert_true("acceptance_write_gate" in writer_approval, "desktop-pack writer approval missed acceptance write gate")
        assert_true("record-desktop-check" in writer_approval, "desktop-pack writer approval missed record command")
        assert_true("writer_mode_readiness" in writer_approval, "desktop-pack writer approval missed writer readiness command")
        assert_true("writer-mode-readiness" in writer_approval, "desktop-pack writer approval missed writer readiness CLI command")
        assert_true("writer_pack" in writer_approval, "desktop-pack writer approval missed writer pack command")
        assert_true("writer_mcp_config_preview" in writer_approval, "desktop-pack writer approval missed writer config preview command")
        assert_true("restore-snapshot" in writer_approval, "desktop-pack writer approval missed snapshot recovery command")
        assert_true("uninstall-local.sh" in writer_approval, "desktop-pack writer approval missed local uninstall recovery path")
        assert_true("writer-mode-approval.json" in writer_approval, "desktop-pack writer approval missed structured JSON pointer")
        assert_true("writer-mode-approval.schema.json" in writer_approval, "desktop-pack writer approval missed schema pointer")
        assert_true(writer_mode_approval_json["type"] == "writer_mode_approval", "desktop-pack writer approval JSON wrong type")
        assert_true(writer_mode_approval_json["$schema"] == "writer-mode-approval.schema.json", "desktop-pack writer approval JSON missed local schema")
        assert_true(writer_mode_approval_json["local_only"] is True, "desktop-pack writer approval JSON should be local only")
        assert_true(writer_mode_approval_json["requires_server"] is False, "desktop-pack writer approval JSON should not require server")
        assert_true(writer_mode_approval_json["requires_real_desktop_client"] is True, "desktop-pack writer approval JSON should require real client proof")
        assert_true(writer_mode_approval_json["would_write_project_state"] is False, "desktop-pack writer approval JSON should be read-only")
        assert_true(writer_mode_approval_json["would_write_install_config"] is False, "desktop-pack writer approval JSON should not write install config")
        assert_true(writer_mode_approval_json["would_write_pack_files"] is False, "desktop-pack writer approval JSON should not write pack files")
        assert_true(writer_mode_approval_json["records_real_client_evidence"] is False, "desktop-pack writer approval JSON should not record evidence")
        assert_true(writer_mode_approval_json["target_mode"] == "writer", "desktop-pack writer approval JSON target mode mismatch")
        assert_true(writer_mode_approval_json["target_chapter"] == 2, "desktop-pack writer approval JSON target chapter mismatch")
        assert_true(writer_mode_approval_json["author_confirmation"]["mcp_argument"] == "author_confirmation", "desktop-pack writer approval JSON missed MCP author confirmation")
        assert_true(writer_mode_approval_json["author_confirmation"]["cli_flag"] == "--author-confirmation", "desktop-pack writer approval JSON missed CLI author confirmation")
        assert_true({"write_session_preflight_gate", "proposal_readiness_gate", "acceptance_write_gate"} <= set(writer_mode_approval_json["writer_gate_check_ids"]), "desktop-pack writer approval JSON missed writer gates")
        assert_true(".novel-agent/proposals.jsonl" in writer_mode_approval_json["write_scope"], "desktop-pack writer approval JSON missed proposal write scope")
        assert_true("Gaoxia frontend" in writer_mode_approval_json["must_not_write"], "desktop-pack writer approval JSON missed main app boundary")
        assert_true(len(writer_mode_approval_json["recommended_flow"]) >= 7, "desktop-pack writer approval JSON missed guarded flow")
        assert_true(writer_mode_approval_json["commands"]["writer_mode_readiness"]["present"] is True, "desktop-pack writer approval JSON missed writer readiness command")
        assert_true(writer_mode_approval_json["commands"]["writer_install_apply"]["writes_install_config"] is True, "desktop-pack writer approval JSON should mark writer install as config write")
        assert_true(writer_mode_approval_json["write_boundaries"]["approval_json_is_read_only"] is True, "desktop-pack writer approval JSON missed read-only boundary")
        assert_true("writer-mode-approval.md" in writer_mode_approval_json["source_files"], "desktop-pack writer approval JSON missed markdown source file")
        assert_true("writer-mode-approval.schema.json" in writer_mode_approval_json["source_files"], "desktop-pack writer approval JSON missed schema source file")
        assert_true(writer_mode_approval_schema["title"] == "Long Novel Agent Writer Mode Approval", "desktop-pack writer approval schema title mismatch")
        assert_true("Client Compatibility" in client_compatibility, "desktop-pack client compatibility missing title")
        assert_true("client-compatibility.json" in client_compatibility, "desktop-pack client compatibility missed structured JSON pointer")
        assert_true("Compatibility Layers" in client_compatibility, "desktop-pack client compatibility missed layers")
        assert_true("Local MCP config status" in client_compatibility, "desktop-pack client compatibility missed local config status")
        assert_true("Real desktop client status" in client_compatibility, "desktop-pack client compatibility missed real client status")
        assert_true("Selected platform/mode status" in client_compatibility, "desktop-pack client compatibility missed selected mode status")
        assert_true("Desktop Matrix Summary" in client_compatibility, "desktop-pack client compatibility missed matrix summary")
        assert_true("Required Check IDs" in client_compatibility, "desktop-pack client compatibility missed required checks")
        assert_true("desktop-verify" in client_compatibility, "desktop-pack client compatibility missed desktop verify command")
        assert_true("desktop-checklist" in client_compatibility, "desktop-pack client compatibility missed desktop checklist command")
        assert_true("desktop-matrix" in client_compatibility, "desktop-pack client compatibility missed desktop matrix command")
        assert_true("record-desktop-check" in client_compatibility, "desktop-pack client compatibility missed record command")
        assert_true("local_mcp_config" in client_compatibility, "desktop-pack client compatibility missed local proof naming")
        assert_true("real_desktop_client" in client_compatibility, "desktop-pack client compatibility missed real client proof naming")
        assert_true("Remote connector-only platforms are outside this local desktop compatibility report" in client_compatibility, "desktop-pack client compatibility missed remote boundary")
        assert_true(client_compatibility_json["type"] == "client_compatibility_report", "desktop-pack client compatibility JSON wrong type")
        assert_true(client_compatibility_json["project_dir"] == str(project.resolve()), "desktop-pack client compatibility JSON missed project dir")
        assert_true(client_compatibility_json["platform"] == "codex", "desktop-pack client compatibility JSON missed platform")
        assert_true(client_compatibility_json["mode"] == "read-only", "desktop-pack client compatibility JSON missed mode")
        assert_true(client_compatibility_json["target_chapter"] == 2, "desktop-pack client compatibility JSON missed target chapter")
        layers = client_compatibility_json.get("compatibility_layers") if isinstance(client_compatibility_json.get("compatibility_layers"), dict) else {}
        assert_true("local_mcp_config" in layers, "desktop-pack client compatibility JSON missed local config layer")
        assert_true("real_desktop_client" in layers, "desktop-pack client compatibility JSON missed real client layer")
        assert_true("selected_platform_mode" in layers, "desktop-pack client compatibility JSON missed selected mode layer")
        assert_true(layers["local_mcp_config"]["proof_level"] == "local_mcp_config", "desktop-pack client compatibility JSON missed local proof level")
        assert_true(layers["local_mcp_config"]["is_real_client_evidence"] is False, "desktop-pack client compatibility JSON local layer must not be real client evidence")
        assert_true(layers["real_desktop_client"]["proof_level"] == "none", "desktop-pack client compatibility JSON should show missing real client proof before GUI check")
        assert_true(layers["selected_platform_mode"]["status"] == "missing", "desktop-pack client compatibility JSON should show missing selected real client evidence")
        assert_true(layers["selected_platform_mode"]["missing_required_check_ids"], "desktop-pack client compatibility JSON should list missing required checks")
        assert_true(client_compatibility_json["required_check_ids"], "desktop-pack client compatibility JSON missed required check IDs")
        recommended = client_compatibility_json.get("recommended_commands") if isinstance(client_compatibility_json.get("recommended_commands"), dict) else {}
        assert_true("desktop-verify" in recommended.get("desktop_verify", ""), "desktop-pack client compatibility JSON missed desktop verify command")
        assert_true("desktop-checklist" in recommended.get("desktop_checklist", ""), "desktop-pack client compatibility JSON missed desktop checklist command")
        assert_true("desktop-matrix" in recommended.get("desktop_matrix", ""), "desktop-pack client compatibility JSON missed desktop matrix command")
        assert_true("record-desktop-check" in recommended.get("record_desktop_check", ""), "desktop-pack client compatibility JSON missed record command")
        assert_true("Remote connector-only platforms are outside this local desktop compatibility report" in client_compatibility_json["remote_boundary"], "desktop-pack client compatibility JSON missed remote boundary")
        assert_true("Local Desktop Troubleshooting" in troubleshooting, "desktop-pack troubleshooting missing title")
        assert_true("Symptom Guide" in troubleshooting, "desktop-pack troubleshooting missed symptom guide")
        assert_true("desktop-verify" in troubleshooting, "desktop-pack troubleshooting missed desktop-verify command")
        assert_true("prepare_chapter_session" in troubleshooting, "desktop-pack troubleshooting missed prepare-session guidance")
        assert_true("source-intake" in troubleshooting, "desktop-pack troubleshooting missed source-intake guidance")
        assert_true("record-desktop-check" in troubleshooting, "desktop-pack troubleshooting missed record guidance")
        assert_true("Read-only and writer mode" in troubleshooting, "desktop-pack troubleshooting missed mode mismatch guidance")
        assert_true("Remote connector-only platforms need a separate wrapper" in troubleshooting, "desktop-pack troubleshooting missed remote boundary")
        assert_true("troubleshooting.json" in troubleshooting, "desktop-pack troubleshooting markdown missed machine-readable guide")
        assert_true("Troubleshooting Dashboard" in troubleshooting_html, "desktop-pack troubleshooting HTML missed title")
        assert_true("Symptom Guide" in troubleshooting_html, "desktop-pack troubleshooting HTML missed symptom guide")
        assert_true("troubleshooting.json" in troubleshooting_html, "desktop-pack troubleshooting HTML missed JSON link")
        assert_true("desktop_troubleshooting" in troubleshooting_html, "desktop-pack troubleshooting HTML missed refresh command key")
        assert_true("record_desktop_check" in troubleshooting_html, "desktop-pack troubleshooting HTML missed read-only evidence boundary")
        assert_true("Remote connector-only platforms" in troubleshooting_html, "desktop-pack troubleshooting HTML missed remote boundary")
        assert_true(troubleshooting_json["type"] == "desktop_pack_troubleshooting", "desktop-pack troubleshooting JSON wrong type")
        assert_true(troubleshooting_json["$schema"] == "troubleshooting.schema.json", "desktop-pack troubleshooting JSON missed local schema")
        assert_true(troubleshooting_json["target_chapter"] == 2, "desktop-pack troubleshooting JSON target mismatch")
        assert_true(troubleshooting_json["local_only"] is True, "desktop-pack troubleshooting JSON should be local only")
        assert_true(troubleshooting_json["requires_server"] is False, "desktop-pack troubleshooting JSON should not require server")
        assert_true(troubleshooting_json["would_write_project_state"] is False, "desktop-pack troubleshooting JSON should be read-only")
        assert_true(troubleshooting_json["would_write_install_config"] is False, "desktop-pack troubleshooting JSON should not write install config")
        assert_true(troubleshooting_json["would_write_pack_files"] is False, "desktop-pack troubleshooting JSON should not write pack files")
        assert_true(troubleshooting_json["records_real_client_evidence"] is False, "desktop-pack troubleshooting JSON should not record real client evidence")
        troubleshooting_symptom_ids = {item.get("id") for item in troubleshooting_json["symptoms"] if isinstance(item, dict)}
        assert_true({"mcp_server_missing", "gui_tools_unavailable", "mode_mismatch", "project_not_initialized", "context_missing_sources", "continuity_check_failed", "result_json_not_recordable", "pack_write_refused", "write_lock_active", "evidence_safety_warning"} <= troubleshooting_symptom_ids, "desktop-pack troubleshooting JSON missed symptom IDs")
        assert_true(troubleshooting_json["write_boundaries"]["troubleshooting_is_read_only"] is True, "desktop-pack troubleshooting JSON missed read-only boundary")
        assert_true("record_desktop_check" in troubleshooting_json["write_boundaries"]["read_only_agents_must_not_execute"], "desktop-pack troubleshooting JSON missed read-only evidence boundary")
        assert_true("troubleshooting.html" in troubleshooting_json["source_files"], "desktop-pack troubleshooting JSON missed HTML source file")
        assert_true(troubleshooting_schema["title"] == "Long Novel Agent Desktop Pack Troubleshooting", "desktop-pack troubleshooting schema title mismatch")
        assert_true("desktop_bootstrap_apply" not in start_here, "desktop-pack START_HERE should show command values, not internal keys")
        assert_true("desktop-bootstrap" in start_here, "desktop-pack START_HERE missed bootstrap command")
        assert_true("quickstart-from-wizard" in start_here, "desktop-pack START_HERE missed wizard command")
        assert_true("writer_mode_readiness" in start_here, "desktop-pack START_HERE missed writer readiness command")
        assert_true("writer_mode_readiness" in next_step, "desktop-pack next-step missed writer readiness command")
        assert_true("Result JSON Viewer" in index_html, "desktop-pack index missed result viewer")
        assert_true("Evidence JSON Checks" in index_html, "desktop-pack index missed evidence JSON checks")
        assert_true("Workflow Status" in index_html, "desktop-pack index missed workflow status panel")
        assert_true("MCP Tool Profile" in index_html, "desktop-pack index missed tool profile panel")
        assert_true("Tools: full" in index_html, "desktop-pack index missed current tool profile")
        assert_true("--tool-profile core" in index_html and "--tool-profile writing" in index_html, "desktop-pack index missed tool profile switch guidance")
        assert_true("This status panel is generated from project-status" in index_html, "desktop-pack index missed workflow status source note")
        assert_true("Next Actions" in index_html, "desktop-pack index missed next actions table")
        assert_true("Author review queue" in index_html, "desktop-pack index missed author review queue workflow tile")
        assert_true("Review author decisions before durable writes" in index_html, "desktop-pack index missed author review queue next action")
        assert_true("Open handoff and chapter session before writing" in index_html, "desktop-pack index missed handoff/chapter next action")
        assert_true("Build the chapter delivery packet" in index_html, "desktop-pack index missed chapter delivery next action")
        assert_true("Collect proof inside the real desktop client" in index_html, "desktop-pack index missed real client evidence next action")
        assert_true("Refresh pack doctor after copying or moving this directory" in index_html, "desktop-pack index missed pack doctor next action")
        assert_true("Pack Index JSON Checks" in index_html, "desktop-pack index missed pack index JSON checks")
        assert_true("validatePackIndexPack" in index_html, "desktop-pack index missed pack index validation function")
        assert_true("packIndexFile" in index_html, "desktop-pack index missed pack index JSON file input")
        assert_true("pack-index.schema.json" in index_html, "desktop-pack index missed pack index schema file")
        assert_true("first-three.html" in index_html and "first-three.json" in index_html, "desktop-pack index missed first three links")
        assert_true("first-three.schema.json" in index_html, "desktop-pack index missed first three schema file")
        assert_true("Long Novel Agent First Three" in index_html, "desktop-pack index missed first three schema payload")
        assert_true("Commands Index JSON Checks" in index_html, "desktop-pack index missed commands index JSON checks")
        assert_true("commands-index.schema.json" in index_html, "desktop-pack index missed commands index schema file")
        assert_true("Long Novel Agent Desktop Pack Commands Index" in index_html, "desktop-pack index missed commands index schema payload")
        assert_true("validateCommandsIndexPack" in index_html, "desktop-pack index missed commands index validation function")
        assert_true("commandsIndexFile" in index_html, "desktop-pack index missed commands index JSON file input")
        assert_true("Local Summary JSON Checks" in index_html, "desktop-pack index missed local summary JSON checks")
        assert_true("local-summary.schema.json" in index_html, "desktop-pack index missed local summary schema file")
        assert_true("Long Novel Agent Local Summary" in index_html, "desktop-pack index missed local summary schema payload")
        assert_true("validateLocalSummaryPack" in index_html, "desktop-pack index missed local summary validation function")
        assert_true("renderLocalSummaryPack" in index_html, "desktop-pack index missed local summary render function")
        assert_true("localSummaryFile" in index_html, "desktop-pack index missed local summary JSON file input")
        assert_true("localSummaryIssues" in index_html, "desktop-pack index missed local summary issues output")
        assert_true("User Steps JSON Checks" in index_html, "desktop-pack index missed user steps JSON checks")
        assert_true("user-steps.schema.json" in index_html, "desktop-pack index missed user steps schema file")
        assert_true("Long Novel Agent User Steps" in index_html, "desktop-pack index missed user steps schema payload")
        assert_true("validateUserStepsPack" in index_html, "desktop-pack index missed user steps validation function")
        assert_true("renderUserStepsPack" in index_html, "desktop-pack index missed user steps render function")
        assert_true("userStepsFile" in index_html, "desktop-pack index missed user steps JSON file input")
        assert_true("Project Wizard JSON Checks" in index_html, "desktop-pack index missed project wizard JSON checks")
        assert_true("Source Intake JSON Checks" in index_html, "desktop-pack index missed source intake JSON checks")
        assert_true("Desktop Next Step JSON Checks" in index_html, "desktop-pack index missed desktop next step JSON checks")
        assert_true("desktop-next-step.schema.json" in index_html, "desktop-pack index missed desktop next step schema file")
        assert_true("Long Novel Agent Desktop Next Step" in index_html, "desktop-pack index missed desktop next step schema payload")
        assert_true("validateDesktopNextStepPack" in index_html, "desktop-pack index missed desktop next step validation function")
        assert_true("Desktop Pack Readiness JSON Checks" in index_html, "desktop-pack index missed desktop pack readiness JSON checks")
        assert_true("desktop-pack-readiness.schema.json" in index_html, "desktop-pack index missed desktop pack readiness schema file")
        assert_true("Long Novel Agent Desktop Pack Readiness" in index_html, "desktop-pack index missed desktop pack readiness schema payload")
        assert_true("validateDesktopPackReadinessPack" in index_html, "desktop-pack index missed desktop pack readiness validation function")
        assert_true("Agent Takeover JSON Checks" in index_html, "desktop-pack index missed agent takeover JSON checks")
        assert_true("agent-takeover.schema.json" in index_html, "desktop-pack index missed agent takeover schema file")
        assert_true("Long Novel Agent Takeover Packet" in index_html, "desktop-pack index missed agent takeover schema payload")
        assert_true("validateAgentTakeoverPack" in index_html, "desktop-pack index missed agent takeover validation function")
        assert_true("renderAgentTakeoverPack" in index_html, "desktop-pack index missed agent takeover render function")
        assert_true("agentTakeoverFile" in index_html, "desktop-pack index missed agent takeover file input")
        assert_true("Author Actions JSON Checks" in index_html, "desktop-pack index missed author actions JSON checks")
        assert_true("author-actions.schema.json" in index_html, "desktop-pack index missed author actions schema file")
        assert_true("Long Novel Agent Author Actions" in index_html, "desktop-pack index missed author actions schema payload")
        assert_true("validateAuthorActionsPack" in index_html, "desktop-pack index missed author actions validation function")
        assert_true("renderAuthorActionsPack" in index_html, "desktop-pack index missed author actions render function")
        assert_true("authorActionsFile" in index_html, "desktop-pack index missed author actions file input")
        assert_true("Desktop Evidence Session JSON Checks" in index_html, "desktop-pack index missed desktop evidence session JSON checks")
        assert_true("desktop-evidence-session.schema.json" in index_html, "desktop-pack index missed desktop evidence session schema file")
        assert_true("Long Novel Agent Desktop Evidence Session" in index_html, "desktop-pack index missed desktop evidence session schema payload")
        assert_true("validateDesktopEvidenceSessionPack" in index_html, "desktop-pack index missed desktop evidence session validation function")
        assert_true("Desktop Evidence Review JSON Checks" in index_html, "desktop-pack index missed desktop evidence review JSON checks")
        assert_true("desktop-evidence-review.html" in index_html, "desktop-pack index missed desktop evidence review HTML file")
        assert_true("desktop-evidence-review.schema.json" in index_html, "desktop-pack index missed desktop evidence review schema file")
        assert_true("Long Novel Agent Desktop Evidence Review" in index_html, "desktop-pack index missed desktop evidence review schema payload")
        assert_true("validateDesktopEvidenceReviewPack" in index_html, "desktop-pack index missed desktop evidence review validation function")
        assert_true("Writer Mode Approval JSON Checks" in index_html, "desktop-pack index missed writer mode approval JSON checks")
        assert_true("writer-mode-approval.schema.json" in index_html, "desktop-pack index missed writer mode approval schema file")
        assert_true("Long Novel Agent Writer Mode Approval" in index_html, "desktop-pack index missed writer mode approval schema payload")
        assert_true("validateWriterModeApprovalPack" in index_html, "desktop-pack index missed writer mode approval validation function")
        assert_true("renderWriterModeApprovalPack" in index_html, "desktop-pack index missed writer mode approval render function")
        assert_true("writerModeApprovalFile" in index_html, "desktop-pack index missed writer mode approval file input")
        assert_true("Chapter Session JSON Checks" in index_html, "desktop-pack index missed chapter session JSON checks")
        assert_true("bundled JSON Schema files plus workflow checks" in index_html, "desktop-pack index missed bundled schema validation note")
        assert_true("schemaRegistry" in index_html, "desktop-pack index missed embedded schema registry")
        assert_true("validateAgainstSchema" in index_html, "desktop-pack index missed local schema validator")
        assert_true("resolveSchemaRef" in index_html, "desktop-pack index missed schema ref resolver")
        assert_true("Long Novel Agent Project Wizard" in index_html, "desktop-pack index missed project wizard schema payload")
        assert_true("Long Novel Agent Source Intake" in index_html, "desktop-pack index missed source intake schema payload")
        assert_true("Long Novel Agent Chapter Session" in index_html, "desktop-pack index missed chapter session schema payload")
        assert_true("Long Novel Agent Handoff Report" in index_html, "desktop-pack index missed handoff report schema payload")
        assert_true("Long Novel Agent Desktop Pack Index" in index_html, "desktop-pack index missed pack index schema payload")
        assert_true("Long Novel Agent Desktop Verification Results" in index_html, "desktop-pack index missed desktop results schema payload")
        assert_true("validateProjectWizard" in index_html, "desktop-pack index missed project wizard validation function")
        assert_true("validateSourceIntakePack" in index_html, "desktop-pack index missed source intake validation function")
        assert_true("validateChapterSessionPack" in index_html, "desktop-pack index missed chapter session validation function")
        assert_true("Handoff Report JSON Checks" in index_html, "desktop-pack index missed handoff report JSON checks")
        assert_true("validateHandoffReportPack" in index_html, "desktop-pack index missed handoff report validation function")
        assert_true("handoffReportFile" in index_html, "desktop-pack index missed handoff report JSON file input")
        assert_true("handoff-report.schema.json" in index_html, "desktop-pack index missed handoff report schema file")
        assert_true("CLI dry-run remains the authoritative check" in index_html, "desktop-pack index missed dry-run authority note")
        assert_true("source-intake dry-run" in index_html, "desktop-pack index missed source intake dry-run guidance")
        assert_true("Regenerate the desktop pack or call prepare-session again" in index_html, "desktop-pack index missed chapter session regeneration guidance")
        assert_true("troubleshooting.json" in index_html, "desktop-pack index missed troubleshooting JSON")
        assert_true("troubleshooting.schema.json" in index_html, "desktop-pack index missed troubleshooting schema")
        assert_true("troubleshootingFile" in index_html, "desktop-pack index missed troubleshooting file input")
        assert_true("validateTroubleshootingPack" in index_html, "desktop-pack index missed troubleshooting validation function")
        assert_true("expectedChecks" in index_html, "desktop-pack index missed expected checklist data")
        assert_true("validateResult" in index_html, "desktop-pack index missed result validation function")
        assert_true("missing_proposal_readiness_evidence" in index_html, "desktop-pack index missed proposal readiness validation")
        assert_true("build_proposal_readiness or proposal-readiness" in index_html, "desktop-pack index missed proposal readiness guidance")
        assert_true("missing_author_confirmation_evidence" in index_html, "desktop-pack index missed writer author confirmation validation")
        assert_true("author_confirmation or --author-confirmation" in index_html, "desktop-pack index missed writer confirmation guidance")
        assert_true("missing_acceptance_write_operation_evidence" in index_html, "desktop-pack index missed writer durable command validation")
        assert_true("durable write command" in index_html, "desktop-pack index missed writer durable command guidance")
        assert_true("evidenceQualityIssue" in index_html, "desktop-pack index missed evidence quality validation function")
        assert_true("weak_evidence" in index_html, "desktop-pack index missed weak evidence validation")
        assert_true("evidenceSafetyIssues" in index_html, "desktop-pack index missed evidence safety validation function")
        assert_true("evidence_safety_warning" in index_html, "desktop-pack index missed evidence safety warning code")
        assert_true("Remove it before recording" in index_html, "desktop-pack index missed secret evidence warning guidance")
        assert_true("short proof snippets" in index_html, "desktop-pack index missed manuscript evidence warning guidance")
        assert_true("escapeHtml" in index_html, "desktop-pack index should escape loaded result JSON issue text")
        assert_true("pack-index.json" in index_html, "desktop-pack index missed pack index file")
        assert_true("START_HERE.md" in index_html, "desktop-pack index missed start file")
        assert_true("next-step.md" in index_html, "desktop-pack index missed next-step file")
        assert_true("quick-install.md" in index_html, "desktop-pack index missed quick install file")
        assert_true("install-local.md" in index_html, "desktop-pack index missed local install file")
        assert_true("install-local.sh" in index_html, "desktop-pack index missed local install script")
        assert_true("install-local.ps1" in index_html, "desktop-pack index missed PowerShell install wrapper")
        assert_true("install-local.cmd" in index_html, "desktop-pack index missed Windows install wrapper")
        assert_true("install-local.command" in index_html, "desktop-pack index missed macOS install launcher")
        assert_true("upgrade-local.md" in index_html, "desktop-pack index missed local upgrade file")
        assert_true("upgrade-local.sh" in index_html, "desktop-pack index missed local upgrade script")
        assert_true("upgrade-local.ps1" in index_html, "desktop-pack index missed PowerShell upgrade wrapper")
        assert_true("upgrade-local.cmd" in index_html, "desktop-pack index missed Windows upgrade wrapper")
        assert_true("upgrade-local.command" in index_html, "desktop-pack index missed macOS upgrade launcher")
        assert_true("uninstall-local.md" in index_html, "desktop-pack index missed local uninstall file")
        assert_true("uninstall-local.sh" in index_html, "desktop-pack index missed local uninstall script")
        assert_true("uninstall-local.ps1" in index_html, "desktop-pack index missed PowerShell uninstall wrapper")
        assert_true("uninstall-local.cmd" in index_html, "desktop-pack index missed Windows uninstall wrapper")
        assert_true("uninstall-local.command" in index_html, "desktop-pack index missed macOS uninstall launcher")
        assert_true("local-checks.md" in index_html, "desktop-pack index missed local checks file")
        assert_true("local-checks.sh" in index_html, "desktop-pack index missed local checks script")
        assert_true("local-checks.ps1" in index_html, "desktop-pack index missed PowerShell checks wrapper")
        assert_true("local-checks.cmd" in index_html, "desktop-pack index missed Windows checks wrapper")
        assert_true("local-checks.command" in index_html, "desktop-pack index missed macOS checks launcher")
        assert_true("archive-local.md" in index_html, "desktop-pack index missed local archive file")
        assert_true("archive-local.sh" in index_html, "desktop-pack index missed local archive script")
        assert_true("archive-local.ps1" in index_html, "desktop-pack index missed PowerShell archive wrapper")
        assert_true("archive-local.cmd" in index_html, "desktop-pack index missed Windows archive wrapper")
        assert_true("archive-local.command" in index_html, "desktop-pack index missed macOS archive launcher")
        assert_true("writer-mode-approval.md" in index_html, "desktop-pack index missed writer approval file")
        assert_true("writer-mode-approval.json" in index_html, "desktop-pack index missed writer approval JSON file")
        assert_true("writer-mode-approval.schema.json" in index_html, "desktop-pack index missed writer approval schema file")
        assert_true("client-compatibility.md" in index_html, "desktop-pack index missed client compatibility file")
        assert_true("client-compatibility.json" in index_html, "desktop-pack index missed client compatibility JSON file")
        assert_true("commands-index.md" in index_html, "desktop-pack index missed commands index markdown file")
        assert_true("commands-index.json" in index_html, "desktop-pack index missed commands index JSON file")
        assert_true("commands-index.schema.json" in index_html, "desktop-pack index missed commands index schema file")
        assert_true("commands.json" in index_html, "desktop-pack index missed structured commands file")
        assert_true("commands.schema.json" in index_html, "desktop-pack index missed commands schema file")
        assert_true("user-steps.schema.json" in index_html, "desktop-pack index missed user steps schema file")
        assert_true("project-wizard.schema.json" in index_html, "desktop-pack index missed project wizard schema file")
        assert_true("source-intake.schema.json" in index_html, "desktop-pack index missed source intake schema file")
        assert_true("desktop-next-step.schema.json" in index_html, "desktop-pack index missed desktop next step schema file")
        assert_true("desktop-pack-readiness.schema.json" in index_html, "desktop-pack index missed desktop pack readiness schema file")
        assert_true("agent-startup-prompt.json" in index_html, "desktop-pack index missed agent startup prompt JSON file")
        assert_true("agent-startup-prompt.schema.json" in index_html, "desktop-pack index missed agent startup prompt schema file")
        assert_true("agentStartupPromptFile" in index_html, "desktop-pack index missed agent startup prompt JSON input")
        assert_true("desktop-evidence-session.schema.json" in index_html, "desktop-pack index missed desktop evidence session schema file")
        assert_true("desktop-evidence-review.html" in index_html, "desktop-pack index missed desktop evidence review HTML file")
        assert_true("desktop-evidence-review.schema.json" in index_html, "desktop-pack index missed desktop evidence review schema file")
        assert_true("writer-mode-approval.schema.json" in index_html, "desktop-pack index missed writer mode approval schema file")
        assert_true("chapter-session.schema.json" in index_html, "desktop-pack index missed chapter session schema file")
        assert_true("handoff-report.schema.json" in index_html, "desktop-pack index missed handoff report schema file")
        assert_true("desktop-results.schema.json" in index_html, "desktop-pack index missed desktop results schema file")
        assert_true("troubleshooting.md" in index_html, "desktop-pack index missed troubleshooting file")
        assert_true("project-status.json" in index_html, "desktop-pack index missed project status JSON file")
        assert_true("desktop-matrix.json" in index_html, "desktop-pack index missed desktop matrix JSON file")
        assert_true("continuity-audit.md" in index_html, "desktop-pack index missed continuity audit file")
        assert_true("continuity-audit.json" in index_html, "desktop-pack index missed continuity audit JSON file")
        assert_true("author-review-queue.md" in index_html, "desktop-pack index missed author review queue file")
        assert_true("author-review-queue.json" in index_html, "desktop-pack index missed author review queue JSON file")
        assert_true("handoff-report.json" in index_html, "desktop-pack index missed handoff report JSON file")
        assert_true("chapter-session.md" in index_html, "desktop-pack index missed chapter session file")
        assert_true("chapter-session.json" in index_html, "desktop-pack index missed chapter session JSON file")
        assert_true("chapterSessionFile" in index_html, "desktop-pack index missed chapter session JSON file input")
        assert_true("real-client-walkthrough.md" in index_html, "desktop-pack index missed real client walkthrough file")
        assert_true("client-prompts.md" in index_html, "desktop-pack index missed client prompts file")
        assert_true("result-json-guide.md" in index_html, "desktop-pack index missed result JSON guide file")
        assert_true("evidence-to-results.md" in index_html, "desktop-pack index missed evidence-to-results file")
        assert_true("platforms/README.md" in index_html, "desktop-pack index missed platform guide")
        assert_true("agent-start.md" in index_html, "desktop-pack index missed agent start file")
        assert_true("agent-start-codex.md" in index_html, "desktop-pack index missed Codex agent start file")
        assert_true("agent-start-cursor.md" in index_html, "desktop-pack index missed Cursor agent start file")
        assert_true("agent-start-claude-desktop.md" in index_html, "desktop-pack index missed Claude agent start file")
        assert_true("agent-start-generic.md" in index_html, "desktop-pack index missed generic agent start file")
        assert_true("source-intake-review.html" in index_html, "desktop-pack index missed source intake review page")
        assert_true("pack-manifest.json" in index_html, "desktop-pack index missed pack manifest file")
        assert_true("verify-pack.py" in index_html, "desktop-pack index missed verify pack script")
        assert_true("prepare_chapter_session" in agent_start, "desktop-pack agent start missed prepare-session gate")
        assert_true("build_chapter_context" in agent_start, "desktop-pack agent start missed context gate")
        assert_true("agent-startup-prompt.md" in agent_start and "agent-startup-prompt.json" in agent_start, "desktop-pack agent start missed agent startup prompt files")
        assert_true("agent_startup_prompt" in agent_start, "desktop-pack agent start missed agent startup prompt command")
        assert_true("project-status.json" in agent_start, "desktop-pack agent start missed project status JSON file")
        assert_true("project-status.json" in client_prompts, "desktop-pack client prompts missed project status JSON file")
        assert_true("continuity-audit.md" in agent_start, "desktop-pack agent start missed continuity audit file")
        assert_true("continuity-audit.json" in agent_start, "desktop-pack agent start missed continuity audit JSON file")
        assert_true("author-review-queue.md" in agent_start, "desktop-pack agent start missed author review queue file")
        assert_true("author-review-queue.json" in agent_start, "desktop-pack agent start missed author review queue JSON file")
        assert_true("chapter-session.md" in agent_start, "desktop-pack agent start missed chapter session file")
        assert_true(project_status_json["type"] == "project_status", "desktop-pack project status JSON wrong type")
        assert_true(project_status_json["readiness"] in {"ready", "ready_with_warnings", "needs_review", "blocked", "not_initialized"}, "desktop-pack project status JSON wrong readiness")
        assert_true(isinstance(project_status_json.get("recommended_commands"), list), "desktop-pack project status JSON missed recommended commands")
        assert_true(continuity_audit_json["type"] == "continuity_audit", "desktop-pack continuity audit JSON wrong type")
        assert_true(continuity_audit_json["status"] in {"ready", "ready_with_warnings", "needs_review", "blocked"}, "desktop-pack continuity audit JSON wrong status")
        assert_true(isinstance(continuity_audit_json.get("action_plan"), dict), "desktop-pack continuity audit JSON missed action plan")
        assert_true("Long Novel Agent Author Review Queue" in author_review_queue, "desktop-pack author review queue markdown missing title")
        assert_true(author_review_queue_json["type"] == "author_review_queue", "desktop-pack author review queue JSON wrong type")
        assert_true(author_review_queue_json["status"] in {"clear", "ready_with_warnings", "needs_author_review", "blocked"}, "desktop-pack author review queue JSON wrong status")
        assert_true(isinstance(author_review_queue_json.get("summary"), dict), "desktop-pack author review queue JSON missed summary")
        assert_true(handoff_report_json["type"] == "handoff_report", "desktop-pack handoff report JSON wrong type")
        assert_true(handoff_report_json["$schema"] == "handoff-report.schema.json", "desktop-pack handoff report JSON should point to local schema")
        assert_true(int(handoff_report_json.get("target_chapter") or 0) == 2, "desktop-pack handoff report JSON wrong target chapter")
        assert_true(handoff_report_json.get("read_only") is True, "desktop-pack handoff report JSON should be read-only")
        assert_true(handoff_report_schema["title"] == "Long Novel Agent Handoff Report", "desktop-pack handoff report schema wrong title")
        assert_true("desktop-matrix.md" in agent_start, "desktop-pack agent start missed desktop matrix file")
        assert_true("desktop-matrix.json" in agent_start, "desktop-pack agent start missed desktop matrix JSON file")
        assert_true("handoff-report.json" in agent_start, "desktop-pack agent start missed handoff report JSON file")
        assert_true("handoff-report.json" in client_prompts, "desktop-pack client prompts missed handoff report JSON file")
        assert_true("desktop-matrix.json" in client_prompts, "desktop-pack client prompts missed desktop matrix JSON file")
        assert_true("recent_agent_activity" in agent_start, "desktop-pack agent start missed recent activity handoff")
        assert_true("list_agent_activity" in agent_start, "desktop-pack agent start missed list activity command")
        assert_true("agent_activity_report" in agent_start, "desktop-pack agent start missed agent activity report command")
        assert_true("handoff_integrity" in agent_start, "desktop-pack agent start missed handoff integrity command")
        assert_true("handoff_range_report" in agent_start, "desktop-pack agent start missed handoff range report command")
        assert_true("handoff_readiness" in agent_start, "desktop-pack agent start missed handoff readiness command")
        assert_true("handoff_report" in agent_start, "desktop-pack agent start missed handoff report command")
        assert_true("record_agent_activity" in agent_start, "desktop-pack agent start missed record activity command")
        assert_true("Long Novel Agent Continuity Audit" in continuity_audit, "desktop-pack continuity audit missing title")
        assert_true("## Action Plan" in continuity_audit, "desktop-pack continuity audit missing action plan")
        assert_true("Long Novel Agent Chapter Session" in chapter_session, "desktop-pack chapter session markdown missing title")
        assert_true("Target chapter: 2" in chapter_session, "desktop-pack chapter session markdown missed target chapter")
        assert_true("Before Draft" in chapter_session, "desktop-pack chapter session markdown missed before-draft gate")
        assert_true("Real Desktop Client Walkthrough" in real_client_walkthrough, "desktop-pack real client walkthrough missing title")
        assert_true("In The Desktop Client" in real_client_walkthrough, "desktop-pack real client walkthrough missed client steps")
        assert_true("Check-By-Check Script" in real_client_walkthrough, "desktop-pack real client walkthrough missed checklist script")
        assert_true("prepare_chapter_session" in real_client_walkthrough, "desktop-pack real client walkthrough missed prepare-session call")
        assert_true("read-only mode" in real_client_walkthrough, "desktop-pack real client walkthrough missed read-only boundary")
        assert_true("desktop-results-doctor" in real_client_walkthrough, "desktop-pack real client walkthrough missed results doctor command")
        assert_true("record-desktop-check" in real_client_walkthrough, "desktop-pack real client walkthrough missed record command")
        assert_true("Desktop Client Prompts" in client_prompts, "desktop-pack client prompts missing title")
        assert_true("Copy These Prompts" in client_prompts, "desktop-pack client prompts missed prompt section")
        assert_true("prepare_chapter_session" in client_prompts, "desktop-pack client prompts missed prepare-session prompt")
        assert_true("build_chapter_context" in client_prompts, "desktop-pack client prompts missed build-context prompt")
        assert_true("recent_agent_activity" in client_prompts, "desktop-pack client prompts missed recent activity prompt")
        assert_true("list_agent_activity" in client_prompts, "desktop-pack client prompts missed list activity helper")
        assert_true("agent_activity_report" in client_prompts, "desktop-pack client prompts missed agent activity report helper")
        assert_true("handoff_integrity" in client_prompts, "desktop-pack client prompts missed handoff integrity helper")
        assert_true("handoff_range_report" in client_prompts, "desktop-pack client prompts missed handoff range helper")
        assert_true("handoff_readiness" in client_prompts, "desktop-pack client prompts missed handoff readiness helper")
        assert_true("handoff_report" in client_prompts, "desktop-pack client prompts missed handoff report helper")
        assert_true("record_agent_activity" in client_prompts, "desktop-pack client prompts missed record activity helper")
        assert_true("Evidence Summary Prompt" in client_prompts, "desktop-pack client prompts missed evidence summary prompt")
        assert_true(pack["result_file"] in client_prompts, "desktop-pack client prompts missed result file")
        assert_true("Do not include API keys" in client_prompts, "desktop-pack client prompts missed evidence safety instruction")
        assert_true("desktop_results_doctor" in client_prompts, "desktop-pack client prompts missed results doctor helper")
        assert_true("desktop_evidence_packet" in client_prompts or "desktop-evidence-packet" in client_prompts, "desktop-pack client prompts missed evidence packet helper")
        assert_true("Result JSON Guide" in result_json_guide, "desktop-pack result JSON guide missing title")
        assert_true(pack["result_file"] in result_json_guide, "desktop-pack result JSON guide missed result file")
        assert_true("desktop-results.schema.json" in result_json_guide, "desktop-pack result JSON guide missed results schema file")
        assert_true("Allowed Status Values" in result_json_guide, "desktop-pack result JSON guide missed statuses")
        assert_true("Required Check IDs" in result_json_guide, "desktop-pack result JSON guide missed required IDs")
        assert_true("Do not paste API keys" in result_json_guide, "desktop-pack result JSON guide missed evidence safety rule")
        assert_true("desktop_results_doctor" in result_json_guide and "ready_to_record" in result_json_guide, "desktop-pack result JSON guide missed results doctor")
        assert_true("record-desktop-check" in result_json_guide, "desktop-pack result JSON guide missed record command")
        assert_true("Evidence To Results" in evidence_to_results, "desktop-pack evidence-to-results missing title")
        assert_true("desktop-evidence.txt" in evidence_to_results, "desktop-pack evidence-to-results missed scratchpad")
        assert_true("ingest-desktop-evidence" in evidence_to_results, "desktop-pack evidence-to-results missed ingest command")
        assert_true("desktop-evidence-packet" in evidence_to_results, "desktop-pack evidence-to-results missed evidence packet command")
        assert_true("imported-" in evidence_to_results, "desktop-pack evidence-to-results missed imported result target")
        assert_true("matched_check_ids" in evidence_to_results, "desktop-pack evidence-to-results missed match review guidance")
        assert_true("missing_required_check_ids" in evidence_to_results, "desktop-pack evidence-to-results missed missing required guidance")
        assert_true("warnings" in evidence_to_results, "desktop-pack evidence-to-results missed warning guidance")
        assert_true("Does not write `.novel-agent/desktop_verifications.jsonl`" in evidence_to_results, "desktop-pack evidence-to-results missed durable write boundary")
        assert_true("imported_desktop_results_doctor" in evidence_to_results, "desktop-pack evidence-to-results missed imported results doctor")
        assert_true("record-desktop-check" in evidence_to_results, "desktop-pack evidence-to-results missed record command")
        assert_true(chapter_session_json["type"] == "chapter_session_packet", "desktop-pack chapter session JSON wrong type")
        assert_true(chapter_session_json["target_chapter"] == 2, "desktop-pack chapter session JSON target mismatch")
        assert_true(isinstance(chapter_session_json.get("project_state_fingerprint"), dict), "desktop-pack chapter session JSON missed project state fingerprint")
        assert_true(isinstance(chapter_session_json.get("project_identity"), dict), "desktop-pack chapter session JSON missed project identity")
        assert_true(isinstance(chapter_session_json.get("chapter_context_fingerprint"), dict), "desktop-pack chapter session JSON missed chapter context fingerprint")
        assert_true(chapter_session_json["project_state_fingerprint"].get("sha256") == pack_freshness_cli["baseline"]["sha256"], "desktop-pack chapter session fingerprint should match pack freshness baseline")
        assert_true("chapter_context" in chapter_session_json, "desktop-pack chapter session JSON missed chapter context")
        assert_true(chapter_session_json["tool_gates"]["before_draft"], "desktop-pack chapter session JSON missed before-draft gates")
        assert_true(chapter_session_json["tool_gates"]["before_ready"], "desktop-pack chapter session JSON missed before-ready gates")
        assert_true("Codex" in agent_start_codex and "Platform Workflow" in agent_start_codex, "desktop-pack Codex agent start missed platform workflow")
        assert_true("Cursor" in agent_start_cursor and "Platform Workflow" in agent_start_cursor, "desktop-pack Cursor agent start missed platform workflow")
        assert_true("Claude Desktop" in agent_start_claude and "explicitly" in agent_start_claude, "desktop-pack Claude agent start missed config guidance")
        assert_true("Generic JSON MCP Client" in agent_start_generic and "remote connector-only" in agent_start_generic, "desktop-pack generic agent start missed boundary guidance")
        assert_true("Long Novel Agent Source Intake" in source_intake_md, "desktop-pack source intake markdown missing title")
        assert_true("source-intake" in source_intake_md, "desktop-pack source intake markdown missed command")
        assert_true("source-intake.schema.json" in source_intake_md, "desktop-pack source intake markdown missed schema file")
        assert_true("quality level" in source_intake_md, "desktop-pack source intake markdown missed quality guidance")
        assert_true("Long Novel Agent Source Intake Review" in source_intake_review, "desktop-pack source intake review missing title")
        assert_true("This page reads the selected file in your browser only" in source_intake_review, "desktop-pack source intake review missed local-only note")
        assert_true("source-intake" in source_intake_review, "desktop-pack source intake review missed command")
        assert_true("bundledTemplate" in source_intake_review, "desktop-pack source intake review missed bundled template")
        assert_true("sourceQuality" in source_intake_review, "desktop-pack source intake review missed quality preflight")
        assert_true("needs_revision" in index_html, "desktop-pack index missed source intake quality warning")
        assert_true(source_intake_json["type"] == "source_intake", "desktop-pack source intake JSON missed type")
        assert_true(source_intake_json["project_dir"] == str(project.resolve()), "desktop-pack source intake JSON missed project dir")
        assert_true("Platform Guides" in platform_index, "desktop-pack platform index missing title")
        assert_true("[Codex](codex.md)" in platform_index, "desktop-pack platform index missed Codex guide")
        assert_true("selected by this pack" in platform_index, "desktop-pack platform index missed selected marker")
        assert_true("Codex Local Setup" in codex_guide, "desktop-pack Codex guide missing title")
        assert_true(str(config) in codex_guide, "desktop-pack Codex guide missed selected config path")
        assert_true("mcp-configs/codex-read-only.toml" in codex_guide, "desktop-pack Codex guide missed snippet path")
        assert_true("Cursor Local Setup" in cursor_guide, "desktop-pack Cursor guide missing title")
        assert_true("mcp-configs/cursor-read-only.json" in cursor_guide, "desktop-pack Cursor guide missed snippet path")
        assert_true("Claude Desktop Local Setup" in claude_guide, "desktop-pack Claude guide missing title")
        assert_true("explicit config path" in claude_guide, "desktop-pack Claude guide missed explicit path warning")
        assert_true("Generic JSON MCP Client Local Setup" in generic_guide, "desktop-pack generic guide missing title")
        assert_true("Remote connector-only platforms" in generic_guide, "desktop-pack generic guide missed remote connector boundary")
        assert_true("quickstart-from-wizard" in project_wizard, "desktop-pack wizard guide missed quickstart command")
        assert_true("project-wizard.schema.json" in project_wizard, "desktop-pack wizard guide missed schema file")
        assert_true(project_wizard_json["project_dir"] == str(project.resolve()), "desktop-pack wizard JSON missed project dir")
        assert_true(project_wizard_json["agent"]["platform"] == "codex", "desktop-pack wizard JSON missed platform")
        assert_true(project_wizard_json["agent"]["mode"] == "read-only", "desktop-pack wizard JSON missed mode")
        assert_true("Local MCP Config Snippets" in config_readme, "desktop-pack config README missing title")
        assert_true(str(target.resolve() / "long-novel-agent" / "server.py") in codex_config, "desktop-pack Codex config missed server path")
        assert_true("--read-only" in codex_config, "desktop-pack Codex read-only config missed flag")
        cursor_args = cursor_config["mcpServers"]["long-novel-agent"]["args"]
        claude_writer_args = claude_writer_config["mcpServers"]["long-novel-agent"]["args"]
        assert_true("--read-only" in cursor_args, "desktop-pack Cursor read-only config missed flag")
        assert_true("--read-only" not in claude_writer_args, "desktop-pack Claude writer config should expose writer mode")
        wizard_report = load_stdout(
            run_cli(
                "quickstart-from-wizard",
                str(project),
                "--answers",
                str(output_dir / "project-wizard.json"),
                "--format",
                "json",
            )
        )
        assert_true(wizard_report["type"] == "quickstart_from_wizard_report", "quickstart-from-wizard returned wrong type")
        assert_true(wizard_report["dry_run"], "quickstart-from-wizard should dry-run by default")
        assert_true(wizard_report["quickstart"]["dry_run"], "quickstart-from-wizard should call quickstart dry-run")
        wizard_markdown = run_cli(
            "quickstart-from-wizard",
            str(project),
            "--answers",
            str(output_dir / "project-wizard.json"),
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Quickstart From Wizard" in wizard_markdown.stdout, "quickstart-from-wizard markdown missing title")
        source_template = load_stdout(run_cli("source-intake-template", str(project), "--platform", "codex", "--mode", "read-only", "--format", "json"))
        assert_true(source_template["type"] == "source_intake_template", "source-intake-template returned wrong type")
        assert_true(source_template["template"]["type"] == "source_intake", "source-intake-template missed intake template")
        assert_true(source_template["template"]["project_dir"] == str(project.resolve()), "source-intake-template missed project dir")
        source_template_markdown = run_cli("source-intake-template", str(project), "--platform", "codex", "--mode", "read-only", "--format", "markdown")
        assert_true("Long Novel Agent Source Intake Template" in source_template_markdown.stdout, "source-intake-template markdown missing title")
        source_intake_json["sources"][0].update(
            {
                "enabled": True,
                "title": "Agent parsed old draft",
                "source_path": "materials/old-draft.txt",
                "summary": "旧稿确认林追持有铜钥匙，第二章前不能交给白石商会。",
                "chapter_range": "1-2",
                "required_phrases": ["铜钥匙"],
                "forbidden_phrases": ["铜钥匙交给白石商会"],
            }
        )
        source_intake_json["research"][0].update(
            {
                "enabled": True,
                "title": "Harbor tides",
                "url": "https://example.com/tides",
                "summary": "潮汐资料只用于第二章码头时间感，不改变主线设定。",
                "chapter_range": "2",
                "reliability": "medium",
            }
        )
        source_intake_json["conflicts"][0].update(
            {
                "enabled": True,
                "conflict": "旧稿写铜钥匙已交给白石商会，但新设定要求林追仍持有。",
                "chosen": "林追仍持有铜钥匙",
                "reason": "作者确认以新设定为准。",
                "options": ["铜钥匙已交给白石商会", "林追仍持有铜钥匙"],
                "chapter_range": "1+",
            }
        )
        source_intake_json["facts"][0].update(
            {
                "enabled": True,
                "kind": "prop",
                "subject": "铜钥匙",
                "predicate": "holder",
                "object": "林追",
                "chapter_range": "1+",
                "source_path": "materials/old-draft.txt",
                "forbidden_phrases": ["铜钥匙交给白石商会"],
            }
        )
        source_intake_json_path.write_text(json.dumps(source_intake_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        source_dry_run = load_stdout(
            run_cli(
                "source-intake",
                str(project),
                "--intake",
                str(source_intake_json_path),
                "--format",
                "json",
            )
        )
        assert_true(source_dry_run["type"] == "source_intake_report", "source-intake returned wrong type")
        assert_true(source_dry_run["ok"], "source-intake dry-run should validate filled template")
        assert_true(not source_dry_run["apply"], "source-intake should dry-run by default")
        assert_true(source_dry_run["validation"]["planned"]["sources"] == 1, "source-intake missed source count")
        assert_true(source_dry_run["validation"]["warning_count"] == 0, "source-intake should not warn for complete filled template")
        assert_true(source_dry_run["validation"]["quality"]["level"] == "ready", "source-intake complete template should be ready quality")
        assert_true(source_dry_run["validation"]["quality"]["score"] == 100, "source-intake complete template should score 100")
        source_markdown = run_cli(
            "source-intake",
            str(project),
            "--intake",
            str(source_intake_json_path),
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Source Intake Report" in source_markdown.stdout, "source-intake markdown missing title")
        assert_true("## Quality" in source_markdown.stdout, "source-intake markdown missing quality section")
        assert_true("Level: ready" in source_markdown.stdout, "source-intake markdown missed ready quality")
        assert_true("## Warnings" in source_markdown.stdout, "source-intake markdown missing warnings section")
        low_quality_source_intake_path = output_dir / "source-intake-low-quality.json"
        low_quality_source_intake = json.loads(json.dumps(source_intake_json, ensure_ascii=False))
        low_quality_source_intake["sources"].append(
            {
                "enabled": True,
                "title": "Duplicate source",
                "source_path": "materials/old-draft.txt",
                "summary": "旧稿确认林追持有铜钥匙，第二章前不能交给白石商会。",
                "chapter_range": "",
                "credibility": "",
            }
        )
        low_quality_source_intake["research"].append(
            {
                "enabled": True,
                "title": "Unbounded research",
                "summary": "缺少 URL 和可靠性说明的考据摘要。",
                "chapter_range": "",
                "reliability": "unrated",
            }
        )
        low_quality_source_intake["facts"].append(
            {
                "enabled": True,
                "kind": "prop",
                "subject": "铜钥匙",
                "predicate": "holder",
                "object": "林追",
                "chapter_range": "",
                "confidence": "",
            }
        )
        low_quality_source_intake_path.write_text(json.dumps(low_quality_source_intake, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        low_quality_dry_run = load_stdout(
            run_cli(
                "source-intake",
                str(project),
                "--intake",
                str(low_quality_source_intake_path),
                "--format",
                "json",
            )
        )
        warning_codes = {warning.get("code") for warning in low_quality_dry_run["validation"]["warnings"]}
        assert_true(low_quality_dry_run["ok"], "source-intake warnings should not fail dry-run")
        assert_true(low_quality_dry_run["validation"]["quality"]["level"] == "needs_revision", "source-intake low-quality template should need revision")
        assert_true(low_quality_dry_run["validation"]["quality"]["risk_counts"]["high"] >= 1, "source-intake low-quality template missed high-risk warning count")
        assert_true("duplicate_source_path" in warning_codes, "source-intake missed duplicate source path warning")
        assert_true("duplicate_fact" in warning_codes, "source-intake missed duplicate fact warning")
        assert_true("missing_chapter_boundary" in warning_codes, "source-intake missed chapter boundary warning")
        assert_true("missing_research_url" in warning_codes, "source-intake missed research URL warning")
        assert_true("missing_fact_source" in warning_codes, "source-intake missed fact source warning")
        low_quality_project = Path(tmp) / "low quality target"
        run_cli("init", str(low_quality_project), "--title", "Low Quality Target")
        low_quality_rejected = run_cli(
            "source-intake",
            str(low_quality_project),
            "--intake",
            str(low_quality_source_intake_path),
            "--apply",
            "--format",
            "json",
            check=False,
        )
        assert_true(low_quality_rejected.returncode != 0, "source-intake should reject low-quality apply by default")
        assert_true("Source intake quality is needs_revision" in low_quality_rejected.stderr, "source-intake low-quality rejection missed quality message")
        low_quality_applied = load_stdout(
            run_cli(
                "source-intake",
                str(low_quality_project),
                "--intake",
                str(low_quality_source_intake_path),
                "--apply",
                "--allow-low-quality",
                "--format",
                "json",
            )
        )
        assert_true(low_quality_applied["quality_override"]["allowed"], "source-intake low-quality override should be recorded")
        assert_true(low_quality_applied["validation"]["quality"]["level"] == "needs_revision", "source-intake low-quality override missed quality level")
        invalid_source_intake_path = output_dir / "source-intake-invalid.json"
        invalid_source_intake = json.loads(json.dumps(source_intake_json, ensure_ascii=False))
        invalid_source_intake["facts"][0]["reveal_after_chapter"] = "later"
        invalid_source_intake_path.write_text(json.dumps(invalid_source_intake, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        invalid_source_dry_run = load_stdout(
            run_cli(
                "source-intake",
                str(project),
                "--intake",
                str(invalid_source_intake_path),
                "--format",
                "json",
            )
        )
        assert_true(not invalid_source_dry_run["ok"], "source-intake should reject invalid chapter numbers")
        assert_true(
            any(issue.get("path") == "facts[0].reveal_after_chapter" for issue in invalid_source_dry_run["validation"]["issues"]),
            "source-intake invalid chapter issue missing",
        )
        applied_intake = load_stdout(
            run_cli(
                "source-intake",
                str(project),
                "--intake",
                str(source_intake_json_path),
                "--apply",
                "--format",
                "json",
            )
        )
        assert_true(applied_intake["applied_counts"]["sources"] == 1, "source-intake did not apply source")
        assert_true(applied_intake["applied_counts"]["research"] == 1, "source-intake did not apply research")
        assert_true(applied_intake["applied_counts"]["conflicts"] == 1, "source-intake did not apply conflict")
        assert_true(applied_intake["applied_counts"]["facts"] == 1, "source-intake did not apply fact")
        context_after_intake = load_stdout(run_cli("build-context", str(project), "--chapter", "2"))
        assert_true(any("旧稿确认林追持有铜钥匙" in row.get("summary", "") for row in context_after_intake["sources"]), "source-intake source not visible in context")
        assert_true(any(row.get("subject") == "铜钥匙" and row.get("object") == "林追" for row in context_after_intake["facts"]), "source-intake fact not visible in context")
        assert_true("desktop-bootstrap" in commands, "desktop-pack commands missed bootstrap")
        commands_schema_summary = verify_desktop_commands_schema_payload(commands_json, commands_schema)
        commands_index_schema_summary = verify_desktop_commands_index_schema_payload(commands_index_json, commands_index_schema, commands_json)
        assert_true(commands_json["type"] == "desktop_pack_commands", "desktop-pack commands JSON wrong type")
        assert_true(commands_json["project_dir"] == str(project.resolve()), "desktop-pack commands JSON missed project dir")
        assert_true(commands_json["platform"] == "codex", "desktop-pack commands JSON missed platform")
        assert_true(commands_json["mode"] == "read-only", "desktop-pack commands JSON missed mode")
        assert_true(commands_json["target_chapter"] == 2, "desktop-pack commands JSON missed target chapter")
        assert_true(commands_json["command_count"] > 0, "desktop-pack commands JSON missed command count")
        command_groups = {group.get("id"): group for group in commands_json.get("groups", []) if isinstance(group, dict)}
        for group_id in [
            "open_pack",
            "pack_integrity",
            "local_install",
            "project_setup",
            "status_and_start",
            "handoff",
            "draft_review",
            "desktop_evidence",
            "writer_mode",
        ]:
            assert_true(group_id in command_groups, f"desktop-pack commands JSON missed group {group_id}")
        assert_true("local_summary" in command_groups["open_pack"]["command_keys"], "local_summary command should be in open_pack group")
        assert_true("desktop_pack_readiness" in command_groups["pack_integrity"]["command_keys"], "desktop-pack readiness command should be in pack_integrity group")
        assert_true("local_summary" in command_groups["pack_integrity"]["command_keys"] or "local_summary" in command_groups["open_pack"]["command_keys"], "local_summary command should be grouped")
        assert_true("pack_freshness" in command_groups["pack_integrity"]["command_keys"], "pack freshness command should be in pack_integrity group")
        assert_true("agent_startup_prompt" in command_groups["status_and_start"]["command_keys"], "agent startup prompt command should be in status_and_start group")
        assert_true("chapter_session_freshness" in command_groups["status_and_start"]["command_keys"], "chapter session freshness command should be in status_and_start group")
        assert_true("write_session_check" in command_groups["status_and_start"]["command_keys"], "write_session_check should be in status_and_start group")
        assert_true("author_actions" in command_groups["status_and_start"]["command_keys"], "author actions command should be in status_and_start group")
        assert_true("acceptance_review" in command_groups["draft_review"]["command_keys"], "acceptance review command should be in draft_review group")
        assert_true("tool_visibility" in command_groups["status_and_start"]["command_keys"], "tool_visibility should be in status_and_start group")
        assert_true("prepare_session" in command_groups["status_and_start"]["command_keys"], "prepare_session should be in status_and_start group")
        assert_true("desktop_evidence_review" in command_groups["desktop_evidence"]["command_keys"], "desktop_evidence_review should be in desktop_evidence group")
        assert_true("desktop_evidence_gaps" in command_groups["desktop_evidence"]["command_keys"], "desktop_evidence_gaps should be in desktop_evidence group")
        assert_true("handoff_integrity" in command_groups["handoff"]["command_keys"], "handoff_integrity should be in handoff group")
        assert_true(commands_json["write_scope_counts"]["read_only"] > commands_json["write_scope_counts"]["writes_project_state"], "commands JSON should mark most commands read-only")
        assert_true(commands_index_json["type"] == "desktop_pack_commands_index", "desktop-pack commands index JSON wrong type")
        assert_true(commands_index_json["$schema"] == "commands-index.schema.json", "desktop-pack commands index JSON missed local schema")
        assert_true(commands_index_schema["title"] == "Long Novel Agent Desktop Pack Commands Index", "desktop-pack commands index schema title mismatch")
        assert_true(commands_index_json["local_only"] is True and commands_index_json["requires_server"] is False, "desktop-pack commands index JSON should be local-only")
        assert_true(commands_index_json["writes_project_state"] is False, "desktop-pack commands index JSON should be read-only")
        assert_true(commands_index_json["command_count"] == commands_json["command_count"], "desktop-pack commands index command count mismatch")
        assert_true(commands_index_json["group_count"] == len(commands_json["groups"]), "desktop-pack commands index group count mismatch")
        assert_true(commands_index_json["read_only_count"] == commands_json["write_scope_counts"]["read_only"], "desktop-pack commands index read-only count mismatch")
        assert_true(commands_index_json["write_scope_counts"] == commands_json["write_scope_counts"], "desktop-pack commands index write counts mismatch")
        index_groups = {group.get("id"): group for group in commands_index_json.get("groups", []) if isinstance(group, dict)}
        assert_true(set(index_groups) == set(command_groups), "desktop-pack commands index groups should match commands JSON")
        for group_id, group in command_groups.items():
            indexed = index_groups[group_id]
            assert_true(indexed["command_keys"] == group["command_keys"], f"desktop-pack commands index keys mismatch for {group_id}")
            assert_true(indexed["command_count"] == group["command_count"], f"desktop-pack commands index count mismatch for {group_id}")
            assert_true(len(indexed.get("commands", [])) == group["command_count"], f"desktop-pack commands index entries mismatch for {group_id}")
        assert_true("Long Novel Agent Commands Index" in commands_index_md, "desktop-pack commands index markdown missed title")
        assert_true("pack_integrity" in commands_index_md and "desktop_evidence" in commands_index_md, "desktop-pack commands index markdown missed command groups")
        assert_true("commands.json" in commands_index_md, "desktop-pack commands index markdown missed commands JSON pointer")
        command_entries = commands_json.get("commands") if isinstance(commands_json.get("commands"), list) else []
        command_by_key = {entry.get("key"): entry for entry in command_entries if isinstance(entry, dict)}
        for key in [
            "open_pack_index",
            "local_summary",
            "powershell_open_launcher",
            "windows_open_launcher",
            "macos_open_launcher",
            "desktop_bootstrap_apply",
            "desktop_pack_archive",
            "desktop_verify",
            "desktop_next_step",
            "desktop_diagnostics",
            "tool_visibility",
            "desktop_evidence_session",
            "desktop_checklist",
            "ingest_evidence",
            "desktop_evidence_packet",
            "desktop_evidence_review",
            "desktop_evidence_gaps",
            "desktop_results_doctor",
            "imported_desktop_results_doctor",
            "record_imported_desktop_check",
            "prepare_session",
            "list_agent_activity",
            "agent_activity_report",
            "handoff_integrity",
            "handoff_range_report",
            "handoff_report",
            "handoff_readiness",
            "record_agent_activity",
            "writer_pack",
            "writer_install_apply",
            "source_intake_apply",
            "import_audit",
            "chapter_readiness",
            "chapter_revision_prompt",
            "chapter_revision_compare",
            "chapter_delivery",
            "chapter_acceptance_plan",
            "chapter_range_readiness",
            "chapter_range_delivery",
            "chapter_range_acceptance_plan",
            "verify_pack",
            "verify_pack_cli",
            "pack_schema_check",
            "pack_freshness",
            "chapter_session_freshness",
            "write_session_check",
            "pack_doctor",
            "pack_doctor_json",
            "desktop_pack_readiness",
            "agent_startup_prompt",
            "author_actions",
            "acceptance_review",
            "pack_rebind_dry_run",
            "pack_rebind_apply",
            "macos_archive_launcher",
            "powershell_install_launcher",
            "powershell_upgrade_launcher",
            "powershell_uninstall_launcher",
            "powershell_local_checks_launcher",
            "powershell_archive_launcher",
            "windows_install_launcher",
            "windows_upgrade_launcher",
            "windows_uninstall_launcher",
            "windows_local_checks_launcher",
            "windows_archive_launcher",
        ]:
            assert_true(key in command_by_key, f"desktop-pack commands JSON missed {key}")
            assert_true(key in commands_json["by_key"], f"desktop-pack commands JSON by_key missed {key}")
        assert_true(command_by_key["open_pack_index"]["stage"] == "pack", "desktop-pack open command should be pack stage")
        assert_true(command_by_key["open_pack_index"]["writes_project_state"] is False, "desktop-pack open command should not write project state")
        assert_true(command_by_key["open_pack_index"]["writes_install_config"] is False, "desktop-pack open command should not write install config")
        assert_true(command_by_key["open_pack_index"]["writes_pack_files"] is False, "desktop-pack open command should not write pack files")
        assert_true(command_by_key["open_pack_index"]["requires_author_confirmation"] is False, "desktop-pack open command should not require author confirmation")
        assert_true("index.html" in command_by_key["open_pack_index"]["input_files"], "desktop-pack open command missed index input")
        assert_true(command_by_key["local_summary"]["stage"] == "maintenance", "desktop-pack local summary command should be maintenance stage")
        assert_true(command_by_key["local_summary"]["writes_project_state"] is False, "desktop-pack local summary should not write project state")
        assert_true(command_by_key["local_summary"]["writes_install_config"] is False, "desktop-pack local summary should not write install config")
        assert_true(command_by_key["local_summary"]["writes_pack_files"] is False, "desktop-pack local summary should not write pack files")
        assert_true("local-summary.json" in command_by_key["open_pack_index"]["input_files"], "desktop-pack open command missed local summary input")
        assert_true("local-summary.schema.json" in command_by_key["open_pack_index"]["input_files"], "desktop-pack open command missed local summary schema input")
        assert_true("metadata.json" in command_by_key["local_summary"]["input_files"], "desktop-pack local summary command missed metadata input")
        assert_true("pack-doctor.json" in command_by_key["local_summary"]["input_files"], "desktop-pack local summary command missed pack doctor input")
        assert_true("local-summary.schema.json" in command_by_key["local_summary"]["input_files"], "desktop-pack local summary command missed schema input")
        assert_true(command_by_key["powershell_open_launcher"]["stage"] == "pack", "desktop-pack PowerShell open launcher should be pack stage")
        assert_true(command_by_key["powershell_open_launcher"]["writes_project_state"] is False, "desktop-pack PowerShell open launcher should not write project state")
        assert_true(command_by_key["powershell_open_launcher"]["writes_install_config"] is False, "desktop-pack PowerShell open launcher should not write install config")
        assert_true(command_by_key["powershell_open_launcher"]["writes_pack_files"] is False, "desktop-pack PowerShell open launcher should not write pack files")
        assert_true("open-local.ps1" in command_by_key["powershell_open_launcher"]["input_files"], "desktop-pack PowerShell open launcher missed input file")
        assert_true(command_by_key["windows_open_launcher"]["stage"] == "pack", "desktop-pack Windows open launcher should be pack stage")
        assert_true(command_by_key["windows_open_launcher"]["writes_project_state"] is False, "desktop-pack Windows open launcher should not write project state")
        assert_true(command_by_key["windows_open_launcher"]["writes_install_config"] is False, "desktop-pack Windows open launcher should not write install config")
        assert_true(command_by_key["windows_open_launcher"]["writes_pack_files"] is False, "desktop-pack Windows open launcher should not write pack files")
        assert_true("open-local.cmd" in command_by_key["windows_open_launcher"]["input_files"], "desktop-pack Windows open launcher missed input file")
        assert_true(command_by_key["macos_open_launcher"]["stage"] == "pack", "desktop-pack macOS open launcher should be pack stage")
        assert_true(command_by_key["macos_open_launcher"]["writes_project_state"] is False, "desktop-pack macOS open launcher should not write project state")
        assert_true(command_by_key["macos_open_launcher"]["writes_install_config"] is False, "desktop-pack macOS open launcher should not write install config")
        assert_true(command_by_key["macos_open_launcher"]["writes_pack_files"] is False, "desktop-pack macOS open launcher should not write pack files")
        assert_true("open-local.command" in command_by_key["macos_open_launcher"]["input_files"], "desktop-pack macOS open launcher missed input file")
        assert_true(command_by_key["desktop_pack_archive"]["stage"] == "pack", "desktop-pack archive command should be pack stage")
        assert_true(command_by_key["desktop_pack_archive"]["writes_pack_files"] is True, "desktop-pack archive command should write pack files")
        assert_true(command_by_key["desktop_pack_archive"]["writes_project_state"] is False, "desktop-pack archive command should not write project state")
        assert_true(command_by_key["desktop_pack_archive"]["writes_install_config"] is False, "desktop-pack archive command should not write install config")
        assert_true("long-novel-agent-desktop-pack/" in command_by_key["desktop_pack_archive"]["input_files"], "desktop-pack archive command missed pack input")
        assert_true(any(str(item).endswith(".zip") for item in command_by_key["desktop_pack_archive"]["output_files"]), "desktop-pack archive command missed zip output")
        assert_true(command_by_key["macos_archive_launcher"]["stage"] == "pack", "desktop-pack macOS archive launcher should be pack stage")
        assert_true(command_by_key["macos_archive_launcher"]["writes_pack_files"] is True, "desktop-pack macOS archive launcher should write pack files")
        assert_true(command_by_key["macos_archive_launcher"]["writes_project_state"] is False, "desktop-pack macOS archive launcher should not write project state")
        assert_true(command_by_key["macos_archive_launcher"]["writes_install_config"] is False, "desktop-pack macOS archive launcher should not write install config")
        assert_true("archive-local.command" in command_by_key["macos_archive_launcher"]["input_files"], "desktop-pack macOS archive launcher missed input file")
        assert_true(command_by_key["powershell_install_launcher"]["stage"] == "install", "desktop-pack PowerShell install launcher should be install stage")
        assert_true(command_by_key["powershell_install_launcher"]["writes_project_state"] is True, "desktop-pack PowerShell install launcher should write project state")
        assert_true(command_by_key["powershell_install_launcher"]["writes_install_config"] is True, "desktop-pack PowerShell install launcher should write install config")
        assert_true("install-local.ps1" in command_by_key["powershell_install_launcher"]["input_files"], "desktop-pack PowerShell install launcher missed input file")
        assert_true(command_by_key["powershell_upgrade_launcher"]["writes_install_config"] is True, "desktop-pack PowerShell upgrade launcher should write install config")
        assert_true("upgrade-local.ps1" in command_by_key["powershell_upgrade_launcher"]["input_files"], "desktop-pack PowerShell upgrade launcher missed input file")
        assert_true(command_by_key["powershell_uninstall_launcher"]["writes_install_config"] is True, "desktop-pack PowerShell uninstall launcher should write install config")
        assert_true("uninstall-local.ps1" in command_by_key["powershell_uninstall_launcher"]["input_files"], "desktop-pack PowerShell uninstall launcher missed input file")
        assert_true(command_by_key["powershell_local_checks_launcher"]["stage"] == "verify", "desktop-pack PowerShell local checks launcher should be verify stage")
        assert_true("local-checks.ps1" in command_by_key["powershell_local_checks_launcher"]["input_files"], "desktop-pack PowerShell local checks launcher missed input file")
        assert_true(command_by_key["powershell_archive_launcher"]["stage"] == "pack", "desktop-pack PowerShell archive launcher should be pack stage")
        assert_true(command_by_key["powershell_archive_launcher"]["writes_pack_files"] is True, "desktop-pack PowerShell archive launcher should write pack files")
        assert_true(command_by_key["powershell_archive_launcher"]["writes_project_state"] is False, "desktop-pack PowerShell archive launcher should not write project state")
        assert_true(command_by_key["powershell_archive_launcher"]["writes_install_config"] is False, "desktop-pack PowerShell archive launcher should not write install config")
        assert_true("archive-local.ps1" in command_by_key["powershell_archive_launcher"]["input_files"], "desktop-pack PowerShell archive launcher missed input file")
        assert_true(command_by_key["windows_install_launcher"]["stage"] == "install", "desktop-pack Windows install launcher should be install stage")
        assert_true(command_by_key["windows_install_launcher"]["writes_project_state"] is True, "desktop-pack Windows install launcher should write project state")
        assert_true(command_by_key["windows_install_launcher"]["writes_install_config"] is True, "desktop-pack Windows install launcher should write install config")
        assert_true("install-local.cmd" in command_by_key["windows_install_launcher"]["input_files"], "desktop-pack Windows install launcher missed input file")
        assert_true(command_by_key["windows_upgrade_launcher"]["writes_install_config"] is True, "desktop-pack Windows upgrade launcher should write install config")
        assert_true("upgrade-local.cmd" in command_by_key["windows_upgrade_launcher"]["input_files"], "desktop-pack Windows upgrade launcher missed input file")
        assert_true(command_by_key["windows_uninstall_launcher"]["writes_install_config"] is True, "desktop-pack Windows uninstall launcher should write install config")
        assert_true("uninstall-local.cmd" in command_by_key["windows_uninstall_launcher"]["input_files"], "desktop-pack Windows uninstall launcher missed input file")
        assert_true(command_by_key["windows_local_checks_launcher"]["stage"] == "verify", "desktop-pack Windows local checks launcher should be verify stage")
        assert_true("local-checks.cmd" in command_by_key["windows_local_checks_launcher"]["input_files"], "desktop-pack Windows local checks launcher missed input file")
        assert_true(command_by_key["windows_archive_launcher"]["stage"] == "pack", "desktop-pack Windows archive launcher should be pack stage")
        assert_true(command_by_key["windows_archive_launcher"]["writes_pack_files"] is True, "desktop-pack Windows archive launcher should write pack files")
        assert_true(command_by_key["windows_archive_launcher"]["writes_project_state"] is False, "desktop-pack Windows archive launcher should not write project state")
        assert_true(command_by_key["windows_archive_launcher"]["writes_install_config"] is False, "desktop-pack Windows archive launcher should not write install config")
        assert_true("archive-local.cmd" in command_by_key["windows_archive_launcher"]["input_files"], "desktop-pack Windows archive launcher missed input file")
        assert_true(command_by_key["verify_pack"]["stage"] == "verify", "desktop-pack commands JSON verify pack should be verify stage")
        assert_true(command_by_key["verify_pack"]["writes_project_state"] is False, "desktop-pack commands JSON verify pack should not write project state")
        assert_true(command_by_key["verify_pack"]["writes_install_config"] is False, "desktop-pack commands JSON verify pack should not write install config")
        assert_true("pack-manifest.json" in command_by_key["verify_pack"]["input_files"], "desktop-pack commands JSON verify pack missed manifest input")
        assert_true(command_by_key["verify_pack_cli"]["stage"] == "verify", "desktop-pack commands JSON CLI verify pack should be verify stage")
        assert_true(command_by_key["verify_pack_cli"]["writes_project_state"] is False, "desktop-pack commands JSON CLI verify pack should not write project state")
        assert_true(command_by_key["verify_pack_cli"]["writes_install_config"] is False, "desktop-pack commands JSON CLI verify pack should not write install config")
        assert_true("pack-manifest.json" in command_by_key["verify_pack_cli"]["input_files"], "desktop-pack commands JSON CLI verify pack missed manifest input")
        assert_true(command_by_key["pack_schema_check"]["stage"] == "verify", "desktop-pack commands JSON pack schema check should be verify stage")
        assert_true(command_by_key["pack_schema_check"]["writes_project_state"] is False, "desktop-pack commands JSON pack schema check should not write project state")
        assert_true(command_by_key["pack_schema_check"]["writes_install_config"] is False, "desktop-pack commands JSON pack schema check should not write install config")
        assert_true(command_by_key["pack_schema_check"]["writes_pack_files"] is False, "desktop-pack commands JSON pack schema check should not write pack files")
        assert_true("commands.schema.json" in command_by_key["pack_schema_check"]["input_files"], "desktop-pack commands JSON pack schema check missed commands schema input")
        assert_true("local-summary.json" in command_by_key["pack_schema_check"]["input_files"], "desktop-pack commands JSON pack schema check missed local summary input")
        assert_true("local-summary.schema.json" in command_by_key["pack_schema_check"]["input_files"], "desktop-pack commands JSON pack schema check missed local summary schema input")
        assert_true(command_by_key["pack_doctor"]["stage"] == "verify", "desktop-pack commands JSON pack doctor should be verify stage")
        assert_true(command_by_key["pack_doctor"]["writes_project_state"] is False, "desktop-pack commands JSON pack doctor should not write project state")
        assert_true(command_by_key["pack_doctor"]["writes_install_config"] is False, "desktop-pack commands JSON pack doctor should not write install config")
        assert_true(command_by_key["pack_doctor"]["requires_author_confirmation"] is False, "desktop-pack commands JSON pack doctor should not require author confirmation")
        assert_true("commands.json" in command_by_key["pack_doctor"]["input_files"], "desktop-pack commands JSON pack doctor missed commands input")
        assert_true(command_by_key["pack_doctor_json"]["stage"] == "verify", "desktop-pack commands JSON pack doctor JSON should be verify stage")
        assert_true(command_by_key["pack_doctor_json"]["writes_pack_files"] is True, "desktop-pack commands JSON pack doctor JSON should write pack report")
        assert_true(command_by_key["pack_doctor_json"]["writes_project_state"] is False, "desktop-pack commands JSON pack doctor JSON should not write project state")
        assert_true(command_by_key["pack_doctor_json"]["writes_install_config"] is False, "desktop-pack commands JSON pack doctor JSON should not write install config")
        assert_true(command_by_key["pack_doctor_json"]["requires_author_confirmation"] is False, "desktop-pack commands JSON pack doctor JSON should not require author confirmation")
        assert_true("commands.json" in command_by_key["pack_doctor_json"]["input_files"], "desktop-pack commands JSON pack doctor JSON missed commands input")
        assert_true("pack-doctor.json" in command_by_key["pack_doctor_json"]["output_files"], "desktop-pack commands JSON pack doctor JSON missed report output")
        assert_true(command_by_key["desktop_pack_readiness"]["stage"] == "verify", "desktop-pack readiness command should be verify stage")
        assert_true(command_by_key["desktop_pack_readiness"]["writes_project_state"] is False, "desktop-pack readiness command should not write project state")
        assert_true(command_by_key["desktop_pack_readiness"]["writes_install_config"] is False, "desktop-pack readiness command should not write install config")
        assert_true(command_by_key["desktop_pack_readiness"]["writes_pack_files"] is False, "desktop-pack readiness command should not write pack files")
        assert_true(command_by_key["desktop_pack_readiness"]["requires_author_confirmation"] is False, "desktop-pack readiness command should not require author confirmation")
        assert_true("metadata.json" in command_by_key["desktop_pack_readiness"]["input_files"], "desktop-pack readiness command missed metadata input")
        assert_true(command_by_key["chapter_session_freshness"]["stage"] == "maintenance", "chapter session freshness command should be maintenance stage")
        assert_true(command_by_key["chapter_session_freshness"]["writes_project_state"] is False, "chapter session freshness command should not write project state")
        assert_true(command_by_key["chapter_session_freshness"]["writes_install_config"] is False, "chapter session freshness command should not write install config")
        assert_true(command_by_key["chapter_session_freshness"]["writes_pack_files"] is False, "chapter session freshness command should not write pack files")
        assert_true(command_by_key["chapter_session_freshness"]["requires_author_confirmation"] is False, "chapter session freshness command should not require author confirmation")
        assert_true("chapter-session.json" in command_by_key["chapter_session_freshness"]["input_files"], "chapter session freshness command missed chapter session input")
        assert_true("metadata.json" in command_by_key["chapter_session_freshness"]["input_files"], "chapter session freshness command missed metadata input")
        assert_true(command_by_key["agent_startup_prompt"]["stage"] == "maintenance", "agent startup prompt command should be maintenance stage")
        assert_true(command_by_key["agent_startup_prompt"]["writes_project_state"] is False, "agent startup prompt command should not write project state")
        assert_true(command_by_key["agent_startup_prompt"]["writes_install_config"] is False, "agent startup prompt command should not write install config")
        assert_true(command_by_key["agent_startup_prompt"]["writes_pack_files"] is False, "agent startup prompt command should not write pack files")
        assert_true(command_by_key["agent_startup_prompt"]["requires_author_confirmation"] is False, "agent startup prompt command should not require author confirmation")
        assert_true("metadata.json" in command_by_key["agent_startup_prompt"]["input_files"], "agent startup prompt command missed metadata input")
        assert_true("chapter-session.json" in command_by_key["agent_startup_prompt"]["input_files"], "agent startup prompt command missed chapter session input")
        assert_true("agent-startup-prompt.json" not in command_by_key["agent_startup_prompt"]["output_files"], "agent startup prompt command should not declare pack writes")
        assert_true(command_by_key["tool_visibility"]["stage"] == "maintenance", "tool visibility command should be maintenance stage")
        assert_true(command_by_key["tool_visibility"]["writes_project_state"] is False, "tool visibility command should not write project state")
        assert_true(command_by_key["tool_visibility"]["writes_install_config"] is False, "tool visibility command should not write install config")
        assert_true(command_by_key["tool_visibility"]["writes_pack_files"] is False, "tool visibility command should not write pack files")
        assert_true(command_by_key["tool_visibility"]["requires_author_confirmation"] is False, "tool visibility command should not require author confirmation")
        assert_true("tool-visibility" in command_by_key["tool_visibility"]["command"], "tool visibility command should call tool-visibility CLI")
        assert_true(command_by_key["author_actions"]["stage"] == "maintenance", "author actions command should be maintenance stage")
        assert_true(command_by_key["author_actions"]["writes_project_state"] is False, "author actions command should not write project state")
        assert_true(command_by_key["author_actions"]["writes_install_config"] is False, "author actions command should not write install config")
        assert_true(command_by_key["author_actions"]["writes_pack_files"] is False, "author actions command should not write pack files")
        assert_true(command_by_key["author_actions"]["requires_author_confirmation"] is False, "author actions command should not require author confirmation")
        assert_true("author-review-queue.json" in command_by_key["author_actions"]["input_files"], "author actions command missed author-review input")
        assert_true("author-actions.json" not in command_by_key["author_actions"]["output_files"], "author actions command should not declare pack writes")
        assert_true(command_by_key["acceptance_review"]["stage"] == "maintenance", "acceptance review command should be maintenance stage")
        assert_true(command_by_key["acceptance_review"]["writes_project_state"] is False, "acceptance review command should not write project state")
        assert_true(command_by_key["acceptance_review"]["writes_install_config"] is False, "acceptance review command should not write install config")
        assert_true(command_by_key["acceptance_review"]["writes_pack_files"] is False, "acceptance review command should not write pack files")
        assert_true(command_by_key["acceptance_review"]["requires_author_confirmation"] is False, "acceptance review command should not require author confirmation")
        assert_true("author-actions.json" in command_by_key["acceptance_review"]["input_files"], "acceptance review command missed author actions input")
        assert_true("acceptance-review.json" in command_by_key["acceptance_review"]["input_files"], "acceptance review command missed acceptance JSON input")
        assert_true("acceptance-review.json" not in command_by_key["acceptance_review"]["output_files"], "acceptance review command should not declare pack writes")
        assert_true(command_by_key["pack_rebind_dry_run"]["stage"] == "pack", "desktop-pack commands JSON pack rebind dry-run should be pack stage")
        assert_true(command_by_key["pack_rebind_dry_run"]["writes_pack_files"] is False, "desktop-pack commands JSON pack rebind dry-run should not write pack files")
        assert_true(command_by_key["pack_rebind_dry_run"]["writes_project_state"] is False, "desktop-pack commands JSON pack rebind dry-run should not write project state")
        assert_true(command_by_key["pack_rebind_dry_run"]["writes_install_config"] is False, "desktop-pack commands JSON pack rebind dry-run should not write install config")
        assert_true(command_by_key["pack_rebind_apply"]["stage"] == "pack", "desktop-pack commands JSON pack rebind apply should be pack stage")
        assert_true(command_by_key["pack_rebind_apply"]["writes_pack_files"] is True, "desktop-pack commands JSON pack rebind apply should write pack files")
        assert_true(command_by_key["pack_rebind_apply"]["writes_project_state"] is False, "desktop-pack commands JSON pack rebind apply should not write project state")
        assert_true(command_by_key["pack_rebind_apply"]["writes_install_config"] is False, "desktop-pack commands JSON pack rebind apply should not write install config")
        assert_true("pack-manifest.json" in command_by_key["pack_rebind_apply"]["output_files"], "desktop-pack commands JSON pack rebind apply missed manifest output")
        assert_true(command_by_key["source_intake_apply"]["requires_author_confirmation"] is True, "desktop-pack commands JSON source intake should require author confirmation")
        assert_true(command_by_key["source_intake_apply"]["writes_project_state"] is True, "desktop-pack commands JSON source intake should write project state")
        assert_true(".novel-agent/sources.jsonl" in command_by_key["source_intake_apply"]["output_files"], "desktop-pack commands JSON source intake missed output files")
        assert_true(command_by_key["chapter_readiness"]["stage"] == "writing", "desktop-pack commands JSON chapter readiness should be writing stage")
        assert_true(command_by_key["chapter_readiness"]["writes_project_state"] is False, "desktop-pack commands JSON chapter readiness should not write project state")
        assert_true(command_by_key["chapter_readiness"]["requires_author_confirmation"] is False, "desktop-pack commands JSON chapter readiness should not require author confirmation")
        assert_true(command_by_key["chapter_revision_prompt"]["stage"] == "writing", "desktop-pack commands JSON chapter revision prompt should be writing stage")
        assert_true(command_by_key["chapter_revision_prompt"]["writes_project_state"] is False, "desktop-pack commands JSON chapter revision prompt should not write project state")
        assert_true(command_by_key["chapter_revision_prompt"]["writes_install_config"] is False, "desktop-pack commands JSON chapter revision prompt should not write install config")
        assert_true(command_by_key["chapter_revision_prompt"]["requires_author_confirmation"] is False, "desktop-pack commands JSON chapter revision prompt should not require author confirmation")
        assert_true("chapters/002.md" in command_by_key["chapter_revision_prompt"]["input_files"], "desktop-pack commands JSON chapter revision prompt missed draft input")
        assert_true(".novel-agent/facts.jsonl" in command_by_key["chapter_revision_prompt"]["input_files"], "desktop-pack commands JSON chapter revision prompt missed facts input")
        assert_true(command_by_key["chapter_revision_compare"]["stage"] == "writing", "desktop-pack commands JSON chapter revision compare should be writing stage")
        assert_true(command_by_key["chapter_revision_compare"]["writes_project_state"] is False, "desktop-pack commands JSON chapter revision compare should not write project state")
        assert_true(command_by_key["chapter_revision_compare"]["writes_install_config"] is False, "desktop-pack commands JSON chapter revision compare should not write install config")
        assert_true(command_by_key["chapter_revision_compare"]["requires_author_confirmation"] is False, "desktop-pack commands JSON chapter revision compare should not require author confirmation")
        assert_true("chapters/002.before.md" in command_by_key["chapter_revision_compare"]["input_files"], "desktop-pack commands JSON chapter revision compare missed before draft input")
        assert_true("chapters/002.md" in command_by_key["chapter_revision_compare"]["input_files"], "desktop-pack commands JSON chapter revision compare missed after draft input")
        assert_true(".novel-agent/facts.jsonl" in command_by_key["chapter_revision_compare"]["input_files"], "desktop-pack commands JSON chapter revision compare missed facts input")
        assert_true(command_by_key["chapter_delivery"]["stage"] == "writing", "desktop-pack commands JSON chapter delivery should be writing stage")
        assert_true(command_by_key["chapter_delivery"]["writes_project_state"] is False, "desktop-pack commands JSON chapter delivery should not write project state")
        assert_true(command_by_key["chapter_delivery"]["writes_install_config"] is False, "desktop-pack commands JSON chapter delivery should not write install config")
        assert_true(command_by_key["chapter_delivery"]["requires_author_confirmation"] is False, "desktop-pack commands JSON chapter delivery should not require author confirmation")
        assert_true("chapters/002.md" in command_by_key["chapter_delivery"]["input_files"], "desktop-pack commands JSON chapter delivery missed draft input")
        assert_true(".novel-agent/proposals.jsonl" in command_by_key["chapter_delivery"]["input_files"], "desktop-pack commands JSON chapter delivery missed proposal input")
        assert_true(command_by_key["chapter_acceptance_plan"]["stage"] == "writing", "desktop-pack commands JSON chapter acceptance plan should be writing stage")
        assert_true(command_by_key["chapter_acceptance_plan"]["writes_project_state"] is False, "desktop-pack commands JSON chapter acceptance plan should not write project state")
        assert_true(command_by_key["chapter_acceptance_plan"]["writes_install_config"] is False, "desktop-pack commands JSON chapter acceptance plan should not write install config")
        assert_true(command_by_key["chapter_acceptance_plan"]["requires_author_confirmation"] is True, "desktop-pack commands JSON chapter acceptance plan should require author confirmation")
        assert_true("chapters/002.md" in command_by_key["chapter_acceptance_plan"]["input_files"], "desktop-pack commands JSON chapter acceptance plan missed draft input")
        assert_true(".novel-agent/proposals.jsonl" in command_by_key["chapter_acceptance_plan"]["input_files"], "desktop-pack commands JSON chapter acceptance plan missed proposal input")
        assert_true(command_by_key["chapter_range_readiness"]["stage"] == "writing", "desktop-pack commands JSON chapter range readiness should be writing stage")
        assert_true(command_by_key["chapter_range_readiness"]["writes_project_state"] is False, "desktop-pack commands JSON chapter range readiness should not write project state")
        assert_true(command_by_key["chapter_range_readiness"]["requires_author_confirmation"] is False, "desktop-pack commands JSON chapter range readiness should not require author confirmation")
        assert_true("chapters/" in command_by_key["chapter_range_readiness"]["input_files"], "desktop-pack commands JSON chapter range readiness missed draft input")
        assert_true(command_by_key["chapter_range_delivery"]["stage"] == "writing", "desktop-pack commands JSON chapter range delivery should be writing stage")
        assert_true(command_by_key["chapter_range_delivery"]["writes_project_state"] is False, "desktop-pack commands JSON chapter range delivery should not write project state")
        assert_true(command_by_key["chapter_range_delivery"]["writes_install_config"] is False, "desktop-pack commands JSON chapter range delivery should not write install config")
        assert_true(command_by_key["chapter_range_delivery"]["requires_author_confirmation"] is False, "desktop-pack commands JSON chapter range delivery should not require author confirmation")
        assert_true("chapters/" in command_by_key["chapter_range_delivery"]["input_files"], "desktop-pack commands JSON chapter range delivery missed draft input")
        assert_true(".novel-agent/proposals.jsonl" in command_by_key["chapter_range_delivery"]["input_files"], "desktop-pack commands JSON chapter range delivery missed proposal input")
        assert_true(command_by_key["chapter_range_acceptance_plan"]["stage"] == "writing", "desktop-pack commands JSON chapter range acceptance plan should be writing stage")
        assert_true(command_by_key["chapter_range_acceptance_plan"]["writes_project_state"] is False, "desktop-pack commands JSON chapter range acceptance plan should not write project state")
        assert_true(command_by_key["chapter_range_acceptance_plan"]["writes_install_config"] is False, "desktop-pack commands JSON chapter range acceptance plan should not write install config")
        assert_true(command_by_key["chapter_range_acceptance_plan"]["requires_author_confirmation"] is True, "desktop-pack commands JSON chapter range acceptance plan should require author confirmation")
        assert_true("chapters/" in command_by_key["chapter_range_acceptance_plan"]["input_files"], "desktop-pack commands JSON chapter range acceptance plan missed draft input")
        assert_true(".novel-agent/proposals.jsonl" in command_by_key["chapter_range_acceptance_plan"]["input_files"], "desktop-pack commands JSON chapter range acceptance plan missed proposal input")
        assert_true(command_by_key["list_agent_activity"]["stage"] == "maintenance", "desktop-pack commands JSON list agent activity should be maintenance stage")
        assert_true(command_by_key["list_agent_activity"]["writes_project_state"] is False, "desktop-pack commands JSON list agent activity should be read-only")
        assert_true(".novel-agent/agent_activity.jsonl" in command_by_key["list_agent_activity"]["input_files"], "desktop-pack commands JSON list agent activity missed input file")
        assert_true(command_by_key["agent_activity_report"]["stage"] == "maintenance", "desktop-pack commands JSON agent activity report should be maintenance stage")
        assert_true(command_by_key["agent_activity_report"]["writes_project_state"] is False, "desktop-pack commands JSON agent activity report should be read-only")
        assert_true(command_by_key["agent_activity_report"]["requires_author_confirmation"] is False, "desktop-pack commands JSON agent activity report should not require author confirmation")
        assert_true(".novel-agent/agent_activity.jsonl" in command_by_key["agent_activity_report"]["input_files"], "desktop-pack commands JSON agent activity report missed input file")
        assert_true(command_by_key["handoff_integrity"]["stage"] == "maintenance", "desktop-pack commands JSON handoff integrity should be maintenance stage")
        assert_true(command_by_key["handoff_integrity"]["writes_project_state"] is False, "desktop-pack commands JSON handoff integrity should be read-only")
        assert_true(command_by_key["handoff_integrity"]["requires_author_confirmation"] is False, "desktop-pack commands JSON handoff integrity should not require author confirmation")
        assert_true(".novel-agent/agent_activity.jsonl" in command_by_key["handoff_integrity"]["input_files"], "desktop-pack commands JSON handoff integrity missed activity input")
        assert_true(".novel-agent/proposals.jsonl" in command_by_key["handoff_integrity"]["input_files"], "desktop-pack commands JSON handoff integrity missed proposals input")
        assert_true(command_by_key["handoff_range_report"]["stage"] == "maintenance", "desktop-pack commands JSON handoff range report should be maintenance stage")
        assert_true(command_by_key["handoff_range_report"]["writes_project_state"] is False, "desktop-pack commands JSON handoff range report should be read-only")
        assert_true(command_by_key["handoff_range_report"]["requires_author_confirmation"] is False, "desktop-pack commands JSON handoff range report should not require author confirmation")
        assert_true("chapters/" in command_by_key["handoff_range_report"]["input_files"], "desktop-pack commands JSON handoff range report missed draft input")
        assert_true(".novel-agent/proposals.jsonl" in command_by_key["handoff_range_report"]["input_files"], "desktop-pack commands JSON handoff range report missed proposals input")
        assert_true(command_by_key["handoff_report"]["stage"] == "maintenance", "desktop-pack commands JSON handoff report should be maintenance stage")
        assert_true(command_by_key["handoff_report"]["writes_project_state"] is False, "desktop-pack commands JSON handoff report should be read-only")
        assert_true(command_by_key["handoff_report"]["requires_author_confirmation"] is False, "desktop-pack commands JSON handoff report should not require author confirmation")
        assert_true(".novel-agent/agent_activity.jsonl" in command_by_key["handoff_report"]["input_files"], "desktop-pack commands JSON handoff report missed activity input")
        assert_true(command_by_key["handoff_readiness"]["stage"] == "maintenance", "desktop-pack commands JSON handoff readiness should be maintenance stage")
        assert_true(command_by_key["handoff_readiness"]["writes_project_state"] is False, "desktop-pack commands JSON handoff readiness should be read-only")
        assert_true(command_by_key["handoff_readiness"]["requires_author_confirmation"] is False, "desktop-pack commands JSON handoff readiness should not require author confirmation")
        assert_true(".novel-agent/agents.jsonl" in command_by_key["handoff_readiness"]["input_files"], "desktop-pack commands JSON handoff readiness missed agents input")
        assert_true(command_by_key["record_agent_activity"]["stage"] == "writing", "desktop-pack commands JSON record agent activity should be writing stage")
        assert_true(command_by_key["record_agent_activity"]["writes_project_state"] is True, "desktop-pack commands JSON record agent activity should write project state")
        assert_true(command_by_key["record_agent_activity"]["requires_review"] is True, "desktop-pack commands JSON record agent activity should require review")
        assert_true(command_by_key["record_agent_activity"]["requires_author_confirmation"] is False, "desktop-pack commands JSON record agent activity should not require author confirmation")
        assert_true(".novel-agent/agent_activity.jsonl" in command_by_key["record_agent_activity"]["output_files"], "desktop-pack commands JSON record agent activity missed output file")
        assert_true(command_by_key["writer_mode_readiness"]["stage"] == "writer_mode", "desktop-pack commands JSON writer readiness should be writer_mode stage")
        assert_true(command_by_key["writer_mode_readiness"]["writes_project_state"] is False, "desktop-pack commands JSON writer readiness should not write project state")
        assert_true(command_by_key["writer_mode_readiness"]["writes_install_config"] is False, "desktop-pack commands JSON writer readiness should not write install config")
        assert_true(command_by_key["writer_mode_readiness"]["requires_author_confirmation"] is True, "desktop-pack commands JSON writer readiness should require author confirmation")
        assert_true(".novel-agent/desktop_verifications.jsonl" in command_by_key["writer_mode_readiness"]["input_files"], "desktop-pack commands JSON writer readiness missed desktop verification input")
        assert_true(command_by_key["author_review_queue"]["stage"] == "maintenance", "desktop-pack commands JSON author review queue should be maintenance stage")
        assert_true(command_by_key["author_review_queue"]["writes_project_state"] is False, "desktop-pack commands JSON author review queue should not write project state")
        assert_true(command_by_key["author_review_queue"]["writes_install_config"] is False, "desktop-pack commands JSON author review queue should not write install config")
        assert_true(command_by_key["author_review_queue"]["requires_author_confirmation"] is False, "desktop-pack commands JSON author review queue should not require author confirmation")
        assert_true(".novel-agent/proposals.jsonl" in command_by_key["author_review_queue"]["input_files"], "desktop-pack commands JSON author review queue missed proposals input")
        assert_true(".novel-agent/conflicts.jsonl" in command_by_key["author_review_queue"]["input_files"], "desktop-pack commands JSON author review queue missed conflicts input")
        assert_true(command_by_key["import_audit"]["stage"] == "maintenance", "desktop-pack commands JSON import audit should be maintenance stage")
        assert_true(command_by_key["import_audit"]["writes_project_state"] is False, "desktop-pack commands JSON import audit should not write project state")
        assert_true(command_by_key["import_audit"]["requires_author_confirmation"] is False, "desktop-pack commands JSON import audit should not require author confirmation")
        assert_true(command_by_key["desktop_verify"]["writes_project_state"] is False, "desktop-pack commands JSON desktop verify should be read-only")
        assert_true(command_by_key["desktop_next_step"]["stage"] == "maintenance", "desktop-pack commands JSON desktop next step should be maintenance stage")
        assert_true(command_by_key["desktop_next_step"]["writes_project_state"] is False, "desktop-pack commands JSON desktop next step should not write project state")
        assert_true(command_by_key["desktop_next_step"]["writes_install_config"] is False, "desktop-pack commands JSON desktop next step should not write install config")
        assert_true(command_by_key["desktop_next_step"]["writes_pack_files"] is False, "desktop-pack commands JSON desktop next step should not write pack files")
        assert_true(command_by_key["desktop_next_step"]["requires_author_confirmation"] is False, "desktop-pack commands JSON desktop next step should not require author confirmation")
        assert_true(".novel-agent/" in command_by_key["desktop_next_step"]["input_files"], "desktop-pack commands JSON desktop next step missed project state input")
        assert_true(command_by_key["desktop_diagnostics"]["stage"] == "maintenance", "desktop-pack commands JSON desktop diagnostics should be maintenance stage")
        assert_true(command_by_key["desktop_diagnostics"]["writes_project_state"] is False, "desktop-pack commands JSON desktop diagnostics should not write project state")
        assert_true(command_by_key["desktop_diagnostics"]["writes_install_config"] is False, "desktop-pack commands JSON desktop diagnostics should not write install config")
        assert_true(command_by_key["desktop_diagnostics"]["writes_pack_files"] is False, "desktop-pack commands JSON desktop diagnostics should not write pack files")
        assert_true(command_by_key["desktop_diagnostics"]["requires_author_confirmation"] is False, "desktop-pack commands JSON desktop diagnostics should not require author confirmation")
        assert_true(".novel-agent/" in command_by_key["desktop_diagnostics"]["input_files"], "desktop-pack commands JSON desktop diagnostics missed project state input")
        assert_true("pack-doctor.json" in command_by_key["desktop_diagnostics"]["input_files"], "desktop-pack commands JSON desktop diagnostics missed pack doctor input")
        assert_true(command_by_key["desktop_evidence_session"]["stage"] == "evidence", "desktop-pack commands JSON desktop evidence session should be evidence stage")
        assert_true(command_by_key["desktop_evidence_session"]["writes_project_state"] is False, "desktop-pack commands JSON desktop evidence session should not write project state")
        assert_true(command_by_key["desktop_evidence_session"]["writes_install_config"] is False, "desktop-pack commands JSON desktop evidence session should not write install config")
        assert_true(command_by_key["desktop_evidence_session"]["writes_pack_files"] is False, "desktop-pack commands JSON desktop evidence session should not write pack files")
        assert_true(command_by_key["desktop_evidence_session"]["requires_author_confirmation"] is False, "desktop-pack commands JSON desktop evidence session should not require author confirmation")
        assert_true("desktop-evidence.txt" in command_by_key["desktop_evidence_session"]["input_files"], "desktop-pack commands JSON desktop evidence session missed evidence input")
        assert_true(pack["result_file"] in command_by_key["desktop_evidence_session"]["input_files"], "desktop-pack commands JSON desktop evidence session missed result input")
        assert_true(command_by_key["desktop_evidence_runbook"]["stage"] == "evidence", "desktop-pack commands JSON desktop evidence runbook should be evidence stage")
        assert_true(command_by_key["desktop_evidence_runbook"]["writes_project_state"] is False, "desktop-pack commands JSON desktop evidence runbook should not write project state")
        assert_true(command_by_key["desktop_evidence_runbook"]["writes_install_config"] is False, "desktop-pack commands JSON desktop evidence runbook should not write install config")
        assert_true(command_by_key["desktop_evidence_runbook"]["writes_pack_files"] is False, "desktop-pack commands JSON desktop evidence runbook should not write pack files")
        assert_true(command_by_key["desktop_evidence_runbook"]["requires_author_confirmation"] is False, "desktop-pack commands JSON desktop evidence runbook should not require author confirmation")
        assert_true("desktop-evidence-session.json" in command_by_key["desktop_evidence_runbook"]["input_files"], "desktop-pack commands JSON desktop evidence runbook missed session input")
        assert_true(command_by_key["desktop_bootstrap_apply"]["writes_install_config"] is True, "desktop-pack commands JSON bootstrap should write install config")
        assert_true(command_by_key["desktop_bootstrap_apply"]["writes_project_state"] is True, "desktop-pack commands JSON bootstrap should write project state")
        assert_true(command_by_key["desktop_results_doctor"]["stage"] == "evidence", "desktop-pack commands JSON results doctor should be evidence stage")
        assert_true(command_by_key["desktop_results_doctor"]["writes_project_state"] is False, "desktop-pack commands JSON results doctor should not write project state")
        assert_true(command_by_key["desktop_results_doctor"]["writes_install_config"] is False, "desktop-pack commands JSON results doctor should not write install config")
        assert_true(command_by_key["desktop_results_doctor"]["requires_author_confirmation"] is False, "desktop-pack commands JSON results doctor should not require author confirmation")
        assert_true(pack["result_file"] in command_by_key["desktop_results_doctor"]["input_files"], "desktop-pack commands JSON results doctor missed result input")
        assert_true(command_by_key["desktop_evidence_packet"]["stage"] == "evidence", "desktop-pack commands JSON evidence packet should be evidence stage")
        assert_true(command_by_key["desktop_evidence_packet"]["writes_project_state"] is False, "desktop-pack commands JSON evidence packet should not write project state")
        assert_true(command_by_key["desktop_evidence_packet"]["writes_install_config"] is False, "desktop-pack commands JSON evidence packet should not write install config")
        assert_true(command_by_key["desktop_evidence_packet"]["writes_pack_files"] is False, "desktop-pack commands JSON evidence packet should not write pack files")
        assert_true(command_by_key["desktop_evidence_packet"]["requires_author_confirmation"] is False, "desktop-pack commands JSON evidence packet should not require author confirmation")
        assert_true("desktop-evidence.txt" in command_by_key["desktop_evidence_packet"]["input_files"], "desktop-pack commands JSON evidence packet missed evidence input")
        assert_true("result-json-guide.md" in command_by_key["desktop_evidence_packet"]["input_files"], "desktop-pack commands JSON evidence packet missed guide input")
        assert_true(command_by_key["desktop_evidence_review"]["stage"] == "evidence", "desktop-pack commands JSON evidence review should be evidence stage")
        assert_true(command_by_key["desktop_evidence_review"]["writes_project_state"] is False, "desktop-pack commands JSON evidence review should not write project state")
        assert_true(command_by_key["desktop_evidence_review"]["writes_install_config"] is False, "desktop-pack commands JSON evidence review should not write install config")
        assert_true(command_by_key["desktop_evidence_review"]["writes_pack_files"] is False, "desktop-pack commands JSON evidence review should not write pack files")
        assert_true(command_by_key["desktop_evidence_review"]["requires_author_confirmation"] is False, "desktop-pack commands JSON evidence review should not require author confirmation")
        assert_true("desktop-evidence.txt" in command_by_key["desktop_evidence_review"]["input_files"], "desktop-pack commands JSON evidence review missed evidence input")
        assert_true(".novel-agent/desktop_verifications.jsonl" in command_by_key["desktop_evidence_review"]["input_files"], "desktop-pack commands JSON evidence review missed saved verification input")
        assert_true(command_by_key["desktop_evidence_gaps"]["stage"] == "evidence", "desktop-pack commands JSON evidence gaps should be evidence stage")
        assert_true(command_by_key["desktop_evidence_gaps"]["writes_project_state"] is False, "desktop-pack commands JSON evidence gaps should not write project state")
        assert_true(command_by_key["desktop_evidence_gaps"]["writes_install_config"] is False, "desktop-pack commands JSON evidence gaps should not write install config")
        assert_true(command_by_key["desktop_evidence_gaps"]["writes_pack_files"] is False, "desktop-pack commands JSON evidence gaps should not write pack files")
        assert_true(command_by_key["desktop_evidence_gaps"]["requires_author_confirmation"] is False, "desktop-pack commands JSON evidence gaps should not require author confirmation")
        assert_true("desktop-evidence.txt" in command_by_key["desktop_evidence_gaps"]["input_files"], "desktop-pack commands JSON evidence gaps missed evidence input")
        assert_true("desktop-evidence-review.json" in command_by_key["desktop_evidence_gaps"]["input_files"], "desktop-pack commands JSON evidence gaps missed review input")
        assert_true(command_by_key["imported_desktop_results_doctor"]["stage"] == "evidence", "desktop-pack commands JSON imported results doctor should be evidence stage")
        assert_true(command_by_key["imported_desktop_results_doctor"]["writes_project_state"] is False, "desktop-pack commands JSON imported results doctor should not write project state")
        assert_true(command_by_key["imported_desktop_results_doctor"]["writes_install_config"] is False, "desktop-pack commands JSON imported results doctor should not write install config")
        assert_true(command_by_key["imported_desktop_results_doctor"]["requires_author_confirmation"] is False, "desktop-pack commands JSON imported results doctor should not require author confirmation")
        assert_true(f"imported-{pack['result_file']}" in command_by_key["imported_desktop_results_doctor"]["input_files"], "desktop-pack commands JSON imported results doctor missed imported result input")
        assert_true("sanitize_evidence" in command_by_key, "desktop-pack commands JSON missed evidence sanitizer")
        assert_true(command_by_key["sanitize_evidence"]["writes_project_state"] is False, "desktop-pack evidence sanitizer should not write project state")
        assert_true(command_by_key["sanitize_evidence"]["writes_install_config"] is False, "desktop-pack evidence sanitizer should not write install config")
        assert_true(command_by_key["sanitize_evidence"]["writes_pack_files"] is True, "desktop-pack evidence sanitizer should write sanitized pack file")
        assert_true("desktop-evidence.txt" in command_by_key["sanitize_evidence"]["input_files"], "desktop-pack evidence sanitizer missed input file")
        assert_true("desktop-evidence.sanitized.txt" in command_by_key["sanitize_evidence"]["output_files"], "desktop-pack evidence sanitizer missed output file")
        assert_true(command_by_key["record_imported_desktop_check"]["writes_project_state"] is True, "desktop-pack commands JSON imported record should write project state")
        assert_true(f"imported-{pack['result_file']}" in command_by_key["record_imported_desktop_check"]["input_files"], "desktop-pack commands JSON imported record missed input file")
        assert_true("writer-mode-readiness" in commands, "desktop-pack commands missed writer readiness CLI command")
        assert_true("author_review_queue" in commands, "desktop-pack commands missed author review queue command")
        assert_true("author-review-queue" in commands, "desktop-pack commands missed author-review-queue CLI command")
        assert_true("desktop_next_step" in commands, "desktop-pack commands missed desktop next step command")
        assert_true("desktop-next-step" in commands, "desktop-pack commands missed desktop-next-step CLI command")
        assert_true("desktop_diagnostics" in commands, "desktop-pack commands missed desktop diagnostics command")
        assert_true("desktop-diagnostics" in commands, "desktop-pack commands missed desktop-diagnostics CLI command")
        assert_true("desktop_user_steps" in commands, "desktop-pack commands missed desktop user steps command")
        assert_true("desktop-user-steps" in commands, "desktop-pack commands missed desktop-user-steps CLI command")
        assert_true("desktop_evidence_session" in commands, "desktop-pack commands missed desktop evidence session command")
        assert_true("desktop-evidence-session" in commands, "desktop-pack commands missed desktop-evidence-session CLI command")
        assert_true("desktop_evidence_runbook" in commands, "desktop-pack commands missed desktop evidence runbook command")
        assert_true("desktop-evidence-runbook" in commands, "desktop-pack commands missed desktop-evidence-runbook CLI command")
        assert_true(command_by_key["writer_install_apply"]["writes_install_config"] is True, "desktop-pack commands JSON writer install should write install config")
        assert_true(any("real GUI desktop-client" in note for note in commands_json["safety_notes"]), "desktop-pack commands JSON missed real GUI evidence boundary")
        assert_true("open_pack_index" in commands, "desktop-pack commands missed open local command")
        assert_true("open-local.sh" in commands, "desktop-pack commands missed open local script path")
        assert_true("powershell_open_launcher" in commands, "desktop-pack commands missed PowerShell open launcher")
        assert_true("open-local.ps1" in commands, "desktop-pack commands missed PowerShell open launcher path")
        assert_true("windows_open_launcher" in commands, "desktop-pack commands missed Windows open launcher")
        assert_true("open-local.cmd" in commands, "desktop-pack commands missed Windows open launcher path")
        assert_true("macos_open_launcher" in commands, "desktop-pack commands missed macOS open launcher")
        assert_true("open-local.command" in commands, "desktop-pack commands missed macOS open launcher path")
        assert_true("desktop_pack_archive" in commands, "desktop-pack commands missed archive command")
        assert_true("archive-pack" in commands, "desktop-pack commands missed archive-pack command")
        assert_true("pack_doctor" in commands, "desktop-pack commands missed pack doctor command")
        assert_true("pack_doctor_json" in commands, "desktop-pack commands missed pack doctor JSON command")
        assert_true("pack-doctor" in commands, "desktop-pack commands missed pack-doctor CLI command")
        assert_true("pack-doctor.json" in commands, "desktop-pack commands missed pack doctor JSON path")
        assert_true("--output" in commands, "desktop-pack commands missed pack doctor output flag")
        assert_true("list_agent_activity" in commands, "desktop-pack commands missed list agent activity command")
        assert_true("list-agent-activity" in commands, "desktop-pack commands missed list-agent-activity CLI command")
        assert_true("agent_activity_report" in commands, "desktop-pack commands missed agent activity report command")
        assert_true("agent-activity-report" in commands, "desktop-pack commands missed agent-activity-report CLI command")
        assert_true("handoff_integrity" in commands, "desktop-pack commands missed handoff integrity command")
        assert_true("handoff-integrity" in commands, "desktop-pack commands missed handoff-integrity CLI command")
        assert_true("handoff_range_report" in commands, "desktop-pack commands missed handoff range report command")
        assert_true("handoff-range-report" in commands, "desktop-pack commands missed handoff-range-report CLI command")
        assert_true("handoff_report" in commands, "desktop-pack commands missed handoff report command")
        assert_true("handoff-report" in commands, "desktop-pack commands missed handoff-report CLI command")
        assert_true("handoff_readiness" in commands, "desktop-pack commands missed handoff readiness command")
        assert_true("handoff-readiness" in commands, "desktop-pack commands missed handoff-readiness CLI command")
        assert_true("record_agent_activity" in commands, "desktop-pack commands missed record agent activity command")
        assert_true("record-agent-activity" in commands, "desktop-pack commands missed record-agent-activity CLI command")
        assert_true("macos_install_launcher" in commands, "desktop-pack commands missed macOS install launcher command")
        assert_true("macos_local_checks_launcher" in commands, "desktop-pack commands missed macOS checks launcher command")
        assert_true("install-local.command" in commands, "desktop-pack commands missed macOS install launcher path")
        assert_true("local-checks.command" in commands, "desktop-pack commands missed macOS checks launcher path")
        assert_true("macos_archive_launcher" in commands, "desktop-pack commands missed macOS archive launcher command")
        assert_true("archive-local.command" in commands, "desktop-pack commands missed macOS archive launcher path")
        assert_true("powershell_install_launcher" in commands, "desktop-pack commands missed PowerShell install launcher command")
        assert_true("powershell_local_checks_launcher" in commands, "desktop-pack commands missed PowerShell checks launcher command")
        assert_true("install-local.ps1" in commands, "desktop-pack commands missed PowerShell install launcher path")
        assert_true("local-checks.ps1" in commands, "desktop-pack commands missed PowerShell checks launcher path")
        assert_true("windows_install_launcher" in commands, "desktop-pack commands missed Windows install launcher command")
        assert_true("windows_local_checks_launcher" in commands, "desktop-pack commands missed Windows checks launcher command")
        assert_true("install-local.cmd" in commands, "desktop-pack commands missed Windows install launcher path")
        assert_true("local-checks.cmd" in commands, "desktop-pack commands missed Windows checks launcher path")
        assert_true("--upgrade" in commands, "desktop-pack commands missed upgrade command")
        assert_true("macos_upgrade_launcher" in commands, "desktop-pack commands missed macOS upgrade launcher command")
        assert_true("upgrade-local.command" in commands, "desktop-pack commands missed macOS upgrade launcher path")
        assert_true("powershell_upgrade_launcher" in commands, "desktop-pack commands missed PowerShell upgrade launcher command")
        assert_true("upgrade-local.ps1" in commands, "desktop-pack commands missed PowerShell upgrade launcher path")
        assert_true("windows_upgrade_launcher" in commands, "desktop-pack commands missed Windows upgrade launcher command")
        assert_true("upgrade-local.cmd" in commands, "desktop-pack commands missed Windows upgrade launcher path")
        assert_true("--uninstall" in commands, "desktop-pack commands missed uninstall command")
        assert_true("macos_uninstall_launcher" in commands, "desktop-pack commands missed macOS uninstall launcher command")
        assert_true("uninstall-local.command" in commands, "desktop-pack commands missed macOS uninstall launcher path")
        assert_true("powershell_uninstall_launcher" in commands, "desktop-pack commands missed PowerShell uninstall launcher command")
        assert_true("uninstall-local.ps1" in commands, "desktop-pack commands missed PowerShell uninstall launcher path")
        assert_true("windows_uninstall_launcher" in commands, "desktop-pack commands missed Windows uninstall launcher command")
        assert_true("uninstall-local.cmd" in commands, "desktop-pack commands missed Windows uninstall launcher path")
        assert_true("powershell_archive_launcher" in commands, "desktop-pack commands missed PowerShell archive launcher command")
        assert_true("archive-local.ps1" in commands, "desktop-pack commands missed PowerShell archive launcher path")
        assert_true("windows_archive_launcher" in commands, "desktop-pack commands missed Windows archive launcher command")
        assert_true("archive-local.cmd" in commands, "desktop-pack commands missed Windows archive launcher path")
        assert_true("writer_pack" in commands, "desktop-pack commands missed writer pack command")
        assert_true("writer_mcp_config_preview" in commands, "desktop-pack commands missed writer config preview command")
        assert_true("writer_install_apply" in commands, "desktop-pack commands missed writer install command")
        assert_true("writer_desktop_checklist" in commands, "desktop-pack commands missed writer checklist command")
        assert_true("chapter-acceptance-plan" in commands, "desktop-pack commands missed chapter acceptance plan")
        assert_true("chapter_acceptance_plan" in command_by_key, "desktop-pack commands JSON missed chapter acceptance plan")
        assert_true("chapter-range-acceptance-plan" in commands, "desktop-pack commands missed chapter range acceptance plan")
        assert_true("chapter_range_acceptance_plan" in command_by_key, "desktop-pack commands JSON missed chapter range acceptance plan")
        assert_true("--writer-mcp" in commands, "desktop-pack commands missed writer MCP flag")
        assert_true("quickstart-from-wizard" in commands, "desktop-pack commands missed wizard quickstart")
        assert_true("source-intake" in commands, "desktop-pack commands missed source intake")
        assert_true("continuity-audit" in commands, "desktop-pack commands missed continuity audit")
        assert_true("import-audit" in commands, "desktop-pack commands missed import audit")
        assert_true("chapter-range-readiness" in commands, "desktop-pack commands missed chapter range readiness")
        assert_true("chapter_range_readiness" in command_by_key, "desktop-pack commands JSON missed chapter range readiness")
        assert_true("chapter-range-delivery" in commands, "desktop-pack commands missed chapter range delivery")
        assert_true("chapter_range_delivery" in command_by_key, "desktop-pack commands JSON missed chapter range delivery")
        assert_true("proposal-readiness" in commands, "desktop-pack commands missed proposal readiness")
        assert_true("proposal_readiness" in command_by_key, "desktop-pack commands JSON missed proposal readiness")
        assert_true(command_by_key["proposal_readiness"]["writes_project_state"] is False, "desktop-pack proposal readiness should be read-only")
        assert_true("desktop-matrix" in commands, "desktop-pack commands missed desktop matrix")
        assert_true("sanitize_evidence" in commands and "sanitize-desktop-evidence" in commands, "desktop-pack commands missed evidence sanitizer")
        assert_true(str(output_dir / "desktop-evidence.sanitized.txt") in commands, "desktop-pack commands missed sanitized evidence path")
        assert_true("ingest-desktop-evidence" in commands, "desktop-pack commands missed evidence ingest command")
        assert_true("desktop_evidence_packet" in commands and "desktop-evidence-packet" in commands, "desktop-pack commands missed evidence packet command")
        assert_true("desktop_evidence_runbook" in commands and "desktop-evidence-runbook" in commands, "desktop-pack commands missed evidence runbook command")
        assert_true("desktop_evidence_review" in commands and "desktop-evidence-review" in commands, "desktop-pack commands missed evidence review command")
        assert_true("desktop_evidence_gaps" in commands and "desktop-evidence-gaps" in commands, "desktop-pack commands missed evidence gaps command")
        assert_true("desktop_results_doctor" in commands, "desktop-pack commands missed results doctor command")
        assert_true("desktop-results-doctor" in commands, "desktop-pack commands missed results doctor CLI")
        assert_true("imported_desktop_results_doctor" in commands, "desktop-pack commands missed imported results doctor command")
        assert_true("record_imported_desktop_check" in commands, "desktop-pack commands missed imported record command")
        assert_true(str(output_dir / "desktop-evidence.txt") in commands, "desktop-pack commands missed pack evidence file path")
        assert_true(str(output_dir / f"imported-{pack['result_file']}") in commands, "desktop-pack commands missed imported result path")
        assert_true("record-desktop-check" in commands, "desktop-pack commands missed record command")
        assert_true("verify_pack" in commands, "desktop-pack commands missed verify pack command")
        assert_true("verify_pack_cli" in commands, "desktop-pack commands missed CLI verify pack command")
        assert_true("pack_schema_check" in commands, "desktop-pack commands missed pack schema check command")
        assert_true("pack-schema-check" in commands, "desktop-pack commands missed pack schema check CLI")
        assert_true("verify-pack.py" in commands, "desktop-pack commands missed verify pack script path")
        assert_true("verify-pack" in commands, "desktop-pack commands missed verify-pack CLI path")
        assert_true("starter_brief" in command_by_key, "desktop-pack commands JSON missed starter brief refresh command")
        assert_true("starter-brief" in commands, "desktop-pack commands missed starter brief CLI")
        assert_true(command_by_key["starter_brief"]["writes_project_state"] is False, "desktop-pack starter brief refresh should not write project state")
        assert_true(command_by_key["starter_brief"]["writes_install_config"] is False, "desktop-pack starter brief refresh should not write install config")
        assert_true(command_by_key["starter_brief"]["writes_pack_files"] is False, "desktop-pack starter brief refresh should not write pack files")
        assert_true("desktop_user_steps" in command_by_key, "desktop-pack commands JSON missed user steps refresh command")
        assert_true(command_by_key["desktop_user_steps"]["writes_project_state"] is False, "desktop-pack user steps refresh should not write project state")
        assert_true(command_by_key["desktop_user_steps"]["writes_install_config"] is False, "desktop-pack user steps refresh should not write install config")
        assert_true(command_by_key["desktop_user_steps"]["writes_pack_files"] is False, "desktop-pack user steps refresh should not write pack files")
        assert_true("agent_takeover" in command_by_key, "desktop-pack commands JSON missed agent takeover refresh command")
        assert_true("agent-takeover-from-pack" in commands, "desktop-pack commands missed agent takeover refresh CLI")
        assert_true(command_by_key["agent_takeover"]["writes_project_state"] is False, "desktop-pack agent takeover refresh should not write project state")
        assert_true(command_by_key["agent_takeover"]["writes_install_config"] is False, "desktop-pack agent takeover refresh should not write install config")
        assert_true(command_by_key["agent_takeover"]["writes_pack_files"] is False, "desktop-pack agent takeover refresh should not write pack files")
        assert_true("desktop_troubleshooting" in command_by_key, "desktop-pack commands JSON missed troubleshooting refresh command")
        assert_true("troubleshooting-from-pack" in commands, "desktop-pack commands missed troubleshooting refresh CLI")
        assert_true(command_by_key["desktop_troubleshooting"]["writes_project_state"] is False, "desktop-pack troubleshooting refresh should not write project state")
        assert_true(command_by_key["desktop_troubleshooting"]["writes_install_config"] is False, "desktop-pack troubleshooting refresh should not write install config")
        assert_true(command_by_key["desktop_troubleshooting"]["writes_pack_files"] is False, "desktop-pack troubleshooting refresh should not write pack files")
        assert_true(str(config) in commands, "desktop-pack commands missed explicit config path")
        assert_true(str(target.resolve()) in setup, "desktop-pack setup missed install target")
        assert_true("Save this JSON as the result file" in checklist, "desktop-pack checklist missed result template")
        assert_true("Long Novel Agent Desktop Verification Matrix" in desktop_matrix, "desktop-pack desktop matrix missing title")
        assert_true("codex" in desktop_matrix and "read-only" in desktop_matrix, "desktop-pack desktop matrix missed selected platform mode")
        assert_true(desktop_matrix_json["type"] == "desktop_verification_matrix", "desktop-pack desktop matrix JSON wrong type")
        assert_true(isinstance(desktop_matrix_json.get("matrix"), list), "desktop-pack desktop matrix JSON missed matrix list")
        assert_true(isinstance(desktop_matrix_json.get("totals"), dict), "desktop-pack desktop matrix JSON missed totals")
        matrix_totals = desktop_matrix_json.get("totals") if isinstance(desktop_matrix_json.get("totals"), dict) else {}
        assert_true(matrix_totals.get("required_checks_total", 0) >= 8, "desktop-pack desktop matrix missed required check total coverage")
        assert_true(matrix_totals.get("cells_with_gaps", 0) >= 1, "desktop-pack desktop matrix missed cells with gaps coverage")
        assert_true("Required checks passed:" in desktop_matrix, "desktop-pack desktop matrix markdown missed required coverage summary")
        assert_true("| Platform | Mode | Status | Required | Gaps | Chapter | Client | Issues |" in desktop_matrix, "desktop-pack desktop matrix markdown missed coverage columns")
        selected_matrix_cell = next((item for item in desktop_matrix_json["matrix"] if isinstance(item, dict) and item.get("platform") == "codex" and item.get("mode") == "read-only"), {})
        assert_true(isinstance(selected_matrix_cell.get("coverage"), dict), "desktop-pack desktop matrix cell missed coverage object")
        assert_true(selected_matrix_cell.get("coverage_status") == selected_matrix_cell["coverage"].get("status"), "desktop-pack desktop matrix coverage status mismatch")
        assert_true(isinstance(selected_matrix_cell.get("missing_required_check_ids"), list), "desktop-pack desktop matrix cell missed missing check ids")
        assert_true(isinstance(selected_matrix_cell["coverage"].get("blocking_check_ids"), list), "desktop-pack desktop matrix coverage missed blocking ids")
        template = result_json
        desktop_pack_schema_summary = verify_desktop_pack_json_schemas(
            pack_index_json,
            pack_index_schema,
            commands_index_json,
            commands_index_schema,
            local_summary_json,
            local_summary_schema,
            first_three_json,
            first_three_schema,
            user_steps_json,
            user_steps_schema,
            desktop_next_step_json,
            desktop_next_step_schema,
            desktop_diagnostics_json,
            desktop_diagnostics_schema,
            desktop_pack_readiness_json,
            desktop_pack_readiness_schema,
            starter_brief_json,
            starter_brief_schema,
            agent_startup_prompt_json,
            agent_startup_prompt_schema,
            troubleshooting_json,
            troubleshooting_schema,
            agent_takeover_json,
            agent_takeover_schema,
            author_actions_json,
            author_actions_schema,
            acceptance_review_json,
            acceptance_review_schema,
            desktop_evidence_session_json,
            desktop_evidence_session_schema,
            desktop_evidence_runbook_json,
            desktop_evidence_runbook_schema,
            desktop_evidence_review_json,
            desktop_evidence_review_schema,
            desktop_evidence_gaps_json,
            writer_mode_approval_json,
            writer_mode_approval_schema,
            project_wizard_json,
            project_wizard_schema,
            source_intake_json,
            source_intake_schema,
            chapter_session_json,
            chapter_session_schema,
            handoff_report_json,
            handoff_report_schema,
            template,
            results_schema,
        )
        assert_true(all(item["status"] == "not_checked" for item in template["checks"]), "desktop-pack result template should start unchecked")
        verification_path = project / ".novel-agent" / "desktop_verifications.jsonl"
        verification_before_template_doctor = verification_path.read_text(encoding="utf-8") if verification_path.exists() else None
        template_doctor_run = run_cli(
            "desktop-results-doctor",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--results",
            str(output_dir / pack["result_file"]),
            "--format",
            "json",
            check=False,
        )
        assert_true(template_doctor_run.returncode != 0, "desktop-results-doctor should return non-zero for an empty result template")
        template_doctor = load_stdout(template_doctor_run)
        assert_true(template_doctor["type"] == "desktop_results_doctor", "desktop-results-doctor empty template returned wrong type")
        assert_true(template_doctor["status"] == "needs_revision", "desktop-results-doctor empty template should need revision")
        assert_true(template_doctor["ok"] is False, "desktop-results-doctor empty template should not be OK")
        assert_true(template_doctor["would_write_project_state"] is False, "desktop-results-doctor should be read-only")
        assert_true(any(item.get("type") == "required_check_not_passed" for item in template_doctor["issues"]), "desktop-results-doctor empty template missed failed required issue")
        verification_after_template_doctor = verification_path.read_text(encoding="utf-8") if verification_path.exists() else None
        assert_true(verification_after_template_doctor == verification_before_template_doctor, "desktop-results-doctor empty template should not record durable verification state")
        weak_template = json.loads(json.dumps(template, ensure_ascii=False))
        for item in weak_template["checks"]:
            item["status"] = "passed"
            item["evidence"] = "ok" if item.get("id") == "local_config_preflight" else f"desktop-pack sample evidence for {item.get('id')}: long-novel-agent tool returned status=passed."
            if item.get("id") == "range_readiness_gate":
                item["evidence"] = "build_chapter_range_readiness returned type=chapter_range_readiness status=needs_files."
        weak_doctor_run = run_cli(
            "desktop-results-doctor",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--results",
            json.dumps(weak_template, ensure_ascii=False),
            "--format",
            "json",
            check=False,
        )
        assert_true(weak_doctor_run.returncode != 0, "desktop-results-doctor should return non-zero for weak evidence")
        weak_doctor = load_stdout(weak_doctor_run)
        assert_true(weak_doctor["status"] == "needs_revision", "desktop-results-doctor weak evidence should need revision")
        assert_true(any(item.get("type") == "weak_evidence" for item in weak_doctor["issues"]), "desktop-results-doctor missed weak evidence issue")
        metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
        assert_true(metadata["type"] == "desktop_pack", "desktop-pack metadata returned wrong type")
        assert_true(metadata["result_file"] == pack["result_file"], "desktop-pack metadata result file mismatch")
        assert_true(metadata["imported_result_file"] == f"imported-{pack['result_file']}", "desktop-pack metadata imported result file mismatch")
        assert_true(metadata["continuity_audit_status"] in {"blocked", "needs_review", "ready_with_warnings", "ready"}, "desktop-pack metadata missed continuity audit status")
        assert_true(metadata["chapter_session_type"] == "chapter_session_packet", "desktop-pack metadata missed chapter session type")
        assert_true(metadata["chapter_session_status"] in {"ready", "needs_attention"}, "desktop-pack metadata missed chapter session status")
        assert_true(metadata["handoff_brief_status"] in {"ready", "ready_with_warnings", "blocked", "pending", "unknown"}, "desktop-pack metadata missed handoff brief status")
        assert_true(metadata["handoff_report_available"] is True, "desktop-pack metadata missed handoff report availability")
        assert_true(metadata["handoff_brief_read_only"] is True, "desktop-pack metadata missed handoff brief read-only flag")
        assert_true(metadata["project_state_fingerprint"]["type"] == "project_state_fingerprint", "desktop-pack metadata missed project state fingerprint")
        assert_true(metadata["project_state_fingerprint"]["state_exists"] is True, "desktop-pack metadata should record initialized state")
        assert_true(metadata["project_state_fingerprint"]["sha256"] == pack_freshness_cli["baseline"]["sha256"], "desktop-pack freshness baseline should come from metadata")
        assert_true(pack_manifest["type"] == "desktop_pack_manifest", "desktop-pack manifest returned wrong type")
        assert_true(pack_manifest["self_excluded"] == "pack-manifest.json", "desktop-pack manifest should exclude itself")
        assert_true("pack-doctor.json" in pack_manifest.get("refreshable_excluded", []), "desktop-pack manifest missed refreshable pack doctor exclusion")
        assert_true(pack_manifest["file_count"] == len([item for item in pack["written_files"] if item.get("name") not in {"pack-manifest.json", "pack-doctor.json"}]), "desktop-pack manifest file count mismatch")
        manifest_names = {item.get("name") for item in pack_manifest["files"] if isinstance(item, dict)}
        assert_true("metadata.json" in manifest_names, "desktop-pack manifest missed metadata")
        assert_true("verify-pack.py" in manifest_names, "desktop-pack manifest missed verify script")
        assert_true("pack-manifest.json" not in manifest_names, "desktop-pack manifest should not include itself")
        assert_true("pack-doctor.json" not in manifest_names, "desktop-pack manifest should not include refreshable pack doctor JSON")
        assert_true(verify_pack_script.startswith("#!/usr/bin/env python3"), "desktop-pack verify script missing shebang")
        assert_true("pack-manifest.json" in verify_pack_script, "desktop-pack verify script missed manifest filename")
        assert_true(os.access(output_dir / "verify-pack.py", os.X_OK), "desktop-pack verify script should be executable")
        assert_true(verify_pack_result["ok"], "desktop-pack verify script should pass generated pack")
        assert_true(verify_pack_result["checked"] == pack_manifest["file_count"], "desktop-pack verify script checked count mismatch")
        assert_true(verify_pack_cli["ok"], "desktop-pack verify CLI should pass generated pack")
        assert_true(verify_pack_cli["checked"] == pack_manifest["file_count"], "desktop-pack verify CLI checked count mismatch")
        assert_true(pack_doctor_report["type"] == "desktop_pack_doctor", "desktop-pack doctor returned wrong type")
        assert_true(pack_doctor_report["ok"], "desktop-pack doctor should pass generated pack")
        assert_true(pack_doctor_report["blocker_count"] == 0, "desktop-pack doctor should have no blockers for generated pack")
        assert_true(pack_doctor_report["command_count"] == commands_json["command_count"], "desktop-pack doctor command count mismatch")
        assert_true(pack_doctor_json["type"] == "desktop_pack_doctor", "desktop-pack generated pack doctor JSON returned wrong type")
        assert_true(pack_doctor_json["ok"], "desktop-pack generated pack doctor JSON should pass generated pack")
        assert_true(pack_doctor_json["blocker_count"] == 0, "desktop-pack generated pack doctor JSON should have no blockers")
        assert_true(refreshed_pack_doctor_report["type"] == "desktop_pack_doctor", "desktop-pack refreshed pack doctor report returned wrong type")
        assert_true(refreshed_pack_doctor_report["ok"], "desktop-pack refreshed pack doctor report should pass generated pack")
        assert_true(refreshed_pack_doctor_json["type"] == "desktop_pack_doctor", "desktop-pack refreshed pack doctor JSON returned wrong type")
        assert_true(refreshed_pack_doctor_json["output"] == str(refreshed_pack_doctor_path.resolve()), "desktop-pack refreshed pack doctor JSON missed output path")
        doctor_checks = {item.get("id"): item for item in pack_doctor_report.get("checks", []) if isinstance(item, dict)}
        assert_true(doctor_checks.get("pack_integrity", {}).get("ok") is True, "desktop-pack doctor should pass pack integrity")
        assert_true(doctor_checks.get("pack_schema_check", {}).get("ok") is True, "desktop-pack doctor should pass pack schema check")
        assert_true(pack_doctor_report["pack_schema_check"]["ok"] is True, "desktop-pack doctor should expose pack schema check summary")
        assert_true(doctor_checks.get("required_commands", {}).get("ok") is True, "desktop-pack doctor should pass required commands")
        assert_true(doctor_checks.get("command_kit_paths", {}).get("ok") is True, "desktop-pack doctor should pass command kit path check")
        assert_true(doctor_checks.get("required_files", {}).get("ok") is True, "desktop-pack doctor should pass required files")
        assert_true(doctor_checks.get("local_summary_schema", {}).get("ok") is True, "desktop-pack doctor should pass local summary schema check")
        assert_true(doctor_checks.get("user_steps_json", {}).get("ok") is True, "desktop-pack doctor should pass user steps JSON check")
        assert_true(doctor_checks.get("user_steps_schema", {}).get("ok") is True, "desktop-pack doctor should pass user steps schema check")
        assert_true(doctor_checks.get("user_steps_html", {}).get("ok") is True, "desktop-pack doctor should pass user steps HTML check")
        assert_true(doctor_checks.get("starter_brief_json", {}).get("ok") is True, "desktop-pack doctor should pass starter brief JSON check")
        assert_true(doctor_checks.get("starter_brief_schema", {}).get("ok") is True, "desktop-pack doctor should pass starter brief schema check")
        assert_true(doctor_checks.get("agent_takeover_json", {}).get("ok") is True, "desktop-pack doctor should pass agent takeover JSON check")
        assert_true(doctor_checks.get("agent_takeover_schema", {}).get("ok") is True, "desktop-pack doctor should pass agent takeover schema check")
        assert_true(doctor_checks.get("agent_startup_prompt_json", {}).get("ok") is True, "desktop-pack doctor should pass agent startup prompt JSON check")
        assert_true(doctor_checks.get("agent_startup_prompt_schema", {}).get("ok") is True, "desktop-pack doctor should pass agent startup prompt schema check")
        assert_true(doctor_checks.get("troubleshooting_json", {}).get("ok") is True, "desktop-pack doctor should pass troubleshooting JSON check")
        assert_true(doctor_checks.get("troubleshooting_schema", {}).get("ok") is True, "desktop-pack doctor should pass troubleshooting schema check")
        assert_true(doctor_checks.get("author_actions_json", {}).get("ok") is True, "desktop-pack doctor should pass author actions JSON check")
        assert_true(doctor_checks.get("author_actions_schema", {}).get("ok") is True, "desktop-pack doctor should pass author actions schema check")
        assert_true(doctor_checks.get("acceptance_review_json", {}).get("ok") is True, "desktop-pack doctor should pass acceptance review JSON check")
        assert_true(doctor_checks.get("acceptance_review_schema", {}).get("ok") is True, "desktop-pack doctor should pass acceptance review schema check")
        assert_true(doctor_checks.get("writer_mode_approval_json", {}).get("ok") is True, "desktop-pack doctor should pass writer approval JSON check")
        assert_true(doctor_checks.get("writer_mode_approval_schema", {}).get("ok") is True, "desktop-pack doctor should pass writer approval schema check")
        assert_true(doctor_checks.get("pack_doctor_json", {}).get("ok") is True, "desktop-pack doctor should see generated pack doctor JSON")
        missing_kit_pack = Path(tmp) / "pack missing kit"
        shutil.copytree(output_dir, missing_kit_pack)
        missing_kit_root = Path(tmp) / "missing-long-novel-agent"
        missing_manifest_path = missing_kit_pack / "pack-manifest.json"
        missing_manifest = json.loads(missing_manifest_path.read_text(encoding="utf-8"))
        changed_pack_files: dict[str, bytes] = {}
        for path in sorted(missing_kit_pack.rglob("*")):
            if not path.is_file() or path.name in {"pack-manifest.json", "pack-doctor.json"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            replaced = text.replace(str(KIT_DIR), str(missing_kit_root))
            if replaced == text:
                continue
            path.write_text(replaced, encoding="utf-8")
            changed_pack_files[str(path.relative_to(missing_kit_pack))] = replaced.encode("utf-8")
        assert_true("commands.json" in changed_pack_files, "desktop-pack missing kit scenario should modify commands.json")
        assert_true("commands.txt" in changed_pack_files, "desktop-pack missing kit scenario should modify commands.txt")
        changed_pack_files["source-intake.json"] = (missing_kit_pack / "source-intake.json").read_bytes()
        for entry in missing_manifest["files"]:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            encoded = changed_pack_files.get(name)
            if encoded is None:
                continue
            entry["bytes"] = len(encoded)
            entry["sha256"] = hashlib.sha256(encoded).hexdigest()
        missing_manifest_path.write_text(json.dumps(missing_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        missing_kit_doctor = load_stdout(run_cli("pack-doctor", str(missing_kit_pack), "--format", "json"))
        missing_kit_checks = {item.get("id"): item for item in missing_kit_doctor.get("checks", []) if isinstance(item, dict)}
        missing_kit_path_check = missing_kit_checks.get("command_kit_paths", {})
        assert_true(
            missing_kit_doctor["ok"],
            "desktop-pack doctor should keep missing kit command paths as warning only: "
            + json.dumps([item for item in missing_kit_doctor.get("checks", []) if not item.get("ok") and item.get("severity") == "blocker"], ensure_ascii=False)[:1000],
        )
        assert_true(not missing_kit_path_check.get("ok"), "desktop-pack doctor should warn on missing command kit paths")
        assert_true(missing_kit_path_check.get("severity") == "warning", "desktop-pack doctor missing kit paths should be warning")
        assert_true(missing_kit_path_check.get("detail", {}).get("missing"), "desktop-pack doctor missing kit path detail should list missing paths")
        assert_true(any("Install or upgrade Long Novel Agent" in str(action) for action in missing_kit_doctor["recommended_actions"]), "desktop-pack doctor should recommend installing or regenerating for missing kit paths")
        rebind_dry_run = load_stdout(run_cli("rebind-pack-kit", str(missing_kit_pack), "--format", "json"))
        assert_true(rebind_dry_run["status"] == "planned", "desktop-pack rebind dry-run should plan stale path changes")
        assert_true(rebind_dry_run["planned_file_count"] >= 2, "desktop-pack rebind dry-run should report changed pack files")
        assert_true(rebind_dry_run["planned_replacement_count"] >= len(rebind_dry_run["replacements"]), "desktop-pack rebind dry-run should count replacements")
        assert_true(not rebind_dry_run["applied"], "desktop-pack rebind dry-run should not apply changes")
        assert_true(load_stdout(run_cli("verify-pack", str(missing_kit_pack), "--format", "json"))["ok"], "desktop-pack should remain manifest-clean before rebind apply")
        rebind_apply = load_stdout(run_cli("rebind-pack-kit", str(missing_kit_pack), "--apply", "--format", "json"))
        assert_true(rebind_apply["ok"], "desktop-pack rebind apply should succeed")
        assert_true(rebind_apply["applied"], "desktop-pack rebind apply should mark applied")
        assert_true(rebind_apply["manifest_updated"], "desktop-pack rebind apply should update manifest")
        assert_true(rebind_apply["pack_doctor_refreshed"], "desktop-pack rebind apply should refresh pack doctor")
        assert_true(rebind_apply["verify_after"]["ok"], "desktop-pack rebind apply should leave pack manifest clean")
        assert_true(rebind_apply["doctor_after"]["command_kit_paths_ok"] is True, "desktop-pack rebind apply should restore command kit path check")
        assert_true(corrupt_verify_cli_run.returncode != 0, "desktop-pack verify CLI should return non-zero for missing files")
        assert_true(not corrupt_verify_cli["ok"], "desktop-pack verify CLI should fail corrupt pack")
        assert_true(any(issue.get("type") == "missing_file" and issue.get("file") == "README.md" for issue in corrupt_verify_cli["issues"]), "desktop-pack verify CLI should report missing file")
        assert_true(not corrupt_doctor_cli["ok"], "desktop-pack doctor should fail corrupt pack")
        assert_true(corrupt_doctor_cli["blocker_count"] > 0, "desktop-pack doctor corrupt pack should report blockers")
        verification_path = project / ".novel-agent" / "desktop_verifications.jsonl"
        verification_before = verification_path.read_text(encoding="utf-8") if verification_path.exists() else None
        scratchpad_template = (output_dir / "desktop-evidence.txt").read_text(encoding="utf-8")
        assert_true("LONG_NOVEL_AGENT_DESKTOP_EVIDENCE_SCRATCHPAD_V1" in scratchpad_template, "desktop-pack evidence scratchpad missed parser marker")
        assert_true("## freeform" in scratchpad_template, "desktop-pack evidence scratchpad missed freeform evidence section")
        assert_true("## range_readiness_gate" in scratchpad_template, "desktop-pack evidence scratchpad missed range readiness section")
        blank_scratchpad_import = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                str(output_dir / "desktop-evidence.txt"),
                "--format",
                "json",
            )
        )
        assert_true(not blank_scratchpad_import["ok"], "blank desktop evidence scratchpad should not pass required checks")
        assert_true(blank_scratchpad_import["source_summary"]["scratchpad_template_detected"], "blank scratchpad import should detect template marker")
        assert_true(blank_scratchpad_import["source_summary"]["match_characters"] == 0, "blank scratchpad import should ignore prompt text")
        assert_true(blank_scratchpad_import["matched_check_ids"] == [], "blank scratchpad prompt text should not match checks")
        assert_true("local_config_preflight" in blank_scratchpad_import["missing_required_check_ids"], "blank scratchpad import missed required local config gap")
        evidence_example = KIT_DIR / "examples" / "desktop-verification-evidence" / "codex-read-only-evidence.txt"
        evidence_text = evidence_example.read_text(encoding="utf-8")

        def fill_desktop_evidence_scratchpad(template_text: str, section_id: str, evidence: str) -> str:
            current = ""
            output_lines: list[str] = []
            for raw_line in template_text.splitlines():
                output_lines.append(raw_line)
                section_match = re.match(r"^\s*##\s+([A-Za-z0-9_.:-]+)\s*$", raw_line)
                if section_match:
                    current = section_match.group(1).strip()
                    continue
                if current == section_id and raw_line.strip().lower() == "- evidence:":
                    output_lines.extend(evidence.splitlines())
            return "\n".join(output_lines).rstrip() + "\n"

        (output_dir / "desktop-evidence.txt").write_text(fill_desktop_evidence_scratchpad(scratchpad_template, "freeform", evidence_text), encoding="utf-8")
        sanitized_pack_path = output_dir / "desktop-evidence.sanitized.txt"
        pack_sanitized = load_stdout(
            run_cli(
                "sanitize-desktop-evidence",
                str(project),
                "--evidence",
                str(output_dir / "desktop-evidence.txt"),
                "--output",
                str(sanitized_pack_path),
                "--format",
                "json",
            )
        )
        assert_true(pack_sanitized["type"] == "desktop_evidence_sanitizer", "desktop-pack evidence sanitizer returned wrong type")
        assert_true(pack_sanitized["writes_output_file"], "desktop-pack evidence sanitizer should write sanitized evidence file")
        assert_true(sanitized_pack_path.exists(), "desktop-pack evidence sanitizer did not write sanitized evidence file")
        assert_true("sk-" not in sanitized_pack_path.read_text(encoding="utf-8"), "desktop-pack sanitized evidence should not contain API key prefix")
        imported_result_path = output_dir / metadata["imported_result_file"]
        evidence_packet = load_stdout(
            run_cli(
                "desktop-evidence-packet",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                str(output_dir / "desktop-evidence.txt"),
                "--results-reference",
                str(imported_result_path),
                "--format",
                "json",
            )
        )
        assert_true(evidence_packet["type"] == "desktop_evidence_packet", "desktop evidence packet returned wrong type")
        assert_true(evidence_packet["ok"], "desktop evidence packet should pass sample evidence")
        assert_true(evidence_packet["status"] == "ready_to_record", "desktop evidence packet should be ready for sample evidence")
        assert_true(evidence_packet["would_write_project_state"] is False, "desktop evidence packet should not write project state")
        assert_true(evidence_packet["would_write_install_config"] is False, "desktop evidence packet should not write install config")
        assert_true(evidence_packet["would_write_pack_files"] is False, "desktop evidence packet should not write pack files")
        assert_true(evidence_packet["import_report"]["missing_required_check_ids"] == [], "desktop evidence packet should match required checks")
        assert_true(evidence_packet["results_doctor"]["status"] == "ready_to_record", "desktop evidence packet should include ready results doctor")
        evidence_packet_markdown = run_cli(
            "desktop-evidence-packet",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--evidence",
            str(output_dir / "desktop-evidence.txt"),
            "--results-reference",
            str(imported_result_path),
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Desktop Evidence Packet" in evidence_packet_markdown.stdout, "desktop evidence packet markdown missing title")
        assert_true("ready_to_record" in evidence_packet_markdown.stdout, "desktop evidence packet markdown missed ready status")
        evidence_review = load_stdout(
            run_cli(
                "desktop-evidence-review",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                str(output_dir / "desktop-evidence.txt"),
                "--results-reference",
                str(imported_result_path),
                "--format",
                "json",
            )
        )
        assert_true(evidence_review["type"] == "desktop_evidence_review", "desktop evidence review returned wrong type")
        assert_true(evidence_review["ok"], "desktop evidence review should pass sample evidence")
        assert_true(evidence_review["status"] == "ready_to_record", "desktop evidence review should be ready for sample evidence before recording")
        assert_true(evidence_review["would_write_project_state"] is False, "desktop evidence review should not write project state")
        assert_true(evidence_review["would_write_install_config"] is False, "desktop evidence review should not write install config")
        assert_true(evidence_review["would_write_pack_files"] is False, "desktop evidence review should not write pack files")
        assert_true(evidence_review["records_real_client_evidence"] is False, "desktop evidence review should not record real client evidence")
        assert_true(evidence_review["results_doctor"]["status"] == "ready_to_record", "desktop evidence review should include ready results doctor")
        assert_true(evidence_review["saved_verification"]["exact"]["status"] in {"missing", "passed", "failed"}, "desktop evidence review should summarize exact saved verification")
        evidence_review_markdown = run_cli(
            "desktop-evidence-review",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--evidence",
            str(output_dir / "desktop-evidence.txt"),
            "--results-reference",
            str(imported_result_path),
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Desktop Evidence Review" in evidence_review_markdown.stdout, "desktop evidence review markdown missing title")
        assert_true("ready_to_record" in evidence_review_markdown.stdout, "desktop evidence review markdown missed ready status")
        evidence_review_html_cli = run_cli(
            "desktop-evidence-review",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--evidence",
            str(output_dir / "desktop-evidence.txt"),
            "--results-reference",
            str(imported_result_path),
            "--format",
            "html",
        )
        assert_true("<!doctype html>" in evidence_review_html_cli.stdout, "desktop evidence review HTML missing doctype")
        assert_true("Long Novel Agent Desktop Evidence Review" in evidence_review_html_cli.stdout, "desktop evidence review HTML missing title")
        assert_true("record_desktop_check" in evidence_review_html_cli.stdout, "desktop evidence review HTML missed record command")
        assert_true("Local File Recheck" in evidence_review_html_cli.stdout, "desktop evidence review HTML missed local recheck section")
        assert_true('id="resultsFile"' in evidence_review_html_cli.stdout, "desktop evidence review HTML missed results file input")
        assert_true('id="localResultsPreview"' in evidence_review_html_cli.stdout, "desktop evidence review HTML missed local results preview")
        assert_true('id="downloadResultsPreview"' in evidence_review_html_cli.stdout, "desktop evidence review HTML missed preview download button")
        assert_true("function parseStructuredEvidenceText" in evidence_review_html_cli.stdout, "desktop evidence review HTML missed structured evidence parser")
        assert_true("function downloadResultsPreview" in evidence_review_html_cli.stdout, "desktop evidence review HTML missed preview downloader")
        assert_true("function analyzeLoadedFiles" in evidence_review_html_cli.stdout, "desktop evidence review HTML missed local analyzer")
        evidence_gaps = load_stdout(
            run_cli(
                "desktop-evidence-gaps",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                str(output_dir / "desktop-evidence.txt"),
                "--results-reference",
                str(imported_result_path),
                "--format",
                "json",
            )
        )
        assert_true(evidence_gaps["type"] == "desktop_evidence_gaps", "desktop evidence gaps returned wrong type")
        assert_true(evidence_gaps["ok"], "desktop evidence gaps should pass sample evidence")
        assert_true(evidence_gaps["status"] == "ready_to_record", "desktop evidence gaps should be ready for sample evidence before recording")
        assert_true(evidence_gaps["would_write_project_state"] is False, "desktop evidence gaps should not write project state")
        assert_true(evidence_gaps["would_write_install_config"] is False, "desktop evidence gaps should not write install config")
        assert_true(evidence_gaps["would_write_pack_files"] is False, "desktop evidence gaps should not write pack files")
        assert_true(evidence_gaps["records_real_client_evidence"] is False, "desktop evidence gaps should not record real client evidence")
        assert_true(evidence_gaps["summary"]["missing_check_count"] == 0, "desktop evidence gaps should have no missing required checks for sample evidence")
        assert_true(all(item.get("state") == "passed" for item in evidence_gaps["gaps"]), "desktop evidence gaps should mark every sample check passed")
        evidence_gaps_markdown = run_cli(
            "desktop-evidence-gaps",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--evidence",
            str(output_dir / "desktop-evidence.txt"),
            "--results-reference",
            str(imported_result_path),
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Desktop Evidence Gaps" in evidence_gaps_markdown.stdout, "desktop evidence gaps markdown missing title")
        assert_true("ready_to_record" in evidence_gaps_markdown.stdout, "desktop evidence gaps markdown missed ready status")
        imported_evidence = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                str(output_dir / "desktop-evidence.txt"),
                "--output",
                str(imported_result_path),
                "--format",
                "json",
            )
        )
        assert_true(imported_evidence["ok"], "desktop-pack evidence-to-results import should pass sample evidence")
        assert_true(imported_evidence["source_summary"]["scratchpad_template_detected"], "desktop-pack evidence import should detect filled scratchpad")
        assert_true(imported_evidence["source_summary"]["scratchpad_filled_sections"] == ["freeform"], "desktop-pack evidence import should read filled freeform section")
        assert_true(imported_result_path.exists(), "desktop-pack evidence-to-results did not write imported result JSON")
        imported_payload = json.loads(imported_result_path.read_text(encoding="utf-8"))
        assert_true(len(imported_payload["checks"]) == len(template["checks"]), "desktop-pack imported result check count mismatch")
        assert_true(all(item["status"] == "passed" for item in imported_payload["checks"]), "desktop-pack imported result should pass sample checks")
        assert_true(imported_evidence["missing_required_check_ids"] == [], "desktop-pack evidence-to-results should match required checks")
        assert_true(len(imported_evidence["match_details"]) >= len(template["checks"]), "desktop-pack evidence-to-results missed match details")
        imported_doctor = load_stdout(
            run_cli(
                "desktop-results-doctor",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                str(imported_result_path),
                "--format",
                "json",
            )
        )
        assert_true(imported_doctor["type"] == "desktop_results_doctor", "desktop-results-doctor imported result returned wrong type")
        assert_true(imported_doctor["ok"], "desktop-results-doctor should pass imported sample result")
        assert_true(imported_doctor["status"] == "ready_to_record", "desktop-results-doctor imported sample should be ready")
        assert_true(imported_doctor["required_passed_with_evidence"] == imported_doctor["required_total"], "desktop-results-doctor imported sample missed required evidence")
        assert_true("record-desktop-check" in imported_doctor["record_command"], "desktop-results-doctor missed record command")
        verification_after = verification_path.read_text(encoding="utf-8") if verification_path.exists() else None
        assert_true(verification_after == verification_before, "desktop-pack evidence import should not record durable verification state")
        blocked = run_cli(
            "desktop-pack",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--install-config",
            str(config),
            "--chapter",
            "2",
            "--output-dir",
            str(output_dir),
            "--format",
            "json",
            check=False,
        )
        assert_true(blocked.returncode != 0, "desktop-pack should refuse existing files without --force")
        assert_true("already exists" in blocked.stderr, "desktop-pack overwrite refusal should name existing files")
        markdown = run_cli(
            "desktop-pack",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--install-config",
            str(config),
            "--chapter",
            "2",
            "--output-dir",
            str(output_dir),
            "--force",
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Desktop Pack" in markdown.stdout, "desktop-pack markdown missing title")
        assert_true("README.md" in markdown.stdout, "desktop-pack markdown missed written files")
        uninitialized_project = Path(tmp) / "fresh novel"
        uninitialized_output = Path(tmp) / "fresh pack"
        uninitialized_pack = load_stdout(
            run_cli(
                "desktop-pack",
                str(uninitialized_project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "1",
                "--output-dir",
                str(uninitialized_output),
                "--format",
                "json",
            )
        )
        uninitialized_session_json = json.loads((uninitialized_output / "chapter-session.json").read_text(encoding="utf-8"))
        uninitialized_session_md = (uninitialized_output / "chapter-session.md").read_text(encoding="utf-8")
        assert_true(uninitialized_pack["chapter_session_type"] == "chapter_session_pending", "uninitialized desktop-pack should write pending chapter session")
        assert_true(uninitialized_session_json["$schema"] == "chapter-session.schema.json", "uninitialized chapter session JSON should point to local schema")
        assert_true(uninitialized_session_json["status"] == "not_initialized", "uninitialized chapter session JSON missed not_initialized status")
        assert_true(isinstance(uninitialized_session_json.get("project_state_fingerprint"), dict), "uninitialized chapter session JSON missed project state fingerprint")
        assert_true("Run quickstart or quickstart-from-wizard before writing" in uninitialized_session_md, "uninitialized chapter session markdown missed next action")
        handoff_path = project / ".novel-agent" / "handoff.md"
        original_handoff = handoff_path.read_text(encoding="utf-8")
        handoff_path.write_text(original_handoff + "\nFreshness test marker after pack generation.\n", encoding="utf-8")
        stale_pack_freshness_cli = load_stdout(run_cli("pack-freshness", str(output_dir), "--format", "json"))
        stale_chapter_session_freshness_cli = load_stdout(run_cli("chapter-session-freshness", str(output_dir), "--format", "json"))
        stale_readiness_cli = load_stdout(run_cli("desktop-pack-readiness", str(output_dir), "--format", "json"))
        stale_local_summary_cli = load_stdout(run_cli("local-summary", str(output_dir), "--format", "json"))
        stale_user_steps_cli = load_stdout(run_cli("desktop-user-steps", str(output_dir), "--format", "json"))
        stale_pack_doctor_cli = load_stdout(run_cli("pack-doctor", str(output_dir), "--format", "json"))
        stale_doctor_checks = {item.get("id"): item for item in stale_pack_doctor_cli.get("checks", []) if isinstance(item, dict)}
        stale_changed = {item.get("name") for item in stale_pack_freshness_cli["comparison"]["changed"] if isinstance(item, dict)}
        stale_session_changed = {item.get("name") for item in stale_chapter_session_freshness_cli["comparison"]["changed"] if isinstance(item, dict)}
        assert_true(stale_pack_freshness_cli["status"] == "stale", "desktop-pack freshness should detect changed project state")
        assert_true(stale_pack_freshness_cli["ok"] is False, "desktop-pack freshness should fail stale state")
        assert_true("handoff.md" in stale_changed, "desktop-pack freshness should identify changed handoff.md")
        assert_true(stale_chapter_session_freshness_cli["status"] == "stale", "chapter session freshness should detect changed project state")
        assert_true(stale_chapter_session_freshness_cli["ok"] is False, "chapter session freshness should fail stale state")
        assert_true("handoff.md" in stale_session_changed, "chapter session freshness should identify changed handoff.md")
        assert_true(stale_readiness_cli["summary"]["chapter_session_freshness_status"] == "stale", "desktop-pack readiness should surface stale chapter session state")
        assert_true(stale_local_summary_cli["summary"]["pack_freshness_status"] == "stale", "local summary should surface stale pack state")
        assert_true(stale_local_summary_cli["summary"]["chapter_session_freshness_status"] == "stale", "local summary should surface stale chapter session state")
        assert_true(stale_local_summary_cli["status"] == "needs_fresh_pack", "local summary should ask for fresh pack when project state changed")
        assert_true(stale_user_steps_cli["status"] == "needs_fresh_pack", "user steps should ask for fresh pack when project state changed")
        assert_true(stale_user_steps_cli["steps"][1]["id"] == "check_integrity", "stale user steps should route to local integrity checks")
        assert_true(stale_doctor_checks.get("chapter_session_freshness", {}).get("ok") is False, "pack-doctor should surface stale chapter session state")
        assert_true(stale_doctor_checks.get("chapter_session_freshness", {}).get("severity") == "warning", "pack-doctor stale chapter session should be a warning")
        return {
            "file_count": len(pack["written_files"]),
            "has_index": (output_dir / "index.html").exists(),
            "has_pack_index_json": (output_dir / "pack-index.json").exists(),
            "has_pack_index_schema": (output_dir / "pack-index.schema.json").exists(),
            "has_start_here": (output_dir / "START_HERE.md").exists(),
            "has_first_three": (output_dir / "first-three.md").exists(),
            "has_first_three_json": (output_dir / "first-three.json").exists(),
            "has_first_three_schema": (output_dir / "first-three.schema.json").exists(),
            "has_first_three_html": (output_dir / "first-three.html").exists(),
            "first_three_status": first_three_json["status"],
            "first_three_file_count": len(first_three_json["files"]),
            "first_three_schema_title": first_three_schema["title"],
            "has_local_summary": (output_dir / "local-summary.md").exists(),
            "has_local_summary_json": (output_dir / "local-summary.json").exists(),
            "has_local_summary_html": (output_dir / "local-summary.html").exists(),
            "has_local_summary_schema": (output_dir / "local-summary.schema.json").exists(),
            "local_summary_status": local_summary_json["status"],
            "local_summary_card_count": len(local_summary_json["cards"]),
            "local_summary_quick_link_count": len(local_summary_json["quick_links"]),
            "local_summary_recommended_quick_link": next((item.get("id") for item in local_summary_json["quick_links"] if item.get("recommended") is True), ""),
            "local_summary_schema_title": local_summary_schema["title"],
            "has_user_steps": (output_dir / "user-steps.md").exists(),
            "has_user_steps_json": (output_dir / "user-steps.json").exists(),
            "has_user_steps_schema": (output_dir / "user-steps.schema.json").exists(),
            "has_user_steps_html": (output_dir / "user-steps.html").exists(),
            "user_steps_status": user_steps_json["status"],
            "user_steps_step_count": user_steps_json["step_count"],
            "user_steps_schema_title": user_steps_schema["title"],
            "has_starter_brief": (output_dir / "starter-brief.md").exists(),
            "has_starter_brief_json": (output_dir / "starter-brief.json").exists(),
            "has_starter_brief_schema": (output_dir / "starter-brief.schema.json").exists(),
            "starter_brief_status": starter_brief_json["status"],
            "starter_brief_first_file_count": len(starter_brief_json["first_files"]),
            "starter_brief_chapter_session_freshness_status": starter_brief_json["summary"]["chapter_session_freshness_status"],
            "starter_brief_schema_title": starter_brief_schema["title"],
            "starter_brief_cli_status": starter_brief_cli["status"],
            "has_next_step": (output_dir / "next-step.md").exists(),
            "has_desktop_next_step": (output_dir / "desktop-next-step.md").exists(),
            "has_desktop_next_step_json": (output_dir / "desktop-next-step.json").exists(),
            "has_desktop_next_step_schema": (output_dir / "desktop-next-step.schema.json").exists(),
            "has_desktop_pack_readiness": (output_dir / "desktop-pack-readiness.md").exists(),
            "has_desktop_pack_readiness_json": (output_dir / "desktop-pack-readiness.json").exists(),
            "has_desktop_pack_readiness_schema": (output_dir / "desktop-pack-readiness.schema.json").exists(),
            "has_desktop_evidence_session": (output_dir / "desktop-evidence-session.md").exists(),
            "has_desktop_evidence_session_json": (output_dir / "desktop-evidence-session.json").exists(),
            "has_desktop_evidence_session_schema": (output_dir / "desktop-evidence-session.schema.json").exists(),
            "has_desktop_evidence_runbook": (output_dir / "desktop-evidence-runbook.md").exists(),
            "has_desktop_evidence_runbook_json": (output_dir / "desktop-evidence-runbook.json").exists(),
            "has_desktop_evidence_runbook_schema": (output_dir / "desktop-evidence-runbook.schema.json").exists(),
            "desktop_evidence_runbook_step_count": desktop_evidence_runbook_json.get("desktop_step_count"),
            "desktop_evidence_runbook_schema_title": desktop_evidence_runbook_schema.get("title"),
            "has_desktop_evidence_review": (output_dir / "desktop-evidence-review.md").exists(),
            "has_desktop_evidence_review_json": (output_dir / "desktop-evidence-review.json").exists(),
            "has_desktop_evidence_review_html": (output_dir / "desktop-evidence-review.html").exists(),
            "has_desktop_evidence_review_schema": (output_dir / "desktop-evidence-review.schema.json").exists(),
            "has_desktop_evidence_gaps": (output_dir / "desktop-evidence-gaps.md").exists(),
            "has_desktop_evidence_gaps_json": (output_dir / "desktop-evidence-gaps.json").exists(),
            "desktop_evidence_session_checks": len(desktop_evidence_session_json["checks"]),
            "desktop_evidence_session_prompts": len(desktop_evidence_session_json["copy_prompts"]),
            "desktop_evidence_review_status": desktop_evidence_review_json["status"],
            "desktop_evidence_gaps_status": desktop_evidence_gaps_json["status"],
            "desktop_pack_readiness_status": desktop_pack_readiness_json["status"],
            "desktop_pack_freshness_status": pack_freshness_cli["status"],
            "desktop_pack_freshness_detects_stale": stale_pack_freshness_cli["status"] == "stale",
            "desktop_pack_readiness_chapter_session_freshness_status": desktop_pack_readiness_json["summary"]["chapter_session_freshness_status"],
            "desktop_pack_readiness_detects_stale_chapter_session": stale_readiness_cli["summary"]["chapter_session_freshness_status"] == "stale",
            "chapter_session_freshness_status": chapter_session_freshness_cli["status"],
            "chapter_session_freshness_detects_stale": stale_chapter_session_freshness_cli["status"] == "stale",
            "desktop_next_step_status": desktop_next_step_json["status"],
            "desktop_next_step_primary_action": desktop_next_step_json["primary_action"]["id"],
            "has_quick_install": (output_dir / "quick-install.md").exists(),
            "has_open_local": (output_dir / "open-local.md").exists() and (output_dir / "open-local.sh").exists(),
            "open_local_executable": os.access(output_dir / "open-local.sh", os.X_OK),
            "open_local_read_only": "does not install, change MCP config, write .novel-agent/" in open_local_script and "--yes" not in open_local_script,
            "has_powershell_open_wrapper": (output_dir / "open-local.ps1").exists(),
            "powershell_open_wrapper_read_only": "Start-Process -FilePath $FirstThreeFile" in open_local_ps1 and "--yes" not in open_local_ps1,
            "has_windows_open_wrapper": (output_dir / "open-local.cmd").exists(),
            "windows_open_wrapper_read_only": 'start "" "%FIRST_THREE_FILE%"' in open_local_cmd and "--yes" not in open_local_cmd,
            "has_macos_open_launcher": (output_dir / "open-local.command").exists(),
            "macos_open_launcher_executable": os.access(output_dir / "open-local.command", os.X_OK),
            "has_install_local": (output_dir / "install-local.md").exists() and (output_dir / "install-local.sh").exists(),
            "install_local_executable": os.access(output_dir / "install-local.sh", os.X_OK),
            "install_local_path_aware": "SCRIPT_DIR=" in install_local_script and 'CHECK_SCRIPT="${SCRIPT_DIR}/local-checks.sh"' in install_local_script,
            "has_powershell_install_wrapper": (output_dir / "install-local.ps1").exists(),
            "powershell_install_wrapper_preview_only": "Default mode is preview only" in install_local_ps1 and "desktop-bootstrap" in install_local_ps1,
            "has_windows_install_wrapper": (output_dir / "install-local.cmd").exists(),
            "windows_install_wrapper_preview_only": "Default mode is preview only" in install_local_cmd and "desktop-bootstrap" in install_local_cmd,
            "powershell_wrappers_python_autodetect": all("Get-Command python" in content and "Get-Command py" in content for content in powershell_wrappers.values()),
            "powershell_wrappers_python_missing_guard": all("Cannot find Python 3" in content for content in powershell_wrappers.values()),
            "powershell_preview_wrappers_show_python": all("Python command:" in content for content in powershell_preview_wrappers.values()),
            "windows_wrappers_python_autodetect": all("where python >nul 2>nul" in content and "where py >nul 2>nul" in content for content in windows_cmd_wrappers.values()),
            "windows_wrappers_py_launcher_fallback": all('set "PYTHON_BIN=py -3"' in content for content in windows_cmd_wrappers.values()),
            "windows_wrappers_python_missing_guard": all("Cannot find Python 3" in content for content in windows_cmd_wrappers.values()),
            "windows_preview_wrappers_show_python": all("Python command: %PYTHON_BIN%" in content for content in windows_preview_wrappers.values()),
            "has_macos_install_launcher": (output_dir / "install-local.command").exists(),
            "macos_install_launcher_executable": os.access(output_dir / "install-local.command", os.X_OK),
            "has_upgrade_local": (output_dir / "upgrade-local.md").exists() and (output_dir / "upgrade-local.sh").exists(),
            "upgrade_local_executable": os.access(output_dir / "upgrade-local.sh", os.X_OK),
            "upgrade_local_preview": "Default mode is preview only" in upgrade_preview.stdout and "--upgrade" in upgrade_preview.stdout,
            "has_powershell_upgrade_wrapper": (output_dir / "upgrade-local.ps1").exists(),
            "powershell_upgrade_wrapper_preview_only": "Default mode is preview only" in upgrade_local_ps1 and "--upgrade" in upgrade_local_ps1,
            "has_windows_upgrade_wrapper": (output_dir / "upgrade-local.cmd").exists(),
            "windows_upgrade_wrapper_preview_only": "Default mode is preview only" in upgrade_local_cmd and "--upgrade" in upgrade_local_cmd,
            "has_macos_upgrade_launcher": (output_dir / "upgrade-local.command").exists(),
            "macos_upgrade_launcher_executable": os.access(output_dir / "upgrade-local.command", os.X_OK),
            "has_uninstall_local": (output_dir / "uninstall-local.md").exists() and (output_dir / "uninstall-local.sh").exists(),
            "uninstall_local_executable": os.access(output_dir / "uninstall-local.sh", os.X_OK),
            "uninstall_local_preview": "Default mode is preview only" in uninstall_preview.stdout and "--uninstall" in uninstall_preview.stdout,
            "has_powershell_uninstall_wrapper": (output_dir / "uninstall-local.ps1").exists(),
            "powershell_uninstall_wrapper_preview_only": "Default mode is preview only" in uninstall_local_ps1 and "--uninstall" in uninstall_local_ps1,
            "has_windows_uninstall_wrapper": (output_dir / "uninstall-local.cmd").exists(),
            "windows_uninstall_wrapper_preview_only": "Default mode is preview only" in uninstall_local_cmd and "--uninstall" in uninstall_local_cmd,
            "has_macos_uninstall_launcher": (output_dir / "uninstall-local.command").exists(),
            "macos_uninstall_launcher_executable": os.access(output_dir / "uninstall-local.command", os.X_OK),
            "has_local_checks": (output_dir / "local-checks.md").exists() and (output_dir / "local-checks.sh").exists(),
            "local_checks_executable": os.access(output_dir / "local-checks.sh", os.X_OK),
            "has_powershell_local_checks_wrapper": (output_dir / "local-checks.ps1").exists(),
            "powershell_local_checks_has_required_commands": all(value in local_checks_ps1 for value in ["desktop-verify", "project-status", "continuity-audit", "prepare-session", "desktop-checklist"]),
            "has_windows_local_checks_wrapper": (output_dir / "local-checks.cmd").exists(),
            "windows_local_checks_has_required_commands": all(value in local_checks_cmd for value in ["desktop-verify", "project-status", "continuity-audit", "prepare-session", "desktop-checklist"]),
            "has_macos_local_checks_launcher": (output_dir / "local-checks.command").exists(),
            "macos_local_checks_launcher_executable": os.access(output_dir / "local-checks.command", os.X_OK),
            "has_archive_local": (output_dir / "archive-local.md").exists() and (output_dir / "archive-local.sh").exists(),
            "archive_local_executable": os.access(output_dir / "archive-local.sh", os.X_OK),
            "archive_local_preview_no_zip": "Default mode is preview only" in archive_preview.stdout,
            "archive_local_zip_exists": archive_local_zip.exists(),
            "archive_local_zip_contains_start_here": f"{output_dir.name}/START_HERE.md" in local_archive_names,
            "has_powershell_archive_wrapper": (output_dir / "archive-local.ps1").exists(),
            "powershell_archive_wrapper_uses_archive_pack": "archive-pack" in archive_local_ps1 and "$ScriptDir" in archive_local_ps1,
            "has_windows_archive_wrapper": (output_dir / "archive-local.cmd").exists(),
            "windows_archive_wrapper_uses_archive_pack": 'archive-pack "%SCRIPT_DIR%" --force --format markdown' in archive_local_cmd,
            "has_macos_archive_launcher": (output_dir / "archive-local.command").exists(),
            "macos_archive_launcher_executable": os.access(output_dir / "archive-local.command", os.X_OK),
            "has_writer_mode_approval": (output_dir / "writer-mode-approval.md").exists(),
            "has_writer_mode_approval_json": (output_dir / "writer-mode-approval.json").exists(),
            "has_writer_mode_approval_schema": (output_dir / "writer-mode-approval.schema.json").exists(),
            "has_agent_startup_prompt": (output_dir / "agent-startup-prompt.md").exists(),
            "has_agent_startup_prompt_json": (output_dir / "agent-startup-prompt.json").exists(),
            "has_agent_startup_prompt_schema": (output_dir / "agent-startup-prompt.schema.json").exists(),
            "agent_startup_prompt_status": agent_startup_prompt_json["status"],
            "agent_startup_prompt_can_draft": agent_startup_prompt_json["can_draft"],
            "agent_startup_prompt_read_first_count": len(agent_startup_prompt_json["read_first"]),
            "agent_startup_prompt_chapter_session_freshness_status": agent_startup_prompt_json["summary"]["chapter_session_freshness_status"],
            "agent_startup_prompt_copy_prompt_chars": len(agent_startup_prompt_json["copy_prompt"]),
            "agent_startup_prompt_schema_title": agent_startup_prompt_schema["title"],
            "has_troubleshooting": (output_dir / "troubleshooting.md").exists(),
            "has_troubleshooting_json": (output_dir / "troubleshooting.json").exists(),
            "has_troubleshooting_html": (output_dir / "troubleshooting.html").exists(),
            "has_troubleshooting_schema": (output_dir / "troubleshooting.schema.json").exists(),
            "troubleshooting_symptom_count": len(troubleshooting_json["symptoms"]),
            "troubleshooting_cli_symptom_count": len(troubleshooting_cli["symptoms"]),
            "troubleshooting_schema_title": troubleshooting_schema["title"],
            "has_desktop_diagnostics": (output_dir / "desktop-diagnostics.md").exists(),
            "has_desktop_diagnostics_json": (output_dir / "desktop-diagnostics.json").exists(),
            "has_desktop_diagnostics_schema": (output_dir / "desktop-diagnostics.schema.json").exists(),
            "desktop_diagnostics_status": desktop_diagnostics_json["status"],
            "desktop_diagnostics_check_count": len(desktop_diagnostics_json["checks"]),
            "desktop_diagnostics_schema_title": desktop_diagnostics_schema["title"],
            "has_agent_takeover": (output_dir / "agent-takeover.md").exists(),
            "has_agent_takeover_json": (output_dir / "agent-takeover.json").exists(),
            "has_agent_takeover_schema": (output_dir / "agent-takeover.schema.json").exists(),
            "agent_takeover_status": agent_takeover_json["status"],
            "agent_takeover_can_draft": agent_takeover_json["can_draft"],
            "agent_takeover_first_read_count": len(agent_takeover_json["first_read"]),
            "agent_takeover_chapter_session_freshness_status": agent_takeover_json["summary"]["chapter_session_freshness_status"],
            "agent_takeover_schema_title": agent_takeover_schema["title"],
            "has_author_actions": (output_dir / "author-actions.md").exists(),
            "has_author_actions_json": (output_dir / "author-actions.json").exists(),
            "has_author_actions_schema": (output_dir / "author-actions.schema.json").exists(),
            "author_actions_status": author_actions_json["status"],
            "author_actions_action_group_count": len(author_actions_json["action_groups"]),
            "author_actions_schema_title": author_actions_schema["title"],
            "has_acceptance_review": (output_dir / "acceptance-review.md").exists(),
            "has_acceptance_review_json": (output_dir / "acceptance-review.json").exists(),
            "has_acceptance_review_html": (output_dir / "acceptance-review.html").exists(),
            "has_acceptance_review_schema": (output_dir / "acceptance-review.schema.json").exists(),
            "acceptance_review_status": acceptance_review_json["status"],
            "acceptance_review_path_count": len(acceptance_review_json["acceptance_paths"]),
            "acceptance_review_schema_title": acceptance_review_schema["title"],
            "writer_mode_approval_required_checks": len(writer_mode_approval_json["required_check_ids"]),
            "writer_mode_approval_flow_steps": len(writer_mode_approval_json["recommended_flow"]),
            "writer_mode_approval_schema_title": writer_mode_approval_schema["title"],
            "has_client_compatibility": (output_dir / "client-compatibility.md").exists(),
            "has_client_compatibility_json": (output_dir / "client-compatibility.json").exists(),
            "has_pack_manifest": (output_dir / "pack-manifest.json").exists(),
            "has_verify_pack_script": (output_dir / "verify-pack.py").exists(),
            "has_pack_schema_check": (output_dir / "pack-schema-check.md").exists(),
            "has_pack_schema_check_json": (output_dir / "pack-schema-check.json").exists(),
            "has_pack_doctor": (output_dir / "pack-doctor.md").exists(),
            "has_pack_doctor_json": (output_dir / "pack-doctor.json").exists(),
            "pack_manifest_file_count": pack_manifest["file_count"],
            "pack_manifest_excludes_pack_doctor_json": "pack-doctor.json" in pack_manifest.get("refreshable_excluded", []) and "pack-doctor.json" not in manifest_names,
            "verify_pack_ok": verify_pack_result["ok"],
            "verify_pack_cli_ok": verify_pack_cli["ok"],
            "pack_schema_check_ok": pack_schema_check_json["ok"],
            "pack_schema_check_cli_ok": pack_schema_check_cli["ok"],
            "pack_schema_check_target_count": pack_schema_check_json["summary"]["target_count"],
            "pack_schema_check_reference_issue_count": pack_schema_check_json["summary"]["reference_issue_count"],
            "pack_schema_check_checked_file_refs": pack_schema_check_json["cross_reference"]["summary"]["checked_file_refs"],
            "pack_schema_check_checked_command_refs": pack_schema_check_json["cross_reference"]["summary"]["checked_command_refs"],
            "verify_pack_cli_detects_missing_file": not corrupt_verify_cli["ok"],
            "pack_doctor_ok": pack_doctor_report["ok"],
            "pack_doctor_json_ok": pack_doctor_json["ok"],
            "pack_doctor_json_refresh_ok": refreshed_pack_doctor_json["ok"],
            "pack_doctor_blocker_count": pack_doctor_report["blocker_count"],
            "pack_doctor_schema_check_ok": doctor_checks.get("pack_schema_check", {}).get("ok") is True,
            "pack_doctor_state_freshness_ok": doctor_checks.get("project_state_freshness", {}).get("ok") is True,
            "pack_doctor_chapter_session_freshness_ok": doctor_checks.get("chapter_session_freshness", {}).get("ok") is True,
            "pack_doctor_detects_stale_chapter_session": stale_doctor_checks.get("chapter_session_freshness", {}).get("ok") is False,
            "pack_doctor_command_kit_paths": doctor_checks.get("command_kit_paths", {}).get("ok") is True,
            "pack_doctor_missing_kit_paths_warning": missing_kit_path_check.get("severity") == "warning",
            "pack_rebind_dry_run_planned": rebind_dry_run["status"] == "planned",
            "pack_rebind_apply_ok": rebind_apply["ok"],
            "pack_rebind_command_kit_paths_ok": rebind_apply["doctor_after"]["command_kit_paths_ok"] is True,
            "pack_doctor_detects_missing_file": not corrupt_doctor_cli["ok"],
            "desktop_results_doctor_blocks_empty_template": template_doctor["status"] == "needs_revision",
            "desktop_evidence_packet_ready": evidence_packet["status"] == "ready_to_record",
            "desktop_results_doctor_imported_ok": imported_doctor["status"] == "ready_to_record",
            "archive_file_exists": archive_path.exists(),
            "archive_file_count": archive_info.get("file_count"),
            "archive_sha256_matches": hashlib.sha256(archive_bytes).hexdigest() == archive_info.get("sha256"),
            "archive_contains_start_here": f"{archive_root}/START_HERE.md" in archive_names,
            "has_archive_command": "desktop_pack_archive" in command_by_key,
            "has_chapter_session_freshness_command": "chapter_session_freshness" in command_by_key,
            "has_open_launcher_commands": all(
                key in command_by_key
                for key in [
                    "open_pack_index",
                    "powershell_open_launcher",
                    "windows_open_launcher",
                    "macos_open_launcher",
                ]
            ),
            "has_agent_activity_commands": all(
                key in command_by_key
                for key in [
                    "list_agent_activity",
                    "agent_activity_report",
                    "record_agent_activity",
                ]
            ),
            "has_handoff_commands": all(
                key in command_by_key
                for key in [
                    "handoff_integrity",
                    "handoff_range_report",
                    "handoff_report",
                    "handoff_readiness",
                ]
            ),
            "has_macos_archive_launcher_command": "macos_archive_launcher" in command_by_key,
            "has_windows_launcher_commands": all(
                key in command_by_key
                for key in [
                    "powershell_install_launcher",
                    "powershell_upgrade_launcher",
                    "powershell_uninstall_launcher",
                    "powershell_local_checks_launcher",
                    "powershell_archive_launcher",
                    "windows_install_launcher",
                    "windows_upgrade_launcher",
                    "windows_uninstall_launcher",
                    "windows_local_checks_launcher",
                    "windows_archive_launcher",
                ]
            ),
            "has_commands_index": (output_dir / "commands-index.md").exists(),
            "has_commands_index_json": (output_dir / "commands-index.json").exists(),
            "has_commands_index_schema": (output_dir / "commands-index.schema.json").exists(),
            "has_commands_json": (output_dir / "commands.json").exists(),
            "has_commands_schema": (output_dir / "commands.schema.json").exists(),
            "has_source_intake_schema": (output_dir / "source-intake.schema.json").exists(),
            "has_chapter_session_schema": (output_dir / "chapter-session.schema.json").exists(),
            "has_desktop_results_schema": (output_dir / "desktop-results.schema.json").exists(),
            "commands_json_count": commands_json["command_count"],
            "commands_json_group_count": len(commands_json["groups"]),
            "commands_json_read_only_count": commands_json["write_scope_counts"]["read_only"],
            "commands_index_group_count": commands_index_json["group_count"],
            "commands_index_schema_group_count": commands_index_schema_summary["group_count"],
            "has_desktop_pack_readiness_command": "desktop_pack_readiness" in command_by_key,
            "has_pack_schema_check_command": "pack_schema_check" in command_by_key,
            "has_pack_freshness_command": "pack_freshness" in command_by_key,
            "has_starter_brief_command": "starter_brief" in command_by_key,
            "has_agent_startup_prompt_command": "agent_startup_prompt" in command_by_key,
            "has_agent_takeover_command": "agent_takeover" in command_by_key,
            "has_desktop_troubleshooting_command": "desktop_troubleshooting" in command_by_key,
            "has_desktop_diagnostics_command": "desktop_diagnostics" in command_by_key,
            "desktop_pack_readiness_cli_status": desktop_pack_readiness_cli["status"],
            "desktop_pack_readiness_cli_command_count": desktop_pack_readiness_cli["summary"]["command_count"],
            "desktop_pack_freshness_cli_status": pack_freshness_cli["status"],
            "agent_startup_prompt_cli_status": agent_startup_prompt_cli["status"],
            "agent_takeover_cli_status": agent_takeover_cli["status"],
            "commands_schema_stage_count": commands_schema_summary["stage_count"],
            "commands_schema_group_count": commands_schema_summary["group_count"],
            "desktop_pack_json_schema_count": desktop_pack_schema_summary["schema_count"],
            "desktop_results_schema_check_count": desktop_pack_schema_summary["result_check_count"],
            "desktop_pack_snapshot_file_count": desktop_pack_snapshot_summary["file_count"],
            "desktop_pack_snapshot_sha256": desktop_pack_snapshot_summary["sha256"],
            "has_agent_start": (output_dir / "agent-start.md").exists(),
            "has_platform_agent_starts": all((output_dir / name).exists() for name in ["agent-start-codex.md", "agent-start-cursor.md", "agent-start-claude-desktop.md", "agent-start-generic.md"]),
            "has_project_wizard": (output_dir / "project-wizard.json").exists(),
            "has_project_wizard_schema": (output_dir / "project-wizard.schema.json").exists(),
            "has_source_intake": (output_dir / "source-intake.json").exists(),
            "has_source_intake_review": (output_dir / "source-intake-review.html").exists(),
            "has_project_status_json": (output_dir / "project-status.json").exists(),
            "has_continuity_audit": (output_dir / "continuity-audit.md").exists(),
            "has_continuity_audit_json": (output_dir / "continuity-audit.json").exists(),
            "has_author_review_queue": (output_dir / "author-review-queue.md").exists(),
            "has_author_review_queue_json": (output_dir / "author-review-queue.json").exists(),
            "has_handoff_report_json": (output_dir / "handoff-report.json").exists(),
            "has_handoff_report_schema": (output_dir / "handoff-report.schema.json").exists(),
            "has_handoff_brief": (output_dir / "handoff-brief.md").exists() and (output_dir / "handoff-brief.json").exists(),
            "has_chapter_session": (output_dir / "chapter-session.md").exists() and (output_dir / "chapter-session.json").exists(),
            "has_index_workflow_status": "Workflow Status" in index_html and "Next Actions" in index_html,
            "uninitialized_chapter_session_pending": uninitialized_session_json["type"] == "chapter_session_pending",
            "has_desktop_matrix": (output_dir / "desktop-matrix.md").exists(),
            "has_desktop_matrix_json": (output_dir / "desktop-matrix.json").exists(),
            "has_real_client_walkthrough": (output_dir / "real-client-walkthrough.md").exists(),
            "has_client_prompts": (output_dir / "client-prompts.md").exists(),
            "has_result_json_guide": (output_dir / "result-json-guide.md").exists(),
            "has_evidence_to_results": (output_dir / "evidence-to-results.md").exists(),
            "has_desktop_evidence_session_command": "desktop_evidence_session" in command_by_key,
            "has_desktop_evidence_packet_command": "desktop_evidence_packet" in command_by_key,
            "evidence_to_results_import_ok": imported_evidence["ok"],
            "evidence_to_results_imported_checks": len(imported_payload["checks"]),
            "index_result_json_checks": True,
            "index_result_safety_checks": True,
            "index_wizard_source_preflight": True,
            "index_chapter_session_preflight": True,
            "source_intake_template_type": source_template["template"]["type"],
            "source_intake_planned": source_dry_run["validation"]["planned"],
            "source_intake_quality_level": source_dry_run["validation"]["quality"]["level"],
            "source_intake_low_quality_level": low_quality_dry_run["validation"]["quality"]["level"],
            "source_intake_low_quality_warnings": low_quality_dry_run["validation"]["warning_count"],
            "source_intake_low_quality_rejected": low_quality_rejected.returncode != 0,
            "source_intake_low_quality_override": low_quality_applied.get("quality_override", {}).get("allowed") is True,
            "source_intake_applied": applied_intake["applied_counts"],
            "platform_guide_file_count": len([item for item in pack.get("platform_guide_files", []) if isinstance(item, dict)]),
            "mcp_config_file_count": len([item for item in pack.get("mcp_config_files", []) if isinstance(item, dict)]),
            "target_chapter": pack["target_chapter"],
            "result_file": pack["result_file"],
        }


def verify_desktop_handoff_bundle() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-desktop-handoff-bundle-") as tmp:
        root = Path(tmp)
        project = root / "novel with space"
        runtime = root / "runtime"
        bundle_dir = root / "handoff bundle"
        runtime.mkdir()
        cli_bin = runtime / "long-novel-agent"
        mcp_bin = runtime / "long-novel-agent-mcp"
        cli_bin.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        mcp_bin.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        cli_bin.chmod(0o755)
        mcp_bin.chmod(0o755)

        plan = load_stdout(run_cli("standalone-build", "--output-dir", str(root / "standalone"), "--target-os", "macos", "--format", "json"))
        assert_true(plan["type"] == "standalone_runtime_build", "standalone-build returned wrong type")
        assert_true(plan["status"] == "plan_only", "standalone-build without --apply should be plan only")
        assert_true(plan["runtime_files"]["cli"].endswith("long-novel-agent"), "standalone-build missed CLI runtime output")
        assert_true(plan["runtime_files"]["mcp"].endswith("long-novel-agent-mcp"), "standalone-build missed MCP runtime output")
        assert_true("PyInstaller" in plan["commands"][0]["command"], "standalone-build should show PyInstaller command")
        assert_true("--add-data" in plan["commands"][0]["command"], "standalone-build should include data files")
        command_data = plan["commands"][0].get("data") if isinstance(plan["commands"][0].get("data"), list) else []
        command_data_dests = {item.get("dest") for item in command_data if isinstance(item, dict)}
        assert_true(
            {"schemas", "assets", "agents", "SKILL.md", "cli.py", "server.py", "install.py"} <= command_data_dests,
            "standalone-build missed bundled data specs",
        )
        markdown = run_cli("standalone-build", "--output-dir", str(root / "standalone"), "--target-os", "macos", "--format", "markdown")
        assert_true("Standalone Runtime" in markdown.stdout, "standalone-build markdown missing title")

        run_cli("init", str(project), "--title", "Portable Bundle")
        bundle = load_stdout(
            run_cli(
                "desktop-handoff-bundle",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--runtime-dir",
                str(runtime),
                "--output-dir",
                str(bundle_dir),
                "--archive",
                "--force",
                "--format",
                "json",
            )
        )
        assert_true(bundle["type"] == "desktop_handoff_bundle", "desktop-handoff-bundle returned wrong type")
        assert_true(bundle["ok"] is True and bundle["status"] == "ready", "desktop-handoff-bundle with runtime should be ready")
        assert_true(bundle["target_chapter"] == 2, "desktop-handoff-bundle missed target chapter")
        assert_true(bundle["runtime"]["cli"]["status"] == "copied", "desktop-handoff-bundle missed CLI runtime")
        assert_true(bundle["runtime"]["mcp"]["status"] == "copied", "desktop-handoff-bundle missed MCP runtime")
        assert_true(bundle["project_snapshot"]["file_count"] > 0, "desktop-handoff-bundle did not copy project snapshot")
        assert_true(bundle["desktop_pack"]["file_count"] >= 150, "desktop-handoff-bundle did not generate full desktop pack")
        expected = {
            "README.md",
            "START_HERE.md",
            "START_HERE.sh",
            "START_HERE.command",
            "START_HERE.ps1",
            "START_HERE.cmd",
            "agent-read-me-first.md",
            "runtime-commands.json",
            "runtime-commands.md",
            "handoff-bundle-manifest.json",
            "mcp-configs/README.md",
            "mcp-configs/codex-read-only.toml",
            "mcp-configs/cursor-read-only.json",
            "project/.novel-agent/manifest.json",
            "pack/first-three.json",
            "pack/commands.json",
            "runtime/long-novel-agent",
            "runtime/long-novel-agent-mcp",
        }
        for name in expected:
            assert_true((bundle_dir / name).exists(), f"desktop-handoff-bundle did not write {name}")
        start_sh = (bundle_dir / "START_HERE.sh").read_text(encoding="utf-8")
        start_ps1 = (bundle_dir / "START_HERE.ps1").read_text(encoding="utf-8")
        runtime_commands = json.loads((bundle_dir / "runtime-commands.json").read_text(encoding="utf-8"))["commands"]
        codex_snippet = (bundle_dir / "mcp-configs" / "codex-read-only.toml").read_text(encoding="utf-8")
        readme = (bundle_dir / "README.md").read_text(encoding="utf-8")
        agent_readme = (bundle_dir / "agent-read-me-first.md").read_text(encoding="utf-8")
        assert_true("runtime/long-novel-agent" in start_sh, "handoff shell launcher should call bundled CLI runtime")
        assert_true("long-novel-agent-mcp" in start_sh, "handoff shell launcher should show bundled MCP runtime")
        assert_true("runtime\\long-novel-agent" in start_ps1, "handoff PowerShell launcher should call bundled CLI runtime")
        assert_true("python " not in start_sh.lower(), "handoff shell launcher should not depend on python")
        assert_true("python " not in start_ps1.lower(), "handoff PowerShell launcher should not depend on python")
        assert_true(all("python " not in command.lower() for command in runtime_commands.values()), "runtime-commands should not depend on python")
        assert_true(runtime_commands["prepare_session"].startswith("./runtime/long-novel-agent"), "runtime-commands should use bundle-relative CLI paths")
        assert_true(str(bundle_dir) not in runtime_commands["prepare_session"], "runtime-commands should not hard-code generated bundle path")
        assert_true("mcp-configs/current" in readme, "handoff README should point users at refreshed MCP snippets")
        assert_true("START_HERE" in agent_readme and "mcp-configs/current" in agent_readme, "agent readme should explain moved-bundle refresh")
        assert_true("long-novel-agent-mcp" in codex_snippet and "server.py" not in codex_snippet, "standalone MCP snippet should use bundled MCP executable")
        assert_true("build_chapter_context" in agent_readme, "agent readme should require context before drafting")
        archive = Path(bundle["archive"]["path"])
        assert_true(archive.exists(), "desktop-handoff-bundle archive missing")
        with zipfile.ZipFile(archive) as zip_handle:
            names = set(zip_handle.namelist())
        assert_true(any(name.endswith("/START_HERE.md") for name in names), "handoff archive missed START_HERE.md")
        assert_true(any(name.endswith("/runtime/long-novel-agent") for name in names), "handoff archive missed CLI runtime")

        missing = run_cli(
            "desktop-handoff-bundle",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--output-dir",
            str(root / "missing runtime bundle"),
            "--force",
            "--format",
            "json",
            check=False,
        )
        missing_payload = load_stdout(missing)
        assert_true(missing.returncode != 0, "desktop-handoff-bundle without runtime should exit nonzero")
        assert_true(missing_payload["status"] == "needs_runtime", "desktop-handoff-bundle should report missing runtime")
        assert_true((root / "missing runtime bundle" / "runtime" / "MISSING_RUNTIME.md").exists(), "missing runtime bundle should write explanation")
        return {
            "bundle_file_count": bundle["file_count"],
            "project_snapshot_files": bundle["project_snapshot"]["file_count"],
            "archive_file_count": bundle["archive"]["file_count"],
            "missing_runtime_status": missing_payload["status"],
        }


def verify_desktop_client_checklist() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-desktop-client-") as tmp:
        project = Path(tmp) / "novel"
        run_cli("init", str(project), "--title", "desktop-client")
        read_only = load_stdout(
            run_cli(
                "desktop-checklist",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--format",
                "json",
            )
        )
        assert_true(read_only["type"] == "desktop_client_checklist", "desktop checklist returned wrong type")
        assert_true(read_only["target_chapter"] == 2, "desktop checklist missed target chapter")
        check_ids = {item["id"] for item in read_only["checklist"]}
        assert_true("local_config_preflight" in check_ids, "desktop checklist missed local preflight")
        assert_true("read_path_prepare_session" in check_ids, "desktop checklist missed prepare session check")
        assert_true("range_readiness_gate" in check_ids, "desktop checklist missed range readiness check")
        assert_true("write_tools_hidden" in check_ids, "read-only checklist missed write tools check")
        assert_true(all(item["status"] == "not_checked" for item in read_only["manual_result_template"]), "manual result template should start unchecked")
        evidence_pack = read_only["evidence_pack"]
        assert_true(evidence_pack["result_file"] == "desktop-codex-read-only-chapter-2-results.json", "desktop checklist evidence pack result filename mismatch")
        assert_true(evidence_pack["results_payload"]["checks"] == read_only["manual_result_template"], "desktop checklist evidence pack should reuse manual result template")
        assert_true("record-desktop-check" in evidence_pack["record_command"], "desktop checklist evidence pack missed record command")
        assert_true(evidence_pack["required_evidence_count"] == len(read_only["checklist"]), "desktop checklist evidence pack required count mismatch")
        evidence_session = load_stdout(
            run_cli(
                "desktop-evidence-session",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--format",
                "json",
            )
        )
        assert_true(evidence_session["type"] == "desktop_evidence_session", "desktop evidence session returned wrong type")
        assert_true(evidence_session["$schema"] == "desktop-evidence-session.schema.json", "desktop evidence session missed local schema")
        assert_true(evidence_session["target_chapter"] == 2, "desktop evidence session missed target chapter")
        assert_true(evidence_session["would_write_project_state"] is False, "desktop evidence session should not write project state")
        assert_true(evidence_session["records_real_client_evidence"] is False, "desktop evidence session should not record real client evidence")
        assert_true(len(evidence_session["checks"]) == len(read_only["checklist"]), "desktop evidence session check count mismatch")
        assert_true(len(evidence_session["copy_prompts"]) >= 6, "desktop evidence session missed copy prompts")
        assert_true(evidence_session["commands"]["desktop_verify"]["present"], "desktop evidence session missed desktop verify command")
        assert_true(evidence_session["commands"]["desktop_checklist"]["present"], "desktop evidence session missed desktop checklist command")
        assert_true(evidence_session["commands"]["desktop_evidence_packet"]["present"], "desktop evidence session missed evidence packet command")
        assert_true(evidence_session["commands"]["record_desktop_check"]["present"], "desktop evidence session missed record command")
        evidence_session_md = run_cli(
            "desktop-evidence-session",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Desktop Evidence Session" in evidence_session_md.stdout, "desktop evidence session markdown missing title")
        assert_true("desktop-results-doctor" in evidence_session_md.stdout, "desktop evidence session markdown missed doctor command")
        template_report = load_stdout(
            run_cli(
                "desktop-checklist",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--write-results-template",
                "--format",
                "json",
            )
        )
        template_path = project / evidence_pack["result_file"]
        assert_true(template_path.exists(), "desktop checklist did not write default results template")
        saved_template = json.loads(template_path.read_text(encoding="utf-8"))
        assert_true(saved_template == template_report["evidence_pack"]["results_payload"], "desktop checklist wrote unexpected results template")
        resolved_template_path = str(template_path.resolve())
        assert_true(template_report["evidence_pack"]["template_path"] == resolved_template_path, "desktop checklist template path mismatch")
        assert_true(resolved_template_path in template_report["evidence_pack"]["record_command"], "desktop checklist record command missed written template path")
        def desktop_check_evidence(check_id: str) -> str:
            if check_id == "range_readiness_gate":
                return "build_chapter_range_readiness returned type=chapter_range_readiness, status=needs_files, problem_chapters=[]."
            if check_id == "proposal_readiness_gate":
                return "build_proposal_readiness returned proposal_readiness status=ready."
            if check_id == "acceptance_write_gate":
                return "record_chapter and apply_after_write_updates used author_confirmation."
            return f"evidence for {check_id}"

        passed_results = [
            {**item, "status": "passed", "evidence": desktop_check_evidence(str(item["id"]))}
            for item in read_only["manual_result_template"]
        ]
        recorded = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                json.dumps({"checks": passed_results}, ensure_ascii=False),
                "--agent-id",
                "codex-local",
                "--client-version",
                "test-client",
            )
        )
        assert_true(recorded["validation"]["ok"], "desktop check record with full evidence should pass validation")
        assert_true(recorded["record"]["required_passed_with_evidence"] == recorded["record"]["required_total"], "desktop check did not count passed evidence")
        missing_evidence = [{**passed_results[0], "evidence": ""}, *passed_results[1:]]
        incomplete = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                json.dumps(missing_evidence, ensure_ascii=False),
            )
        )
        assert_true(not incomplete["validation"]["ok"], "desktop check missing evidence should fail validation")
        listed = load_stdout(run_cli("list-desktop-checks", str(project), "--platform", "codex", "--limit", "5"))
        assert_true(len(listed["records"]) == 2, "desktop check list should return recorded rows")
        status = load_stdout(run_cli("project-status", str(project), "--platform", "codex"))
        assert_true(status["type"] == "project_status", "project-status returned wrong type")
        assert_true(status["author_review_queue"]["status"] in {"clear", "ready_with_warnings", "needs_author_review", "blocked"}, "project-status missed author review queue status")
        assert_true(
            any("author-review-queue" in command for command in status["recommended_commands"]),
            "project-status missed author review queue command",
        )
        assert_true(status["counts"]["desktop_verifications"] == 2, "project-status missed desktop verification records")
        assert_true(status["latest_desktop_verification"]["platform"] == "codex", "project-status missed latest desktop verification")
        status_matrix = status["desktop_verification_matrix"]
        assert_true(status_matrix["type"] == "desktop_verification_matrix", "project-status missed desktop verification matrix")
        assert_true(status_matrix["totals"]["failed"] == 1, "project-status matrix should expose failed desktop evidence")
        assert_true("required_checks_total" in status_matrix["totals"], "project-status matrix missed required coverage totals")
        verification = status["verification_status"]
        assert_true(verification["real_desktop_client"]["status"] == "failed", "project-status should expose latest real desktop verification status")
        assert_true(verification["real_desktop_client"]["is_real_client_evidence"], "project-status should mark desktop record as real client evidence")
        assert_true(verification["local_mcp_config"]["status"] == "not_checked", "project-status should separate local MCP config checks")
        assert_true(not verification["local_mcp_config"]["is_real_client_evidence"], "local MCP config check should not count as real client evidence")
        matrix = load_stdout(run_cli("desktop-matrix", str(project), "--platform", "codex", "--mode", "read-only", "--chapter", "2"))
        assert_true(matrix["type"] == "desktop_verification_matrix", "desktop-matrix returned wrong type")
        assert_true(matrix["totals"]["records_considered"] == 2, "desktop-matrix missed chapter-scoped records")
        assert_true(matrix["totals"]["failed"] == 1, "desktop-matrix should expose failed latest read-only check")
        assert_true(matrix["matrix"][0]["platform"] == "codex", "desktop-matrix missed platform")
        assert_true(matrix["matrix"][0]["mode"] == "read-only", "desktop-matrix missed mode")
        assert_true(matrix["matrix"][0]["status"] == "failed", "desktop-matrix missed failed status")
        assert_true(isinstance(matrix["matrix"][0].get("coverage"), dict), "desktop-matrix missed coverage object")
        assert_true(matrix["matrix"][0]["coverage"]["required_total"] == 8, "desktop-matrix coverage missed required total")
        assert_true(matrix["matrix"][0]["coverage"]["required_passed_with_evidence"] < 8, "desktop-matrix coverage should expose incomplete evidence")
        assert_true(matrix["matrix"][0]["coverage"]["blocking_check_ids"], "desktop-matrix coverage missed blocking check ids")
        matrix_md = run_cli("desktop-matrix", str(project), "--platform", "codex", "--mode", "read-only", "--chapter", "2", "--format", "markdown")
        assert_true("Long Novel Agent Desktop Verification Matrix" in matrix_md.stdout, "desktop-matrix markdown missing title")
        assert_true("| Platform | Mode | Status | Required | Gaps | Chapter | Client | Issues |" in matrix_md.stdout, "desktop-matrix markdown missed coverage header")
        assert_true("| codex | read-only | failed |" in matrix_md.stdout and "| 2 |" in matrix_md.stdout, "desktop-matrix markdown missed matrix row")
        status_md = run_cli("project-status", str(project), "--platform", "codex", "--format", "markdown")
        assert_true("Long Novel Agent Project Status" in status_md.stdout, "project-status markdown missing title")
        assert_true("Author Review Queue" in status_md.stdout, "project-status markdown missed author review queue section")
        assert_true("Verification Evidence" in status_md.stdout, "project-status markdown missed verification evidence section")
        assert_true("Desktop Verification Matrix" in status_md.stdout, "project-status markdown missed desktop verification matrix section")
        assert_true("Local MCP config check" in status_md.stdout, "project-status markdown missed local MCP config status")
        assert_true("Real desktop client check" in status_md.stdout, "project-status markdown missed real desktop client status")
        run_cli("declare-agent", str(project), "--agent-id", "codex-local", "--platform", "codex", "--capabilities", "mcp,file-read")
        handoff = load_stdout(
            run_cli(
                "handoff-readiness",
                str(project),
                "--chapter",
                "2",
                "--incoming-agent-id",
                "codex-local",
                "--incoming-platform",
                "codex",
                "--mode",
                "read-only",
            )
        )
        assert_true(handoff["type"] == "handoff_readiness", "handoff-readiness returned wrong type")
        assert_true(handoff["incoming_agent"]["id"] == "codex-local", "handoff-readiness missed incoming agent")
        assert_true(handoff["latest_desktop_verification"]["platform"] == "codex", "handoff-readiness missed desktop verification")
        assert_true(handoff["verification_status"]["real_desktop_client"]["status"] == "failed", "handoff-readiness should expose latest real desktop verification status")
        handoff_md = run_cli(
            "handoff-readiness",
            str(project),
            "--chapter",
            "2",
            "--incoming-agent-id",
            "codex-local",
            "--incoming-platform",
            "codex",
            "--mode",
            "read-only",
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Handoff Readiness" in handoff_md.stdout, "handoff-readiness markdown missing title")
        assert_true("Verification Evidence" in handoff_md.stdout, "handoff-readiness markdown missed verification evidence section")
        audit = load_stdout(run_cli("list-audit", str(project), "--limit", "10"))
        assert_true(any(row.get("operation") == "record-desktop-check" for row in audit["audit"]), "desktop check record should be audited")
        evidence_text = "\n".join(
            [
                "desktop-verify completed for codex read-only config; OK: True; local stdio MCP listed long-novel-agent tools and prepare_chapter_session returned chapter_session_packet.",
                "Codex Desktop client restarted after MCP config change; client version test-client.",
                "Desktop client tool list showed long-novel-agent tools: prepare_chapter_session, build_chapter_context, build_chapter_readiness, build_chapter_range_readiness, check_chapter, doctor.",
                "prepare_chapter_session returned target_chapter=2, chapter_session_packet, handoff, and workflow gates.",
                "build_chapter_context returned visible rules, sources, facts, debts, and contracts for chapter 2.",
                "Before ready, the agent named build_chapter_readiness, with check_chapter and diff_chapter_contract as fallback checks.",
                "build_chapter_range_readiness returned type=chapter_range_readiness, status=needs_files, missing files for chapter 2, and problem_chapters=[2].",
                "Read-only mode write tools hidden: did not expose record_chapter or apply_after_write_updates.",
            ]
        )
        evidence_path = Path(tmp) / "desktop-evidence.txt"
        evidence_path.write_text(evidence_text, encoding="utf-8")
        imported = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                str(evidence_path),
                "--output",
                "imported-desktop-results.json",
                "--format",
                "json",
            )
        )
        assert_true(imported["type"] == "desktop_evidence_import", "desktop evidence import returned wrong type")
        assert_true(imported["ok"], "desktop evidence import should mark all required checks as found")
        assert_true(not imported["missing_required_check_ids"], "desktop evidence import missed required checks")
        imported_path = project / "imported-desktop-results.json"
        assert_true(imported_path.exists(), "desktop evidence import did not write output file")
        imported_payload = json.loads(imported_path.read_text(encoding="utf-8"))
        assert_true(all(item["status"] == "passed" and item["evidence"] for item in imported_payload["checks"]), "desktop evidence import output should have passed evidence")
        result_json_import = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                json.dumps(imported_payload, ensure_ascii=False),
                "--format",
                "json",
            )
        )
        assert_true(result_json_import["ok"], "desktop evidence import should accept filled result JSON")
        assert_true(result_json_import["source_summary"]["detected_result_checks"] == len(imported_payload["checks"]), "desktop evidence import missed result JSON checks")
        assert_true(
            all(item.get("source") == "result_json" for item in result_json_import["match_details"]),
            "desktop evidence import result JSON should mark matches as result_json",
        )
        structured_evidence = "\n".join(
            [
                "Codex Desktop client restarted after MCP config change; MCP server list refreshed without startup error.",
                json.dumps({"type": "desktop_setup_verification", "ok": True, "platform": "codex", "mode": "read-only", "checks": [{"name": "mcp_start_check", "ok": True}]}, ensure_ascii=False),
                json.dumps(
                    {
                        "result": {
                            "tools": [
                                {"name": "prepare_chapter_session"},
                                {"name": "build_chapter_context"},
                                {"name": "build_chapter_range_readiness"},
                                {"name": "check_chapter"},
                                {"name": "doctor"},
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "result": {
                            "structuredContent": {
                                "type": "chapter_session_packet",
                                "status": "ready",
                                "target_chapter": 2,
                                "chapter_context": {
                                    "rules": {"required_phrases": []},
                                    "sources": [],
                                    "facts": [],
                                    "contracts": [],
                                    "open_debts": [],
                                },
                                "tool_gates": {
                                    "before_draft": ["Use chapter_context."],
                                    "before_ready": ["check_chapter", "diff_chapter_contract"],
                                },
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"type": "desktop_agent_note", "tools": ["check_chapter", "diff_chapter_contract"]}, ensure_ascii=False),
                json.dumps({"type": "chapter_range_readiness", "status": "needs_files", "problem_chapters": [2]}, ensure_ascii=False),
            ]
        )
        structured_import = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                structured_evidence,
                "--format",
                "json",
            )
        )
        assert_true(structured_import["ok"], "desktop evidence import should use structured JSON snippets")
        assert_true(structured_import["source_summary"]["detected_json_payloads"] >= 4, "desktop evidence import missed structured JSON payloads")
        detail_sources = {item["id"]: item["source"] for item in structured_import["match_details"]}
        assert_true(detail_sources["local_config_preflight"] == "structured_json", "desktop evidence import missed structured local config evidence")
        assert_true(detail_sources["server_visible"] == "structured_json", "desktop evidence import missed structured tool-list evidence")
        assert_true(detail_sources["read_path_prepare_session"] == "structured_json", "desktop evidence import missed structured chapter session evidence")
        assert_true(detail_sources["context_gate"] == "structured_json", "desktop evidence import missed structured context evidence")
        assert_true(detail_sources["range_readiness_gate"] == "structured_json", "desktop evidence import missed structured range readiness evidence")
        assert_true(detail_sources["write_tools_hidden"] == "structured_json", "desktop evidence import missed structured read-only evidence")
        transcript_evidence = "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in [
                {"event": "tool_result", "tool_name": "verify_desktop_setup", "status": "passed", "ok": True, "output": {"mcp_start_check": True}},
                {"event": "desktop_client", "client_restarted": True, "message": "MCP config reloaded for Codex Desktop"},
                {
                    "event": "mcp_tools_list",
                    "server": "long-novel-agent",
                    "tools": [
                        {"name": "prepare_chapter_session"},
                        {"name": "build_chapter_context"},
                        {"name": "build_chapter_range_readiness"},
                        {"name": "check_chapter"},
                        {"name": "doctor"},
                    ],
                },
                {
                    "event": "tool_result",
                    "tool_name": "prepare_chapter_session",
                    "status": "succeeded",
                    "output": {
                        "type": "chapter_session_packet",
                        "target_chapter": 2,
                        "chapter_context": {
                            "rules": {"required_phrases": []},
                            "sources": [],
                            "facts": [],
                            "contracts": [],
                            "open_debts": [],
                        },
                    },
                },
                {"event": "tool_result", "tool_name": "build_chapter_range_readiness", "status": "succeeded", "output": {"type": "chapter_range_readiness", "status": "needs_files", "problem_chapters": [2]}},
                {"event": "agent_step", "tool_calls": [{"name": "check_chapter"}, {"name": "diff_chapter_contract"}]},
            ]
        )
        transcript_import = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                transcript_evidence,
                "--format",
                "json",
            )
        )
        assert_true(transcript_import["ok"], "desktop evidence import should use NDJSON transcript events")
        assert_true(transcript_import["source_summary"]["detected_tool_name_count"] >= 5, "desktop evidence import missed transcript tool names")
        transcript_sources = {item["id"]: item["source"] for item in transcript_import["match_details"]}
        assert_true(transcript_sources["client_restart"] == "structured_json", "desktop evidence import missed transcript client restart")
        assert_true(transcript_sources["local_config_preflight"] == "structured_json", "desktop evidence import missed transcript local config")
        assert_true(transcript_sources["server_visible"] == "structured_json", "desktop evidence import missed transcript tool list")
        assert_true(transcript_sources["range_readiness_gate"] == "structured_json", "desktop evidence import missed transcript range readiness")
        assert_true(transcript_sources["ready_check_gate"] == "structured_json", "desktop evidence import missed transcript ready gates")
        imported_md = run_cli(
            "ingest-desktop-evidence",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--evidence",
            str(evidence_path),
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Desktop Evidence Import" in imported_md.stdout, "desktop evidence import markdown missing title")
        assert_true("Detected JSON payloads" in imported_md.stdout, "desktop evidence import markdown missed JSON payload count")
        assert_true("Detected tool names" in imported_md.stdout, "desktop evidence import markdown missed tool name count")
        assert_true("Match Details" in imported_md.stdout, "desktop evidence import markdown missed match details")
        unsafe_evidence = evidence_text + "\napi_key=sk-testsecret1234567890 should not be stored.\n" + ("这是一段不该作为验证证据保存的长稿内容。" * 180)
        unsafe_import = load_stdout(
            run_cli(
                "ingest-desktop-evidence",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--evidence",
                unsafe_evidence,
                "--format",
                "json",
            )
        )
        warning_codes = {item.get("code") for item in unsafe_import["warnings"]}
        assert_true("redacted_sensitive_value" in warning_codes, "desktop evidence import missed sensitive value warning")
        assert_true(unsafe_import["source_summary"]["warning_count"] >= 1, "desktop evidence import missed warning count")
        assert_true("sk-testsecret" not in json.dumps(unsafe_import["results_payload"], ensure_ascii=False), "desktop evidence import leaked sensitive evidence")
        sanitized_path = Path(tmp) / "sanitized-desktop-evidence.txt"
        sanitized = load_stdout(
            run_cli(
                "sanitize-desktop-evidence",
                str(project),
                "--evidence",
                unsafe_evidence,
                "--output",
                str(sanitized_path),
                "--max-line-chars",
                "160",
                "--max-total-chars",
                "900",
                "--format",
                "json",
            )
        )
        assert_true(sanitized["type"] == "desktop_evidence_sanitizer", "desktop evidence sanitizer returned wrong type")
        assert_true(sanitized["source_summary"]["redaction_applied"], "desktop evidence sanitizer missed redaction")
        assert_true(sanitized["truncation"]["line_truncation_count"] >= 1, "desktop evidence sanitizer missed long line truncation")
        assert_true(sanitized["writes_output_file"], "desktop evidence sanitizer should write requested output file")
        assert_true(sanitized_path.exists(), "desktop evidence sanitizer did not write output file")
        assert_true("sk-testsecret" not in sanitized["sanitized_text"], "desktop evidence sanitizer leaked sensitive text in payload")
        assert_true("sk-testsecret" not in sanitized_path.read_text(encoding="utf-8"), "desktop evidence sanitizer leaked sensitive text in output file")
        sanitized_md = run_cli(
            "sanitize-desktop-evidence",
            str(project),
            "--evidence",
            unsafe_evidence,
            "--max-line-chars",
            "160",
            "--max-total-chars",
            "900",
            "--format",
            "markdown",
        )
        assert_true("Long Novel Agent Desktop Evidence Sanitizer" in sanitized_md.stdout, "desktop evidence sanitizer markdown missing title")
        assert_true("sk-testsecret" not in sanitized_md.stdout, "desktop evidence sanitizer markdown leaked sensitive text")
        unsafe_md = run_cli(
            "ingest-desktop-evidence",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "2",
            "--evidence",
            unsafe_evidence,
            "--format",
            "markdown",
        )
        assert_true("## Warnings" in unsafe_md.stdout, "desktop evidence import markdown missed warnings section")
        recorded_import = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                str(imported_path),
                "--agent-id",
                "codex-local",
                "--client-version",
                "test-client",
            )
        )
        assert_true(recorded_import["validation"]["ok"], "desktop evidence import output should record successfully")
        unsafe_results = {
            "checks": [
                {
                    **item,
                    "status": "passed",
                    "evidence": f"{desktop_check_evidence(str(item['id']))} api_key=sk-recordsecret1234567890",
                }
                for item in read_only["manual_result_template"]
            ]
        }
        unsafe_recorded = load_stdout(
            run_cli(
                "record-desktop-check",
                str(project),
                "--platform",
                "codex",
                "--mode",
                "read-only",
                "--chapter",
                "2",
                "--results",
                json.dumps(unsafe_results, ensure_ascii=False),
                "--agent-id",
                "codex-local",
                "--client-version",
                "unsafe-test-client",
            )
        )
        assert_true(unsafe_recorded["validation"]["ok"], "desktop check safety warnings should not block complete evidence")
        assert_true(unsafe_recorded["validation"]["safety_warnings"], "desktop check record missed safety warnings")
        assert_true("sk-recordsecret" not in json.dumps(unsafe_recorded["record"]["checks"], ensure_ascii=False), "desktop check record leaked sensitive evidence")
        writer = run_cli("desktop-checklist", str(project), "--platform", "cursor", "--mode", "writer", "--install-config", str(Path(tmp) / "cursor.json"), "--format", "markdown")
        assert_true("Long Novel Agent Desktop Client Checklist" in writer.stdout, "desktop checklist markdown missing title")
        writer_json = load_stdout(run_cli("desktop-checklist", str(project), "--platform", "cursor", "--mode", "writer", "--install-config", str(Path(tmp) / "cursor.json"), "--format", "json"))
        writer_ids = {item["id"] for item in writer_json["checklist"]}
        assert_true("write_session_preflight_gate" in writer_ids, "writer checklist missed write session preflight gate")
        assert_true("proposal_readiness_gate" in writer_ids, "writer checklist missed proposal readiness gate")
        assert_true("range_readiness_gate" in writer_ids, "writer checklist missed range readiness gate")
        assert_true(writer_json["evidence_pack"]["required_evidence_count"] == len(writer_json["checklist"]), "writer checklist evidence pack count mismatch")
        assert_true("write_session_preflight_gate" in writer.stdout, "writer checklist markdown missed write session preflight gate")
        assert_true("acceptance_write_gate" in writer.stdout, "writer checklist missed acceptance write gate")
        assert_true("proposal_readiness_gate" in writer.stdout, "writer checklist markdown missed proposal readiness gate")
        assert_true("not proof" in writer.stdout.lower(), "desktop checklist should state it is not proof")
        assert_true("Evidence Pack" in writer.stdout, "desktop checklist markdown missed evidence pack")
        assert_true("record-desktop-check" in writer.stdout, "desktop checklist markdown missed record command")
        return {
            "read_only_checks": len(read_only["checklist"]),
            "evidence_session_checks": len(evidence_session["checks"]),
            "evidence_session_prompts": len(evidence_session["copy_prompts"]),
            "records": len(listed["records"]),
            "imported_checks": len(imported["matched_check_ids"]),
            "result_json_imported_checks": len(result_json_import["matched_check_ids"]),
            "structured_json_payloads": structured_import["source_summary"]["detected_json_payloads"],
            "structured_json_imported_checks": len(structured_import["matched_check_ids"]),
            "transcript_tool_names": transcript_import["source_summary"]["detected_tool_name_count"],
            "transcript_imported_checks": len(transcript_import["matched_check_ids"]),
            "safety_warning_count": unsafe_import["source_summary"]["warning_count"],
            "record_safety_warnings": len(unsafe_recorded["validation"]["safety_warnings"]),
            "status": status["readiness"],
            "handoff_status": handoff["status"],
            "writer_markdown": True,
        }


def verify_agent_activity_log() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-agent-activity-") as tmp:
        project = Path(tmp) / "novel"
        chapters = project / "chapters"
        chapters.mkdir(parents=True)
        run_cli("init", str(project), "--title", "agent-activity")
        context_file = project / "context.md"
        context_file.write_text("# Chapter Context\n\n第 2 章只能使用第 1 章已接受事实。", encoding="utf-8")
        draft_file = chapters / "002.md"
        draft_file.write_text(
            "第二章\n\n林追把账页留在灯下，确认铜钥匙仍在自己掌心。白石商会的人没有进门，雨声遮住了巷口脚步。",
            encoding="utf-8",
        )
        recorded = load_stdout(
            run_cli(
                "record-agent-activity",
                str(project),
                "--agent-id",
                "codex-local",
                "--platform",
                "codex",
                "--action",
                "prepare-session",
                "--chapter",
                "2",
                "--summary",
                "Prepared chapter 2 context before drafting.",
                "--context-source",
                "prepare_chapter_session",
                "--context-file",
                str(context_file),
                "--file",
                "chapters/002.md",
                "--status",
                "done",
                "--handoff-to",
                "cursor-local",
            )
        )
        assert_true(recorded["type"] == "agent_activity_record", "agent activity record returned wrong type")
        record = recorded["record"]
        assert_true(record["context"]["file"] == "context.md", "agent activity should store project-relative context path")
        assert_true(len(record["context"]["hash"]) == 64, "agent activity context hash missing")
        assert_true(record["files"] == ["chapters/002.md"], "agent activity files were not recorded")
        listed = load_stdout(run_cli("list-agent-activity", str(project), "--agent-id", "codex-local", "--chapter", "2", "--limit", "5"))
        assert_true(listed["type"] == "agent_activity", "agent activity list returned wrong type")
        assert_true(len(listed["records"]) == 1, "agent activity list should return one row")
        listed_md = run_cli("list-agent-activity", str(project), "--format", "markdown")
        assert_true("Long Novel Agent Activity" in listed_md.stdout, "agent activity markdown missing title")
        status = load_stdout(run_cli("project-status", str(project), "--platform", "codex"))
        assert_true(status["counts"]["agent_activity"] == 1, "project-status missed agent activity count")
        assert_true(status["latest_agent_activity"]["id"] == record["id"], "project-status missed latest agent activity")
        status_md = run_cli("project-status", str(project), "--platform", "codex", "--format", "markdown")
        assert_true("Latest Agent Activity" in status_md.stdout, "project-status markdown missed agent activity section")
        handoff = load_stdout(run_cli("handoff-report", str(project), "--chapter", "2"))
        assert_true(handoff["recent_agent_activity"][0]["id"] == record["id"], "handoff-report missed recent agent activity")
        handoff_md = run_cli("handoff-report", str(project), "--chapter", "2", "--format", "markdown")
        assert_true("Recent Agent Activity" in handoff_md.stdout, "handoff-report markdown missed agent activity section")
        assert_true("Prepared chapter 2 context" in handoff_md.stdout, "handoff-report markdown missed activity summary")
        session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "2", "--summary-only"))
        assert_true(session["recent_agent_activity"][0]["id"] == record["id"], "prepare-session missed recent agent activity")
        assert_true(session["handoff_report"]["recent_agent_activity"][0]["id"] == record["id"], "prepare-session handoff report missed agent activity")
        session_md = run_cli("prepare-session", str(project), "--chapter", "2", "--format", "markdown")
        assert_true("Recent Agent Activity" in session_md.stdout, "prepare-session markdown missed agent activity section")
        activity_report = load_stdout(run_cli("agent-activity-report", str(project), "--chapter", "2", "--required-action", "prepare-session"))
        assert_true(activity_report["type"] == "agent_activity_report", "agent activity report returned wrong type")
        assert_true(activity_report["status"] == "ready", "agent activity report should pass for specific context-backed activity")
        assert_true(activity_report["missing_required_actions"] == [], "agent activity report should find required action")
        assert_true(activity_report["ok"], "agent activity report should be ok for complete activity")
        assert_true(activity_report["handoff_quality"]["score"] == 100, "complete activity should have perfect handoff quality")
        assert_true(activity_report["handoff_quality"]["rating"] == "excellent", "complete activity should be excellent handoff quality")
        assert_true(activity_report["quality_score"] == 100, "agent activity report should expose top-level quality score")
        activity_report_md = run_cli("agent-activity-report", str(project), "--chapter", "2", "--required-action", "prepare-session", "--format", "markdown")
        assert_true("Long Novel Agent Activity Report" in activity_report_md.stdout, "agent activity report markdown missing title")
        assert_true("## Handoff Quality" in activity_report_md.stdout, "agent activity report markdown missing handoff quality")
        assert_true("Score: 100" in activity_report_md.stdout, "agent activity report markdown missed quality score")
        handoff_integrity = load_stdout(run_cli("handoff-integrity", str(project), "--chapter", "2", "--required-action", "prepare-session"))
        assert_true(handoff_integrity["type"] == "handoff_integrity_report", "handoff-integrity returned wrong type")
        assert_true(handoff_integrity["status"] in {"ready", "ready_with_warnings"}, "handoff-integrity should pass usable activity with warnings at most")
        assert_true(handoff_integrity["current_context"]["fingerprint"], "handoff-integrity missed context fingerprint")
        assert_true(handoff_integrity["activity_report"]["status"] == "ready", "handoff-integrity missed nested activity report")
        handoff_integrity_md = run_cli("handoff-integrity", str(project), "--chapter", "2", "--required-action", "prepare-session", "--format", "markdown")
        assert_true("Long Novel Agent Handoff Integrity" in handoff_integrity_md.stdout, "handoff-integrity markdown missing title")
        stale_integrity = load_stdout(run_cli("handoff-integrity", str(project), "--chapter", "2", "--required-action", "prepare-session", "--expected-context-hash", "0" * 64))
        assert_true(stale_integrity["status"] == "needs_review", "handoff-integrity should flag expected context hash mismatch")
        handoff_range = load_stdout(
            run_cli(
                "handoff-range-report",
                str(project),
                "--start",
                "2",
                "--end",
                "2",
                "--handoff-chapter",
                "3",
                "--required-action",
                "prepare-session",
            )
        )
        assert_true(handoff_range["type"] == "handoff_range_report", "handoff-range-report returned wrong type")
        assert_true(handoff_range["chapter_range_readiness"]["status"] in {"ready", "ready_with_notes"}, "handoff-range-report should run real chapter range readiness")
        assert_true(handoff_range["handoff_integrity"]["status"] in {"ready", "ready_with_warnings", "needs_review"}, "handoff-range-report missed nested handoff integrity")
        assert_true(handoff_range["pending_range_proposals_count"] == 0, "handoff-range-report should count range proposals")
        handoff_range_md = run_cli("handoff-range-report", str(project), "--start", "2", "--end", "2", "--handoff-chapter", "3", "--required-action", "prepare-session", "--format", "markdown")
        assert_true("Long Novel Agent Handoff Range Report" in handoff_range_md.stdout, "handoff-range-report markdown missing title")
        run_cli("declare-agent", str(project), "--agent-id", "codex-local", "--platform", "codex", "--capabilities", "mcp,cli,file-read,file-write")
        run_cli("declare-agent", str(project), "--agent-id", "cursor-local", "--platform", "cursor", "--capabilities", "mcp,cli,file-read,file-write")
        readiness = load_stdout(
            run_cli(
                "handoff-readiness",
                str(project),
                "--chapter",
                "2",
                "--incoming-agent-id",
                "cursor-local",
                "--incoming-platform",
                "cursor",
                "--outgoing-agent-id",
                "codex-local",
                "--mode",
                "writer",
            )
        )
        assert_true(readiness["recent_agent_activity"][0]["id"] == record["id"], "handoff-readiness missed recent agent activity")
        activity_check = next((item for item in readiness["checks"] if item.get("id") == "outgoing_agent_activity_visible"), {})
        assert_true(activity_check.get("ok"), "handoff-readiness should verify outgoing agent activity when outgoing agent is specified")
        readiness_md = run_cli(
            "handoff-readiness",
            str(project),
            "--chapter",
            "2",
            "--incoming-agent-id",
            "cursor-local",
            "--incoming-platform",
            "cursor",
            "--outgoing-agent-id",
            "codex-local",
            "--mode",
            "writer",
            "--format",
            "markdown",
        )
        assert_true("Recent Agent Activity" in readiness_md.stdout, "handoff-readiness markdown missed agent activity section")
        assert_true("Prepared chapter 2 context" in readiness_md.stdout, "handoff-readiness markdown missed activity summary")
        run_cli(
            "record-agent-activity",
            str(project),
            "--agent-id",
            "codex-local",
            "--platform",
            "codex",
            "--action",
            "future-plan",
            "--chapter",
            "9",
            "--summary",
            "Future-only activity should not appear in chapter 2 handoff.",
        )
        early_handoff = load_stdout(run_cli("handoff-report", str(project), "--chapter", "2"))
        assert_true(len(early_handoff["recent_agent_activity"]) == 1, "handoff-report should hide future chapter activity")
        later_handoff = load_stdout(run_cli("handoff-report", str(project), "--chapter", "9"))
        assert_true(len(later_handoff["recent_agent_activity"]) == 2, "handoff-report should reveal activity at its target chapter")
        run_cli(
            "record-agent-activity",
            str(project),
            "--agent-id",
            "codex-local",
            "--platform",
            "codex",
            "--action",
            "check",
            "--chapter",
            "2",
            "--summary",
            "ok",
        )
        weak_report = load_stdout(run_cli("agent-activity-report", str(project), "--chapter", "2", "--required-action", "check"))
        issue_types = {item.get("type") for item in weak_report["record_issues"]}
        assert_true(weak_report["status"] == "needs_review", "agent activity report should flag weak activity summaries")
        assert_true({"weak_summary", "missing_context", "missing_files"}.issubset(issue_types), "agent activity report missed weak activity issues")
        assert_true(weak_report["handoff_quality"]["score"] < 100, "weak activity should lower handoff quality")
        assert_true(weak_report["handoff_quality"]["deductions"], "weak activity should include quality deductions")
        assert_true(any(item.get("reason") == "weak_summary" for item in weak_report["handoff_quality"]["deductions"]), "weak activity quality missed weak summary deduction")
        audit = load_stdout(run_cli("list-audit", str(project), "--limit", "10"))
        assert_true(any(row.get("operation") == "record-agent-activity" for row in audit["audit"]), "agent activity record should be audited")
        return {
            "records": len(listed["records"]),
            "context_hash": True,
            "activity_report_status": activity_report["status"],
            "activity_report_quality_score": activity_report["handoff_quality"]["score"],
            "handoff_integrity_status": handoff_integrity["status"],
            "handoff_range_status": handoff_range["status"],
            "stale_handoff_integrity_status": stale_integrity["status"],
            "weak_activity_report_status": weak_report["status"],
            "weak_activity_quality_score": weak_report["handoff_quality"]["score"],
            "status_count": status["counts"]["agent_activity"],
            "handoff_activity": len(later_handoff["recent_agent_activity"]),
            "readiness_activity": len(readiness["recent_agent_activity"]),
        }


def verify_desktop_setup_verification() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-desktop-verify-") as tmp:
        project = Path(tmp) / "novel"
        config = Path(tmp) / "cursor-mcp.json"
        run_cli("init", str(project), "--title", "desktop-verify")
        subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--target",
                str(Path(tmp) / "skills"),
                "--mcp-platform",
                "cursor",
                "--mcp-config",
                str(config),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        verified = load_stdout(
            run_cli(
                "desktop-verify",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "read-only",
                "--install-config",
                str(config),
                "--start-mcp-test",
                "--format",
                "json",
            )
        )
        assert_true(verified["type"] == "desktop_setup_verification", "desktop verify returned wrong type")
        assert_true(verified["ok"], "desktop verify should pass for installed read-only config")
        assert_true(any(item["name"] == "mcp_start_check" and item["ok"] for item in verified["checks"]), "desktop verify missed MCP start check")
        version_check = next((item for item in verified["checks"] if item["name"] == "installed_kit_matches_source"), {})
        assert_true(version_check.get("ok"), "desktop verify should pass installed kit version check")
        assert_true(version_check.get("detail", {}).get("installed_version") == current_kit_version(), "desktop verify installed version mismatch")
        assert_true(version_check.get("detail", {}).get("skill_content_matches_source"), "desktop verify installed skill content mismatch")
        status_with_config = load_stdout(run_cli("project-status", str(project), "--platform", "cursor", "--install-config", str(config), "--format", "json"))
        local_status = status_with_config["verification_status"]["local_mcp_config"]
        real_status = status_with_config["verification_status"]["real_desktop_client"]
        assert_true(local_status["status"] == "passed", "project-status should expose local MCP config check status")
        assert_true(not local_status["is_real_client_evidence"], "local MCP config check should not be marked real client evidence")
        assert_true(real_status["status"] == "missing", "project-status should not treat local MCP config check as real desktop evidence")
        not_initialized = load_stdout(
            run_cli(
                "desktop-next-step",
                str(Path(tmp) / "not-initialized"),
                "--platform",
                "cursor",
                "--mode",
                "read-only",
                "--install-config",
                str(config),
                "--format",
                "json",
            )
        )
        assert_true(not_initialized["type"] == "desktop_next_step", "desktop-next-step returned wrong type")
        assert_true(not_initialized["status"] == "needs_project_initialization", "desktop-next-step should ask to initialize an uninitialized project")
        assert_true(not_initialized["primary_action"]["id"] == "quickstart_project", "desktop-next-step should choose quickstart for uninitialized project")
        assert_true(not_initialized["would_write_project_state"] is False, "desktop-next-step report should be read-only")
        next_step = load_stdout(
            run_cli(
                "desktop-next-step",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "read-only",
                "--install-config",
                str(config),
                "--chapter",
                "1",
                "--include-session",
                "--format",
                "json",
            )
        )
        assert_true(next_step["status"] == "needs_real_client_evidence", "desktop-next-step should ask for real desktop evidence before writing")
        assert_true(next_step["tool_profile"] == "full", "desktop-next-step should expose default tool profile")
        assert_true(next_step["primary_action"]["id"] == "desktop_checklist", "desktop-next-step should choose desktop checklist when real evidence is missing")
        assert_true(next_step["verification_status"]["local_mcp_config"]["status"] == "passed", "desktop-next-step should include local MCP status")
        assert_true(next_step["verification_status"]["selected_platform_mode"]["status"] == "missing", "desktop-next-step should check selected platform/mode evidence")
        assert_true(next_step["chapter_session"]["type"] == "chapter_session_packet", "desktop-next-step include-session missed chapter session summary")
        next_step_markdown = run_cli("desktop-next-step", str(project), "--platform", "cursor", "--mode", "read-only", "--install-config", str(config), "--format", "markdown")
        assert_true("Long Novel Agent Desktop Next Step" in next_step_markdown.stdout, "desktop-next-step markdown missing title")
        assert_true("MCP tool profile: full" in next_step_markdown.stdout, "desktop-next-step markdown missing tool profile")
        diagnostics = load_stdout(
            run_cli(
                "desktop-diagnostics",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "read-only",
                "--install-config",
                str(config),
                "--chapter",
                "1",
                "--include-session",
                "--format",
                "json",
            )
        )
        assert_true(diagnostics["type"] == "desktop_diagnostics", "desktop-diagnostics returned wrong type")
        assert_true(diagnostics["local_only"] is True and diagnostics["requires_server"] is False, "desktop-diagnostics should be local-only")
        assert_true(diagnostics["would_write_project_state"] is False, "desktop-diagnostics should not write project state")
        assert_true(diagnostics["records_real_client_evidence"] is False, "desktop-diagnostics should not record desktop evidence")
        assert_true(diagnostics["status"] == "ready_with_warnings", "desktop-diagnostics should allow local setup with missing GUI evidence as warning")
        assert_true(diagnostics["next_step_status"] == "needs_real_client_evidence", "desktop-diagnostics should include next-step status")
        assert_true(any(item["id"] == "real_desktop_client_evidence" and not item["ok"] for item in diagnostics["checks"]), "desktop-diagnostics missed real client evidence check")
        diagnostics_markdown = run_cli("desktop-diagnostics", str(project), "--platform", "cursor", "--mode", "read-only", "--install-config", str(config), "--format", "markdown")
        assert_true("Long Novel Agent Desktop Diagnostics" in diagnostics_markdown.stdout, "desktop-diagnostics markdown missing title")
        assert_true("Real desktop client evidence" in diagnostics_markdown.stdout, "desktop-diagnostics markdown missed real evidence check")
        markdown = run_cli("desktop-verify", str(project), "--platform", "cursor", "--mode", "read-only", "--install-config", str(config), "--format", "markdown")
        assert_true("Long Novel Agent Desktop Verification" in markdown.stdout, "desktop verify markdown missing title")
        profile_mismatch = load_stdout(run_cli("desktop-verify", str(project), "--platform", "cursor", "--mode", "read-only", "--tool-profile", "core", "--install-config", str(config), "--format", "json"))
        assert_true(not profile_mismatch["ok"], "desktop verify should fail when expected tool profile does not match config")
        assert_true(any(item["name"] == "mcp_tool_profile_matches" and not item["ok"] for item in profile_mismatch["checks"]), "desktop verify missed tool profile mismatch")
        next_step_profile_mismatch = load_stdout(
            run_cli(
                "desktop-next-step",
                str(project),
                "--platform",
                "cursor",
                "--mode",
                "read-only",
                "--tool-profile",
                "core",
                "--install-config",
                str(config),
                "--chapter",
                "1",
                "--format",
                "json",
            )
        )
        assert_true(next_step_profile_mismatch["tool_profile"] == "core", "desktop-next-step should preserve requested core tool profile")
        assert_true(next_step_profile_mismatch["status"] == "needs_local_install", "desktop-next-step should flag a tool profile config mismatch")
        assert_true(next_step_profile_mismatch["primary_action"]["id"] == "verify_or_install_local_mcp", "desktop-next-step should choose local MCP action on profile mismatch")
        installed_skill = Path(tmp) / "skills" / "long-novel-agent" / "SKILL.md"
        original_skill_text = installed_skill.read_text(encoding="utf-8")
        installed_skill.write_text(original_skill_text + "\n<!-- stale skill marker -->\n", encoding="utf-8")
        stale_skill = load_stdout(run_cli("desktop-verify", str(project), "--platform", "cursor", "--mode", "read-only", "--install-config", str(config), "--format", "json"))
        assert_true(not stale_skill["ok"], "desktop verify should fail when installed skill content is stale")
        stale_skill_check = next((item for item in stale_skill["checks"] if item["name"] == "installed_kit_matches_source"), {})
        assert_true(not stale_skill_check.get("ok"), "desktop verify missed stale installed skill content")
        assert_true(stale_skill_check.get("detail", {}).get("version_matches"), "desktop verify should keep version match for skill-only staleness")
        assert_true(not stale_skill_check.get("detail", {}).get("skill_content_matches_source"), "desktop verify should report stale skill content")
        assert_true(any("--upgrade" in str(action) for action in stale_skill["recommended_actions"]), "desktop verify should recommend upgrade for stale installed skill")
        installed_skill.write_text(original_skill_text, encoding="utf-8")
        installed_cli = Path(tmp) / "skills" / "long-novel-agent" / "cli.py"
        stale_text = re.sub(r"(?m)^KIT_VERSION\s*=\s*[\"'][^\"']+[\"']", 'KIT_VERSION = "0.0.0-stale"', installed_cli.read_text(encoding="utf-8"), count=1)
        installed_cli.write_text(stale_text, encoding="utf-8")
        stale = load_stdout(run_cli("desktop-verify", str(project), "--platform", "cursor", "--mode", "read-only", "--install-config", str(config), "--format", "json"))
        assert_true(not stale["ok"], "desktop verify should fail when installed kit version is stale")
        stale_version_check = next((item for item in stale["checks"] if item["name"] == "installed_kit_matches_source"), {})
        assert_true(not stale_version_check.get("ok"), "desktop verify missed stale installed kit version")
        assert_true(stale_version_check.get("detail", {}).get("installed_version") == "0.0.0-stale", "desktop verify did not report stale version")
        assert_true(any("--upgrade" in str(action) for action in stale["recommended_actions"]), "desktop verify should recommend upgrade for stale installed kit")
        mismatch = load_stdout(run_cli("desktop-verify", str(project), "--platform", "cursor", "--mode", "writer", "--install-config", str(config), "--format", "json"))
        assert_true(not mismatch["ok"], "desktop verify should fail when expected writer mode sees read-only config")
        assert_true(any(item["name"] == "mcp_mode_matches" and not item["ok"] for item in mismatch["checks"]), "desktop verify missed mode mismatch")
        assert_true(mismatch["recommended_actions"], "desktop verify should recommend action on mode mismatch")
        missing = load_stdout(run_cli("desktop-verify", str(project), "--platform", "generic", "--mode", "read-only", "--format", "json"))
        assert_true(not missing["ok"], "desktop verify should fail without a known config path")
        return {
            "start_mcp_test": True,
            "mode_mismatch_detected": True,
            "stale_installed_kit_detected": True,
            "stale_installed_skill_detected": True,
            "missing_config_detected": True,
            "desktop_next_step_status": next_step["status"],
            "desktop_next_step_uninitialized": not_initialized["status"],
            "desktop_diagnostics_status": diagnostics["status"],
        }


def verify_snapshots_and_locks() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-safety-") as tmp:
        project = Path(tmp) / "novel"
        run_cli("init", str(project), "--title", "safety-test")
        baseline = load_stdout(run_cli("create-snapshot", str(project), "--reason", "baseline"))
        run_cli("add-fact", str(project), "--kind", "event", "--subject", "测试事件", "--predicate", "state", "--object", "临时事实")
        facts = load_stdout(run_cli("list-facts", str(project), "--include-inactive"))
        assert_true(any(row.get("subject") == "测试事件" for row in facts["facts"]), "test fact was not written")
        restored = load_stdout(run_cli("restore-snapshot", str(project), "--snapshot-id", baseline["snapshot"]["id"]))
        facts_after = load_stdout(run_cli("list-facts", str(project), "--include-inactive"))
        assert_true(not any(row.get("subject") == "测试事件" for row in facts_after["facts"]), "restore-snapshot did not restore facts")
        assert_true(restored.get("backup_snapshot", {}).get("id"), "restore did not create backup snapshot")

        lock_dir = project / ".novel-agent" / ".write.lock"
        lock_dir.mkdir()
        (lock_dir / "lock.json").write_text(json.dumps({"operation": "test-lock", "pid": 999999, "created_at_ts": 99999999999}, ensure_ascii=False), encoding="utf-8")
        locked = run_cli("add-source", str(project), "--summary", "locked", check=False)
        assert_true(locked.returncode != 0, "fresh lock should reject writes")
        shutil.rmtree(lock_dir)

        lock_dir.mkdir()
        (lock_dir / "lock.json").write_text(json.dumps({"operation": "stale-lock", "pid": 999999, "created_at_ts": 0}, ensure_ascii=False), encoding="utf-8")
        stale = run_cli("add-source", str(project), "--summary", "stale lock cleaned")
        assert_true(stale.returncode == 0, "stale lock should be cleaned and write should succeed")
        assert_true(not lock_dir.exists(), "write lock was not released")
        snapshots = load_stdout(run_cli("list-snapshots", str(project)))
        assert_true(len(snapshots["snapshots"]) >= 2, "snapshot list missed baseline or restore backup")
        audit = load_stdout(run_cli("list-audit", str(project), "--limit", "20"))
        operations = {row.get("operation") for row in audit["audit"]}
        assert_true("add-source" in operations and "restore-snapshot" in operations, "audit log missed write operations")
        return {
            "baseline_snapshot": baseline["snapshot"]["id"],
            "restore_backup": restored["backup_snapshot"]["id"],
            "snapshot_count": len(snapshots["snapshots"]),
            "audit_count": len(audit["audit"]),
        }


def verify_adversarial_cases() -> dict[str, Any]:
    payload = json.loads(ADVERSARIAL_CASES.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    assert_true(cases, "adversarial case library is empty")
    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="long-novel-adversarial-") as tmp:
        project = Path(tmp) / "novel"
        chapters = project / "chapters"
        chapters.mkdir(parents=True)
        run_cli("init", str(project), "--title", "adversarial-test")
        for case in cases:
            case_id = case["id"]
            setup_adversarial_case(project, case)
            chapter = int(case.get("chapter") or 1)
            draft = chapters / f"{chapter:03d}-{case_id}.md"
            draft.write_text(case.get("draft", ""), encoding="utf-8")
            if case.get("expected_issue_types"):
                check = load_stdout(run_cli("check-chapter", str(project), "--chapter", str(chapter), "--file", str(draft)))
                issue_types = {item["type"] for item in check["issues"]}
                for expected in case["expected_issue_types"]:
                    assert_true(expected in issue_types, f"{case_id} missed issue type {expected}")
                results[case_id] = {"issue_types": sorted(issue_types)}
            if case.get("expected_contract_status"):
                diff = load_stdout(run_cli("diff-contract", str(project), "--chapter", str(chapter), "--file", str(draft)))
                assert_true(diff["status"] == case["expected_contract_status"], f"{case_id} contract diff status mismatch")
                results.setdefault(case_id, {})["contract_summary"] = diff["summary"]
            if case.get("proposal"):
                proposed = load_stdout(
                    run_cli(
                        "propose-after-write",
                        str(project),
                        "--chapter",
                        str(chapter),
                        "--file",
                        str(draft),
                        "--updates",
                        json.dumps(case["proposal"], ensure_ascii=False),
                    )
                )
                risk = proposed["validation"]["risk"]
                if case.get("expected_risk_level"):
                    assert_true(risk["level"] == case["expected_risk_level"], f"{case_id} risk level mismatch")
                issue_types = {item["type"] for item in proposed["validation"]["issues"]}
                if case.get("expected_risk_level") == "high":
                    assert_true("high_risk_update" in issue_types, f"{case_id} missed high risk warning")
                for expected in case.get("expected_validation_issue_types", []):
                    assert_true(expected in issue_types, f"{case_id} missed proposal validation issue type {expected}")
                if case.get("expected_apply_rejected"):
                    rejected_apply = run_cli("apply-after-write", str(project), "--proposal-id", proposed["proposal"]["id"], check=False)
                    assert_true(rejected_apply.returncode != 0, f"{case_id} apply should reject invalid proposal")
                results[case_id] = {"risk": risk, "proposal_issue_types": sorted(issue_types)}
    return {"case_count": len(cases), "cases": results}


def setup_adversarial_case(project: Path, case: dict[str, Any]) -> None:
    state = project / ".novel-agent"
    setup = case.get("setup") or {}
    rules = setup.get("rules")
    if isinstance(rules, dict):
        rules_path = state / "rules.json"
        current = json.loads(rules_path.read_text(encoding="utf-8"))
        for key, value in rules.items():
            current[key] = value
        rules_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for fact in setup.get("facts", []) if isinstance(setup.get("facts"), list) else []:
        args = [
            "add-fact",
            str(project),
            "--kind",
            str(fact.get("kind") or "fact"),
            "--subject",
            str(fact.get("subject") or ""),
            "--predicate",
            str(fact.get("predicate") or "is"),
            "--object",
            str(fact.get("object") or ""),
        ]
        if fact.get("forbidden_phrases"):
            args.extend(["--forbidden", ",".join(fact["forbidden_phrases"])])
        run_cli(*args)
    contracts = setup.get("contracts")
    if isinstance(contracts, list):
        with (state / "contracts.jsonl").open("a", encoding="utf-8") as handle:
            for contract in contracts:
                handle.write(json.dumps(contract, ensure_ascii=False) + "\n")


def verify_migration() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-migrate-") as tmp:
        project = Path(tmp) / "novel"
        run_cli("init", str(project), "--title", "migration-test")
        state = project / ".novel-agent"
        manifest_path = state / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("state_schema_version", None)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (state / "audit.jsonl").unlink()
        migrated = load_stdout(run_cli("add-source", str(project), "--summary", "migration source"))
        manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert_true(manifest_after.get("state_schema_version") == 5, "migration did not update state_schema_version")
        assert_true((state / "agent_activity.jsonl").exists(), "migration did not create agent_activity.jsonl")
        audit_rows = [json.loads(line) for line in (state / "audit.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        assert_true(any(row.get("operation") == "migrate_state" for row in audit_rows), "migration audit row missing")
        assert_true(migrated.get("migration", {}).get("changed"), "write result did not report migration")
        return {"state_schema_version": manifest_after["state_schema_version"], "audit_rows": len(audit_rows)}


def verify_state_package_and_capabilities() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-package-") as tmp:
        project = Path(tmp) / "novel"
        chapters = project / "chapters"
        chapters.mkdir(parents=True)
        run_cli("init", str(project), "--title", "package-test")
        run_cli("declare-agent", str(project), "--agent-id", "agent-a", "--platform", "codex", "--capabilities", "mcp,cli,file-read,file-write")
        agents = load_stdout(run_cli("list-agents", str(project)))
        assert_true(agents["agents"][0]["capabilities"] == ["mcp", "cli", "file-read", "file-write"], "agent capabilities were not recorded")
        chapter = chapters / "001.md"
        chapter.write_text("林追推开窗。雨落在瓦上。\n\n“账册还在。”他说。", encoding="utf-8")
        style = load_stdout(run_cli("record-style", str(project), "--chapter", "1", "--file", str(chapter), "--note", "baseline"))
        assert_true(style["profile"]["sample_count"] == 1, "style sample was not recorded")
        package = load_stdout(run_cli("export-state", str(project), "--output", "handoff.novel-agent.zip"))
        assert_true(Path(package["package"]).exists(), "state package was not created")
        imported = Path(tmp) / "imported"
        imported_result = load_stdout(run_cli("import-state", str(imported), "--package", package["package"]))
        assert_true(Path(imported_result["state_dir"]).exists(), "state package was not imported")
        imported_agents = load_stdout(run_cli("list-agents", str(imported)))
        assert_true(imported_agents["agents"][0]["id"] == "agent-a", "imported state missed agent capabilities")
        return {"package_files": len(package["files"]), "agent_count": len(imported_agents["agents"])}


def verify_conflict_scope_and_style() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-style-") as tmp:
        project = Path(tmp) / "novel"
        chapters = project / "chapters"
        chapters.mkdir(parents=True)
        run_cli("init", str(project), "--title", "style-test")
        run_cli(
            "resolve-conflict",
            str(project),
            "--conflict",
            "林追身份认知",
            "--chosen",
            "林追自称账房先生",
            "--options",
            "林追真实身份是卧底",
            "--scope",
            "viewpoint",
            "--viewpoint",
            "白石商会",
        )
        chapter = chapters / "002.md"
        chapter.write_text("白石商会仍相信林追真实身份是卧底只是谣言。", encoding="utf-8")
        check = load_stdout(run_cli("check-chapter", str(project), "--chapter", "2", "--file", str(chapter)))
        assert_true("resolved_conflict_violation" not in {item["type"] for item in check["issues"]}, "viewpoint conflict should not be treated as canon violation")
        sample1 = chapters / "001.md"
        sample2 = chapters / "002-style.md"
        sample1.write_text("林追看账。雨声很近。\n\n“缺页。”他说。", encoding="utf-8")
        sample2.write_text("灯暗下去。纸页发潮。\n\n“有人来过。”她说。", encoding="utf-8")
        run_cli("record-style", str(project), "--chapter", "1", "--file", str(sample1))
        run_cli("record-style", str(project), "--chapter", "2", "--file", str(sample2))
        long_chapter = chapters / "003.md"
        long_chapter.write_text("林追在极其漫长的沉默里意识到这一切并不仅仅是账册缺页所能够解释的事情而是某种程度上关系到所有人命运的巨大转折。", encoding="utf-8")
        style_check = load_stdout(run_cli("check-chapter", str(project), "--chapter", "3", "--file", str(long_chapter)))
        assert_true("style_sentence_drift" in {item["type"] for item in style_check["issues"]}, "style drift was not reported")
        return {"style_issue_count": len(style_check["issues"])}


def verify_tool_visibility() -> dict[str, Any]:
    visible = load_stdout(run_cli("tool-visibility", "build_chapter_context", "--mode", "read-only", "--tool-profile", "core", "--format", "json"))
    hidden_by_profile = load_stdout(run_cli("tool-visibility", "verify_desktop_setup", "--mode", "read-only", "--tool-profile", "core", "--format", "json"))
    hidden_by_mode = load_stdout(run_cli("tool-visibility", "record_chapter", "--mode", "read-only", "--tool-profile", "core", "--format", "json"))
    unknown = load_stdout(run_cli("tool-visibility", "build-context", "--mode", "read-only", "--tool-profile", "core", "--format", "json"))
    markdown = run_cli("tool-visibility", "record_chapter", "--mode", "read-only", "--tool-profile", "core", "--format", "markdown")
    assert_true(visible["type"] == "tool_visibility_explanation", "tool-visibility returned wrong type")
    assert_true(visible["visible"] is True and not visible["blocked_by"], "build_chapter_context should be visible in read-only core profile")
    assert_true(visible["required_profile"] == "core", "visible tool missed required core profile")
    assert_true(hidden_by_profile["visible"] is False, "verify_desktop_setup should be hidden in core profile")
    assert_true("tool_profile" in hidden_by_profile["blocked_by"], "hidden profile explanation missed tool_profile blocker")
    assert_true(hidden_by_profile["suggested_profile"] == "writing", "hidden profile explanation should suggest writing profile")
    assert_true(hidden_by_mode["visible"] is False and hidden_by_mode["mutating_tool"] is True, "record_chapter should be hidden by read-only mode")
    assert_true("read_only_mode" in hidden_by_mode["blocked_by"], "write tool explanation missed read_only_mode blocker")
    assert_true(hidden_by_mode["suggested_mode"] == "writer", "write tool explanation should suggest writer mode")
    assert_true(unknown["known_tool"] is False and "build_chapter_context" in unknown["suggested_matches"], "unknown tool explanation should suggest close MCP tool names")
    assert_true("MCP Tool Visibility" in markdown.stdout, "tool-visibility markdown missing title")
    assert_true("read_only_mode" in markdown.stdout, "tool-visibility markdown missed blocker")
    return {
        "visible_tool_count_core_read_only": visible["visible_tool_count"],
        "all_tool_count": visible["all_tool_count"],
        "profile_blocker": hidden_by_profile["blocked_by"],
        "mode_blocker": hidden_by_mode["blocked_by"],
    }


def verify_mcp() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-mcp-") as tmp:
        project = Path(tmp) / "novel"
        config = Path(tmp) / "cursor-mcp.json"
        run_cli("init", str(project))
        subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--target",
                str(Path(tmp) / "skills"),
                "--mcp-platform",
                "cursor",
                "--mcp-config",
                str(config),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        desktop_results = {
            "checks": [
                {"id": "local_config_preflight", "status": "passed", "evidence": "local config verified"},
                {"id": "client_restart", "status": "passed", "evidence": "desktop client restarted"},
                {"id": "server_visible", "status": "passed", "evidence": "long-novel-agent visible in client"},
                {"id": "read_path_prepare_session", "status": "passed", "evidence": "prepare_chapter_session returned a session"},
                {"id": "context_gate", "status": "passed", "evidence": "build_chapter_context returned chapter-safe context"},
                {"id": "ready_check_gate", "status": "passed", "evidence": "build_chapter_readiness was available before delivery"},
                {"id": "range_readiness_gate", "status": "passed", "evidence": "build_chapter_range_readiness returned chapter_range_readiness status=needs_files"},
                {"id": "write_tools_hidden", "status": "passed", "evidence": "write tools hidden in read-only client"},
            ]
        }
        desktop_evidence_text = "\n".join(
            [
                "desktop-verify completed for codex read-only config; OK: True; prepare_chapter_session returned chapter_session_packet.",
                "Codex Desktop client restarted and reloaded MCP config.",
                "long-novel-agent tool list showed prepare_chapter_session, build_chapter_context, build_chapter_readiness, build_chapter_range_readiness, check_chapter, and doctor.",
                "prepare_chapter_session returned target_chapter=1 and handoff.",
                "build_chapter_context returned visible rules, sources, facts, debts, and contracts.",
                "Before ready, build_chapter_readiness is required; check_chapter and diff_chapter_contract are fallback checks.",
                "build_chapter_range_readiness returned chapter_range_readiness status=needs_files for the draft range.",
                "Read-only write tools hidden: did not expose record_chapter.",
            ]
        )
        mcp_intake_path = project / "mcp-source-intake.json"
        mcp_intake_path.write_text(
            json.dumps(
                {
                    "version": "test",
                    "type": "source_intake",
                    "project_dir": str(project),
                    "sources": [
                        {
                            "enabled": True,
                            "title": "MCP source",
                            "source_path": "materials/mcp.txt",
                            "summary": "MCP 只读校验确认的资料摘要。",
                            "chapter_range": "1",
                        }
                    ],
                    "research": [],
                    "conflicts": [],
                    "facts": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        chapters = project / "chapters"
        chapters.mkdir(exist_ok=True)
        proposal_chapter = chapters / "001.md"
        proposal_chapter.write_text("第一章确认码头钟声延迟，巡夜人留下半页潮汐表。", encoding="utf-8")
        proposal_updates = {
            "facts": [
                {
                    "kind": "event",
                    "subject": "第 1 章",
                    "predicate": "result",
                    "object": "巡夜人留下半页潮汐表",
                    "chapter_range": "1+",
                    "evidence": "巡夜人留下半页潮汐表。",
                }
            ],
            "handoff": "下一章追查半页潮汐表。",
        }
        proposal = load_stdout(
            run_cli(
                "propose-after-write",
                str(project),
                "--chapter",
                "1",
                "--file",
                str(proposal_chapter),
                "--updates",
                json.dumps(proposal_updates, ensure_ascii=False),
            )
        )
        proposal_id = proposal["proposal"]["id"]
        mcp_pack_dir = Path(tmp) / "mcp-pack"
        run_cli(
            "desktop-pack",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "1",
            "--output-dir",
            str(mcp_pack_dir),
            "--format",
            "json",
        )
        mcp_session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "1", "--platform", "codex", "--mode", "writer"))
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "doctor", "arguments": {"project_dir": str(project)}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "build_handoff_report", "arguments": {"project_dir": str(project), "chapter": 1}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "get_agent_template", "arguments": {"platform": "codex", "mode": "writer"}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "prepare_chapter_session", "arguments": {"project_dir": str(project), "chapter": 1, "platform": "codex", "mode": "writer", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 62, "method": "tools/call", "params": {"name": "check_write_session", "arguments": {"project_dir": str(project), "chapter": 1, "expected_project_id": mcp_session["project_identity"]["project_id"], "expected_state_sha256": mcp_session["project_state_fingerprint"]["sha256"], "expected_context_hash": mcp_session["chapter_context_fingerprint"]["sha256"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 46, "method": "tools/call", "params": {"name": "build_chapter_context_brief", "arguments": {"project_dir": str(project), "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 47, "method": "tools/call", "params": {"name": "build_agent_startup_prompt", "arguments": {"project_dir": str(project), "chapter": 1, "platform": "codex", "mode": "read-only", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "quickstart_project", "arguments": {"project_dir": str(Path(tmp) / "quickstart-mcp"), "platform": "codex", "dry_run": True, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "build_desktop_setup_guide", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "verify_desktop_setup", "arguments": {"project_dir": str(project), "platform": "cursor", "mode": "read-only", "install_config": str(config), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 43, "method": "tools/call", "params": {"name": "build_desktop_next_step", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 63, "method": "tools/call", "params": {"name": "build_desktop_diagnostics", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "pack_dir": str(mcp_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 55, "method": "tools/call", "params": {"name": "explain_tool_visibility", "arguments": {"tool_name": "record_chapter", "mode": "read-only", "tool_profile": "core", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 44, "method": "tools/call", "params": {"name": "build_desktop_evidence_session", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 58, "method": "tools/call", "params": {"name": "build_desktop_evidence_runbook", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 59, "method": "tools/call", "params": {"name": "build_desktop_user_steps", "arguments": {"pack_dir": str(mcp_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 60, "method": "tools/call", "params": {"name": "build_desktop_user_steps", "arguments": {"pack_dir": str(mcp_pack_dir), "format": "html"}}},
            {"jsonrpc": "2.0", "id": 48, "method": "tools/call", "params": {"name": "build_desktop_pack_schema_check", "arguments": {"pack_dir": str(mcp_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 53, "method": "tools/call", "params": {"name": "build_desktop_pack_freshness", "arguments": {"pack_dir": str(mcp_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 54, "method": "tools/call", "params": {"name": "build_chapter_session_freshness", "arguments": {"pack_dir": str(mcp_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "build_desktop_client_checklist", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 28, "method": "tools/call", "params": {"name": "build_desktop_results_doctor", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "results": desktop_results, "results_reference": "mcp-desktop-results.json", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 30, "method": "tools/call", "params": {"name": "build_desktop_evidence_packet", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "evidence_text": desktop_evidence_text, "results_reference": "mcp-imported-desktop-results.json", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 45, "method": "tools/call", "params": {"name": "build_desktop_evidence_review", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "evidence_text": desktop_evidence_text, "results_reference": "mcp-imported-desktop-results.json", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 61, "method": "tools/call", "params": {"name": "build_desktop_evidence_review", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "evidence_text": desktop_evidence_text, "results_reference": "mcp-imported-desktop-results.json", "format": "html"}}},
            {"jsonrpc": "2.0", "id": 56, "method": "tools/call", "params": {"name": "build_desktop_evidence_gaps", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "evidence_text": desktop_evidence_text, "results_reference": "mcp-imported-desktop-results.json", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "record_desktop_client_check", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "results": desktop_results, "agent_id": "codex-local", "client_version": "test-client"}}},
            {"jsonrpc": "2.0", "id": 29, "method": "tools/call", "params": {"name": "build_writer_mode_readiness", "arguments": {"project_dir": str(project), "platform": "codex", "chapter": 1, "author_confirmation": "Author approved enabling writer MCP mode for this desktop agent.", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 39, "method": "tools/call", "params": {"name": "build_author_review_queue", "arguments": {"project_dir": str(project), "chapter": 1, "platform": "codex", "mode": "writer", "start_chapter": 1, "end_chapter": 1, "handoff_chapter": 2, "draft_dir": "chapters", "author_confirmation": "Author approved review queue MCP verification.", "include_writer_mode": True, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 32, "method": "tools/call", "params": {"name": "sanitize_desktop_evidence", "arguments": {"project_dir": str(project), "evidence_text": desktop_evidence_text + "\napi_key=sk-mcptestsecret1234567890", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "list_desktop_client_checks", "arguments": {"project_dir": str(project), "platform": "codex", "limit": 5}}},
            {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "get_project_status", "arguments": {"project_dir": str(project), "platform": "codex", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 14, "method": "tools/call", "params": {"name": "declare_agent_capabilities", "arguments": {"project_dir": str(project), "agent_id": "codex-local", "platform": "codex", "capabilities": ["mcp", "file-read"]}}},
            {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "verify_handoff_readiness", "arguments": {"project_dir": str(project), "incoming_agent_id": "codex-local", "incoming_platform": "codex", "mode": "read-only", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {"name": "ingest_desktop_client_evidence", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "evidence_text": desktop_evidence_text, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": "build_desktop_onboarding_guide", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 18, "method": "tools/call", "params": {"name": "record_agent_activity", "arguments": {"project_dir": str(project), "agent_id": "codex-local", "platform": "codex", "action": "prepare-session", "chapter": 1, "summary": "MCP activity verification", "context_source": "prepare_chapter_session", "files": ["chapters/001.md"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 34, "method": "tools/call", "params": {"name": "build_agent_activity_report", "arguments": {"project_dir": str(project), "agent_id": "codex-local", "chapter": 1, "required_actions": ["prepare-session"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 35, "method": "tools/call", "params": {"name": "build_handoff_integrity_report", "arguments": {"project_dir": str(project), "agent_id": "codex-local", "chapter": 1, "required_actions": ["prepare-session"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 36, "method": "tools/call", "params": {"name": "build_handoff_range_report", "arguments": {"project_dir": str(project), "start_chapter": 1, "end_chapter": 1, "handoff_chapter": 2, "agent_id": "codex-local", "required_actions": ["prepare-session"], "draft_dir": "chapters", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 37, "method": "tools/call", "params": {"name": "build_chapter_range_delivery_packet", "arguments": {"project_dir": str(project), "start_chapter": 1, "end_chapter": 1, "handoff_chapter": 2, "agent_id": "codex-local", "required_actions": ["prepare-session"], "draft_dir": "chapters", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 38, "method": "tools/call", "params": {"name": "build_chapter_range_acceptance_plan", "arguments": {"project_dir": str(project), "start_chapter": 1, "end_chapter": 1, "handoff_chapter": 2, "agent_id": "codex-local", "required_actions": ["prepare-session"], "draft_dir": "chapters", "author_confirmation": "Author accepted chapter 1 for MCP verification.", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 19, "method": "tools/call", "params": {"name": "list_agent_activity", "arguments": {"project_dir": str(project), "agent_id": "codex-local", "limit": 5, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "validate_source_intake", "arguments": {"project_dir": str(project), "intake": str(mcp_intake_path), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "build_source_intake_template", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 22, "method": "tools/call", "params": {"name": "get_desktop_verification_matrix", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 23, "method": "tools/call", "params": {"name": "build_proposal_review", "arguments": {"project_dir": str(project), "proposal_id": proposal_id, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 24, "method": "tools/call", "params": {"name": "build_continuity_audit", "arguments": {"project_dir": str(project), "chapter": 1, "platform": "codex", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 25, "method": "tools/call", "params": {"name": "build_chapter_readiness", "arguments": {"project_dir": str(project), "chapter": 1, "file": str(proposal_chapter), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 40, "method": "tools/call", "params": {"name": "build_chapter_revision_prompt", "arguments": {"project_dir": str(project), "chapter": 1, "file": str(proposal_chapter), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 41, "method": "tools/call", "params": {"name": "build_chapter_revision_comparison", "arguments": {"project_dir": str(project), "chapter": 1, "before_file": str(proposal_chapter), "after_file": str(proposal_chapter), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 33, "method": "tools/call", "params": {"name": "build_chapter_delivery_packet", "arguments": {"project_dir": str(project), "chapter": 1, "file": str(proposal_chapter), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 42, "method": "tools/call", "params": {"name": "build_chapter_acceptance_plan", "arguments": {"project_dir": str(project), "chapter": 1, "file": str(proposal_chapter), "handoff_chapter": 2, "author_confirmation": "Author accepted chapter 1 for MCP verification.", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 26, "method": "tools/call", "params": {"name": "audit_gaoxia_import", "arguments": {"project_dir": str(project), "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 27, "method": "tools/call", "params": {"name": "build_proposal_readiness", "arguments": {"project_dir": str(project), "proposal_id": proposal_id, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 31, "method": "tools/call", "params": {"name": "build_chapter_range_readiness", "arguments": {"project_dir": str(project), "start_chapter": 1, "end_chapter": 2, "draft_dir": "chapters", "format": "markdown"}}},
        ]
        payload = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
        proc = subprocess.run([sys.executable, str(SERVER)], input=payload, text=True, capture_output=True, check=True)
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        by_id = {reply.get("id"): reply for reply in replies}
        names = {tool["name"] for tool in by_id[2]["result"]["tools"]}
        for name in (
            "init_wizard",
            "quickstart_project",
            "build_proposal_template",
            "get_proposal_schema",
            "diff_chapter_contract",
            "export_state_package",
            "import_state_package",
            "get_agent_template",
            "explain_tool_visibility",
            "build_agent_startup_prompt",
            "build_desktop_setup_guide",
            "build_desktop_onboarding_guide",
            "verify_desktop_setup",
            "build_desktop_next_step",
            "build_desktop_diagnostics",
            "build_desktop_evidence_session",
            "build_desktop_evidence_runbook",
            "build_desktop_user_steps",
            "build_author_actions",
            "build_acceptance_review",
            "build_desktop_pack_readiness",
            "build_desktop_pack_freshness",
            "build_chapter_session_freshness",
            "build_desktop_pack_schema_check",
            "build_starter_brief",
            "build_agent_takeover",
            "build_desktop_troubleshooting",
            "build_desktop_client_checklist",
            "build_source_intake_template",
            "validate_source_intake",
            "ingest_desktop_client_evidence",
            "sanitize_desktop_evidence",
            "build_desktop_results_doctor",
            "build_desktop_evidence_packet",
            "build_desktop_evidence_review",
            "build_desktop_evidence_gaps",
            "build_writer_mode_readiness",
            "build_author_review_queue",
            "record_desktop_client_check",
            "list_desktop_client_checks",
            "get_desktop_verification_matrix",
            "record_agent_activity",
            "list_agent_activity",
            "build_agent_activity_report",
            "build_handoff_integrity_report",
            "build_handoff_range_report",
            "get_project_status",
            "build_continuity_audit",
            "audit_gaoxia_import",
            "verify_handoff_readiness",
            "declare_agent_capabilities",
            "list_agent_capabilities",
            "record_style_sample",
            "create_snapshot",
            "list_snapshots",
            "restore_snapshot",
            "list_audit",
            "validate_proposal",
            "diff_proposal",
            "build_proposal_review",
            "build_proposal_readiness",
            "build_chapter_delivery_packet",
            "build_chapter_readiness",
            "build_chapter_revision_prompt",
            "build_chapter_revision_comparison",
            "build_chapter_acceptance_plan",
            "build_chapter_range_readiness",
            "build_chapter_range_delivery_packet",
            "build_chapter_range_acceptance_plan",
            "reject_proposal",
            "build_handoff_report",
            "build_chapter_context_brief",
            "prepare_chapter_session",
            "check_write_session",
            "doctor",
        ):
            assert_true(name in names, f"MCP tools/list missed {name}")
        assert_true(by_id[3]["result"]["structuredContent"]["ok"], "MCP doctor failed")
        assert_true(by_id[4]["result"]["structuredContent"]["type"] == "handoff_report", "MCP build_handoff_report failed")
        assert_true(by_id[5]["result"]["structuredContent"]["type"] == "agent_template", "MCP get_agent_template failed")
        assert_true(by_id[6]["result"]["structuredContent"]["type"] == "chapter_session_packet", "MCP prepare_chapter_session failed")
        assert_true("Long Novel Agent Chapter Session" in by_id[6]["result"]["content"][0]["text"], "MCP prepare_chapter_session markdown failed")
        assert_true(by_id[62]["result"]["structuredContent"]["type"] == "write_session_check", "MCP check_write_session failed")
        assert_true(by_id[62]["result"]["structuredContent"]["status"] == "ready", "MCP check_write_session should pass fresh session values")
        assert_true("Write Session Check" in by_id[62]["result"]["content"][0]["text"], "MCP check_write_session markdown failed")
        assert_true(by_id[46]["result"]["structuredContent"]["type"] == "chapter_context_brief", "MCP build_chapter_context_brief failed")
        assert_true("characters" not in by_id[46]["result"]["structuredContent"], "MCP context brief should not return full character table")
        assert_true("Chapter 1 Context Brief" in by_id[46]["result"]["content"][0]["text"], "MCP build_chapter_context_brief markdown failed")
        assert_true(by_id[47]["result"]["structuredContent"]["type"] == "agent_startup_prompt", "MCP build_agent_startup_prompt failed")
        assert_true(by_id[47]["result"]["structuredContent"]["would_write_project_state"] is False, "MCP build_agent_startup_prompt should be read-only")
        assert_true("Agent Startup Prompt" in by_id[47]["result"]["content"][0]["text"], "MCP build_agent_startup_prompt markdown failed")
        assert_true(by_id[7]["result"]["structuredContent"]["type"] == "quickstart_report", "MCP quickstart_project failed")
        assert_true("Long Novel Agent Quickstart" in by_id[7]["result"]["content"][0]["text"], "MCP quickstart_project markdown failed")
        assert_true(by_id[8]["result"]["structuredContent"]["type"] == "desktop_setup_guide", "MCP build_desktop_setup_guide failed")
        assert_true("Long Novel Agent Desktop Setup" in by_id[8]["result"]["content"][0]["text"], "MCP desktop setup markdown failed")
        assert_true(by_id[9]["result"]["structuredContent"]["type"] == "desktop_setup_verification", "MCP verify_desktop_setup failed")
        assert_true("Long Novel Agent Desktop Verification" in by_id[9]["result"]["content"][0]["text"], "MCP desktop verification markdown failed")
        assert_true(by_id[43]["result"]["structuredContent"]["type"] == "desktop_next_step", "MCP build_desktop_next_step failed")
        assert_true(by_id[43]["result"]["structuredContent"]["tool_profile"] == "full", "MCP build_desktop_next_step missed default tool profile")
        assert_true(by_id[43]["result"]["structuredContent"]["primary_action"]["id"], "MCP build_desktop_next_step missed primary action")
        assert_true(by_id[63]["result"]["structuredContent"]["type"] == "desktop_diagnostics", "MCP build_desktop_diagnostics failed")
        assert_true(by_id[63]["result"]["structuredContent"]["would_write_project_state"] is False, "MCP build_desktop_diagnostics should be read-only")
        assert_true("Long Novel Agent Desktop Diagnostics" in by_id[63]["result"]["content"][0]["text"], "MCP build_desktop_diagnostics markdown failed")
        assert_true("Long Novel Agent Desktop Next Step" in by_id[43]["result"]["content"][0]["text"], "MCP desktop next step markdown failed")
        assert_true("MCP tool profile: full" in by_id[43]["result"]["content"][0]["text"], "MCP desktop next step markdown missed tool profile")
        assert_true(by_id[55]["result"]["structuredContent"]["type"] == "tool_visibility_explanation", "MCP explain_tool_visibility failed")
        assert_true("read_only_mode" in by_id[55]["result"]["structuredContent"]["blocked_by"], "MCP explain_tool_visibility missed read-only blocker")
        assert_true("MCP Tool Visibility" in by_id[55]["result"]["content"][0]["text"], "MCP explain_tool_visibility markdown failed")
        assert_true(by_id[44]["result"]["structuredContent"]["type"] == "desktop_evidence_session", "MCP build_desktop_evidence_session failed")
        assert_true(by_id[58]["result"]["structuredContent"]["type"] == "desktop_evidence_runbook", "MCP build_desktop_evidence_runbook failed")
        assert_true("Desktop Evidence Runbook" in by_id[58]["result"]["content"][0]["text"], "MCP build_desktop_evidence_runbook markdown failed")
        assert_true(by_id[59]["result"]["structuredContent"]["type"] == "desktop_user_steps", "MCP build_desktop_user_steps failed")
        assert_true(by_id[59]["result"]["structuredContent"]["step_count"] == 5, "MCP build_desktop_user_steps step count mismatch")
        assert_true("User Steps" in by_id[59]["result"]["content"][0]["text"], "MCP build_desktop_user_steps markdown failed")
        assert_true(by_id[60]["result"]["structuredContent"]["type"] == "desktop_user_steps", "MCP build_desktop_user_steps HTML failed")
        assert_true("<!doctype html>" in by_id[60]["result"]["content"][0]["text"], "MCP build_desktop_user_steps HTML text failed")
        assert_true(by_id[44]["result"]["structuredContent"]["would_write_project_state"] is False, "MCP desktop evidence session should be read-only")
        assert_true("Long Novel Agent Desktop Evidence Session" in by_id[44]["result"]["content"][0]["text"], "MCP desktop evidence session markdown failed")
        assert_true(by_id[48]["result"]["structuredContent"]["type"] == "desktop_pack_schema_check", "MCP build_desktop_pack_schema_check failed")
        assert_true(by_id[48]["result"]["structuredContent"]["ok"] is True, "MCP build_desktop_pack_schema_check should pass generated pack")
        assert_true(by_id[48]["result"]["structuredContent"]["would_write_pack_files"] is False, "MCP build_desktop_pack_schema_check should not write pack files")
        assert_true("Desktop Pack Schema Check" in by_id[48]["result"]["content"][0]["text"], "MCP desktop pack schema check markdown failed")
        assert_true(by_id[53]["result"]["structuredContent"]["type"] == "desktop_pack_freshness", "MCP build_desktop_pack_freshness failed")
        assert_true(by_id[53]["result"]["structuredContent"]["status"] == "fresh", "MCP build_desktop_pack_freshness should report fresh pack")
        assert_true(by_id[53]["result"]["structuredContent"]["would_write_project_state"] is False, "MCP build_desktop_pack_freshness should not write state")
        assert_true("Desktop Pack Freshness" in by_id[53]["result"]["content"][0]["text"], "MCP desktop pack freshness markdown failed")
        assert_true(by_id[54]["result"]["structuredContent"]["type"] == "chapter_session_freshness", "MCP build_chapter_session_freshness failed")
        assert_true(by_id[54]["result"]["structuredContent"]["status"] == "fresh", "MCP build_chapter_session_freshness should report fresh chapter session")
        assert_true(by_id[54]["result"]["structuredContent"]["would_write_project_state"] is False, "MCP build_chapter_session_freshness should not write state")
        assert_true("Chapter Session Freshness" in by_id[54]["result"]["content"][0]["text"], "MCP chapter session freshness markdown failed")
        assert_true(by_id[10]["result"]["structuredContent"]["type"] == "desktop_client_checklist", "MCP build_desktop_client_checklist failed")
        assert_true("Long Novel Agent Desktop Client Checklist" in by_id[10]["result"]["content"][0]["text"], "MCP desktop checklist markdown failed")
        assert_true(by_id[28]["result"]["structuredContent"]["type"] == "desktop_results_doctor", "MCP build_desktop_results_doctor failed")
        assert_true(by_id[28]["result"]["structuredContent"]["status"] == "ready_to_record", "MCP desktop results doctor should be ready")
        assert_true("Long Novel Agent Desktop Results Doctor" in by_id[28]["result"]["content"][0]["text"], "MCP desktop results doctor markdown failed")
        assert_true(by_id[30]["result"]["structuredContent"]["type"] == "desktop_evidence_packet", "MCP build_desktop_evidence_packet failed")
        assert_true(by_id[30]["result"]["structuredContent"]["status"] == "ready_to_record", "MCP desktop evidence packet should be ready")
        assert_true("Long Novel Agent Desktop Evidence Packet" in by_id[30]["result"]["content"][0]["text"], "MCP desktop evidence packet markdown failed")
        assert_true(by_id[45]["result"]["structuredContent"]["type"] == "desktop_evidence_review", "MCP build_desktop_evidence_review failed")
        assert_true(by_id[45]["result"]["structuredContent"]["status"] == "ready_to_record", "MCP desktop evidence review should be ready")
        assert_true("Long Novel Agent Desktop Evidence Review" in by_id[45]["result"]["content"][0]["text"], "MCP desktop evidence review markdown failed")
        assert_true(by_id[61]["result"]["structuredContent"]["type"] == "desktop_evidence_review", "MCP build_desktop_evidence_review HTML failed")
        assert_true("<!doctype html>" in by_id[61]["result"]["content"][0]["text"], "MCP desktop evidence review HTML text failed")
        assert_true("record_desktop_check" in by_id[61]["result"]["content"][0]["text"], "MCP desktop evidence review HTML missed record command")
        assert_true(by_id[56]["result"]["structuredContent"]["type"] == "desktop_evidence_gaps", "MCP build_desktop_evidence_gaps failed")
        assert_true(by_id[56]["result"]["structuredContent"]["status"] == "ready_to_record", "MCP desktop evidence gaps should be ready")
        assert_true("Long Novel Agent Desktop Evidence Gaps" in by_id[56]["result"]["content"][0]["text"], "MCP desktop evidence gaps markdown failed")
        assert_true(by_id[29]["result"]["structuredContent"]["type"] == "writer_mode_readiness", "MCP build_writer_mode_readiness failed")
        assert_true(by_id[29]["result"]["structuredContent"]["status"] in {"ready_to_enable_writer", "ready_with_warnings", "needs_review"}, "MCP writer mode readiness returned unexpected status")
        assert_true("Long Novel Agent Writer Mode Readiness" in by_id[29]["result"]["content"][0]["text"], "MCP writer mode readiness markdown failed")
        assert_true(by_id[39]["result"]["structuredContent"]["type"] == "author_review_queue", "MCP build_author_review_queue failed")
        assert_true(by_id[39]["result"]["structuredContent"]["read_only"] is True, "MCP author review queue should be read-only")
        assert_true("Long Novel Agent Author Review Queue" in by_id[39]["result"]["content"][0]["text"], "MCP author review queue markdown failed")
        assert_true(by_id[32]["result"]["structuredContent"]["type"] == "desktop_evidence_sanitizer", "MCP sanitize_desktop_evidence failed")
        assert_true(by_id[32]["result"]["structuredContent"]["source_summary"]["redaction_applied"], "MCP sanitize_desktop_evidence missed redaction")
        assert_true("sk-mcptestsecret" not in by_id[32]["result"]["structuredContent"]["sanitized_text"], "MCP sanitize_desktop_evidence leaked secret")
        assert_true("Long Novel Agent Desktop Evidence Sanitizer" in by_id[32]["result"]["content"][0]["text"], "MCP desktop evidence sanitizer markdown failed")
        assert_true(by_id[11]["result"]["structuredContent"]["validation"]["ok"], "MCP desktop check record failed")
        assert_true(len(by_id[12]["result"]["structuredContent"]["records"]) == 1, "MCP desktop check list failed")
        assert_true(by_id[13]["result"]["structuredContent"]["type"] == "project_status", "MCP get_project_status failed")
        assert_true(by_id[13]["result"]["structuredContent"]["desktop_verification_matrix"]["type"] == "desktop_verification_matrix", "MCP get_project_status missed desktop verification matrix")
        assert_true("Long Novel Agent Project Status" in by_id[13]["result"]["content"][0]["text"], "MCP project status markdown failed")
        assert_true("Desktop Verification Matrix" in by_id[13]["result"]["content"][0]["text"], "MCP project status markdown missed desktop verification matrix")
        assert_true(by_id[14]["result"]["structuredContent"]["agent"]["id"] == "codex-local", "MCP declare_agent_capabilities failed")
        assert_true(by_id[15]["result"]["structuredContent"]["type"] == "handoff_readiness", "MCP verify_handoff_readiness failed")
        assert_true("Long Novel Agent Handoff Readiness" in by_id[15]["result"]["content"][0]["text"], "MCP handoff readiness markdown failed")
        assert_true(by_id[16]["result"]["structuredContent"]["type"] == "desktop_evidence_import", "MCP ingest_desktop_client_evidence failed")
        assert_true(by_id[16]["result"]["structuredContent"]["ok"], "MCP ingest_desktop_client_evidence should find required checks")
        assert_true("Long Novel Agent Desktop Evidence Import" in by_id[16]["result"]["content"][0]["text"], "MCP desktop evidence import markdown failed")
        assert_true(by_id[17]["result"]["structuredContent"]["type"] == "desktop_onboarding_guide", "MCP build_desktop_onboarding_guide failed")
        assert_true("Long Novel Agent Desktop Onboarding" in by_id[17]["result"]["content"][0]["text"], "MCP desktop onboarding markdown failed")
        assert_true(by_id[18]["result"]["structuredContent"]["type"] == "agent_activity_record", "MCP record_agent_activity failed")
        assert_true("Long Novel Agent Activity" in by_id[18]["result"]["content"][0]["text"], "MCP record_agent_activity markdown failed")
        assert_true(by_id[34]["result"]["structuredContent"]["type"] == "agent_activity_report", "MCP build_agent_activity_report failed")
        assert_true(by_id[34]["result"]["structuredContent"]["status"] in {"ready", "ready_with_warnings"}, "MCP build_agent_activity_report should pass recorded activity")
        assert_true("Long Novel Agent Activity Report" in by_id[34]["result"]["content"][0]["text"], "MCP build_agent_activity_report markdown failed")
        assert_true(by_id[35]["result"]["structuredContent"]["type"] == "handoff_integrity_report", "MCP build_handoff_integrity_report failed")
        assert_true(by_id[35]["result"]["structuredContent"]["status"] in {"ready", "ready_with_warnings", "needs_review"}, "MCP build_handoff_integrity_report returned unexpected status")
        assert_true("Long Novel Agent Handoff Integrity" in by_id[35]["result"]["content"][0]["text"], "MCP build_handoff_integrity_report markdown failed")
        assert_true(by_id[36]["result"]["structuredContent"]["type"] == "handoff_range_report", "MCP build_handoff_range_report failed")
        assert_true(by_id[36]["result"]["structuredContent"]["status"] in {"ready", "ready_with_warnings", "needs_review", "blocked"}, "MCP build_handoff_range_report returned unexpected status")
        assert_true("Long Novel Agent Handoff Range Report" in by_id[36]["result"]["content"][0]["text"], "MCP build_handoff_range_report markdown failed")
        assert_true(by_id[37]["result"]["structuredContent"]["type"] == "chapter_range_delivery_packet", "MCP build_chapter_range_delivery_packet failed")
        assert_true(by_id[37]["result"]["structuredContent"]["read_only"] is True, "MCP chapter range delivery should be read-only")
        assert_true(by_id[37]["result"]["structuredContent"]["ready_to_present"], "MCP chapter range delivery should allow clean chapter")
        assert_true("Long Novel Agent Chapter Range Delivery" in by_id[37]["result"]["content"][0]["text"], "MCP chapter range delivery markdown failed")
        assert_true(by_id[38]["result"]["structuredContent"]["type"] == "chapter_range_acceptance_plan", "MCP build_chapter_range_acceptance_plan failed")
        assert_true(by_id[38]["result"]["structuredContent"]["read_only"] is True, "MCP chapter range acceptance plan should be read-only")
        assert_true(by_id[38]["result"]["structuredContent"]["author_confirmation_present"] is True, "MCP chapter range acceptance plan missed author confirmation")
        assert_true("Long Novel Agent Chapter Range Acceptance Plan" in by_id[38]["result"]["content"][0]["text"], "MCP chapter range acceptance markdown failed")
        assert_true(len(by_id[19]["result"]["structuredContent"]["records"]) == 1, "MCP list_agent_activity failed")
        assert_true("Long Novel Agent Activity" in by_id[19]["result"]["content"][0]["text"], "MCP list_agent_activity markdown failed")
        assert_true(by_id[20]["result"]["structuredContent"]["type"] == "source_intake_report", "MCP validate_source_intake failed")
        assert_true(by_id[20]["result"]["structuredContent"]["validation"]["planned"]["sources"] == 1, "MCP validate_source_intake missed source count")
        assert_true(by_id[20]["result"]["structuredContent"]["validation"]["quality"]["level"] in {"ready", "review"}, "MCP validate_source_intake missed quality level")
        assert_true("Long Novel Agent Source Intake Report" in by_id[20]["result"]["content"][0]["text"], "MCP validate_source_intake markdown failed")
        assert_true(by_id[21]["result"]["structuredContent"]["type"] == "source_intake_template", "MCP build_source_intake_template failed")
        assert_true(by_id[21]["result"]["structuredContent"]["template"]["type"] == "source_intake", "MCP build_source_intake_template missed template")
        assert_true("Long Novel Agent Source Intake Template" in by_id[21]["result"]["content"][0]["text"], "MCP build_source_intake_template markdown failed")
        assert_true(by_id[22]["result"]["structuredContent"]["type"] == "desktop_verification_matrix", "MCP get_desktop_verification_matrix failed")
        mcp_matrix_cells = by_id[22]["result"]["structuredContent"].get("matrix")
        assert_true(isinstance(mcp_matrix_cells, list) and mcp_matrix_cells, "MCP desktop verification matrix missed cells")
        assert_true(isinstance(mcp_matrix_cells[0].get("coverage"), dict), "MCP desktop verification matrix missed coverage")
        assert_true("required_checks_total" in by_id[22]["result"]["structuredContent"].get("totals", {}), "MCP desktop verification matrix missed required coverage totals")
        assert_true("Long Novel Agent Desktop Verification Matrix" in by_id[22]["result"]["content"][0]["text"], "MCP desktop verification matrix markdown failed")
        assert_true(by_id[23]["result"]["structuredContent"]["type"] == "proposal_review", "MCP build_proposal_review failed")
        assert_true(by_id[23]["result"]["structuredContent"]["ready_to_apply"], "MCP build_proposal_review should mark valid proposal ready")
        assert_true("Long Novel Agent Proposal Review" in by_id[23]["result"]["content"][0]["text"], "MCP proposal review markdown failed")
        assert_true(by_id[27]["result"]["structuredContent"]["type"] == "proposal_readiness", "MCP build_proposal_readiness failed")
        assert_true(by_id[27]["result"]["structuredContent"]["ready_to_apply"], "MCP build_proposal_readiness should mark valid proposal ready")
        assert_true("Long Novel Agent Proposal Readiness" in by_id[27]["result"]["content"][0]["text"], "MCP proposal readiness markdown failed")
        assert_true(by_id[24]["result"]["structuredContent"]["type"] == "continuity_audit", "MCP build_continuity_audit failed")
        assert_true(by_id[25]["result"]["structuredContent"]["type"] == "chapter_readiness", "MCP build_chapter_readiness failed")
        assert_true(by_id[25]["result"]["structuredContent"]["ready_to_present"], "MCP build_chapter_readiness should allow clean chapter")
        assert_true("Long Novel Agent Chapter Readiness" in by_id[25]["result"]["content"][0]["text"], "MCP chapter readiness markdown failed")
        assert_true(by_id[40]["result"]["structuredContent"]["type"] == "chapter_revision_prompt", "MCP build_chapter_revision_prompt failed")
        assert_true(by_id[40]["result"]["structuredContent"]["read_only"] is True, "MCP chapter revision prompt should be read-only")
        assert_true("Long Novel Agent Chapter Revision Prompt" in by_id[40]["result"]["content"][0]["text"], "MCP chapter revision prompt markdown failed")
        assert_true(by_id[41]["result"]["structuredContent"]["type"] == "chapter_revision_comparison", "MCP build_chapter_revision_comparison failed")
        assert_true(by_id[41]["result"]["structuredContent"]["read_only"] is True, "MCP chapter revision comparison should be read-only")
        assert_true("Long Novel Agent Chapter Revision Comparison" in by_id[41]["result"]["content"][0]["text"], "MCP chapter revision comparison markdown failed")
        assert_true(by_id[33]["result"]["structuredContent"]["type"] == "chapter_delivery_packet", "MCP build_chapter_delivery_packet failed")
        assert_true(by_id[33]["result"]["structuredContent"]["read_only"] is True, "MCP chapter delivery should be read-only")
        assert_true(by_id[33]["result"]["structuredContent"]["ready_to_present"], "MCP chapter delivery should allow clean chapter")
        assert_true("Long Novel Agent Chapter Delivery" in by_id[33]["result"]["content"][0]["text"], "MCP chapter delivery markdown failed")
        assert_true(by_id[42]["result"]["structuredContent"]["type"] == "chapter_acceptance_plan", "MCP build_chapter_acceptance_plan failed")
        assert_true(by_id[42]["result"]["structuredContent"]["read_only"] is True, "MCP chapter acceptance plan should be read-only")
        assert_true("Long Novel Agent Chapter Acceptance Plan" in by_id[42]["result"]["content"][0]["text"], "MCP chapter acceptance plan markdown failed")
        assert_true(by_id[31]["result"]["structuredContent"]["type"] == "chapter_range_readiness", "MCP build_chapter_range_readiness failed")
        assert_true(by_id[31]["result"]["structuredContent"]["status"] in {"ready", "ready_with_notes", "needs_files"}, "MCP chapter range readiness returned unexpected status")
        assert_true("Long Novel Agent Chapter Range Readiness" in by_id[31]["result"]["content"][0]["text"], "MCP chapter range readiness markdown failed")
        assert_true(by_id[26]["result"]["structuredContent"]["type"] == "gaoxia_import_audit", "MCP audit_gaoxia_import failed")
        assert_true(by_id[26]["result"]["structuredContent"]["status"] == "not_gaoxia_project", "MCP audit_gaoxia_import should report non-Gaoxia project")
        assert_true("Long Novel Agent Gaoxia Import Audit" in by_id[26]["result"]["content"][0]["text"], "MCP audit gaoxia import markdown failed")
        assert_true("action_plan" in by_id[24]["result"]["structuredContent"], "MCP build_continuity_audit missed action plan")
        assert_true("Long Novel Agent Continuity Audit" in by_id[24]["result"]["content"][0]["text"], "MCP continuity audit markdown failed")
        assert_true("## Action Plan" in by_id[24]["result"]["content"][0]["text"], "MCP continuity audit markdown missed action plan")
        return {"tool_count": len(names)}


def verify_mcp_read_only() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-mcp-readonly-") as tmp:
        project = Path(tmp) / "novel"
        project.mkdir()
        run_cli("init", str(project), "--title", "Read Only MCP")
        chapter = project / "chapters" / "001.md"
        chapter.parent.mkdir(exist_ok=True)
        chapter.write_text("第一章确认码头钟声延迟。", encoding="utf-8")
        proposal_updates = {
            "facts": [
                {
                    "kind": "event",
                    "subject": "第 1 章",
                    "predicate": "result",
                    "object": "码头钟声延迟",
                    "chapter_range": "1+",
                    "evidence": "码头钟声延迟",
                }
            ],
            "handoff": "第二章追查码头钟声延迟。",
        }
        proposal = load_stdout(
            run_cli(
                "propose-after-write",
                str(project),
                "--chapter",
                "1",
                "--file",
                str(chapter),
                "--updates",
                json.dumps(proposal_updates, ensure_ascii=False),
            )
        )
        proposal_id = proposal["proposal"]["id"]
        intake_path = project / "source-intake.json"
        intake_path.write_text(
            json.dumps(
                {
                    "version": "test",
                    "type": "source_intake",
                    "project_dir": str(project),
                    "sources": [
                        {
                            "enabled": True,
                            "title": "Read-only source",
                            "source_path": "materials/readonly.txt",
                            "summary": "只读 MCP 校验确认的资料摘要。",
                            "chapter_range": "1",
                        }
                    ],
                    "research": [],
                    "conflicts": [],
                    "facts": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        desktop_results = {
            "checks": [
                {"id": "local_config_preflight", "status": "passed", "evidence": "local config verified"},
                {"id": "client_restart", "status": "passed", "evidence": "desktop client restarted"},
                {"id": "server_visible", "status": "passed", "evidence": "long-novel-agent visible in client"},
                {"id": "read_path_prepare_session", "status": "passed", "evidence": "prepare_chapter_session returned a session"},
                {"id": "context_gate", "status": "passed", "evidence": "build_chapter_context returned chapter-safe context"},
                {"id": "ready_check_gate", "status": "passed", "evidence": "build_chapter_readiness was available before delivery"},
                {"id": "range_readiness_gate", "status": "passed", "evidence": "build_chapter_range_readiness returned chapter_range_readiness status=needs_files"},
                {"id": "write_tools_hidden", "status": "passed", "evidence": "write tools hidden in read-only client"},
            ]
        }
        read_only_pack_dir = Path(tmp) / "readonly-pack"
        run_cli(
            "desktop-pack",
            str(project),
            "--platform",
            "codex",
            "--mode",
            "read-only",
            "--chapter",
            "1",
            "--output-dir",
            str(read_only_pack_dir),
            "--format",
            "json",
        )
        read_only_session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "1", "--platform", "codex", "--mode", "read-only"))
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "init_project", "arguments": {"project_dir": "/tmp/blocked"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "validate_source_intake", "arguments": {"project_dir": str(project), "intake": str(intake_path), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "build_source_intake_template", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "build_continuity_audit", "arguments": {"project_dir": str(project), "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "build_chapter_readiness", "arguments": {"project_dir": str(project), "chapter": 1, "file": str(chapter), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 46, "method": "tools/call", "params": {"name": "build_chapter_context_brief", "arguments": {"project_dir": str(project), "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 47, "method": "tools/call", "params": {"name": "build_agent_startup_prompt", "arguments": {"project_dir": str(project), "chapter": 1, "platform": "codex", "mode": "read-only", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 62, "method": "tools/call", "params": {"name": "check_write_session", "arguments": {"project_dir": str(project), "chapter": 1, "expected_project_id": read_only_session["project_identity"]["project_id"], "expected_state_sha256": read_only_session["project_state_fingerprint"]["sha256"], "expected_context_hash": read_only_session["chapter_context_fingerprint"]["sha256"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 22, "method": "tools/call", "params": {"name": "build_chapter_revision_prompt", "arguments": {"project_dir": str(project), "chapter": 1, "file": str(chapter), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 23, "method": "tools/call", "params": {"name": "build_chapter_revision_comparison", "arguments": {"project_dir": str(project), "chapter": 1, "before_file": str(chapter), "after_file": str(chapter), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "build_chapter_delivery_packet", "arguments": {"project_dir": str(project), "chapter": 1, "file": str(chapter), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 24, "method": "tools/call", "params": {"name": "build_chapter_acceptance_plan", "arguments": {"project_dir": str(project), "chapter": 1, "file": str(chapter), "handoff_chapter": 2, "author_confirmation": "Author accepted chapter 1 for read-only MCP verification.", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "audit_gaoxia_import", "arguments": {"project_dir": str(project), "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "build_proposal_readiness", "arguments": {"project_dir": str(project), "proposal_id": proposal_id, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "build_desktop_results_doctor", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "results": desktop_results, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "build_writer_mode_readiness", "arguments": {"project_dir": str(project), "platform": "codex", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 25, "method": "tools/call", "params": {"name": "build_desktop_next_step", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 63, "method": "tools/call", "params": {"name": "build_desktop_diagnostics", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 55, "method": "tools/call", "params": {"name": "explain_tool_visibility", "arguments": {"tool_name": "record_chapter", "mode": "read-only", "tool_profile": "core", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 44, "method": "tools/call", "params": {"name": "build_desktop_evidence_session", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 58, "method": "tools/call", "params": {"name": "build_desktop_evidence_runbook", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 59, "method": "tools/call", "params": {"name": "build_desktop_user_steps", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 60, "method": "tools/call", "params": {"name": "build_desktop_user_steps", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "html"}}},
            {"jsonrpc": "2.0", "id": 45, "method": "tools/call", "params": {"name": "build_author_actions", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 64, "method": "tools/call", "params": {"name": "build_acceptance_review", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 50, "method": "tools/call", "params": {"name": "build_desktop_pack_readiness", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 51, "method": "tools/call", "params": {"name": "build_starter_brief", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 52, "method": "tools/call", "params": {"name": "build_desktop_pack_schema_check", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 53, "method": "tools/call", "params": {"name": "build_desktop_pack_freshness", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 54, "method": "tools/call", "params": {"name": "build_chapter_session_freshness", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 48, "method": "tools/call", "params": {"name": "build_desktop_troubleshooting", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 49, "method": "tools/call", "params": {"name": "build_agent_takeover", "arguments": {"pack_dir": str(read_only_pack_dir), "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "build_author_review_queue", "arguments": {"project_dir": str(project), "chapter": 1, "platform": "codex", "mode": "read-only", "start_chapter": 1, "end_chapter": 2, "handoff_chapter": 3, "draft_dir": "chapters", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "build_desktop_evidence_packet", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 26, "method": "tools/call", "params": {"name": "build_desktop_evidence_review", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 57, "method": "tools/call", "params": {"name": "build_desktop_evidence_gaps", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "build_chapter_range_readiness", "arguments": {"project_dir": str(project), "start_chapter": 1, "end_chapter": 2, "draft_dir": "chapters", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 14, "method": "tools/call", "params": {"name": "sanitize_desktop_evidence", "arguments": {"project_dir": str(project), "evidence_text": "prepare_chapter_session returned chapter_session_packet\nBearer abcdefghijklmnopqrstuvwxyz", "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {"name": "build_agent_activity_report", "arguments": {"project_dir": str(project), "chapter": 1, "required_actions": ["prepare-session"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": "build_handoff_integrity_report", "arguments": {"project_dir": str(project), "chapter": 1, "required_actions": ["prepare-session"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 18, "method": "tools/call", "params": {"name": "build_handoff_range_report", "arguments": {"project_dir": str(project), "start_chapter": 1, "end_chapter": 2, "handoff_chapter": 3, "draft_dir": "chapters", "required_actions": ["prepare-session"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 19, "method": "tools/call", "params": {"name": "build_chapter_range_delivery_packet", "arguments": {"project_dir": str(project), "start_chapter": 1, "end_chapter": 2, "handoff_chapter": 3, "draft_dir": "chapters", "required_actions": ["prepare-session"], "format": "markdown"}}},
            {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "build_chapter_range_acceptance_plan", "arguments": {"project_dir": str(project), "start_chapter": 1, "end_chapter": 2, "handoff_chapter": 3, "draft_dir": "chapters", "required_actions": ["prepare-session"], "author_confirmation": "Author accepted chapters 1-2 for read-only MCP verification.", "format": "markdown"}}},
        ]
        payload = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
        proc = subprocess.run([sys.executable, str(SERVER), "--read-only"], input=payload, text=True, capture_output=True, check=True)
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        by_id = {reply.get("id"): reply for reply in replies}
        names = {tool["name"] for tool in by_id[2]["result"]["tools"]}
        assert_true(
            "build_chapter_context" in names
            and "build_chapter_context_brief" in names
            and "build_agent_startup_prompt" in names
            and "prepare_chapter_session" in names
            and "check_write_session" in names
            and "check_chapter" in names
            and "build_handoff_report" in names
            and "get_agent_template" in names
            and "explain_tool_visibility" in names
            and "build_desktop_setup_guide" in names
            and "build_desktop_onboarding_guide" in names
            and "verify_desktop_setup" in names
            and "build_desktop_client_checklist" in names
            and "build_source_intake_template" in names
            and "validate_source_intake" in names
            and "ingest_desktop_client_evidence" in names
            and "sanitize_desktop_evidence" in names
            and "build_desktop_results_doctor" in names
            and "build_desktop_evidence_packet" in names
            and "build_desktop_evidence_review" in names
            and "build_desktop_evidence_gaps" in names
            and "build_desktop_next_step" in names
            and "build_desktop_diagnostics" in names
            and "build_desktop_evidence_session" in names
            and "build_desktop_evidence_runbook" in names
            and "build_desktop_user_steps" in names
            and "build_author_actions" in names
            and "build_acceptance_review" in names
            and "build_desktop_pack_readiness" in names
            and "build_desktop_pack_freshness" in names
            and "build_chapter_session_freshness" in names
            and "build_desktop_pack_schema_check" in names
            and "build_starter_brief" in names
            and "build_agent_takeover" in names
            and "build_desktop_troubleshooting" in names
            and "build_writer_mode_readiness" in names
            and "build_author_review_queue" in names
            and "list_desktop_client_checks" in names
            and "get_desktop_verification_matrix" in names
            and "list_agent_activity" in names
            and "build_agent_activity_report" in names
            and "build_handoff_integrity_report" in names
            and "build_handoff_range_report" in names
            and "get_project_status" in names
            and "build_continuity_audit" in names
            and "audit_gaoxia_import" in names
            and "build_chapter_delivery_packet" in names
            and "build_chapter_readiness" in names
            and "build_chapter_revision_prompt" in names
            and "build_chapter_revision_comparison" in names
            and "build_chapter_acceptance_plan" in names
            and "build_chapter_range_readiness" in names
            and "build_chapter_range_delivery_packet" in names
            and "build_chapter_range_acceptance_plan" in names
            and "verify_handoff_readiness" in names
            and "build_proposal_readiness" in names
            and "build_proposal_review" in names,
            "read-only tools/list missed read tools",
        )
        assert_true("init_project" not in names and "init_wizard" not in names and "quickstart_project" not in names and "add_source_summary" not in names and "record_desktop_client_check" not in names and "record_agent_activity" not in names, "read-only tools/list exposed write tools")
        assert_true("error" in by_id[3], "read-only server did not reject direct write call")
        assert_true(by_id[4]["result"]["structuredContent"]["type"] == "source_intake_report", "read-only validate_source_intake failed")
        assert_true(by_id[4]["result"]["structuredContent"]["validation"]["planned"]["sources"] == 1, "read-only validate_source_intake missed source count")
        assert_true(by_id[4]["result"]["structuredContent"]["validation"]["quality"]["level"] in {"ready", "review"}, "read-only validate_source_intake missed quality level")
        assert_true("Long Novel Agent Source Intake Report" in by_id[4]["result"]["content"][0]["text"], "read-only validate_source_intake markdown failed")
        assert_true(by_id[5]["result"]["structuredContent"]["type"] == "source_intake_template", "read-only build_source_intake_template failed")
        assert_true(by_id[5]["result"]["structuredContent"]["template"]["type"] == "source_intake", "read-only build_source_intake_template missed template")
        assert_true("Long Novel Agent Source Intake Template" in by_id[5]["result"]["content"][0]["text"], "read-only build_source_intake_template markdown failed")
        assert_true(by_id[6]["result"]["structuredContent"]["type"] == "continuity_audit", "read-only build_continuity_audit failed")
        assert_true("action_plan" in by_id[6]["result"]["structuredContent"], "read-only continuity audit missed action plan")
        assert_true("Long Novel Agent Continuity Audit" in by_id[6]["result"]["content"][0]["text"], "read-only continuity audit markdown failed")
        assert_true("## Action Plan" in by_id[6]["result"]["content"][0]["text"], "read-only continuity audit markdown missed action plan")
        assert_true(by_id[7]["result"]["structuredContent"]["type"] == "chapter_readiness", "read-only build_chapter_readiness failed")
        assert_true(by_id[7]["result"]["structuredContent"]["ready_to_present"], "read-only build_chapter_readiness should allow simple chapter")
        assert_true("Long Novel Agent Chapter Readiness" in by_id[7]["result"]["content"][0]["text"], "read-only chapter readiness markdown failed")
        assert_true(by_id[46]["result"]["structuredContent"]["type"] == "chapter_context_brief", "read-only build_chapter_context_brief failed")
        assert_true("characters" not in by_id[46]["result"]["structuredContent"], "read-only context brief should not return full character table")
        assert_true("Chapter 1 Context Brief" in by_id[46]["result"]["content"][0]["text"], "read-only context brief markdown failed")
        assert_true(by_id[47]["result"]["structuredContent"]["type"] == "agent_startup_prompt", "read-only build_agent_startup_prompt failed")
        assert_true(by_id[47]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_agent_startup_prompt should not write state")
        assert_true("Agent Startup Prompt" in by_id[47]["result"]["content"][0]["text"], "read-only build_agent_startup_prompt markdown failed")
        assert_true(by_id[62]["result"]["structuredContent"]["type"] == "write_session_check", "read-only check_write_session failed")
        assert_true(by_id[62]["result"]["structuredContent"]["status"] == "ready", "read-only check_write_session should pass fresh values")
        assert_true("Write Session Check" in by_id[62]["result"]["content"][0]["text"], "read-only check_write_session markdown failed")
        assert_true(by_id[22]["result"]["structuredContent"]["type"] == "chapter_revision_prompt", "read-only build_chapter_revision_prompt failed")
        assert_true(by_id[22]["result"]["structuredContent"]["read_only"] is True, "read-only chapter revision prompt should be read-only")
        assert_true("Long Novel Agent Chapter Revision Prompt" in by_id[22]["result"]["content"][0]["text"], "read-only chapter revision prompt markdown failed")
        assert_true(by_id[23]["result"]["structuredContent"]["type"] == "chapter_revision_comparison", "read-only build_chapter_revision_comparison failed")
        assert_true(by_id[23]["result"]["structuredContent"]["read_only"] is True, "read-only chapter revision comparison should be read-only")
        assert_true("Long Novel Agent Chapter Revision Comparison" in by_id[23]["result"]["content"][0]["text"], "read-only chapter revision comparison markdown failed")
        assert_true(by_id[15]["result"]["structuredContent"]["type"] == "chapter_delivery_packet", "read-only build_chapter_delivery_packet failed")
        assert_true(by_id[15]["result"]["structuredContent"]["read_only"] is True, "read-only chapter delivery should be read-only")
        assert_true(by_id[15]["result"]["structuredContent"]["ready_to_present"], "read-only chapter delivery should allow simple chapter")
        assert_true("Long Novel Agent Chapter Delivery" in by_id[15]["result"]["content"][0]["text"], "read-only chapter delivery markdown failed")
        assert_true(by_id[24]["result"]["structuredContent"]["type"] == "chapter_acceptance_plan", "read-only build_chapter_acceptance_plan failed")
        assert_true(by_id[24]["result"]["structuredContent"]["read_only"] is True, "read-only chapter acceptance plan should be read-only")
        assert_true("Long Novel Agent Chapter Acceptance Plan" in by_id[24]["result"]["content"][0]["text"], "read-only chapter acceptance plan markdown failed")
        assert_true(by_id[8]["result"]["structuredContent"]["type"] == "gaoxia_import_audit", "read-only audit_gaoxia_import failed")
        assert_true("Long Novel Agent Gaoxia Import Audit" in by_id[8]["result"]["content"][0]["text"], "read-only audit gaoxia import markdown failed")
        assert_true(by_id[9]["result"]["structuredContent"]["type"] == "proposal_readiness", "read-only build_proposal_readiness failed")
        assert_true(by_id[9]["result"]["structuredContent"]["ready_to_apply"], "read-only build_proposal_readiness should mark valid proposal ready")
        assert_true("Long Novel Agent Proposal Readiness" in by_id[9]["result"]["content"][0]["text"], "read-only proposal readiness markdown failed")
        assert_true(by_id[10]["result"]["structuredContent"]["type"] == "desktop_results_doctor", "read-only build_desktop_results_doctor failed")
        assert_true(by_id[10]["result"]["structuredContent"]["status"] == "ready_to_record", "read-only desktop results doctor should be ready")
        assert_true("Long Novel Agent Desktop Results Doctor" in by_id[10]["result"]["content"][0]["text"], "read-only desktop results doctor markdown failed")
        assert_true(by_id[11]["result"]["structuredContent"]["type"] == "writer_mode_readiness", "read-only build_writer_mode_readiness failed")
        assert_true(by_id[11]["result"]["structuredContent"]["status"] == "blocked", "read-only writer mode readiness should block without real client proof")
        assert_true("Long Novel Agent Writer Mode Readiness" in by_id[11]["result"]["content"][0]["text"], "read-only writer mode readiness markdown failed")
        assert_true(by_id[25]["result"]["structuredContent"]["type"] == "desktop_next_step", "read-only build_desktop_next_step failed")
        assert_true(by_id[25]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only desktop next step should not write state")
        assert_true("Long Novel Agent Desktop Next Step" in by_id[25]["result"]["content"][0]["text"], "read-only desktop next step markdown failed")
        assert_true(by_id[63]["result"]["structuredContent"]["type"] == "desktop_diagnostics", "read-only build_desktop_diagnostics failed")
        assert_true(by_id[63]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only desktop diagnostics should not write state")
        assert_true("Long Novel Agent Desktop Diagnostics" in by_id[63]["result"]["content"][0]["text"], "read-only desktop diagnostics markdown failed")
        assert_true(by_id[55]["result"]["structuredContent"]["type"] == "tool_visibility_explanation", "read-only explain_tool_visibility failed")
        assert_true("read_only_mode" in by_id[55]["result"]["structuredContent"]["blocked_by"], "read-only explain_tool_visibility missed blocker")
        assert_true("MCP Tool Visibility" in by_id[55]["result"]["content"][0]["text"], "read-only explain_tool_visibility markdown failed")
        assert_true(by_id[44]["result"]["structuredContent"]["type"] == "desktop_evidence_session", "read-only build_desktop_evidence_session failed")
        assert_true(by_id[58]["result"]["structuredContent"]["type"] == "desktop_evidence_runbook", "read-only build_desktop_evidence_runbook failed")
        assert_true("Desktop Evidence Runbook" in by_id[58]["result"]["content"][0]["text"], "read-only desktop evidence runbook markdown failed")
        assert_true(by_id[59]["result"]["structuredContent"]["type"] == "desktop_user_steps", "read-only build_desktop_user_steps failed")
        assert_true(by_id[59]["result"]["structuredContent"]["step_count"] == 5, "read-only build_desktop_user_steps step count mismatch")
        assert_true("User Steps" in by_id[59]["result"]["content"][0]["text"], "read-only desktop user steps markdown failed")
        assert_true(by_id[60]["result"]["structuredContent"]["type"] == "desktop_user_steps", "read-only build_desktop_user_steps HTML failed")
        assert_true("<!doctype html>" in by_id[60]["result"]["content"][0]["text"], "read-only desktop user steps HTML failed")
        assert_true(by_id[44]["result"]["structuredContent"]["records_real_client_evidence"] is False, "read-only desktop evidence session should not record evidence")
        assert_true("Long Novel Agent Desktop Evidence Session" in by_id[44]["result"]["content"][0]["text"], "read-only desktop evidence session markdown failed")
        assert_true(by_id[45]["result"]["structuredContent"]["type"] == "author_actions", "read-only build_author_actions failed")
        assert_true(by_id[45]["result"]["structuredContent"]["source"] == "desktop_pack_directory", "read-only build_author_actions missed directory source")
        assert_true(by_id[45]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_author_actions should not write state")
        assert_true("Author Actions" in by_id[45]["result"]["content"][0]["text"], "read-only build_author_actions markdown failed")
        assert_true(by_id[64]["result"]["structuredContent"]["type"] == "acceptance_review", "read-only build_acceptance_review failed")
        assert_true(by_id[64]["result"]["structuredContent"]["source"] == "desktop_pack_directory", "read-only build_acceptance_review missed directory source")
        assert_true(by_id[64]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_acceptance_review should not write state")
        assert_true("Acceptance Review" in by_id[64]["result"]["content"][0]["text"], "read-only build_acceptance_review markdown failed")
        assert_true(by_id[50]["result"]["structuredContent"]["type"] == "desktop_pack_readiness", "read-only build_desktop_pack_readiness failed")
        assert_true(by_id[50]["result"]["structuredContent"]["source"] == "desktop_pack_directory", "read-only build_desktop_pack_readiness missed directory source")
        assert_true(by_id[50]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_desktop_pack_readiness should not write state")
        assert_true(by_id[50]["result"]["structuredContent"]["would_write_pack_files"] is False, "read-only build_desktop_pack_readiness should not write pack files")
        assert_true("Desktop Pack Readiness" in by_id[50]["result"]["content"][0]["text"], "read-only build_desktop_pack_readiness markdown failed")
        assert_true(by_id[52]["result"]["structuredContent"]["type"] == "desktop_pack_schema_check", "read-only build_desktop_pack_schema_check failed")
        assert_true(by_id[52]["result"]["structuredContent"]["ok"] is True, "read-only build_desktop_pack_schema_check should pass generated pack")
        assert_true(by_id[52]["result"]["structuredContent"]["would_write_pack_files"] is False, "read-only build_desktop_pack_schema_check should not write pack files")
        assert_true("Desktop Pack Schema Check" in by_id[52]["result"]["content"][0]["text"], "read-only build_desktop_pack_schema_check markdown failed")
        assert_true(by_id[53]["result"]["structuredContent"]["type"] == "desktop_pack_freshness", "read-only build_desktop_pack_freshness failed")
        assert_true(by_id[53]["result"]["structuredContent"]["status"] == "fresh", "read-only build_desktop_pack_freshness should report fresh pack")
        assert_true(by_id[53]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_desktop_pack_freshness should not write state")
        assert_true("Desktop Pack Freshness" in by_id[53]["result"]["content"][0]["text"], "read-only build_desktop_pack_freshness markdown failed")
        assert_true(by_id[54]["result"]["structuredContent"]["type"] == "chapter_session_freshness", "read-only build_chapter_session_freshness failed")
        assert_true(by_id[54]["result"]["structuredContent"]["status"] == "fresh", "read-only build_chapter_session_freshness should report fresh chapter session")
        assert_true(by_id[54]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_chapter_session_freshness should not write state")
        assert_true("Chapter Session Freshness" in by_id[54]["result"]["content"][0]["text"], "read-only build_chapter_session_freshness markdown failed")
        assert_true(by_id[51]["result"]["structuredContent"]["type"] == "starter_brief", "read-only build_starter_brief failed")
        assert_true(by_id[51]["result"]["structuredContent"]["source"] == "desktop_pack_directory", "read-only build_starter_brief missed directory source")
        assert_true(by_id[51]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_starter_brief should not write state")
        assert_true(by_id[51]["result"]["structuredContent"]["would_write_pack_files"] is False, "read-only build_starter_brief should not write pack files")
        assert_true("Starter Brief" in by_id[51]["result"]["content"][0]["text"], "read-only build_starter_brief markdown failed")
        assert_true(by_id[48]["result"]["structuredContent"]["type"] == "desktop_pack_troubleshooting", "read-only build_desktop_troubleshooting failed")
        assert_true(by_id[48]["result"]["structuredContent"]["source"] == "desktop_pack_directory", "read-only build_desktop_troubleshooting missed directory source")
        assert_true(by_id[48]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_desktop_troubleshooting should not write state")
        assert_true("Local Desktop Troubleshooting" in by_id[48]["result"]["content"][0]["text"], "read-only build_desktop_troubleshooting markdown failed")
        assert_true(by_id[49]["result"]["structuredContent"]["type"] == "agent_takeover", "read-only build_agent_takeover failed")
        assert_true(by_id[49]["result"]["structuredContent"]["source"] == "desktop_pack_directory", "read-only build_agent_takeover missed directory source")
        assert_true(by_id[49]["result"]["structuredContent"]["would_write_project_state"] is False, "read-only build_agent_takeover should not write state")
        assert_true("Agent Takeover" in by_id[49]["result"]["content"][0]["text"], "read-only build_agent_takeover markdown failed")
        assert_true(by_id[21]["result"]["structuredContent"]["type"] == "author_review_queue", "read-only build_author_review_queue failed")
        assert_true(by_id[21]["result"]["structuredContent"]["read_only"] is True, "read-only author review queue should be read-only")
        assert_true("Long Novel Agent Author Review Queue" in by_id[21]["result"]["content"][0]["text"], "read-only author review queue markdown failed")
        assert_true(by_id[12]["result"]["structuredContent"]["type"] == "desktop_evidence_packet", "read-only build_desktop_evidence_packet failed")
        assert_true(by_id[12]["result"]["structuredContent"]["status"] == "needs_evidence", "read-only desktop evidence packet should ask for evidence when none is provided")
        assert_true("Long Novel Agent Desktop Evidence Packet" in by_id[12]["result"]["content"][0]["text"], "read-only desktop evidence packet markdown failed")
        assert_true(by_id[26]["result"]["structuredContent"]["type"] == "desktop_evidence_review", "read-only build_desktop_evidence_review failed")
        assert_true(by_id[26]["result"]["structuredContent"]["status"] == "needs_evidence", "read-only desktop evidence review should ask for evidence when none is provided")
        assert_true("Long Novel Agent Desktop Evidence Review" in by_id[26]["result"]["content"][0]["text"], "read-only desktop evidence review markdown failed")
        assert_true(by_id[57]["result"]["structuredContent"]["type"] == "desktop_evidence_gaps", "read-only build_desktop_evidence_gaps failed")
        assert_true(by_id[57]["result"]["structuredContent"]["status"] == "needs_evidence", "read-only desktop evidence gaps should ask for evidence when none is provided")
        assert_true("Long Novel Agent Desktop Evidence Gaps" in by_id[57]["result"]["content"][0]["text"], "read-only desktop evidence gaps markdown failed")
        assert_true(by_id[13]["result"]["structuredContent"]["type"] == "chapter_range_readiness", "read-only build_chapter_range_readiness failed")
        assert_true(by_id[13]["result"]["structuredContent"]["status"] == "needs_files", "read-only chapter range readiness should report missing second draft")
        assert_true("Long Novel Agent Chapter Range Readiness" in by_id[13]["result"]["content"][0]["text"], "read-only chapter range readiness markdown failed")
        assert_true(by_id[14]["result"]["structuredContent"]["type"] == "desktop_evidence_sanitizer", "read-only sanitize_desktop_evidence failed")
        assert_true("Bearer <redacted>" in by_id[14]["result"]["structuredContent"]["sanitized_text"], "read-only sanitize_desktop_evidence missed bearer redaction")
        assert_true("Long Novel Agent Desktop Evidence Sanitizer" in by_id[14]["result"]["content"][0]["text"], "read-only sanitize_desktop_evidence markdown failed")
        assert_true(by_id[16]["result"]["structuredContent"]["type"] == "agent_activity_report", "read-only build_agent_activity_report failed")
        assert_true(by_id[16]["result"]["structuredContent"]["status"] == "blocked", "read-only build_agent_activity_report should flag missing activity")
        assert_true("Long Novel Agent Activity Report" in by_id[16]["result"]["content"][0]["text"], "read-only agent activity report markdown failed")
        assert_true(by_id[17]["result"]["structuredContent"]["type"] == "handoff_integrity_report", "read-only build_handoff_integrity_report failed")
        assert_true(by_id[17]["result"]["structuredContent"]["status"] == "needs_review", "read-only build_handoff_integrity_report should ask for activity review")
        assert_true("Long Novel Agent Handoff Integrity" in by_id[17]["result"]["content"][0]["text"], "read-only handoff integrity markdown failed")
        assert_true(by_id[18]["result"]["structuredContent"]["type"] == "handoff_range_report", "read-only build_handoff_range_report failed")
        assert_true(by_id[18]["result"]["structuredContent"]["status"] in {"blocked", "needs_review"}, "read-only build_handoff_range_report should flag missing range or activity proof")
        assert_true("Long Novel Agent Handoff Range Report" in by_id[18]["result"]["content"][0]["text"], "read-only handoff range markdown failed")
        assert_true(by_id[19]["result"]["structuredContent"]["type"] == "chapter_range_delivery_packet", "read-only build_chapter_range_delivery_packet failed")
        assert_true(by_id[19]["result"]["structuredContent"]["status"] in {"blocked", "needs_revision"}, "read-only chapter range delivery should flag missing second draft")
        assert_true("Long Novel Agent Chapter Range Delivery" in by_id[19]["result"]["content"][0]["text"], "read-only chapter range delivery markdown failed")
        assert_true(by_id[20]["result"]["structuredContent"]["type"] == "chapter_range_acceptance_plan", "read-only build_chapter_range_acceptance_plan failed")
        assert_true(by_id[20]["result"]["structuredContent"]["read_only"] is True, "read-only chapter range acceptance plan should be read-only")
        assert_true(by_id[20]["result"]["structuredContent"]["status"] in {"blocked", "needs_revision"}, "read-only chapter range acceptance plan should flag missing second draft")
        assert_true("Long Novel Agent Chapter Range Acceptance Plan" in by_id[20]["result"]["content"][0]["text"], "read-only chapter range acceptance markdown failed")
        return {"tool_count": len(names), "write_call_rejected": True}


def verify_mcp_tool_profiles() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-mcp-profile-") as tmp:
        project = Path(tmp) / "novel"
        project.mkdir()
        run_cli("init", str(project), "--title", "Tool Profile MCP")
        profile_session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "1", "--platform", "codex", "--mode", "read-only"))
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "prepare_chapter_session",
                    "arguments": {"project_dir": str(project), "chapter": 1, "platform": "codex", "mode": "read-only", "format": "markdown"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "build_desktop_pack_readiness", "arguments": {"pack_dir": str(project)}},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "add_fact", "arguments": {"project_dir": str(project), "kind": "prop", "subject": "灯", "predicate": "holder", "object": "林追"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "explain_tool_visibility", "arguments": {"tool_name": "verify_desktop_setup", "mode": "read-only", "tool_profile": "core", "format": "markdown"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "check_write_session",
                    "arguments": {
                        "project_dir": str(project),
                        "chapter": 1,
                        "expected_project_id": profile_session["project_identity"]["project_id"],
                        "expected_state_sha256": profile_session["project_state_fingerprint"]["sha256"],
                        "expected_context_hash": profile_session["chapter_context_fingerprint"]["sha256"],
                        "format": "markdown",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "build_desktop_diagnostics", "arguments": {"project_dir": str(project), "platform": "codex", "mode": "read-only", "chapter": 1, "format": "markdown"}},
            },
        ]
        payload = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
        proc = subprocess.run(
            [sys.executable, str(SERVER), "--read-only", "--tool-profile", "core"],
            input=payload,
            text=True,
            capture_output=True,
            check=True,
        )
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        by_id = {reply.get("id"): reply for reply in replies}
        server_info = by_id[1]["result"]["serverInfo"]
        names = {tool["name"] for tool in by_id[2]["result"]["tools"]}
        assert_true(server_info["toolProfile"] == "core", "MCP initialize missed core tool profile")
        assert_true(server_info["readOnly"] is True, "MCP initialize missed read-only mode with profile")
        assert_true({"prepare_chapter_session", "check_write_session", "build_chapter_context", "check_chapter", "build_chapter_delivery_packet"} <= names, "core MCP profile missed writing-path read tools")
        assert_true("explain_tool_visibility" in names, "core MCP profile missed tool visibility explanation")
        assert_true("build_desktop_diagnostics" in names, "core MCP profile missed desktop diagnostics")
        assert_true("build_desktop_pack_readiness" not in names, "core MCP profile exposed pack maintenance tool")
        assert_true("validate_source_intake" not in names, "core MCP profile exposed source intake maintenance tool")
        assert_true("init_project" not in names and "add_fact" not in names, "read-only core profile exposed write tools")
        assert_true(by_id[3]["result"]["structuredContent"]["type"] == "chapter_session_packet", "core MCP profile failed prepare_chapter_session")
        assert_true("error" in by_id[4] and "tool profile" in by_id[4]["error"]["message"], "core MCP profile did not reject hidden read tool")
        assert_true("error" in by_id[5] and "Read-only MCP mode blocks write tool" in by_id[5]["error"]["message"], "read-only core profile did not reject write tool")
        assert_true(by_id[6]["result"]["structuredContent"]["type"] == "tool_visibility_explanation", "core MCP profile failed explain_tool_visibility")
        assert_true("tool_profile" in by_id[6]["result"]["structuredContent"]["blocked_by"], "core explain_tool_visibility missed profile blocker")
        assert_true("MCP Tool Visibility" in by_id[6]["result"]["content"][0]["text"], "core explain_tool_visibility markdown failed")
        assert_true(by_id[7]["result"]["structuredContent"]["type"] == "write_session_check", "core MCP profile failed check_write_session")
        assert_true(by_id[7]["result"]["structuredContent"]["status"] == "ready", "core MCP check_write_session should pass fresh values")
        assert_true("Write Session Check" in by_id[7]["result"]["content"][0]["text"], "core MCP check_write_session markdown failed")
        assert_true(by_id[8]["result"]["structuredContent"]["type"] == "desktop_diagnostics", "core MCP profile failed build_desktop_diagnostics")
        assert_true("Long Novel Agent Desktop Diagnostics" in by_id[8]["result"]["content"][0]["text"], "core MCP desktop diagnostics markdown failed")
        return {"core_read_only_tool_count": len(names), "hidden_tool_rejected": True, "write_tool_rejected": True}


def verify_install_platforms() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-install-") as tmp:
        target = Path(tmp) / "skills"
        proc = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--dry-run",
                "--target",
                str(target),
                "--mcp-platform",
                "codex",
                "--print-mcp-config",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("[mcp_servers.long-novel-agent]" in proc.stdout, "codex MCP snippet missing TOML header")
        assert_true("--read-only" in proc.stdout, "default MCP snippet should be read-only")
        core = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--dry-run",
                "--target",
                str(target),
                "--mcp-platform",
                "codex",
                "--tool-profile",
                "core",
                "--print-mcp-config",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true('"--tool-profile", "core"' in core.stdout, "codex MCP snippet missed core tool profile args")
        writer = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--dry-run",
                "--target",
                str(target),
                "--mcp-platform",
                "codex",
                "--writer-mcp",
                "--print-mcp-config",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("--read-only" not in writer.stdout, "writer MCP snippet should not include read-only flag")
        cursor = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--dry-run",
                "--target",
                str(target),
                "--mcp-platform",
                "cursor",
                "--print-mcp-config",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true('"mcpServers"' in cursor.stdout, "cursor MCP snippet missing mcpServers")
        existing_json = Path(tmp) / "existing-mcp.json"
        existing_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "existing-tool": {
                            "command": "/bin/echo",
                            "args": ["existing"],
                        }
                    },
                    "metadata": {"kept": True},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        merge_target = Path(tmp) / "merge-skills"
        subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--target",
                str(merge_target),
                "--mcp-platform",
                "cursor",
                "--mcp-config",
                str(existing_json),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        merged_json = json.loads(existing_json.read_text(encoding="utf-8"))
        servers = merged_json["mcpServers"]
        assert_true("existing-tool" in servers, "JSON MCP merge removed existing server")
        assert_true("long-novel-agent" in servers, "JSON MCP merge missed long-novel-agent")
        assert_true("--read-only" in servers["long-novel-agent"]["args"], "merged JSON MCP config should default to read-only")
        dry_uninstall = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--dry-run",
                "--uninstall",
                "--target",
                str(merge_target),
                "--mcp-platform",
                "cursor",
                "--mcp-config",
                str(existing_json),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert_true("DRY RUN: remove skill" in dry_uninstall.stdout, "dry-run uninstall should report skill removal")
        assert_true((merge_target / "long-novel-agent").exists(), "dry-run uninstall should not remove installed skill")
        assert_true("long-novel-agent" in json.loads(existing_json.read_text(encoding="utf-8"))["mcpServers"], "dry-run uninstall should not change config")
        uninstall_json = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--uninstall",
                "--target",
                str(merge_target),
                "--mcp-platform",
                "cursor",
                "--mcp-config",
                str(existing_json),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        after_uninstall_json = json.loads(existing_json.read_text(encoding="utf-8"))
        assert_true(not (merge_target / "long-novel-agent").exists(), "uninstall should remove installed skill")
        assert_true("existing-tool" in after_uninstall_json["mcpServers"], "JSON MCP uninstall removed other server")
        assert_true("long-novel-agent" not in after_uninstall_json["mcpServers"], "JSON MCP uninstall kept long-novel-agent")
        assert_true("Uninstalled long-novel-agent" in uninstall_json.stdout, "uninstall should report completion")

        conflict_json = Path(tmp) / "conflict-mcp.json"
        conflict_json.write_text(
            json.dumps({"mcpServers": {"long-novel-agent": {"command": "/old/python", "args": ["old-server.py"]}}}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        conflict = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--dry-run",
                "--target",
                str(Path(tmp) / "conflict-skills"),
                "--mcp-platform",
                "cursor",
                "--mcp-config",
                str(conflict_json),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(conflict.returncode != 0, "existing long-novel-agent entry should require --force")
        assert_true("Use --force" in conflict.stderr, "existing long-novel-agent conflict should mention --force")
        upgrade_target = Path(tmp) / "upgrade-skills"
        upgrade = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--upgrade",
                "--target",
                str(upgrade_target),
                "--mcp-platform",
                "cursor",
                "--mcp-config",
                str(conflict_json),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        upgraded_json = json.loads(conflict_json.read_text(encoding="utf-8"))
        assert_true(upgraded_json["mcpServers"]["long-novel-agent"]["command"] != "/old/python", "upgrade should replace old MCP entry")
        assert_true((upgrade_target / "long-novel-agent" / "SKILL.md").exists(), "upgrade should install skill files")
        assert_true("Upgraded long-novel-agent" in upgrade.stdout, "upgrade should report completion")

        codex_config = Path(tmp) / "config.toml"
        codex_config.write_text("[profile]\ndefault = \"main\"\n", encoding="utf-8")
        codex_target = Path(tmp) / "codex-merge-skills"
        subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--target",
                str(codex_target),
                "--mcp-platform",
                "codex",
                "--mcp-config",
                str(codex_config),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        codex_text = codex_config.read_text(encoding="utf-8")
        assert_true("[profile]" in codex_text, "Codex MCP merge removed existing config")
        assert_true("[mcp_servers.long-novel-agent]" in codex_text, "Codex MCP merge missed long-novel-agent section")
        assert_true("--read-only" in codex_text, "merged Codex config should default to read-only")
        subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--uninstall",
                "--target",
                str(codex_target),
                "--mcp-platform",
                "codex",
                "--mcp-config",
                str(codex_config),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        codex_after_uninstall = codex_config.read_text(encoding="utf-8")
        assert_true("[profile]" in codex_after_uninstall, "Codex MCP uninstall removed unrelated config")
        assert_true("[mcp_servers.long-novel-agent]" not in codex_after_uninstall, "Codex MCP uninstall kept long-novel-agent section")
        assert_true(not (codex_target / "long-novel-agent").exists(), "Codex uninstall should remove installed skill")

        auto_codex_config = Path(tmp) / "auto-codex.toml"
        auto_env = os.environ.copy()
        auto_env["LONG_NOVEL_AGENT_CODEX_CONFIG"] = str(auto_codex_config)
        auto_codex = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--target",
                str(Path(tmp) / "auto-codex-skills"),
                "--mcp-platform",
                "codex",
                "--mcp-config",
                "auto",
            ],
            text=True,
            capture_output=True,
            check=True,
            env=auto_env,
        )
        auto_codex_text = auto_codex_config.read_text(encoding="utf-8")
        assert_true("[mcp_servers.long-novel-agent]" in auto_codex_text, "auto Codex MCP config missing server section")
        assert_true("--read-only" in auto_codex_text, "auto Codex MCP config should default to read-only")
        assert_true(str(auto_codex_config) in auto_codex.stdout, "auto Codex install output should show resolved path")
        assert_true("doctor /path/to/novel" in auto_codex.stdout, "auto Codex install output should include doctor command")
        assert_true("quickstart /path/to/novel" in auto_codex.stdout, "auto Codex install output should include quickstart command")

        auto_cursor_config = Path(tmp) / "auto-cursor.json"
        auto_cursor_env = os.environ.copy()
        auto_cursor_env["LONG_NOVEL_AGENT_CURSOR_CONFIG"] = str(auto_cursor_config)
        auto_cursor = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--target",
                str(Path(tmp) / "auto-cursor-skills"),
                "--mcp-platform",
                "cursor",
                "--mcp-config",
                "auto",
            ],
            text=True,
            capture_output=True,
            check=True,
            env=auto_cursor_env,
        )
        auto_cursor_json = json.loads(auto_cursor_config.read_text(encoding="utf-8"))
        auto_cursor_servers = auto_cursor_json["mcpServers"]
        assert_true("long-novel-agent" in auto_cursor_servers, "auto Cursor MCP config missing server entry")
        assert_true("--read-only" in auto_cursor_servers["long-novel-agent"]["args"], "auto Cursor MCP config should default to read-only")
        assert_true(str(auto_cursor_config) in auto_cursor.stdout, "auto Cursor install output should show resolved path")

        generic_auto = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--dry-run",
                "--target",
                str(target),
                "--mcp-platform",
                "generic-json",
                "--mcp-config",
                "auto",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(generic_auto.returncode != 0, "generic-json should reject --mcp-config auto")
        assert_true("explicit --mcp-config path" in generic_auto.stderr, "generic-json auto rejection should ask for explicit path")

        chatgpt = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--dry-run",
                "--target",
                str(target),
                "--mcp-platform",
                "chatgpt",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(chatgpt.returncode != 0, "chatgpt should not be a local desktop install platform")
        assert_true("invalid choice" in chatgpt.stderr, "chatgpt rejection should come from platform choices")
    return {
        "platforms": ["codex", "cursor"],
        "default_read_only": True,
        "writer_opt_in": True,
        "merged_existing_config": True,
        "auto_config": True,
        "quickstart_command": True,
        "upgrade": True,
        "uninstall": True,
        "chatgpt_rejected": True,
    }


def verify_review_panel_asset() -> dict[str, Any]:
    html = REVIEW_PANEL.read_text(encoding="utf-8")
    for marker in (
        "Proposal Summary",
        "Risk & Validation",
        "apply-after-write",
        "reject-proposal",
        "validate-proposal",
        "diff-proposal",
        "navigator.clipboard.writeText",
    ):
        assert_true(marker in html, f"review panel missing {marker}")
    return {"bytes": len(html), "has_commands": True}


def verify_author_confirmation_guardrails() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="long-novel-author-confirmation-") as tmp:
        project = Path(tmp) / "novel"
        run_cli("init", str(project), "--title", "author-confirmation")
        missing = run_cli(
            "add-fact",
            str(project),
            "--kind",
            "prop",
            "--subject",
            "铜钥匙",
            "--predicate",
            "holder",
            "--object",
            "林追",
            check=False,
            auto_author=False,
        )
        assert_true(missing.returncode != 0, "add-fact should require author confirmation")
        assert_true("author confirmation" in missing.stderr.lower(), "missing author confirmation error should be explicit")
        added = load_stdout(
            run_cli(
                "add-fact",
                str(project),
                "--kind",
                "prop",
                "--subject",
                "铜钥匙",
                "--predicate",
                "holder",
                "--object",
                "林追",
            )
        )
        assert_true(added["author_confirmation"]["provided"], "add-fact result missed author confirmation")
        audit = load_stdout(run_cli("list-audit", str(project), "--limit", "10"))
        fact_audit = [row for row in audit["audit"] if row.get("operation") == "add-fact"]
        assert_true(fact_audit and fact_audit[-1].get("author_confirmation", {}).get("provided"), "audit missed author confirmation")

        chapter_dir = project / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        chapter_file = chapter_dir / "001.md"
        chapter_file.write_text("林追收好铜钥匙，把码头旧账重新记在袖中。\n", encoding="utf-8")
        session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "1", "--platform", "codex", "--mode", "writer"))
        project_id = session["project_identity"]["project_id"]
        state_sha = session["project_state_fingerprint"]["sha256"]
        context_sha = session["chapter_context_fingerprint"]["sha256"]
        write_session_ready = load_stdout(
            run_cli(
                "write-session-check",
                str(project),
                "--chapter",
                "1",
                "--expected-project-id",
                project_id,
                "--expected-state-sha256",
                state_sha,
                "--expected-context-hash",
                context_sha,
                "--format",
                "json",
            )
        )
        assert_true(write_session_ready["type"] == "write_session_check", "write-session-check returned wrong type")
        assert_true(write_session_ready["status"] == "ready" and write_session_ready["ok"] is True, "write-session-check should pass fresh prepare-session values")
        assert_true(write_session_ready["write_guard_arguments"], "write-session-check missed writer guard arguments")
        write_session_markdown = run_cli(
            "write-session-check",
            str(project),
            "--chapter",
            "1",
            "--expected-project-id",
            project_id,
            "--expected-state-sha256",
            state_sha,
            "--expected-context-hash",
            context_sha,
            "--format",
            "markdown",
        )
        assert_true("Write Session Check" in write_session_markdown.stdout, "write-session-check markdown missing title")
        guarded_record = load_stdout(
            run_cli(
                "record-chapter",
                str(project),
                "--chapter",
                "1",
                "--file",
                str(chapter_file),
                "--expected-project-id",
                project_id,
                "--expected-state-sha256",
                state_sha,
                "--expected-context-hash",
                context_sha,
            )
        )
        guard = guarded_record.get("write_session_guard") if isinstance(guarded_record.get("write_session_guard"), dict) else {}
        assert_true(guard.get("type") == "write_session_guard", "record-chapter missed write session guard result")
        assert_true(len(guard.get("checks") or []) == 3, "write session guard should check project, state, and context")
        write_session_stale = load_stdout(
            run_cli(
                "write-session-check",
                str(project),
                "--chapter",
                "1",
                "--expected-project-id",
                project_id,
                "--expected-state-sha256",
                state_sha,
                "--expected-context-hash",
                context_sha,
                "--format",
                "json",
            )
        )
        assert_true(write_session_stale["status"] == "stale_state", "write-session-check should flag stale state after record-chapter")
        stale_state = run_cli(
            "record-style",
            str(project),
            "--chapter",
            "1",
            "--file",
            str(chapter_file),
            "--expected-project-id",
            project_id,
            "--expected-state-sha256",
            state_sha,
            check=False,
        )
        assert_true(stale_state.returncode != 0, "record-style should reject stale expected state sha")
        assert_true("state changed" in stale_state.stderr.lower(), "stale state guard error should explain state changed")
        fresh_session = load_stdout(run_cli("prepare-session", str(project), "--chapter", "1", "--platform", "codex", "--mode", "writer"))
        fresh_state_sha = fresh_session["project_state_fingerprint"]["sha256"]
        fresh_context_sha = fresh_session["chapter_context_fingerprint"]["sha256"]
        write_session_wrong_project = load_stdout(
            run_cli(
                "write-session-check",
                str(project),
                "--chapter",
                "1",
                "--expected-project-id",
                "novel-wrong-project",
                "--expected-state-sha256",
                fresh_state_sha,
                "--expected-context-hash",
                fresh_context_sha,
                "--format",
                "json",
            )
        )
        assert_true(write_session_wrong_project["status"] == "project_mismatch", "write-session-check should flag wrong project id")
        write_session_wrong_context = load_stdout(
            run_cli(
                "write-session-check",
                str(project),
                "--chapter",
                "1",
                "--expected-project-id",
                project_id,
                "--expected-state-sha256",
                fresh_state_sha,
                "--expected-context-hash",
                "0" * 64,
                "--format",
                "json",
            )
        )
        assert_true(write_session_wrong_context["status"] == "stale_context", "write-session-check should flag stale context hash")
        wrong_project = run_cli(
            "record-style",
            str(project),
            "--chapter",
            "1",
            "--file",
            str(chapter_file),
            "--expected-project-id",
            "novel-wrong-project",
            "--expected-state-sha256",
            fresh_state_sha,
            check=False,
        )
        assert_true(wrong_project.returncode != 0, "record-style should reject wrong expected project id")
        assert_true("project identity mismatch" in wrong_project.stderr.lower(), "wrong project guard error should be explicit")
        wrong_context = run_cli(
            "record-style",
            str(project),
            "--chapter",
            "1",
            "--file",
            str(chapter_file),
            "--expected-project-id",
            project_id,
            "--expected-state-sha256",
            fresh_state_sha,
            "--expected-context-hash",
            "0" * 64,
            check=False,
        )
        assert_true(wrong_context.returncode != 0, "record-style should reject wrong expected context hash")
        assert_true("chapter context changed" in wrong_context.stderr.lower(), "context guard error should explain context changed")

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "add_fact",
                    "arguments": {
                        "project_dir": str(project),
                        "kind": "prop",
                        "subject": "银钥匙",
                        "predicate": "holder",
                        "object": "顾临",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "check_write_session",
                    "arguments": {
                        "project_dir": str(project),
                        "chapter": 1,
                        "expected_project_id": project_id,
                        "expected_state_sha256": fresh_state_sha,
                        "expected_context_hash": fresh_context_sha,
                        "format": "markdown",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "add_fact",
                    "arguments": {
                        "project_dir": str(project),
                        "kind": "prop",
                        "subject": "银钥匙",
                        "predicate": "holder",
                        "object": "顾临",
                        "author_confirmation": TEST_AUTHOR_CONFIRMATION,
                        "expected_project_id": project_id,
                        "expected_state_sha256": fresh_state_sha,
                    },
                },
            },
        ]
        payload = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
        proc = subprocess.run([sys.executable, str(SERVER)], input=payload, text=True, capture_output=True, check=True)
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        by_id = {reply.get("id"): reply for reply in replies}
        assert_true("error" in by_id[2], "MCP add_fact should reject missing author confirmation")
        assert_true("author confirmation" in by_id[2]["error"]["message"].lower(), "MCP missing author confirmation error should be explicit")
        assert_true(by_id[4]["result"]["structuredContent"]["type"] == "write_session_check", "MCP check_write_session returned wrong type")
        assert_true(by_id[4]["result"]["structuredContent"]["status"] == "ready", "MCP check_write_session should pass fresh values")
        assert_true("Write Session Check" in by_id[4]["result"]["content"][0]["text"], "MCP check_write_session markdown failed")
        assert_true(by_id[3]["result"]["structuredContent"]["author_confirmation"]["provided"], "MCP add_fact result missed author confirmation")
        return {
            "cli_missing_rejected": True,
            "cli_audit_recorded": True,
            "write_session_guard_recorded": bool(guard),
            "stale_state_rejected": True,
            "wrong_project_rejected": True,
            "wrong_context_rejected": True,
            "write_session_check_ready": True,
            "write_session_check_stale_state": True,
            "write_session_check_wrong_project": True,
            "write_session_check_wrong_context": True,
            "mcp_missing_rejected": True,
            "mcp_write_session_check_ready": True,
            "mcp_author_recorded": True,
        }


def main() -> int:
    results = {
        "example": verify_example_import(),
        "desktop_handoff_relay": verify_desktop_handoff_relay_example(),
        "longform_continuity_chain": verify_longform_continuity_chain_example(),
        "longform_twenty_chapter_stress": verify_longform_twenty_chapter_stress(),
        "context_brief_chapter_range_priority": verify_context_brief_chapter_range_priority(),
        "desktop_verification_evidence_example": verify_desktop_verification_evidence_example(),
        "proposal_guardrails": verify_proposal_guardrails(),
        "continuity_audit": verify_continuity_audit(),
        "init_wizard_handoff_install_doctor": verify_init_wizard_handoff_and_install_doctor(),
        "quickstart": verify_quickstart(),
        "desktop_setup_guide": verify_desktop_setup_guide(),
        "desktop_onboarding_guide": verify_desktop_onboarding_guide(),
        "desktop_bootstrap": verify_desktop_bootstrap(),
        "desktop_pack": verify_desktop_pack(),
        "desktop_handoff_bundle": verify_desktop_handoff_bundle(),
        "desktop_client_checklist": verify_desktop_client_checklist(),
        "agent_activity_log": verify_agent_activity_log(),
        "desktop_setup_verification": verify_desktop_setup_verification(),
        "snapshots_and_locks": verify_snapshots_and_locks(),
        "adversarial_cases": verify_adversarial_cases(),
        "migration": verify_migration(),
        "state_package_and_capabilities": verify_state_package_and_capabilities(),
        "conflict_scope_and_style": verify_conflict_scope_and_style(),
        "tool_visibility": verify_tool_visibility(),
        "mcp": verify_mcp(),
        "mcp_read_only": verify_mcp_read_only(),
        "mcp_tool_profiles": verify_mcp_tool_profiles(),
        "author_confirmation": verify_author_confirmation_guardrails(),
        "install_platforms": verify_install_platforms(),
        "review_panel": verify_review_panel_asset(),
    }
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
