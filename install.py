#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

SKILL_NAME = "long-novel-agent"
MCP_PLATFORMS = ("generic-json", "codex", "claude-desktop", "cursor")
MCP_TOOL_PROFILES = ("core", "writing", "full")
AUTO_MCP_CONFIG = "auto"
PLATFORM_DOCS = {
    "generic-json": "Generic JSON snippet for MCP clients that accept mcpServers.",
    "codex": "Codex CLI config.toml uses [mcp_servers.<name>] entries.",
    "claude-desktop": "Claude Desktop uses a JSON mcpServers object.",
    "cursor": "Cursor uses an mcp.json file with a JSON mcpServers object.",
}
CODEX_MCP_SECTION_PATTERN = re.compile(r"(?ms)^\[mcp_servers\.long-novel-agent\]\s*.*?(?=^\[|\Z)")


class InstallError(Exception):
    pass


def source_dir() -> Path:
    return Path(__file__).resolve().parent


def shell_join(parts: list[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def agent_platform(platform: str) -> str:
    return "generic" if platform == "generic-json" else platform


def default_target() -> Path:
    return Path.home() / ".codex" / "skills"


def configured_path(env_name: str) -> Path | None:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def default_mcp_config_path(platform: str) -> Path | None:
    if platform == "codex":
        return configured_path("LONG_NOVEL_AGENT_CODEX_CONFIG") or (Path.home() / ".codex" / "config.toml")
    if platform == "cursor":
        return configured_path("LONG_NOVEL_AGENT_CURSOR_CONFIG") or (Path.home() / ".cursor" / "mcp.json")
    return None


def resolve_mcp_config_path(value: str | None, platform: str) -> Path | None:
    if not value:
        return None
    if value.strip().lower() == AUTO_MCP_CONFIG:
        default_path = default_mcp_config_path(platform)
        if default_path is None:
            raise InstallError(f"--mcp-config auto is not available for {platform}. Pass an explicit --mcp-config path.")
        return default_path.expanduser().resolve()
    return Path(value).expanduser().resolve()


def copy_tree(src: Path, dest: Path, *, force: bool, dry_run: bool) -> None:
    if dest.exists():
        if not force:
            raise InstallError(f"Target already exists: {dest}. Use --force to replace it.")
        if not dry_run:
            shutil.rmtree(dest)
    if dry_run:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )


def server_args(dest: Path, *, read_only: bool = False, tool_profile: str = "full") -> list[str]:
    args = [str(dest / "server.py")]
    if read_only:
        args.append("--read-only")
    if tool_profile != "full":
        args.extend(["--tool-profile", tool_profile])
    return args


def mcp_server_entry(dest: Path, *, read_only: bool = True, tool_profile: str = "full") -> dict[str, object]:
    return {
        "command": sys.executable,
        "args": server_args(dest, read_only=read_only, tool_profile=tool_profile),
    }


def mcp_config(dest: Path) -> dict[str, object]:
    return mcp_config_payload(dest, platform="generic-json", read_only=True)


def mcp_config_payload(dest: Path, *, platform: str, read_only: bool = True, tool_profile: str = "full") -> dict[str, object]:
    return {
        "mcpServers": {
            "long-novel-agent": mcp_server_entry(dest, read_only=read_only, tool_profile=tool_profile)
        }
    }


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def mcp_config_text(dest: Path, *, platform: str, read_only: bool = True, tool_profile: str = "full") -> str:
    if platform in {"generic-json", "claude-desktop", "cursor"}:
        payload = mcp_config_payload(dest, platform=platform, read_only=read_only, tool_profile=tool_profile)
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if platform == "codex":
        args = ", ".join(toml_string(item) for item in server_args(dest, read_only=read_only, tool_profile=tool_profile))
        return "\n".join(
            [
                "[mcp_servers.long-novel-agent]",
                f"command = {toml_string(sys.executable)}",
                f"args = [{args}]",
                "",
            ]
        )
    raise InstallError(f"Unsupported MCP platform: {platform}")


def platform_install_hint(
    platform: str,
    config_path: Path | None = None,
    *,
    read_only: bool = True,
    tool_profile: str = "full",
    kit_path: Path | None = None,
) -> dict[str, object]:
    hints: dict[str, object] = {
        "platform": platform,
        "note": PLATFORM_DOCS.get(platform, ""),
        "mcp_mode": "read-only" if read_only else "writer",
        "tool_profile": tool_profile,
    }
    if platform == "codex":
        hints["suggested_config_path"] = str(config_path or default_mcp_config_path(platform))
    elif platform == "claude-desktop":
        hints["suggested_config_path"] = "Pass an explicit Claude Desktop config path after checking the installed app settings."
    elif platform == "cursor":
        hints["suggested_config_path"] = str(config_path or default_mcp_config_path(platform))
    else:
        hints["suggested_config_path"] = str(config_path) if config_path else "Any MCP client config file that accepts mcpServers JSON"
    if config_path and kit_path:
        hints["doctor_command"] = shell_join(
            ["python", kit_path / "cli.py", "doctor", "/path/to/novel", "--install-config", config_path, "--start-mcp-test"]
        )
        hints["quickstart_command"] = shell_join(
            [
                "python",
                kit_path / "cli.py",
                "quickstart",
                "/path/to/novel",
                "--source",
                "auto",
                "--platform",
                agent_platform(platform),
                "--install-config",
                config_path,
                "--start-mcp-test",
            ]
        )
    return hints


def merged_mcp_config_text(path: Path, dest: Path, *, platform: str, read_only: bool, tool_profile: str, force: bool) -> str:
    if not path.exists():
        return mcp_config_text(dest, platform=platform, read_only=read_only, tool_profile=tool_profile)
    current = path.read_text(encoding="utf-8")
    if platform in {"generic-json", "claude-desktop", "cursor"}:
        return merged_json_mcp_config(current, dest, platform=platform, read_only=read_only, tool_profile=tool_profile, force=force)
    if platform == "codex":
        return merged_codex_mcp_config(current, dest, read_only=read_only, tool_profile=tool_profile, force=force)
    raise InstallError(f"Unsupported MCP platform: {platform}")


def merged_json_mcp_config(current: str, dest: Path, *, platform: str, read_only: bool, tool_profile: str, force: bool) -> str:
    if current.strip():
        try:
            payload = json.loads(current)
        except json.JSONDecodeError as exc:
            raise InstallError(f"Existing MCP config is not valid JSON: {exc}") from exc
    else:
        payload = {}
    if not isinstance(payload, dict):
        raise InstallError("Existing MCP config must be a JSON object.")
    servers = payload.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallError("Existing MCP config field mcpServers must be an object.")
    entry = mcp_server_entry(dest, read_only=read_only, tool_profile=tool_profile)
    existing = servers.get("long-novel-agent")
    if existing is not None and existing != entry and not force:
        raise InstallError("MCP config already has long-novel-agent. Use --force to replace that entry.")
    servers["long-novel-agent"] = entry
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def merged_codex_mcp_config(current: str, dest: Path, *, read_only: bool, tool_profile: str, force: bool) -> str:
    snippet = mcp_config_text(dest, platform="codex", read_only=read_only, tool_profile=tool_profile).rstrip() + "\n"
    match = CODEX_MCP_SECTION_PATTERN.search(current)
    if match:
        existing = match.group(0).strip()
        if existing != snippet.strip() and not force:
            raise InstallError("Codex config already has [mcp_servers.long-novel-agent]. Use --force to replace it.")
        return current[: match.start()] + snippet + current[match.end() :]
    prefix = current.rstrip()
    if prefix:
        return prefix + "\n\n" + snippet
    return snippet


def write_mcp_config(path: Path, dest: Path, *, platform: str, read_only: bool, tool_profile: str, force: bool, dry_run: bool) -> None:
    text = merged_mcp_config_text(path, dest, platform=platform, read_only=read_only, tool_profile=tool_profile, force=force)
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def remove_json_mcp_config(current: str) -> tuple[str, bool]:
    if current.strip():
        try:
            payload = json.loads(current)
        except json.JSONDecodeError as exc:
            raise InstallError(f"Existing MCP config is not valid JSON: {exc}") from exc
    else:
        payload = {}
    if not isinstance(payload, dict):
        raise InstallError("Existing MCP config must be a JSON object.")
    servers = payload.get("mcpServers")
    if servers is None:
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n", False
    if not isinstance(servers, dict):
        raise InstallError("Existing MCP config field mcpServers must be an object.")
    removed = servers.pop(SKILL_NAME, None) is not None
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n", removed


def remove_codex_mcp_config(current: str) -> tuple[str, bool]:
    match = CODEX_MCP_SECTION_PATTERN.search(current)
    if not match:
        return current, False
    updated = current[: match.start()] + current[match.end() :]
    updated = re.sub(r"\n{3,}", "\n\n", updated).lstrip("\n")
    if updated and not updated.endswith("\n"):
        updated += "\n"
    return updated, True


def removed_mcp_config_text(path: Path, *, platform: str) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    current = path.read_text(encoding="utf-8")
    if platform in {"generic-json", "claude-desktop", "cursor"}:
        return remove_json_mcp_config(current)
    if platform == "codex":
        return remove_codex_mcp_config(current)
    raise InstallError(f"Unsupported MCP platform: {platform}")


def remove_mcp_config_entry(path: Path, *, platform: str, dry_run: bool) -> bool:
    text, removed = removed_mcp_config_text(path, platform=platform)
    if removed and not dry_run:
        path.write_text(text, encoding="utf-8")
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the long novel agent kit")
    parser.add_argument("--target", default=str(default_target()), help="Skills directory to install into")
    parser.add_argument("--mcp-config", help="Optional MCP config path to merge; use 'auto' for Codex/Cursor defaults")
    parser.add_argument("--mcp-platform", choices=MCP_PLATFORMS, default="generic-json", help="Local desktop MCP config format to generate")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--upgrade", action="store_true", help="Replace the installed skill and existing long-novel-agent MCP entry")
    operation.add_argument("--uninstall", action="store_true", help="Remove the installed skill and long-novel-agent MCP entry")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--read-only-mcp", action="store_true", help="Generate MCP config that starts the server in read-only mode. This is the default.")
    mode.add_argument("--writer-mcp", action="store_true", help="Generate MCP config with write tools enabled")
    parser.add_argument(
        "--tool-profile",
        choices=MCP_TOOL_PROFILES,
        default="full",
        help="MCP server tool set to expose: core, writing, or full. Default keeps existing full tool list.",
    )
    parser.add_argument("--print-mcp-config", action="store_true", help="Print the generated MCP config snippet")
    parser.add_argument("--force", action="store_true", help="Replace an existing install or existing long-novel-agent MCP entry")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without writing files")
    parser.add_argument("--print-path", action="store_true", help="Print this kit directory and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    src = source_dir()
    if args.print_path:
        print(src)
        return 0
    target_root = Path(args.target).expanduser().resolve()
    dest = target_root / SKILL_NAME
    read_only_mcp = not bool(args.writer_mcp)
    try:
        mcp_config_path = resolve_mcp_config_path(args.mcp_config, args.mcp_platform)
        if args.uninstall and args.print_mcp_config:
            raise InstallError("--print-mcp-config is not available with --uninstall.")
        if args.uninstall:
            target_exists = dest.exists()
            config_exists = bool(mcp_config_path and mcp_config_path.exists())
            actions = [f"remove skill {dest}" if target_exists else f"skill not installed at {dest}"]
            if mcp_config_path:
                actions.append(f"remove {args.mcp_platform} MCP config entry -> {mcp_config_path}")
            for action in actions:
                print(("DRY RUN: " if args.dry_run else "") + action)
            mcp_entry_removed = False
            if mcp_config_path:
                mcp_entry_removed = remove_mcp_config_entry(mcp_config_path, platform=args.mcp_platform, dry_run=args.dry_run)
            if target_exists and not args.dry_run:
                shutil.rmtree(dest)
            print(
                json.dumps(
                    {
                        "uninstall": {
                            "target": str(dest),
                            "target_exists": target_exists,
                            "target_removed": bool(target_exists and not args.dry_run),
                            "mcp_platform": args.mcp_platform,
                            "mcp_config": str(mcp_config_path) if mcp_config_path else "",
                            "mcp_config_exists": config_exists,
                            "mcp_entry_removed": mcp_entry_removed,
                            "dry_run": bool(args.dry_run),
                        }
                    },
                    ensure_ascii=False,
                )
            )
            if not args.dry_run:
                print(f"Uninstalled {SKILL_NAME} from {dest}")
            return 0

        effective_force = bool(args.force or args.upgrade)
        action_name = "upgrade" if args.upgrade else "install"
        actions = [f"{action_name} {src} -> {dest}"]
        if mcp_config_path:
            actions.append(f"merge {args.mcp_platform} MCP config -> {mcp_config_path}")
        for action in actions:
            print(("DRY RUN: " if args.dry_run else "") + action)
        print(
            json.dumps(
                {
                    "platform": platform_install_hint(
                        args.mcp_platform,
                        mcp_config_path,
                        read_only=read_only_mcp,
                        tool_profile=args.tool_profile,
                        kit_path=dest,
                    )
                },
                ensure_ascii=False,
            )
        )
        if args.print_mcp_config:
            print(mcp_config_text(dest, platform=args.mcp_platform, read_only=read_only_mcp, tool_profile=args.tool_profile), end="")
        copy_tree(src, dest, force=effective_force, dry_run=args.dry_run)
        if mcp_config_path:
            write_mcp_config(
                mcp_config_path,
                dest,
                platform=args.mcp_platform,
                read_only=read_only_mcp,
                tool_profile=args.tool_profile,
                force=effective_force,
                dry_run=args.dry_run,
            )
        if not args.dry_run:
            verb = "Upgraded" if args.upgrade else "Installed"
            print(f"{verb} {SKILL_NAME} at {dest}")
        return 0
    except InstallError as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
