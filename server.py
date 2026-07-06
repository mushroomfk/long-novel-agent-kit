#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Callable

import cli

Json = dict[str, Any]
MUTATING_TOOLS = cli.MCP_MUTATING_TOOLS
READ_ONLY_MODE = False
SUPPORTED_TOOL_PROFILES = cli.MCP_TOOL_PROFILES
TOOL_PROFILE = "full"
CORE_PROFILE_TOOLS = cli.MCP_CORE_PROFILE_TOOLS
WRITING_PROFILE_TOOLS = cli.MCP_WRITING_PROFILE_TOOLS


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def schema(properties: Json, required: list[str] | None = None) -> Json:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def with_author_confirmation(properties: Json) -> Json:
    updated = dict(properties)
    updated["author_confirmation"] = {"type": "string"}
    updated["expected_project_id"] = {"type": "string"}
    updated["expected_state_sha256"] = {"type": "string"}
    updated["expected_context_hash"] = {"type": "string"}
    return updated


TOOLS: list[Json] = [
    {
        "name": "init_project",
        "description": "Initialize a .novel-agent continuity workspace for a long novel project.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "title": {"type": "string"},
                "target_chapters": {"type": "integer", "minimum": 1},
                "target_words": {"type": "integer", "minimum": 1},
                "force": {"type": "boolean"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "init_wizard",
        "description": "Plan or initialize a project with rules, source summary, and host-agent metadata. Use dry_run before writing.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "title": {"type": "string"},
                "target_chapters": {"type": "integer", "minimum": 1},
                "target_words": {"type": "integer", "minimum": 1},
                "required_phrases": {"type": "array", "items": {"type": "string"}},
                "forbidden_phrases": {"type": "array", "items": {"type": "string"}},
                "future_markers": {"type": "array", "items": {"type": "string"}},
                "rule_notes": {"type": "array", "items": {"type": "string"}},
                "style_note": {"type": "string"},
                "source_summary": {"type": "string"},
                "source_path": {"type": "string"},
                "chapter_range": {"type": "string"},
                "agent_id": {"type": "string"},
                "platform": {"type": "string"},
                "capabilities": {"type": "array", "items": {"type": "string"}},
                "agent_notes": {"type": "string"},
                "import_gaoxia": {"type": "boolean"},
                "append_import": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
                "force": {"type": "boolean"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "quickstart_project",
        "description": "Initialize or import a local novel project, declare the host agent, build a startup packet, and run doctor for first-time desktop agent use.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "source": {"type": "string", "enum": ["auto", "init", "import-gaoxia"]},
                "title": {"type": "string"},
                "target_chapters": {"type": "integer", "minimum": 1},
                "target_words": {"type": "integer", "minimum": 1},
                "required_phrases": {"type": "array", "items": {"type": "string"}},
                "forbidden_phrases": {"type": "array", "items": {"type": "string"}},
                "future_markers": {"type": "array", "items": {"type": "string"}},
                "rule_notes": {"type": "array", "items": {"type": "string"}},
                "style_note": {"type": "string"},
                "source_summary": {"type": "string"},
                "source_path": {"type": "string"},
                "chapter_range": {"type": "string"},
                "agent_id": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "capabilities": {"type": "array", "items": {"type": "string"}},
                "agent_notes": {"type": "string"},
                "append_import": {"type": "boolean"},
                "chapter": {"type": "integer", "minimum": 1},
                "install_config": {"type": "string"},
                "start_mcp_test": {"type": "boolean"},
                "summary_only": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
                "force": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "import_gaoxia_project",
        "description": "Import continuity state from an existing Gaoxia project into .novel-agent without changing the Gaoxia project files.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "include_chapters": {"type": "boolean"},
                "include_vault": {"type": "boolean"},
                "include_narrative_state": {"type": "boolean"},
                "max_vault_notes": {"type": "integer", "minimum": 1},
                "append": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
                "report_path": {"type": "string"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "audit_gaoxia_import",
        "description": "Read-only audit that compares Gaoxia source files with imported .novel-agent continuity state.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "max_vault_notes": {"type": "integer", "minimum": 1},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "export_state_package",
        "description": "Export .novel-agent state as a portable zip package for handoff to another agent or machine.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "output": {"type": "string"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "import_state_package",
        "description": "Import a portable .novel-agent zip package into a project directory.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "package": {"type": "string"},
                "force": {"type": "boolean"},
            },
            ["project_dir", "package"],
        ),
    },
    {
        "name": "get_agent_template",
        "description": "Return host desktop agent workflow instructions for a local platform and write mode.",
        "inputSchema": schema(
            {
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
            },
            [],
        ),
    },
    {
        "name": "explain_tool_visibility",
        "description": "Explain whether a local MCP tool is visible under read-only/writer mode and core/writing/full profile.",
        "inputSchema": schema(
            {
                "tool_name": {"type": "string"},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["tool_name"],
        ),
    },
    {
        "name": "build_desktop_setup_guide",
        "description": "Build a local desktop agent setup guide with MCP config, quickstart, doctor, and chapter-session commands.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            [],
        ),
    },
    {
        "name": "build_desktop_onboarding_guide",
        "description": "Build a plain-language onboarding guide that sequences install, quickstart, local verification, desktop client evidence, and first chapter session.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "source": {"type": "string", "enum": ["auto", "init", "import-gaoxia"]},
                "chapter": {"type": "integer", "minimum": 1},
                "evidence_file": {"type": "string"},
                "results_file": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            [],
        ),
    },
    {
        "name": "verify_desktop_setup",
        "description": "Verify a local desktop MCP config for this kit, including expected read-only or writer mode and optional MCP startup.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "start_mcp_test": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            [],
        ),
    },
    {
        "name": "build_desktop_next_step",
        "description": "Build a read-only next-step decision report for a local desktop agent from project state, MCP config, desktop evidence, and author review queue.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "chapter": {"type": "integer", "minimum": 1},
                "include_session": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            [],
        ),
    },
    {
        "name": "build_desktop_diagnostics",
        "description": "Build a read-only local desktop-agent diagnostics report from project state, MCP config, desktop evidence, and optional pack health.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "chapter": {"type": "integer", "minimum": 1},
                "pack_dir": {"type": "string"},
                "include_session": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            [],
        ),
    },
    {
        "name": "build_agent_startup_prompt",
        "description": "Build a copy-ready read-only startup prompt for a local desktop agent starting or taking over a target chapter.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "chapter": {"type": "integer", "minimum": 1},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_desktop_client_checklist",
        "description": "Build a manual verification checklist for confirming this kit inside a real local desktop agent client.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "chapter": {"type": "integer", "minimum": 1},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            [],
        ),
    },
    {
        "name": "build_desktop_evidence_session",
        "description": "Build a read-only real desktop-client evidence session with checklist prompts and local follow-up commands.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "chapter": {"type": "integer", "minimum": 1},
                "evidence_file": {"type": "string"},
                "results_file": {"type": "string"},
                "imported_results_file": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            [],
        ),
    },
    {
        "name": "build_desktop_evidence_runbook",
        "description": "Build a read-only ordered runbook for collecting real desktop-client evidence inside a GUI desktop client.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "install_target": {"type": "string"},
                "tool_profile": {"type": "string", "enum": ["core", "writing", "full"]},
                "chapter": {"type": "integer", "minimum": 1},
                "evidence_file": {"type": "string"},
                "results_file": {"type": "string"},
                "imported_results_file": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            [],
        ),
    },
    {
        "name": "build_source_intake_template",
        "description": "Return a fillable source-intake.json template without writing files or .novel-agent state.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_author_actions",
        "description": "Refresh the read-only author action checklist from a generated desktop-pack directory.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_acceptance_review",
        "description": "Refresh the read-only post-acceptance author review from a generated desktop-pack directory, including single-chapter and multi-chapter write gates.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown", "html"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_desktop_pack_readiness",
        "description": "Refresh the read-only desktop-pack readiness report from a generated desktop-pack directory.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_desktop_local_summary",
        "description": "Refresh the short read-only local desktop summary from a generated desktop-pack directory.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_desktop_user_steps",
        "description": "Refresh the five-step read-only local user path from a generated desktop-pack directory.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown", "html"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_desktop_pack_freshness",
        "description": "Check whether a generated desktop pack still matches the current tracked .novel-agent state.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_chapter_session_freshness",
        "description": "Check whether a generated chapter-session.json still matches the current tracked .novel-agent state.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_desktop_pack_schema_check",
        "description": "Validate generated desktop-pack JSON files against their local schema files without writing state.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_starter_brief",
        "description": "Refresh the read-only starter brief from a generated desktop-pack directory.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_agent_takeover",
        "description": "Refresh the read-only desktop-agent takeover packet from a generated desktop-pack directory.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "build_desktop_troubleshooting",
        "description": "Refresh the read-only troubleshooting packet from a generated desktop-pack directory.",
        "inputSchema": schema(
            {
                "pack_dir": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["pack_dir"],
        ),
    },
    {
        "name": "validate_source_intake",
        "description": "Validate a filled source-intake.json file and return warning quality without writing .novel-agent state.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "intake": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "intake"],
        ),
    },
    {
        "name": "ingest_desktop_client_evidence",
        "description": "Convert pasted real desktop client evidence text, filled result JSON, tool JSON snippets, or NDJSON tool transcripts into desktop checklist results JSON and safety warnings without recording it.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "evidence_text": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["evidence_text"],
        ),
    },
    {
        "name": "sanitize_desktop_evidence",
        "description": "Redact sensitive values and shorten copied desktop-client evidence before importing or recording it. This does not write .novel-agent state.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "evidence_text": {"type": "string"},
                "max_line_chars": {"type": "integer", "minimum": 80},
                "max_total_chars": {"type": "integer", "minimum": 80},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["evidence_text"],
        ),
    },
    {
        "name": "build_desktop_results_doctor",
        "description": "Check filled real desktop client verification results without recording them in .novel-agent.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "results": {"type": ["object", "array"]},
                "install_config": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "results_reference": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "results"],
        ),
    },
    {
        "name": "build_desktop_evidence_packet",
        "description": "Build a read-only desktop evidence status packet from pasted evidence or results JSON without recording it.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "evidence_text": {"type": "string"},
                "results": {"type": ["object", "array"]},
                "install_config": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "results_reference": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_desktop_evidence_review",
        "description": "Build a read-only review that combines desktop evidence session, copied evidence or results JSON, and saved real-client verification records.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "evidence_text": {"type": "string"},
                "results": {"type": ["object", "array"]},
                "install_config": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "results_reference": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown", "html"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_desktop_evidence_gaps",
        "description": "Build a read-only per-check gap report for missing, failed, weak, or specific real desktop-client evidence.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "evidence_text": {"type": "string"},
                "results": {"type": ["object", "array"]},
                "install_config": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "results_reference": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_writer_mode_readiness",
        "description": "Read-only gate for enabling local writer MCP mode after read-only real-client proof and author approval.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "install_config": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "author_confirmation": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_author_review_queue",
        "description": "Build a read-only queue of proposals, conflicts, writer-mode authorization, and chapter-range acceptance items that need author review before durable state changes.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "install_config": {"type": "string"},
                "start_chapter": {"type": "integer", "minimum": 1},
                "end_chapter": {"type": "integer", "minimum": 1},
                "handoff_chapter": {"type": "integer", "minimum": 1},
                "draft_dir": {"type": "string"},
                "file_template": {"type": "string"},
                "author_confirmation": {"type": "string"},
                "include_writer_mode": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "record_desktop_client_check",
        "description": "Record real desktop client verification evidence for this kit in .novel-agent.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "results": {"type": ["object", "array"]},
                "install_config": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "agent_id": {"type": "string"},
                "client_version": {"type": "string"},
                "notes": {"type": "string"},
            },
            ["project_dir", "results"],
        ),
    },
    {
        "name": "list_desktop_client_checks",
        "description": "List recorded real desktop client verification evidence.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "get_desktop_verification_matrix",
        "description": "Summarize real desktop client verification evidence by local platform and mode.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "platform": {"type": "string"},
                "platforms": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string"},
                "modes": {"type": "array", "items": {"type": "string"}},
                "chapter": {"type": "integer", "minimum": 1},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "record_agent_activity",
        "description": "Record what a local desktop agent did, which context it used, and what it is handing to the next agent.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "agent_id": {"type": "string"},
                "platform": {"type": "string"},
                "action": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "summary": {"type": "string"},
                "context_source": {"type": "string"},
                "context_hash": {"type": "string"},
                "context_file": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string"},
                "handoff_to": {"type": "string"},
                "notes": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "agent_id", "action"],
        ),
    },
    {
        "name": "list_agent_activity",
        "description": "List local desktop agent activity and handoff records.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "agent_id": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "action": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_agent_activity_report",
        "description": "Check whether target-visible desktop agent activity is specific enough for handoff. Read-only.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "agent_id": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "required_actions": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_handoff_integrity_report",
        "description": "Check whether handoff text, target context, agent activity records, context hashes, and referenced files are consistent. Read-only.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "agent_id": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "required_actions": {"type": "array", "items": {"type": "string"}},
                "expected_context_hash": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_handoff_range_report",
        "description": "Build a read-only multi-chapter handoff report from chapter range readiness, handoff integrity, activity records, and range proposals.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "start_chapter": {"type": "integer", "minimum": 1},
                "end_chapter": {"type": "integer", "minimum": 1},
                "handoff_chapter": {"type": "integer", "minimum": 1},
                "draft_dir": {"type": "string"},
                "file_template": {"type": "string"},
                "agent_id": {"type": "string"},
                "required_actions": {"type": "array", "items": {"type": "string"}},
                "expected_context_hash": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "start_chapter", "end_chapter"],
        ),
    },
    {
        "name": "get_project_status",
        "description": "Summarize whether a .novel-agent project is ready for desktop-agent writing.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "platform": {"type": "string"},
                "install_config": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_continuity_audit",
        "description": "Build a read-only continuity risk audit for chapters, facts, sources, pending proposals, handoff, and desktop evidence.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "platform": {"type": "string"},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "verify_handoff_readiness",
        "description": "Verify whether an incoming local desktop agent is ready to take over a target chapter, including recent target-visible agent activity.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "incoming_agent_id": {"type": "string"},
                "incoming_platform": {"type": "string"},
                "outgoing_agent_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["read-only", "writer"]},
                "required_capabilities": {"type": "array", "items": {"type": "string"}},
                "install_config": {"type": "string"},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "add_source_summary",
        "description": "Persist a source summary produced by the host agent, with chapter visibility and writing constraints.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "summary": {"type": "string"},
                "source_path": {"type": "string"},
                "title": {"type": "string"},
                "chapter_range": {"type": "string"},
                "required_phrases": {"type": ["string", "array"], "items": {"type": "string"}},
                "forbidden_phrases": {"type": ["string", "array"], "items": {"type": "string"}},
                "credibility": {"type": "string"},
                "reveal_after_chapter": {"type": "integer"},
            }),
            ["project_dir", "summary"],
        ),
    },
    {
        "name": "add_research_note",
        "description": "Persist external research or web evidence summarized by the host agent.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "summary": {"type": "string"},
                "url": {"type": "string"},
                "title": {"type": "string"},
                "chapter_range": {"type": "string"},
                "required_phrases": {"type": ["string", "array"], "items": {"type": "string"}},
                "forbidden_phrases": {"type": ["string", "array"], "items": {"type": "string"}},
                "reliability": {"type": "string"},
                "reveal_after_chapter": {"type": "integer"},
            }),
            ["project_dir", "summary"],
        ),
    },
    {
        "name": "declare_agent_capabilities",
        "description": "Record the host agent platform and available abilities such as MCP, CLI, file read/write, PDF, web, OCR, or long context.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "agent_id": {"type": "string"},
                "platform": {"type": "string"},
                "capabilities": {"type": ["string", "array"], "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            ["project_dir", "agent_id"],
        ),
    },
    {
        "name": "list_agent_capabilities",
        "description": "List recorded host agent capability declarations.",
        "inputSchema": schema({"project_dir": {"type": "string"}}, ["project_dir"]),
    },
    {
        "name": "resolve_conflict",
        "description": "Record which version wins when accepted chapters, project facts, or sources disagree.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "conflict": {"type": "string"},
                "chosen": {"type": "string"},
                "reason": {"type": "string"},
                "options": {"type": ["string", "array"], "items": {"type": "string"}},
                "chapter_range": {"type": "string"},
                "scope": {"type": "string", "enum": ["canon", "viewpoint", "range"]},
                "viewpoint": {"type": "string"},
                "effective_until_chapter": {"type": "integer", "minimum": 1},
            }),
            ["project_dir", "conflict", "chosen"],
        ),
    },
    {
        "name": "add_fact",
        "description": "Add a structured continuity fact for chapter-safe context and checks.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "kind": {"type": "string"},
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "object": {"type": "string"},
                "chapter_range": {"type": "string"},
                "source_ids": {"type": ["string", "array"], "items": {"type": "string"}},
                "source_path": {"type": "string"},
                "confidence": {"type": "string"},
                "status": {"type": "string"},
                "reveal_after_chapter": {"type": "integer"},
                "required_phrases": {"type": ["string", "array"], "items": {"type": "string"}},
                "forbidden_phrases": {"type": ["string", "array"], "items": {"type": "string"}},
                "future_markers": {"type": ["string", "array"], "items": {"type": "string"}},
            }),
            ["project_dir", "kind", "subject", "predicate", "object"],
        ),
    },
    {
        "name": "update_fact",
        "description": "Update an existing structured continuity fact.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "fact_id": {"type": "string"},
                "kind": {"type": "string"},
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "object": {"type": "string"},
                "chapter_range": {"type": "string"},
                "source_ids": {"type": ["string", "array"], "items": {"type": "string"}},
                "source_path": {"type": "string"},
                "confidence": {"type": "string"},
                "status": {"type": "string"},
                "reveal_after_chapter": {"type": "integer"},
                "required_phrases": {"type": ["string", "array"], "items": {"type": "string"}},
                "forbidden_phrases": {"type": ["string", "array"], "items": {"type": "string"}},
                "future_markers": {"type": ["string", "array"], "items": {"type": "string"}},
            }),
            ["project_dir", "fact_id"],
        ),
    },
    {
        "name": "list_facts",
        "description": "List active or all structured continuity facts, optionally filtered by chapter and kind.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "kind": {"type": "string"},
                "include_inactive": {"type": "boolean"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "supersede_fact",
        "description": "Mark a structured fact as superseded by a newer fact or author decision.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "fact_id": {"type": "string"},
                "replacement_fact_id": {"type": "string"},
                "reason": {"type": "string"},
            }),
            ["project_dir", "fact_id"],
        ),
    },
    {
        "name": "build_chapter_context",
        "description": "Build chapter-safe context for the target chapter before writing or revising.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "chapter"],
        ),
    },
    {
        "name": "build_chapter_context_brief",
        "description": "Build only the compact read-first chapter context brief for small-context desktop agents.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "chapter"],
        ),
    },
    {
        "name": "prepare_chapter_session",
        "description": "Build a read-only startup packet for a desktop agent before drafting or revising a target chapter.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "platform": {"type": "string", "enum": ["generic", "codex", "claude-desktop", "cursor"]},
                "mode": {"type": "string", "enum": ["writer", "read-only"]},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "check_write_session",
        "description": "Read-only preflight before writer tools mutate .novel-agent state. Checks project_id, project-state sha256, and optional target chapter context hash from prepare_chapter_session.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "expected_project_id": {"type": "string"},
                "expected_state_sha256": {"type": "string"},
                "expected_context_hash": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "record_chapter",
        "description": "Record metadata for an accepted chapter file after the author approves it.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "handoff": {"type": "string"},
            }),
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "record_style_sample",
        "description": "Record a chapter file as a structured style continuity sample.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
                "note": {"type": "string"},
            }),
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "check_chapter",
        "description": "Check a chapter file against visible rules, future boundaries, character state, structured facts, timeline order, location, relationship, life state, and continuity obligations; returns issues plus a revision_plan.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
            },
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "diff_chapter_contract",
        "description": "Compare a chapter file with visible required beats, acceptance checks, forbidden moves, and future markers.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
            },
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "build_chapter_readiness",
        "description": "Build a read-only author-facing chapter acceptance readiness report from continuity checks and contract diff, including a revision_plan ordered for desktop agents.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "build_chapter_revision_prompt",
        "description": "Build a read-only copyable revision prompt from chapter readiness, revision_plan, and fact_context for a local desktop agent.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "build_chapter_revision_comparison",
        "description": "Compare before and after draft readiness for a read-only revision report with resolved, new, and remaining issues.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "before_file": {"type": "string"},
                "after_file": {"type": "string"},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "chapter", "before_file", "after_file"],
        ),
    },
    {
        "name": "build_chapter_delivery_packet",
        "description": "Build a read-only author delivery packet that combines draft metadata, chapter readiness, handoff report, gates, and post-acceptance commands.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "build_chapter_acceptance_plan",
        "description": "Build a read-only post-acceptance write plan for one draft chapter, including pre-write review, author-confirmation gates, write-session guard guidance, and ordered writer commands.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
                "handoff_chapter": {"type": "integer", "minimum": 1},
                "author_confirmation": {"type": "string"},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "build_chapter_range_readiness",
        "description": "Build a read-only readiness report for a range of draft chapters by running chapter readiness on each file, with per-chapter revision_plan summaries.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "start_chapter": {"type": "integer", "minimum": 1},
                "end_chapter": {"type": "integer", "minimum": 1},
                "draft_dir": {"type": "string"},
                "file_template": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "start_chapter", "end_chapter"],
        ),
    },
    {
        "name": "build_chapter_range_delivery_packet",
        "description": "Build a read-only author delivery packet for a range of draft chapters from range readiness, per-chapter delivery packets, handoff range status, gates, and post-acceptance commands.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "start_chapter": {"type": "integer", "minimum": 1},
                "end_chapter": {"type": "integer", "minimum": 1},
                "handoff_chapter": {"type": "integer", "minimum": 1},
                "draft_dir": {"type": "string"},
                "file_template": {"type": "string"},
                "agent_id": {"type": "string"},
                "required_actions": {"type": "array", "items": {"type": "string"}},
                "expected_context_hash": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "start_chapter", "end_chapter"],
        ),
    },
    {
        "name": "build_chapter_range_acceptance_plan",
        "description": "Build a read-only post-acceptance write plan for a range of draft chapters, including pre-write review, per-chapter write-session guard guidance, author-confirmation gates, and ordered writer commands.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "start_chapter": {"type": "integer", "minimum": 1},
                "end_chapter": {"type": "integer", "minimum": 1},
                "handoff_chapter": {"type": "integer", "minimum": 1},
                "draft_dir": {"type": "string"},
                "file_template": {"type": "string"},
                "author_confirmation": {"type": "string"},
                "agent_id": {"type": "string"},
                "required_actions": {"type": "array", "items": {"type": "string"}},
                "expected_context_hash": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
                "include_context": {"type": "boolean"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir", "start_chapter", "end_chapter"],
        ),
    },
    {
        "name": "update_after_write",
        "description": "Create post-write archive suggestions and update the handoff file after a chapter is accepted.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
            }),
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "propose_after_write_updates",
        "description": "Save structured post-write updates for confirmation before they become durable continuity state.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
                "updates": {"type": "object"},
            }),
            ["project_dir", "chapter", "file"],
        ),
    },
    {
        "name": "build_proposal_template",
        "description": "Build an empty post-write proposal template with chapter context and evidence requirements.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "file": {"type": "string"},
            },
            ["project_dir", "chapter"],
        ),
    },
    {
        "name": "get_proposal_schema",
        "description": "Return the JSON Schema for post-write proposal updates.",
        "inputSchema": schema({}, []),
    },
    {
        "name": "validate_proposal",
        "description": "Validate a post-write update proposal for evidence and conflicts before applying it.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "proposal_id": {"type": "string"},
                "updates": {"type": "object"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "diff_proposal",
        "description": "Preview which facts, characters, debts, and handoff fields a proposal would change.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "proposal_id": {"type": "string"},
                "updates": {"type": "object"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_proposal_review",
        "description": "Build a read-only author review packet for a saved or inline post-write proposal.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "proposal_id": {"type": "string"},
                "updates": {"type": "object"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_proposal_readiness",
        "description": "Build a read-only readiness gate for a saved or inline post-write proposal before durable apply.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "proposal_id": {"type": "string"},
                "updates": {"type": "object"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "apply_after_write_updates",
        "description": "Apply a confirmed post-write update proposal to facts, characters, debts, and handoff.",
        "inputSchema": schema(
            with_author_confirmation({
                "project_dir": {"type": "string"},
                "proposal_id": {"type": "string"},
                "updates": {"type": "object"},
            }),
            ["project_dir"],
        ),
    },
    {
        "name": "reject_proposal",
        "description": "Reject a saved post-write update proposal and record the reason.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "proposal_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["project_dir", "proposal_id"],
        ),
    },
    {
        "name": "create_snapshot",
        "description": "Create a rollback snapshot of .novel-agent state files.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "list_snapshots",
        "description": "List rollback snapshots for this .novel-agent project.",
        "inputSchema": schema({"project_dir": {"type": "string"}}, ["project_dir"]),
    },
    {
        "name": "list_audit",
        "description": "List audit log entries for .novel-agent writes.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "restore_snapshot",
        "description": "Restore .novel-agent state from a rollback snapshot. A backup snapshot is created before restore.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "snapshot_id": {"type": "string"},
            },
            ["project_dir", "snapshot_id"],
        ),
    },
    {
        "name": "list_open_threads",
        "description": "List visible unresolved plot debts, chapter contracts, conflicts, and the latest handoff.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "build_handoff_report",
        "description": "Build a handoff report and next-agent brief, including required workflow, startup commands, write boundaries, and pending continuity work.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1},
                "include_context": {"type": "boolean"},
            },
            ["project_dir"],
        ),
    },
    {
        "name": "doctor",
        "description": "Check kit installation and optional project .novel-agent state health.",
        "inputSchema": schema(
            {
                "project_dir": {"type": "string"},
                "install_config": {"type": "string"},
                "start_mcp_test": {"type": "boolean"},
            },
            [],
        ),
    },
]


def tool_map() -> dict[str, Callable[[Json], Any]]:
    return {
        "init_project": lambda args: cli.init_project(
            args["project_dir"],
            title=args.get("title"),
            target_chapters=args.get("target_chapters"),
            target_words=args.get("target_words"),
            force=bool(args.get("force", False)),
        ),
        "init_wizard": lambda args: cli.init_wizard(
            args["project_dir"],
            title=args.get("title"),
            target_chapters=args.get("target_chapters"),
            target_words=args.get("target_words"),
            required_phrases=args.get("required_phrases") or [],
            forbidden_phrases=args.get("forbidden_phrases") or [],
            future_markers=args.get("future_markers") or [],
            rule_notes=args.get("rule_notes") or [],
            style_note=args.get("style_note", ""),
            source_summary=args.get("source_summary", ""),
            source_path=args.get("source_path", ""),
            chapter_range=args.get("chapter_range", ""),
            agent_id=args.get("agent_id", ""),
            platform=args.get("platform", ""),
            capabilities=args.get("capabilities") or [],
            agent_notes=args.get("agent_notes", ""),
            import_gaoxia=bool(args.get("import_gaoxia", False)),
            append_import=bool(args.get("append_import", False)),
            dry_run=bool(args.get("dry_run", False)),
            force=bool(args.get("force", False)),
        ),
        "quickstart_project": lambda args: cli.quickstart_project(
            args["project_dir"],
            source=args.get("source", "auto"),
            title=args.get("title"),
            target_chapters=args.get("target_chapters"),
            target_words=args.get("target_words"),
            required_phrases=args.get("required_phrases") or [],
            forbidden_phrases=args.get("forbidden_phrases") or [],
            future_markers=args.get("future_markers") or [],
            rule_notes=args.get("rule_notes") or [],
            style_note=args.get("style_note", ""),
            source_summary=args.get("source_summary", ""),
            source_path=args.get("source_path", ""),
            chapter_range=args.get("chapter_range", ""),
            agent_id=args.get("agent_id", ""),
            platform=args.get("platform", "generic"),
            mode=args.get("mode", "writer"),
            capabilities=args.get("capabilities") or [],
            agent_notes=args.get("agent_notes", ""),
            append_import=bool(args.get("append_import", False)),
            chapter=args.get("chapter"),
            install_config=args.get("install_config", ""),
            start_mcp_test=bool(args.get("start_mcp_test", False)),
            summary_only=bool(args.get("summary_only", True)),
            dry_run=bool(args.get("dry_run", False)),
            force=bool(args.get("force", False)),
        ),
        "import_gaoxia_project": lambda args: cli.import_gaoxia_project(
            args["project_dir"],
            include_chapters=bool(args.get("include_chapters", True)),
            include_vault=bool(args.get("include_vault", True)),
            include_narrative_state=bool(args.get("include_narrative_state", True)),
            max_vault_notes=int(args.get("max_vault_notes") or 120),
            append=bool(args.get("append", False)),
            dry_run=bool(args.get("dry_run", False)),
            report_path=args.get("report_path", ""),
        ),
        "audit_gaoxia_import": lambda args: cli.build_gaoxia_import_audit(
            args["project_dir"],
            chapter=args.get("chapter"),
            max_vault_notes=int(args.get("max_vault_notes") or 120),
            include_context=bool(args.get("include_context", False)),
        ),
        "export_state_package": lambda args: cli.export_state_package(args["project_dir"], output=args.get("output", "")),
        "import_state_package": lambda args: cli.import_state_package(
            args["project_dir"],
            package=args["package"],
            force=bool(args.get("force", False)),
        ),
        "get_agent_template": lambda args: cli.build_agent_template(
            platform=args.get("platform", "generic"),
            mode=args.get("mode", "writer"),
        ),
        "explain_tool_visibility": lambda args: cli.explain_tool_visibility(
            args["tool_name"],
            mode=args.get("mode", "read-only"),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            all_tool_names={str(tool.get("name") or "") for tool in TOOLS},
        ),
        "build_desktop_setup_guide": lambda args: cli.build_desktop_setup_guide(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
        ),
        "build_desktop_onboarding_guide": lambda args: cli.build_desktop_onboarding_guide(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            source=args.get("source", "auto"),
            chapter=args.get("chapter"),
            evidence_file=args.get("evidence_file", "desktop-evidence.txt"),
            results_file=args.get("results_file", ""),
        ),
        "verify_desktop_setup": lambda args: cli.verify_desktop_setup(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            start_mcp_test=bool(args.get("start_mcp_test", False)),
        ),
        "build_desktop_next_step": lambda args: cli.build_desktop_next_step(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            chapter=args.get("chapter"),
            include_session=bool(args.get("include_session", False)),
        ),
        "build_desktop_diagnostics": lambda args: cli.build_desktop_diagnostics(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            chapter=args.get("chapter"),
            pack_dir=args.get("pack_dir", ""),
            include_session=bool(args.get("include_session", False)),
        ),
        "build_agent_startup_prompt": lambda args: cli.build_agent_startup_prompt(
            args["project_dir"],
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            chapter=args.get("chapter"),
        ),
        "build_desktop_client_checklist": lambda args: cli.build_desktop_client_checklist(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            chapter=args.get("chapter"),
        ),
        "build_source_intake_template": lambda args: cli.build_source_intake_template(
            args["project_dir"],
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
        ),
        "validate_source_intake": lambda args: cli.validate_source_intake_file(
            args["project_dir"],
            intake_path=args["intake"],
        ),
        "ingest_desktop_client_evidence": lambda args: cli.ingest_desktop_client_evidence(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            chapter=args.get("chapter"),
            evidence_text=args.get("evidence_text", ""),
        ),
        "sanitize_desktop_evidence": lambda args: cli.sanitize_desktop_evidence(
            args.get("project_dir"),
            evidence_text=args.get("evidence_text", ""),
            max_line_chars=args.get("max_line_chars", cli.DESKTOP_EVIDENCE_SANITIZE_MAX_LINE_CHARS),
            max_total_chars=args.get("max_total_chars", cli.DESKTOP_EVIDENCE_SANITIZE_MAX_TOTAL_CHARS),
        ),
        "build_desktop_results_doctor": lambda args: cli.build_desktop_results_doctor(
            args["project_dir"],
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            results=args.get("results"),
            install_config=args.get("install_config", ""),
            chapter=args.get("chapter"),
            results_reference=args.get("results_reference", "<results.json>"),
        ),
        "build_desktop_evidence_packet": lambda args: cli.build_desktop_evidence_packet(
            args["project_dir"],
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            evidence_text=args.get("evidence_text", ""),
            results=args.get("results") if "results" in args else None,
            install_config=args.get("install_config", ""),
            chapter=args.get("chapter"),
            results_reference=args.get("results_reference", ""),
        ),
        "build_desktop_evidence_review": lambda args: cli.build_desktop_evidence_review(
            args["project_dir"],
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            evidence_text=args.get("evidence_text", ""),
            results=args.get("results") if "results" in args else None,
            install_config=args.get("install_config", ""),
            chapter=args.get("chapter"),
            results_reference=args.get("results_reference", ""),
        ),
        "build_desktop_evidence_gaps": lambda args: cli.build_desktop_evidence_gaps(
            args["project_dir"],
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            evidence_text=args.get("evidence_text", ""),
            results=args.get("results") if "results" in args else None,
            install_config=args.get("install_config", ""),
            chapter=args.get("chapter"),
            results_reference=args.get("results_reference", ""),
        ),
        "build_desktop_evidence_session": lambda args: cli.build_desktop_evidence_session(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            chapter=args.get("chapter"),
            evidence_file=args.get("evidence_file", "desktop-evidence.txt"),
            results_file=args.get("results_file", ""),
            imported_results_file=args.get("imported_results_file", ""),
        ),
        "build_desktop_evidence_runbook": lambda args: cli.build_desktop_evidence_runbook(
            args.get("project_dir"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            install_target=args.get("install_target", ""),
            tool_profile=args.get("tool_profile", cli.DEFAULT_MCP_TOOL_PROFILE),
            chapter=args.get("chapter"),
            evidence_file=args.get("evidence_file", "desktop-evidence.txt"),
            results_file=args.get("results_file", ""),
            imported_results_file=args.get("imported_results_file", ""),
        ),
        "build_author_actions": lambda args: cli.build_author_actions_from_directory(args["pack_dir"]),
        "build_acceptance_review": lambda args: cli.build_acceptance_review_from_directory(args["pack_dir"]),
        "build_desktop_pack_readiness": lambda args: cli.build_desktop_pack_readiness_from_directory(args["pack_dir"]),
        "build_desktop_local_summary": lambda args: cli.build_desktop_local_summary_from_directory(args["pack_dir"]),
        "build_desktop_user_steps": lambda args: cli.build_desktop_user_steps_from_directory(args["pack_dir"]),
        "build_desktop_pack_freshness": lambda args: cli.build_desktop_pack_freshness_from_directory(args["pack_dir"]),
        "build_chapter_session_freshness": lambda args: cli.build_chapter_session_freshness_from_directory(args["pack_dir"]),
        "build_desktop_pack_schema_check": lambda args: cli.build_desktop_pack_schema_check_from_directory(args["pack_dir"]),
        "build_starter_brief": lambda args: cli.build_starter_brief_from_directory(args["pack_dir"]),
        "build_agent_takeover": lambda args: cli.build_agent_takeover_from_directory(args["pack_dir"]),
        "build_desktop_troubleshooting": lambda args: cli.build_desktop_pack_troubleshooting_from_directory(args["pack_dir"]),
        "build_writer_mode_readiness": lambda args: cli.build_writer_mode_readiness(
            args["project_dir"],
            platform=args.get("platform", "codex"),
            install_config=args.get("install_config", ""),
            chapter=args.get("chapter"),
            author_confirmation=args.get("author_confirmation", ""),
        ),
        "build_author_review_queue": lambda args: cli.build_author_review_queue(
            args["project_dir"],
            chapter=args.get("chapter"),
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            install_config=args.get("install_config", ""),
            start_chapter=args.get("start_chapter"),
            end_chapter=args.get("end_chapter"),
            handoff_chapter=args.get("handoff_chapter"),
            draft_dir=args.get("draft_dir", "chapters"),
            file_template=args.get("file_template", "{chapter:03d}.md"),
            author_confirmation=args.get("author_confirmation", ""),
            include_writer_mode=bool(args.get("include_writer_mode", False)),
            limit=int(args.get("limit") or 20),
        ),
        "record_desktop_client_check": lambda args: cli.record_desktop_client_check(
            args["project_dir"],
            platform=args.get("platform", "codex"),
            mode=args.get("mode", "read-only"),
            results=args.get("results"),
            install_config=args.get("install_config", ""),
            chapter=args.get("chapter"),
            agent_id=args.get("agent_id", ""),
            client_version=args.get("client_version", ""),
            notes=args.get("notes", ""),
        ),
        "list_desktop_client_checks": lambda args: cli.list_desktop_client_checks(
            args["project_dir"],
            platform=args.get("platform", ""),
            limit=int(args.get("limit") or 20),
        ),
        "get_desktop_verification_matrix": lambda args: cli.build_desktop_verification_matrix(
            args["project_dir"],
            platform=args.get("platform", ""),
            platforms=args.get("platforms"),
            mode=args.get("mode", ""),
            modes=args.get("modes"),
            chapter=args.get("chapter"),
        ),
        "record_agent_activity": lambda args: cli.record_agent_activity(
            args["project_dir"],
            agent_id=args.get("agent_id", ""),
            platform=args.get("platform", ""),
            action=args.get("action", ""),
            chapter=args.get("chapter"),
            summary=args.get("summary", ""),
            context_source=args.get("context_source", ""),
            context_hash=args.get("context_hash", ""),
            context_file=args.get("context_file", ""),
            files=args.get("files"),
            status=args.get("status", "done"),
            handoff_to=args.get("handoff_to", ""),
            notes=args.get("notes", ""),
        ),
        "list_agent_activity": lambda args: cli.list_agent_activity(
            args["project_dir"],
            agent_id=args.get("agent_id", ""),
            chapter=args.get("chapter"),
            action=args.get("action", ""),
            limit=int(args.get("limit") or 20),
        ),
        "build_agent_activity_report": lambda args: cli.build_agent_activity_report(
            args["project_dir"],
            agent_id=args.get("agent_id", ""),
            chapter=args.get("chapter"),
            required_actions=args.get("required_actions"),
            limit=int(args.get("limit") or 20),
        ),
        "build_handoff_integrity_report": lambda args: cli.build_handoff_integrity_report(
            args["project_dir"],
            agent_id=args.get("agent_id", ""),
            chapter=args.get("chapter"),
            required_actions=args.get("required_actions"),
            expected_context_hash=args.get("expected_context_hash", ""),
            limit=int(args.get("limit") or 20),
            include_context=bool(args.get("include_context", False)),
        ),
        "build_handoff_range_report": lambda args: cli.build_handoff_range_report(
            args["project_dir"],
            start_chapter=int(args["start_chapter"]),
            end_chapter=int(args["end_chapter"]),
            handoff_chapter=args.get("handoff_chapter"),
            draft_dir=args.get("draft_dir", "chapters"),
            file_template=args.get("file_template", "{chapter:03d}.md"),
            agent_id=args.get("agent_id", ""),
            required_actions=args.get("required_actions"),
            expected_context_hash=args.get("expected_context_hash", ""),
            limit=int(args.get("limit") or 20),
            include_context=bool(args.get("include_context", False)),
        ),
        "get_project_status": lambda args: cli.project_status(
            args["project_dir"],
            chapter=args.get("chapter"),
            platform=args.get("platform", ""),
            install_config=args.get("install_config", ""),
        ),
        "build_continuity_audit": lambda args: cli.build_continuity_audit(
            args["project_dir"],
            chapter=args.get("chapter"),
            platform=args.get("platform", ""),
            include_context=bool(args.get("include_context", False)),
        ),
        "verify_handoff_readiness": lambda args: cli.verify_handoff_readiness(
            args["project_dir"],
            chapter=args.get("chapter"),
            incoming_agent_id=args.get("incoming_agent_id", ""),
            incoming_platform=args.get("incoming_platform", ""),
            outgoing_agent_id=args.get("outgoing_agent_id", ""),
            mode=args.get("mode", "writer"),
            required_capabilities=args.get("required_capabilities"),
            install_config=args.get("install_config", ""),
            include_context=bool(args.get("include_context", False)),
        ),
        "add_source_summary": lambda args: cli.add_source_summary(
            args["project_dir"],
            summary=args["summary"],
            source_path=args.get("source_path", ""),
            title=args.get("title", ""),
            chapter_range=args.get("chapter_range", ""),
            required_phrases=args.get("required_phrases"),
            forbidden_phrases=args.get("forbidden_phrases"),
            credibility=args.get("credibility", "agent-summary"),
            reveal_after_chapter=args.get("reveal_after_chapter"),
        ),
        "declare_agent_capabilities": lambda args: cli.declare_agent_capabilities(
            args["project_dir"],
            agent_id=args["agent_id"],
            platform=args.get("platform", ""),
            capabilities=args.get("capabilities"),
            notes=args.get("notes", ""),
        ),
        "list_agent_capabilities": lambda args: cli.list_agent_capabilities(args["project_dir"]),
        "add_research_note": lambda args: cli.add_research_note(
            args["project_dir"],
            summary=args["summary"],
            url=args.get("url", ""),
            title=args.get("title", ""),
            chapter_range=args.get("chapter_range", ""),
            required_phrases=args.get("required_phrases"),
            forbidden_phrases=args.get("forbidden_phrases"),
            reliability=args.get("reliability", "unrated"),
            reveal_after_chapter=args.get("reveal_after_chapter"),
        ),
        "resolve_conflict": lambda args: cli.resolve_conflict(
            args["project_dir"],
            conflict=args["conflict"],
            chosen=args["chosen"],
            reason=args.get("reason", ""),
            options=args.get("options"),
            chapter_range=args.get("chapter_range", ""),
            scope=args.get("scope", "canon"),
            viewpoint=args.get("viewpoint", ""),
            effective_until_chapter=args.get("effective_until_chapter"),
        ),
        "add_fact": lambda args: cli.add_fact(
            args["project_dir"],
            kind=args["kind"],
            subject=args["subject"],
            predicate=args["predicate"],
            object_value=args["object"],
            chapter_range=args.get("chapter_range", ""),
            source_ids=args.get("source_ids"),
            source_path=args.get("source_path", ""),
            confidence=args.get("confidence", "agent-confirmed"),
            status=args.get("status", "active"),
            reveal_after_chapter=args.get("reveal_after_chapter"),
            required_phrases=args.get("required_phrases"),
            forbidden_phrases=args.get("forbidden_phrases"),
            future_markers=args.get("future_markers"),
        ),
        "update_fact": lambda args: cli.update_fact(
            args["project_dir"],
            fact_id=args["fact_id"],
            kind=args.get("kind"),
            subject=args.get("subject"),
            predicate=args.get("predicate"),
            object_value=args.get("object"),
            chapter_range=args.get("chapter_range"),
            source_ids=args.get("source_ids"),
            source_path=args.get("source_path"),
            confidence=args.get("confidence"),
            status=args.get("status"),
            reveal_after_chapter=args.get("reveal_after_chapter"),
            required_phrases=args.get("required_phrases"),
            forbidden_phrases=args.get("forbidden_phrases"),
            future_markers=args.get("future_markers"),
        ),
        "list_facts": lambda args: cli.list_facts(
            args["project_dir"],
            chapter=args.get("chapter"),
            kind=args.get("kind", ""),
            include_inactive=bool(args.get("include_inactive", False)),
        ),
        "supersede_fact": lambda args: cli.supersede_fact(
            args["project_dir"],
            fact_id=args["fact_id"],
            replacement_fact_id=args.get("replacement_fact_id", ""),
            reason=args.get("reason", ""),
        ),
        "build_chapter_context": lambda args: cli.build_chapter_context(args["project_dir"], chapter=int(args["chapter"])),
        "build_chapter_context_brief": lambda args: cli.build_chapter_context_brief(args["project_dir"], chapter=int(args["chapter"])),
        "prepare_chapter_session": lambda args: cli.prepare_chapter_session(
            args["project_dir"],
            chapter=int(args["chapter"]) if args.get("chapter") is not None else None,
            platform=args.get("platform", "generic"),
            mode=args.get("mode", "writer"),
            include_context=bool(args.get("include_context", True)),
        ),
        "check_write_session": lambda args: cli.build_write_session_check(
            args["project_dir"],
            chapter=int(args["chapter"]) if args.get("chapter") is not None else None,
            expected_project_id=args.get("expected_project_id", ""),
            expected_state_sha256=args.get("expected_state_sha256", ""),
            expected_context_hash=args.get("expected_context_hash", ""),
        ),
        "record_chapter": lambda args: cli.record_chapter(
            args["project_dir"],
            chapter=int(args["chapter"]),
            file=args["file"],
            title=args.get("title", ""),
            summary=args.get("summary", ""),
            handoff=args.get("handoff", ""),
        ),
        "record_style_sample": lambda args: cli.record_style_sample(
            args["project_dir"],
            chapter=int(args["chapter"]),
            file=args["file"],
            note=args.get("note", ""),
        ),
        "check_chapter": lambda args: cli.check_chapter(args["project_dir"], chapter=int(args["chapter"]), file=args["file"]),
        "diff_chapter_contract": lambda args: cli.diff_chapter_contract(args["project_dir"], chapter=int(args["chapter"]), file=args["file"]),
        "build_chapter_readiness": lambda args: cli.build_chapter_readiness(args["project_dir"], chapter=int(args["chapter"]), file=args["file"]),
        "build_chapter_revision_prompt": lambda args: cli.build_chapter_revision_prompt(
            args["project_dir"],
            chapter=int(args["chapter"]),
            file=args["file"],
            include_context=bool(args.get("include_context", False)),
        ),
        "build_chapter_revision_comparison": lambda args: cli.build_chapter_revision_comparison(
            args["project_dir"],
            chapter=int(args["chapter"]),
            before_file=args["before_file"],
            after_file=args["after_file"],
            include_context=bool(args.get("include_context", False)),
        ),
        "build_chapter_delivery_packet": lambda args: cli.build_chapter_delivery_packet(
            args["project_dir"],
            chapter=int(args["chapter"]),
            file=args["file"],
            include_context=bool(args.get("include_context", False)),
        ),
        "build_chapter_acceptance_plan": lambda args: cli.build_chapter_acceptance_plan(
            args["project_dir"],
            chapter=int(args["chapter"]),
            file=args["file"],
            handoff_chapter=int(args["handoff_chapter"]) if args.get("handoff_chapter") else None,
            author_confirmation=args.get("author_confirmation", ""),
            include_context=bool(args.get("include_context", False)),
        ),
        "build_chapter_range_readiness": lambda args: cli.build_chapter_range_readiness(
            args["project_dir"],
            start_chapter=int(args["start_chapter"]),
            end_chapter=int(args["end_chapter"]),
            draft_dir=args.get("draft_dir", "chapters"),
            file_template=args.get("file_template", "{chapter:03d}.md"),
        ),
        "build_chapter_range_delivery_packet": lambda args: cli.build_chapter_range_delivery_packet(
            args["project_dir"],
            start_chapter=int(args["start_chapter"]),
            end_chapter=int(args["end_chapter"]),
            handoff_chapter=int(args["handoff_chapter"]) if args.get("handoff_chapter") else None,
            draft_dir=args.get("draft_dir", "chapters"),
            file_template=args.get("file_template", "{chapter:03d}.md"),
            agent_id=args.get("agent_id", ""),
            required_actions=args.get("required_actions"),
            expected_context_hash=args.get("expected_context_hash", ""),
            limit=int(args.get("limit", 20)),
            include_context=bool(args.get("include_context", False)),
        ),
        "build_chapter_range_acceptance_plan": lambda args: cli.build_chapter_range_acceptance_plan(
            args["project_dir"],
            start_chapter=int(args["start_chapter"]),
            end_chapter=int(args["end_chapter"]),
            handoff_chapter=int(args["handoff_chapter"]) if args.get("handoff_chapter") else None,
            draft_dir=args.get("draft_dir", "chapters"),
            file_template=args.get("file_template", "{chapter:03d}.md"),
            author_confirmation=args.get("author_confirmation", ""),
            agent_id=args.get("agent_id", ""),
            required_actions=args.get("required_actions"),
            expected_context_hash=args.get("expected_context_hash", ""),
            limit=int(args.get("limit", 20)),
            include_context=bool(args.get("include_context", False)),
        ),
        "update_after_write": lambda args: cli.update_after_write(args["project_dir"], chapter=int(args["chapter"]), file=args["file"]),
        "propose_after_write_updates": lambda args: cli.propose_after_write_updates(
            args["project_dir"],
            chapter=int(args["chapter"]),
            file=args["file"],
            updates=args.get("updates") or {},
        ),
        "build_proposal_template": lambda args: cli.build_proposal_template(
            args["project_dir"],
            chapter=int(args["chapter"]),
            file=args.get("file", ""),
        ),
        "get_proposal_schema": lambda args: cli.get_proposal_schema(),
        "validate_proposal": lambda args: cli.validate_proposal(
            args["project_dir"],
            proposal_id=args.get("proposal_id", ""),
            updates=args.get("updates") or {},
        ),
        "diff_proposal": lambda args: cli.diff_proposal(
            args["project_dir"],
            proposal_id=args.get("proposal_id", ""),
            updates=args.get("updates") or {},
        ),
        "build_proposal_review": lambda args: cli.build_proposal_review(
            args["project_dir"],
            proposal_id=args.get("proposal_id", ""),
            updates=args.get("updates") or {},
        ),
        "build_proposal_readiness": lambda args: cli.build_proposal_readiness(
            args["project_dir"],
            proposal_id=args.get("proposal_id", ""),
            updates=args.get("updates") or {},
        ),
        "apply_after_write_updates": lambda args: cli.apply_after_write_updates(
            args["project_dir"],
            proposal_id=args.get("proposal_id", ""),
            updates=args.get("updates") or {},
        ),
        "reject_proposal": lambda args: cli.reject_proposal(
            args["project_dir"],
            proposal_id=args["proposal_id"],
            reason=args.get("reason", ""),
        ),
        "create_snapshot": lambda args: cli.create_snapshot(args["project_dir"], reason=args.get("reason", "manual")),
        "list_snapshots": lambda args: cli.list_snapshots(args["project_dir"]),
        "list_audit": lambda args: cli.list_audit(args["project_dir"], limit=int(args.get("limit") or 20)),
        "restore_snapshot": lambda args: cli.restore_snapshot(args["project_dir"], snapshot_id=args["snapshot_id"]),
        "list_open_threads": lambda args: cli.list_open_threads(args["project_dir"], chapter=args.get("chapter")),
        "build_handoff_report": lambda args: cli.build_handoff_report(
            args["project_dir"],
            chapter=args.get("chapter"),
            include_context=bool(args.get("include_context", False)),
        ),
        "doctor": lambda args: cli.doctor(
            args.get("project_dir"),
            install_config=args.get("install_config", ""),
            start_mcp_test=bool(args.get("start_mcp_test", False)),
        ),
    }


def should_lock_tool(name: str, args: Json) -> bool:
    if name not in MUTATING_TOOLS:
        return False
    if name == "quickstart_project":
        return False
    if name == "import_state_package":
        return False
    if name == "import_gaoxia_project" and bool(args.get("dry_run", False)):
        return False
    if name == "init_wizard" and bool(args.get("dry_run", False)):
        return False
    return "project_dir" in args


def normalize_tool_profile(value: str | None) -> str:
    profile = str(value or "full").strip().lower()
    if not profile:
        profile = "full"
    if profile not in SUPPORTED_TOOL_PROFILES:
        raise cli.KitError(f"Unsupported MCP tool profile: {value}. Use core, writing, or full.")
    return profile


def tool_profile_names() -> set[str]:
    if TOOL_PROFILE == "core":
        return CORE_PROFILE_TOOLS
    if TOOL_PROFILE == "writing":
        return WRITING_PROFILE_TOOLS
    return {str(tool.get("name") or "") for tool in TOOLS}


def visible_tool_names() -> set[str]:
    names = tool_profile_names()
    if READ_ONLY_MODE:
        names = {name for name in names if name not in MUTATING_TOOLS}
    return names


def visible_tools() -> list[Json]:
    names = visible_tool_names()
    return [tool for tool in TOOLS if str(tool.get("name") or "") in names]


def run_tool(name: str, args: Json, tools: dict[str, Callable[[Json], Any]]) -> Any:
    if READ_ONLY_MODE and name in MUTATING_TOOLS:
        raise cli.KitError(f"Read-only MCP mode blocks write tool: {name}")
    if should_lock_tool(name, args):
        return cli.run_state_mutation(
            args["project_dir"],
            name,
            lambda: tools[name](args),
            create=name in {"init_project", "init_wizard", "quickstart_project", "import_gaoxia_project"},
            migrate_before=name not in {"init_project", "init_wizard", "quickstart_project", "import_gaoxia_project"},
            author_confirmation=args.get("author_confirmation", ""),
            expected_project_id=args.get("expected_project_id", ""),
            expected_state_sha256=args.get("expected_state_sha256", ""),
            expected_context_hash=args.get("expected_context_hash", ""),
            guard_chapter=args.get("chapter"),
        )
    return tools[name](args)


def handle_request(request: Json) -> Json | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "long-novel-agent",
                    "version": cli.KIT_VERSION,
                    "readOnly": READ_ONLY_MODE,
                    "toolProfile": TOOL_PROFILE,
                },
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": visible_tools()}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise RpcError(-32602, "Tool arguments must be an object")
        tools = tool_map()
        if name not in tools:
            raise RpcError(-32601, f"Unknown tool: {name}")
        if READ_ONLY_MODE and name in MUTATING_TOOLS:
            raise cli.KitError(f"Read-only MCP mode blocks write tool: {name}")
        if str(name) not in visible_tool_names():
            raise RpcError(-32601, f"Tool is not available in MCP tool profile '{TOOL_PROFILE}': {name}")
        payload = run_tool(str(name), args, tools)
        if name == "build_chapter_context" and args.get("format") == "markdown":
            text = cli.render_context_markdown(payload)
        elif name == "build_chapter_context_brief" and args.get("format") == "markdown":
            text = cli.render_context_brief_markdown(payload)
        elif name == "build_desktop_setup_guide" and args.get("format") == "markdown":
            text = cli.render_desktop_setup_markdown(payload)
        elif name == "build_desktop_onboarding_guide" and args.get("format") == "markdown":
            text = cli.render_desktop_onboarding_markdown(payload)
        elif name == "verify_desktop_setup" and args.get("format") == "markdown":
            text = cli.render_desktop_verification_markdown(payload)
        elif name == "build_desktop_next_step" and args.get("format") == "markdown":
            text = cli.render_desktop_next_step_markdown(payload)
        elif name == "build_desktop_diagnostics" and args.get("format") == "markdown":
            text = cli.render_desktop_diagnostics_markdown(payload)
        elif name == "explain_tool_visibility" and args.get("format") == "markdown":
            text = cli.render_tool_visibility_markdown(payload)
        elif name == "build_agent_startup_prompt" and args.get("format") == "markdown":
            text = cli.render_agent_startup_prompt_markdown(payload)
        elif name == "build_desktop_client_checklist" and args.get("format") == "markdown":
            text = cli.render_desktop_client_checklist_markdown(payload)
        elif name == "build_source_intake_template" and args.get("format") == "markdown":
            text = cli.render_source_intake_template_markdown(payload)
        elif name == "validate_source_intake" and args.get("format") == "markdown":
            text = cli.render_source_intake_report_markdown(payload)
        elif name == "ingest_desktop_client_evidence" and args.get("format") == "markdown":
            text = cli.render_desktop_evidence_import_markdown(payload)
        elif name == "sanitize_desktop_evidence" and args.get("format") == "markdown":
            text = cli.render_desktop_evidence_sanitizer_markdown(payload)
        elif name == "build_desktop_results_doctor" and args.get("format") == "markdown":
            text = cli.render_desktop_results_doctor_markdown(payload)
        elif name == "build_desktop_evidence_packet" and args.get("format") == "markdown":
            text = cli.render_desktop_evidence_packet_markdown(payload)
        elif name == "build_desktop_evidence_review" and args.get("format") == "markdown":
            text = cli.render_desktop_evidence_review_markdown(payload)
        elif name == "build_desktop_evidence_review" and args.get("format") == "html":
            text = cli.render_desktop_evidence_review_html(payload)
        elif name == "build_desktop_evidence_gaps" and args.get("format") == "markdown":
            text = cli.render_desktop_evidence_gaps_markdown(payload)
        elif name == "build_desktop_evidence_session" and args.get("format") == "markdown":
            text = cli.render_desktop_evidence_session_markdown(payload)
        elif name == "build_desktop_evidence_runbook" and args.get("format") == "markdown":
            text = cli.render_desktop_evidence_runbook_markdown(payload)
        elif name == "build_author_actions" and args.get("format") == "markdown":
            text = cli.render_author_actions_markdown(payload)
        elif name == "build_acceptance_review" and args.get("format") == "markdown":
            text = cli.render_acceptance_review_markdown(payload)
        elif name == "build_acceptance_review" and args.get("format") == "html":
            text = cli.render_acceptance_review_html(payload)
        elif name == "build_desktop_pack_readiness" and args.get("format") == "markdown":
            text = cli.render_desktop_pack_readiness_markdown(payload)
        elif name == "build_desktop_local_summary" and args.get("format") == "markdown":
            text = cli.render_desktop_local_summary_markdown(payload)
        elif name == "build_desktop_user_steps" and args.get("format") == "markdown":
            text = cli.render_desktop_user_steps_markdown(payload)
        elif name == "build_desktop_user_steps" and args.get("format") == "html":
            text = cli.render_desktop_user_steps_html(payload)
        elif name == "build_desktop_pack_freshness" and args.get("format") == "markdown":
            text = cli.render_desktop_pack_freshness_markdown(payload)
        elif name == "build_chapter_session_freshness" and args.get("format") == "markdown":
            text = cli.render_chapter_session_freshness_markdown(payload)
        elif name == "build_desktop_pack_schema_check" and args.get("format") == "markdown":
            text = cli.render_desktop_pack_schema_check_markdown(payload)
        elif name == "build_starter_brief" and args.get("format") == "markdown":
            text = cli.render_starter_brief_markdown(payload)
        elif name == "build_agent_takeover" and args.get("format") == "markdown":
            text = cli.render_agent_takeover_markdown(payload)
        elif name == "build_desktop_troubleshooting" and args.get("format") == "markdown":
            text = cli.render_desktop_pack_troubleshooting_markdown(payload)
        elif name == "build_writer_mode_readiness" and args.get("format") == "markdown":
            text = cli.render_writer_mode_readiness_markdown(payload)
        elif name == "build_author_review_queue" and args.get("format") == "markdown":
            text = cli.render_author_review_queue_markdown(payload)
        elif name == "get_desktop_verification_matrix" and args.get("format") == "markdown":
            text = cli.render_desktop_verification_matrix_markdown(payload)
        elif name in {"record_agent_activity", "list_agent_activity"} and args.get("format") == "markdown":
            if payload.get("type") == "agent_activity_record":
                text = cli.render_agent_activity_markdown({"type": "agent_activity", "filters": {}, "records": [payload.get("record")]})
            else:
                text = cli.render_agent_activity_markdown(payload)
        elif name == "build_agent_activity_report" and args.get("format") == "markdown":
            text = cli.render_agent_activity_report_markdown(payload)
        elif name == "build_handoff_integrity_report" and args.get("format") == "markdown":
            text = cli.render_handoff_integrity_markdown(payload)
        elif name == "build_handoff_range_report" and args.get("format") == "markdown":
            text = cli.render_handoff_range_report_markdown(payload)
        elif name == "get_project_status" and args.get("format") == "markdown":
            text = cli.render_project_status_markdown(payload)
        elif name == "build_continuity_audit" and args.get("format") == "markdown":
            text = cli.render_continuity_audit_markdown(payload)
        elif name == "audit_gaoxia_import" and args.get("format") == "markdown":
            text = cli.render_gaoxia_import_audit_markdown(payload)
        elif name == "verify_handoff_readiness" and args.get("format") == "markdown":
            text = cli.render_handoff_readiness_markdown(payload)
        elif name == "build_proposal_review" and args.get("format") == "markdown":
            text = cli.render_proposal_review_markdown(payload)
        elif name == "build_proposal_readiness" and args.get("format") == "markdown":
            text = cli.render_proposal_readiness_markdown(payload)
        elif name == "build_chapter_readiness" and args.get("format") == "markdown":
            text = cli.render_chapter_readiness_markdown(payload)
        elif name == "build_chapter_revision_prompt" and args.get("format") == "markdown":
            text = cli.render_chapter_revision_prompt_markdown(payload)
        elif name == "build_chapter_revision_comparison" and args.get("format") == "markdown":
            text = cli.render_chapter_revision_comparison_markdown(payload)
        elif name == "build_chapter_delivery_packet" and args.get("format") == "markdown":
            text = cli.render_chapter_delivery_markdown(payload)
        elif name == "build_chapter_acceptance_plan" and args.get("format") == "markdown":
            text = cli.render_chapter_acceptance_plan_markdown(payload)
        elif name == "build_chapter_range_readiness" and args.get("format") == "markdown":
            text = cli.render_chapter_range_readiness_markdown(payload)
        elif name == "build_chapter_range_delivery_packet" and args.get("format") == "markdown":
            text = cli.render_chapter_range_delivery_markdown(payload)
        elif name == "build_chapter_range_acceptance_plan" and args.get("format") == "markdown":
            text = cli.render_chapter_range_acceptance_plan_markdown(payload)
        elif name == "quickstart_project" and args.get("format") == "markdown":
            text = cli.render_quickstart_markdown(payload)
        elif name == "prepare_chapter_session" and args.get("format") == "markdown":
            text = cli.render_chapter_session_markdown(payload)
        elif name == "check_write_session" and args.get("format") == "markdown":
            text = cli.render_write_session_check_markdown(payload)
        else:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": payload,
                "isError": False,
            },
        }
    if request_id is None:
        return None
    raise RpcError(-32601, f"Method not found: {method}")


def error_response(request_id: Any, exc: RpcError) -> Json:
    error: Json = {"code": exc.code, "message": exc.message}
    if exc.data is not None:
        error["data"] = exc.data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def send(message: Json) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Long Novel Agent MCP server")
    parser.add_argument("--read-only", action="store_true", help="Expose only read/check tools and reject write tool calls")
    parser.add_argument(
        "--tool-profile",
        choices=sorted(SUPPORTED_TOOL_PROFILES),
        default="",
        help="Expose a smaller local desktop tool set: core, writing, or full. Defaults to full.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    global READ_ONLY_MODE, TOOL_PROFILE
    args = build_parser().parse_args(argv)
    READ_ONLY_MODE = bool(args.read_only or os.environ.get("LONG_NOVEL_AGENT_READ_ONLY") in {"1", "true", "TRUE", "yes", "YES"})
    try:
        TOOL_PROFILE = normalize_tool_profile(args.tool_profile or os.environ.get("LONG_NOVEL_AGENT_TOOL_PROFILE") or "full")
    except cli.KitError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        request_id: Any = None
        try:
            request = json.loads(stripped)
            if not isinstance(request, dict):
                raise RpcError(-32600, "Request must be a JSON object")
            request_id = request.get("id")
            response = handle_request(request)
            if response is not None:
                send(response)
        except RpcError as exc:
            if request_id is not None:
                send(error_response(request_id, exc))
        except cli.KitError as exc:
            if request_id is not None:
                send(error_response(request_id, RpcError(-32603, str(exc))))
        except Exception as exc:  # pragma: no cover - defensive for host agents
            traceback.print_exc(file=sys.stderr)
            if request_id is not None:
                send(error_response(request_id, RpcError(-32603, f"Internal error: {exc}")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
